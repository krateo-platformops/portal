#!/usr/bin/env python3
"""
test-alerts-overview — /alerts as a root-pages overview (slice 3a), over the chart's own jq.

WHAT IT GUARDS (build plan §3.4, mockup screen 07, decisions of 2026-11-13 and 2026-11-14):
  - obs-alerts reads the Incidents too, through a stage filter that keeps only each incident's
    alert label and state (an Incident is ~26 KB), for "has open incidents";
  - Signal, State and Has open incidents count under every OTHER filter, over every alert; a facet
    with one value is left out of the bar unless applied; with the incidents unread no alert has a
    "has open incidents" value, so that facet hides, and the strip says "—", never 0;
  - the strip: Firing (of N, red above 0), Back to OK in the last 24h, With open incidents (and how
    many incidents), Don't open incidents;
  - the groups, in order: Firing (red), Not evaluated (orange), Pending, Back to OK in the last 24
    hours (newest first), OK with open incidents (most first), then OK by signal, folded; six cards
    a group, then "See all N →" to the table filtered to it (Q1); a Firing card claims no time it
    started firing (the CR records none);
  - the header: Ask Agentiko (about the alerts on screen), Author with Autopilot (authoring), New
    alert; both Agentiko links keep every filter;
  - the calm line when nothing is firing, not evaluated or pending; ?view=table keeps today's table,
    filtered; every widget declares the page's filter keys.

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
KX = ['projects', 'status', 'signal', 'hasIncidents', 'q', 'view']


def ago(hours):
    return (NOW - datetime.timedelta(hours=hours)).strftime('%Y-%m-%dT%H:%M:%S.123456+00:00')


def alert(name, state='OK', typ='Logs', ok_hours=None, synced=True, opens=None):
    st = {'state': state, 'type': typ}
    if synced:
        st['hyperdxAlertId'] = 'hdx-' + name
    if ok_hours is not None:
        st['okSince'] = ago(ok_hours)
    if opens:
        st['conditions'] = [{'type': 'OpensIncidents', 'status': opens[0], 'reason': opens[1]}]
    return {'metadata': {'name': name}, 'spec': {'displayName': name.replace('-', ' ').title(), 'description': 'watches ' + name,
                                                 'threshold': 1, 'thresholdType': 'above', 'interval': '5m'}, 'status': st}


def incident(name, alert_name, state):
    return {'metadata': {'name': name, 'labels': {'observability.krateo.io/alert': alert_name}},
            'spec': {'alertRef': {'name': alert_name}, 'prompt': 'x' * 2000},
            'status': {'state': state, 'rootCause': {'statement': 'y' * 500}}}


ALERTS = [alert('reconcile-errors', 'ALERT'), alert('orphan', 'OK', synced=False), alert('fresh', 'PENDING'),
          alert('cert-expiry', ok_hours=2), alert('job-failed', ok_hours=12), alert('old-recovery', ok_hours=40),
          alert('probe', ok_hours=900), alert('oom', ok_hours=900, typ='Metric', opens=('False', 'AggregatedValue')),
          alert('quiet-logs', ok_hours=900), alert('quiet-trace', ok_hours=900, typ='Traces')]
INCIDENTS = ([incident(f'p{i}', 'probe', 'Open') for i in range(5)] + [incident('r1', 'reconcile-errors', 'Open'),
             incident('c1', 'cert-expiry', 'Open'), incident('o1', 'oom', 'Open'), incident('o2', 'oom', 'Resolved'),
             incident('q1', 'quiet-logs', 'Closed')])


def resolve(extras, alerts=None, incidents=None):
    r = {'alerts': {'items': ALERTS if alerts is None else alerts}}
    if incidents != 'ERROR':
        r['incidents'] = {'items': INCIDENTS if incidents is None else incidents}
    else:
        r['incidents'] = 'ERROR'
    return tbi.resolve(CH, 'obs-alerts', r, extras)


def w(kind, name, out, extras=None):
    return tbi.resolved_widget(CH, kind, name, out, extras or {}, f'{kind} {name}')['spec']['widgetData']


def facet(out, key):
    return {v['value']: v['count'] for v in [f for f in out['facets'] if f['key'] == key][0]['values']}


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def check_incidents_read():
    f = []
    step = [s for s in CH.get('RESTAction', 'obs-alerts')['spec']['api'] if s['name'] == 'incidents'][0]
    kept = tbi.jq(step['filter'], {'incidents': {'items': INCIDENTS[:1]}})
    expect(f, 'the incidents stage keeps the alert and the state only', kept, [{'alert': 'probe', 'state': 'Open'}])
    expect(f, 'read as the caller (no userAccessFilter), failures carried', ('userAccessFilter' in step, step.get('continueOnError'), step.get('errorKey')),
           (False, True, 'incidentsError'))
    out = resolve({})
    open_by = {r['routeName']: r['openIncidents'] for r in out['rows']}
    expect(f, 'open incidents per alert: Resolved and Closed do not count', {k: v for k, v in open_by.items() if v},
           {'probe': 5, 'reconcile-errors': 1, 'cert-expiry': 1, 'oom': 1})
    return f


def check_facets():
    f = []
    out = resolve({})
    expect(f, 'Signal', facet(out, 'signal'), {'Logs': 8, 'Metric': 1, 'Traces': 1})
    expect(f, 'State, an unsynced alert Not evaluated', facet(out, 'status'), {'alert': 1, 'not-evaluated': 1, 'pending': 1, 'ok': 7})
    expect(f, 'Has open incidents', facet(out, 'hasIncidents'), {'yes': 4, 'no': 6})
    out = resolve({'hasIncidents': 'yes'})
    expect(f, '?hasIncidents=yes: its own counts ignore it', facet(out, 'hasIncidents'), {'yes': 4, 'no': 6})
    expect(f, '?hasIncidents=yes: the state counts apply it', facet(out, 'status'), {'alert': 1, 'not-evaluated': 0, 'pending': 0, 'ok': 3})
    out = resolve({'q': 'PROBE'})
    expect(f, 'search: name, any case', [r['routeName'] for r in out['rows']], ['probe'])
    out = resolve({}, incidents='ERROR')
    hi = [x for x in out['facets'] if x['key'] == 'hasIncidents'][0]
    expect(f, 'incidents unread: no alert has a value, the facet hides', (hi['distinct'], hi['show']), (0, False))
    expect(f, 'incidents unread: not in the bar', [i['resourceRefId'] for i in w('Flex', 'alerts-filter-bar', out)['items']],
           ['alerts-signal', 'alerts-status', 'alerts-search'])
    ok = [a for a in ALERTS if a['status']['state'] == 'OK' and a['status'].get('hyperdxAlertId')]
    expect(f, 'every alert OK: State left out', [i['resourceRefId'] for i in w('Flex', 'alerts-filter-bar', resolve({}, ok))['items']],
           ['alerts-signal', 'alerts-hasIncidents', 'alerts-search'])
    return f


def check_strip():
    f = []
    rows = [(t['title'], t['value'], t['caption'], t['tone']) for t in w('Listy', 'alerts-strip', resolve({}))['dataSource']]
    expect(f, 'the strip, as drawn', rows, [('Firing', '1', 'of 10', 'bad'), ('Back to OK in the last 24h', '2', '', ''),
                                           ('With open incidents', '4', '8 incidents', ''), ("Don't open incidents", '1', 'a metric or a value', '')])
    rows = [(t['title'], t['value'], t['caption'], t['tone']) for t in w('Listy', 'alerts-strip', resolve({}, incidents='ERROR'))['dataSource']]
    expect(f, 'incidents unread: "—" on that tile only', rows[2], ('With open incidents', '', 'incidents could not be read', 'warn'))
    return f


def check_groups():
    f = []
    ex = {'projects': 'team-a'}
    g = w('Collapse', 'alerts-groups', resolve(ex), ex)
    expect(f, 'groups in order', [(p['key'], p['count'], p.get('status')) for p in g['items']],
           [('firing', 1, 'error'), ('notEvaluated', 1, 'warning'), ('pending', 1, None), ('backToOk', 2, None), ('okOpen', 2, None), ('ok', 3, None)])
    expect(f, 'every group open but OK', g['defaultActiveKey'], ['firing', 'notEvaluated', 'pending', 'backToOk', 'okOpen'])
    fire = g['items'][0]['dataSource'][0]
    expect(f, 'a Firing card: its open incidents, its cadence, no firing-since claim', (fire['tag'], fire['body'], fire['foot'], fire['href']),
           ('Alert', '1 open incident.', 'reconcile-errors · Logs · every 5m', '/alerts/krateo-system/reconcile-errors'))
    back = [p for p in g['items'] if p['key'] == 'backToOk'][0]['dataSource']
    expect(f, 'Back to OK in the last 24 hours: newest first; 40 hours ago is not', [c['title'] for c in back], ['Cert Expiry', 'Job Failed'])
    okopen = [p for p in g['items'] if p['key'] == 'okOpen'][0]['dataSource']
    expect(f, 'OK with open incidents: most first; a metric alert says No incidents', [(c['title'], c['body'], c['tag']) for c in okopen],
           [('Probe', '5 open incidents.', ''), ('Oom', '1 open incident.', 'No incidents')])
    ok = [p for p in g['items'] if p['key'] == 'ok'][0]['dataSource']
    expect(f, 'OK by signal: a card per signal, opening its table', [(c['title'], c['href']) for c in ok],
           [('Logs', '/alerts?projects=team-a&status=ok&signal=Logs&view=table'), ('Traces', '/alerts?projects=team-a&status=ok&signal=Traces&view=table')])
    many = ALERTS + [alert(f'storm-{i}', 'ALERT') for i in range(7)]
    fire = w('Collapse', 'alerts-groups', resolve(ex, many), ex)['items'][0]
    expect(f, 'past six: See all N to the Firing table (Q1)', (len(fire['dataSource']), fire['footer'], fire['footerNavigateTo']),
           (6, 'See all 8 →', '/alerts?projects=team-a&status=alert&view=table'))
    return f


def check_header_and_asks():
    f = []
    hdr = w('PageHeader', 'alerts-page-header', {})
    expect(f, 'header: Ask, Author, New', [i['resourceRefId'] for i in hdr['items']], ['alerts-ask-agentiko', 'alerts-author-autopilot', 'alerts-new'])
    ex = {'projects': 'team-a', 'signal': 'Logs', 'hasIncidents': 'yes', 'view': 'table'}
    out = resolve(ex)
    for name, starts in (('alerts-ask-agentiko', 'Review my observability alerts in the view on screen (Signal: Logs; Has open incidents: Yes): 3 alerts — 1 firing,'),
                         ('alerts-author-autopilot', 'I want to create a new observability alert.')):
        path = w('Button', name, out, ex)['actions']['navigate'][0]['path']
        base, _, query = path.partition('?')
        params = parse_qs(query)
        expect(f, f'{name}: every filter kept, in the page order', [k for k in params], ['projects', 'signal', 'hasIncidents', 'view', 'ask'])
        expect(f, f'{name}: its prompt', params['ask'][0][:len(starts)], starts)
    path = w('Button', 'alerts-ask-agentiko', resolve({}, incidents='ERROR'))['actions']['navigate'][0]['path']
    prompt = parse_qs(path.partition('?')[2])['ask'][0]
    expect(f, 'incidents unread: the prompt says so, never "no open incidents"',
           ('the incidents could not be read' in prompt, 'with open incidents (' in prompt), (True, False))
    return f


def check_calm_and_table():
    f = []
    ok = [a for a in ALERTS if a['status']['state'] == 'OK' and a['status'].get('hyperdxAlertId')]
    out = resolve({}, ok)
    expect(f, 'nothing firing: the calm line, as drawn', w('Paragraph', 'alerts-calm', out)['text'], 'Nothing is firing: all 7 alerts are OK.')
    # Firing is the ONLY reason for no calm line here: no alert is Not evaluated or Pending.
    firing_only = ok + [alert('reconcile-errors', 'ALERT')]
    out = resolve({}, firing_only)
    expect(f, 'one alert firing, nothing else wrong: no calm line', 'alerts-calm' in [i['resourceRefId'] for i in w('Flex', 'page-alerts', out)['items']], False)
    expect(f, 'one alert firing: the calm text says nothing is fine', w('Paragraph', 'alerts-calm', out)['text'], '')
    ex = {'status': 'ok', 'signal': 'Traces', 'view': 'table'}
    out = resolve(ex)
    expect(f, '?view=table: the table, filtered', [{c['valueKey']: c['stringValue'] for c in r}['name'] for r in w('Table', 'obs-alerts', out, ex)['dataSource']],
           ['Quiet Trace'])
    btn = w('Button', 'alerts-view-toggle', out, ex)
    expect(f, '?view=table: back keeps every filter', btn['actions']['navigate'][0]['path'], '/alerts?status=ok&signal=Traces')
    return f


def check_filter_keys():
    f = []
    expect(f, 'the root names them', CH.get('Flex', 'page-alerts')['metadata']['annotations'].get('krateo.io/filter-keys'), ','.join(KX))
    for d in CH.docs:
        n = d.get('metadata', {}).get('name', '')
        if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/') and (n.startswith('alerts-') or n in ('page-alerts', 'obs-alerts')):
            expect(f, f'{d["kind"]} {n} declares every filter key', [k for k in KX if k not in (d['spec'].get('keyExtras') or [])], [])
    return f


CHECKS = [check_incidents_read, check_facets, check_strip, check_groups, check_header_and_asks, check_calm_and_table, check_filter_keys]


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
