#!/usr/bin/env python3
"""Every duration statistic reads the same way, over each chart's OWN jq.

WHY. The composition page showed "p95 duration 1,189 ms". The comma was antd Statistic's English
thousands separator, and an Italian reader takes it for a decimal point: 1.189 ms. Diego decided
(2026-11-13): under one second, whole milliseconds ("830 ms"); from one second up, seconds with ONE
decimal ("1.2 s"); no thousands separator anywhere.

ONE FORMATTER, THREE COPIES. `durationLabel` is defined in `_duration.tpl`, which compositions-page,
observability-page and portal-agents each carry, because a `define` cannot cross charts and a page
chart cannot ship a snowplow jq module. So this checks:
  - the three files are byte-for-byte the same;
  - the formatter's edge table, run through EACH chart's rendered copy (lifted out of a rendered
    expression, not read from the .tpl), gives the documented strings;
  - no label it can produce looks like a number to antd: Statistic's formatter re-groups any value
    matching ^-?\\d*(\\.\\d+)?$ ("1234.6" renders "1,234.6"), and a value carrying its unit is
    rendered verbatim (antd 6 statistic/Number.js);
  - every widget and RESTAction that shows a duration, evaluated through its own rendered jq, says
    what a reader should see — a Statistic takes the whole label as its value and has NO suffix;
  - no rendered expression in the three charts still glues a bare " ms" / "ms" / "s" unit onto a
    number by hand (the trace view's own format is the one exception, and it is checked to be
    unchanged: span durations keep microseconds and two decimals on purpose).

THE TIME-TO-FIRST-TOKEN AVERAGE is over the spans that carry one. agents-token-usage's SQL, rendered
by snowplow's own path renderer (snowplow-path.py) and decoded once, must aggregate it with
avgIfOrNull(…, has(mapKeys(SpanAttributes), 'agw.ai.time_to_first_token')): a plain avg() over
toFloat64OrZero counted every span without the attribute as 0 and halved the figure on krateo-057
(4.88 s against 9.81 s). OrNull because avgIf over no rows is NaN, which FORMAT JSON writes as null
or "-nan" by a server setting; and NaN reaching durationLabel any other way is "—" too. The query
is also EXECUTED, test-audit-page's way: decoded once, its ClickHouse spellings rewritten for sqlite,
run over fixture spans where half of one consumer's lack the attribute, and its rows fed to the RA's
own filter. Its effect on real data was measured on krateo-057 (see the PR).

Uses the `jq` binary; JQ=gojq runs the same checks on the engine snowplow uses. Needs `helm`, and
`go` + `git` for snowplow's path renderer (SNOWPLOW_SRC=<checkout> skips the clone of the pinned tag).
Exit code = number of failed checks (capped at 125).
"""
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from urllib.parse import unquote

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
JQ = os.environ.get('JQ', 'jq')

CHARTS = {
    'compositions-page': ['--set', 'namespace=krateo-compositions'],
    # Both sections that show durations are off by default; switch them on so they render.
    'observability-page': ['--set', 'observability.reconcileTracing=true',
                           '--set', 'observability.snowplowSli=true'],
    'portal-agents': ['--namespace', 'krateo-system'],
}

# The documented edges (see _duration.tpl). Input is milliseconds.
TABLE = [
    (0, '0 ms'),
    (1, '1 ms'),
    (999, '999 ms'),
    (999.4, '999 ms'),
    (999.6, '1.0 s'),       # rounded to the nearest ms FIRST: "1000 ms" never appears
    (1000, '1.0 s'),
    (1049, '1.0 s'),
    (1050, '1.1 s'),        # tenths round halves away from zero
    (1189, '1.2 s'),        # the composition page's 1,189 ms
    (9950, '10.0 s'),
    (59950, '60.0 s'),
    (61234, '61.2 s'),      # no minutes
    (1234567, '1234.6 s'),  # and no grouping, however large
    ('1189', '1.2 s'),      # ClickHouse quotes 64-bit numbers in FORMAT JSON
    (None, '—'),
    ('', '—'),
    ('n/a', '—'),
]

ANTD_NUMERIC = re.compile(r'^(-?)(\d*)(\.(\d+))?$')
DEF_RE = re.compile(r'def durationLabel: .*? end end;')

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f'{label}: {got!r} != {want!r}')


def render(chart, args):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', os.path.join(REPO, 'helm', chart), copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'd', copy, *args],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template {chart} failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def named(docs, kind, name):
    for d in docs:
        if d.get('kind') == kind and d.get('metadata', {}).get('name') == name:
            return d
    raise AssertionError(f'{kind}/{name} not in the rendered chart')


def jq(program, data):
    with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run([JQ, '-c', '-f', path], input=json.dumps(data),
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise AssertionError(f'jq failed: {proc.stderr.strip()}')
        return json.loads(proc.stdout)
    finally:
        os.unlink(path)


def body(expression):
    """The jq inside a widget's `${ … }`."""
    m = re.fullmatch(r'\s*\$\{(.*)\}\s*', expression, re.S)
    if not m:
        raise AssertionError(f'not a ${{ }} expression: {expression[:80]!r}')
    return m.group(1)


def template(doc, path):
    for e in doc['spec'].get('widgetDataTemplate', []):
        if e['forPath'] == path:
            return body(e['expression'])
    raise AssertionError(f'{doc["metadata"]["name"]}: no widgetDataTemplate for {path}')


def run(label, fn):
    """One case; a crash is a failure of that case, not of the whole run (so the control on main's
    templates reports every case it breaks)."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — reported, counted, and the run goes on
        failures.append(f'{label}: {type(exc).__name__}: {str(exc).strip()[:300]}')


def cell(row, key):
    for c in row:
        if c.get('valueKey') == key:
            return c
    raise AssertionError(f'no {key} cell in {row}')


# --- 1. one definition, three byte-identical copies ---------------------------------------------
copies = {}
for chart in CHARTS:
    path = os.path.join(REPO, 'helm', chart, 'templates', '_duration.tpl')
    copies[chart] = open(path, 'rb').read() if os.path.exists(path) else None
check('every chart carries _duration.tpl', sorted(c for c, b in copies.items() if b is None), [])
present = [b for b in copies.values() if b is not None]
check('_duration.tpl is byte-for-byte the same in every chart',
      len(present) == len(CHARTS) and len(set(present)) == 1, True)

docs = {}
for chart, args in CHARTS.items():
    docs[chart] = render(chart, args)

# --- 2. the edge table, through each chart's RENDERED copy --------------------------------------
LIFT_FROM = {
    'compositions-page': ('Statistic', 'composition-reconcile-p95', 'value'),
    'observability-page': ('Statistic', 'obs-perf-p95', 'value'),
    'portal-agents': ('RESTAction', 'agents-token-usage', None),
}
for chart, (kind, name, path) in LIFT_FROM.items():
    def edges(chart=chart, kind=kind, name=name, path=path):
        doc = named(docs[chart], kind, name)
        text = doc['spec']['filter'] if path is None else template(doc, path)
        m = DEF_RE.search(text)
        if not m:
            raise AssertionError(f'{kind}/{name} does not carry durationLabel')
        got = jq(m.group(0) + ' map(durationLabel)', [i for i, _ in TABLE])
        check(f'{chart}: durationLabel of NaN and infinities',
              jq(m.group(0) + ' [nan, infinite, -infinite] | map(durationLabel)', None), ['—', '—', '—'])
        for (i, want), g in zip(TABLE, got):
            check(f'{chart}: durationLabel({i!r})', g, want)
            check(f'{chart}: durationLabel({i!r}) has no thousands separator', ',' in g, False)
            # antd renders a numeric-looking value through its grouping formatter
            check(f'{chart}: durationLabel({i!r}) is rendered verbatim by antd Statistic',
                  bool(ANTD_NUMERIC.match(g)), False)
    run(f'{chart}: edge table', edges)

# --- 3. each widget, through its own rendered jq ------------------------------------------------
comp = docs['compositions-page']
obs = docs['observability-page']
agents = docs['portal-agents']


def statistic(doc, cases):
    name = doc['metadata']['name']
    check(f'{name}: no static suffix', 'suffix' in doc['spec']['widgetData'], False)
    check(f'{name}: no suffix template',
          [e['forPath'] for e in doc['spec'].get('widgetDataTemplate', []) if e['forPath'] == 'suffix'], [])
    check(f'{name}: static value is the em dash', doc['spec']['widgetData'].get('value'), '—')
    prog = template(doc, 'value')
    for data, want in cases:
        check(f'{name}: value for {data}', jq(prog, data), want)


run('composition-reconcile-p95', lambda: statistic(named(comp, 'Statistic', 'composition-reconcile-p95'), [
    ({'p95': 1189, 'n': 7}, '1.2 s'),
    ({'p95': 830, 'n': 7}, '830 ms'),
    ({'p95': '1189', 'n': 7}, '1.2 s'),
    ({'p95': None, 'n': 0}, '—'),
]))

for q in ('p50', 'p95', 'p99'):
    run(f'obs-perf-{q}', lambda q=q: statistic(named(obs, 'Statistic', f'obs-perf-{q}'), [
        ({'n': 40, q: 1189}, '1.2 s'),
        ({'n': 40, q: 830}, '830 ms'),
        ({'n': 40, q: 41234}, '41.2 s'),
        ({'n': 0, q: 0}, '—'),       # zero spans in range: never a fabricated "0 ms"
    ]))


def phases_card():
    prog = template(named(obs, 'Card', 'obs-phases-card'), 'extra')
    check('obs-phases-card extra', jq(prog, {'avg': 1189, 'n': 42}), 'avg 1.2 s · 42 reconciles')
    check('obs-phases-card extra, sub-second', jq(prog, {'avg': 830, 'n': 42}), 'avg 830 ms · 42 reconciles')


def throughput_card():
    prog = template(named(obs, 'Card', 'obs-throughput-card'), 'extra')
    check('obs-throughput-card extra',
          jq(prog, {'current': {'p95_ms': 1189}, 'failures': 1, 'successes': 99}), 'p95 1.2 s · 1 fail (1%)')
    check('obs-throughput-card extra, sub-second',
          jq(prog, {'current': {'p95_ms': 830}, 'failures': 0, 'successes': 10}), 'p95 830 ms · 0 fail (0%)')
    check('obs-throughput-card extra, no data',
          jq(prog, {'current': {}, 'failures': 0, 'successes': 0}), 'no reconcile-trace data')


def phases_list():
    prog = template(named(obs, 'Listy', 'obs-reconcile-phases'), 'dataSource')
    got = jq(prog, {'phases': [
        {'metric': 'x.rbac_generation_duration_seconds', 'mean_ms': '1189'},
        {'metric': 'x.get.duration_seconds', 'mean_ms': '12.5'},
    ]})
    check('obs-reconcile-phases labels', [(r['label'], r['msLabel']) for r in got],
          [('RBAC generate', '1.2 s'), ('Get object', '13 ms')])
    # the bar length still comes from the raw number, not the label
    check('obs-reconcile-phases percents', [r['percent'] for r in got], [100, 1])


def by_composition():
    prog = template(named(obs, 'Table', 'obs-reconcile-by-composition'), 'dataSource')
    rows = jq(prog, {'rows': [
        {'name': 'a', 'ns': 'n', 'kind': 'K', 'reconciles': '7', 'p95_ms': 3189, 'avg_ms': 830},
        {'name': 'b', 'ns': 'n', 'kind': 'K', 'reconciles': '3', 'p95_ms': '1189', 'avg_ms': '999.6'},
    ]})
    check('obs-reconcile-by-composition p95 cells', [cell(r, 'p95')['stringValue'] for r in rows], ['3.2 s', '1.2 s'])
    check('obs-reconcile-by-composition avg cells', [cell(r, 'avg')['stringValue'] for r in rows], ['830 ms', '1.0 s'])
    # the severity tint still reads the raw milliseconds
    check('obs-reconcile-by-composition p95 tint', [cell(r, 'p95')['color'] for r in rows],
          ['var(--red-color)', 'var(--gray-color)'])


def http_p95():
    doc = named(obs, 'Table', 'sp-http-p95')
    cols = [(c['valueKey'], c['title']) for c in doc['spec']['widgetData']['columns']]
    check('sp-http-p95 columns', cols, [('route', 'Route'), ('p95', 'p95')])
    rows = jq(template(doc, 'dataSource'), {'rows': [
        {'route': '/call', 'p95_s': 0.025}, {'route': '/list', 'p95_s': 2.5}, {'route': '/x', 'p95_s': None},
    ]})
    check('sp-http-p95 cells (the RA answers in seconds)', [cell(r, 'p95')['stringValue'] for r in rows],
          ['25 ms', '2.5 s', '—'])


def ttft():
    prog = named(agents, 'RESTAction', 'agents-token-usage')['spec']['filter']
    out = jq(prog, {'usage': {'data': [
        {'consumer': 'a', 'avg_ttft_s': 0.45}, {'consumer': 'b', 'avg_ttft_s': 1.23},
        {'consumer': 'c', 'avg_ttft_s': None}, {'consumer': 'd', 'avg_ttft_s': '12.34'},
        # the magnitudes krateo-057 stores (7 days, 892 spans): min 0.99, median 4.21, max 156.18
        {'consumer': 'e', 'avg_ttft_s': 0.99}, {'consumer': 'f', 'avg_ttft_s': 4.21},
        {'consumer': 'g', 'avg_ttft_s': 156.18},
        # no span in the group carried the attribute: avgIfOrNull is null; a bare avgIf's NaN with
        # output_format_json_quote_denormals=1 arrives as "-nan" (jq reads it as NaN, gojq refuses)
        {'consumer': 'h', 'avg_ttft_s': '-nan'},
    ]}})
    check('agents-token-usage ttft (agentgateway writes seconds: as_secs_f64)', [r['ttft'] for r in out['rows']],
          ['450 ms', '1.2 s', '—', '12.3 s', '990 ms', '4.2 s', '156.2 s', '—'])


TTFT_AGG = ("round(avgIfOrNull(toFloat64OrZero(SpanAttributes['agw.ai.time_to_first_token']), "
            "has(mapKeys(SpanAttributes), 'agw.ai.time_to_first_token')), 2) AS avg_ttft_s")

# A stand-in ClickHouse for this one query, test-audit-page's way: the SQL snowplow dials, decoded
# once, with the handful of ClickHouse spellings it uses rewritten for sqlite and executed against
# fixture spans. Anything left untranslated is sent as written, so sqlite refuses it rather than
# this passing on a guess. A Map read of a missing key is '' (ClickHouse's default for a String map).
NOW_MS = 1794218652000
HOUR_MS = 3600 * 1000


def _balanced(text, start):
    depth, i = 0, start
    while i < len(text):
        if text[i] == "'":
            i = text.index("'", i + 1)
        elif text[i] == '(':
            depth += 1
        elif text[i] == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f'unbalanced: {text[start:]}')


def _rewrite_call(text, name, build):
    while name + '(' in text:
        i = text.index(name + '(')
        j = _balanced(text, i + len(name))
        args, depth, cur = [], 0, ''
        for ch in text[i + len(name) + 1:j]:
            if ch == ',' and depth == 0:
                args.append(cur.strip())
                cur = ''
                continue
            depth += ch == '('
            depth -= ch == ')'
            cur += ch
        args.append(cur.strip())
        text = text[:i] + build(*args) + text[j + 1:]
    return text


def to_sqlite(sql):
    assert sql.endswith(' FORMAT JSON'), sql
    q = sql[:-len(' FORMAT JSON')]
    q = re.sub(r"has\(mapKeys\(SpanAttributes\), '([^']*)'\)", r"hasKey(SpanAttributes, '\1')", q)
    q = re.sub(r"SpanAttributes\['([^']*)'\]", r"attr(SpanAttributes, '\1')", q)
    q = re.sub(r'now\(\) - INTERVAL (\d+) (HOUR|DAY)',
               lambda m: f'({NOW_MS} - {int(m.group(1)) * (HOUR_MS if m.group(2) == "HOUR" else 24 * HOUR_MS)})', q)
    q = q.replace('count()', 'count(*)').replace(' / ', ' * 1.0 / ')      # ClickHouse `/` is never integer
    q = _rewrite_call(q, 'countIf', lambda c: f'sum(CASE WHEN {c} THEN 1 ELSE 0 END)')
    # avgIfOrNull: the mean over the rows matching the condition, NULL when none does — sqlite's avg
    # over all-NULL is NULL, which is exactly OrNull.
    q = _rewrite_call(q, 'avgIfOrNull', lambda x, c: f'avg(CASE WHEN {c} THEN {x} END)')
    return q


def _num(conv):
    def f(v):
        try:
            return conv(v)
        except (TypeError, ValueError):
            return conv(0)
    return f


def fake_clickhouse(spans, path):
    db = sqlite3.connect(':memory:')
    db.create_function('attr', 2, lambda js, k: json.loads(js).get(k, ''))
    db.create_function('hasKey', 2, lambda js, k: int(k in json.loads(js)))
    db.create_function('toUInt64OrZero', 1, _num(int))
    db.create_function('toFloat64OrZero', 1, _num(float))
    db.execute('CREATE TABLE otel_traces (Timestamp INTEGER, SpanAttributes TEXT)')
    for ago_ms, attrs in spans:
        db.execute('INSERT INTO otel_traces VALUES (?, ?)', (NOW_MS - ago_ms, json.dumps(attrs)))
    assert path.startswith('/?query='), path
    cur = db.execute(to_sqlite(unquote(path[len('/?query='):])))
    cols = [c[0] for c in cur.description]
    # FORMAT JSON quotes UInt64 (calls, token sums, errors): the filter's `num` coerces them back
    data = [{c: (str(v) if isinstance(v, int) else v) for c, v in zip(cols, row)} for row in cur.fetchall()]
    return {'meta': [{'name': c} for c in cols], 'data': data, 'rows': len(data)}


def llm_span(ago_min, consumer, ttft=None, tokens=100):
    attrs = {'gen_ai.usage.input_tokens': str(tokens), 'gen_ai.usage.output_tokens': '10',
             'gen_ai.request.model': 'm', 'gen_ai.provider.name': 'p', 'http.status': '200'}
    if consumer:
        attrs['jwt.sub'] = consumer
    if ttft is not None:
        attrs['agw.ai.time_to_first_token'] = repr(ttft)      # the exporter writes the f64 as text
    return (ago_min * 60 * 1000, attrs)


# krateo-057's shape in miniature: half of one consumer's spans carry no TTFT (non-streamed calls),
# and the unattributed caller's carry none at all.
TTFT_SPANS = [
    llm_span(5, 'alice', 9.0), llm_span(6, 'alice', 10.62), llm_span(7, 'alice'), llm_span(8, 'alice'),
    llm_span(9, '', tokens=50), llm_span(10, '', tokens=50),
    llm_span(60 * 30, 'alice', 1.0),                          # outside 24h: not averaged in
]


def ttft_sql():
    spec = importlib.util.spec_from_file_location('spath', os.path.join(HERE, 'snowplow-path.py'))
    spath = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spath)
    ra = named(agents, 'RESTAction', 'agents-token-usage')
    path = [st for st in ra['spec']['api'] if st.get('name') == 'usage'][0]['path']
    for rng in ('1h', '6h', '24h', '7d', None):
        dialled = spath.render(path, {} if rng is None else {'range': rng})
        sql = unquote(dialled.partition('?query=')[2])
        label = f'agents-token-usage SQL (range={rng})'
        check(f'{label}: TTFT averaged over the spans that carry it', TTFT_AGG in sql, True)
        check(f'{label}: no plain avg() over a zero-filled attribute', 'avg(toFloat64OrZero(' in sql, False)
        check(f'{label}: encoded exactly once', unquote(sql) == sql and '%' not in sql, True)
    # Executed: the dialled 24h query over the fixture spans, then the RA's own filter.
    usage = fake_clickhouse(TTFT_SPANS, spath.render(path, {'range': '24h'}))
    rows = {r['consumer']: r for r in jq(ra['spec']['filter'], {'usage': usage})['rows']}
    check('agents-token-usage executed: the mean over the two spans that carry a TTFT, not all four',
          rows.get('alice', {}).get('ttft'), '9.8 s')
    check('agents-token-usage executed: calls still count every span', rows.get('alice', {}).get('calls'), '4')
    check('agents-token-usage executed: a group with no TTFT at all is "—", never "0 ms"',
          rows.get('unattributed', {}).get('ttft'), '—')


run('obs-phases-card', phases_card)
run('obs-throughput-card', throughput_card)
run('obs-reconcile-phases', phases_list)
run('obs-reconcile-by-composition', by_composition)
run('sp-http-p95', http_p95)
run('agents-token-usage', ttft)
run('agents-token-usage SQL', ttft_sql)

# --- 4. nothing glues a unit on by hand any more ------------------------------------------------
TRACE_FORMAT_RE = re.compile(r'def traceDuration: .*?;\s*def traceUtc', re.S)
# A number turned into text and given a unit (`(.x|tostring) + " ms"`), or a bare "ms" literal (a
# Statistic suffix). Seconds are matched only when glued to a tostring: a bare "s" is a plural.
HAND_UNIT = re.compile(r'tostring\)\s*\+\s*"\s?(ms|s)\b|"\s?ms"')
# Durations that are NOT statistics, and keep their own spelling on purpose.
NOT_A_STATISTIC = {
    'observability-page RESTAction/obs-composition-staleness filter':
        'an AGE ("42s", "5m", "3h" since the last reconcile), at clock granularity, not a measurement',
    'portal-agents RESTAction/agents-policies filter':
        'a CONFIGURED CORS max-age ("max-age 600s") and rate-limit window, as the policy spells them',
}


def expressions(doc):
    spec = doc.get('spec') or {}
    for e in spec.get('widgetDataTemplate') or []:
        yield f'{doc["kind"]}/{doc["metadata"]["name"]} {e["forPath"]}', e['expression']
    if isinstance(spec.get('filter'), str):
        yield f'{doc["kind"]}/{doc["metadata"]["name"]} filter', spec['filter']
    for step in spec.get('api') or []:
        if isinstance(step.get('filter'), str):
            yield f'{doc["kind"]}/{doc["metadata"]["name"]} {step.get("name")} filter', step['filter']


hand = []
for chart in CHARTS:
    for doc in docs[chart]:
        for where, text in expressions(doc):
            text = TRACE_FORMAT_RE.sub('def traceUtc', DEF_RE.sub('', text))
            if HAND_UNIT.search(text) and f'{chart} {where}' not in NOT_A_STATISTIC:
                hand.append(f'{chart} {where}')
check('no expression appends a duration unit by hand (use durationLabel)', hand, [])

# --- 5. the trace view keeps its own precision --------------------------------------------------
def trace_view():
    for name in ('obs-traces', 'trace-detail-spans'):
        prog = template(named(obs, 'Table', name), 'dataSource')
        check(f'{name} formats with traceDuration', 'traceDuration' in prog, True)
        check(f'{name} does not use durationLabel', 'durationLabel' in prog, False)
    prog = template(named(obs, 'Table', 'obs-traces'), 'dataSource')
    m = TRACE_FORMAT_RE.search(prog)
    if not m:
        raise AssertionError('obs-traces does not carry traceDuration')
    defs = m.group(0)[:-len('def traceUtc')]
    ns = [0, 1500, 830_000_000, 1_189_000_000, 9_994_000_000, 61_234_000_000]
    check('traceDuration is unchanged (span precision is deliberate)', jq(defs + ' map(traceDuration)', ns),
          ['0 ms', '1 µs', '830 ms', '1.19 s', '9.99 s', '61.2 s'])


run('trace view', trace_view)

for f in failures:
    print(f'FAIL  {f}')
print(f'{"FAILED" if failures else "ok"}: duration labels across {len(CHARTS)} charts ({len(failures)} failure(s))')
raise SystemExit(min(len(failures), 125))
