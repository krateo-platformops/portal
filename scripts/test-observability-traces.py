#!/usr/bin/env python3
"""
test-observability-traces — the /observability Traces tab and the /observability/traces/{traceId}
page (installer#404), evaluated on span rows shaped like clickstack's otel_traces.

WHAT IT GUARDS. Three RESTActions (obs-traces, obs-trace-facets, obs-trace-detail) put request
text into ClickHouse SQL, and a dozen widgets turn their rows into a span list, filter chips, a
service diagram and a header. `helm template` evaluates none of that jq. This renders the chart,
resolves the RESTActions with test-ra-input-gates' model of snowplow's resolver (iterators,
dependsOn, step filters, continueOnError), runs the widgets' own jq over the result, and checks
what a reader would see:
  - the span list is newest first, bounded by a time window and LIMIT 200, and each filter reaches
    the SQL as exactly one predicate;
  - an Error span shows as a red "Error" pill, Ok as "OK", Unset as "Unset";
  - a row opens /observability/traces/<its full trace id>;
  - the trace page lists spans in start order with offsets from the first, and draws one node per
    service with an edge wherever a span's ParentSpanId belongs to another service;
  - a TraceId or filter carrying a quote or an SQL fragment makes NO request at all, and every
    request that is made keeps each SQL string literal to the characters its filter allows;
  - the HyperDX link exists only when `observabilityConsoleUrl` is set, and opens /trace/<id>;
  - every new object lives in the chart's namespace, which only `admins` can read.

With --crds DIR every widget, as rendered and as resolved, is validated against that frontend-crds
chart; --pinned pulls the version the installer pins (lint-pinned-crds.py reads it), which is what
the cluster serves. Widget CRDs are strict, so a field the CRD lacks is refused by the apiserver
(authored) or by snowplow (resolved).

Uses the `jq` binary; JQ=gojq runs the same checks on it.
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
    ('Listy', 'obs-traces-user-chips'), ('Listy', 'obs-traces-status-chips'),
    ('Table', 'obs-traces'),
    ('Flex', 'page-trace-detail'), ('PageHeader', 'trace-detail-page-header'), ('Row', 'trace-detail-grid'),
    ('Flex', 'trace-detail-col-left'), ('Flex', 'trace-detail-col-right'),
    ('Card', 'card-trace-detail-spans'), ('Card', 'card-trace-detail-topology'),
    ('Table', 'trace-detail-spans'), ('FlowChart', 'trace-detail-topology'), ('Markdown', 'trace-detail-note'),
]
CONSOLE_WIDGETS = [('Flex', 'trace-detail-hyperdx'), ('Button', 'trace-detail-hyperdx-link'),
                   ('Paragraph', 'trace-detail-hyperdx-caption')]
NEW_RAS = ['obs-traces', 'obs-trace-facets', 'obs-trace-detail']


def render(console=''):
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(CHART, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        open(meta, 'w', encoding='utf-8').write(text.replace('CHART_VERSION', '0.0.0-dev'))
        cmd = ['helm', 'template', 'observability-page', staged, '--namespace', RELEASE_NS]
        if console:
            cmd += ['--set', f'observabilityConsoleUrl={console}']
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


def span(start_us, sid, parent, svc, name, dur_ns, status='Unset', sub=''):
    return {'start_us': str(start_us), 'span_id': sid, 'parent_id': parent, 'svc': svc, 'span': name,
            'dur_ns': str(dur_ns), 'status': status, 'sub': sub}


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
    span(US - 133886000, 'b000000000000001', 'ffffffffffffffff', 'snowplow', 'GET /call restactions/obs-log-stream', 1940000000, 'Error', 'cyberjoker'),
    span(US - 133880000, 'b000000000000002', 'b000000000000001', 'snowplow', 'clickhouse query',                    1900000000, 'Error'),
    span(US - 133000000, 'b000000000000003', 'b000000000000001', 'snowplow', 'jq filter',                           820000),
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


LIST_SQL = ("SELECT toUnixTimestamp64Milli(Timestamp) AS ts_ms, ServiceName AS svc, SpanName AS span, "
            "Duration AS dur_ns, SpanAttributes['jwt.sub'] AS sub, StatusCode AS status, TraceId AS trace_id "
            "FROM otel_traces WHERE Timestamp > now() - INTERVAL {window}{cond} "
            "ORDER BY Timestamp DESC LIMIT 200 FORMAT JSON")
DETAIL_SQL = ("SELECT toUnixTimestamp64Micro(Timestamp) AS start_us, SpanId AS span_id, ParentSpanId AS parent_id, "
              "ServiceName AS svc, SpanName AS span, Duration AS dur_ns, StatusCode AS status, "
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
    expect(p, 'no filters: the SQL', sql_of(reqs[0][1]), LIST_SQL.format(window='24 HOUR', cond=''))
    for rng, window in [('1h', '1 HOUR'), ('6h', '6 HOUR'), ('24h', '24 HOUR'), ('7d', '7 DAY'), ('30d', '24 HOUR')]:
        r, _, _ = resolve(c, 'obs-traces', {'range': rng}, {'spans': ch([])})
        expect(p, f'range={rng}: the window', sql_of(r[0][1]), LIST_SQL.format(window=window, cond=''))
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
           ['Time (UTC)', 'Service', 'Span', 'Duration', 'User', 'Status', 'Trace'])
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


def check_filters_reach_the_sql_as_one_predicate_each(c):
    p = []
    cases = [
        ({'traceService': 'composition-dynamic-controller'}, " AND ServiceName = 'composition-dynamic-controller'"),
        ({'traceUser': 'cyberjoker'}, " AND SpanAttributes['jwt.sub'] = 'cyberjoker'"),
        ({'traceUser': 'system:serviceaccount:krateo-system:snowplow'},
         " AND SpanAttributes['jwt.sub'] = 'system:serviceaccount:krateo-system:snowplow'"),
        ({'traceUser': 'CgVhZG1pbhIF+bG9j/YWw='}, " AND SpanAttributes['jwt.sub'] = 'CgVhZG1pbhIF+bG9j/YWw='"),
        ({'traceUser': 'diego.braga@kiratech.it'}, " AND SpanAttributes['jwt.sub'] = 'diego.braga@kiratech.it'"),
        ({'traceStatus': 'Error'}, " AND StatusCode = 'Error'"),
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
    checks = [
        ('span list is newest first and bounded', lambda: check_span_list_is_newest_first_and_bounded(c)),
        ('an Error span shows as an error', lambda: check_error_status_shows_as_an_error(c)),
        ('each filter reaches the SQL as one predicate', lambda: check_filters_reach_the_sql_as_one_predicate_each(c)),
        ('a quote or SQL fragment in any input makes no request', lambda: check_injection_is_refused(c)),
        ('the trace page lists spans in start order', lambda: check_trace_page_lists_spans_in_start_order(c)),
        ('the service diagram follows ParentSpanId', lambda: check_topology_follows_parent_span_ids(c)),
        ('filter chips keep the tab and the other filters', lambda: check_filter_chips_keep_the_tab_and_the_other_filters(c)),
        ('the HyperDX link exists only when configured', lambda: check_hyperdx_link_only_when_configured(c, with_console)),
        ('the tab and the trace route are wired', lambda: check_tab_and_route_are_wired(c)),
        ('admins only: new objects sit in the page namespace', lambda: check_admins_only(c, with_console)),
    ]
    if crds_dir:
        checks.append(('(authored widgets queued for validation)', lambda: check_authored_widgets_validate(c, with_console, crds_dir)))
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
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}'
          f'{"" if crds_dir else "; widgets NOT validated (no --crds / --pinned)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
