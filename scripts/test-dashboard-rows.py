#!/usr/bin/env python3
"""The Dashboard's card rows reflow with the viewport instead of squeezing their cards on a phone.

WHY. Every Dashboard row gave its cards fixed spans, so a 390 px phone kept the desktop columns: the
four stat cards in ~86 px columns (labels wrapped letter by letter), Condition coverage in ~114 px
(title cut to "CO…"), Recent activity and Recent blueprints in ~171 px each (text wrapped word by
word). Diego's decisions:
- stats-row (2026-11-10): 4 across on desktop, 2 x 2 below 768 px, one per row below 400 px. antd's
  grid has no breakpoint at 400 (`xs` runs to 575 px), so it stacks below 576 px: the 390 px phone
  gets one per row as decided, and a phone from 400 to 575 px also gets one per row, not 2 x 2.
- charts-row (2026-11-11): one per row below 992 px, the 16 / 8 split from 992 px (antd `lg`).
- lists-row (2026-11-11): one per row below 768 px, 12 / 12 from 768 px (antd `md`).

HOW. The dashboard-page chart is rendered with `helm template`, and each row item's span is resolved
at a set of viewport widths the way antd 6 resolves a Col's classes (grid/style: the `span` class,
then the unconditioned `-xs` class, then `-sm` … `-xxl` under `min-width` media queries, each later
rule winning). The check is on the spans and the cards per line a person sees at each width, not on
the keys. With --crds DIR (a frontend-crds chart, as `helm pull --untar` leaves it) each rendered Row
is validated against its CRD, closed the way the apiserver's strict field validation is, so a span
key the deployed CRD does not know fails here rather than at install.

Usage: test-dashboard-rows.py [--crds DIR]
"""
import argparse
import copy
import glob
import os
import subprocess
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# antd 6 grid breakpoints (screen*Min tokens). `xs` has no media query: it is the base.
BREAKPOINTS = [('sm', 576), ('md', 768), ('lg', 992), ('xl', 1200), ('xxl', 1600)]

WIDTHS = [320, 390, 575, 576, 700, 767, 768, 900, 991, 992, 1440, 1920]


def stats_want(width):
    if width < 576:
        return [24] * 4  # one per row (the decision says below 400; antd has no 400)
    if width < 768:
        return [12] * 4  # 2 x 2
    return [6] * 4       # 4 across


# row name -> (its items in order, width -> the span of each item at that width)
ROWS = {
    'stats-row': (['stat-card-compositions', 'stat-card-healthy', 'stat-card-reconciles', 'stat-card-failed'],
                  stats_want),
    'charts-row': (['throughput-card', 'status-card'], lambda w: [24, 24] if w < 992 else [16, 8]),
    'lists-row': (['activity-card', 'blueprints-card'], lambda w: [24, 24] if w < 768 else [12, 12]),
}

failures = []


def render(chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy_dir = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', os.path.join(REPO, 'helm', chart), copy_dir], check=True)
        meta = os.path.join(copy_dir, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy_dir], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template {chart} failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def named(docs, kind, name):
    for d in docs:
        if d.get('kind') == kind and d.get('metadata', {}).get('name') == name:
            return d
    raise AssertionError(f'{kind} {name} not in the rendered chart')


def span_at(item, width, visible):
    """The span antd 6 gives a Row widget item's Col at `width` px.

    The Row widget passes `span={size ?? floor(24 / visible)}` plus the item's xs … xxl. antd emits
    `.ant-col-N` then `.ant-col-xs-N` unconditioned, then `-sm` … `-xxl` under min-width queries,
    so the last one that applies wins."""
    span = item.get('size', 24 // visible or 24)
    if 'xs' in item:
        span = item['xs']
    for key, min_width in BREAKPOINTS:
        if width >= min_width and key in item:
            span = item[key]
    return span


def per_row(spans):
    """Cards on the first line of an antd Row that wraps (24 units per line)."""
    used = count = 0
    for span in spans:
        if span == 0:
            continue
        if used + span > 24:
            break
        used += span
        count += 1
    return count


DASH = render('dashboard-page')
checked = []
for name, (want_ids, want_at) in ROWS.items():
    row = named(DASH, 'Row', name)
    checked.append(row)
    items = row['spec']['widgetData']['items']
    ids = [i['resourceRefId'] for i in items]
    if ids != want_ids:
        failures.append(f'{name} items: {ids!r} != {want_ids!r}')
        continue
    for width in WIDTHS:
        spans = [span_at(i, width, len(items)) for i in items]
        want = want_at(width)
        if spans != want:
            failures.append(f'{name} at {width} px: spans {spans} ({per_row(spans)} per line), '
                            f'want {want} ({per_row(want)} per line)')

parser = argparse.ArgumentParser()
parser.add_argument('--crds')
args = parser.parse_args()
crd_checks = 0
if args.crds:
    import jsonschema

    def strict(schema):
        """Close an openAPIV3Schema the way the apiserver's strict field validation does."""
        if isinstance(schema, dict):
            schema = {k: strict(v) for k, v in schema.items()}
            if schema.get('type') == 'object' and 'properties' in schema \
                    and not schema.get('x-kubernetes-preserve-unknown-fields') and 'additionalProperties' not in schema:
                schema['additionalProperties'] = False
            if schema.get('x-kubernetes-int-or-string'):
                schema.pop('type', None)
                schema['anyOf'] = [{'type': 'integer'}, {'type': 'string'}]
            return schema
        if isinstance(schema, list):
            return [strict(x) for x in schema]
        return schema

    crds = {}
    for path in glob.glob(os.path.join(args.crds, '**', '*.yaml'), recursive=True):
        for doc in yaml.safe_load_all(open(path, encoding='utf-8')):
            if isinstance(doc, dict) and doc.get('kind') == 'CustomResourceDefinition':
                crds[doc['spec']['names']['kind']] = doc
    crd = crds.get('Row')
    if crd is None:
        failures.append(f'no CRD for kind Row in {args.crds}')
    else:
        for row in checked:
            served = [v for v in crd['spec']['versions'] if v['name'] == row['apiVersion'].split('/')[1]]
            if not served:
                failures.append(f'the Row CRD serves no {row["apiVersion"]}')
                continue
            crd_checks += 1
            schema = strict(copy.deepcopy(served[0]['schema']['openAPIV3Schema']))
            schema.setdefault('properties', {}).update({'metadata': {'type': 'object'},
                                                        'apiVersion': {'type': 'string'}, 'kind': {'type': 'string'}})
            for err in sorted(jsonschema.Draft7Validator(schema).iter_errors(row), key=lambda e: list(e.path)):
                failures.append(f'Row {row["metadata"]["name"]}: {"/".join(map(str, err.path))}: {err.message[:200]}')

for line in failures:
    print(f'FAIL  {line}')
crd_note = f', {crd_checks} widget(s) validated against the CRDs' if args.crds else ', CRDs not checked (no --crds)'
print(f'{"FAILED" if failures else "ok"}: dashboard rows {", ".join(ROWS)} at {len(WIDTHS)} widths '
      f'({len(failures)} failure(s){crd_note})')
raise SystemExit(1 if failures else 0)
