#!/usr/bin/env python3
"""
test-obs-counter-series — every /observability query over a CUMULATIVE OTel counter measures it per
SERIES, so a process restart or a second attribute set is not read as a reset or a jump.

WHY. A cumulative counter only grows within one process; a restart starts a new series at 0, and
each attribute set (method, status...) is a series of its own. Three RESTActions took max-min over
groups that held several series, and each was wrong on krateo-057:

  obs-reconcile-perf / phases   per-phase latency. Grouped by (service.instance.id, composition-gvr)
      and service.instance.id is the cdc DEPLOYMENT name: every roll's pod restart joined two
      processes. 6h: 56691 reconciles counted vs 30719 reconcile spans; per series 30769.
  obs-reconcile-metrics / fail  reconcile success/failure counts. Grouped by MetricName ONLY, i.e.
      across every core-provider process: 24h read 160869 successes where the processes made 52081.
  sp-http-mean / q              snowplow mean request duration per route. max-min per (bucket,
      route) mixed every method/status series and both pods across a restart: 24h read 2603073
      /call requests at 61 ms; per series 5651 at 6.7 ms.

THE RULE, checked on the SQL each request sends for every range the page offers (rendered by
snowplow's own path renderer through test-ra-input-gates' resolver, decoded exactly once):
  - the series identity is in the grouping or window partition: the resource identity, the point
    Attributes, and StartTimeUnix (OTel's start of a cumulative series; observability measured one
    StartTimeUnix per pod per series on 057 over 24h);
  - a series that STARTED inside the window counts from 0, against the same interval as the WHERE;
  - snowplow's per-bucket mean uses point-to-point increments (lagInFrame), so nothing between two
    buckets is lost, and no max-min is left.

CONTROLS. Each RESTAction's previous query, swapped into the same rendered request, must fail.

Usage: test-obs-counter-series.py. Exit code = failed checks.
"""
import copy
import importlib.util
import os
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

enc = lambda t: quote(t, safe='._-~')                       # this chart's encoding of SQL text

IV = {'phases': {'1h': '1 HOUR', '6h': '6 HOUR', '24h': '24 HOUR', '7d': '7 DAY'},
      'fail': {'1h': '1 HOUR', '6h': '6 HOUR', '24h': '24 HOUR', '7d': '7 DAY'},
      'q': {'1h': '1 HOUR', '6h': '6 HOUR', '24h': '24 HOUR', '7d': '7 DAY'}}


def rules(step, iv):
    """(label, substring) pairs the decoded SQL must contain."""
    w = f'now() - INTERVAL {iv}'
    if step == 'phases':
        return [('series identity in the inner SELECT',
                 "Attributes['krateo.composition.gvr'] AS pgvr, StartTimeUnix AS st"),
                ('grouped per series', 'GROUP BY metric, sid, gvr, pgvr, st )'),
                ('Sum from 0 when started in window', f'max(Sum) - if(any(StartTimeUnix) > {w}, 0, min(Sum))'),
                ('Count from 0 when started in window', f'max(Count) - if(any(StartTimeUnix) > {w}, 0, min(Count))'),
                ('window', f'TimeUnix > {w}')]
    if step == 'fail':
        return [('grouped per series',
                 "GROUP BY m, ResourceAttributes['service.instance.id'], toString(Attributes), StartTimeUnix )"),
                ('from 0 when started in window', f'max(Value) - if(any(StartTimeUnix) > {w}, 0, min(Value))'),
                ('summed across series', 'round(sum(d)) AS v'),
                ('window', f'TimeUnix > {w}')]
    return [('partitioned per series',
             "PARTITION BY ResourceAttributes['service.instance.id'], toString(Attributes), StartTimeUnix ORDER BY TimeUnix"),
            ('Sum point-to-point', f'if(row_number() OVER w = 1, if(StartTimeUnix > {w}, Sum, NULL), Sum - lagInFrame(Sum) OVER w)'),
            ('Count point-to-point',
             f'if(row_number() OVER w = 1, if(StartTimeUnix > {w}, toInt64(Count), NULL), toInt64(Count) - lagInFrame(toInt64(Count)) OVER w)'),
            ('window', f'TimeUnix > {w}')]


def sql_of(chart, ra_name, step, rng, ra=None):
    ra = ra or chart.get('RESTAction', ra_name)
    stub = {s['name']: {'data': []} for s in ra['spec']['api']}
    requests, _, _ = gates.resolve(ra, {'range': rng}, stub)
    paths = [p for s, p, _ in requests if s == step]
    assert len(paths) == 1, f'{ra_name}/{step} {rng}: {len(paths)} requests'
    assert paths[0].startswith('/?query='), paths[0]
    return unquote(paths[0][len('/?query='):])


def problems_in(sql, step, rng):
    out = [] if '%' not in sql else [f'{step} {rng}: still encoded after one decode']
    out += [f'{step} {rng}: {label} (missing {want!r})' for label, want in rules(step, IV[step][rng]) if want not in sql]
    if step == 'q' and 'max(Sum) - min(Sum)' in sql:
        out.append(f'q {rng}: a max-min per bucket is left')
    return out


# The previous query of each step, as it was on main, to swap in for the controls.
OLD = {
    'phases': ("SELECT metric, round(sum(dsum)/nullif(sum(dcount),0)*1000, 0) AS mean_ms, sum(dcount) AS samples FROM ( "
               "SELECT MetricName AS metric, ResourceAttributes['service.instance.id'] AS sid, "
               "ResourceAttributes['krateo.io/composition-gvr'] AS gvr, max(Sum)-min(Sum) AS dsum, max(Count)-min(Count) AS dcount "
               "FROM otel_metrics_histogram WHERE MetricName IN ('unstructured_runtime.reconcile.queue.wait.duration_seconds') "
               "AND TimeUnix > now() - INTERVAL {IV} GROUP BY metric, sid, gvr ) GROUP BY metric FORMAT JSON"),
    'fail': ("SELECT MetricName AS m, round(max(Value)-min(Value)) AS v FROM otel_metrics_sum WHERE MetricName IN "
             "('provider_runtime.reconcile.failure','provider_runtime.reconcile.success') AND TimeUnix > now() - INTERVAL {IV} "
             "GROUP BY m FORMAT JSON"),
    'q': ("SELECT t, route, round(sd / nullIf(cd, 0), 4) AS mean_s FROM ( SELECT toUnixTimestamp(toStartOfInterval(TimeUnix, "
          "INTERVAL 1 HOUR)) AS t, Attributes['http.route'] AS route, greatest(max(Sum) - min(Sum), 0) AS sd, "
          "greatest(max(Count) - min(Count), 0) AS cd FROM otel_metrics_histogram WHERE ServiceName='snowplow' AND "
          "MetricName='http.server.request.duration' AND TimeUnix > now() - INTERVAL {IV} AND Attributes['http.route'] != "
          "'/refreshes' GROUP BY t, route) WHERE cd > 0 ORDER BY t FORMAT JSON"),
}


def with_old(ra, step):
    ra = copy.deepcopy(ra)
    st = next(s for s in ra['spec']['api'] if s['name'] == step)
    head, tail = OLD[step].split('{IV}')
    keep = st['path'].split('"/?query="')[0] if '"/?query="' in st['path'] else st['path'].split('"/?query=')[0]
    st['path'] = keep + '"/?query=' + enc(head) + '" + $iv + "' + enc(tail) + '" }'
    return ra


STEPS = [('obs-reconcile-perf', 'phases'), ('obs-reconcile-metrics', 'fail'), ('sp-http-mean', 'q')]


def main():
    chart = traces.render(values={'observability': {'snowplowSli': True}})   # sp-http-mean renders only then
    failures = []
    for ra_name, step in STEPS:
        old = with_old(chart.get('RESTAction', ra_name), step)
        for rng in IV[step]:
            sql = sql_of(chart, ra_name, step, rng, ra=old)
            if 'max(' not in sql or 'StartTimeUnix' in sql:
                failures.append(f'control {step} {rng}: the old query could not be swapped in, so the control means nothing')
            elif not problems_in(sql, step, rng):
                failures.append(f'control {step} {rng}: the old query was NOT refused')
    if not failures:
        print('[PASS] controls: each step\'s previous max-min query is refused for every range')
    for ra_name, step in STEPS:
        for rng in IV[step]:
            failures += problems_in(sql_of(chart, ra_name, step, rng), step, rng)
    for f in failures:
        print(f'[FAIL] {f}')
    n = sum(len(IV[s]) for _, s in STEPS)
    print(f'{"FAILED" if failures else "ok"}: {len(STEPS)} cumulative-counter queries over {n} ranges '
          f'({len(failures)} failure(s))')
    return min(len(failures), 125)


if __name__ == '__main__':
    sys.exit(main())
