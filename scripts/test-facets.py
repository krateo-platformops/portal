#!/usr/bin/env python3
"""portal.facets (_facets.tpl): one filter-and-facet helper, byte-identical in every page chart.

WHY. The root-pages UX (build plan §2.2) gives every overview page the same filter bar: one URL
param per facet, comma-joined values, counts beside each value, active-filter chips that remove
themselves, and "See all N →" into the table view. A `define` cannot cross charts and a page chart
cannot ship a snowplow jq module, so each chart that filters carries its own copy of _facets.tpl
(the _duration.tpl precedent). This checks:
  - the copies are byte-for-byte the same (every chart under helm/ that has the file, and
    dashboard-page, the first, must have it);
  - the semantics, run through each chart's RENDERED copy (lifted out of a RESTAction that includes
    it, not read from the .tpl) under jq and, with JQ=gojq, under snowplow's engine:
      a facet's counts ignore its own filter and apply every other one;
      visibility is decided over the base set (decision Q2): two or more distinct values, or an
      active one; a single-valued facet is hidden;
      a URL value no row carries is listed with count 0 and active, so it can be removed;
      at most 12 values, plus any active value outside them;
      an empty URL means no filter; values are split on commas and deduplicated;
      every href keeps the page's param order (gojq does not keep object key order, so an href built
      from an object would differ between jq and gojq), keeps ?projects, and @uri-encodes;
      the remove-chip href takes out exactly that one value; "Clear all" keeps only ?projects;
      "See all" opens the table view filtered to that group (decision Q1).

Usage: test-facets.py        (JQ=/path/to/gojq to run the same checks on gojq)
"""
import glob
import json
import os
import re
import subprocess
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
JQ = os.environ.get('JQ', 'jq')
FIRST = 'dashboard-page'
# Where each chart's rendered copy is lifted from: a RESTAction whose filter includes portal.facets.
LIFT_FROM = {'dashboard-page': 'dashboard-inbox'}

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f'{label}: {got!r} != {want!r}')


def render(chart):
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
        proc = subprocess.run(['helm', 'template', 'f', copy, '--namespace', 'krateo-system'],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template {chart} failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def jq(program, data=None):
    with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        args = [JQ, '-c', '-f', path] + (['-n'] if data is None else [])
        proc = subprocess.run(args, input=None if data is None else json.dumps(data),
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise AssertionError(f'jq failed: {proc.stderr.strip()}')
        return json.loads(proc.stdout)
    finally:
        os.unlink(path)


def run(label, fn):
    """One case; a crash is a failure of that case, not of the whole run (so the control on main's
    templates reports every case it breaks)."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - reported, counted, and the run goes on
        failures.append(f'{label}: {type(exc).__name__}: {str(exc).strip()[:300]}')


# The defs, as they appear once rendered: from `def fx_list:` to the end of `def fx_seeall …;`.
DEFS_RE = re.compile(r'def fx_list:.*?def fx_seeall\(\$path; \$params; \$k; \$v\):\n\s*[^\n]*;', re.S)

# --- 1. one helper, byte-identical copies ---------------------------------------------------------
paths = sorted(glob.glob(os.path.join(REPO, 'helm', '*', 'templates', '_facets.tpl')))
charts = {p.split(os.sep)[-3]: open(p, 'rb').read() for p in paths}
check(f'{FIRST} carries _facets.tpl', FIRST in charts, True)
check('_facets.tpl is byte-for-byte the same in every chart that carries it', len(set(charts.values())) <= 1, True)
check('every chart carrying _facets.tpl says where its rendered copy is lifted from',
      sorted(set(charts) - set(LIFT_FROM)), [])

# --- 2. the semantics, through each chart's rendered copy -----------------------------------------
BASE = ([{'f': {'kind': 'Alert', 'repo': 'org/x'}}] * 5 + [{'f': {'kind': 'Alert', 'repo': 'org/y'}}] * 2
        + [{'f': {'kind': 'Prompt', 'repo': 'org/x'}}] * 3 + [{'f': {'kind': 'Drift', 'repo': 'org/x'}}]
        + [{'f': {'kind': 'Drift', 'repo': ''}}])
KEYS = ['projects', 'kind', 'repo', 'q', 'view']


def facets(defs, extras, base=BASE, names=None):
    """Run the helper the way a page RA does: filters from extras, then a facet per key, the hrefs."""
    program = defs + '''
    . as $x | $x.extras | fx_filters(["kind", "repo"]) as $flt
    | ($x.extras | fx_params(''' + json.dumps(KEYS) + ''')) as $p
    | [ fx_facet($x.base; $flt; "kind"; "Kind"; $x.names), fx_facet($x.base; $flt; "repo"; "Repo"; {}) ] as $fc
    | { filters: $flt, facets: $fc, matched: ($x.base | fx_apply($flt) | length),
        chips: fx_chips("/reviews"; $p; $fc), table: fx_href("/reviews"; fx_set($p; "view"; "table")),
        cards: fx_href("/reviews"; fx_set($p; "view"; "")),
        seeall: fx_seeall("/reviews"; $p; "kind"; "Alert") }'''
    return jq(program, {'extras': extras, 'base': base, 'names': names or {}})


def by_value(facet):
    return {v['value']: (v['count'], v['active']) for v in facet['values']}


for chart, ra in LIFT_FROM.items():
    if chart not in charts:
        continue

    def semantics(chart=chart, ra=ra):
        doc = next(d for d in render(chart) if d.get('kind') == 'RESTAction' and d['metadata']['name'] == ra)
        m = DEFS_RE.search(doc['spec']['filter'])
        if not m:
            raise AssertionError(f'RESTAction/{ra} does not carry portal.facets')
        defs = m.group(0)

        # No filter: every count is over the base set, and nothing is active.
        out = facets(defs, {})
        check(f'{chart}: no URL, no filter', out['filters'], {'kind': [], 'repo': []})
        check(f'{chart}: no URL, every row matches', out['matched'], len(BASE))
        check(f'{chart}: kind counts, largest first', [(v['value'], v['count']) for v in out['facets'][0]['values']],
              [('Alert', 7), ('Prompt', 3), ('Drift', 2)])
        check(f'{chart}: an empty facet value is not a value', sorted(by_value(out['facets'][1])), ['org/x', 'org/y'])
        check(f'{chart}: no URL, no chips', out['chips'], [])

        # A facet's counts ignore its own filter and apply the others.
        out = facets(defs, {'kind': 'Alert'})
        check(f'{chart}: ?kind=Alert keeps the other kinds counted', by_value(out['facets'][0]),
              {'Alert': (7, True), 'Prompt': (3, False), 'Drift': (2, False)})
        check(f'{chart}: ?kind=Alert narrows the repo counts', by_value(out['facets'][1]),
              {'org/x': (5, False), 'org/y': (2, False)})
        check(f'{chart}: ?kind=Alert matches the alerts', out['matched'], 7)
        out = facets(defs, {'kind': 'Alert,Prompt', 'repo': 'org/x'})
        check(f'{chart}: two kinds and a repo', out['matched'], 8)
        check(f'{chart}: the kind counts apply the repo filter only', by_value(out['facets'][0]),
              {'Alert': (5, True), 'Prompt': (3, True), 'Drift': (1, False)})
        check(f'{chart}: the repo counts apply the kind filter only', by_value(out['facets'][1]),
              {'org/x': (8, True), 'org/y': (2, False)})

        # Values: split, deduplicated, unknown kept and removable, labels.
        out = facets(defs, {'kind': 'Alert,,Alert,Nope'}, names={'Alert': 'Alert rule'})
        check(f'{chart}: values deduplicated, empty dropped', out['filters']['kind'], ['Alert', 'Nope'])
        check(f'{chart}: an unknown URL value is listed, 0 and active', by_value(out['facets'][0]).get('Nope'), (0, True))
        check(f'{chart}: an unknown value matches no row', out['matched'], 7)
        check(f'{chart}: a value is labelled from the names map, else itself',
              {v['value']: v['label'] for v in out['facets'][0]['values']},
              {'Alert': 'Alert rule', 'Prompt': 'Prompt', 'Drift': 'Drift', 'Nope': 'Nope'})

        # Visibility over the BASE set (Q2).
        single = [{'f': {'kind': 'Alert', 'repo': 'org/x'}}] * 4
        out = facets(defs, {}, base=single)
        check(f'{chart}: a single-valued facet is hidden', [(f['key'], f['distinct'], f['show']) for f in out['facets']],
              [('kind', 1, False), ('repo', 1, False)])
        out = facets(defs, {'repo': 'org/x'}, base=single)
        check(f'{chart}: a single-valued facet shows while it is active', out['facets'][1]['show'], True)
        out = facets(defs, {'kind': 'Prompt'})
        check(f'{chart}: the repo facet stays visible under a filter that leaves one repo (distinct over the base)',
              (out['facets'][1]['distinct'], out['facets'][1]['show']), (2, True))
        check(f'{chart}: ... and its other value is still offered, at 0', by_value(out['facets'][1]),
              {'org/x': (3, False), 'org/y': (0, False)})

        # At most 12 values, plus an active one outside them.
        many = [{'f': {'kind': f'k{i:02d}', 'repo': 'r'}} for i in range(20) for _ in range(20 - i)]
        out = facets(defs, {'kind': 'k19'}, base=many)
        vals = [v['value'] for v in out['facets'][0]['values']]
        check(f'{chart}: the 12 largest values', vals[:12], [f'k{i:02d}' for i in range(12)])
        check(f'{chart}: plus the active value outside them', vals[12:], ['k19'])

        # Hrefs: order, projects carried, encoding, chips, table view.
        ext = {'projects': 'team-a,team-b', 'kind': 'Alert,Prompt', 'repo': 'org/x', 'q': 'cpu load'}
        out = facets(defs, ext)
        check(f'{chart}: the table view keeps every param, in the page order',
              out['table'], '/reviews?projects=team-a%2Cteam-b&kind=Alert%2CPrompt&repo=org%2Fx&q=cpu%20load&view=table')
        check(f'{chart}: the cards view drops ?view', out['cards'],
              '/reviews?projects=team-a%2Cteam-b&kind=Alert%2CPrompt&repo=org%2Fx&q=cpu%20load')
        check(f'{chart}: one removable chip per active value, then Clear all',
              [(c['label'], c.get('removable', False)) for c in out['chips']],
              [('Kind: Alert', True), ('Kind: Prompt', True), ('Repo: org/x', True), ('Clear all', False)])
        check(f'{chart}: a chip removes exactly its value', out['chips'][0]['href'],
              '/reviews?projects=team-a%2Cteam-b&kind=Prompt&repo=org%2Fx&q=cpu%20load')
        check(f'{chart}: the last value of a facet removes the param', out['chips'][2]['href'],
              '/reviews?projects=team-a%2Cteam-b&kind=Alert%2CPrompt&q=cpu%20load')
        check(f'{chart}: Clear all keeps only the shell scope', out['chips'][3]['href'], '/reviews?projects=team-a%2Cteam-b')
        check(f'{chart}: See all = the table view filtered to that group (Q1)', out['seeall'],
              '/reviews?projects=team-a%2Cteam-b&kind=Alert&repo=org%2Fx&q=cpu%20load&view=table')
        out = facets(defs, {'kind': 'Alert'})
        check(f'{chart}: Clear all with no scope is the bare path', out['chips'][-1]['href'], '/reviews')
        program = defs + ' fx_href("/x"; fx_set([["projects", ""]]; "view"; "table"))'
        check(f'{chart}: fx_set appends a key the page did not list', jq(program), '/x?view=table')

    run(f'{chart}: semantics', semantics)

for line in failures:
    print(f'FAIL  {line}')
print(f'{"FAILED" if failures else "ok"}: portal.facets ({len(failures)} failure(s), {len(charts)} chart(s), JQ={JQ})')
raise SystemExit(1 if failures else 0)
