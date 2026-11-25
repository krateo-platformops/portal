#!/usr/bin/env python3
"""The observability page's SLOs tab (Diego, 2026-11-16: the approved mockup, a tab, admins only).

Over the chart as rendered, and through snowplow's own path renderer:
  - every stage sends its statement from files/slo/ VERBATIM as ClickHouse's `query`, with
    default_format=JSON and output_format_json_quote_denormals=1, and the range chip's window as the
    statement's own param_start / param_end ([now - range, now + 1 s));
  - the filter, over the real responses observability read on krateo-057 (scripts/fixtures/obs-slo,
    ClickHouse 26.10, the same settings), gives the figures the mockup shows: 99.9845 % / 100 %,
    114 failed of 736,961 calls, and NO percentile while the window spans the 11 Nov layout change;
  - the rules sli-span-metrics.sql states hold: one row per layout means "layout changed", no row or a
    NULL means "no samples" (never 0 or 100 %), `inf` is "> last bound", and an SLI is above its target
    only when its whole interval is;
  - a stage that could not be read says so, rather than reading as no traffic;
  - the latency card holds the explanation or the table, never both; the table and the tiles are
    exception-only (orange above target, nothing when met).
That the vendored statements equal the pinned source is scripts/vendor-sli-sql.py --check.

Usage: test-obs-slo.py [--crds DIR | --pinned]. Exit code = failed checks.
"""
import copy
import importlib.util
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, 'fixtures', 'obs-slo')
SLO = os.path.join(os.path.dirname(HERE), 'helm', 'observability-page', 'files', 'slo')


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


traces = _load('traces', 'test-observability-traces.py')   # render() of observability-page
gates = traces.gates
tbi = traces.tbi

RA = 'obs-slo'
STAGES = {'avail': '1-availability', 'apiLat': '2-api-latency', 'uiLat': '3-ui-latency', 'fetch': '4-widget-fetch', 'burn': '5-burn',
          'avail30': '30d-1-availability-budget', 'burn1h': '5-burn'}
FIXED = {'avail30': 2592000, 'burn1h': 3600}         # the SLO's own window and the burn line's, whatever the range
SECONDS = {'1h': 3600, '6h': 21600, '24h': 86400, '7d': 604800}


def fx(name):
    return json.load(open(os.path.join(FIX, name + '.json'), encoding='utf-8'))


# krateo-057 today: krateo_slo_daily does not exist, and ClickHouse answers 404 with this body (read by
# observability, 2026-11-16). snowplow records it as a Failure envelope under the errorKey.
UNKNOWN_TABLE = {'__status__': {'kind': 'Status', 'apiVersion': 'v1', 'status': 'Failure', 'code': 404,
                                'message': open(os.path.join(FIX, '057-unknown-table.body.txt'), encoding='utf-8').read()}}
WEEK = {'avail': fx('stmt1-7d'), 'apiLat': fx('stmt2-7d'), 'uiLat': fx('stmt3-7d'), 'fetch': fx('stmt4-7d'), 'burn': fx('stmt5-7d'),
        'avail30': UNKNOWN_TABLE, 'burn1h': fx('stmt5-7d')}


def ch(rows):
    return {'meta': [], 'data': rows, 'rows': len(rows)}


def resolve(c, extras=None, responses=None):
    reqs, _, out = gates.resolve(c.get('RESTAction', RA), extras or {}, copy.deepcopy(WEEK if responses is None else responses))
    return reqs, out


def expect(problems, label, got, want):
    if got != want:
        problems.append(f'{label}: got {got!r}, want {want!r}')


def check_requests(c):
    p = []
    for rng in ('1h', '6h', '24h', '7d', None):
        reqs, _ = resolve(c, {} if rng is None else {'range': rng})
        expect(p, f'{rng}: one request per statement', [r[0] for r in reqs], list(STAGES))
        for name, path, _ in reqs:
            q = parse_qs(urlsplit(path).query, keep_blank_values=True)
            stmt = open(os.path.join(SLO, STAGES[name] + '.sql'), encoding='utf-8').read()
            expect(p, f'{rng} {name}: the statement, verbatim', q.get('query'), [stmt])
            expect(p, f'{rng} {name}: settings', (q.get('default_format'), q.get('output_format_json_quote_denormals')), (['JSON'], ['1']))
            import datetime
            start, end = (datetime.datetime.strptime(q[k][0], '%Y-%m-%d %H:%M:%S') for k in ('param_start', 'param_end'))
            expect(p, f'{rng} {name}: window (end exclusive, +1 s)', int((end - start).total_seconds()), FIXED.get(name, SECONDS[rng or '24h']) + 1)
            expect(p, f'{rng} {name}: nothing else on the request', sorted(q), ['default_format', 'output_format_json_quote_denormals', 'param_end', 'param_start', 'query'])
    return p


def check_week(c):
    """The real 7-day responses from krateo-057: the mockup's first board."""
    p = []
    _, out = resolve(c, {'range': '7d'})
    av = {a['key']: a for a in out['availability']}
    expect(p, 'API availability', (av['snowplow']['value'], av['snowplow']['below']), ('99.9845 %', False))
    # OBS-5: the figure says whether it meets its target, then the counts.
    expect(p, 'API detail: the verdict, then the counts', av['snowplow']['detail'].split(' · ')[0:2], ['Meets the 99.9 % target', '114 failed of 736,961 calls'])
    expect(p, 'the window, in range mode, governs every card', out['window']['label'].startswith('WINDOW · last 7 days · '), True)
    expect(p, 'UI availability', av['krateo-frontend']['value'], '100 %')
    expect(p, 'UI detail carries the widget fetches', av['krateo-frontend']['detail'].endswith('widget fetches: 0 failed of 59,835'), True)
    expect(p, 'the API window spans two layouts', out['layoutChanged'], True)
    rows = {r['objective']: r for r in out['latency']}
    expect(p, 'API row', (rows['API · every snowplow request']['state'], rows['API · every snowplow request']['n']), ('layoutChanged', 736961))
    expect(p, 'form.submit has samples in one layout only', (rows['UI · submit a form']['state'], rows['UI · submit a form']['p95'], rows['UI · submit a form']['n']), ('met', 'between 100 ms and 250 ms', 62))
    expect(p, 'page.publish has none', (rows['UI · publish a page']['state'], rows['UI · publish a page']['n']), ('noSamples', 0))
    expect(p, 'the note names the window', '7-day figures' in out['note'], True)
    return p


def check_one_layout(c):
    p = []
    _, out = resolve(c, {'range': '24h'}, dict(WEEK, apiLat=fx('stmt2-1layout')))
    api = out['latency'][0]
    expect(p, 'one layout: a percentile', (out['layoutChanged'], api['state'], api['p95'], api['n']), (False, 'met', 'between 100 ms and 250 ms', 1614))
    return p


def check_rules(c):
    p = []
    bounds = [25, 50, 100, 250, 500, 750, 1000, 1500, 2000, 2500, 5000, 10000]
    row = lambda lo, up, ok, n=10: {'bounds_ms': bounds, 'n': n, 'p95_lower_ms': lo, 'p95_upper_ms': up, 'p95_within_target': ok}
    for label, r, want in [
        # OBS-5: the interval in words, not "(2.5 s, 5.0 s]".
        ('overflow is "above the last bound"', row(10000, 'inf', 0), ('above', 'above 10.0 s')),
        ('a whole interval above the target', row(500, 750, 0), ('above', 'between 500 ms and 750 ms')),
        ('an interval that straddles the target', row(250, 1000, 0), ('atTarget', 'between 250 ms and 1.0 s')),
        ('upper equal to the target is met (le)', row(250, 500, 1), ('met', 'between 250 ms and 500 ms')),
        ('64-bit counts may arrive quoted', dict(row(0, 25, 1), n='307317'), ('met', 'between 0 ms and 25 ms')),
    ]:
        _, out = resolve(c, {}, dict(WEEK, apiLat=ch([r])))
        expect(p, label, (out['latency'][0]['state'], out['latency'][0]['p95']), want)
    _, out = resolve(c, {}, dict(WEEK, avail=ch([{'sli_service': 'snowplow', 'calls': 0, 'sli_errors': 0, 'availability': None}]), burn=ch([])))
    av = {a['key']: a for a in out['availability']}
    expect(p, 'no calls is no samples, not 100 %', (av['snowplow']['value'], av['snowplow']['detail']), ('—', 'No calls in this window.'))
    expect(p, 'a class with no row at all is no samples too', av['krateo-frontend']['value'], '—')
    _, out = resolve(c, {}, dict(WEEK, avail=ch([{'sli_service': 'snowplow', 'calls': 10000, 'sli_errors': 20, 'availability': 0.998}])))
    expect(p, 'below 99.9 % is flagged', [a['below'] for a in out['availability'] if a['key'] == 'snowplow'], [True])
    expect(p, 'below 99.9 % says it misses the target', [a['detail'].split(' · ')[0] for a in out['availability'] if a['key'] == 'snowplow'],
           ['Below the 99.9 % target'])
    _, out = resolve(c, {}, {k: v for k, v in WEEK.items() if k not in ('avail', 'apiLat')})
    expect(p, 'an unreadable statement says so', ([a['detail'] for a in out['availability']][0], out['latency'][0]['p95']),
           ('Couldn’t read this from ClickHouse.', 'Couldn’t read this from ClickHouse.'))
    expect(p, 'an unreadable API latency is not a layout change', out['layoutChanged'], False)
    return p


def check_30d(c):
    """Board 3, once krateo_slo_daily exists: observability's fixtures from the chart's real DDL."""
    p = []
    av = lambda out: {a['key']: a for a in out['availability']}
    # Two days of data: figures, no bar yet, and an overspent UI budget.
    _, out = resolve(c, {'range': '7d'}, dict(WEEK, avail30=fx('30d-1-two-classes')))
    a = av(out)
    expect(p, '30d mode', (out['mode'], out['availTitle']), ('30d', 'Availability · target ≥ 99.9 % over 30 days'))
    expect(p, 'API over 30 days', (a['snowplow']['value'], a['snowplow']['below'], a['snowplow']['showBar']), ('99.9867 %', False, False))
    expect(p, 'fewer than 30 days: the count, no bar', a['snowplow']['budget'], 'Budget from 30 days of data: 2 of 30 so far.')
    expect(p, 'an overspent budget says so, in red', (a['krateo-frontend']['budget'], a['krateo-frontend']['budgetColor'], a['krateo-frontend']['below']),
           ('Error budget spent: availability below 99.9 % over 30 days.', 'red', True))
    expect(p, 'the note names the days with data', '2 of them have data so far' in out['note'], True)
    # OBS-5: "WINDOW · LAST 24 HOURS" sat above the 30-day availability; in 30-day mode it is the latency's.
    expect(p, '30d mode: the range window is the latency\'s', out['window']['label'].startswith('LATENCY WINDOW · last 7 days · '), True)
    expect(p, '30d mode: the verdict over 30 days', (a['snowplow']['detail'].split(' · ')[0], a['krateo-frontend']['detail'].split(' · ')[0]),
           ('Meets the 99.9 % target', 'Below the 99.9 % target'))
    # A full month: the bar appears.
    full = fx('30d-1-two-classes')
    full['data'] = [dict(r, days_with_data=30) for r in full['data']]
    _, out = resolve(c, {}, dict(WEEK, avail30=full))
    a = av(out)
    expect(p, '30 days: the bar and the remaining budget', (a['snowplow']['showBar'], a['snowplow']['budgetPercent'], a['snowplow']['budget']),
           (True, 87, 'Error budget left 86.7 %'))
    # No calls, and a class with no row at all, are both "no samples", never 100 %.
    _, out = resolve(c, {}, dict(WEEK, avail30=fx('30d-1-calls-zero')))
    a = av(out)
    expect(p, 'calls = 0 is no samples', (a['krateo-frontend']['value'], a['krateo-frontend']['detail'], a['krateo-frontend']['budget']),
           ('—', 'No interactions in the last 30 days.', ''))
    expect(p, 'a class with no row is no samples', (a['snowplow']['value'], a['snowplow']['detail']), ('—', 'No calls in the last 30 days.'))
    _, out = resolve(c, {}, dict(WEEK, avail30=fx('30d-1-empty-window')))
    expect(p, 'an empty window', [x['value'] for x in out['availability']], ['—', '—'])
    # The last hour burning past the alerts' line.
    hot = {'meta': [], 'data': [{'sli_service': 'snowplow', 'calls': 1000, 'sli_errors': 16, 'burn_rate': 16.0}], 'rows': 1}
    _, out = resolve(c, {}, dict(WEEK, avail30=full, burn1h=hot))
    a = av(out)
    expect(p, 'fast burn is named', (a['snowplow']['burning'], a['snowplow']['detail'].endswith('above the 14.4× line the alerts page on')), (True, True))
    # The table missing falls back to the range's figures (board 1); any other failure says so.
    _, out = resolve(c, {'range': '7d'})
    expect(p, 'UNKNOWN_TABLE falls back to the range', (out['mode'], av(out)['snowplow']['value']), ('range', '99.9845 %'))
    other = {'__status__': {'kind': 'Status', 'status': 'Failure', 'code': 500, 'message': 'Code: 241. DB::Exception: Memory limit exceeded'}}
    _, out = resolve(c, {}, dict(WEEK, avail30=other))
    expect(p, 'another failure is reported, not hidden', (out['mode'], av(out)['snowplow']['detail']), ('error', 'Couldn’t read this from ClickHouse.'))
    return p


def check_widgets(c):
    p = []
    _, week = resolve(c, {'range': '7d'})
    _, one = resolve(c, {'range': '24h'}, dict(WEEK, apiLat=fx('stmt2-1layout')))
    items = lambda out: [i['resourceRefId'] for i in tbi.resolved_widget(c, 'Card', 'obs-slo-latency-card', out, {'range': 'x'}, 'latency card')['spec']['widgetData']['items']]
    expect(p, 'layout changed: the explanation only', items(week), ['obs-slo-layout-changed'])
    expect(p, 'one layout: the table only', items(one), ['obs-slo-latency'])
    table = tbi.resolved_widget(c, 'Table', 'obs-slo-latency', one, {'range': 'x'}, 'latency table')['spec']['widgetData']['dataSource']
    cell = {row[0]['stringValue']: {d['valueKey']: d for d in row} for row in table}
    expect(p, 'a met row has no colour', 'color' in cell['API · every snowplow request']['p95'], False)
    expect(p, 'every row shows its samples', cell['API · every snowplow request']['samples']['stringValue'], '1614')
    expect(p, 'no samples is gray', cell['UI · publish a page']['p95'].get('color'), 'var(--gray-color)')
    stat = tbi.resolved_widget(c, 'Statistic', 'obs-slo-avail-api-value', week, {'range': 'x'}, 'api value')['spec']['widgetData']
    expect(p, 'the tile value', (stat['value'], stat.get('valueStyle')), ('99.9845 %', {}))
    full = fx('30d-1-two-classes')
    full['data'] = [dict(r, days_with_data=30) for r in full['data']]
    _, month = resolve(c, {}, dict(WEEK, avail30=full))
    _, early = resolve(c, {}, dict(WEEK, avail30=fx('30d-1-two-classes')))
    tile = lambda out: [i['resourceRefId'].removeprefix('obs-slo-avail-api-') for i in
                        tbi.resolved_widget(c, 'Card', 'obs-slo-avail-api', out, {'range': 'x'}, 'api tile')['spec']['widgetData']['items']]
    expect(p, 'range mode: value and detail only', tile(week), ['value', 'detail'])
    expect(p, 'under 30 days: the budget line, no bar', tile(early), ['value', 'budget', 'detail'])
    expect(p, '30 days: the bar and the budget line', tile(month), ['value', 'budget-bar', 'budget', 'detail'])
    title = lambda out: tbi.resolved_widget(c, 'Card', 'obs-slo-avail-card', out, {'range': 'x'}, 'avail card')['spec']['widgetData']['title']
    expect(p, 'the card title follows the window', (title(week), title(month)), ('Availability · target ≥ 99.9 %', 'Availability · target ≥ 99.9 % over 30 days'))
    bar = tbi.resolved_widget(c, 'Progress', 'obs-slo-avail-api-budget-bar', month, {'range': 'x'}, 'bar')['spec']['widgetData']
    expect(p, 'the bar', (bar['percent'], bar['strokeColor']), (87, 'gray'))
    for kind, name in [('Paragraph', 'obs-slo-avail-api-budget'), ('Paragraph', 'obs-slo-avail-ui-budget'), ('Progress', 'obs-slo-avail-ui-budget-bar'), ('Card', 'obs-slo-avail-ui')]:
        tbi.resolved_widget(c, kind, name, month, {'range': 'x'}, name)
    for kind, name in [('Paragraph', 'obs-slo-window'), ('Paragraph', 'obs-slo-note'), ('Paragraph', 'obs-slo-avail-api-detail'),
                       ('Paragraph', 'obs-slo-avail-ui-detail'), ('Statistic', 'obs-slo-avail-ui-value')]:
        tbi.resolved_widget(c, kind, name, week, {'range': 'x'}, name)
    tabs = c.get('Tabs', 'obs-main')['spec']['widgetData']['items']
    expect(p, 'SLOs follows Traces', [t['label'] for t in tabs][[t['label'] for t in tabs].index('Traces') + 1], 'SLOs')
    return p


def check_authored(c):
    for kind, name in [('Col', 'obs-tab-slos'), ('Paragraph', 'obs-slo-desc'), ('Card', 'obs-slo-avail-card'), ('Row', 'obs-slo-avail-row'),
                       ('Card', 'obs-slo-avail-api'), ('Card', 'obs-slo-avail-ui'), ('Alert', 'obs-slo-layout-changed'), ('Tabs', 'obs-main')]:
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
    c = traces.render(values={})
    checks = [
        ('every stage sends its statement verbatim, with the range as its parameters', lambda: check_requests(c)),
        ('the real 7-day responses from krateo-057 give the mockup\'s figures', lambda: check_week(c)),
        ('a one-layout window gives a percentile', lambda: check_one_layout(c)),
        ('the SQL file\'s rules: overflow, straddle, le, no samples, unreadable', lambda: check_rules(c)),
        ('board 3: 30 days and the error budget, or the range while the rollup table is missing', lambda: check_30d(c)),
        ('the widgets: explanation or table, exception-only colour, the tab', lambda: check_widgets(c)),
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
