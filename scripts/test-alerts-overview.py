#!/usr/bin/env python3
"""
test-alerts-overview — /alerts as a dashboard (the incidents dashboard's layout, mockup A4), over the
chart's own jq.

WHAT IT GUARDS:
  - obs-alerts reads the Incidents too, through a stage filter that keeps only each incident's
    alert label, state and trigger time (an Incident is ~26 KB), for each alert's open incidents and
    the incidents opened in the range;
  - the five figures over every alert: Firing (of N, red above 0), Not evaluated (orange above 0),
    Pending, OK, With open incidents (and how many incidents); with the incidents unread that figure
    is "—", never 0; with the alerts unread every figure is "—";
  - each figure opens the list filtered to what it counts, the route of its chip, every other param
    kept, zero or not; With open incidents is ?status=open-incidents (a chip too): the alerts in any
    state with an open incident, said when the incidents are unread; a failed read is not clickable;
  - ?range= (7d when absent): incidents opened per hour (24h) or per day (7d, 30d), ending now, and
    the five alerts that opened the most in the range, most first, each linking to its alert;
  - the donuts: by state (triage order) and by signal;
  - the layout: header (no subtitle; Ask Agentiko and New alert), range, figures, then the alerts
    card (state chips, cards or ?view=table the table, or a line when empty) on two thirds and the
    charts on one third, "Most incidents" only while there are any;
  - a card: the state as its tag ("Firing", gray while paused), the signal, "Firing since <time>" from
    status.stateSince (alert-provider 0.2.83), in UTC whatever its offset, "since: unknown" when it is
    absent or empty, never stateObservedAt and never now; a red triangle with the count of open
    incidents;
  - Ask Agentiko keeps every param and also offers to author a new alert; every widget declares the
    page's params.

Usage: test-alerts-overview.py [--crds DIR]. JQ=gojq runs the same on snowplow's engine.
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

NOW = datetime.datetime.now(datetime.timezone.utc)
KX = ['projects', 'status', 'range', 'view', 'q']


def ago(hours):
    return (NOW - datetime.timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.123456+00:00')


ABSENT = object()


def alert(name, state='OK', typ='Logs', ok_hours=None, synced=True, opens=None, since=ABSENT, observed=None):
    st = {'state': state, 'type': typ}
    if since is not ABSENT:
        st['stateSince'] = since
    if observed:
        st['stateObservedAt'] = observed
    if synced:
        st['hyperdxAlertId'] = 'hdx-' + name
    if ok_hours is not None:
        st['okSince'] = ago(ok_hours)
    if opens:
        st['conditions'] = [{'type': 'OpensIncidents', 'status': opens[0], 'reason': opens[1]}]
    return {'metadata': {'name': name}, 'spec': {'displayName': name.replace('-', ' ').title(), 'description': 'watches ' + name,
                                                 'threshold': 1, 'thresholdType': 'above', 'interval': '5m'}, 'status': st}


def incident(name, alert_name, state, hours=1):
    return {'metadata': {'name': name, 'labels': {'observability.krateo.io/alert': alert_name}},
            'spec': {'alertRef': {'name': alert_name}, 'prompt': 'x' * 2000, 'triggeredAt': ago(hours)},
            'status': {'state': state, 'rootCause': {'statement': 'y' * 500}}}


# The firing alert as one already ALERT before alert-provider 0.2.83: no stateSince yet, but a
# stateObservedAt (the last observation), which must never stand in for it.
OBSERVED = (NOW - datetime.timedelta(minutes=3)).strftime('%Y-%m-%dT%H:%M:%S.123456+00:00')
ALERTS = [alert('reconcile-errors', 'ALERT', observed=OBSERVED), alert('orphan', 'OK', synced=False), alert('fresh', 'PENDING'),
          alert('cert-expiry', ok_hours=2), alert('job-failed', ok_hours=12), alert('old-recovery', ok_hours=40),
          alert('probe', ok_hours=900), alert('oom', ok_hours=900, typ='Metric', opens=('False', 'AggregatedValue')),
          alert('quiet-logs', ok_hours=900), alert('quiet-trace', ok_hours=900, typ='Traces')]
# Opened 1 h ago unless said: probe 5 (one 3 days ago), the rest one each; oom's second 20 days ago.
INCIDENTS = ([incident(f'p{i}', 'probe', 'Open', 72 if i == 4 else 1) for i in range(5)] + [incident('r1', 'reconcile-errors', 'Open'),
             incident('c1', 'cert-expiry', 'Open'), incident('o1', 'oom', 'Open'), incident('o2', 'oom', 'Resolved', 480),
             incident('q1', 'quiet-logs', 'Closed', 30)])


def resolve(extras, alerts=None, incidents=None):
    r = {'alerts': {'items': ALERTS if alerts is None else alerts}}
    if incidents != 'ERROR':
        r['incidents'] = {'items': INCIDENTS if incidents is None else incidents}
    else:
        r['incidents'] = 'ERROR'
    return tbi.resolve(CH, 'obs-alerts', r, extras)


def w(kind, name, out, extras=None):
    return tbi.resolved_widget(CH, kind, name, out, extras or {}, f'{kind} {name}')['spec']['widgetData']


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def items(kind, name, out, extras=None):
    return [i['resourceRefId'] for i in w(kind, name, out, extras)['items']]


def check_incidents_read():
    f = []
    step = [s for s in CH.get('RESTAction', 'obs-alerts')['spec']['api'] if s['name'] == 'incidents'][0]
    kept = tbi.jq(step['filter'], {'incidents': {'items': INCIDENTS[:1]}})
    expect(f, 'the incidents stage keeps the alert, the state and the trigger time only', kept,
           [{'alert': 'probe', 'state': 'Open', 'at': INCIDENTS[0]['spec']['triggeredAt']}])
    expect(f, 'read as the caller (no userAccessFilter), failures carried', ('userAccessFilter' in step, step.get('continueOnError'), step.get('errorKey')),
           (False, True, 'incidentsError'))
    out = resolve({'status': 'all'})
    open_by = {r['routeName']: r['openIncidents'] for r in out['rows']}
    expect(f, 'open incidents per alert: Resolved and Closed do not count', {k: v for k, v in open_by.items() if v},
           {'probe': 5, 'reconcile-errors': 1, 'cert-expiry': 1, 'oom': 1})
    return f


def check_figures():
    f = []
    out = resolve({})
    expect(f, 'the five figures', [(s['title'], s['value'], s['caption'], s['tone']) for s in out['stats']],
           [('Firing', '1', 'of 10 alerts', 'alert'), ('Not evaluated', '1', 'no HyperDX alert behind it', 'not-evaluated'),
            ('Pending', '1', 'not decided yet', 'pending'), ('OK', '7', '', 'ok'), ('With open incidents', '4', '8 open incidents', 'incident')])
    smap = w('Listy', 'alerts-stats', out)['itemTemplate']['color']['map']
    cmap = w('Listy', 'alerts-cards', out)['itemTemplate']['color']['map']
    expect(f, 'each state figure wears its card colour, zero or not', {k: smap[k] for k in ('alert', 'not-evaluated', 'pending', 'ok')},
           {k: cmap[k] for k in ('alert', 'not-evaluated', 'pending', 'ok')})
    expect(f, 'open incidents red, as the cards\' mark; a failed read orange', (smap['incident'], smap['unread']), ('red', 'orange'))
    out = resolve({}, incidents='ERROR')
    expect(f, 'incidents unread: that figure is a dash, said', out['stats'][4],
           {'title': 'With open incidents', 'value': '', 'caption': 'the incidents could not be read', 'tone': 'unread', 'route': ''})
    expect(f, 'incidents unread: no trend, no top list', (out['incidentTrend'], out['topIncidents']), ([], []))
    ra = CH.get('RESTAction', 'obs-alerts')
    failed = tbi.jq(ra['spec']['filter'], {'alertsError': ['dial tcp: i/o timeout'], 'incidents': []})
    expect(f, 'alerts unread: every figure a dash', {(s['value'], s['caption']) for s in failed['stats']}, {('', 'could not be read')})
    para = w('Paragraph', 'alerts-empty', failed)
    expect(f, 'alerts unread: said, as a warning', (para['text'], para['type']),
           ('The alerts could not be read (dial tcp: i/o timeout), so nothing on this page counts them.', 'warning'))
    return f


def check_charts():
    f = []
    for rng, unit, n, opened in (('24h', 'hour', 24, 7), ('7d', 'day', 7, 9), ('30d', 'day', 30, 10)):
        out = resolve({'range': rng})
        pts = out['incidentTrend']
        expect(f, f'{rng}: one point per {unit}, ending now', (len(pts), out['trendUnit'],
               pts[-1]['t'] <= NOW.timestamp() < pts[-1]['t'] + (3600 if unit == 'hour' else 86400)), (n, unit, True))
        expect(f, f'{rng}: the incidents opened in it', sum(p['v'] for p in pts), opened)
        expect(f, f'{rng}: the chart labels {unit}s', w('LineChart', 'alerts-incident-trend', out, {'range': rng})['xTimeUnit'], unit)
    top = resolve({})['topIncidents']
    expect(f, '7d: most incidents, most first, linked, bars against the first', [(t['name'], t['count'], t['percent'], t['href']) for t in top],
           [('Probe', '5', 100, '/alerts/krateo-system/probe'), ('Cert Expiry', '1', 20, '/alerts/krateo-system/cert-expiry'),
            ('Oom', '1', 20, '/alerts/krateo-system/oom'), ('Quiet Logs', '1', 20, '/alerts/krateo-system/quiet-logs'),
            ('Reconcile Errors', '1', 20, '/alerts/krateo-system/reconcile-errors')])
    expect(f, '24h: what is older drops out', [t['name'] for t in resolve({'range': '24h'})['topIncidents']],
           ['Probe', 'Cert Expiry', 'Oom', 'Reconcile Errors'])
    out = resolve({})
    expect(f, 'by state, triage order', w('PieChart', 'alerts-by-state', out)['data'],
           [{'state': 'Firing', 'count': 1}, {'state': 'Not evaluated', 'count': 1}, {'state': 'Pending', 'count': 1}, {'state': 'OK', 'count': 7}])
    expect(f, 'by signal, most first', w('PieChart', 'alerts-by-signal', out)['data'],
           [{'signal': 'Logs', 'count': 8}, {'signal': 'Metric', 'count': 1}, {'signal': 'Traces', 'count': 1}])
    expect(f, 'the range on the chart cards', w('Card', 'alerts-incident-trend-card', out)['extra'], 'in the last 7 days')
    return f


def check_search():
    f = []
    out = resolve({'status': 'all', 'q': ' PROBE '})
    expect(f, 'by name, case-insensitive, trimmed', (out['q'], [r['routeName'] for r in out['rows']], out['counts']['all']), ('PROBE', ['probe'], 1))
    expect(f, 'by signal type and description too',
           ([r['routeName'] for r in resolve({'status': 'all', 'q': 'traces'})['rows']], [r['routeName'] for r in resolve({'status': 'all', 'q': 'watches oom'})['rows']]),
           (['quiet-trace'], ['oom']))
    expect(f, 'the figures and the donut follow it', ([s['value'] for s in out['stats']], w('PieChart', 'alerts-by-state', out, {'status': 'all', 'q': 'PROBE'})['data']),
           (['0', '0', '0', '1', '1'], [{'state': 'OK', 'count': 1}]))
    expect(f, 'the incident charts are not searched', (out['incidentTrend'], out['topIncidents']),
           (resolve({'status': 'all'})['incidentTrend'], resolve({'status': 'all'})['topIncidents']))
    expect(f, 'every route keeps it', ([c['route'] for c in out['chips']][:2], out['viewChips'][1]['route'], out['rangeChips'][0]['route']),
           (['/alerts?q=PROBE', '/alerts?status=not-evaluated&q=PROBE'], '/alerts?status=all&view=table&q=PROBE', '/alerts?status=all&range=24h&q=PROBE'))
    ex = {'status': 'ok', 'q': 'cert'}
    path = w('Button', 'alerts-ask-agentiko', resolve(ex), ex)['actions']['navigate'][0]['path']
    params = parse_qs(path.partition('?')[2])
    starts = 'Review my observability alerts in the view on screen (State: OK, Search: "cert"): 1 alert —'
    expect(f, 'Ask keeps it and the prompt names it', (params['q'], params['ask'][0][:len(starts)]), (['cert'], starts))
    none = resolve({'q': 'zzz'})
    expect(f, 'nothing matches: said', w('Paragraph', 'alerts-empty', none, {'q': 'zzz'})['text'], 'No alert matches "zzz".')
    return f


def check_layout():
    f = []
    out = resolve({})
    expect(f, 'the page', items('Flex', 'page-alerts', out), ['alerts-page-header', 'alerts-toolbar', 'alerts-stats', 'alerts-main'])
    bar = w('Row', 'alerts-toolbar', out)
    expect(f, 'the toolbar: the range left, the search in a quarter (as on /marketplace), stacked when narrow',
           [(i['resourceRefId'], i['size'], i['xs']) for i in bar['items']], [('alerts-range-group', 18, 24), ('alerts-search-wrap', 6, 24)])
    expect(f, 'the search wrap holds the search', items('Flex', 'alerts-search-wrap', out), ['alerts-search'])
    search = w('Input', 'alerts-search', out)
    expect(f, 'the search writes ?q= on Enter, at the standard size', (search['queryParam'], search['allowClear'], search['size']), ('q', True, 'middle'))
    row = w('Row', 'alerts-main', out)
    expect(f, 'two thirds and one third', [(i['resourceRefId'], i['size'], i['xs']) for i in row['items']],
           [('alerts-list-card', 16, 24), ('alerts-side', 8, 24)])
    card = w('Card', 'alerts-list-card', out)
    expect(f, 'the alerts card: chips then cards; Summary | Full list top right', ([i['resourceRefId'] for i in card['items']], card['extraRefId']),
           (['alerts-status-chips', 'alerts-cards'], 'alerts-view-chips'))
    expect(f, 'the charts, "Most incidents" while there are any', items('Flex', 'alerts-side', out),
           ['alerts-by-state-card', 'alerts-by-signal-card', 'alerts-incident-trend-card', 'alerts-top-incidents-card'])
    expect(f, 'no incident in the range: no "Most incidents"', items('Flex', 'alerts-side', resolve({}, incidents=[])),
           ['alerts-by-state-card', 'alerts-by-signal-card', 'alerts-incident-trend-card'])
    quiet = resolve({}, [a for a in ALERTS if a['status']['state'] == 'OK' and a['status'].get('hyperdxAlertId')], incidents=[])
    expect(f, 'nothing firing: the line in place of the cards', (items('Card', 'alerts-list-card', quiet), w('Paragraph', 'alerts-empty', quiet)['text']),
           (['alerts-status-chips', 'alerts-empty'], 'Nothing is firing: all 7 alerts are quiet.'))
    ex = {'status': 'ok', 'view': 'table'}
    t = resolve(ex)
    expect(f, '?view=table: the table, in the picked state', (items('Card', 'alerts-list-card', t, ex),
           [{c['valueKey']: c['stringValue'] for c in r}['name'] for r in w('Table', 'obs-alerts', t, ex)['dataSource']][:2]),
           (['alerts-status-chips', 'obs-alerts'], ['Cert Expiry', 'Job Failed']))
    for kind, name in (('Flex', 'alerts-range-group'), ('Paragraph', 'alerts-range-label'), ('Listy', 'alerts-range-chips'),
                       ('Listy', 'alerts-stats'), ('Listy', 'alerts-view-chips'), ('Card', 'alerts-by-state-card'),
                       ('Card', 'alerts-by-signal-card'), ('Card', 'alerts-top-incidents-card'), ('Listy', 'alerts-top-incidents')):
        w(kind, name, out)
    return f


def card_foot(alerts):
    return w('Listy', 'alerts-cards', resolve({}, alerts, incidents=[]))['dataSource'][0]['foot']


def check_cards():
    f = []
    out = resolve({'status': 'all'})
    cards = w('Listy', 'alerts-cards', out, {'status': 'all'})
    tpl = cards['itemTemplate']
    expect(f, 'a card per alert, opening it', (tpl['rowVariant'], tpl['navigateTo'], tpl['secondaryText']),
           ('card', '/alerts/{routeNs}/{routeName}', '{stateLabel}'))
    by = {c['routeName']: c for c in cards['dataSource']}
    expect(f, 'triage order', [c['routeName'] for c in cards['dataSource']][:3], ['reconcile-errors', 'orphan', 'fresh'])
    expect(f, 'labels and tones', [(by[n]['stateLabel'], by[n]['tone']) for n in ('reconcile-errors', 'orphan', 'fresh', 'probe')],
           [('Firing', 'alert'), ('Not evaluated', 'not-evaluated'), ('Pending', 'pending'), ('OK', 'ok')])
    expect(f, 'a red triangle with the count of open incidents', [(by[n]['incidentIcon'], by[n]['incidentTip']) for n in ('probe', 'oom', 'quiet-logs')],
           [('fa-triangle-exclamation', '5 open incidents'), ('fa-triangle-exclamation', '1 open incident'), ('', '')])
    paused = alert('p', 'ALERT')
    paused['metadata']['annotations'] = {'krateo.io/paused': 'true'}
    c = w('Listy', 'alerts-cards', resolve({}, [paused], incidents=[]))['dataSource'][0]
    expect(f, 'paused: said, in gray', (c['stateLabel'], c['tone']), ('Firing · paused', 'paused'))
    obs = '2026-11-14T08:22:39.886675+00:00'
    for label, since, want in (('fractional, +00:00', '2026-11-13T21:05:07.123456+00:00', 'Firing since 2026-11-13 21:05 UTC'),
                               ('Z', '2026-11-13T21:05:07Z', 'Firing since 2026-11-13 21:05 UTC'),
                               ('another offset, shown in UTC', '2026-11-13T23:05:07+02:00', 'Firing since 2026-11-13 21:05 UTC'),
                               ('empty', '', 'since: unknown'),
                               ('absent', ABSENT, 'since: unknown')):
        foot = card_foot([alert('a', 'ALERT', since=since, observed=obs)])
        expect(f, f'stateSince {label}', foot, want + ' · every 5m')
        expect(f, f'stateSince {label}: never the last observation', '08:22' in foot, False)
    row = resolve({}, [alert('a', 'ALERT', since='2026-11-13T21:05:07Z')], incidents=[])['rows'][0]
    expect(f, 'the RA keeps the raw value and its UTC form', (row['stateSince'], row['stateSinceUtc']), ('2026-11-13T21:05:07Z', '2026-11-13 21:05 UTC'))
    return f


def check_header_and_asks():
    f = []
    hdr = w('PageHeader', 'alerts-page-header', {})
    expect(f, 'header: no subtitle; Ask and New (one Agentiko button, Diego 2026-11-14)', ('subtitle' in hdr, [i['resourceRefId'] for i in hdr['items']]),
           (False, ['alerts-ask-agentiko', 'alerts-new']))
    ex = {'projects': 'team-a', 'status': 'ok', 'range': '30d', 'view': 'table'}
    out = resolve(ex)
    path = w('Button', 'alerts-ask-agentiko', out, ex)['actions']['navigate'][0]['path']
    params = parse_qs(path.partition('?')[2])
    expect(f, 'every param kept, in the page order', [k for k in params], ['projects', 'status', 'range', 'view', 'ask'])
    starts = 'Review my observability alerts in the view on screen (State: OK): 7 alerts — 0 firing,'
    expect(f, 'the prompt names the state', params['ask'][0][:len(starts)], starts)
    author = ' If I want a new alert instead, help me author it: what to watch (the telemetry query), the threshold and comparison, and the evaluation interval, then create it once I confirm.'
    expect(f, 'Ask also offers to author a new alert', params['ask'][0].endswith(author), True)
    path = w('Button', 'alerts-ask-agentiko', resolve({'status': 'all'}, incidents='ERROR'), {'status': 'all'})['actions']['navigate'][0]['path']
    prompt = parse_qs(path.partition('?')[2])['ask'][0]
    expect(f, 'incidents unread: the prompt says so, never "no open incidents"',
           ('the incidents could not be read' in prompt, 'with open incidents (' in prompt), (True, False))
    return f


def check_filter_keys():
    f = []
    expect(f, 'the root names them', CH.get('Flex', 'page-alerts')['metadata']['annotations'].get('krateo.io/filter-keys'), ','.join(KX))
    for d in CH.docs:
        n = d.get('metadata', {}).get('name', '')
        if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/') and (n.startswith('alerts-') or n in ('page-alerts', 'obs-alerts')):
            expect(f, f'{d["kind"]} {n} declares every page param', d['spec'].get('keyExtras'), KX)
    return f


def check_tile_routes():
    f = []
    ex = {'projects': 'team-a', 'status': 'ok', 'range': '30d', 'view': 'table', 'q': 'o'}
    out = resolve(ex)
    chip = {c['label']: c['route'] for c in out['chips']}
    expect(f, 'each figure routes as its chip (q, view, range, projects kept)', [(s['title'], s['route']) for s in out['stats']],
           [(t, chip[t]) for t in ('Firing', 'Not evaluated', 'Pending', 'OK', 'With open incidents')])
    expect(f, 'Firing (the default) leaves ?status out; With open incidents is a status value',
           (out['stats'][0]['route'], out['stats'][4]['route']),
           ('/alerts?projects=team-a&range=30d&view=table&q=o', '/alerts?projects=team-a&status=open-incidents&range=30d&view=table&q=o'))
    zero = resolve({}, [alert('a', 'OK')], incidents=[])
    expect(f, 'a zero figure still routes', [(s['value'], s['route']) for s in zero['stats']],
           [('0', '/alerts'), ('0', '/alerts?status=not-evaluated'), ('0', '/alerts?status=pending'), ('1', '/alerts?status=ok'),
            ('0', '/alerts?status=open-incidents')])
    ex = {'status': 'Open-Incidents'}
    wi = resolve(ex)
    expect(f, 'open-incidents: the alerts with one, any state, in triage order; its chip active',
           ([r['routeName'] for r in wi['rows']], [c['label'] for c in wi['chips'] if c['active']], wi['counts']),
           (['reconcile-errors', 'cert-expiry', 'oom', 'probe'], ['With open incidents'], resolve({})['counts']))
    path = w('Button', 'alerts-ask-agentiko', wi, ex)['actions']['navigate'][0]['path']
    starts = 'Review my observability alerts in the view on screen (With open incidents): 4 alerts —'
    expect(f, 'open-incidents: the prompt names it', parse_qs(path.partition('?')[2])['ask'][0][:len(starts)], starts)
    none = resolve(ex, incidents=[])
    para = w('Paragraph', 'alerts-empty', none, ex)
    expect(f, 'open-incidents, none: said', (none['rows'], para['text'], para['type']), ([], 'No alert has an open incident.', 'secondary'))
    unread = resolve(ex, incidents='ERROR')
    para = w('Paragraph', 'alerts-empty', unread, ex)
    expect(f, 'open-incidents, incidents unread: said as a warning, no chip, no route',
           (unread['rows'], para['text'], para['type'], [c['label'] for c in unread['chips']][-1], unread['stats'][4]['route']),
           ([], 'The incidents could not be read, so which alerts have an open incident is unknown.', 'warning', 'All', ''))
    ra = CH.get('RESTAction', 'obs-alerts')
    failed = tbi.jq(ra['spec']['filter'], {'alertsError': ['dial tcp: i/o timeout'], 'incidents': []})
    expect(f, 'alerts unread: no figure is clickable', [s['route'] for s in failed['stats']], [''] * 5)
    tpl = w('Listy', 'alerts-stats', out, ex)['itemTemplate']
    expect(f, 'the figures navigate to their route', (tpl['rowVariant'], tpl['navigateTo']), ('statistic', '{route}'))
    return f


CHECKS = [check_incidents_read, check_figures, check_tile_routes, check_charts, check_search, check_layout, check_cards, check_header_and_asks, check_filter_keys]


def main():
    global CH
    args = sys.argv[1:]
    crds_dir = args[args.index('--crds') + 1] if '--crds' in args else None
    CH = tbi.Chart(tbi.render(os.path.join(HERE, '..', 'helm', 'alerts-page')))
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
