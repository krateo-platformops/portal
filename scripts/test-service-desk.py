#!/usr/bin/env python3
"""The service desk's queue, over the chart's OWN jq.

A REFUSED READ MUST NOT RENDER AS A QUIET DESK. `tickets-list` runs under `continueOnError` and
guards the absent step with `// []`, so a 403, a missing Ticket CRD and a genuinely empty desk all
produced the same empty table with nothing said anywhere. That was already a defect; the per-ticket
RBAC model makes it the DEFAULT failure, because a requester has no `list` at all and every ticket
arrives through snowplow's refilter — so any misconfigured grant produces exactly that shape.

It renders the chart and runs the real programs rather than keeping copies here: a test holding its
own copy of the jq passes while the chart drifts away from it.

WHAT THIS CANNOT TEST, so nobody mistakes it for coverage of the model: the userAccessFilter itself.
Whether snowplow drops the tickets a caller may not read is snowplow's refilter against a live
apiserver, proven by acmp's kind run (226/226) and snowplow's end-to-end test. This file covers the
chart's half — what the page SAYS about what it got back.
"""
import json
import os
import subprocess
import tempfile
import urllib.parse

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHART = os.path.join(REPO, 'helm', 'service-desk-page')
NS = 'krateo-service-desk'
JQ = os.environ.get('JQ', 'jq')


def render(desk_actions):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', CHART, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(
            ['helm', 'template', 'c', copy, '--set', f'namespace={NS}',
             '--set', f'deskActions={"true" if desk_actions else "false"}'],
            capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def named(docs, kind, name):
    for d in docs:
        if d.get('kind') == kind and d.get('metadata', {}).get('name') == name:
            return d
    raise AssertionError(f'{kind}/{name} not in the rendered chart')


def jq(program, data):
    with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run([JQ, '-c', '-f', path], input=json.dumps(data),
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise AssertionError(f'jq failed: {proc.stderr.strip()}')
        return json.loads(proc.stdout)
    finally:
        os.unlink(path)


def expr_of(doc, for_path):
    """The widgetDataTemplate expression for a path, unwrapped from its ${ } envelope."""
    for e in doc['spec'].get('widgetDataTemplate') or []:
        if e['forPath'] == for_path:
            raw = e['expression'].strip()
            assert raw.startswith('${') and raw.endswith('}'), raw
            return raw[2:-1]
    raise AssertionError(f'no widgetDataTemplate for {for_path}')


def ticket(name, state='Open', requester='alice', created='2026-11-04T10:00:00Z', summary='s',
           urgency=None, assignee=None):
    spec = {'summary': summary, 'state': state, 'kind': 'Question', 'requester': requester}
    if state is None:
        del spec['state']
    if urgency:
        spec['urgency'] = urgency
    if assignee:
        spec['assignee'] = assignee
    return {'metadata': {'name': name, 'namespace': NS, 'creationTimestamp': created}, 'spec': spec}


def ask(href):
    """(the path and query the link opens with ?ask removed, the decoded prompt)."""
    path, _, query = href.partition('?')
    params = urllib.parse.parse_qs(query, keep_blank_values=True)
    prompt = params.pop('ask', [''])
    kept = '&'.join(f'{k}={v[0]}' for k, v in params.items())
    return (path + ('?' + kept if kept else ''), prompt[0])


# A desk of seven: three Open (one with no state at all, which is Open), one In progress, one
# Waiting on requester, one Resolved and one Closed. Ages are distinct so the order is checkable.
QUEUE = {'tickets': {'items': [
    ticket('sd-new', 'Open', created='2026-11-09T10:00:00Z', summary='Newest open', assignee='bob'),
    ticket('sd-old', 'Open', created='2026-11-01T10:00:00Z', summary='Grant team-alpha read access', urgency='high'),
    ticket('sd-nostate', None, created='2026-11-05T10:00:00Z', summary='A ticket with no state'),
    ticket('sd-ip', 'In progress', created='2026-11-03T10:00:00Z', summary='Upgrade blocked', assignee='bob'),
    ticket('sd-wait', 'Waiting on requester', created='2026-11-02T10:00:00Z', summary='Which namespace?'),
    ticket('sd-res', 'Resolved', created='2026-11-06T10:00:00Z', summary='Done one', assignee='bob'),
    ticket('sd-closed', 'Closed', created='2026-11-07T10:00:00Z', summary='Closed one'),
]}}
SLUGS = ['all', 'open', 'in-progress', 'waiting-on-requester', 'resolved', 'closed']


# A 63-character DNS-1123 name: the longest acmp's admission webhook allows (64 is refused).
LONG = 'a' * 62 + 'z'

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f'{label}: {got!r} != {want!r}')


for desk_actions in (False, True):
    docs = render(desk_actions)
    tag = desk_actions and 'deskActions' or 'requester-only'
    rows_prog = named(docs, 'RESTAction', 'tickets-list')['spec']['filter']
    header = named(docs, 'PageHeader', 'service-desk-header')

    # --- the queue's own verdict on what it got back -------------------------------------------
    out = jq(rows_prog, {'tickets': {'items': [ticket('sd-t1'), ticket('sd-t2', 'Resolved')]}})
    check(f'{tag}: two tickets -> not failed', out['failed'], False)
    check(f'{tag}: two tickets -> count', out['total'], 2)

    # A genuinely empty desk. This is the case that must NOT look like a failure.
    out = jq(rows_prog, {'tickets': {'items': []}})
    check(f'{tag}: empty desk -> rows', out['rows'], [])
    check(f'{tag}: empty desk -> not failed', out['failed'], False)
    check(f'{tag}: empty desk -> not denied', out['denied'], False)

    # THE REGRESSION. A refused read used to be indistinguishable from the line above.
    denied = jq(rows_prog, {'ticketsError': 'tickets.servicedesk.krateo.io is forbidden: User "erin" cannot list resource'})
    check(f'{tag}: 403 -> rows still empty', denied['rows'], [])
    check(f'{tag}: 403 -> failed', denied['failed'], True)
    check(f'{tag}: 403 -> denied', denied['denied'], True)
    check(f'{tag}: 403 -> not absent', denied['absent'], False)

    absent = jq(rows_prog, {'ticketsError': 'the server could not find the requested resource (404)'})
    check(f'{tag}: 404 -> absent', absent['absent'], True)
    check(f'{tag}: 404 -> not denied', absent['denied'], False)

    other = jq(rows_prog, {'ticketsError': 'dial tcp: i/o timeout'})
    check(f'{tag}: timeout -> failed', other['failed'], True)
    check(f'{tag}: timeout -> neither denied nor absent', (other['denied'], other['absent']), (False, False))

    # erin's case, which is the one acmp and I agreed matters most: an operator with no ticket.
    # Her queue is empty BECAUSE THE FILTER RETURNED NOTHING, not because the request was refused,
    # and the two must be distinguishable from the payload alone.
    erin = jq(rows_prog, {'tickets': {'items': []}})
    check(f'{tag}: erin empty != denied', (erin['failed'], erin['denied']), (False, False))

    # --- what the header SAYS about each of those ---------------------------------------------
    tags_prog, sub_prog = expr_of(header, 'tags'), expr_of(header, 'subtitle')

    check(f'{tag}: healthy -> no tag', jq(tags_prog, {'failed': False}), [])
    check(f'{tag}: empty desk -> no tag', jq(tags_prog, erin), [])
    for state, label in (('denied', 'denied'), ('absent', 'absent'), ('other', 'other')):
        payload = {'failed': True, 'denied': state == 'denied', 'absent': state == 'absent'}
        got = jq(tags_prog, payload)
        check(f'{tag}: {label} -> exactly one tag', len(got), 1)
        check(f'{tag}: {label} -> red', got[0]['color'], 'red')
    # The three failures carry DIFFERENT text, because the remedies differ completely: a grant to
    # fix, a component to install, and something unknown that must be described as neither.
    said = {s: jq(tags_prog, {'failed': True, 'denied': s == 'denied', 'absent': s == 'absent'})[0]['label']
            for s in ('denied', 'absent', 'other')}
    check(f'{tag}: three distinct messages', len(set(said.values())), 3)

    # The healthy subtitle must equal the static default, or the page says one thing before the
    # data arrives and another after. They are written twice in that file and can drift.
    check(f'{tag}: templated healthy subtitle == static default',
          jq(sub_prog, {'failed': False}), header['spec']['widgetData']['subtitle'])
    # And on a refused read it must NOT claim the person has no tickets.
    refused_sub = jq(sub_prog, {'failed': True, 'denied': True})
    check(f'{tag}: refused subtitle != the healthy claim',
          refused_sub == header['spec']['widgetData']['subtitle'], False)

    # --- the detail route at the longest name admission allows --------------------------------
    # acmp proved 63 admits, files and is readable by name through the per-ticket Role. The portal's
    # half is that a 63-character name survives the route and the path the detail RA builds from it.
    detail = named(docs, 'RESTAction', 'ticket-detail')
    step = detail['spec']['api'][0]
    # The iterator lives under `dependsOn`, not on the step: dependsOn orders the stage and the
    # iterator fans it out, and a step with no iterator runs once. Reading it from the wrong level
    # would have made this check silently vacuous rather than fail, so it is asserted present.
    assert 'dependsOn' in step and 'iterator' in step['dependsOn'], step
    it_prog = step['dependsOn']['iterator'].strip()
    assert it_prog.startswith('[') , it_prog
    picked = jq(it_prog, {'namespace': NS, 'name': LONG})
    check('63-char name -> iterator yields one item', len(picked), 1)
    check('63-char name -> name intact', picked[0]['name'], LONG)
    path_expr = step['path'].strip()
    assert path_expr.startswith('${') and path_expr.endswith('}'), path_expr
    built = jq(path_expr[2:-1], picked[0])
    check('63-char name -> path', built,
          f'/apis/servicedesk.krateo.io/v1alpha1/namespaces/{NS}/tickets/{LONG}')
    # One segment per spliced value (snowplow#1123): the name must not introduce a '/'.
    check('63-char path has the expected segment count', built.count('/'), 7)
    # And an EMPTY name must still yield nothing, which is what stops the detail RA dialling the
    # collection when the route param is missing.
    check('empty name -> iterator yields nothing', jq(it_prog, {'namespace': NS, 'name': ''}), [])

    # --- the ?state= chips, the empty state, and Ask Agentiko --------------------------------
    chips = named(docs, 'Listy', 'service-desk-state-chips')
    chips_prog = expr_of(chips, 'dataSource')
    root = named(docs, 'Flex', 'page-service-desk')
    items_prog = expr_of(root, 'items')
    empty = named(docs, 'Result', 'service-desk-empty')
    button = named(docs, 'Button', 'service-desk-ask-agentiko')
    actions_prog = expr_of(button, 'actions')

    def run(state=None, data=QUEUE):
        payload = dict(data)
        if state is not None:
            payload['state'] = state
        return jq(rows_prog, payload)

    def children(out):
        return [i['resourceRefId'] for i in jq(items_prog, out)]

    allq = run()
    check(f'{tag}: no ?state= -> all', allq['state'], 'all')
    check(f'{tag}: no ?state= -> every row', len(allq['rows']), 7)
    # THE stateKey DEFECT: a ticket with no state was "Open" in the pill and the count, and "" here,
    # so the Open chip did not show it.
    nostate = [r for r in allq['rows'] if r['name'] == 'sd-nostate'][0]
    check(f'{tag}: no spec.state -> stateKey open', (nostate['state'], nostate['stateKey']), ('Open', 'open'))
    check(f'{tag}: counts over every ticket, every state, plus all',
          allq['counts'], {'all': 7, 'open': 3, 'in-progress': 1, 'waiting-on-requester': 1, 'resolved': 1, 'closed': 1})

    opened = run('open')
    check(f'{tag}: ?state=open -> the three Open rows, the stateless one included',
          sorted(r['name'] for r in opened['rows']), ['sd-new', 'sd-nostate', 'sd-old'])
    check(f'{tag}: ?state=open -> counts unchanged by the chip', opened['counts'], allq['counts'])
    check(f'{tag}: ?state=open -> echoed', (opened['state'], opened['stateValid']), ('open', True))
    check(f'{tag}: ?state=Waiting-On-Requester -> case-insensitive',
          [r['name'] for r in run('Waiting-On-Requester')['rows']], ['sd-wait'])

    # The chips: the fixed six, live counts, the selected one active, routes to ?state=.
    got = jq(chips_prog, opened)
    check(f'{tag}: chips -> the six, in order', [c['label'] for c in got],
          ['All', 'Open', 'In progress', 'Waiting on requester', 'Resolved', 'Closed'])
    check(f'{tag}: chips -> counts', [c['count'] for c in got], [7, 3, 1, 1, 1, 1])
    check(f'{tag}: chips -> Open active, alone', [c['active'] for c in got], [False, True, False, False, False, False])
    check(f'{tag}: chips -> routes', [c['route'] for c in got], [f'/service-desk?state={s}' for s in SLUGS])
    check(f'{tag}: chips, no ?state= -> All active', [c['active'] for c in jq(chips_prog, allq)][0], True)
    # Every chip route must select what its label says: follow each one back through the RA.
    for c, slug in zip(got, SLUGS):
        followed = run(slug)
        check(f'{tag}: chip {c["label"]} -> its own count of rows', len(followed['rows']), c['count'])

    # A ?state= that is not a state. Audit refuses an outcome it does not know (okOutcome); so does
    # this — and SAYS so, instead of an empty table with no chip active.
    bogus = run('foo')
    check(f'{tag}: ?state=foo -> refused', (bogus['stateValid'], bogus['rows']), (False, []))
    check(f'{tag}: ?state=foo -> no chip active', any(c['active'] for c in jq(chips_prog, bogus)), False)
    check(f'{tag}: ?state=foo -> the sentence names the address', bogus['emptyTitle'], 'That is not a ticket state')
    check(f'{tag}: ?state=foo -> the sentence', bogus['emptyText'],
          "The state in this page's address is not one a ticket can be in. Choose a state above, or All to see every ticket.")

    # The page root: chips then the table, or chips then the sentence; on a failed read, neither
    # chips nor sentence.
    check(f'{tag}: root, tickets -> header, chips, table', children(allq),
          ['service-desk-header', 'service-desk-state-chips', 'tickets-table'])
    check(f'{tag}: root, ?state=foo -> header, chips, sentence', children(bogus),
          ['service-desk-header', 'service-desk-state-chips', 'service-desk-empty'])
    refused = run(data={'ticketsError': 'tickets.servicedesk.krateo.io is forbidden: User "erin" cannot list resource'})
    check(f'{tag}: root, failed read -> header, table, nothing else', children(refused),
          ['service-desk-header', 'tickets-table'])
    check(f'{tag}: failed read -> no sentence', (refused['emptyTitle'], refused['emptyText']), ('', ''))
    check(f'{tag}: root, no data at all -> the static items',
          [i['resourceRefId'] for i in root['spec']['widgetData']['items']],
          ['service-desk-header', 'service-desk-state-chips', 'tickets-table'])

    # The empty-state sentences, as the mockup words them.
    one_state = {'tickets': {'items': [ticket('t1', 'Open'), ticket('t2', 'Open')]}}
    closed = run('closed', one_state)
    check(f'{tag}: none Closed -> title', closed['emptyTitle'], 'No ticket is Closed.')
    check(f'{tag}: none Closed -> text', closed['emptyText'], '2 tickets are in other states. Choose All to see them.')
    check(f'{tag}: none Closed, one ticket -> singular',
          run('closed', {'tickets': {'items': [ticket('t1', 'Open')]}})['emptyText'],
          '1 ticket is in another state. Choose All to see it.')
    check(f'{tag}: none Waiting -> the label as the chip says it',
          run('waiting-on-requester', one_state)['emptyTitle'], 'No ticket is Waiting on requester.')
    nothing = run(data={'tickets': {'items': []}})
    if desk_actions:
        check(f'{tag}: empty desk -> title', nothing['emptyTitle'], 'No one has raised a ticket yet.')
        check(f'{tag}: empty desk -> text', nothing['emptyText'],
              'When someone raises a request on this platform, it appears here with its state and who is working on it.')
    else:
        check(f'{tag}: none raised -> title', nothing['emptyTitle'], 'You have not raised a ticket yet.')
    check(f'{tag}: empty desk, a chip selected -> the no-tickets sentence, not "No ticket is Open"',
          run('open', {'tickets': {'items': []}})['emptyTitle'], nothing['emptyTitle'])
    check(f'{tag}: tickets shown -> no sentence', (allq['emptyTitle'], allq['emptyText']), ('', ''))
    check(f'{tag}: result title <- emptyTitle', jq(expr_of(empty, 'title'), closed), 'No ticket is Closed.')
    check(f'{tag}: result subTitle <- emptyText', jq(expr_of(empty, 'subTitle'), closed), closed['emptyText'])

    # Ask Agentiko: the prompt is about the view on screen, and the link keeps the chip.
    href, prompt = ask(allq['askHref'])
    check(f'{tag}: ask, all -> self page, no state', href, '/service-desk')
    if desk_actions:
        check(f'{tag}: ask, all -> the counts', prompt.split(' The oldest')[0],
              'Triage the service-desk queue: 7 tickets — 3 open, 1 in progress, 1 waiting on the requester, 3 unassigned.')
        # Oldest Open FIRST, with urgency and the day it was raised; the stateless one is Open.
        check(f'{tag}: ask, all -> the oldest open, oldest first',
              prompt.split('The oldest open ones are: ')[1].split(' Tell me')[0],
              '1. "Grant team-alpha read access" (high, raised 2026-11-01) 2. "A ticket with no state" (raised 2026-11-05) '
              '3. "Newest open" (raised 2026-11-09).')
        check(f'{tag}: ask, all -> the ask', prompt.endswith(
            'Tell me which to pick up first and why, and what the desk should do next for each — grounded in the evidence on this page.'), True)
    else:
        check(f'{tag}: ask, all -> the requester view', prompt,
              'Go through the tickets I have raised (7; 5 still open): what is happening with each, and does any need something from me?')

    href, prompt = ask(opened['askHref'])
    check(f'{tag}: ask, ?state=open -> keeps the chip', href, '/service-desk?state=open')
    if desk_actions:
        check(f'{tag}: ask, ?state=open -> about the open tickets', prompt.split(' The oldest')[0],
              'Triage the open tickets in the service-desk queue: 3 of 7 tickets, 2 unassigned.')
        check(f'{tag}: ask, ?state=open -> only open tickets listed', 'Upgrade blocked' in prompt, False)
    else:
        check(f'{tag}: ask, ?state=open -> requester, filtered', prompt,
              'Go through the open tickets I have raised (3 of my 7): what is happening with each, and does any need something from me?')
    href, prompt = ask(run('resolved')['askHref'])
    check(f'{tag}: ask, ?state=resolved -> keeps the chip', href, '/service-desk?state=resolved')
    if desk_actions:
        check(f'{tag}: ask, ?state=resolved -> reviews, not triages', prompt,
              'Review the resolved tickets in the service-desk queue: 1 of 7 tickets. It is: 1. "Done one" (raised 2026-11-06). '
              'Tell me whether any was resolved without what was asked actually being done, and whether any should be reopened — '
              'grounded in the evidence on this page.')
    href, prompt = ask(closed['askHref'])
    check(f'{tag}: ask, a chip with no tickets -> keeps the chip', href, '/service-desk?state=closed')
    if desk_actions:
        check(f'{tag}: ask, a chip with no tickets -> says so', prompt,
              "The service-desk queue has no closed tickets; its 2 tickets are in other states. Tell me whether anything in the "
              "queue needs the desk's attention — grounded in the evidence on this page.")
    else:
        check(f'{tag}: ask, a chip with no tickets -> says so', prompt.startswith('I have no closed tickets. Go through the tickets I have raised (2; 2 still open)'), True)
    href, prompt = ask(bogus['askHref'])
    check(f'{tag}: ask, ?state=foo -> drops it', href, '/service-desk')
    check(f'{tag}: ask, ?state=foo -> the whole queue', prompt, ask(allq['askHref'])[1])
    href, prompt = ask(nothing['askHref'])
    check(f'{tag}: ask, empty -> says the queue is empty', prompt.startswith(
        'The service-desk queue is empty: no one has raised a ticket on this platform yet.' if desk_actions
        else 'I have not raised any service-desk tickets.'), True)
    # A FAILED READ IS NOT AN EMPTY QUEUE, in the prompt as on the page.
    href, prompt = ask(refused['askHref'])
    check(f'{tag}: ask, failed read -> asks about the error', prompt,
          'The service-desk queue could not be read (tickets.servicedesk.krateo.io is forbidden: User "erin" cannot list resource). '
          'Explain what that error means and what an administrator has to fix.')
    check(f'{tag}: ask, failed read -> no claim about tickets', 'empty' in prompt or 'Triage' in prompt, False)
    href, _ = ask(run('open', {'ticketsError': 'dial tcp: i/o timeout'})['askHref'])
    check(f'{tag}: ask, failed read -> keeps the chip', href, '/service-desk?state=open')
    # A summary is cut at 110, and at most eight tickets are listed.
    many = {'tickets': {'items': [ticket(f't{i}', 'Open', created=f'2026-11-{i + 10:02d}T00:00:00Z', summary='x' * 150)
                                  for i in range(10)]}}
    _, prompt = ask(run(data=many)['askHref'])
    check(f'{tag}: ask -> eight at most', ('8. "' in prompt, '9. "' in prompt), (True, False) if desk_actions else (False, False))
    if desk_actions:
        check(f'{tag}: ask -> a summary is capped', ('"' + 'x' * 109 + '…"') in prompt, True)

    # The Button: the RA's link, or the queue — never a 404.
    nav = jq(actions_prog, allq)['navigate'][0]
    check(f'{tag}: button -> askHref', (nav['id'], nav['path']), ('ask', allq['askHref']))
    check(f'{tag}: button, no data -> the queue', jq(actions_prog, {})['navigate'][0]['path'], '/service-desk')
    check(f'{tag}: button -> label and intent',
          (button['spec']['widgetData']['label'], button['spec']['widgetData']['intent']), ('Ask Agentiko →', 'agentiko'))
    # Immediately LEFT of the header's primary, which it does not take.
    check(f'{tag}: header -> Ask, then Raise',
          [i['resourceRefId'] for i in header['spec']['widgetData']['items']], ['service-desk-ask-agentiko', 'service-desk-raise'])
    # Every widget that reads the chip declares it, or one chip's cell is served to another.
    for kind, name in (('Flex', 'page-service-desk'), ('Listy', 'service-desk-state-chips'), ('Table', 'tickets-table'),
                       ('Result', 'service-desk-empty'), ('Button', 'service-desk-ask-agentiko')):
        check(f'{tag}: {name} keyExtras', named(docs, kind, name)['spec'].get('keyExtras'), ['state'])

for f in failures:
    print(f'FAIL  {f}')
print(f'{"FAILED" if failures else "ok"}: service desk, both deskActions modes'
      f' ({len(failures)} failure(s))')
raise SystemExit(1 if failures else 0)
