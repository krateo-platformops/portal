#!/usr/bin/env python3
"""
test-alerts-filter — /alerts' state filter, over the chart's own jq.

WHAT IT GUARDS. obs-alerts filters its rows by the ?status= chip and counts every chip over the full
set. The rules a reader would notice:
  - the State facet (root-pages slice 3a; it replaced the chips) offers the states some alert is in,
    in triage order, plus the one in force even at 0; with every alert OK it is left out of the bar;
  - an alert with no HyperDX alert behind it is "Not evaluated", in the chip and in the table,
    whatever .status.state last said: nothing evaluates it;
  - a state is matched in any case; a ?status= that is no state shows no rows and is a chip that
    removes it, as on /incidents;
  - the applied-filter chips and the "Author with Autopilot" link keep the ?projects= scope and every
    filter, so opening the rail does not reset the view;
  - ?view=table is the header, the filter bar, the back link and the table; no summary band;
  - the alert page's state pill says "Not evaluated" too, in gray;
  - the Incidents column says whether a firing opens an incident, off alert-provider's
    OpensIncidents condition: "Yes", or "No" with the reason in a word, in gray; "—" without one.
Every resolved widget is validated against its CRD with --crds.

Uses the `jq` binary; JQ=gojq runs the same checks on it.
Usage: test-alerts-filter.py [--crds DIR]. Exit code = failed checks.
"""
import importlib.util
import os
import sys
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

CHART = os.path.join(HERE, '..', 'helm', 'alerts-page')


def alert(name, state, synced=True, paused=False, opens=None, typ=None):
    meta = {'name': name}
    if paused:
        meta['annotations'] = {'krateo.io/paused': 'true'}
    st = {} if state is None else {'state': state}
    if synced:
        st['hyperdxAlertId'] = 'hdx-' + name
    if typ:
        st['type'] = typ
    if opens:  # (status, reason): the OpensIncidents condition, after provider-runtime's Ready
        st['conditions'] = [{'type': 'Ready', 'status': 'True', 'reason': 'Available'},
                            {'type': 'OpensIncidents', 'status': opens[0], 'reason': opens[1]}]
    return {'metadata': meta, 'spec': {'displayName': name.title(), 'description': 'watches ' + name,
                                       'threshold': 1, 'thresholdType': 'above', 'interval': '5m'},
            'status': st}


ALERTS = [
    alert('latency', 'ALERT'),
    alert('disk', 'ALERT', paused=True),
    alert('crashloop', 'OK'),
    alert('restarts', 'OK'),
    alert('oom', 'OK'),
    alert('fresh', None),                    # synced, HyperDX has not decided yet: Pending
    alert('rejected', 'OK', synced=False),   # last said OK, but nothing evaluates it now
]


def resolve(extras, items=ALERTS, failed=False):
    ra = CH.get('RESTAction', 'obs-alerts')
    data = dict(extras)
    if failed:
        data[ra['spec']['api'][0]['errorKey']] = ['dial tcp: i/o timeout']
    else:
        data['alerts'] = {'items': items}
    return tbi.jq(ra['spec']['filter'], data)


def states(out, extras):
    """The State facet's options (root-pages slice 3a replaced the status chips with it)."""
    opts = tbi.resolved_widget(CH, 'Select', 'alerts-status', out, extras, 'state facet')['spec']['widgetData']['options']
    return [(o['label'], o['value'], o['count']) for o in opts]


def table(out, extras):
    rows = tbi.resolved_widget(CH, 'Table', 'obs-alerts', out, extras, 'table')['spec']['widgetData']['dataSource']
    return [{c['valueKey']: (c['stringValue'], c.get('color')) for c in r} for r in rows]


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def check_counts_and_rows():
    f = []
    out = resolve({})
    expect(f, 'counts over the full set', out['counts'],
           {'all': 7, 'alert': 2, 'ok': 3, 'pending': 1, 'not-evaluated': 1})
    expect(f, 'unfiltered: every row', len(out['rows']), 7)
    expect(f, 'unfiltered: the chip in force', out['status'], 'all')
    for sel, want in (('alert', ['latency', 'disk']), ('OK', ['crashloop', 'restarts', 'oom']),
                      ('pending', ['fresh']), ('not-evaluated', ['rejected'])):
        got = [r['routeName'] for r in resolve({'status': sel})['rows']]
        expect(f, f'{sel} chip: its rows', got, want)
    expect(f, 'counts do not follow the chip', resolve({'status': 'ok'})['counts'], out['counts'])
    expect(f, 'not a chip: no rows', resolve({'status': 'firing'})['rows'], [])
    expect(f, 'explicit all: every row', len(resolve({'status': 'all'})['rows']), 7)
    out = resolve({}, failed=True)
    expect(f, 'unreadable alerts: zero, no crash', (out['rows'], out['counts']['all']), ([], 0))
    return f


def check_state_facet():
    f = []
    ex = {'projects': 'team a'}
    out = resolve(ex)
    expect(f, 'every state some alert is in, in triage order', states(out, ex),
           [('Alert', 'alert', 2), ('Not evaluated', 'not-evaluated', 1), ('Pending', 'pending', 1), ('OK', 'ok', 3)])
    out = resolve(dict(ex, status='alert'))
    expect(f, 'a picked state is a chip that removes itself and keeps the scope', [(c['label'], c['href']) for c in out['chips']],
           [('State: Alert', '/alerts?projects=team%20a'), ('Clear all', '/alerts?projects=team%20a')])
    quiet = [a for a in ALERTS if a['metadata']['name'] in ('latency', 'crashloop')]
    expect(f, 'a quiet platform: no Pending, no Not evaluated', states(resolve({}, quiet), {}), [('Alert', 'alert', 1), ('OK', 'ok', 1)])
    ex = {'status': 'not-evaluated'}
    expect(f, 'the state in force stays, even at 0, in its place', states(resolve(ex, quiet), ex),
           [('Alert', 'alert', 1), ('Not evaluated', 'not-evaluated', 0), ('OK', 'ok', 1)])
    only_ok = [a for a in ALERTS if a['metadata']['name'] in ('crashloop', 'restarts')]
    expect(f, 'every alert OK: the State facet is left out of the bar',
           [i['resourceRefId'] for i in tbi.resolved_widget(CH, 'Flex', 'alerts-filter-bar', resolve({}, only_ok), {}, 'bar')['spec']['widgetData']['items']],
           ['alerts-search'])
    return f


def check_table():
    f = []
    rows = {r['name'][0]: r['state'] for r in table(resolve({}), {})}
    expect(f, 'firing', rows['Latency'], ('Alert', 'red'))
    expect(f, 'firing, paused', rows['Disk'], ('Alert · paused', 'gray'))
    expect(f, 'quiet', rows['Crashloop'], ('OK', 'green'))
    expect(f, 'synced, undecided', rows['Fresh'], ('Pending', 'orange'))
    expect(f, 'no HyperDX alert', rows['Rejected'], ('Not evaluated', 'gray'))
    ex = {'status': 'alert'}
    expect(f, 'filtered table', [r['name'][0] for r in table(resolve(ex), ex)], ['Latency', 'Disk'])
    return f


def check_incidents_column():
    f = []
    items = [alert('logs', 'OK', opens=('True', 'CountsRecords'), typ='Logs'),
             alert('spans', 'ALERT', opens=('True', 'CountsRecords'), typ='Traces'),
             alert('disk', 'ALERT', opens=('False', 'AggregatedValue'), typ='Metric'),
             alert('cost', 'OK', opens=('False', 'AggregatedValue'), typ='LogValue'),
             alert('latency', 'OK', opens=('False', 'AggregatedValue'), typ='TraceValue'),
             alert('muted', 'ALERT', paused=True, opens=('False', 'Paused'), typ='Logs'),
             alert('tenant', 'OK', opens=('False', 'OutsideHomeNamespace'), typ='Logs'),
             alert('unknown', 'OK', opens=('False', 'SomeNewReason'), typ='Logs'),
             alert('older', 'OK')]  # an alert-provider that writes no condition yet
    rows = {r['name'][0]: r['incidents'] for r in table(resolve({}, items), {})}
    gray = 'var(--gray-color)'
    expect(f, 'incidents column', rows, {
        'Logs': ('Yes', None), 'Spans': ('Yes', None),
        'Disk': ('No · metric', gray), 'Cost': ('No · value', gray), 'Latency': ('No · value', gray),
        'Muted': ('No · paused', gray), 'Tenant': ('No · other namespace', gray),
        'Unknown': ('No', gray), 'Older': ('—', gray)})
    cols = [c['valueKey'] for c in CH.get('Table', 'obs-alerts')['spec']['widgetData']['columns']]
    expect(f, 'Incidents is the last column', cols[-2:], ['state', 'incidents'])
    return f


def check_button_and_page():
    f = []
    for ex, want in (({}, '/alerts'), ({'status': 'OK', 'projects': 'team-a'}, '/alerts?projects=team-a&status=ok'),
                     ({'status': 'firing'}, '/alerts?status=firing')):
        out = resolve(ex)
        path = tbi.resolved_widget(CH, 'Button', 'alerts-author-autopilot', out, ex, 'ask button')['spec']['widgetData']['actions']['navigate'][0]['path']
        base, _, query = path.partition('?')
        params = parse_qs(query, keep_blank_values=True)
        prompt = params.pop('ask', [''])[0]
        kept = '&'.join(f'{k}={v[0]}' for k, v in params.items())
        expect(f, f'ask link keeps the view ({ex})', base + ('?' + kept if kept else ''), want)
        expect(f, f'ask link seeds the authoring flow ({ex})', prompt.startswith('I want to create a new observability alert.'), True)
    expect(f, 'button, no data: the list',
           tbi.widget(CH, 'Button', 'alerts-author-autopilot', 'actions', {}, {})['navigate'][0]['path'], '/alerts')
    want = CH.get('Table', 'obs-alerts')['spec']['keyExtras']
    expect(f, 'one cache key for every reader of the filtered action',
           [CH.get(k, n)['spec'].get('keyExtras') for k, n in (('Select', 'alerts-status'), ('Button', 'alerts-author-autopilot'), ('Button', 'alerts-ask-agentiko'))],
           [want, want, want])
    ex = {'view': 'table'}
    page = tbi.resolved_widget(CH, 'Flex', 'page-alerts', resolve(ex), ex, 'page')['spec']['widgetData']
    expect(f, 'the table view: header, filter bar, back link, table', [i['resourceRefId'] for i in page['items']],
           ['alerts-page-header', 'alerts-filter-bar', 'alerts-view-toggle', 'obs-alerts'])
    expect(f, 'no summary band left', [d['metadata']['name'] for d in CH.docs if d.get('kind') in ('Statistic', 'Row')
                                       or d['metadata'].get('name', '').startswith(('alerts-stat-', 'alerts-stats'))], [])
    return f


def check_detail_pill():
    f = []
    ra = CH.get('RESTAction', 'alert-detail')
    ex = {'name': 'x', 'namespace': 'krateo-system'}
    for a, want in ((alert('x', 'OK'), [{'label': 'OK', 'color': 'green'}]),
                    (alert('x', 'ALERT', paused=True), [{'label': 'Alert', 'color': 'red'}, {'label': 'Paused', 'color': 'gray'}]),
                    (alert('x', 'OK', synced=False), [{'label': 'Not evaluated', 'color': 'gray'}])):
        out = tbi.jq(ra['spec']['filter'], dict(ex, alert=a, incidents={'items': []}))
        got = tbi.resolved_widget(CH, 'PageHeader', 'alert-detail-page-header', out, ex, 'detail header')['spec']['widgetData']['tags']
        expect(f, f'detail pill ({a["status"]})', got, want)
    return f


CHECKS = [check_counts_and_rows, check_state_facet, check_table, check_incidents_column, check_button_and_page, check_detail_pill]


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
