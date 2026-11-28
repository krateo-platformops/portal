#!/usr/bin/env python3
"""
test-incidents-list — /incidents, the dashboard and its "Ask Agentiko →", over the chart's own jq.

WHAT IT GUARDS.
THE DASHBOARD (mockup A4):
  - the state is the page's one filter: absent it is Open; ?status= is read in any case, `all` lists
    every incident, and a value that is no state lists nothing, with the chips still there;
  - the chips: Open, Analyzing, Verifying, Resolved, Closed, All, the live states and All counting
    every incident, Resolved and Closed their range's, the picked one active; each donut slice is
    its chip's count;
  - RESOLVED IS THE RANGE'S: the Resolved figure, its chip's count and the rows it lists (cards and
    table) are the incidents resolved in the range (resolution.at in the window, not before
    triggeredAt): one number, an incident resolved earlier or with no resolution.at left out, and
    the empty line and the prompt say "in the last …"; CLOSED likewise, by its resolution.at; every route on the page (state, range and view chips) keeps the other params
    and the ?projects= scope, and leaves out a param at its default;
  - the five figures count every incident: Open, Analyzing, Verifying now; Resolved and the median
    Time to resolve (triggered to resolution.at) in the range; a failed read is "—", never 0;
  - a state figure (Open, Analyzing, Verifying, Resolved) opens the list filtered to its state: the
    route of its state chip, every other param kept, zero or not; Time to resolve and a failed read
    are not clickable;
  - ?range=: 7d when absent; 24h buckets the opened-vs-resolved chart per hour, 7d and 30d per day,
    one point per bucket and series ending now; the time to resolve per week covers 8 weeks, a week
    with nothing resolved has no point;
  - the layout: the header (no subtitle), the range, the figures, then the row: the incidents card
    (chips, then the cards, or ?view=table the table, or a line when there is nothing to list or the
    read failed) on two thirds, the charts on one third, the "Firing, not investigated" card only
    while there are such alerts or their read failed;
  - a card says the state (its colour, and its tile's mark: a triangle while Open, a check once
    Resolved, never the triangle on a settled incident), the category, the cause and the object
    the analysis read, and a wrench marks an Open incident whose fix can be applied;
  - an alert's display name loses a decorative emoji but keeps its punctuation (an em dash), with
    whitespace runs collapsed: it is the card title and the table's Incident.
THE PROMPT. incidents-list builds the header button's `askHref`: /incidents with every param and the
?projects= scope kept, and a prompt about the state on screen:
  - Open (the default) and the other active states: a triage of that state's incidents (title, root
    cause, the DAY each was triggered), at most eight; a done state (Resolved, Closed) asks for a
    review; a state with no incidents says so; All gives the per-state counts, the alerts firing
    with no incident, and the open incidents;
  - A FAILED READ IS NOT A QUIET PLATFORM: an Incident list that could not be read asks what the error
    means, and an alerts read that failed is never "no alert is firing". snowplow accumulates an
    errorKey as a LIST whose elements are strings or the apiserver's Status object
    (resolve.go accumulateErrorKey); both shapes are fed here;
  - the alerts firing with no incident: each says why it has none, off alert-provider's
    OpensIncidents condition (a metric or value alert opens no incidents, a counting alert has not
    opened one yet, listed first), and opens Agentiko on it; its prompt says when the alert never
    opens one; a paused alert is not listed.
The fixtures of the figures and the charts are dated relative to now, so they hold on any day.
Every widget is resolved over the same output, and with --crds every resolved widget is validated
against its CRD.

Uses the `jq` binary; JQ=gojq runs the same checks on it.
Usage: test-incidents-list.py [--crds DIR]. Exit code = failed checks.
"""
import datetime
import importlib.util
import os
import sys
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

CHART = os.path.join(HERE, '..', 'helm', 'incidents-page')
TAIL = ' Tell me which to work on first, whether any share a cause, and the next step for each — grounded in the evidence on this page.'
KX = ['projects', 'status', 'range', 'view', 'q']
NOW = datetime.datetime.now(datetime.timezone.utc)


def ago(hours):
    return (NOW - datetime.timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')


def incident(name, state, triggered, alert=None, cause=None, error=None, resolved=None, category=None,
             analyzed=None, fix=None):
    st = {} if state is None else {'state': state}
    if cause:
        st['rootCause'] = {'statement': cause}
        if category:
            st['rootCause']['category'] = category
    if error:
        st['error'] = error
    if resolved:
        st['resolution'] = {'by': 'verify' if state == 'Resolved' else 'user', 'at': resolved}
    if analyzed:
        st['analyzedResources'] = analyzed
    if fix:
        st['howToFix'] = fix
    meta = {'name': name, 'creationTimestamp': triggered}
    if alert:
        meta['labels'] = {'observability.krateo.io/alert': alert}
    return {'metadata': meta, 'spec': {'triggeredAt': triggered}, 'status': st}


def alert(name, display, state='ALERT', opens=None, typ=None, paused=False):
    st = {'state': state}
    if typ:
        st['type'] = typ
    if opens:  # (status, reason, message): alert-provider's OpensIncidents condition
        st['conditions'] = [{'type': 'Ready', 'status': 'True', 'reason': 'Available'},
                            {'type': 'OpensIncidents', 'status': opens[0], 'reason': opens[1], 'message': opens[2]}]
    meta = {'name': name}
    if paused:
        meta['annotations'] = {'krateo.io/paused': 'true'}
    return {'metadata': meta, 'spec': {'displayName': display}, 'status': st}


AGG = 'It compares an aggregated value; only an alert that counts log lines or spans opens incidents.'


INCIDENTS = [
    incident('inc-lat', 'Open', '2026-11-08T10:00:00Z', alert='api-latency', cause='connection pool exhausted on orders-db'),
    incident('inc-disk', 'Open', '2026-11-06T09:00:00Z', alert='disk', cause='PVC data-pg-0 at 97%'),
    incident('inc-new', None, '2026-11-09T11:00:00Z', alert='crashloop'),                 # Analyzing
    incident('inc-ver', 'Verifying', '2026-11-07T08:00:00Z', alert='api-latency', cause='rolled back orders-api'),
    incident('inc-res', 'Resolved', '2026-11-05T07:00:00Z', alert='disk', cause='log volume filled /var', resolved=ago(2)),
]
ALERTS = [alert('api-latency', 'API latency p95'), alert('disk', 'Disk pressure', 'OK'), alert('crashloop', 'Pod crashloop'),
          alert('restarts', 'Pod restarts')]                                              # firing, no incident


def responses(**over):
    r = {'incidents': {'items': INCIDENTS}, 'alerts': {'items': ALERTS}}
    r.update(over)
    return r


def resolve(resp, extras):
    """incidents-list over `resp`: a step's response, or a list of errors (as snowplow accumulates
    them under the errorKey) for a failed call."""
    ra = CH.get('RESTAction', 'incidents-list')
    data = dict(extras)
    for step in ra['spec']['api']:
        raw = resp.get(step['name'])
        if isinstance(raw, list):
            data[step['errorKey']] = raw
        elif raw is not None:
            data[step['name']] = raw
    return tbi.jq(ra['spec']['filter'], data)


def ask(href):
    path, _, query = href.partition('?')
    params = parse_qs(query, keep_blank_values=True)
    prompt = params.pop('ask', [''])
    kept = '&'.join(f'{k}={v[0]}' for k, v in params.items())
    return (path + ('?' + kept if kept else ''), prompt[0])


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def widget_data(kind, name, out, extras=None):
    return tbi.resolved_widget(CH, kind, name, out, extras or {}, f'{kind} {name}')['spec']['widgetData']


def items_of(kind, name, out, extras=None):
    return [i['resourceRefId'] for i in widget_data(kind, name, out, extras)['items']]


def check_default_is_open():
    f = []
    out = resolve(responses(), {'projects': 'team-a'})
    expect(f, 'no ?status: Open', (out['status'], [r['name'] for r in out['rows']]), ('open', ['inc-lat', 'inc-disk']))
    href, prompt = ask(out['askHref'])
    expect(f, 'self page, scope kept, the default left out', href, '/incidents?projects=team-a')
    expect(f, 'the prompt is about the open ones', prompt,
           'Triage my open incidents: 2 of 5 incidents. They are: 1. "API latency p95" — connection pool exhausted on '
           'orders-db (triggered 2026-11-08) 2. "Disk pressure" — PVC data-pg-0 at 97% (triggered 2026-11-06).' + TAIL)
    expect(f, 'the chips: Open first, every incident counted, Open active',
           [(c['label'], c['count'], c['active']) for c in out['chips']],
           [('Open', 2, True), ('Analyzing', 1, False), ('Verifying', 1, False), ('Resolved', 1, False),
            ('Closed', 0, False), ('All', 5, False)])
    expect(f, 'the chips keep the scope', [c['route'] for c in out['chips']],
           ['/incidents?projects=team-a', '/incidents?projects=team-a&status=analyzing',
            '/incidents?projects=team-a&status=verifying', '/incidents?projects=team-a&status=resolved',
            '/incidents?projects=team-a&status=closed', '/incidents?projects=team-a&status=all'])
    expect(f, 'the per-alert links are untouched', [a['askHref'].split('?ask=')[0] for a in out['awaiting']], ['/incidents'])
    many = [incident(f'i{i}', 'Open', f'2026-11-{i + 10:02d}T00:00:00Z', cause='x' * 200) for i in range(10)]
    _, prompt = ask(resolve(responses(incidents={'items': many}), {})['askHref'])
    expect(f, 'eight at most', ('8. "' in prompt, '9. "' in prompt), (True, False))
    # With no alert, the title IS the root cause: it is cut at 110 and not repeated as the cause.
    expect(f, 'a cause equal to the title is not repeated', '"' + 'x' * 109 + '…" (triggered ' in prompt, True)
    long_cause = [incident('i-long', 'Open', '2026-11-10T00:00:00Z', alert='api-latency', cause='y' * 200)]
    _, prompt = ask(resolve(responses(incidents={'items': long_cause}), {})['askHref'])
    expect(f, 'a root cause is cut at 160', '"API latency p95" — ' + 'y' * 159 + '… (triggered 2026-11-10)' in prompt, True)
    return f


def check_status_param():
    f = []
    out = resolve(responses(), {'status': 'all'})
    href, prompt = ask(out['askHref'])
    expect(f, 'all: every incident', (out['matched'], href), (5, '/incidents?status=all'))
    expect(f, 'all: the counts, the alerts, the open ones', prompt,
           'Triage my incidents: 2 open, 1 verifying, 1 still being analyzed, and 1 alert firing with no incident '
           '(Pod restarts). The open ones are: 1. "API latency p95" — connection pool exhausted on orders-db '
           '(triggered 2026-11-08) 2. "Disk pressure" — PVC data-pg-0 at 97% (triggered 2026-11-06).' + TAIL)
    href, prompt = ask(resolve(responses(), {'status': 'Resolved'})['askHref'])
    expect(f, 'resolved, any case: kept', href, '/incidents?status=resolved')
    expect(f, 'resolved: a review', prompt,
           'Review my incidents resolved in the last 7 days: 1 of 5 incidents. It is: 1. "Disk pressure" — log volume filled /var '
           '(triggered 2026-11-05). Tell me whether any looks resolved without its cause being fixed, and whether any '
           'is likely to come back — grounded in the evidence on this page.')
    out = resolve(responses(), {'status': 'closed'})
    href, prompt = ask(out['askHref'])
    expect(f, 'empty state: kept', href, '/incidents?status=closed')
    expect(f, 'empty state: says so', prompt,
           'No incident was closed in the last 7 days; my 5 incidents are in other states or closed before that, with 1 alert '
           'firing with no incident (Pod restarts). Tell me whether anything here needs attention — grounded in the evidence on this page.')
    expect(f, 'empty state: the line in place of the cards', items_of('Card', 'incidents-list-card', out, {'status': 'closed'}),
           ['incidents-status-chips', 'incidents-empty'])
    expect(f, 'empty state: the line says the range', widget_data('Paragraph', 'incidents-empty', out)['text'], 'No incident closed in the last 7 days.')
    out = resolve(responses(), {'status': 'foo', 'projects': 'team-a'})
    href, prompt = ask(out['askHref'])
    expect(f, 'not a state: no rows, no chip active', (out['rows'], [c['active'] for c in out['chips']]), ([], [False] * 6))
    expect(f, 'not a state: kept', href, '/incidents?projects=team-a&status=foo')
    expect(f, 'not a state: the prompt says nothing is in it', prompt,
           'No incident is in the state "foo"; my 5 incidents are in other states, with 1 alert firing with no incident '
           '(Pod restarts). Tell me whether anything here needs attention — grounded in the evidence on this page.')
    return f


def check_empty_and_failed():
    f = []
    out = resolve(responses(incidents={'items': []}), {})
    _, prompt = ask(out['askHref'])
    expect(f, 'no incidents, an alert firing', prompt,
           'There are no incidents on this platform, and 3 alerts firing with no incident (API latency p95, Pod '
           'crashloop, Pod restarts). Tell me which alert to investigate first and why — grounded in the evidence on this page.')
    expect(f, 'no incidents: the line', widget_data('Paragraph', 'incidents-empty', out)['text'], 'There are no incidents.')
    expect(f, 'no incidents: zeros, not dashes', [s['value'] for s in out['stats']], ['0', '0', '0', '0', '—'])
    expect(f, 'no incidents: every figure neutral and plain, a 0 never red or orange',
           [(s['color'], s['highlight']) for s in out['stats']], [('', False)] * 5)
    _, prompt = ask(resolve(responses(incidents={'items': []}, alerts={'items': []}), {})['askHref'])
    expect(f, 'nothing at all', prompt,
           'There are no incidents on this platform, and no alert firing without an incident. Tell me whether anything '
           'on this page needs attention.')
    status = {'kind': 'Status', 'status': 'Failure', 'code': 403, 'reason': 'Forbidden',
              'message': 'incidents.observability.krateo.io is forbidden: User "erin" cannot list resource'}
    for label, errs, said in (('a Status object', [status], status['message']),
                              ('a string', ['dial tcp: i/o timeout'], 'dial tcp: i/o timeout')):
        out = resolve(responses(incidents=errs), {'status': 'verifying'})
        href, prompt = ask(out['askHref'])
        expect(f, f'failed read ({label}): flagged', (out['failed'], out['readError']), (True, said))
        expect(f, f'failed read ({label}): asks about the error', prompt,
               'The incidents on this page could not be read (' + said + '). Explain what that error means and what to check.')
        expect(f, f'failed read ({label}): keeps the state', href, '/incidents?status=verifying')
        expect(f, f'failed read ({label}): every figure "could not be read", never 0',
               {(s['value'], s['caption'], s['tone']) for s in out['stats']}, {('', 'could not be read', 'unread')})
        expect(f, f'failed read ({label}): every figure orange and highlighted',
               {(s['color'], s['highlight']) for s in out['stats']}, {('orange', True)})
        para = widget_data('Paragraph', 'incidents-empty', out)
        expect(f, f'failed read ({label}): said, as a warning', (para['text'], para['type']),
               ('The incidents could not be read (' + said + '), so nothing on this page counts them.', 'warning'))
        expect(f, f'failed read ({label}): no chart drawn from it',
               [widget_data(k, n, out)['data'] for k, n in (('PieChart', 'incidents-by-state'), ('LineChart', 'incidents-trend'),
                                                             ('LineChart', 'incidents-ttr'))], [[], [], []])
    out = resolve(responses(alerts=['dial tcp: i/o timeout']), {'status': 'all'})
    _, prompt = ask(out['askHref'])
    expect(f, 'alerts unread: never "no alert firing"', 'no alert firing' in prompt, False)
    expect(f, 'alerts unread: said', 'an unknown number of alerts firing with no incident (the alerts could not be read)' in prompt, True)
    expect(f, 'alerts unread: the card shows, and says so', (
        'incidents-awaiting-card' in items_of('Flex', 'incidents-side', out),
        widget_data('Listy', 'incidents-awaiting', out)['header']),
        (True, 'The alerts could not be read, so an alert firing without an incident would not show here.'))
    out = resolve(responses(), {})
    expect(f, 'healthy read: not failed', (out['failed'], out['readError']), (False, ''))
    return f


# Dated relative to now. Resolved: r1 in 2 h (12 h ago), r2 in 40 min (3 days ago), r3 in 6 h (20 days ago).
TIMED = [
    incident('o1', 'Open', ago(3), alert='a', cause='c1', category='permission',
             analyzed=[{'gvr': 'v1/pods', 'name': 'web-0'}, {'gvr': 'composition.krateo.io/v1/fireworksapps', 'name': 'fw-prod'}],
             fix={'applyAction': {'verb': 'patch', 'apiVersion': 'v1', 'resource': 'secrets', 'name': 's', 'payload': {}}}),
    incident('o2', 'Open', ago(50), alert='a', cause='c2', analyzed=[{'gvr': 'apps/v1/deployments', 'name': 'snowplow'}]),
    incident('o3', 'Open', ago(60), alert='a', cause='c3', fix={'apply': 'kubectl rollout restart deploy/x'}),
    incident('a1', 'Analyzing', ago(1), alert='a'),
    incident('v1', 'Verifying', ago(5), alert='a', cause='c4', fix={'apply': 'x'}),
    incident('r1', 'Resolved', ago(14), alert='a', cause='c5', resolved=ago(12)),
    incident('r2', 'Resolved', ago(72 + 40 / 60), alert='a', cause='c6', resolved=ago(72)),
    incident('r3', 'Resolved', ago(486), alert='a', cause='c7', resolved=ago(480)),
    incident('c1', 'Closed', ago(30), alert='a', cause='c8', resolved=ago(29)),
]


def timed(extras=None):
    return resolve(responses(incidents={'items': TIMED}, alerts={'items': [alert('a', 'A', 'OK')]}), extras or {})


def check_figures_and_charts():
    f = []
    out = timed()
    expect(f, '7d: the five figures', [(s['title'], s['value'], s['caption'], s['tone']) for s in out['stats']],
           [('Open', '3', '1 opened in the last 24 hours', 'open'), ('Analyzing', '1', 'root-cause analysis running', 'analyzing'),
            ('Verifying', '1', 'fix applied, checks running', 'verifying'), ('Resolved', '2', 'in the last 7 days', 'resolved'),
            ('Time to resolve', '1 h 20 min', 'median over the last 7 days', 'resolved')])
    # Verifying is not always in progress: ChecksUnknown=True means the checks stopped with no verdict.
    gave_up = incident('v2', 'Verifying', ago(200), alert='a', cause='c9', fix={'apply': 'x'})
    gave_up['status']['conditions'] = [{'type': 'ChecksUnknown', 'status': 'True', 'reason': 'VerifyNoVerdict'}]
    cleared = incident('v3', 'Verifying', ago(6), alert='a', cause='c10', fix={'apply': 'x'})
    cleared['status']['conditions'] = [{'type': 'ChecksUnknown', 'status': 'False', 'reason': 'Verdict'}]
    caption = lambda items: next(s['caption'] for s in resolve(responses(incidents={'items': items}, alerts={'items': [alert('a', 'A', 'OK')]}), {})['stats'] if s['title'] == 'Verifying')
    expect(f, 'Verifying: what its checks are doing, never "running" once they gave up',
           [caption([gave_up]), caption([gave_up, TIMED[4]]), caption([cleared]), caption([TIMED[0]])],
           ['fix applied, checks gave no verdict', '1 with checks running, 1 with no verdict', 'fix applied, checks running', ''])
    # No analysis agent (alert-provider >= 0.2.88): the cause says so, never "analysis failed".
    no_agent = incident('n1', 'Open', ago(2), alert='a', error='leftover')
    no_agent['status']['conditions'] = [{'type': 'AnalysisUnavailable', 'status': 'True', 'reason': 'NoAnalysisAgent'}]
    failed = incident('n2', 'Open', ago(2), alert='a', error='model timed out')
    causes = {r['name']: r['cause'] for r in resolve(responses(incidents={'items': [no_agent, failed]}, alerts={'items': [alert('a', 'A', 'OK')]}), {'status': 'all'})['rows']}
    expect(f, 'no analysis agent is said as such; a real failure stays one', causes, {'n1': 'no analysis agent', 'n2': 'analysis failed'})
    stats = widget_data('Listy', 'incidents-stats', out)
    cards = widget_data('Listy', 'incidents-cards', out)
    expect(f, 'the strip takes colour and border from the data (portal.tileDefs)',
           (stats['itemTemplate']['color'], stats['itemTemplate'].get('highlight')), ({'value': '{color}'}, '{highlight}'))
    cmap = cards['itemTemplate']['color']['map']
    expect(f, 'above 0, each unresolved state wears its card colour, highlighted; Resolved and Time to resolve neutral',
           [(s['title'], s['color'], s['highlight']) for s in out['stats']],
           [('Open', cmap['Open'], True), ('Analyzing', cmap['Analyzing'], True), ('Verifying', cmap['Verifying'], True),
            ('Resolved', '', False), ('Time to resolve', '', False)])
    three = resolve(responses(incidents={'items': [incident(f'o{i}', 'Open', ago(2), alert='a') for i in range(3)]},
                              alerts={'items': [alert('a', 'A', 'OK')]}), {})
    expect(f, 'Open at 3: red and highlighted', [(s['value'], s['color'], s['highlight']) for s in three['stats']][0], ('3', 'red', True))
    out30 = timed({'range': '30d'})
    expect(f, '30d: Resolved and Time to resolve widen', [s['value'] for s in out30['stats'][3:]], ['3', '2 h'])
    out24 = timed({'range': '24h'})
    expect(f, '24h', [(s['value'], s['caption']) for s in out24['stats'][3:]],
           [('1', 'in the last 24 hours'), ('2 h', 'median over the last 24 hours')])
    expect(f, 'nothing resolved in the range: a dash, said',
           [(s['value'], s['caption']) for s in resolve(responses(incidents={'items': TIMED[:5]}), {})['stats'][4:]],
           [('—', 'nothing resolved in the last 7 days')])
    for rng, unit, n in (('24h', 'hour', 24), ('7d', 'day', 7), ('30d', 'day', 30)):
        o = timed({'range': rng})
        pts = o['trend']
        opened = [p for p in pts if p['series'] == 'Opened']
        resolved = [p for p in pts if p['series'] == 'Resolved']
        expect(f, f'{rng}: one point per bucket and series, per {unit}', (len(opened), len(resolved), o['trendUnit']), (n, n, unit))
        expect(f, f'{rng}: the buckets are consecutive and end now',
               ({b['t'] - a['t'] for a, b in zip(opened, opened[1:])}, opened[-1]['t'] <= NOW.timestamp() < opened[-1]['t'] + (3600 if unit == 'hour' else 86400)),
               ({3600 if unit == 'hour' else 86400}, True))
        want_opened = {'24h': 4, '7d': 8, '30d': 9}[rng]
        want_resolved = {'24h': 1, '7d': 2, '30d': 3}[rng]
        expect(f, f'{rng}: opened and resolved in the range', (sum(p['v'] for p in opened), sum(p['v'] for p in resolved)),
               (want_opened, want_resolved))
        xu = widget_data('LineChart', 'incidents-trend', o, {'range': rng})['xTimeUnit']
        expect(f, f'{rng}: the chart labels {unit}s', xu, unit)
    # Each resolved incident's week (epoch weeks, as the RESTAction buckets them) and minutes.
    by_week = {}
    for hours, minutes in ((12, 120), (72, 40), (480, 360)):
        at = (NOW - datetime.timedelta(hours=hours)).replace(microsecond=0).timestamp()
        by_week.setdefault(int(at // 604800) * 604800, []).append(minutes)
    # CHART-3: the weekly medians in the largest unit that keeps the longest readable. Here the
    # longest is 6 h: hours. Under 2 h, minutes; from 3 days, days.
    want = [{'t': t, 'v': round(sum(ms) / len(ms) / 60 * 10) / 10} for t, ms in sorted(by_week.items())]
    expect(f, 'per week: the median of each week with something resolved, in hours', (out['ttrWeekly'], out['ttrUnit']), (want, 'hours'))
    expect(f, 'the chart names the unit on its axis', widget_data('LineChart', 'incidents-ttr', out)['axis']['y']['title'], 'hours')
    quick = resolve(responses(incidents={'items': [TIMED[6]]}, alerts={'items': [alert('a', 'A', 'OK')]}), {'range': '30d'})
    expect(f, 'under 2 h: minutes', (quick['ttrWeekly'][0]['v'], quick['ttrUnit']), (40, 'minutes'))
    slow = incident('s1', 'Resolved', ago(24 * 30), alert='a', cause='c', resolved=ago(24))
    long_ = resolve(responses(incidents={'items': [slow]}, alerts={'items': [alert('a', 'A', 'OK')]}), {'range': '30d'})
    expect(f, 'from 3 days: days', (long_['ttrWeekly'][0]['v'], long_['ttrUnit'], widget_data('LineChart', 'incidents-ttr', long_)['axis']['y']['title']),
           (29.0, 'days', 'days'))
    expect(f, 'the TTR card shows the median', widget_data('Card', 'incidents-ttr-card', out)['extra'], '1 h 20 min')
    expect(f, 'by state: Open first, a slice per state with incidents; Resolved is the figure\'s 2, not every one ever',
           widget_data('PieChart', 'incidents-by-state', out)['data'],
           [{'state': 'Open', 'count': 3}, {'state': 'Analyzing', 'count': 1}, {'state': 'Verifying', 'count': 1},
            {'state': 'Resolved', 'count': 2}, {'state': 'Closed', 'count': 1}])
    return f


def check_no_model_analysis():
    """An incident no model analyses is not a failed analysis. An alert labelled
    observability.krateo.io/analysis=human (copied to its incidents) is analysed by a person, by
    design, and alert-provider writes its reason into status.error: the cause says who analyses it,
    in every state (a label does not change). Any other label value leaves a failure a failure."""
    f = []
    human_only = 'This alert is analysed by a person: no model reads what it matched.'
    rows = []
    for name, state, label in (('h1', 'Open', 'human'), ('h2', 'Closed', 'human'), ('m1', 'Open', 'model')):
        inc = incident(name, state, ago(2), alert='a', error=human_only if label == 'human' else 'model timed out')
        inc['metadata']['labels']['observability.krateo.io/analysis'] = label
        rows.append(inc)
    out = resolve(responses(incidents={'items': rows}, alerts={'items': [alert('a', 'A', 'OK')]}), {'status': 'all'})
    expect(f, 'analysed by a person, open or closed; another label value still failed',
           {r['name']: r['cause'] for r in out['rows']}, {'h1': 'analyzed by a person', 'h2': 'analyzed by a person', 'm1': 'analysis failed'})
    return f


def check_routes():
    f = []
    ex = {'projects': 'team-a', 'status': 'resolved', 'range': '30d', 'view': 'table'}
    out = resolve(responses(), ex)
    expect(f, 'range chips: each keeps the rest, 7d left out', [(c['label'], c['active'], c['route']) for c in out['rangeChips']],
           [('24h', False, '/incidents?projects=team-a&status=resolved&range=24h&view=table'),
            ('7d', False, '/incidents?projects=team-a&status=resolved&view=table'),
            ('30d', True, '/incidents?projects=team-a&status=resolved&range=30d&view=table')])
    expect(f, 'view chips: cards drops ?view, table sets it', [(c['label'], c['active'], c['route']) for c in out['viewChips']],
           [('Summary', False, '/incidents?projects=team-a&status=resolved&range=30d'),
            ('Full list', True, '/incidents?projects=team-a&status=resolved&range=30d&view=table')])
    expect(f, 'state chips keep range and view', [c['route'] for c in out['chips']][:2],
           ['/incidents?projects=team-a&status=open&range=30d&view=table', '/incidents?projects=team-a&status=analyzing&range=30d&view=table'])
    href, _ = ask(out['askHref'])
    expect(f, 'Ask Agentiko keeps every param, in the page order', href, '/incidents?projects=team-a&status=resolved&range=30d&view=table')
    expect(f, 'an unknown range is 7d', resolve(responses(), {'range': '1y'})['range'], '7d')
    for name in ('incidents-status-chips', 'incidents-view-chips', 'incidents-range-chips'):
        tpl = widget_data('Listy', name, out, ex)['itemTemplate']
        expect(f, f'{name}: chips that navigate', (tpl['rowVariant'], tpl['navigateTo']), ('chip', '{route}'))
    return f


def check_search():
    f = []
    out = resolve(responses(), {'q': ' ORDERS '})
    expect(f, 'case-insensitive, trimmed: the open ones that match', (out['q'], [r['name'] for r in out['rows']]), ('ORDERS', ['inc-lat']))
    expect(f, 'the counts follow it', (out['counts']['open'], out['counts']['verifying'], out['counts']['analyzing'], out['counts']['all']), (1, 1, 0, 2))
    expect(f, 'the figures follow it', [s['value'] for s in out['stats'][:3]], ['1', '0', '1'])
    expect(f, 'by target and by alert too',
           ([r['name'] for r in timed({'q': 'snowplow'})['rows']], [r['name'] for r in resolve(responses(), {'q': 'disk', 'status': 'all'})['rows']]),
           (['o2'], ['inc-disk', 'inc-res']))
    expect(f, 'every route keeps it', ([c['route'] for c in out['chips']][:2], out['viewChips'][1]['route'], out['rangeChips'][0]['route']),
           (['/incidents?q=ORDERS', '/incidents?status=analyzing&q=ORDERS'], '/incidents?view=table&q=ORDERS', '/incidents?range=24h&q=ORDERS'))
    href, prompt = ask(out['askHref'])
    expect(f, 'Ask Agentiko keeps it and says so', (href, prompt.endswith(' These are only the incidents matching the search "ORDERS".')),
           ('/incidents?q=ORDERS', True))
    none = resolve(responses(), {'q': 'zzz'})
    expect(f, 'nothing matches: said', widget_data('Paragraph', 'incidents-empty', none, {'q': 'zzz'})['text'], 'No incident matches "zzz".')
    expect(f, 'nothing matches: the prompt says it, not "no incidents"', ask(none['askHref'])[1].startswith('No incident matches the search "zzz"; 5 incidents do not'), True)
    expect(f, 'the awaiting alerts are not searched', [a['name'] for a in none['awaiting']], [a['name'] for a in resolve(responses(), {})['awaiting']])
    return f


def check_layout():
    f = []
    out = resolve(responses(), {})
    expect(f, 'the page', items_of('Flex', 'page-incidents', out),
           ['incidents-page-header', 'incidents-toolbar', 'incidents-stats', 'incidents-main'])
    bar = widget_data('Row', 'incidents-toolbar', out)
    expect(f, 'the toolbar: the range left, the search in a quarter (as on /marketplace), stacked when narrow',
           [(i['resourceRefId'], i['size'], i['xs']) for i in bar['items']], [('incidents-range-group', 18, 24), ('incidents-search-wrap', 6, 24)])
    expect(f, 'the search wrap holds the search', items_of('Flex', 'incidents-search-wrap', out), ['incidents-search'])
    search = widget_data('Input', 'incidents-search', out)
    expect(f, 'the search writes ?q= on Enter, at the standard size', (search['queryParam'], search['allowClear'], search['size']), ('q', True, 'middle'))
    row = widget_data('Row', 'incidents-main', out)
    expect(f, 'two thirds and one third, stacked when narrow', [(i['resourceRefId'], i['size'], i['xs']) for i in row['items']],
           [('incidents-list-card', 16, 24), ('incidents-side', 8, 24)])
    card = widget_data('Card', 'incidents-list-card', out)
    expect(f, 'the incidents card: chips, then the cards; Summary | Full list top right',
           ([i['resourceRefId'] for i in card['items']], card['extraRefId']),
           (['incidents-status-chips', 'incidents-cards'], 'incidents-view-chips'))
    tbl = resolve(responses(), {'view': 'table'})
    expect(f, '?view=table: the table instead', items_of('Card', 'incidents-list-card', tbl, {'view': 'table'}),
           ['incidents-status-chips', 'incidents-table'])
    expect(f, 'the charts, and the alerts firing with no incident while there are some', items_of('Flex', 'incidents-side', out),
           ['incidents-by-state-card', 'incidents-trend-card', 'incidents-ttr-card', 'incidents-awaiting-card'])
    quiet = resolve(responses(alerts={'items': [alert('disk', 'Disk pressure', 'OK')]}), {})
    expect(f, 'no such alert: no card', items_of('Flex', 'incidents-side', quiet),
           ['incidents-by-state-card', 'incidents-trend-card', 'incidents-ttr-card'])
    for kind, name in (('Flex', 'incidents-range-group'), ('Paragraph', 'incidents-range-label'), ('Listy', 'incidents-stats'),
                       ('Card', 'incidents-by-state-card'), ('Card', 'incidents-trend-card'), ('Card', 'incidents-awaiting-card'),
                       ('Listy', 'incidents-awaiting')):
        widget_data(kind, name, out)
    hdr = widget_data('PageHeader', 'incidents-page-header', out)
    expect(f, 'header: no subtitle, the button alone', ('subtitle' in hdr, [i['resourceRefId'] for i in hdr['items']]),
           (False, ['incidents-ask-agentiko']))
    btn = tbi.resolved_widget(CH, 'Button', 'incidents-ask-agentiko', out, {}, 'ask button')['spec']
    expect(f, 'button: the RA\'s link', btn['widgetData']['actions']['navigate'][0]['path'], out['askHref'])
    expect(f, 'button: label and intent', (btn['widgetData']['label'], btn['widgetData']['intent']), ('Ask Agentiko →', 'agentiko'))
    expect(f, 'button, no data: the list', tbi.widget(CH, 'Button', 'incidents-ask-agentiko', 'actions', {}, {})['navigate'][0]['path'], '/incidents')
    return f


def check_cards_and_table():
    f = []
    out = timed()
    cards = widget_data('Listy', 'incidents-cards', out)
    tpl = cards['itemTemplate']
    expect(f, 'a card per incident, opening it', (tpl['rowVariant'], tpl['navigateTo'], tpl['secondaryText'], tpl['description']),
           ('card', '/incidents/{namespace}/{name}', '{state}', '{cause}'))
    rows = {r['name']: r for r in cards['dataSource']}
    expect(f, 'the open ones, newest first', list(rows), ['o1', 'o2', 'o3'])
    expect(f, 'target: the composition the analysis read, else the first object',
           [rows[n]['target'] for n in ('o1', 'o2', 'o3')], ['fireworksapps/fw-prod', 'deployments/snowplow', ''])
    expect(f, 'category, capitalized', [rows[n]['category'] for n in ('o1', 'o2')], ['Permission', ''])
    expect(f, 'a wrench where a fix can be applied (applyAction or an apply script)',
           [rows[n]['fixIcon'] for n in ('o1', 'o2', 'o3')], ['fa-wrench', '', 'fa-wrench'])
    ver = widget_data('Listy', 'incidents-cards', timed({'status': 'verifying'}), {'status': 'verifying'})['dataSource']
    expect(f, 'no wrench once it is not Open', [r['fixIcon'] for r in ver], [''])
    # INC-7: the tile's mark is the state's, so a Resolved card shows no warning triangle.
    expect(f, 'the tile mark is the row\'s', tpl['icon'], '{stateIcon}')
    every = widget_data('Listy', 'incidents-cards', resolve(responses(), {'status': 'all'}), {'status': 'all'})['dataSource']
    expect(f, 'a mark per state', sorted({(r['state'], r['stateIcon']) for r in every}),
           [('Analyzing', 'fa-magnifying-glass'), ('Open', 'fa-triangle-exclamation'), ('Resolved', 'fa-circle-check'),
            ('Verifying', 'fa-hourglass-half')])
    closed = resolve(responses(incidents={'items': [incident('c1', 'Closed', ago(30), alert='disk', resolved=ago(2))]}), {'status': 'closed'})
    expect(f, 'a Closed card: the cross', [r['stateIcon'] for r in closed['rows']], ['fa-circle-xmark'])
    table = widget_data('Table', 'incidents-table', out, {'view': 'table'})
    expect(f, 'the table: every column', [c['valueKey'] for c in table['columns']], ['sev', 'title', 'cause', 'category', 'target', 'state', 'age'])
    first = {c['valueKey']: c.get('stringValue') for c in table['dataSource'][0]}
    expect(f, 'the table row', (first['title'], first['category'], first['target'], first['state']), ('A', 'Permission', 'fireworksapps/fw-prod', 'Open'))
    return f


def check_awaiting():
    f = []
    alerts = [alert('disk-free', 'ClickHouse disk below 20% free', opens=('False', 'AggregatedValue', AGG), typ='Metric'),
              alert('cost', 'LLM spend over budget', opens=('False', 'AggregatedValue', AGG), typ='LogValue'),
              alert('pull', 'Image pull failing', opens=('True', 'CountsRecords', 'A firing opens an incident.'), typ='Logs'),
              alert('muted', 'Muted alert', paused=True, opens=('False', 'Paused', 'Paused.'), typ='Logs'),
              alert('older', 'Older provider')]  # no condition: an alert-provider before OpensIncidents
    out = resolve(responses(incidents={'items': []}, alerts={'items': alerts}), {})
    expect(f, 'why each has no incident, the not-yet ones first, paused left out',
           [(a['name'], a['why']) for a in out['awaiting']],
           [('Image pull failing', 'Incident not opened yet'), ('Older provider', ''),
            ('ClickHouse disk below 20% free', 'Metric · opens no incidents'), ('LLM spend over budget', 'Value · opens no incidents')])
    prompts = {a['name']: ask(a['askHref'])[1] for a in out['awaiting']}
    expect(f, 'a metric alert\'s prompt says it never opens one', prompts['ClickHouse disk below 20% free'],
           'Investigate the "ClickHouse disk below 20% free" observability alert — it is firing but has no open incident. '
           'It never opens one: ' + AGG + ' Run a root-cause analysis and give me the concrete next steps.')
    expect(f, 'a counting alert\'s prompt is unchanged', prompts['Image pull failing'],
           'Investigate the "Image pull failing" observability alert — it is firing but has no open incident. '
           'Run a root-cause analysis and give me the concrete next steps.')
    lst = widget_data('Listy', 'incidents-awaiting', out)
    expect(f, 'the list: alert, why, opens Agentiko on it',
           ([(a['name'], a['why']) for a in lst['dataSource']], lst['itemTemplate']['navigateTo'], lst['header']),
           ([(a['name'], a['why']) for a in out['awaiting']], '{askHref}', ''))
    return f


# Display names as alert authors write them. The sanitizer exists to drop a decorative emoji
# (7f414fb, "strip the leading emoji"); punctuation such as the em dash is part of the name.
NAMES = [('Krateo — composition reconcile errors', 'Krateo — composition reconcile errors'),
         ('🚨 Pod crash-looping', 'Pod crash-looping'),
         ('⚠️  Disk  full ', 'Disk full'),
         ('👩🏽‍💻 Dev box', 'Dev box'),
         ('Café · ünïcode – ok', 'Café · ünïcode – ok')]


def check_display_names():
    f = []
    alerts = [alert(f'a{i}', shown, 'OK') for i, (shown, _) in enumerate(NAMES)]
    incs = [incident(f'i{i}', 'Open', ago(10 + i), alert=f'a{i}', cause='c') for i in range(len(NAMES))]
    out = resolve(responses(incidents={'items': incs}, alerts={'items': alerts}), {})
    got = {r['alert']: r['title'] for r in out['rows']}
    for i, (shown, want) in enumerate(NAMES):
        expect(f, f'display name {shown!r}: the emoji dropped, the punctuation kept, one space, trimmed', got.get(f'a{i}'), want)
    titles = [c['title'] for c in widget_data('Listy', 'incidents-cards', out)['dataSource']]
    expect(f, 'and so does the card title', NAMES[0][1] in titles, True)
    return f


def check_filter_keys():
    f = []
    for d in CH.docs:
        if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/') and d['metadata']['name'].startswith(('incidents-', 'page-incidents')):
            expect(f, f'{d["kind"]} {d["metadata"]["name"]} declares every page param', d['spec'].get('keyExtras'), KX)
    root = CH.get('Flex', 'page-incidents')
    expect(f, 'the page root names them', root['metadata']['annotations'].get('krateo.io/filter-keys'), ','.join(KX))
    return f


def check_resolved_in_range():
    f = []
    for rng, want in (('24h', ['r1']), (None, ['r1', 'r2']), ('30d', ['r1', 'r2', 'r3'])):
        ex = {'status': 'resolved', **({'range': rng} if rng else {})}
        incs = TIMED + [incident('r-noat', 'Resolved', ago(2), alert='a', cause='c9')]  # Resolved, no resolution.at
        out = resolve(responses(incidents={'items': incs}, alerts={'items': [alert('a', 'A', 'OK')]}), ex)
        tile = {s['title']: s['value'] for s in out['stats']}['Resolved']
        chip = {c['label']: c['count'] for c in out['chips']}['Resolved']
        cards = [r['name'] for r in widget_data('Listy', 'incidents-cards', out, ex)['dataSource']]
        table = widget_data('Table', 'incidents-table', out, {**ex, 'view': 'table'})['dataSource']
        names = [{c['valueKey']: c.get('stringValue') for c in row}['name'] for row in table]
        expect(f, f'{rng or "7d"}: Resolved figure == chip count == cards == table rows, only the range\'s',
               (tile, chip, cards, names, out['matched']), (str(len(want)), len(want), want, want, len(want)))
        expect(f, f'{rng or "7d"}: the live chips and All count every incident; Closed, its range\'s (c1, closed 29 h ago)',
               [(c['label'], c['count']) for c in out['chips'] if c['label'] != 'Resolved'],
               [('Open', 3), ('Analyzing', 1), ('Verifying', 1), ('Closed', 0 if rng == '24h' else 1), ('All', 10)])
    out = resolve(responses(incidents={'items': TIMED[:5] + TIMED[7:]}, alerts={'items': [alert('a', 'A', 'OK')]}),
                  {'status': 'resolved'})  # only r3, 20 days ago
    expect(f, 'none resolved in the range: zero, the empty line says the range',
           ({s['title']: s['value'] for s in out['stats']}['Resolved'], out['rows'], items_of('Card', 'incidents-list-card', out, {'status': 'resolved'}),
            widget_data('Paragraph', 'incidents-empty', out, {'status': 'resolved'})['text']),
           ('0', [], ['incidents-status-chips', 'incidents-empty'], 'No incident resolved in the last 7 days.'))
    expect(f, '24h: the empty line follows the range',
           widget_data('Paragraph', 'incidents-empty', resolve(responses(incidents={'items': TIMED[:5]}), {'status': 'resolved', 'range': '24h'}),
                       {'status': 'resolved', 'range': '24h'})['text'], 'No incident resolved in the last 24 hours.')
    _, prompt = ask(out['askHref'])
    expect(f, 'none resolved in the range: the prompt says so', prompt,
           'No incident was resolved in the last 7 days; my 7 incidents are in other states or resolved before that, with '
           'no alert firing without an incident. Tell me whether anything here needs attention — grounded in the evidence on this page.')
    return f


def check_closed_in_range():
    """UX audit INC-4: with 7d picked, the Resolved figure and chip said 3 while the donut's Resolved
    slice and the Closed chip counted every incident ever. Both states an incident ends in are the
    range's: the Closed chip, its cards and table, and each donut slice are the chip's own count."""
    f = []
    closed = [incident('k1', 'Closed', ago(30), alert='a', cause='k1', resolved=ago(20)),
              incident('k2', 'Closed', ago(200), alert='a', cause='k2', resolved=ago(100)),
              incident('k3', 'Closed', ago(800), alert='a', cause='k3', resolved=ago(700)),
              incident('k-noat', 'Closed', ago(2), alert='a', cause='k4')]  # Closed, no resolution.at: in no range
    incs = TIMED[:8] + closed
    for rng, want in (('24h', ['k1']), (None, ['k1', 'k2']), ('30d', ['k1', 'k2', 'k3'])):
        ex = {'status': 'closed', **({'range': rng} if rng else {})}
        out = resolve(responses(incidents={'items': incs}, alerts={'items': [alert('a', 'A', 'OK')]}), ex)
        chip = {c['label']: c['count'] for c in out['chips']}
        cards = [r['name'] for r in widget_data('Listy', 'incidents-cards', out, ex)['dataSource']]
        table = widget_data('Table', 'incidents-table', out, {**ex, 'view': 'table'})['dataSource']
        names = [{c['valueKey']: c.get('stringValue') for c in row}['name'] for row in table]
        expect(f, f'{rng or "7d"}: Closed chip count == cards == table rows, only the range\'s',
               (chip['Closed'], cards, names, out['matched']), (len(want), want, want, len(want)))
        donut = {d['state']: d['count'] for d in widget_data('PieChart', 'incidents-by-state', out, ex)['data']}
        expect(f, f'{rng or "7d"}: each donut slice is its chip\'s count (no slice for a zero)',
               donut, {c['label']: c['count'] for c in out['chips'] if c['label'] != 'All' and c['count'] > 0})
        expect(f, f'{rng or "7d"}: All is every incident', chip['All'], len(incs))
    return f


def check_full_list_shows_all():
    f = []
    vc = lambda o: [(c['label'], c['active'], c['route']) for c in o['viewChips']]
    act = lambda o: [c['label'] for c in o['chips'] if c['active']]
    out = resolve(responses(), {'view': 'table'})
    expect(f, 'Full list with no ?status shows every incident', (out['status'], len(out['rows'])), ('all', 5))
    expect(f, 'Full list with no ?status: All is the active chip', act(out), ['All'])
    expect(f, 'Full list with no ?status -> Summary drops the implicit all; Summary -> Full list adds none', vc(out),
           [('Summary', False, '/incidents'), ('Full list', True, '/incidents?view=table')])
    out = resolve(responses(), {})
    expect(f, 'Summary with no ?status still defaults to Open', (out['status'], len(out['rows']), act(out)), ('open', 2, ['Open']))
    expect(f, 'Summary with no ?status -> Full list: no ?status, so All', vc(out),
           [('Summary', True, '/incidents'), ('Full list', False, '/incidents?view=table')])
    out = resolve(responses(), {'view': 'table', 'status': 'resolved'})
    expect(f, 'Full list with ?status=resolved: the picked state applies and is active', (len(out['rows']), act(out)), (1, ['Resolved']))
    expect(f, 'an explicit status is kept across the switch, both ways', vc(out),
           [('Summary', False, '/incidents?status=resolved'), ('Full list', True, '/incidents?status=resolved&view=table')])
    out = resolve(responses(), {'view': 'table', 'status': 'open'})
    expect(f, 'Full list with ?status=open: Open applies and is active', (len(out['rows']), act(out)), (2, ['Open']))
    expect(f, 'Full list -> Summary: an explicit Open is the Summary default, left out', vc(out)[0][2], '/incidents')
    chips = {c['label']: c['route'] for c in resolve(responses(), {'view': 'table'})['chips']}
    expect(f, 'Full list: the Open chip names its state, the All chip is the default and leaves it out',
           (chips['Open'], chips['All']), ('/incidents?status=open&view=table', '/incidents?view=table'))
    return f


def check_tile_routes():
    f = []
    ex = {'projects': 'team-a', 'status': 'verifying', 'range': '30d', 'view': 'table', 'q': 'a b'}
    out = timed(ex)
    chip = {c['label']: c['route'] for c in out['chips']}
    expect(f, 'a state figure routes as its chip (q, view, range, projects kept); Time to resolve does not',
           [(s['title'], s['route']) for s in out['stats']],
           [(t, chip[t]) for t in ('Open', 'Analyzing', 'Verifying', 'Resolved')] + [('Time to resolve', '')])
    expect(f, 'Open is the Summary default, not the Full list one: it is named in the table view, the rest kept', out['stats'][0]['route'],
           '/incidents?projects=team-a&status=open&range=30d&view=table&q=a%20b')
    zero = resolve(responses(incidents={'items': []}), {})
    expect(f, 'a zero figure still routes', [s['route'] for s in zero['stats']],
           ['/incidents', '/incidents?status=analyzing', '/incidents?status=verifying', '/incidents?status=resolved', ''])
    failed = resolve(responses(incidents=['dial tcp: i/o timeout']), ex)
    expect(f, 'a failed read is not clickable', [s['route'] for s in failed['stats']], [''] * 5)
    tpl = widget_data('Listy', 'incidents-stats', out, ex)['itemTemplate']
    expect(f, 'the figures navigate to their route', (tpl['rowVariant'], tpl['navigateTo']), ('statistic', '{route}'))
    return f


CHECKS = [check_default_is_open, check_full_list_shows_all, check_no_model_analysis, check_status_param, check_empty_and_failed, check_figures_and_charts, check_resolved_in_range, check_closed_in_range, check_tile_routes, check_routes,
          check_search, check_layout, check_cards_and_table, check_awaiting, check_display_names, check_filter_keys]


def main():
    global CH
    args = sys.argv[1:]
    crds_dir = args[args.index('--crds') + 1] if '--crds' in args else None
    CH = tbi.Chart(tbi.render(CHART))
    failed = 0
    for check in CHECKS:
        try:
            problems = check()
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {check.__name__.replace("check_", "").replace("_", " ")}')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    total = len(CHECKS)
    if crds_dir:
        total += 1
        problems = tbi.validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} resolved widgets validate against their CRDs')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}{"" if crds_dir else "; resolved widgets NOT validated (no --crds)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
