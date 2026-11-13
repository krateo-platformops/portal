#!/usr/bin/env python3
"""
test-incidents-list — /incidents, the overview and its "Ask Agentiko →", over the chart's own jq.

WHAT IT GUARDS.
THE OVERVIEW (root-pages UX, build plan §3.2, mockup screen 06; decisions of 2026-11-13):
  - each facet (Alert, State, Age, Owner) counts its values under every OTHER filter, and search
    narrows them all; a facet with one value over every incident is left out of the filter bar
    unless one of its values is applied (Q2); the applied values are chips that remove themselves;
  - Owner: Krateo when the alert carries krateo.io/composition-name, Yours when the alert exists
    without it, Alert deleted when it no longer exists; with the alerts unread, nobody owns anything
    and the facet hides (an unread alert is not a deleted one);
  - ?status= is still the State param: /incidents?status=open lands filtered, any case, `all` is no
    filter, and it takes several values now;
  - the strip counts the filtered set (Open red, open longer than 7 days orange); a failed read is
    "—", never 0;
  - the active incidents: one card per alert, largest first, or with ?group=age per age bucket,
    newest first; Triage opens the newest; a card holding several opens the table view filtered to
    that alert (or age) and state, "See all N" (Q1); six cards, then the state's table;
  - Resolved and Closed are folded, their six newest, then "See all N →" to that state's table;
  - ?view=table swaps the overview for today's table, filtered, and the toggle keeps every filter
    both ways.
THE PROMPT. incidents-list builds the header button's `askHref`: /incidents with every filter param
and the ?projects= scope kept, and a prompt about the view on screen. The rules a reader would notice:
  - unfiltered, the prompt gives the per-state counts, the alerts firing with no incident, and the
    open incidents (title, root cause, the DAY each was triggered), at most eight;
  - with one state picked and nothing else, it is about that state's incidents only, as the chip's
    was; a done state (Resolved, Closed) asks for a review, not a triage; a state with no incidents
    says so;
  - any other filter is named in the prompt ("Alert: Probe failing; State: Open; search ...") and the
    prompt is about the incidents it matches;
  - a ?status= that is no state matches nothing, and is a chip that removes it (portal.facets: an
    unknown URL value is listed at 0 so it can be taken out);
  - A FAILED READ IS NOT A QUIET PLATFORM: an Incident list that could not be read asks what the error
    means, and an alerts read that failed is never "no alert is firing". snowplow accumulates an
    errorKey as a LIST whose elements are strings or the apiserver's Status object
    (resolve.go accumulateErrorKey); both shapes are fed here;
  - the existing per-alert `awaiting[].askHref` keeps its path;
  - the "Firing alerts with no incident" section is a second table laid out like the incident table:
    each alert says why it has none, off alert-provider's OpensIncidents condition (a metric or value
    alert opens no incidents, a counting alert has not opened one yet, listed first), and an
    "Ask Agentiko →" pill says what a row click does; the prompt says when the alert never opens one;
    a paused alert is not listed; the page shows the section only while it has rows.
The fixtures of the overview checks are dated relative to now, so the age buckets hold on any day.
Every widget is resolved over the same output, and with --crds every resolved widget is validated
against its CRD (the frontend's CRDs must carry Collapse, Listy `statistic`, Select counts/prefix/
panelFooter and Flex role/ariaLabel: frontend 1.8.43).

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


def incident(name, state, triggered, alert=None, cause=None, error=None):
    st = {} if state is None else {'state': state}
    if cause:
        st['rootCause'] = {'statement': cause}
    if error:
        st['error'] = error
    meta = {'name': name, 'creationTimestamp': triggered}
    if alert:
        meta['labels'] = {'observability.krateo.io/alert': alert}
    return {'metadata': meta, 'spec': {'triggeredAt': triggered}, 'status': st}


def alert(name, display, state='ALERT', opens=None, typ=None, paused=False, krateo=False):
    st = {'state': state}
    if typ:
        st['type'] = typ
    if opens:  # (status, reason, message): alert-provider's OpensIncidents condition
        st['conditions'] = [{'type': 'Ready', 'status': 'True', 'reason': 'Available'},
                            {'type': 'OpensIncidents', 'status': opens[0], 'reason': opens[1], 'message': opens[2]}]
    meta = {'name': name}
    if paused:
        meta['annotations'] = {'krateo.io/paused': 'true'}
    if krateo:
        meta['labels'] = {'krateo.io/composition-name': 'krateo-observability'}
    return {'metadata': meta, 'spec': {'displayName': display}, 'status': st}


AGG = 'It compares an aggregated value; only an alert that counts log lines or spans opens incidents.'


INCIDENTS = [
    incident('inc-lat', 'Open', '2026-11-08T10:00:00Z', alert='api-latency', cause='connection pool exhausted on orders-db'),
    incident('inc-disk', 'Open', '2026-11-06T09:00:00Z', alert='disk', cause='PVC data-pg-0 at 97%'),
    incident('inc-new', None, '2026-11-09T11:00:00Z', alert='crashloop'),                 # Analyzing
    incident('inc-ver', 'Verifying', '2026-11-07T08:00:00Z', alert='api-latency', cause='rolled back orders-api'),
    incident('inc-res', 'Resolved', '2026-11-05T07:00:00Z', alert='disk', cause='log volume filled /var'),
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


def check_unfiltered():
    f = []
    out = resolve(responses(), {'projects': 'team-a'})
    href, prompt = ask(out['askHref'])
    expect(f, 'self page, scope kept, no chip', href, '/incidents?projects=team-a')
    expect(f, 'counts, awaiting, the open ones', prompt,
           'Triage my incidents: 2 open, 1 verifying, 1 still being analyzed, and 1 alert firing with no incident '
           '(Pod restarts). The open ones are: 1. "API latency p95" — connection pool exhausted on orders-db '
           '(triggered 2026-11-08) 2. "Disk pressure" — PVC data-pg-0 at 97% (triggered 2026-11-06).' + TAIL)
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


def check_filtered():
    f = []
    href, prompt = ask(resolve(responses(), {'status': 'open', 'projects': 'team-a'})['askHref'])
    expect(f, 'open state: kept with the scope', href, '/incidents?projects=team-a&status=open')
    expect(f, 'open chip: only the open ones', prompt,
           'Triage my open incidents: 2 of 5 incidents. They are: 1. "API latency p95" — connection pool exhausted on '
           'orders-db (triggered 2026-11-08) 2. "Disk pressure" — PVC data-pg-0 at 97% (triggered 2026-11-06).' + TAIL)
    href, prompt = ask(resolve(responses(), {'status': 'Resolved'})['askHref'])
    expect(f, 'resolved chip, any case: kept', href, '/incidents?status=resolved')
    expect(f, 'resolved chip: a review', prompt,
           'Review my resolved incidents: 1 of 5 incidents. It is: 1. "Disk pressure" — log volume filled /var '
           '(triggered 2026-11-05). Tell me whether any looks resolved without its cause being fixed, and whether any '
           'is likely to come back — grounded in the evidence on this page.')
    href, prompt = ask(resolve(responses(), {'status': 'closed'})['askHref'])
    expect(f, 'empty chip: kept', href, '/incidents?status=closed')
    expect(f, 'empty chip: says so', prompt,
           'I have no closed incidents; my 5 incidents are in other states, with 1 alert firing with no incident '
           '(Pod restarts). Tell me whether anything here needs attention — grounded in the evidence on this page.')
    out = resolve(responses(), {'status': 'foo', 'projects': 'team-a'})
    href, prompt = ask(out['askHref'])
    expect(f, 'not a state: no rows, as before', out['rows'], [])
    expect(f, 'not a state: kept, so the chip can take it out', href, '/incidents?projects=team-a&status=foo')
    expect(f, 'not a state: a removable chip at 0', [(c['label'], c['href']) for c in out['chips']],
           [('State: foo', '/incidents?projects=team-a'), ('Clear all', '/incidents?projects=team-a')])
    expect(f, 'not a state: the prompt says nothing matches', prompt,
           'No incident matches the view on screen (State: foo); my 5 incidents are outside it, with 1 alert firing with '
           'no incident (Pod restarts). Tell me whether anything here needs attention — grounded in the evidence on this page.')
    return f


def check_empty_and_failed():
    f = []
    _, prompt = ask(resolve(responses(incidents={'items': []}), {})['askHref'])
    expect(f, 'no incidents, an alert firing', prompt,
           'There are no incidents on this platform, and 3 alerts firing with no incident (API latency p95, Pod '
           'crashloop, Pod restarts). Tell me which alert to investigate first and why — grounded in the evidence on this page.')
    _, prompt = ask(resolve(responses(incidents={'items': []}, alerts={'items': []}), {})['askHref'])
    expect(f, 'nothing at all', prompt,
           'There are no incidents on this platform, and no alert firing without an incident. Tell me whether anything '
           'on this page needs attention.')
    status = {'kind': 'Status', 'status': 'Failure', 'code': 403, 'reason': 'Forbidden',
              'message': 'incidents.observability.krateo.io is forbidden: User "erin" cannot list resource'}
    for label, errs, said in (('a Status object', [status], status['message']),
                              ('a string', ['dial tcp: i/o timeout'], 'dial tcp: i/o timeout')):
        out = resolve(responses(incidents=errs), {'status': 'open'})
        href, prompt = ask(out['askHref'])
        expect(f, f'failed read ({label}): flagged', (out['failed'], out['readError']), (True, said))
        expect(f, f'failed read ({label}): asks about the error', prompt,
               'The incidents on this page could not be read (' + said + '). Explain what that error means and what to check.')
        expect(f, f'failed read ({label}): keeps the chip', href, '/incidents?status=open')
    _, prompt = ask(resolve(responses(alerts=['dial tcp: i/o timeout']), {})['askHref'])
    expect(f, 'alerts unread: never "no alert firing"', 'no alert firing' in prompt, False)
    expect(f, 'alerts unread: said', 'an unknown number of alerts firing with no incident (the alerts could not be read)' in prompt, True)
    out = resolve(responses(), {})
    expect(f, 'healthy read: not failed', (out['failed'], out['readError']), (False, ''))
    return f


def check_button_and_header():
    f = []
    ex = {'status': 'open', 'projects': 'team-a'}
    out = resolve(responses(), ex)
    btn = tbi.resolved_widget(CH, 'Button', 'incidents-ask-agentiko', out, ex, 'ask button')['spec']
    expect(f, 'button: the RA\'s link', btn['widgetData']['actions']['navigate'][0]['path'], out['askHref'])
    expect(f, 'button: label and intent', (btn['widgetData']['label'], btn['widgetData']['intent']), ('Ask Agentiko →', 'agentiko'))
    expect(f, 'button: keyExtras match the table', btn['keyExtras'],
           CH.get('Table', 'incidents-table')['spec']['keyExtras'])
    expect(f, 'button, no data: the list', tbi.widget(CH, 'Button', 'incidents-ask-agentiko', 'actions', {}, {})['navigate'][0]['path'], '/incidents')
    hdr = tbi.resolved_widget(CH, 'PageHeader', 'incidents-page-header', {}, ex, 'header')['spec']['widgetData']
    expect(f, 'header: the button, alone', ([i['resourceRefId'] for i in hdr['items']], hdr['allowedResources']),
           (['incidents-ask-agentiko'], ['buttons']))
    tbi.resolved_widget(CH, 'Table', 'incidents-table', out, ex, 'table')
    return f


def check_awaiting_section():
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
    table = tbi.resolved_widget(CH, 'Table', 'incidents-awaiting', out, {}, 'awaiting table')['spec']['widgetData']
    incidents = CH.get('Table', 'incidents-table')['spec']['widgetData']
    expect(f, 'laid out like the incident table', [(c['valueKey'], c['title']) for c in table['columns']],
           [('sev', ' '), ('name', 'Alert'), ('why', 'Why no incident'), ('ask', ' ')])
    expect(f, 'same table style', {k: table[k] for k in ('fitContent', 'bordered', 'size')},
           {k: incidents[k] for k in ('fitContent', 'bordered', 'size')})
    rows = [{c['valueKey']: (c.get('stringValue'), c.get('kind'), c.get('color')) for c in r} for r in table['dataSource']]
    expect(f, 'rows: alert, why (— when unknown)', [(r['name'][0], r['why'][0]) for r in rows],
           [(a['name'], a['why'] or '—') for a in out['awaiting']])
    expect(f, 'every row carries the Ask Agentiko pill', {r['ask'] for r in rows}, {('Ask Agentiko →', 'tag', 'primary')})
    expect(f, 'row click opens Agentiko on that alert', (table['rowNavigateTo'], [r['askHref'][0] for r in rows]),
           ('{askHref}', [a['askHref'] for a in out['awaiting']]))
    title = tbi.resolved_widget(CH, 'Paragraph', 'incidents-awaiting-title', {}, {}, 'section title')['spec']['widgetData']
    expect(f, 'section title, no em dash', (title['text'], title.get('level')), ('Firing alerts with no incident', 2))
    tbi.resolved_widget(CH, 'Flex', 'incidents-awaiting-section', {}, {}, 'section')
    page = lambda o: [i['resourceRefId'] for i in tbi.resolved_widget(CH, 'Flex', 'page-incidents', o, {}, 'page')['spec']['widgetData']['items']]
    expect(f, 'the page shows the section while it has rows (no incident: the calm line says so)', page(out),
           ['incidents-page-header', 'incidents-filter-bar', 'incidents-strip', 'incidents-calm', 'incidents-awaiting-section'])
    expect(f, 'no incident, alerts firing: the calm line does not claim the alerts are quiet',
           widget_data('Paragraph', 'incidents-calm', out)['text'], 'There are no incidents.')
    busy = resolve(responses(), {})
    expect(f, 'with incidents and such alerts: the section above the active incidents, no calm line', page(busy),
           ['incidents-page-header', 'incidents-filter-bar', 'incidents-group-toggle', 'incidents-strip', 'incidents-awaiting-section',
            'incidents-groups', 'incidents-done', 'incidents-view-toggle'])
    quiet = resolve(responses(alerts={'items': [alert('disk', 'Disk pressure', 'OK')]}), {})
    expect(f, 'and leaves it out when none, for the calm line', page(quiet),
           ['incidents-page-header', 'incidents-filter-bar', 'incidents-group-toggle', 'incidents-strip', 'incidents-calm',
            'incidents-groups', 'incidents-done', 'incidents-view-toggle'])
    expect(f, 'the calm line', widget_data('Paragraph', 'incidents-calm', quiet)['text'],
           'No alert is firing without an incident (0 of 1 alerts in state ALERT).')
    hdr = tbi.resolved_widget(CH, 'PageHeader', 'incidents-page-header', {}, {}, 'header')['spec']['widgetData']
    expect(f, 'subtitle', hdr['subtitle'],
           'Each incident tracks one problem an alert caught, from finding its cause until it is fixed or someone closes it.')
    return f


# ---------------------------------------------------------------------------------------------
# The overview: facets, strip, groups, caps, the table view
# ---------------------------------------------------------------------------------------------

NOW = datetime.datetime.now(datetime.timezone.utc)
KX = ['projects', 'status', 'alert', 'age', 'owner', 'q', 'group', 'view']


def ago(hours):
    return (NOW - datetime.timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%SZ')


def day(hours):
    return ago(hours)[:10]


# probe: a Krateo alert (4 incidents), oom: yours (2), gone: deleted since (2). One per age bucket pair.
OV_INCIDENTS = [
    incident('p1', 'Open', ago(2), alert='probe', cause='readiness probe on 8081 without a delay'),
    incident('p2', 'Open', ago(30), alert='probe', cause='probe timeout after the upgrade'),
    incident('p3', 'Open', ago(240), alert='probe', cause='probe flapping'),
    incident('m1', 'Open', ago(72), alert='oom', cause='mongod OOMKilled at 500M'),
    incident('m2', 'Resolved', ago(960), alert='oom', cause='mongod OOMKilled earlier'),
    incident('g1', 'Open', ago(480), alert='gone', cause='an alert deleted since'),
    incident('g2', 'Closed', ago(1200), alert='gone', cause='closed by hand'),
    incident('v1', 'Verifying', ago(5), alert='probe', cause='rolled back the probe'),
]
OV_ALERTS = [alert('probe', 'Probe failing', 'OK', krateo=True), alert('oom', 'Pod killed for memory', 'OK')]


def overview(extras=None, **over):
    r = {'incidents': {'items': OV_INCIDENTS}, 'alerts': {'items': OV_ALERTS}}
    r.update(over)
    return resolve(r, extras or {})


def widget_data(kind, name, out, extras=None):
    return tbi.resolved_widget(CH, kind, name, out, extras or {}, f'{kind} {name}')['spec']['widgetData']


def facet(out, key):
    return [x for x in out['facets'] if x['key'] == key][0]


def counts(out, key):
    return {v['value']: v['count'] for v in facet(out, key)['values']}


def page_items(out, extras=None):
    return [i['resourceRefId'] for i in widget_data('Flex', 'page-incidents', out, extras)['items']]


def bar(out):
    return [i['resourceRefId'] for i in widget_data('Flex', 'incidents-filter-bar', out)['items']]


def check_facets():
    f = []
    out = overview()
    expect(f, 'no filter: alert counts', counts(out, 'alert'), {'probe': 4, 'oom': 2, 'gone': 2})
    expect(f, 'no filter: alerts labelled with their display name, else their name',
           {v['value']: v['label'] for v in facet(out, 'alert')['values']}, {'probe': 'Probe failing', 'oom': 'Pod killed for memory', 'gone': 'gone'})
    expect(f, 'no filter: state counts', counts(out, 'status'), {'open': 5, 'verifying': 1, 'resolved': 1, 'closed': 1})
    expect(f, 'no filter: age buckets', counts(out, 'age'), {'24h': 2, '7d': 2, '30d': 2, 'older': 2})
    expect(f, 'owner: Krateo when the alert carries krateo.io/composition-name, yours without, deleted when gone',
           counts(out, 'owner'), {'krateo': 4, 'yours': 2, 'deleted': 2})
    expect(f, 'owner labels', {v['value']: v['label'] for v in facet(out, 'owner')['values']},
           {'krateo': 'Krateo', 'yours': 'Yours', 'deleted': 'Alert deleted'})
    out = overview({'status': 'open'})
    expect(f, '?status=open: the state counts ignore their own filter', counts(out, 'status'),
           {'open': 5, 'verifying': 1, 'resolved': 1, 'closed': 1})
    expect(f, '?status=open: the alert counts apply it', counts(out, 'alert'), {'probe': 3, 'oom': 1, 'gone': 1})
    expect(f, '?status=open: the owner counts apply it', counts(out, 'owner'), {'krateo': 3, 'yours': 1, 'deleted': 1})
    expect(f, '?status=open: the age counts apply it (a value with none left stays, at 0)', counts(out, 'age'),
           {'24h': 1, '7d': 2, '30d': 2, 'older': 0})
    out = overview({'alert': 'probe', 'age': '24h'})
    expect(f, 'alert and age: the alert counts apply the age only', counts(out, 'alert'), {'probe': 2, 'oom': 0, 'gone': 0})
    expect(f, 'alert and age: the age counts apply the alert only', counts(out, 'age'), {'24h': 2, '7d': 1, '30d': 1, 'older': 0})
    expect(f, 'alert and age: matched', [r['name'] for r in out['rows']], ['p1', 'v1'])
    out = overview({'owner': 'deleted'})
    expect(f, '?owner=deleted: the incidents of the deleted alert', sorted(r['name'] for r in out['rows']), ['g1', 'g2'])
    # Search: title and root cause, any case; the facet counts follow it.
    out = overview({'q': '  PROBE Timeout '})
    expect(f, 'search: title and root cause, case-insensitive, trimmed', [r['name'] for r in out['rows']], ['p2'])
    expect(f, 'search: the facet counts follow it', counts(out, 'status'), {'open': 1, 'verifying': 0, 'resolved': 0, 'closed': 0})
    expect(f, 'search: the facets stay visible (decided over every incident)', [x['show'] for x in out['facets']], [True] * 4)
    # The Selects: URL-bound, counted, lifecycle and age order where it is an order.
    out = overview()
    sel = widget_data('Select', 'incidents-status', out)
    expect(f, 'State Select: ?status=, multiple, counted', (sel['queryParam'], sel['mode'], sel['prefix']), ('status', 'multiple', 'State'))
    expect(f, 'State Select: lifecycle order', [o['value'] for o in sel['options']], ['open', 'verifying', 'resolved', 'closed'])
    expect(f, 'Age Select: age order', [(o['label'], o['count']) for o in widget_data('Select', 'incidents-age', out)['options']],
           [('Last 24 hours', 2), ('1–7 days', 2), ('7–30 days', 2), ('Over 30 days', 2)])
    expect(f, 'Alert Select: largest first, display names', [(o['label'], o['value'], o['count']) for o in widget_data('Select', 'incidents-alert', out)['options']],
           [('Probe failing', 'probe', 4), ('gone', 'gone', 2), ('Pod killed for memory', 'oom', 2)])
    expect(f, 'every facet in the bar, then search', bar(out), ['incidents-alert', 'incidents-status', 'incidents-age', 'incidents-owner', 'incidents-search'])
    search = widget_data('Input', 'incidents-search', out)
    expect(f, 'search box writes ?q=', (search['queryParam'], search['name']), ('q', 'q'))
    return f


def check_hidden_facets():
    f = []
    one = [incident(f'o{i}', 'Open', ago(h), alert='probe', cause='x') for i, h in enumerate((1, 50, 300, 900))]
    out = overview(incidents={'items': one})
    expect(f, 'one alert, one state, one owner: those facets hidden, age shown',
           [(x['key'], x['distinct'], x['show']) for x in out['facets']],
           [('alert', 1, False), ('status', 1, False), ('age', 4, True), ('owner', 1, False)])
    expect(f, 'the bar: age and search only', bar(out), ['incidents-age', 'incidents-search'])
    out = overview({'status': 'open'}, incidents={'items': one})
    expect(f, 'a hidden facet shows while one of its values is applied', bar(out), ['incidents-status', 'incidents-age', 'incidents-search'])
    out = overview(alerts=['alerts.observability.krateo.io is forbidden'])
    expect(f, 'alerts unread: nobody owns anything, the Owner facet hides', (facet(out, 'owner')['distinct'], facet(out, 'owner')['show']), (0, False))
    expect(f, 'alerts unread: not in the bar', 'incidents-owner' in bar(out), False)
    calm = widget_data('Paragraph', 'incidents-calm', out)
    expect(f, 'alerts unread: the calm line says so, as a warning', (calm['text'], calm['type']),
           ('The alerts could not be read, so an alert firing without an incident would not show here.', 'warning'))
    return f


def check_status_back_compat():
    f = []
    for label, param, want in (('?status=open', 'open', ['m1', 'g1', 'p1', 'p2', 'p3']),
                               ('?status=Open (any case)', 'Open', ['m1', 'g1', 'p1', 'p2', 'p3']),
                               ('?status=open,verifying (several now)', 'open,verifying', ['m1', 'g1', 'p1', 'p2', 'p3', 'v1']),
                               ('?status=all (the old All chip)', 'all', [r['metadata']['name'] for r in OV_INCIDENTS])):
        out = overview({'status': param, 'projects': 'team-a'})
        expect(f, f'{label}: the rows', sorted(r['name'] for r in out['rows']), sorted(want))
    out = overview({'status': 'all', 'projects': 'team-a'})
    expect(f, '?status=all: no chip, no filter', (out['chips'], out['filtered']), ([], False))
    expect(f, '?status=all: dropped from every href', out['tableHref'], '/incidents?projects=team-a&view=table')
    out = overview({'status': 'Resolved', 'projects': 'team-a'})
    expect(f, '?status=Resolved: one chip, in lower case, removing it', [(c['label'], c['href']) for c in out['chips']],
           [('State: Resolved', '/incidents?projects=team-a'), ('Clear all', '/incidents?projects=team-a')])
    expect(f, '?status=Resolved: the hrefs carry it lower case', out['tableHref'], '/incidents?projects=team-a&status=resolved&view=table')
    return f


def check_strip():
    f = []
    out = overview()
    strip = widget_data('Listy', 'incidents-strip', out)
    rows = [(r['title'], r['value'], r['caption'], r['tone']) for r in strip['dataSource']]
    expect(f, 'the strip, as drawn, over every incident', rows, [
        ('Open', '5', 'from 3 alerts', 'bad'),
        ('Opened in the last 7 days', '3', '', ''),
        ('Open longer than 7 days', '2', 'oldest ' + day(480), 'warn'),
        ('Analyzing', '0', '', ''),
        ('Verifying', '1', '', '')])
    expect(f, 'a statistic strip, red and orange only through the map', (strip['itemTemplate']['rowVariant'], strip['itemTemplate']['color']['map']),
           ('statistic', {'bad': 'red', 'warn': 'orange'}))
    out = overview({'alert': 'oom'})
    rows = [(r['title'], r['value'], r['tone']) for r in widget_data('Listy', 'incidents-strip', out)['dataSource']]
    expect(f, '?alert=oom: the strip counts the filtered set', rows[:3], [('Open', '1', 'bad'), ('Opened in the last 7 days', '1', ''), ('Open longer than 7 days', '0', '')])
    out = overview(incidents=['incidents.observability.krateo.io is forbidden: User "erin" cannot list'])
    rows = [(r['value'], r['caption'], r['tone']) for r in widget_data('Listy', 'incidents-strip', out)['dataSource']]
    expect(f, 'failed read: every tile is "—", never 0', rows, [('', 'could not be read', 'warn')] * 5)
    calm = widget_data('Paragraph', 'incidents-calm', out)
    expect(f, 'failed read: the calm line says it, as a warning', (calm['text'].startswith('The incidents could not be read ('), calm['type']), (True, 'warning'))
    return f


def cards_of(panel):
    return [(c['title'], c['tag'], c['href'], c['cta']) for c in panel['dataSource']]


def check_groups():
    f = []
    out = overview({'projects': 'team-a'})
    w = widget_data('Collapse', 'incidents-groups', out)
    expect(f, 'panels: Open (red) then Verifying; Analyzing has none', [(p['key'], p['count'], p.get('status')) for p in w['items']],
           [('open', 5, 'error'), ('verifying', 1, None)])
    expect(f, 'every active panel starts open', w['defaultActiveKey'], ['open', 'verifying'])
    expect(f, 'by alert: largest first, then by label', cards_of(w['items'][0]), [
        ('Probe failing', '3 open', '/incidents?projects=team-a&status=open&alert=probe&view=table', 'See all 3'),
        ('Pod killed for memory', '1 open', '/incidents/krateo-system/m1', ''),
        ('gone', '1 open', '/incidents/krateo-system/g1', '')])
    probe = w['items'][0]['dataSource'][0]
    expect(f, 'a card: the alert name, the newest root cause, first and newest, the alert now', (probe['meta'], probe['body'], probe['foot'], probe['tone']),
           ('probe', 'Latest root cause: readiness probe on 8081 without a delay', f'since {day(240)} · newest {day(2)} · alert now OK', 'bad'))
    expect(f, 'Triage opens the newest of the group', (probe['namespace'], probe['newestName']), ('krateo-system', 'p1'))
    tmpl = w['itemTemplate']
    act = w['actions']['navigate'][0]
    expect(f, 'the Triage button: a row action navigating to that row\'s newest incident',
           ([(a['actionId'], a['label']) for a in tmpl['rowActions']], act['path']), ([('triage', 'Triage')], '/incidents/${namespace}/${newestName}'))
    expect(f, 'the panel header', w['items'][0]['extra'], '3 alerts, largest first')
    expect(f, 'five cards: no footer', 'footer' in w['items'][0], False)
    # ?group=age
    out = overview({'group': 'age'})
    w = widget_data('Collapse', 'incidents-groups', out)
    expect(f, 'by age: one card per bucket, newest first', cards_of(w['items'][0]), [
        ('Last 24 hours', '1 open', '/incidents/krateo-system/p1', ''),
        ('1–7 days', '2 open', '/incidents?status=open&age=7d&group=age&view=table', 'See all 2'),
        ('7–30 days', '2 open', '/incidents?status=open&age=30d&group=age&view=table', 'See all 2')])
    expect(f, 'by age: the header says so', w['items'][0]['extra'], 'by age, newest first')
    toggle = widget_data('Listy', 'incidents-group-toggle', out)
    expect(f, 'the group toggle: By age active, each keeping the rest', [(r['label'], r['active'], r['href']) for r in toggle['dataSource']],
           [('By alert', False, '/incidents'), ('By age', True, '/incidents?group=age')])
    toggle = widget_data('Listy', 'incidents-group-toggle', overview({'status': 'open'}))
    expect(f, 'the group toggle keeps the filters', [(r['active'], r['href']) for r in toggle['dataSource']],
           [(True, '/incidents?status=open'), (False, '/incidents?status=open&group=age')])
    expect(f, 'an unknown ?group= is by alert', overview({'group': 'nope'})['group'], 'alert')
    return f


def check_caps():
    f = []
    many = [incident(f'a{i}-{j}', 'Open', ago(10 + i * 24 + j), alert=f'a{i}', cause=f'cause {i}') for i in range(8) for j in range(8 - i)]
    out = overview({'projects': 'team-a', 'q': 'cause'}, incidents={'items': many + [incident(f'r{i}', 'Resolved', ago(2000 + i), alert='a0', cause='r') for i in range(7)]})
    w = widget_data('Collapse', 'incidents-groups', out)
    panel = w['items'][0]
    expect(f, 'eight alerts: six cards, largest first', [c['title'] for c in panel['dataSource']], [f'a{i}' for i in range(6)])
    expect(f, 'past six: the footer opens the state\'s table, every filter kept', (panel['footer'], panel['footerNavigateTo']),
           ('See all 36 open incidents →', '/incidents?projects=team-a&status=open&q=cause&view=table'))
    expect(f, 'See all N on a card: the table filtered to that alert and state, every filter kept',
           panel['dataSource'][0]['href'], '/incidents?projects=team-a&status=open&alert=a0&q=cause&view=table')
    out = overview({'projects': 'team-a'}, incidents={'items': many + [incident(f'r{i}', 'Resolved', ago(2000 + i), alert='a0', cause='r') for i in range(7)]})
    done = widget_data('Collapse', 'incidents-done', out)
    expect(f, 'Resolved folded: its count, six newest, See all N to its table', (
        [(p['key'], p['count']) for p in done['items']], done['defaultActiveKey'], [c['href'] for c in done['items'][0]['dataSource']],
        done['items'][0]['footer'], done['items'][0]['footerNavigateTo']),
        ([('resolved', 7)], [], [f'/incidents/krateo-system/r{i}' for i in range(6)], 'See all 7 →', '/incidents?projects=team-a&status=resolved&view=table'))
    expect(f, 'a settled card carries no Triage', 'rowActions' in done['itemTemplate'], False)
    out = overview()
    done = widget_data('Collapse', 'incidents-done', out)
    expect(f, 'Resolved and Closed, one each: no footer', [(p['label'], p['count'], 'footer' in p) for p in done['items']],
           [('Resolved', 1, False), ('Closed', 1, False)])
    return f


def check_table_view():
    f = []
    ex = {'projects': 'team-a', 'status': 'open', 'alert': 'probe', 'view': 'table'}
    out = overview(ex)
    expect(f, '?view=table: the table in place of the overview, filters kept', page_items(out, ex),
           ['incidents-page-header', 'incidents-filter-bar', 'incidents-active-filters', 'incidents-view-toggle', 'incidents-table'])
    table = widget_data('Table', 'incidents-table', out, ex)
    names = [{c['valueKey']: c.get('stringValue') for c in r}['name'] for r in table['dataSource']]
    expect(f, '?view=table: the table holds what the filters match, newest first', names, ['p1', 'p2', 'p3'])
    btn = widget_data('Button', 'incidents-view-toggle', out, ex)
    expect(f, '?view=table: back to the overview, every filter kept', (btn['label'], btn['actions']['navigate'][0]['path']),
           ('← Back to the overview', '/incidents?projects=team-a&status=open&alert=probe'))
    ex = dict(ex, view='')
    out = overview(ex)
    btn = widget_data('Button', 'incidents-view-toggle', out, ex)
    expect(f, 'overview: the table link counts the match and keeps every filter', (btn['label'], btn['actions']['navigate'][0]['path']),
           ('All 3 matching incidents as a table →', '/incidents?projects=team-a&status=open&alert=probe&view=table'))
    btn = widget_data('Button', 'incidents-view-toggle', overview())
    expect(f, 'overview, no filter: as drawn', btn['label'], 'All 8 incidents as a table →')
    chips = overview({'projects': 'team-a', 'status': 'open', 'alert': 'probe,oom'})['chips']
    expect(f, 'chips: one per value, removing only itself, then Clear all keeping the scope', [(c['label'], c['href'], c.get('removable', False)) for c in chips], [
        ('Alert: Probe failing', '/incidents?projects=team-a&status=open&alert=oom', True),
        ('Alert: Pod killed for memory', '/incidents?projects=team-a&status=open&alert=probe', True),
        ('State: Open', '/incidents?projects=team-a&alert=probe%2Coom', True),
        ('Clear all', '/incidents?projects=team-a', False)])
    out = overview({'alert': 'nope'})
    expect(f, 'nothing matches: no groups, the calm line says so', (page_items(out)[2:], widget_data('Paragraph', 'incidents-calm', out)['text']),
           (['incidents-active-filters', 'incidents-strip', 'incidents-calm'],
            'No incident matches these filters. No alert is firing without an incident (0 of 2 alerts in state ALERT).'))
    return f


def check_ask_keeps_the_view():
    f = []
    ex = {'projects': 'team-a', 'status': 'open', 'alert': 'probe', 'q': 'probe', 'group': 'age', 'view': 'table'}
    out = overview(ex)
    href, prompt = ask(out['askHref'])
    expect(f, 'every filter param kept, in the page order', href, '/incidents?projects=team-a&status=open&alert=probe&q=probe&group=age&view=table')
    expect(f, 'the prompt names the view and is about what it matches', prompt,
           'Triage the incidents in the view on screen (Alert: Probe failing; State: Open; search "probe"): 3 of 8 incidents (3 open). '
           'The active ones are: 1. "Probe failing" — readiness probe on 8081 without a delay (triggered ' + day(2) + ') '
           '2. "Probe failing" — probe timeout after the upgrade (triggered ' + day(30) + ') '
           '3. "Probe failing" — probe flapping (triggered ' + day(240) + ').' + TAIL)
    _, prompt = ask(overview({'owner': 'deleted', 'status': 'closed'})['askHref'])
    expect(f, 'a settled view asks for a review', prompt,
           'Review the incidents in the view on screen (State: Closed; Owner: Alert deleted): 1 of 8 incidents (1 closed). '
           'It is: 1. "gone" — closed by hand (triggered ' + day(1200) + '). Tell me whether any looks fixed without its cause '
           'being fixed, and whether any is likely to come back — grounded in the evidence on this page.')
    btn = widget_data('Button', 'incidents-ask-agentiko', out, ex)
    expect(f, 'the button carries it', btn['actions']['navigate'][0]['path'], out['askHref'])
    return f


def check_filter_keys():
    f = []
    for d in CH.docs:
        if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/') and d['metadata']['name'].startswith(('incidents-', 'page-incidents')):
            expect(f, f'{d["kind"]} {d["metadata"]["name"]} declares every filter key', d['spec'].get('keyExtras'), KX)
    root = CH.get('Flex', 'page-incidents')
    expect(f, 'the page root names them', root['metadata']['annotations'].get('krateo.io/filter-keys'), ','.join(KX))
    return f

CHECKS = [check_unfiltered, check_filtered, check_empty_and_failed, check_button_and_header, check_awaiting_section,
          check_facets, check_hidden_facets, check_status_back_compat, check_strip, check_groups, check_caps,
          check_table_view, check_ask_keeps_the_view, check_filter_keys]


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
