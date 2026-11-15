#!/usr/bin/env python3
"""
test-obs-reconcile-phases — the per-phase reconcile latency on /observability (obs-reconcile-perf,
step `phases`) measures each cumulative counter as ONE series.

WHAT IT GUARDS. The step reads four cumulative OTel histograms (queue wait, get, RBAC generate, RBAC
apply) and turns them into a windowed mean: per series, the increase of Sum and Count over the
window; then summed across series. That is only right when a "series" is one counter. Before this
test it was (service.instance.id, krateo.io/composition-gvr), and service.instance.id is the cdc
DEPLOYMENT name, so a pod restart in the window put the old process and the new one (whose counters
restart at 0) in one group. On krateo-057 the 6h count read 56691 reconciles against 30719 reconcile
spans in otel_traces; grouped per series it reads 30769.

For every range the page offers (1h, 6h, 24h, 7d), with the request rendered by snowplow's own path
renderer (test-ra-input-gates' resolver), the decoded SQL must:
  - group the inner SELECT by the series identity: instance, the composition-gvr resource attribute,
    the krateo.composition.gvr POINT attribute (a pooled cdc hosts several Kinds in one process,
    where both resource attributes name the pool; cdc 2.13.100+), and StartTimeUnix (each process's
    counters are their own series);
  - count a series that STARTED inside the window from 0, not from its first exported point, with
    the same interval as the WHERE;
  - be decoded exactly once (no '%' left after one unquote).

CONTROL. The previous query, swapped into the same rendered request, must fail these checks, or the
checks do not test the change. The live numbers above are the measurement; this pins the shape.

Usage: test-obs-reconcile-phases.py. Exit code = failed checks.
"""
import importlib.util
import os
import re
import sys
from urllib.parse import quote, unquote

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


traces = _load('traces', 'test-observability-traces.py')   # its render() of observability-page
gates = traces.gates                                        # snowplow's resolver, modelled

INTERVALS = {'1h': '1 HOUR', '6h': '6 HOUR', '24h': '24 HOUR', '7d': '7 DAY'}
IDENTITY = ["ResourceAttributes['service.instance.id'] AS sid",
            "ResourceAttributes['krateo.io/composition-gvr'] AS gvr",
            "Attributes['krateo.composition.gvr'] AS pgvr",
            "StartTimeUnix AS st"]
GROUP = 'GROUP BY metric, sid, gvr, pgvr, st )'
OLD_INNER = ("ResourceAttributes['krateo.io/composition-gvr'] AS gvr, max(Sum)-min(Sum) AS dsum, "
             "max(Count)-min(Count) AS dcount FROM")


def phases_sql(chart, rng, swap=None):
    ra = chart.get('RESTAction', 'obs-reconcile-perf')
    if swap:
        ra = swap(ra)
    requests, _, _ = gates.resolve(ra, {'range': rng}, {'pctl': {'data': []}, 'phases': {'data': []}})
    paths = [p for s, p, _ in requests if s == 'phases']
    assert len(paths) == 1, f'{rng}: {len(paths)} phases requests'
    assert paths[0].startswith('/?query='), paths[0]
    return unquote(paths[0][len('/?query='):])


def problems_in(sql, rng):
    iv = INTERVALS[rng]
    out = []
    if '%' in sql:
        out.append(f'{rng}: still encoded after one decode')
    inner = sql.split('FROM ( ', 1)[-1]
    for col in IDENTITY:
        if col not in inner:
            out.append(f'{rng}: the inner SELECT lacks {col!r}')
    if GROUP not in sql:
        out.append(f'{rng}: the inner GROUP BY is not the series identity ({GROUP!r})')
    for col in ('Sum', 'Count'):
        rule = f'max({col}) - if(any(StartTimeUnix) > now() - INTERVAL {iv}, 0, min({col}))'
        if rule not in inner:
            out.append(f'{rng}: {col} is not counted from 0 for a series that started in the window ({rule!r})')
    if f'TimeUnix > now() - INTERVAL {iv}' not in inner:
        out.append(f'{rng}: the window is not {iv}')
    return out


def old_query(ra):
    """The query before this change: no point attribute, no StartTimeUnix, plain max-min."""
    import copy
    ra = copy.deepcopy(ra)
    step = next(s for s in ra['spec']['api'] if s['name'] == 'phases')
    enc = lambda t: quote(t, safe='._-~')
    path = step['path']
    path = re.sub(re.escape(enc("ResourceAttributes['krateo.io/composition-gvr'] AS gvr, ")) + r'.*?' +
                  re.escape(enc(' AS dcount FROM')), enc(OLD_INNER).replace('\\', '\\\\'), path, count=1)
    path = path.replace(enc(GROUP), enc('GROUP BY metric, sid, gvr )'))
    step['path'] = path
    return ra


def main():
    chart = traces.render()
    failures = []
    # Control first: the old query must be refused for every range.
    for rng in INTERVALS:
        sql = phases_sql(chart, rng, swap=old_query)
        if OLD_INNER not in sql:
            failures.append(f'control {rng}: the old query could not be swapped in, so the control means nothing')
        elif not problems_in(sql, rng):
            failures.append(f'control {rng}: the old query was NOT refused')
    if not failures:
        print('[PASS] control: the old (instance, gvr) max-min query is refused for every range')
    for rng in INTERVALS:
        failures += problems_in(phases_sql(chart, rng), rng)
    for f in failures:
        print(f'[FAIL] {f}')
    print(f'{"FAILED" if failures else "ok"}: obs-reconcile-perf phases, {len(INTERVALS)} ranges '
          f'({len(failures)} failure(s))')
    return min(len(failures), 125)


if __name__ == '__main__':
    sys.exit(main())
