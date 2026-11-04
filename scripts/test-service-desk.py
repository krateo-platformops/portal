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


def ticket(name, state='Open', requester='alice'):
    return {'metadata': {'name': name, 'namespace': NS, 'creationTimestamp': '2026-11-04T10:00:00Z'},
            'spec': {'summary': 's', 'state': state, 'kind': 'Question', 'requester': requester}}


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

for f in failures:
    print(f'FAIL  {f}')
print(f'{"FAILED" if failures else "ok"}: service desk, both deskActions modes'
      f' ({len(failures)} failure(s))')
raise SystemExit(1 if failures else 0)
