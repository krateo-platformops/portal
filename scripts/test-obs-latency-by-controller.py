#!/usr/bin/env python3
"""
test-obs-latency-by-controller — the Telemetry tab's Reconcile latency card, By composition | By
controller (Diego, 2026-11-15; mockup approved), evaluated end to end.

WHAT IT GUARDS, each through the chart's own jq (widgets via test-ra-input-gates' model of snowplow,
every stage path rendered by snowplow's own renderer):
  - the card shows the per-composition table unless ?latencyBy=controller, and only then the
    controller view; any other value is By composition. Only the view shown is referenced, so the
    other makes no ClickHouse call;
  - the chips: two, the active one from ?latencyBy, each route keeping the tab, the range, the log
    limit, serviceName and projects, the controller chip adding latencyBy=controller. The shared
    range chips and the log-limit chips carry latencyBy, so changing either keeps the view;
  - the RESTAction's two requests, for every range, decoded once: the exact p95 over tagged reconcile
    spans grouped by group+plural (versions merged), no root-only filter; the coverage count over
    every reconcile span; default 24h, the range chips' own default;
  - the filter: table / empty / unavailable views, the coverage note written from the data (first
    tagged span, "complete from" the minute after the last untagged one, the untagged count), no
    note once the window starts after the cutover, 64-bit integers quoted or bare, the error text
    verbatim when ClickHouse fails;
  - the body Flex shows the note only when there is one, then the table or the one message; the
    message is info when empty, warning when unreadable;
  - the table: "Kind · N versions" when a range spans an upgrade, the p95 tint of the composition
    table (>3 s red, >1.5 s orange), avg neutral, a row opening Compositions filtered to its Kind;
  - with --crds DIR or --pinned, every widget, as authored and as resolved here, validates.

Usage: test-obs-latency-by-controller.py [--crds DIR | --pinned]. Exit code = failed checks.
"""
import importlib.util
import os
import sys
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


traces = _load('traces', 'test-observability-traces.py')   # render() of observability-page
gates = traces.gates
tbi = traces.tbi

RA = 'obs-reconcile-by-controller'
IV = {'1h': '1 HOUR', '6h': '6 HOUR', '24h': '24 HOUR', '7d': '7 DAY'}
FIRST, LAST_UNTAGGED = 1794738256, 1794738347          # 2026-11-15 10:24:16Z, 10:25:47Z (krateo-057)
ROWS = [
    {'kind': 'Installer', 'grp': 'composition.krateo.io', 'plural': 'installers', 'versions': '8', 'compositions': '1',
     'reconciles': '334', 'p95_ms': 69583, 'avg_ms': 42943},
    {'kind': 'BuilderPublish', 'grp': 'composition.krateo.io', 'plural': 'builderpublishes', 'versions': 1, 'compositions': 17,
     'reconciles': 5468, 'p95_ms': 2100, 'avg_ms': 900},
    {'kind': 'EventFeedback', 'grp': 'composition.krateo.io', 'plural': 'eventfeedbacks', 'versions': 1, 'compositions': 1,
     'reconciles': 3, 'p95_ms': 830, 'avg_ms': 610},
]


def ch(data):
    return {'meta': [], 'data': data, 'rows': len(data)}


def resolve(c, extras=None, rows=ROWS, cov=None, down=False):
    responses = {'cov': ch([cov if cov is not None else {'untagged': '96203', 'first_tagged': FIRST, 'last_untagged': LAST_UNTAGGED}])}
    if not down:
        responses['byk'] = ch(rows)
    return gates.resolve(c.get('RESTAction', RA), extras or {}, responses)


def expect(problems, label, got, want):
    if got != want:
        problems.append(f'{label}: got {got!r}, want {want!r}')


def card_view(c, extras, label):
    w = tbi.resolved_widget(c, 'Card', 'obs-latency-card', {}, extras, f'card ({label})')['spec']['widgetData']
    return [i['resourceRefId'] for i in w['items']], w['extra']


def check_card(c):
    f = []
    expect(f, 'default: the composition table', card_view(c, {}, 'default'), (['obs-latency-chips', 'obs-reconcile-by-composition'], 'p95 · slowest first'))
    expect(f, 'controller', card_view(c, {'latencyBy': 'controller'}, 'controller'),
           (['obs-latency-chips', 'obs-latency-by-controller'], 'p95 · slowest first · exact, from reconcile spans'))
    expect(f, 'an unknown value is By composition', card_view(c, {'latencyBy': 'x;drop'}, 'junk')[0], ['obs-latency-chips', 'obs-reconcile-by-composition'])
    card = c.get('Card', 'obs-latency-card')
    expect(f, 'title', card['spec']['widgetData']['title'], 'Reconcile latency')
    expect(f, 'every view is a declared ref', sorted(r['id'] for r in card['spec']['resourcesRefs']['items']),
           ['obs-latency-by-controller', 'obs-latency-chips', 'obs-reconcile-by-composition'])
    expect(f, 'keyExtras partitions on latencyBy', card['spec'].get('keyExtras'), ['latencyBy'])
    return f


def chips(c, kind, name, extras, label):
    return tbi.resolved_widget(c, kind, name, {}, extras, label)['spec']['widgetData']['dataSource']


def check_chips(c):
    f = []
    ex = {'range': '7d', 'limit': '100', 'serviceName': 'snowplow', 'projects': 'team a', 'latencyBy': 'controller'}
    got = chips(c, 'Listy', 'obs-latency-chips', ex, 'latency chips (controller)')
    base = '/observability?tab=Telemetry&range=7d&limit=100&serviceName=snowplow&projects=team%20a'
    expect(f, 'chips', [(x['label'], x['active'], x['route']) for x in got],
           [('By composition', False, base), ('By controller', True, base + '&latencyBy=controller')])
    got = chips(c, 'Listy', 'obs-latency-chips', {}, 'latency chips (default)')
    expect(f, 'default: By composition active, range 24h',
           [(x['active'], x['route']) for x in got],
           [(True, '/observability?tab=Telemetry&range=24h'), (False, '/observability?tab=Telemetry&range=24h&latencyBy=controller')])
    bad = chips(c, 'Listy', 'obs-latency-chips', {'serviceName': "x' OR 1=1", 'limit': '5;0'}, 'latency chips (hostile)')
    expect(f, 'hostile inputs are stripped', bad[0]['route'], '/observability?tab=Telemetry&range=24h&limit=50&serviceName=xOR11')
    rng = chips(c, 'Listy', 'obs-range-chips', {'latencyBy': 'controller'}, 'range chips (controller)')
    expect(f, 'range chips keep latencyBy', rng[0]['route'], '/observability?range=1h&latencyBy=controller')
    rng = chips(c, 'Listy', 'obs-range-chips', {}, 'range chips (default)')
    expect(f, 'range chips add nothing by default', rng[0]['route'], '/observability?range=1h')
    lim = chips(c, 'Listy', 'obs-limit-chips', {'latencyBy': 'controller'}, 'limit chips (controller)')
    expect(f, 'limit chips keep latencyBy', lim[0]['route'], '/observability?range=24h&limit=40&latencyBy=controller&tab=Telemetry')
    return f


def check_requests(c):
    f = []
    for rng, iv in [(None, '24 HOUR')] + list(IV.items()):
        requests, _, _ = resolve(c, {'range': rng} if rng else {})
        sql = {s: unquote(p[len('/?query='):]) for s, p, _ in requests}
        label = rng or 'default'
        expect(f, f'{label}: two requests', sorted(sql), ['byk', 'cov'])
        byk, cov = sql.get('byk', ''), sql.get('cov', '')
        for want in (f'Timestamp > now() - INTERVAL {iv}', "SpanAttributes['krateo.composition.gvr'] != ''",
                     'quantileExact(0.95)(Duration)', 'GROUP BY grp, plural', 'ORDER BY p95_ms DESC'):
            if want not in byk:
                f.append(f'{label}: byk lacks {want!r}')
        if 'ParentSpanId' in byk or 'ParentSpanId' in cov:
            f.append(f'{label}: a root-only filter would drop reconciles By composition counts')
        if f'Timestamp > now() - INTERVAL {iv}' not in cov or "countIf(SpanAttributes['krateo.composition.gvr'] = '')" not in cov:
            f.append(f'{label}: cov is not the untagged count over the same window')
        if '%' in byk + cov:
            f.append(f'{label}: still encoded after one decode')
    return f


def check_filter(c):
    f = []
    _, _, out = resolve(c)
    expect(f, 'view', out['view'], 'table')
    expect(f, 'rows', [(r['kind'], r['versions'], r['compositions'], r['reconciles'], r['p95_ms']) for r in out['rows']],
           [('Installer', 8, 1, 334, 69583), ('BuilderPublish', 1, 17, 5468, 2100), ('EventFeedback', 1, 1, 3, 830)])
    expect(f, 'note', out['note'],
           'Controller timings from 15 Nov 10:24 UTC (complete from 10:26): only reconciles from cdc 2.13.100 on name '
           'their controller. 96203 earlier reconciles in this range are in By composition, not here.')
    _, _, after = resolve(c, {'range': '1h'}, cov={'untagged': 0, 'first_tagged': FIRST + 20000, 'last_untagged': 0})
    expect(f, 'no note once the window starts after the cutover', after['note'], '')
    _, _, empty = resolve(c, rows=[])
    expect(f, 'empty, older controllers', (empty['view'], empty['title'], empty['note']),
           ('empty', 'No reconcile in the last 24h names its controller.', ''))
    _, _, none = resolve(c, {'range': '6h'}, rows=[], cov={'untagged': 0, 'first_tagged': 0, 'last_untagged': 0})
    expect(f, 'empty, no spans at all', (none['view'], none['title']), ('empty', 'No reconcile spans in the last 6h.'))
    requests, d, _ = resolve(c, down=True)
    expect(f, 'byk dialled even when ClickHouse is down', sorted(s for s, _, _ in requests), ['byk', 'cov'])
    _, _, down = resolve(c, down=True)
    expect(f, 'unreadable', (down['view'], down['title'], down['rows']), ('unavailable', "Couldn't read reconcile timings.", []))
    if not down['message'].startswith('clickhouse-endpoint: '):
        f.append(f'unreadable: the message does not carry the error: {down["message"]!r}')
    return f


def check_body(c):
    f = []
    for label, kw, want_items, status in [
            ('table with note', {}, ['obs-latency-controller-coverage', 'obs-reconcile-by-controller'], None),
            ('table, no note', {'cov': {'untagged': 0, 'first_tagged': FIRST, 'last_untagged': 0}}, ['obs-reconcile-by-controller'], None),
            ('empty', {'rows': []}, ['obs-latency-controller-message'], 'info'),
            ('unreadable', {'down': True}, ['obs-latency-controller-message'], 'warning')]:
        _, _, out = resolve(c, **kw)
        flex = tbi.resolved_widget(c, 'Flex', 'obs-latency-by-controller', out, {}, f'body ({label})')['spec']['widgetData']
        expect(f, f'{label}: items', [i['resourceRefId'] for i in flex['items']], want_items)
        if 'obs-latency-controller-coverage' in want_items:
            alert = tbi.resolved_widget(c, 'Alert', 'obs-latency-controller-coverage', out, {}, f'coverage ({label})')['spec']['widgetData']
            expect(f, f'{label}: the note is the alert', alert['title'], out['note'])
        if status:
            res = tbi.resolved_widget(c, 'Result', 'obs-latency-controller-message', out, {}, f'message ({label})')['spec']['widgetData']
            expect(f, f'{label}: message', (res['status'], res['title'], res['subTitle']), (status, out['title'], out['message']))
        if 'obs-reconcile-by-controller' in want_items:
            tbi.resolved_widget(c, 'Table', 'obs-reconcile-by-controller', out, {}, f'table ({label})')
    return f


def check_table(c):
    f = []
    _, _, out = resolve(c)
    rows = tbi.resolved_widget(c, 'Table', 'obs-reconcile-by-controller', out, {}, 'table')['spec']['widgetData']['dataSource']
    cells = [{x['valueKey']: x for x in r} for r in rows]
    expect(f, 'controller labels', [x['controller']['stringValue'] for x in cells],
           ['Installer · 8 versions', 'BuilderPublish', 'EventFeedback'])
    expect(f, 'p95 labels and tints', [(x['p95']['stringValue'], x['p95']['color']) for x in cells],
           [('69.6 s', 'var(--red-color)'), ('2.1 s', 'var(--orange-color)'), ('830 ms', 'var(--gray-color)')])
    expect(f, 'avg is neutral', {x['avg']['color'] for x in cells}, {'var(--gray-color)'})
    expect(f, 'counts', [(x['compositions']['stringValue'], x['reconciles']['stringValue']) for x in cells],
           [('1', '334'), ('17', '5468'), ('1', '3')])
    t = c.get('Table', 'obs-reconcile-by-controller')['spec']['widgetData']
    expect(f, 'a row opens Compositions filtered to its Kind', t['rowNavigateTo'], '/compositions?scope=all&kind={kind}')
    expect(f, 'the kind cell feeds the row link', [x['kind']['stringValue'] for x in cells], ['Installer', 'BuilderPublish', 'EventFeedback'])
    return f


def check_authored(c):
    for kind, name in [('Card', 'obs-latency-card'), ('Listy', 'obs-latency-chips'), ('Flex', 'obs-latency-by-controller'),
                       ('Alert', 'obs-latency-controller-coverage'), ('Result', 'obs-latency-controller-message'),
                       ('Table', 'obs-reconcile-by-controller'), ('Listy', 'obs-range-chips'), ('Listy', 'obs-limit-chips')]:
        tbi.RESOLVED.append((f'authored {kind} {name}', c.get(kind, name)))
    return []


def main():
    args = sys.argv[1:]
    crds_dir, source = None, ''
    if '--crds' in args:
        crds_dir = source = args[args.index('--crds') + 1]
    elif '--pinned' in args:
        pinned = _load('pinned', 'lint-pinned-crds.py')
        version, why = pinned.pinned_version()
        if not version:
            print(f'cannot determine the pinned frontend-crds version: {why}')
            return 1
        crds_dir, source = pinned.unpack(version), f'frontend-crds {version} ({why})'
    c = traces.render(values={'observability': {'reconcileTracing': True}})
    checks = [
        ('the card shows one view, from ?latencyBy', lambda: check_card(c)),
        ('the chips keep the page state; range and limit chips keep latencyBy', lambda: check_chips(c)),
        ('two requests per range: exact p95 by controller, and coverage', lambda: check_requests(c)),
        ('the filter: views, coverage note, errors, 64-bit integers', lambda: check_filter(c)),
        ('the body: note, then table or one message', lambda: check_body(c)),
        ('the table: versions, tints, row link', lambda: check_table(c)),
    ]
    if crds_dir:
        checks.append(('(authored widgets queued for validation)', lambda: check_authored(c)))
    failed = 0
    for label, run in checks:
        try:
            problems = run()
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError, ValueError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {label}')
        for prob in dict.fromkeys(problems):
            print(f'        {prob}')
        failed += bool(problems)
    total = len(checks)
    if crds_dir:
        total += 1
        problems = tbi.validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} widgets (authored and resolved) validate against {source}')
        for prob in dict.fromkeys(problems):
            print(f'        {prob}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}; paths rendered by snowplow {gates.spath.renderer().source}'
          f'{"" if crds_dir else "; widgets NOT validated (no --crds / --pinned)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
