#!/usr/bin/env python3
"""
test-alerts-filter — /alerts' state filter, over the chart's own jq.

WHAT IT GUARDS. obs-alerts filters its rows by the ?status= chip and counts every chip over the full
set. The rules a reader would notice:
  - All, Alert and OK are always offered; Pending and Not evaluated only while an alert is in them
    (or the chip is the one in force), so a chip that means "something is broken" is never a 0;
  - an alert with no HyperDX alert behind it is "Not evaluated", in the chip and in the table,
    whatever .status.state last said: nothing evaluates it;
  - a chip is matched in any case; a ?status= that is no chip shows no rows, as on /incidents;
  - every chip route, and the header's "Ask Agentiko →" link, keep the ?projects= scope; the link
    also keeps the chip, so opening the rail does not reset the view;
  - the page is the header, the chips and the table: the summary band is gone;
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


def chips(out, extras):
    return tbi.resolved_widget(CH, 'Listy', 'alerts-status-chips', out, extras, 'chips')['spec']['widgetData']['dataSource']


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


def check_chips():
    f = []
    ex = {'projects': 'team a'}
    got = chips(resolve(ex), ex)
    expect(f, 'chips while all five have alerts', [(c['label'], c['count'], c['active']) for c in got],
           [('All', 7, True), ('Alert', 2, False), ('OK', 3, False), ('Pending', 1, False), ('Not evaluated', 1, False)])
    expect(f, 'routes carry the scope', got[1]['route'], '/alerts?status=alert&projects=team%20a')
    quiet = [a for a in ALERTS if a['metadata']['name'] in ('latency', 'crashloop')]
    got = chips(resolve({}, quiet), {})
    expect(f, 'a quiet platform: no Pending, no Not evaluated', [(c['label'], c['count']) for c in got],
           [('All', 2), ('Alert', 1), ('OK', 1)])
    expect(f, 'no scope: nothing carried', got[2]['route'], '/alerts?status=ok')
    ex = {'status': 'not-evaluated'}
    got = chips(resolve(ex, quiet), ex)
    expect(f, 'the chip in force stays, even at 0', [(c['label'], c['count'], c['active']) for c in got][-1],
           ('Not evaluated', 0, True))
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
    for ex, want in (({}, '/alerts'), ({'status': 'OK', 'projects': 'team-a'}, '/alerts?status=ok&projects=team-a'),
                     ({'status': 'firing'}, '/alerts')):
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
           [CH.get(k, n)['spec'].get('keyExtras') for k, n in (('Listy', 'alerts-status-chips'), ('Button', 'alerts-author-autopilot'))],
           [want, want])
    page = CH.get('Flex', 'page-alerts')['spec']
    expect(f, 'the page: header, chips, table', [i['resourceRefId'] for i in page['widgetData']['items']],
           ['alerts-page-header', 'alerts-status-chips', 'obs-alerts'])
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


CHECKS = [check_counts_and_rows, check_chips, check_table, check_incidents_column, check_button_and_page, check_detail_pill]


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
