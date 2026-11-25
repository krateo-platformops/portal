#!/usr/bin/env python3
"""
test-alerts-filter — /alerts' state filter, over the chart's own jq.

WHAT IT GUARDS. obs-alerts filters its rows by the ?status= chip (Firing when absent) and counts
every chip over the full set. The rules a reader would notice:
  - the state chips (alerts dashboard): Firing, Not evaluated, Pending, OK, All, in triage order,
    then With unresolved incidents (?status=open-incidents, any state; test-alerts-overview checks its
    rows), each with its count, the picked one active, every route keeping ?range, ?view and ?projects;
  - an alert with no HyperDX alert behind it is "Not evaluated", in the chip and in the table,
    whatever .status.state last said: nothing evaluates it;
  - a state is matched in any case; a ?status= that is no state shows no rows, and no chip is active;
  - the "Ask Agentiko →" link keeps the ?projects= scope and every param, so opening the rail does
    not reset the view;
  - ?view=table puts the table in the alerts card in place of the cards;
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
    """The state chips: label, count, active."""
    chips = tbi.resolved_widget(CH, 'Listy', 'alerts-status-chips', out, extras, 'state chips')['spec']['widgetData']['dataSource']
    return [(c['label'], c['count'], c['active']) for c in chips]


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
    expect(f, 'no ?status: the firing ones', [r['routeName'] for r in out['rows']], ['disk', 'latency'])
    expect(f, 'no ?status: Firing', out['status'], 'alert')
    for sel, want in (('alert', ['disk', 'latency']), ('OK', ['crashloop', 'oom', 'restarts']),
                      ('pending', ['fresh']), ('not-evaluated', ['rejected'])):
        got = [r['routeName'] for r in resolve({'status': sel})['rows']]
        expect(f, f'{sel} chip: its rows', got, want)
    expect(f, 'counts do not follow the chip', resolve({'status': 'ok'})['counts'], out['counts'])
    expect(f, 'not a chip: no rows', resolve({'status': 'firing'})['rows'], [])
    expect(f, 'explicit all: every row', len(resolve({'status': 'all'})['rows']), 7)
    out = resolve({}, failed=True)
    expect(f, 'unreadable alerts: zero, no crash', (out['rows'], out['counts']['all']), ([], 0))
    return f


def check_state_chips():
    f = []
    ex = {'projects': 'team a'}
    out = resolve(ex)
    expect(f, 'every state, in triage order, with its count, Firing active', states(out, ex),
           [('Firing', 2, True), ('Not evaluated', 1, False), ('Pending', 1, False), ('OK', 3, False), ('All', 7, False),
            ('With unresolved incidents', 0, False)])
    expect(f, 'the routes keep the scope and leave the default out', [c['route'] for c in out['chips']],
           ['/alerts?projects=team%20a', '/alerts?projects=team%20a&status=not-evaluated', '/alerts?projects=team%20a&status=pending',
            '/alerts?projects=team%20a&status=ok', '/alerts?projects=team%20a&status=all', '/alerts?projects=team%20a&status=open-incidents'])
    ex = {'status': 'firing'}
    expect(f, 'not a state: no chip active', [a for _, _, a in states(resolve(ex), ex)], [False] * 6)
    return f


def check_table():
    f = []
    rows = {r['name'][0]: r['state'] for r in table(resolve({'status': 'all'}), {'status': 'all'})}
    expect(f, 'firing', rows['Latency'], ('Firing', 'red'))
    expect(f, 'firing, paused', rows['Disk'], ('Firing · paused', 'gray'))
    expect(f, 'quiet', rows['Crashloop'], ('OK', 'green'))
    expect(f, 'synced, undecided', rows['Fresh'], ('Pending', 'orange'))
    expect(f, 'no HyperDX alert', rows['Rejected'], ('Not evaluated', 'gray'))
    ex = {'status': 'alert'}
    expect(f, 'filtered table', [r['name'][0] for r in table(resolve(ex), ex)], ['Disk', 'Latency'])
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
    rows = {r['name'][0]: r['incidents'] for r in table(resolve({'status': 'all'}, items), {'status': 'all'})}
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
    for ex, want in (({}, '/alerts'), ({'status': 'OK', 'projects': 'team-a', 'range': '30d'}, '/alerts?projects=team-a&status=ok&range=30d'),
                     ({'status': 'firing'}, '/alerts?status=firing')):
        out = resolve(ex)
        path = tbi.resolved_widget(CH, 'Button', 'alerts-ask-agentiko', out, ex, 'ask button')['spec']['widgetData']['actions']['navigate'][0]['path']
        base, _, query = path.partition('?')
        params = parse_qs(query, keep_blank_values=True)
        prompt = params.pop('ask', [''])[0]
        kept = '&'.join(f'{k}={v[0]}' for k, v in params.items())
        expect(f, f'ask link keeps the view ({ex})', base + ('?' + kept if kept else ''), want)
        expect(f, f'ask link also offers to author a new alert ({ex})', prompt.endswith(' If I want a new alert instead, help me author it: what to watch (the telemetry query), the threshold and comparison, and the evaluation interval, then create it once I confirm.'), True)
    expect(f, 'button, no data: the list',
           tbi.widget(CH, 'Button', 'alerts-ask-agentiko', 'actions', {}, {})['navigate'][0]['path'], '/alerts')
    expect(f, 'no second Agentiko button: the author button is gone', [d['metadata']['name'] for d in CH.docs if d['metadata'].get('name') == 'alerts-author-autopilot'], [])
    want = CH.get('Table', 'obs-alerts')['spec']['keyExtras']
    expect(f, 'one cache key for every reader of the filtered action',
           [CH.get(k, n)['spec'].get('keyExtras') for k, n in (('Listy', 'alerts-status-chips'), ('Button', 'alerts-ask-agentiko'))],
           [want, want])
    ex = {'view': 'table'}
    card = tbi.resolved_widget(CH, 'Card', 'alerts-list-card', resolve(ex), ex, 'alerts card')['spec']['widgetData']
    expect(f, 'the table view: the state chips, then the table', [i['resourceRefId'] for i in card['items']],
           ['alerts-status-chips', 'obs-alerts'])
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


def check_create_form_offers_services():
    """The create form offers, on `where`, a predicate per service with telemetry in the last hour
    (portal#228), without closing the field; ClickHouse down leaves a plain field and a working form."""
    f = []
    crd = {'spec': {'versions': [{'name': 'v1alpha1', 'schema': {'openAPIV3Schema': {'properties': {'spec': {
        'type': 'object', 'required': ['interval'],
        'properties': {'where': {'type': 'string'}, 'interval': {'type': 'string'}}}}}}}]}}
    nss = {'items': [{'metadata': {'name': 'krateo-system'}}]}
    # ClickHouse FORMAT JSON: 64-bit counts arrive quoted.
    services = {'data': [{'svc': 'api', 'logs': '1200', 'spans': '0'},
                         {'svc': 'snowplow', 'logs': '1', 'spans': '35'},
                         {'svc': 'otel.collector-1', 'logs': '0', 'spans': '1'},
                         {'svc': "it's", 'logs': '3', 'spans': '0'},   # no SQL literal as it is: left out
                         {'svc': '', 'logs': '9', 'spans': '0'}]}

    def form(responses):
        out = tbi.resolve(CH, 'alert-formdef', responses, {})
        return out, tbi.resolved_widget(CH, 'Form', 'alert-create', out, {}, 'alert create form')['spec']['widgetData']

    out, wd = form({'crd': crd, 'namespaces': nss, 'services': services})
    group = 'Services with telemetry in the last hour'
    expect(f, 'where: one predicate per populated service', wd.get('suggestions'), [{'name': 'where', 'options': [
        {'value': "ServiceName = 'api'", 'description': '1200 log lines in the last hour', 'group': group},
        {'value': "ServiceName = 'snowplow'", 'description': '1 log line · 35 spans in the last hour', 'group': group},
        {'value': "ServiceName = 'otel.collector-1'", 'description': '1 span in the last hour', 'group': group}]}])
    expect(f, 'where stays an open text field (no enum)', 'enum' in wd['schema']['properties']['where'], False)
    expect(f, 'the services read is a static query',
           '${' in CH.get('RESTAction', 'alert-formdef')['spec']['api'][2]['path'], False)
    for label, resp in (('ClickHouse down', 'ERROR'), ('no telemetry', {'data': []})):
        out, wd = form({'crd': crd, 'namespaces': nss, 'services': resp})
        expect(f, f'{label}: no suggestions', wd.get('suggestions'), [])
        expect(f, f'{label}: the form still renders the spec', sorted(wd['schema']['properties']),
               ['__alert_name__', '__alert_namespace__', 'interval', 'where'])
    return f


def check_detail_subtitle():
    """The alert page's header says the rule in words and when it was last checked: the threshold,
    the interval and the last evaluation, which only the manifest showed (audit ALD-1)."""
    f = []
    ra = CH.get('RESTAction', 'alert-detail')
    ex = {'name': 'x', 'namespace': 'krateo-system'}

    def subtitle(spec=None, status=None, a=None):
        a = a if a is not None else alert('x', 'OK')
        a['spec'].update(spec or {})
        a['status'].update(status or {})
        out = tbi.jq(ra['spec']['filter'], dict(ex, alert=a, incidents={'items': []}))
        return tbi.resolved_widget(CH, 'PageHeader', 'alert-detail-page-header', out, ex, 'detail header')['spec']['widgetData']['subtitle']

    seen = {'stateObservedAt': '2026-11-25T14:02:31Z'}
    expect(f, 'log alert, every row', subtitle(status=seen),
           'Fires when the number of log lines over 5 minutes is at least 1 · last checked 2026-11-25 14:02 UTC')
    expect(f, 'log alert with a where; an offset stamp in UTC',
           subtitle({'where': "ServiceName = 'api'"}, {'stateObservedAt': '2026-11-25T16:02:31.5+02:00'}),
           'Fires when the number of matching log lines over 5 minutes is at least 1 · last checked 2026-11-25 14:02 UTC')
    expect(f, 'traces, strictly more than, 1 hour',
           subtitle({'source': 'traces', 'where': "StatusCode = 'Error'", 'thresholdType': 'above_exclusive',
                     'threshold': 10, 'interval': '1h'}, seen),
           'Fires when the number of matching spans over 1 hour is more than 10 · last checked 2026-11-25 14:02 UTC')
    expect(f, 'grouped metric, consecutive intervals',
           subtitle({'metricRef': {'name': 'k8s-container-ready'}, 'aggregation': 'max', 'thresholdType': 'below',
                     'interval': '1m', 'groupBy': ['k8s.pod.name', 'k8s.container.name'], 'consecutiveWindows': 5}, seen),
           'Fires when the maximum of k8s-container-ready per k8s.pod.name, k8s.container.name over 1 minute'
           ' is less than 1, 5 intervals in a row · last checked 2026-11-25 14:02 UTC')
    expect(f, 'ratio metric, a range',
           subtitle({'metricRef': {'name': 'http-requests'}, 'aggregation': 'sum', 'ratio': {'labels': {}},
                     'thresholdType': 'not_between', 'threshold': 0, 'thresholdMax': 0.01}, seen),
           'Fires when the sum of http-requests as a share over 5 minutes is outside 0 to 0.01'
           ' · last checked 2026-11-25 14:02 UTC')
    expect(f, 'value read from log rows, at most',
           subtitle({'value': 'toFloat64OrZero(Body)', 'aggregation': 'p95', 'where': 'x', 'thresholdType': 'below_or_equal',
                     'threshold': 2.5}, seen),
           'Fires when the 95th percentile of a value read from matching log lines over 5 minutes is at most 2.5'
           ' · last checked 2026-11-25 14:02 UTC')
    expect(f, 'between names both bounds', subtitle({'thresholdType': 'between', 'threshold': 1, 'thresholdMax': 3}),
           'Fires when the number of log lines over 5 minutes is between 1 and 3')
    expect(f, 'never read: no "last checked"', subtitle(), 'Fires when the number of log lines over 5 minutes is at least 1')
    expect(f, 'nothing evaluates it: no "last checked", whatever the stale stamp says',
           subtitle(status=seen, a=alert('x', 'OK', synced=False)),
           'Fires when the number of log lines over 5 minutes is at least 1')
    out = tbi.jq(ra['spec']['filter'], dict(ex, alertError=['forbidden'], incidents={'items': []}))
    expect(f, 'unreadable alert: no subtitle',
           tbi.resolved_widget(CH, 'PageHeader', 'alert-detail-page-header', out, ex, 'detail header')['spec']['widgetData']['subtitle'], '')
    return f


CHECKS = [check_counts_and_rows, check_state_chips, check_table, check_incidents_column, check_button_and_page, check_detail_pill,
          check_create_form_offers_services, check_detail_subtitle]


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
