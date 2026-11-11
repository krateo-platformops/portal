#!/usr/bin/env python3
"""
test-observability-traces — the /observability Traces tab and the /observability/traces/{traceId}
page (installer#404), evaluated on span rows shaped like clickstack's otel_traces.

WHAT IT GUARDS. Three RESTActions (obs-traces, obs-trace-facets, obs-trace-detail) put request
text into ClickHouse SQL, and a dozen widgets turn their rows into a span list, filter chips, a
service diagram and a header. `helm template` evaluates none of that jq. This renders the chart,
resolves the RESTActions with test-ra-input-gates' model of snowplow's resolver (iterators,
dependsOn, step filters, continueOnError; each stage PATH rendered by snowplow's own code, see
snowplow-path.py), runs the widgets' own jq over the result, and checks what a reader would see:
  - the span list is newest first, bounded by a time window and LIMIT 200, and each filter reaches
    the SQL as exactly one predicate;
  - the request snowplow dials is encoded exactly once: decoded once it is that SQL (portal 1.8.99
    encoded its WHERE fragments twice, and ClickHouse refused every span-list query);
  - an Error span shows as a red "Error" pill, Ok as "OK", Unset as "Unset";
  - the trace page's Status column says which span failed: the span's HTTP code under any of the
    three keys producers write it (`http.response.status_code`, `http.status_code`, `http.status`),
    then "error" when StatusCode is Error — "403 · error" as a red tag on the failing span, "403" as
    plain text on spans that carried it back Unset, "—" where there is neither;
  - a row opens /observability/traces/<its full trace id>;
  - the trace page lists spans in start order with offsets from the first, and draws one node per
    service with an edge wherever a span's ParentSpanId belongs to another service;
  - a TraceId or filter carrying a quote or an SQL fragment makes NO request at all, and every
    request that is made keeps each SQL string literal to the characters its filter allows;
  - the HyperDX link exists only when `observabilityConsoleUrl` is set, and opens /trace/<id>;
  - HyperDX's own spans (`tracesHiddenService`) are left out until chosen, and the bar says so;
  - 64-bit integers read the same whether the server quotes them in FORMAT JSON or not;
  - every new object lives in the chart's namespace, which only `admins` can read;
  - "Last reconcile by composition" (obs-composition-staleness, Reconciliation tab, only with
    observability.reconcileTracing): one bounded ClickHouse aggregate joined to the LIVE
    composition list on kind|ns|name, trace-only rows dropped, compositions with no reconcile in the
    trace window first as a "none in the trace window" pill, then the oldest reconcile first; the
    header names the trace window; an unreadable or empty trace store shows one message and never
    a flag on every row; nothing overdue shows the all-clear; colours are design tokens only.

With --crds DIR every widget, as rendered and as resolved, is validated against that frontend-crds
chart; --pinned pulls the version the installer pins (lint-pinned-crds.py reads it), which is what
the cluster serves. Widget CRDs are strict, so a field the CRD lacks is refused by the apiserver
(authored) or by snowplow (resolved).

Uses the `jq` binary; JQ=gojq runs the same checks on it. Needs `go` and `git` for snowplow's path
renderer (SNOWPLOW_SRC=<checkout> skips the clone of the pinned tag).
Usage: test-observability-traces.py [--crds DIR | --pinned]. Exit code = failed checks.
"""
import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.path.join(HERE, '..', 'helm', 'observability-page')


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gates = _load('gates', 'test-ra-input-gates.py')        # snowplow's resolver, modelled
tbi = gates.tbi                                           # render helpers + CRD validation
rownav = _load('rownav', 'test-rownavigateto-f8.py')      # Table.tsx buildRowPath, ported

NS = 'krateo-observability'          # the chart's default page namespace
RELEASE_NS = 'krateo-system'
CONSOLE = 'https://hdx.example.test/'

NEW_WIDGETS = [
    ('Col', 'obs-tab-traces'), ('Paragraph', 'obs-sec-traces-desc'), ('Flex', 'obs-traces-filters'),
    ('Flex', 'obs-traces-range-group'), ('Flex', 'obs-traces-service-group'),
    ('Flex', 'obs-traces-user-group'), ('Flex', 'obs-traces-status-group'),
    ('Paragraph', 'obs-traces-service-label'), ('Paragraph', 'obs-traces-user-label'),
    ('Paragraph', 'obs-traces-status-label'),
    ('Listy', 'obs-traces-range-chips'), ('Listy', 'obs-traces-service-chips'),
    ('Listy', 'obs-traces-user-chips'), ('Listy', 'obs-traces-status-chips'), ('Listy', 'obs-traces-hidden-note'),
    ('Table', 'obs-traces'),
    ('Flex', 'page-trace-detail'), ('PageHeader', 'trace-detail-page-header'), ('Row', 'trace-detail-grid'),
    ('Flex', 'trace-detail-col-left'), ('Flex', 'trace-detail-col-right'),
    ('Card', 'card-trace-detail-spans'), ('Card', 'card-trace-detail-topology'),
    ('Table', 'trace-detail-spans'), ('FlowChart', 'trace-detail-topology'), ('Markdown', 'trace-detail-note'),
]
CONSOLE_WIDGETS = [('Flex', 'trace-detail-hyperdx'), ('Button', 'trace-detail-hyperdx-link'),
                   ('Paragraph', 'trace-detail-hyperdx-caption')]
NEW_RAS = ['obs-traces', 'obs-trace-facets', 'obs-trace-detail']


def render(console='', sets=(), values=None):
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(CHART, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        open(meta, 'w', encoding='utf-8').write(text.replace('CHART_VERSION', '0.0.0-dev'))
        cmd = ['helm', 'template', 'observability-page', staged, '--namespace', RELEASE_NS]
        if console:
            cmd += ['--set', f'observabilityConsoleUrl={console}']
        for value in sets:
            cmd += ['--set-string', value]
        if values is not None:      # a values file, so helm's --set parsing cannot alter the value
            vf = os.path.join(tmp, 'values.yaml')
            open(vf, 'w', encoding='utf-8').write(yaml.safe_dump(values))
            cmd += ['-f', vf]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode:
            raise SystemExit(f'helm template failed:\n{proc.stderr.strip()}')
        return tbi.Chart([d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)])


# ---------------------------------------------------------------------------------------------
# Fixtures: ClickHouse FORMAT JSON answers, shaped as the exporter writes otel_traces
# (krateo-platformops/otel-collector clickhouseexporter/exporter_traces.go: Timestamp = span
# start, Duration = End - Start in ns, StatusCode = pdata's "Unset" | "Ok" | "Error", ids as
# lowercase hex). 64-bit integers arrive as JSON strings.
# ---------------------------------------------------------------------------------------------

T0_MS = 1794171146004                      # 2026-11-08 20:52:26.004 UTC
TRACE_A = '4f1c9e2a7b3d4c5e8f9a0b1c2d3ea07e'     # the mockup's agentiko call
TRACE_B = '2a88d0e14b7c4f3a9e6d5c2b1a09c3f0'     # a snowplow call that failed


def ch(rows):
    return {'meta': [], 'data': rows, 'rows': len(rows), 'statistics': {'elapsed': 0.01}}


# Newest first, as `ORDER BY Timestamp DESC` returns them.
LIST_ROWS = [
    {'ts_ms': str(T0_MS), 'svc': 'agentgateway', 'span': 'POST /api/a2a/krateo-system/agentiko',
     'dur_ns': '5810000000', 'sub': 'admin', 'status': 'Unset', 'trace_id': TRACE_A},
    {'ts_ms': str(T0_MS - 92), 'svc': 'snowplow', 'span': 'GET /call restactions/compositions-list',
     'dur_ns': '212400000', 'sub': 'admin', 'status': 'Ok', 'trace_id': '9b30aa55c1d24e6f8a7b6c5d4e3f11c2'},
    {'ts_ms': str(T0_MS - 27574), 'svc': 'agentgateway', 'span': 'POST /llm/v1/chat/completions',
     'dur_ns': '2470000000', 'sub': '', 'status': 'Unset', 'trace_id': 'e7d24c1b9a8f4e3d2c1b0a9f8e7d5a19'},
    {'ts_ms': str(T0_MS - 133886), 'svc': 'snowplow', 'span': 'GET /call restactions/obs-log-stream',
     'dur_ns': '1940000000', 'sub': 'cyberjoker', 'status': 'Error', 'trace_id': TRACE_B},
    {'ts_ms': str(T0_MS - 166001), 'svc': 'agentgateway', 'span': 'POST /api/a2a/krateo-system/incident-agent',
     'dur_ns': '11230000000', 'sub': 'admin', 'status': 'Unset', 'trace_id': '71b5e2d3c4b5a69788776655443309de'},
    {'ts_ms': str(T0_MS - 170000), 'svc': 'snowplow', 'span': 'informer resync',
     'dur_ns': '840000', 'sub': '', 'status': 'Unset', 'trace_id': '0123456789abcdef0123456789abcdef'},
]

US = T0_MS * 1000                          # trace A's start, in microseconds


def span(start_us, sid, parent, svc, name, dur_ns, status='Unset', sub='', http=''):
    # `http` is the SQL's coalesce over the three HTTP status keys: '' when the span has none.
    return {'start_us': str(start_us), 'span_id': sid, 'parent_id': parent, 'svc': svc, 'span': name,
            'dur_ns': str(dur_ns), 'status': status, 'http': http, 'sub': sub}


# The mockup's trace: the gateway receives the A2A call, agentiko calls the model and k8s-agent back
# out through the gateway. In start order, as `ORDER BY Timestamp ASC` returns them.
TRACE_A_SPANS = [
    span(US,             'a000000000000001', '',                 'agentgateway', 'POST /api/a2a/krateo-system/agentiko', 5810000000, sub='admin'),
    span(US + 4000,      'a000000000000002', 'a000000000000001', 'agentiko',     'message/send',                          5790000000, sub='admin'),
    span(US + 31000,     'a000000000000003', 'a000000000000002', 'agentgateway', 'POST /llm/v1/chat/completions',         2100000000),
    span(US + 2160000,   'a000000000000004', 'a000000000000002', 'agentgateway', 'POST / (x-kagent-host k8s-agent)',      1400000000),
    span(US + 2170000,   'a000000000000005', 'a000000000000004', 'k8s-agent',    'message/send',                          1380000000),
    span(US + 3600000,   'a000000000000006', 'a000000000000005', 'agentgateway', 'POST /llm/v1/chat/completions',         2180000000),
]
# Its root's parent (the portal's browser span) was never exported, so no span has parent_id "".
TRACE_B_SPANS = [
    span(US - 133886000, 'b000000000000001', 'ffffffffffffffff', 'snowplow', 'GET /call restactions/obs-log-stream', 1940000000, 'Error', 'cyberjoker', '500'),
    span(US - 133880000, 'b000000000000002', 'b000000000000001', 'snowplow', 'clickhouse query',                    1900000000, 'Error'),
    span(US - 133000000, 'b000000000000003', 'b000000000000001', 'snowplow', 'jq filter',                           820000),
]
# A widget fetch snowplow refused, as krateo-057's ClickHouse answered the detail query for trace
# ff5a4879d0c68269b1139c9f397df304: only the browser span is Error; the gateway's two spans and
# snowplow's carry the same 403 with StatusCode Unset. The gateway writes it as `http.status`, the
# others as `http.response.status_code`; the SQL folds both into `http`. Unquoted 64-bit integers,
# as 057 sends them.
TRACE_C = 'ff5a4879d0c68269b1139c9f397df304'
TRACE_C_SPANS = [
    {'start_us': 1794325039029000, 'span_id': '6d0efbb1933ac92e', 'parent_id': '', 'svc': 'krateo-frontend',
     'span': 'krateo.widget.fetch', 'dur_ns': 115000000, 'status': 'Error', 'http': '403', 'sub': ''},
    {'start_us': 1794325039169499, 'span_id': 'c17089a8861ddd6f', 'parent_id': '6d0efbb1933ac92e',
     'svc': 'krateo-agent-gateway-platform', 'span': 'POST /content/*', 'dur_ns': 69155777, 'status': 'Unset',
     'http': '403', 'sub': ''},
    {'start_us': 1794325039169950, 'span_id': '25c7e32e998d976e', 'parent_id': 'c17089a8861ddd6f',
     'svc': 'krateo-agent-gateway-platform', 'span': 'POST snowplow.krateo-system.svc.cluster.local:8081',
     'dur_ns': 68638957, 'status': 'Unset', 'http': '403', 'sub': ''},
    {'start_us': 1794325039180670, 'span_id': 'd47b19fd2666d974', 'parent_id': '25c7e32e998d976e', 'svc': 'snowplow',
     'span': 'POST /call', 'dur_ns': 57521053, 'status': 'Unset', 'http': '403', 'sub': ''},
]

FACETS = {'services': ch([{'svc': 'agentgateway', 'n': '4120'}, {'svc': 'snowplow', 'n': '2210'},
                          {'svc': 'composition-dynamic-controller', 'n': '310'}]),
          'users': ch([{'sub': 'admin', 'n': '3900'}, {'sub': 'cyberjoker', 'n': '120'}])}


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------

def sql_of(path):
    assert path.startswith('/?query='), path
    return unquote(path[len('/?query='):])


def resolve(chart, name, extras, responses):
    return gates.resolve(chart.get('RESTAction', name), extras, responses)


def cells(row):
    return {c['valueKey']: c for c in row}


def expect(problems, label, got, want):
    if got != want:
        problems.append(f'{label}: got {got!r}, want {want!r}')


HIDE = " AND ServiceName != 'krateo-observability'"      # the default tracesHiddenService
LIST_SQL = ("SELECT toUnixTimestamp64Milli(Timestamp) AS ts_ms, ServiceName AS svc, SpanName AS span, "
            "Duration AS dur_ns, SpanAttributes['jwt.sub'] AS sub, StatusCode AS status, TraceId AS trace_id "
            "FROM otel_traces WHERE Timestamp > now() - INTERVAL {window}{cond} "
            "ORDER BY Timestamp DESC LIMIT 200 FORMAT JSON")
DETAIL_SQL = ("SELECT toUnixTimestamp64Micro(Timestamp) AS start_us, SpanId AS span_id, ParentSpanId AS parent_id, "
              "ServiceName AS svc, SpanName AS span, Duration AS dur_ns, StatusCode AS status, "
              "coalesce(nullIf(SpanAttributes['http.response.status_code'], ''), "
              "nullIf(SpanAttributes['http.status_code'], ''), SpanAttributes['http.status']) AS http, "
              "SpanAttributes['jwt.sub'] AS sub FROM otel_traces WHERE TraceId = '{tid}' "
              "AND Timestamp > now() - INTERVAL 7 DAY ORDER BY Timestamp ASC, SpanId ASC LIMIT 1000 FORMAT JSON")


def literals_are_closed(sql):
    """Every single quote in the SQL opens or closes a literal, and each literal holds only the
    characters some filter allows. A value that escaped its literal shows up as an odd quote count,
    or as SQL inside one."""
    if sql.count("'") % 2:
        return False
    return all(re.fullmatch(r"[A-Za-z0-9._@:+=/-]*", lit) for lit in re.findall(r"'([^']*)'", sql))


# ---------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------

def check_span_list_is_newest_first_and_bounded(c):
    p = []
    reqs, _, out = resolve(c, 'obs-traces', {}, {'spans': ch(LIST_ROWS)})
    expect(p, 'no filters: one request', len(reqs), 1)
    expect(p, 'no filters: the SQL', sql_of(reqs[0][1]), LIST_SQL.format(window='24 HOUR', cond=HIDE))
    for rng, window in [('1h', '1 HOUR'), ('6h', '6 HOUR'), ('24h', '24 HOUR'), ('7d', '7 DAY'), ('30d', '24 HOUR')]:
        r, _, _ = resolve(c, 'obs-traces', {'range': rng}, {'spans': ch([])})
        expect(p, f'range={rng}: the window', sql_of(r[0][1]), LIST_SQL.format(window=window, cond=HIDE))
    rows = tbi.resolved_widget(c, 'Table', 'obs-traces', out, {}, 'obs-traces (6 spans)')['spec']['widgetData']['dataSource']
    times = [cells(r)['time']['stringValue'] for r in rows]
    expect(p, 'rows keep the newest-first order', times, sorted(times, reverse=True))
    first = cells(rows[0])
    expect(p, 'time is UTC with milliseconds', first['time']['stringValue'], '2026-11-08 20:52:26.004')
    expect(p, 'service', first['service']['stringValue'], 'agentgateway')
    expect(p, 'span name', first['span']['stringValue'], 'POST /api/a2a/krateo-system/agentiko')
    expect(p, 'user is jwt.sub', first['user']['stringValue'], 'admin')
    expect(p, 'trace column is shortened', first['trace']['stringValue'], '4f1c…a07e')
    expect(p, 'a span with no jwt.sub', cells(rows[2])['user']['stringValue'], '—')
    expect(p, 'durations', [cells(r)['duration']['stringValue'] for r in rows],
           ['5.81 s', '212 ms', '2.47 s', '1.94 s', '11.2 s', '840 µs'])
    table = c.get('Table', 'obs-traces')['spec']['widgetData']
    expect(p, 'row click target', rownav.build_row_path(table['rowNavigateTo'], rows[0]), f'/observability/traces/{TRACE_A}')
    expect(p, 'columns', [col['title'] for col in table['columns']],
           ['Time (UTC)', 'Service', 'Span', 'Duration', 'User (AI calls)', 'Status', 'Trace'])
    expect(p, 'pages of 25 over the bounded set', table.get('pagination'), {'pageSize': 25, 'hideOnSinglePage': True})
    return p


def check_error_status_shows_as_an_error(c):
    p = []
    _, _, out = resolve(c, 'obs-traces', {}, {'spans': ch(LIST_ROWS)})
    rows = tbi.widget(c, 'Table', 'obs-traces', 'dataSource', out, {})
    pills = {cells(r)['traceId']['stringValue']: (cells(r)['status']['kind'], cells(r)['status']['stringValue'],
                                                  cells(r)['status']['color']) for r in rows}
    expect(p, 'Error span', pills[TRACE_B], ('tag', 'Error', 'red'))
    expect(p, 'Ok span', pills['9b30aa55c1d24e6f8a7b6c5d4e3f11c2'], ('tag', 'OK', 'green'))
    expect(p, 'Unset span is not dressed as OK', pills[TRACE_A], ('tag', 'Unset', 'gray'))
    _, _, det = resolve(c, 'obs-trace-detail', {'traceId': TRACE_B}, {'spans': ch(TRACE_B_SPANS)})
    tags = tbi.widget(c, 'PageHeader', 'trace-detail-page-header', 'tags', det, {'traceId': TRACE_B})
    expect(p, 'a trace with an Error span', tags, [{'label': 'Error', 'color': 'red'}])
    nodes = tbi.widget(c, 'FlowChart', 'trace-detail-topology', 'data', det, {'traceId': TRACE_B})
    expect(p, 'the service node counts its error spans', [n['detail'] for n in nodes], ['3 spans · 2 errors'])
    _, _, det = resolve(c, 'obs-trace-detail', {'traceId': TRACE_A}, {'spans': ch(TRACE_A_SPANS)})
    tags = tbi.widget(c, 'PageHeader', 'trace-detail-page-header', 'tags', det, {'traceId': TRACE_A})
    expect(p, 'a trace with no Error span', tags, [{'label': 'OK', 'color': 'green'}])
    return p


def check_span_status_says_which_span_failed(c):
    """The trace page's Status column: the HTTP code where the span has one, then "error" when its
    StatusCode is Error. Only the Error span is a red tag, so the failing span is the one that
    stands out; a span that only carried the 403 back reads "403", and one with neither "—"."""
    p = []

    def status(tid, spans):
        _, _, out = resolve(c, 'obs-trace-detail', {'traceId': tid}, {'spans': ch(spans)})
        rows = tbi.resolved_widget(c, 'Table', 'trace-detail-spans', out, {'traceId': tid},
                                   f'trace-detail-spans (status, {tid[:4]})')['spec']['widgetData']['dataSource']
        return [(cells(r)['span']['stringValue'],) + tuple(cells(r)['status'].get(k) for k in ('kind', 'stringValue', 'color'))
                for r in rows]

    expect(p, 'a widget fetch snowplow refused (057 trace ff5a…f304)', status(TRACE_C, TRACE_C_SPANS), [
        ('krateo.widget.fetch', 'tag', '403 · error', 'red'),
        ('POST /content/*', 'jsonSchemaType', '403', None),
        ('POST snowplow.krateo-system.svc.cluster.local:8081', 'jsonSchemaType', '403', None),
        ('POST /call', 'jsonSchemaType', '403', None)])
    expect(p, 'an Error span with no HTTP code still says error', status(TRACE_B, TRACE_B_SPANS), [
        ('GET /call restactions/obs-log-stream', 'tag', '500 · error', 'red'),
        ('clickhouse query', 'tag', 'error', 'red'),
        ('jq filter', 'jsonSchemaType', '—', None)])
    expect(p, 'Ok is not dressed as an error', status(TRACE_A, [span(US, 'a000000000000001', '', 'snowplow', 'GET /call', 1000, 'Ok', http='200')]),
           [('GET /call', 'jsonSchemaType', '200', None)])
    expect(p, 'a row from a ClickHouse without the http column', status(TRACE_A, [{k: v for k, v in TRACE_A_SPANS[0].items() if k != 'http'}]),
           [('POST /api/a2a/krateo-system/agentiko', 'jsonSchemaType', '—', None)])
    cols = [col['title'] for col in c.get('Table', 'trace-detail-spans')['spec']['widgetData']['columns']]
    expect(p, 'columns', cols, ['Service', 'Span', 'Start', 'Duration', 'Status'])
    reqs, _, _ = resolve(c, 'obs-trace-detail', {'traceId': TRACE_C}, {'spans': ch(TRACE_C_SPANS)})
    expect(p, 'the SQL reads all three HTTP status keys', [sql_of(r[1]) for r in reqs], [DETAIL_SQL.format(tid=TRACE_C)])
    return p


def check_filters_reach_the_sql_as_one_predicate_each(c):
    p = []
    cases = [
        ({'traceService': 'composition-dynamic-controller'}, " AND ServiceName = 'composition-dynamic-controller'"),
        ({'traceUser': 'cyberjoker'}, HIDE + " AND SpanAttributes['jwt.sub'] = 'cyberjoker'"),
        ({'traceUser': 'system:serviceaccount:krateo-system:snowplow'},
         HIDE + " AND SpanAttributes['jwt.sub'] = 'system:serviceaccount:krateo-system:snowplow'"),
        ({'traceUser': 'CgVhZG1pbhIF+bG9j/YWw='}, HIDE + " AND SpanAttributes['jwt.sub'] = 'CgVhZG1pbhIF+bG9j/YWw='"),
        ({'traceUser': 'diego.braga@kiratech.it'}, HIDE + " AND SpanAttributes['jwt.sub'] = 'diego.braga@kiratech.it'"),
        ({'traceStatus': 'Error'}, HIDE + " AND StatusCode = 'Error'"),
        ({'traceService': 'snowplow', 'traceUser': 'admin', 'traceStatus': 'Ok', 'range': '1h'},
         " AND ServiceName = 'snowplow' AND SpanAttributes['jwt.sub'] = 'admin' AND StatusCode = 'Ok'"),
    ]
    for extras, cond in cases:
        reqs, _, out = resolve(c, 'obs-traces', extras, {'spans': ch([])})
        window = '1 HOUR' if extras.get('range') == '1h' else '24 HOUR'
        expect(p, f'{extras}', [sql_of(r[1]) for r in reqs], [LIST_SQL.format(window=window, cond=cond)])
        expect(p, f'{extras}: nothing rejected', out['rejected'], [])
        if reqs and '+' in reqs[0][1]:
            p.append(f'{extras}: a literal + reached the URL, where it reads as a space')
    return p


def check_requests_are_encoded_exactly_once(c):
    """What ClickHouse decodes is the SQL, once. snowplow escapes every value spliced into a path's
    query (snowplow#1123), `%` included, so a fragment that arrives already %-encoded is sent as
    `%2520`: portal 1.8.99 built its WHERE fragments in the iterator and ClickHouse received
    `INTERVAL 24%20HOUR%20AND%20ServiceName…`. The path here is snowplow's own rendering
    (snowplow-path.py), so this reads the request it actually dials: decoded once, it is exactly
    the intended SQL; decoded again, nothing changes (no escape survived the first decode); and it
    carries no `%25`, no space and no bare `+`, which ClickHouse would read as a space."""
    p = []
    user = 'CgVhZG1pbhIF+bG9j/YWw='          # an OIDC subject: base64 `+`, `/`, `=`
    cases = [
        ('obs-traces', {}, LIST_SQL.format(window='24 HOUR', cond=HIDE)),
        ('obs-traces', {'range': '7d'}, LIST_SQL.format(window='7 DAY', cond=HIDE)),
        ('obs-traces', {'traceService': 'snowplow'}, LIST_SQL.format(window='24 HOUR', cond=" AND ServiceName = 'snowplow'")),
        ('obs-traces', {'traceUser': user},
         LIST_SQL.format(window='24 HOUR', cond=HIDE + f" AND SpanAttributes['jwt.sub'] = '{user}'")),
        ('obs-traces', {'traceUser': 'x+y/z=@:.-_'},
         LIST_SQL.format(window='24 HOUR', cond=HIDE + " AND SpanAttributes['jwt.sub'] = 'x+y/z=@:.-_'")),
        ('obs-traces', {'traceStatus': 'Error'}, LIST_SQL.format(window='24 HOUR', cond=HIDE + " AND StatusCode = 'Error'")),
        ('obs-traces', {'traceService': 'snowplow', 'traceUser': user, 'traceStatus': 'Ok', 'range': '1h'},
         LIST_SQL.format(window='1 HOUR', cond=" AND ServiceName = 'snowplow'"
                                               f" AND SpanAttributes['jwt.sub'] = '{user}' AND StatusCode = 'Ok'")),
        ('obs-trace-detail', {'traceId': TRACE_A}, DETAIL_SQL.format(tid=TRACE_A)),
    ]
    for ra, extras, want in cases:
        reqs, _, _ = resolve(c, ra, extras, {'spans': ch([])})
        if len(reqs) != 1:
            p.append(f'{ra} {extras}: {len(reqs)} requests, want 1')
            continue
        raw = reqs[0][1]
        once = sql_of(raw)
        expect(p, f'{ra} {extras}: decoded once', once, want)
        if unquote(once) != once:
            p.append(f'{ra} {extras}: an escape survived one decode (encoded twice): {once!r}')
        for bad, why in [('%25', 'an escaped %'), (' ', 'a space'), ('+', 'a bare +')]:
            if bad in raw:
                p.append(f'{ra} {extras}: the request carries {why}: {raw!r}')
    for _, path, _ in resolve(c, 'obs-trace-facets', {'range': '6h'}, FACETS)[0]:
        if unquote(sql_of(path)) != sql_of(path) or '%25' in path:
            p.append(f'obs-trace-facets: encoded twice: {sql_of(path)!r}')
    return p


INJECTIONS = ["x' OR '1'='1", "a'b", "snow\\plow", "snow plow", "admin'--", "admin%27", "1; DROP TABLE otel_traces",
              "a'); SELECT * FROM system.users; --", "Error' OR StatusCode != '", "’quote", ""]


def check_injection_is_refused(c):
    p = []
    for bad in INJECTIONS[:-1]:
        for key, name in [('traceService', 'service'), ('traceUser', 'user'), ('traceStatus', 'status')]:
            reqs, _, out = resolve(c, 'obs-traces', {key: bad}, {'spans': ch(LIST_ROWS)})
            expect(p, f'{key}={bad!r}: requests', reqs, [])
            expect(p, f'{key}={bad!r}: rows', out['rows'], [])
            expect(p, f'{key}={bad!r}: rejected', out['rejected'], [name])
    for bad in INJECTIONS + ['4f1c9e2a7b3d4c5e8f9a0b1c2d3ea07', TRACE_A + '0', TRACE_A[:31] + 'g',
                             TRACE_A[:30] + "'#", f"{TRACE_A}' OR '1'='1"]:
        reqs, _, out = resolve(c, 'obs-trace-detail', {'traceId': bad}, {'spans': ch(TRACE_A_SPANS)})
        expect(p, f'traceId={bad!r}: requests', reqs, [])
        expect(p, f'traceId={bad!r}: valid', out['valid'], False)
        expect(p, f'traceId={bad!r}: echoed', out['traceId'], '')
        hdr = tbi.resolved_widget(c, 'PageHeader', 'trace-detail-page-header', out, {'traceId': bad},
                                  f'trace-detail-page-header (traceId={bad!r})')['spec']['widgetData']
        expect(p, f'traceId={bad!r}: header pill', hdr['tags'], [{'label': 'Not a trace id', 'color': 'gray'}])
    reqs, _, out = resolve(c, 'obs-trace-detail', {'traceId': TRACE_A.upper()}, {'spans': ch(TRACE_A_SPANS)})
    expect(p, 'an upper-case id is the same trace', [sql_of(r[1]) for r in reqs], [DETAIL_SQL.format(tid=TRACE_A)])
    # Whatever IS requested keeps every literal closed, across every accepted value above.
    for extras in [{'traceService': 'a.b-c_d'}, {'traceUser': 'x+y/z=@:.-_'}, {'traceStatus': 'Unset'}, {}]:
        for _, path, _ in resolve(c, 'obs-traces', extras, {'spans': ch([])})[0]:
            if not literals_are_closed(sql_of(path)):
                p.append(f'{extras}: a literal is not closed in {sql_of(path)!r}')
    for _, path, _ in resolve(c, 'obs-trace-detail', {'traceId': TRACE_A}, {'spans': ch([])})[0]:
        if not literals_are_closed(sql_of(path)):
            p.append(f'detail: a literal is not closed in {sql_of(path)!r}')
    # The facets read only ?range, through a four-way match: request text never reaches their SQL.
    for rng in ["24h' OR 1=1", '7d', '']:
        for _, path, _ in resolve(c, 'obs-trace-facets', {'range': rng, 'traceService': "x'"}, FACETS)[0]:
            if "OR 1=1" in sql_of(path) or "x'" in sql_of(path) or not literals_are_closed(sql_of(path)):
                p.append(f'facets range={rng!r}: request text reached the SQL: {sql_of(path)!r}')
    return p


def check_trace_page_lists_spans_in_start_order(c):
    p = []
    reqs, _, out = resolve(c, 'obs-trace-detail', {'traceId': TRACE_A}, {'spans': ch(TRACE_A_SPANS)})
    expect(p, 'the SQL', [sql_of(r[1]) for r in reqs], [DETAIL_SQL.format(tid=TRACE_A)])
    ex = {'traceId': TRACE_A}
    rows = tbi.resolved_widget(c, 'Table', 'trace-detail-spans', out, ex, 'trace-detail-spans (trace A)')['spec']['widgetData']['dataSource']
    got = [(cells(r)['service']['stringValue'], cells(r)['span']['stringValue'], cells(r)['start']['stringValue'],
            cells(r)['duration']['stringValue']) for r in rows]
    expect(p, 'span list', got, [
        ('agentgateway', 'POST /api/a2a/krateo-system/agentiko', '0 ms', '5.81 s'),
        ('agentiko', 'message/send', '4 ms', '5.79 s'),
        ('agentgateway', 'POST /llm/v1/chat/completions', '31 ms', '2.10 s'),
        ('agentgateway', 'POST / (x-kagent-host k8s-agent)', '2.16 s', '1.40 s'),
        ('k8s-agent', 'message/send', '2.17 s', '1.38 s'),
        ('agentgateway', 'POST /llm/v1/chat/completions', '3.60 s', '2.18 s')])
    hdr = tbi.resolved_widget(c, 'PageHeader', 'trace-detail-page-header', out, ex, 'trace-detail-page-header (trace A)')['spec']['widgetData']
    expect(p, 'header title', hdr['title'], 'Trace 4f1c…a07e')
    expect(p, 'header subtitle', hdr['subtitle'], 'admin · 2026-11-08 20:52:26.004 UTC · 5.81 s · 6 spans')
    _, _, out_b = resolve(c, 'obs-trace-detail', {'traceId': TRACE_B}, {'spans': ch(TRACE_B_SPANS)})
    hdr = tbi.widget(c, 'PageHeader', 'trace-detail-page-header', 'subtitle', out_b, {'traceId': TRACE_B})
    expect(p, 'root whose parent was not exported', hdr, 'cyberjoker · 2026-11-08 20:50:12.118 UTC · 1.94 s · 3 spans')
    _, _, none = resolve(c, 'obs-trace-detail', {'traceId': TRACE_A}, {'spans': ch([])})
    hdr = tbi.resolved_widget(c, 'PageHeader', 'trace-detail-page-header', none, ex, 'trace-detail-page-header (not found)')['spec']['widgetData']
    expect(p, 'not found', (hdr['tags'], hdr['subtitle']),
           ([{'label': 'Not found', 'color': 'gray'}], 'No span with this trace id in the last 7 days.'))
    _, _, down = resolve(c, 'obs-trace-detail', {'traceId': TRACE_A}, {})
    hdr = tbi.widget(c, 'PageHeader', 'trace-detail-page-header', 'tags', down, ex)
    expect(p, 'ClickHouse down is not "not found"', hdr, [{'label': 'Unreadable', 'color': 'gray'}])
    for label, resolved in [('not found', none), ('down', down)]:
        tbi.resolved_widget(c, 'Table', 'trace-detail-spans', resolved, ex, f'trace-detail-spans ({label})')
        tbi.resolved_widget(c, 'FlowChart', 'trace-detail-topology', resolved, ex, f'trace-detail-topology ({label})')
    return p


def check_topology_follows_parent_span_ids(c):
    p = []
    ex = {'traceId': TRACE_A}
    _, _, out = resolve(c, 'obs-trace-detail', ex, {'spans': ch(TRACE_A_SPANS)})
    nodes = tbi.resolved_widget(c, 'FlowChart', 'trace-detail-topology', out, ex, 'trace-detail-topology (trace A)')['spec']['widgetData']['data']
    expect(p, 'one node per service, in order of first span', [n['name'] for n in nodes], ['agentgateway', 'agentiko', 'k8s-agent'])
    expect(p, 'node lines', [n['detail'] for n in nodes], ['4 spans', '1 span', '1 span'])
    edges = sorted((r['uid'].split(':', 1)[1], n['name']) for n in nodes for r in n['parentRefs'])
    expect(p, 'edges, parent service → child service', edges, sorted([
        ('agentgateway', 'agentiko'),       # the A2A call
        ('agentiko', 'agentgateway'),       # agentiko → gateway (model, k8s-agent)
        ('agentgateway', 'k8s-agent'),      # the gateway's hop to k8s-agent
        ('k8s-agent', 'agentgateway')]))    # k8s-agent → gateway (model)
    uids = {n['uid'] for n in nodes}
    for n in nodes:
        for r in n['parentRefs']:
            if r['uid'] not in uids:
                p.append(f'{n["name"]}: edge from a node that is not drawn ({r["uid"]})')
            if r['uid'] == n['uid']:
                p.append(f'{n["name"]}: a self-edge (a call inside one service)')
    _, _, out_b = resolve(c, 'obs-trace-detail', {'traceId': TRACE_B}, {'spans': ch(TRACE_B_SPANS)})
    nodes = tbi.widget(c, 'FlowChart', 'trace-detail-topology', 'data', out_b, {'traceId': TRACE_B})
    expect(p, 'a parent outside the trace draws no edge', [(n['name'], n['parentRefs']) for n in nodes], [('snowplow', [])])
    note = c.get('Markdown', 'trace-detail-note')['spec']['widgetData']['markdown']
    expect(p, 'the note, with no HyperDX link configured', note,
           'This diagram shows which services the request passed through, not how long each took. '
           'For timing, read the span list.')
    return p


def check_filter_chips_keep_the_tab_and_the_other_filters(c):
    p = []
    ex = {'range': '6h', 'traceUser': 'admin', 'projects': 'team-a'}
    _, _, facets = resolve(c, 'obs-trace-facets', ex, FACETS)
    chips = tbi.resolved_widget(c, 'Listy', 'obs-traces-service-chips', facets, ex, 'service chips')['spec']['widgetData']['dataSource']
    expect(p, 'service chips', [(x['label'], x['active'], x.get('count')) for x in chips],
           [('All', True, None), ('agentgateway', False, 4120), ('snowplow', False, 2210),
            ('composition-dynamic-controller', False, 310)])
    expect(p, 'a service chip route', chips[2]['route'],
           '/observability?range=6h&traceService=snowplow&traceUser=admin&projects=team-a&tab=Traces')
    expect(p, '"All" clears only its own filter', chips[0]['route'],
           '/observability?range=6h&traceUser=admin&projects=team-a&tab=Traces')
    users = tbi.resolved_widget(c, 'Listy', 'obs-traces-user-chips', facets, ex, 'user chips')['spec']['widgetData']['dataSource']
    expect(p, 'user chips', [(x['label'], x['active']) for x in users], [('All', False), ('admin', True), ('cyberjoker', False)])
    expect(p, 'a user value with + / = is @uri-encoded in the route',
           tbi.widget(c, 'Listy', 'obs-traces-user-chips', 'dataSource', facets, {'traceUser': 'a+b/c='})[-1]['route'],
           '/observability?range=24h&traceUser=a%2Bb%2Fc%3D&tab=Traces')
    rare = tbi.widget(c, 'Listy', 'obs-traces-service-chips', 'dataSource', facets, {'traceService': 'krateo-bff'})
    expect(p, 'an active value outside the top 12 keeps its chip', (rare[-1]['label'], rare[-1]['active']), ('krateo-bff', True))
    bad = tbi.widget(c, 'Listy', 'obs-traces-service-chips', 'dataSource', facets, {'traceService': "x' OR 1=1"})
    expect(p, 'a refused value gets no chip', [x['label'] for x in bad], ['All', 'agentgateway', 'snowplow', 'composition-dynamic-controller'])
    rng = tbi.resolved_widget(c, 'Listy', 'obs-traces-range-chips', {}, ex, 'range chips')['spec']['widgetData']['dataSource']
    expect(p, 'range chips', [(x['label'], x['active']) for x in rng], [('1h', False), ('6h', True), ('24h', False), ('7d', False)])
    expect(p, 'a range chip route', rng[3]['route'], '/observability?range=7d&traceUser=admin&projects=team-a&tab=Traces')
    st = tbi.resolved_widget(c, 'Listy', 'obs-traces-status-chips', {}, {'traceStatus': 'Error'}, 'status chips')['spec']['widgetData']['dataSource']
    expect(p, 'status chips', [(x['label'], x['active']) for x in st], [('All', False), ('Error', True), ('OK', False), ('Unset', False)])
    expect(p, 'a status chip route', st[2]['route'], '/observability?range=24h&traceStatus=Ok&tab=Traces')
    for kind, name in [('Listy', 'obs-traces-range-chips'), ('Listy', 'obs-traces-status-chips')]:
        tbi.resolved_widget(c, kind, name, {}, {}, f'{name} (no extras)')
    _, _, empty = resolve(c, 'obs-trace-facets', {}, {})
    tbi.resolved_widget(c, 'Listy', 'obs-traces-service-chips', empty, {}, 'service chips (ClickHouse down)')
    _, _, down = resolve(c, 'obs-traces', {}, {})
    tbi.resolved_widget(c, 'Table', 'obs-traces', down, {}, 'obs-traces (ClickHouse down)')
    return p


def check_hyperdx_link_only_when_configured(c, with_console):
    p = []
    for kind, name in CONSOLE_WIDGETS:
        try:
            c.get(kind, name)
            p.append(f'{kind} {name} renders with no observabilityConsoleUrl')
        except LookupError:
            pass
    right = c.get('Flex', 'trace-detail-col-right')['spec']
    expect(p, 'no console: right column', [i['resourceRefId'] for i in right['widgetData']['items']], ['card-trace-detail-topology'])
    right = with_console.get('Flex', 'trace-detail-col-right')['spec']
    expect(p, 'console: right column', [i['resourceRefId'] for i in right['widgetData']['items']],
           ['card-trace-detail-topology', 'trace-detail-hyperdx'])
    btn = tbi.resolved_widget(with_console, 'Button', 'trace-detail-hyperdx-link', {}, {'traceId': TRACE_A}, 'hyperdx link')['spec']['widgetData']
    expect(p, 'the deep link', btn['actions']['navigate'][0]['path'], f'https://hdx.example.test/trace/{TRACE_A}')
    expect(p, 'it is the click action', btn['clickActionId'], btn['actions']['navigate'][0]['id'])
    btn = tbi.widget(with_console, 'Button', 'trace-detail-hyperdx-link', 'actions', {}, {'traceId': "x'/../"})
    expect(p, 'a malformed id links nowhere', btn['navigate'][0]['path'], '#')
    note = with_console.get('Markdown', 'trace-detail-note')['spec']['widgetData']['markdown']
    expect(p, 'the note, with the link', note,
           'This diagram shows which services the request passed through, not how long each took. '
           'For timing, read the span list or open the trace in HyperDX.')
    return p


def check_tab_and_route_are_wired(c):
    p = []
    tabs = c.get('Tabs', 'obs-main')['spec']
    items = {i['label']: i['resourceRefId'] for i in tabs['widgetData']['items']}
    expect(p, 'the Traces tab', items.get('Traces'), 'obs-tab-traces')
    expect(p, 'its ref', [r for r in tabs['resourcesRefs']['items'] if r['id'] == 'obs-tab-traces'][0]['resource'], 'cols')
    root = c.get('Flex', 'page-trace-detail')
    ann = root['metadata']['annotations']
    expect(p, 'nav-path', ann.get('krateo.io/nav-path'), '/observability/traces/{traceId}')
    if 'krateo.io/nav-label' in ann:
        p.append('page-trace-detail has a nav-label: a sub-route must not earn a sidebar entry')
    # Every widget the route reaches declares the route param (snowplow's F6 put-guard).
    by_name = {d['metadata']['name']: d for d in c.docs if d.get('apiVersion', '').startswith('widgets.')}
    seen, todo = set(), ['page-trace-detail']
    while todo:
        name = todo.pop()
        if name in seen or name not in by_name:
            continue
        seen.add(name)
        doc = by_name[name]
        if 'traceId' not in (doc['spec'].get('keyExtras') or []):
            p.append(f'{doc["kind"]} {name} is on /observability/traces/{{traceId}} without keyExtras traceId')
        todo += [r['name'] for r in (doc['spec'].get('resourcesRefs') or {}).get('items') or []]
    expect(p, 'widgets the trace route reaches', len(seen), 10)
    for kind, name in NEW_WIDGETS:
        if name.startswith(('obs-traces-', 'obs-tab-traces', 'obs-sec-traces')):
            missing = {'range', 'tab', 'traceService', 'traceUser', 'traceStatus', 'projects'} - set(c.get(kind, name)['spec'].get('keyExtras') or [])
            if missing:
                p.append(f'{kind} {name}: keyExtras lacks {sorted(missing)}')
    return p


def check_hyperdx_internals_hidden_unless_chosen(c):
    p = []
    _, _, out = resolve(c, 'obs-traces', {}, {'spans': ch(LIST_ROWS)})
    expect(p, 'no Service filter: hidden names it', out['hidden'], 'krateo-observability')
    for svc in ('krateo-observability', 'snowplow'):
        reqs, _, out = resolve(c, 'obs-traces', {'traceService': svc}, {'spans': ch([])})
        expect(p, f'traceService={svc}: exactly that service, no exclusion', [sql_of(r[1]) for r in reqs],
               [LIST_SQL.format(window='24 HOUR', cond=f" AND ServiceName = '{svc}'")])
        expect(p, f'traceService={svc}: nothing hidden', out['hidden'], '')
    note = tbi.resolved_widget(c, 'Listy', 'obs-traces-hidden-note', {}, {'range': '1h'}, 'hidden note (default)')
    expect(p, 'the note', note['spec']['widgetData']['dataSource'],
           [{'label': 'HyperDX internals hidden',
             'route': '/observability?range=1h&traceService=krateo-observability&tab=Traces'}])
    expect(p, 'the note renders nothing when empty', note['spec']['widgetData'].get('hideWhenEmpty'), True)
    gone = tbi.resolved_widget(c, 'Listy', 'obs-traces-hidden-note', {}, {'traceService': 'snowplow'}, 'hidden note (service chosen)')
    expect(p, 'a chosen service: no note', gone['spec']['widgetData']['dataSource'], [])
    expect(p, 'the note is in the filter bar', 'obs-traces-hidden-note' in
           [i['resourceRefId'] for i in c.get('Flex', 'obs-traces-filters')['spec']['widgetData']['items']], True)
    _, _, facets = resolve(c, 'obs-trace-facets', {}, {'services': ch([{'svc': 'krateo-observability', 'n': '4838'},
                                                                          {'svc': 'agentgateway', 'n': '120'}]), 'users': ch([])})
    chips = tbi.widget(c, 'Listy', 'obs-traces-service-chips', 'dataSource', facets, {})
    expect(p, 'the service chips still offer it', [x['label'] for x in chips], ['All', 'krateo-observability', 'agentgateway'])
    for _, path, _ in resolve(c, 'obs-trace-facets', {}, FACETS)[0]:
        if 'krateo-observability' in sql_of(path):
            p.append(f'the facets query excludes it: {sql_of(path)!r}')
    # The value is the chart's: another service, or none.
    other = render(sets=['tracesHiddenService=snowplow'])
    reqs, _, _ = resolve(other, 'obs-traces', {}, {'spans': ch([])})
    expect(p, 'tracesHiddenService=snowplow', [sql_of(r[1]) for r in reqs],
           [LIST_SQL.format(window='24 HOUR', cond=" AND ServiceName != 'snowplow'")])
    expect(p, 'tracesHiddenService=snowplow: its note', tbi.widget(other, 'Listy', 'obs-traces-hidden-note', 'dataSource', {}, {})[0]['label'],
           'snowplow spans hidden')
    off = render(sets=['tracesHiddenService='])
    reqs, _, out = resolve(off, 'obs-traces', {}, {'spans': ch([])})
    expect(p, 'tracesHiddenService empty: nothing excluded', [sql_of(r[1]) for r in reqs], [LIST_SQL.format(window='24 HOUR', cond='')])
    expect(p, 'tracesHiddenService empty: no note', tbi.widget(off, 'Listy', 'obs-traces-hidden-note', 'dataSource', {}, {}), [])
    # It lands in an SQL literal, so a value outside the service class must not render at all.
    for bad in ["x' OR '1'='1", 'krateo observability', "a\\b"]:
        try:
            render(values={'tracesHiddenService': bad})
            p.append(f'tracesHiddenService={bad!r} rendered')
        except SystemExit as exc:
            if 'tracesHiddenService' not in str(exc):
                p.append(f'tracesHiddenService={bad!r} failed for another reason: {str(exc)[-200:]}')
    return p


def check_user_is_labelled_ai_calls(c):
    p = []
    cols = {col['valueKey']: col['title'] for col in c.get('Table', 'obs-traces')['spec']['widgetData']['columns']}
    expect(p, 'the column', cols['user'], 'User (AI calls)')
    expect(p, 'the filter label', c.get('Paragraph', 'obs-traces-user-label')['spec']['widgetData']['text'], 'User (AI calls)')
    _, _, out = resolve(c, 'obs-traces', {}, {'spans': ch(LIST_ROWS)})
    users = [cells(r)['user']['stringValue'] for r in tbi.widget(c, 'Table', 'obs-traces', 'dataSource', out, {})]
    expect(p, 'rows without jwt.sub show a dash', users, ['admin', 'admin', '—', 'cyberjoker', 'admin', '—'])
    return p


def unquoted(rows, keys):
    """The same rows as a server with output_format_json_quote_64bit_integers=0 sends them (057)."""
    return [{k: (int(v) if k in keys else v) for k, v in r.items()} for r in rows]


def check_64bit_integers_quoted_or_not(c):
    """057's ClickHouse sends Int64/UInt64 unquoted; the server default quotes them. Same view either way."""
    p = []
    seen = []
    for label, rows, spans_a, spans_b, facets in [
            ('quoted', LIST_ROWS, TRACE_A_SPANS, TRACE_B_SPANS, FACETS),
            ('unquoted', unquoted(LIST_ROWS, {'ts_ms', 'dur_ns'}), unquoted(TRACE_A_SPANS, {'start_us', 'dur_ns'}),
             unquoted(TRACE_B_SPANS, {'start_us', 'dur_ns'}),
             {k: ch(unquoted(v['data'], {'n'})) for k, v in FACETS.items()})]:
        if label == 'unquoted' and not isinstance(rows[0]['ts_ms'], int):
            p.append('the unquoted fixture is still quoted')
        view = {}
        _, _, out = resolve(c, 'obs-traces', {}, {'spans': ch(rows)})
        view['list'] = tbi.resolved_widget(c, 'Table', 'obs-traces', out, {}, f'obs-traces ({label} ints)')['spec']['widgetData']['dataSource']
        for name, tid, spans in (('a', TRACE_A, spans_a), ('b', TRACE_B, spans_b)):
            _, _, det = resolve(c, 'obs-trace-detail', {'traceId': tid}, {'spans': ch(spans)})
            ex = {'traceId': tid}
            view[f'spans-{name}'] = tbi.widget(c, 'Table', 'trace-detail-spans', 'dataSource', det, ex)
            view[f'graph-{name}'] = tbi.widget(c, 'FlowChart', 'trace-detail-topology', 'data', det, ex)
            view[f'subtitle-{name}'] = tbi.widget(c, 'PageHeader', 'trace-detail-page-header', 'subtitle', det, ex)
        _, _, fac = resolve(c, 'obs-trace-facets', {}, facets)
        view['service-chips'] = tbi.widget(c, 'Listy', 'obs-traces-service-chips', 'dataSource', fac, {})
        seen.append(view)
    for key in seen[0]:
        expect(p, f'{key}: unquoted == quoted', seen[1][key], seen[0][key])
    expect(p, 'quoted: the first row still reads right', cells(seen[0]['list'][0])['duration']['stringValue'], '5.81 s')
    return p


def check_admins_only(c, with_console):
    p = []
    for chart in (c, with_console):
        for kind, name in NEW_WIDGETS + [('RESTAction', n) for n in NEW_RAS]:
            expect(p, f'{kind} {name} namespace', chart.get(kind, name)['metadata']['namespace'], NS)
    for kind, name in CONSOLE_WIDGETS:
        expect(p, f'{kind} {name} namespace', with_console.get(kind, name)['metadata']['namespace'], NS)
    for name in NEW_RAS:
        for step in c.get('RESTAction', name)['spec']['api']:
            expect(p, f'{name}/{step["name"]} endpoint', step.get('endpointRef'),
                   {'name': 'clickhouse-endpoint', 'namespace': RELEASE_NS})
    rbac = [d for d in c.docs if d.get('apiVersion', '').startswith('rbac.authorization.k8s.io/')]
    cluster = [d['kind'] for d in rbac if d['kind'] in ('ClusterRole', 'ClusterRoleBinding')]
    expect(p, 'cluster-scoped grants', cluster, [])
    bindings = [d for d in rbac if d['kind'] == 'RoleBinding' and d['metadata']['namespace'] == NS]
    subjects = sorted((s['kind'], s['name']) for b in bindings for s in b.get('subjects') or [])
    expect(p, f'who is bound in {NS}', subjects, [('Group', 'admins')])
    roles = {d['metadata']['name']: d for d in rbac if d['kind'] == 'Role' and d['metadata']['namespace'] == NS}
    rules = {(g, r, tuple(sorted(rule['verbs']))) for b in bindings for rule in roles[b['roleRef']['name']]['rules']
             for g in rule['apiGroups'] for r in rule['resources']}
    expect(p, 'what admins may do there', rules, {('widgets.templates.krateo.io', '*', ('get', 'list')),
                                                  ('templates.krateo.io', 'restactions', ('get',))})
    return p


def check_authored_widgets_validate(c, with_console, crds_dir):
    """Every new widget AS RENDERED, against the CRDs — the apiserver's own admission question."""
    for chart, label in ((c, 'no console'), (with_console, 'console')):
        for kind, name in NEW_WIDGETS + (CONSOLE_WIDGETS if chart is with_console else []):
            tbi.RESOLVED.append((f'{kind} {name} as rendered ({label})', copy.deepcopy(chart.get(kind, name))))
    return []

# ---------------------------------------------------------------------------------------------
# "Last reconcile by composition" (obs-composition-staleness)
# ---------------------------------------------------------------------------------------------

STALE_RA = 'obs-composition-staleness'
STALE_WIDGETS = [('Row', 'obs-staleness-row'), ('Card', 'obs-staleness-card'),
                 ('Table', 'obs-composition-staleness'), ('Result', 'obs-staleness-allclear'),
                 ('Result', 'obs-staleness-unavailable'), ('Alert', 'obs-staleness-list-error')]
STALE_SQL = ("SELECT SpanAttributes['k8s.object.kind'] AS kind, SpanAttributes['k8s.object.namespace'] AS ns, "
             "SpanAttributes['k8s.object.name'] AS name, toUnixTimestamp(max(Timestamp)) AS last, "
             "toUnixTimestamp(min(Timestamp)) AS first, toUnixTimestamp(now()) AS now FROM otel_traces "
             "WHERE ServiceName = 'composition-dynamic-controller' AND SpanName = 'reconcile' "
             "AND Timestamp > now() - INTERVAL 7 DAY GROUP BY kind, ns, name FORMAT JSON")
NOW_S = 1794319200            # 2026-11-10 14:00:00 UTC, ClickHouse's now()
SINCE_S = 1793714520          # 2026-11-03 14:02:00 UTC, the oldest reconcile span held


def cdef(name, ns, api, plural, kind):
    return {'apiVersion': 'core.krateo.io/v1alpha1', 'kind': 'CompositionDefinition',
            'metadata': {'name': name, 'namespace': ns},
            'status': {'apiVersion': api, 'resource': plural, 'kind': kind}}


def comp(kind, api, ns, name, created):
    return {'apiVersion': api, 'kind': kind,
            'metadata': {'name': name, 'namespace': ns, 'creationTimestamp': created}}


STALE_CDS = {'items': [cdef('portal', 'krateo-system', 'composition.krateo.io/v1-8-106', 'portals', 'Portal'),
                       cdef('fireworks', 'krateo-blueprints', 'composition.krateo.io/v0-3-0', 'fireworksapps', 'FireworksApp'),
                       # A definition its controller has not reconciled yet: no GVR, no list.
                       {'apiVersion': 'core.krateo.io/v1alpha1', 'kind': 'CompositionDefinition',
                        'metadata': {'name': 'pending', 'namespace': 'krateo-system'}}]}
PORTALS = '/apis/composition.krateo.io/v1-8-106/portals'
FIREWORKS = '/apis/composition.krateo.io/v0-3-0/fireworksapps'
STALE_LISTS = {
    PORTALS: {'items': [comp('Portal', 'composition.krateo.io/v1-8-106', 'krateo-system', 'portal', '2026-08-25T10:00:00Z')]},
    FIREWORKS: {'items': [
        comp('FireworksApp', 'composition.krateo.io/v0-3-0', 'team-a', 'fresh', '2026-11-01T09:00:00Z'),
        comp('FireworksApp', 'composition.krateo.io/v0-3-0', 'team-a', 'stuck', '2026-11-02T09:00:00Z'),
        comp('FireworksApp', 'composition.krateo.io/v0-3-0', 'team-b', 'never-new', '2026-11-10T13:50:00Z'),
        comp('FireworksApp', 'composition.krateo.io/v0-3-0', 'team-b', 'never-old', '2026-11-10T11:00:00Z')]},
}


def recon(kind, ns, name, last, first):
    # As FORMAT JSON sends it: toUnixTimestamp is UInt32, so unquoted.
    return {'kind': kind, 'ns': ns, 'name': name, 'last': last, 'first': first, 'now': NOW_S}


STALE_SPANS = [
    recon('Portal', 'krateo-system', 'portal', NOW_S - 70, SINCE_S),
    recon('FireworksApp', 'team-a', 'fresh', NOW_S - 30, SINCE_S + 3600),
    recon('FireworksApp', 'team-a', 'stuck', NOW_S - 7200, SINCE_S + 7200),
    # Deleted since: spans, no live composition. Must not appear.
    recon('FireworksApp', 'team-a', 'deleted', NOW_S - 86400, SINCE_S + 60),
    # Same namespace/name, another kind: must not date team-b/never-old.
    recon('Portal', 'team-b', 'never-old', NOW_S - 10, SINCE_S + 120),
]


def staleness_chart():
    return render(values={'observability': {'reconcileTracing': True}})


def stale_resolve(c, spans=None, lists=None, ch_down=False):
    responses = {'crds': STALE_CDS, 'comps': STALE_LISTS if lists is None else lists}
    if not ch_down:
        responses['last'] = ch(STALE_SPANS if spans is None else spans)
    return gates.resolve(c.get('RESTAction', STALE_RA), {}, responses)


def stale_view(c, out, label):
    card = tbi.resolved_widget(c, 'Card', 'obs-staleness-card', out, {}, f'obs-staleness-card ({label})')['spec']['widgetData']
    return card, [i['resourceRefId'] for i in card['items']]


def check_staleness_gated_on_reconcile_tracing(c, on):
    p = []
    for kind, name in STALE_WIDGETS + [('RESTAction', STALE_RA)]:
        try:
            c.get(kind, name)
            p.append(f'{kind} {name} renders with reconcileTracing off')
        except LookupError:
            pass
        on.get(kind, name)
    for chart, want in ((c, False), (on, True)):
        col = chart.get('Col', 'obs-tab-reconciliation')['spec']
        items = [i['resourceRefId'] for i in col['widgetData']['items']]
        refs = [r['id'] for r in col['resourcesRefs']['items']]
        expect(p, f'reconcileTracing={want}: the tab lists the row', 'obs-staleness-row' in items, want)
        expect(p, f'reconcileTracing={want}: the tab refs the row', 'obs-staleness-row' in refs, want)
        if want:
            expect(p, 'the row is below the existing cards', items[-1], 'obs-staleness-row')
    card = on.get('Card', 'obs-staleness-card')['spec']['widgetData']
    expect(p, 'card title', card['title'], 'Last reconcile by composition')
    return p


def check_staleness_query_is_one_bounded_aggregate(c):
    p = []
    reqs, _, _ = stale_resolve(c)
    by = {}
    for name, path, _ in reqs:
        by.setdefault(name, []).append(path)
    expect(p, 'one CompositionDefinition list', by.get('crds'), ['/apis/core.krateo.io/v1alpha1/compositiondefinitions'])
    expect(p, 'one cluster-scope list per composition kind', sorted(by.get('comps') or []), sorted([FIREWORKS, PORTALS]))
    expect(p, 'one ClickHouse request', len(by.get('last') or []), 1)
    raw = (by.get('last') or [''])[0]
    expect(p, 'the SQL, decoded once', sql_of(raw), STALE_SQL)
    if unquote(sql_of(raw)) != sql_of(raw):
        p.append('the ClickHouse request is encoded twice')
    for bad, why in [('%25', 'an escaped %'), (' ', 'a space'), ('+', 'a bare +')]:
        if bad in raw:
            p.append(f'the request carries {why}')
    if 'ParentSpanId' in sql_of(raw):
        p.append('the SQL filters on ParentSpanId: traceparent-stamped compositions reconcile as child spans')
    ra = c.get('RESTAction', STALE_RA)['spec']['api']
    for step in ra:
        if step.get('endpointRef'):
            expect(p, f'{step["name"]} endpoint', step['endpointRef'], {'name': 'clickhouse-endpoint', 'namespace': RELEASE_NS})
        else:
            if not step.get('userAccessFilter'):
                p.append(f'{step["name"]}: an apiserver stage with no userAccessFilter (dialled as the caller)')
        if not step.get('continueOnError') or not step.get('errorKey'):
            p.append(f'{step["name"]}: needs continueOnError and an errorKey')
    return p


def stale_rows(c, out, label):
    return tbi.resolved_widget(c, 'Table', 'obs-composition-staleness', out, {}, f'obs-composition-staleness ({label})')['spec']['widgetData']['dataSource']


def check_staleness_joins_live_compositions(c):
    p = []
    _, _, out = stale_resolve(c)
    card, items = stale_view(c, out, 'mixed')
    expect(p, 'the body is the table', items, ['obs-composition-staleness'])
    expect(p, 'header right: the trace window', card['extra'], 'trace window: since 3 Nov, 14:02 (7 days)')
    rows = stale_rows(c, out, 'mixed')
    got = [(cells(r)['composition']['stringValue'], cells(r)['kind']['stringValue'],
            cells(r)['last']['kind'], cells(r)['last']['stringValue'], cells(r)['utc']['stringValue'],
            cells(r)['age']['stringValue']) for r in rows]
    expect(p, 'rows: none-in-window first (oldest created first), then oldest reconcile first; trace-only dropped', got, [
        ('team-b/never-old', 'FireworksApp', 'tag', 'none in the trace window', '—', 'created 3h ago'),
        ('team-b/never-new', 'FireworksApp', 'tag', 'none in the trace window', '—', 'created 10m ago'),
        ('team-a/stuck', 'FireworksApp', 'jsonSchemaType', '2026-11-10T12:00:00Z', '2026-11-10 12:00:00 UTC', 'created 8d ago'),
        ('krateo-system/portal', 'Portal', 'jsonSchemaType', '2026-11-10T13:58:50Z', '2026-11-10 13:58:50 UTC', 'created 77d ago'),
        ('team-a/fresh', 'FireworksApp', 'jsonSchemaType', '2026-11-10T13:59:30Z', '2026-11-10 13:59:30 UTC', 'created 9d ago'),
    ])
    first = cells(rows[0])
    expect(p, 'the pill is the warning token', first['last'].get('color'), 'warning')
    expect(p, 'a reconciled row shows relative time', cells(rows[2])['last'].get('format'), 'relative')
    expect(p, 'Kind and UTC are faint (token)', (first['kind'].get('color'), cells(rows[2])['utc'].get('color')),
           ('var(--faint-color)', 'var(--faint-color)'))
    table = c.get('Table', 'obs-composition-staleness')['spec']['widgetData']
    expect(p, 'columns', [col['title'] for col in table['columns']], ['Composition', 'Kind', 'Last reconcile', 'UTC', 'Age'])
    expect(p, 'paginated like its sibling', table.get('pagination'), {'defaultPageSize': 10})
    expect(p, 'a row opens its composition', rownav.build_row_path(table['rowNavigateTo'], rows[2]), '/compositions/team-a/stuck')
    expect(p, 'counts', out['counts'], {'live': 5, 'never': 2, 'overdue': 3})
    _, _, unq = stale_resolve(c, spans=[{k: (str(v) if isinstance(v, int) else v) for k, v in r.items()} for r in STALE_SPANS])
    expect(p, 'quoted integers read the same', stale_rows(c, unq, 'quoted ints'), rows)
    return p


def check_staleness_all_clear(c):
    p = []
    lists = {PORTALS: STALE_LISTS[PORTALS],
             FIREWORKS: {'items': [i for i in STALE_LISTS[FIREWORKS]['items'] if i['metadata']['name'] == 'fresh']}}
    _, _, out = stale_resolve(c, lists=lists)
    card, items = stale_view(c, out, 'all clear')
    expect(p, 'nothing overdue: the all-clear', items, ['obs-staleness-allclear'])
    expect(p, 'the header still names the window', card['extra'], 'trace window: since 3 Nov, 14:02 (7 days)')
    res = tbi.resolved_widget(c, 'Result', 'obs-staleness-allclear', out, {}, 'all clear')['spec']['widgetData']
    expect(p, 'all-clear text', (res['title'], res['subTitle']),
           ('All 2 compositions reconciled within the last 5 min', 'Nothing is overdue.'))
    # 6 minutes is overdue.
    late = [dict(r, last=NOW_S - 360) if r['name'] == 'fresh' else r for r in STALE_SPANS]
    _, _, out = stale_resolve(c, spans=late, lists=lists)
    expect(p, 'a reconcile 6 min ago is overdue', stale_view(c, out, 'six minutes')[1], ['obs-composition-staleness'])
    return p


UNAVAILABLE = ("The reconcile traces could not be read, so last-reconcile times can't be shown right now. "
               "The compositions themselves are not affected.")


def check_staleness_never_flags_every_row(c):
    p = []
    for label, kw, want, title in [('ClickHouse down', {'ch_down': True}, UNAVAILABLE, 'Telemetry unavailable'),
                                   ('no reconcile span at all', {'spans': []}, None, 'No reconcile traces')]:
        _, _, out = stale_resolve(c, **kw)
        card, items = stale_view(c, out, label)
        expect(p, f'{label}: one message, no table', items, ['obs-staleness-unavailable'])
        expect(p, f'{label}: header', card['extra'], 'trace window: unavailable')
        expect(p, f'{label}: no rows', out['rows'], [])
        expect(p, f'{label}: no composition counted as never reconciled or overdue', out['counts'],
               {'live': 5, 'never': 0, 'overdue': 0})
        res = tbi.resolved_widget(c, 'Result', 'obs-staleness-unavailable', out, {}, label)['spec']['widgetData']
        expect(p, f'{label}: the bold title', res.get('title'), title)
        if want:
            expect(p, f'{label}: the message', res['subTitle'], want)
        elif 'not affected' not in res['subTitle'] or 'tracing' not in res['subTitle']:
            p.append(f'{label}: the message should say tracing looks off: {res["subTitle"]!r}')
        stale_rows(c, out, label)
    _, _, out = stale_resolve(c, lists={PORTALS: STALE_LISTS[PORTALS]})      # the FireworksApp list fails
    expect(p, 'a failed composition list: alert above the table', stale_view(c, out, 'list error')[1],
           ['obs-staleness-list-error', 'obs-composition-staleness'])
    alert = tbi.resolved_widget(c, 'Alert', 'obs-staleness-list-error', out, {}, 'list error')['spec']['widgetData']
    if 'missing' not in alert['description']:
        p.append(f'the list-error alert: {alert["description"]!r}')
    return p


def check_staleness_admins_only_and_tokens(c):
    p = []
    for kind, name in STALE_WIDGETS + [('RESTAction', STALE_RA)]:
        doc = c.get(kind, name)
        expect(p, f'{kind} {name} namespace', doc['metadata']['namespace'], NS)
        if re.search(r'#[0-9A-Fa-f]{3,8}\b', json.dumps(doc['spec'])):
            p.append(f'{kind} {name}: a literal hex colour (use a design token)')
        if kind != 'RESTAction' and sorted(doc['spec'].get('keyExtras') or []) != ['projects', 'range']:
            p.append(f'{kind} {name}: keyExtras {doc["spec"].get("keyExtras")}')
    return p


def check_staleness_widgets_validate(c):
    for kind, name in STALE_WIDGETS:
        tbi.RESOLVED.append((f'{kind} {name} as rendered (reconcileTracing)', copy.deepcopy(c.get(kind, name))))
    return []



def main():
    args = sys.argv[1:]
    crds_dir, source = None, ''
    if '--crds' in args:
        crds_dir = args[args.index('--crds') + 1]
        source = crds_dir
    elif '--pinned' in args:
        pinned = _load('pinned', 'lint-pinned-crds.py')
        version, why = pinned.pinned_version()
        if not version:
            print(f'cannot determine the pinned frontend-crds version: {why}')
            return 1
        crds_dir = pinned.unpack(version)
        source = f'frontend-crds {version} ({why})'
    c, with_console = render(), render(CONSOLE)
    stale = staleness_chart()
    checks = [
        ('span list is newest first and bounded', lambda: check_span_list_is_newest_first_and_bounded(c)),
        ('an Error span shows as an error', lambda: check_error_status_shows_as_an_error(c)),
        ('the trace page says which span failed, and with what HTTP code', lambda: check_span_status_says_which_span_failed(c)),
        ('each filter reaches the SQL as one predicate', lambda: check_filters_reach_the_sql_as_one_predicate_each(c)),
        ('each request is encoded exactly once, as snowplow dials it', lambda: check_requests_are_encoded_exactly_once(c)),
        ('a quote or SQL fragment in any input makes no request', lambda: check_injection_is_refused(c)),
        ('the trace page lists spans in start order', lambda: check_trace_page_lists_spans_in_start_order(c)),
        ('the service diagram follows ParentSpanId', lambda: check_topology_follows_parent_span_ids(c)),
        ('filter chips keep the tab and the other filters', lambda: check_filter_chips_keep_the_tab_and_the_other_filters(c)),
        ('the HyperDX link exists only when configured', lambda: check_hyperdx_link_only_when_configured(c, with_console)),
        ('the tab and the trace route are wired', lambda: check_tab_and_route_are_wired(c)),
        ('HyperDX internals are hidden unless chosen', lambda: check_hyperdx_internals_hidden_unless_chosen(c)),
        ('the User column and filter say "AI calls"', lambda: check_user_is_labelled_ai_calls(c)),
        ('64-bit integers read the same quoted or not', lambda: check_64bit_integers_quoted_or_not(c)),
        ('admins only: new objects sit in the page namespace', lambda: check_admins_only(c, with_console)),
        ('staleness: renders only with reconcileTracing, below the Reconciliation cards',
         lambda: check_staleness_gated_on_reconcile_tracing(c, stale)),
        ('staleness: one bounded ClickHouse aggregate, kinds listed under a userAccessFilter',
         lambda: check_staleness_query_is_one_bounded_aggregate(stale)),
        ('staleness: live compositions joined on kind|ns|name, none-in-window first',
         lambda: check_staleness_joins_live_compositions(stale)),
        ('staleness: nothing overdue shows the all-clear', lambda: check_staleness_all_clear(stale)),
        ('staleness: unreadable or empty traces never flag every row', lambda: check_staleness_never_flags_every_row(stale)),
        ('staleness: admins only, design tokens only', lambda: check_staleness_admins_only_and_tokens(stale)),
    ]
    if crds_dir:
        checks.append(('(authored widgets queued for validation)', lambda: check_authored_widgets_validate(c, with_console, crds_dir)))
        checks.append(('(staleness widgets queued for validation)', lambda: check_staleness_widgets_validate(stale)))
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
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} widgets (rendered and resolved) validate against {source}')
        for prob in dict.fromkeys(problems):
            print(f'        {prob}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}; paths rendered by snowplow '
          f'{gates.spath.renderer().source}'
          f'{"" if crds_dir else "; widgets NOT validated (no --crds / --pinned)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
