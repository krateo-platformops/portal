#!/usr/bin/env python3
"""The Dashboard's four stat cards reflow with the viewport: 4 across, then 2 x 2, then one per row.

WHY. The stats row gave every card a fixed `size: 6`, so a 390 px phone kept four ~86 px columns and
the cards' labels wrapped letter by letter. Diego's decision of 2026-11-10: 4 across on desktop,
2 x 2 below 768 px, one per row below 400 px. antd's grid has no breakpoint at 400 (`xs` runs to
575 px), so the row stacks below 576 px: the 390 px phone gets one per row as decided, and a phone
from 400 to 575 px also gets one per row rather than 2 x 2.

HOW. The dashboard-page chart is rendered with `helm template`, and each stats-row item's spans are
resolved at a set of viewport widths the way antd 6 resolves a Col's classes (grid/style: the `span`
class, then the unconditioned `-xs` class, then `-sm` … `-xxl` under `min-width` media queries, each
later rule winning). The check is on the cards per row a person sees at each width, not on the
keys. With --crds DIR (a frontend-crds chart, as `helm pull --untar` leaves it) the rendered Row is
validated against its CRD, closed the way the apiserver's strict field validation is, so a span key
the deployed CRD does not know fails here rather than at install.

Usage: test-dashboard-stat-cards.py [--crds DIR]
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

STAT_CARDS = ['stat-card-compositions', 'stat-card-healthy', 'stat-card-reconciles', 'stat-card-failed']

# antd 6 grid breakpoints (screen*Min tokens). `xs` has no media query: it is the base.
BREAKPOINTS = [('sm', 576), ('md', 768), ('lg', 992), ('xl', 1200), ('xxl', 1600)]

# width (px) -> cards per row, per the decision and the antd breakpoint nearest its 400 px cut.
WANT = {
    320: 1, 390: 1, 575: 1,     # below sm: one per row (the decision says below 400; antd has no 400)
    576: 2, 700: 2, 767: 2,     # sm: 2 x 2
    768: 4, 900: 4, 1440: 4, 1920: 4,  # md and up: 4 across
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
row = named(DASH, 'Row', 'stats-row')
items = row['spec']['widgetData']['items']

ids = [i['resourceRefId'] for i in items]
if ids != STAT_CARDS:
    failures.append(f'stats-row items: {ids!r} != {STAT_CARDS!r}')

for width, want in sorted(WANT.items()):
    spans = [span_at(i, width, len(items)) for i in items]
    got = per_row(spans)
    if got != want:
        failures.append(f'at {width} px: {got} stat card(s) per row (spans {spans}), want {want}')
    # Every card on a line has the same span, so the 2 x 2 is two equal pairs and not 3 + 1.
    if len(set(spans)) != 1:
        failures.append(f'at {width} px: unequal spans {spans}')

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
        served = [v for v in crd['spec']['versions'] if v['name'] == row['apiVersion'].split('/')[1]]
        if not served:
            failures.append(f'the Row CRD serves no {row["apiVersion"]}')
        else:
            crd_checks += 1
            schema = strict(copy.deepcopy(served[0]['schema']['openAPIV3Schema']))
            schema.setdefault('properties', {}).update({'metadata': {'type': 'object'},
                                                        'apiVersion': {'type': 'string'}, 'kind': {'type': 'string'}})
            for err in sorted(jsonschema.Draft7Validator(schema).iter_errors(row), key=lambda e: list(e.path)):
                failures.append(f'Row stats-row: {"/".join(map(str, err.path))}: {err.message[:200]}')

for line in failures:
    print(f'FAIL  {line}')
crd_note = f', {crd_checks} widget(s) validated against the CRDs' if args.crds else ', CRDs not checked (no --crds)'
print(f'{"FAILED" if failures else "ok"}: dashboard stat cards per row at {len(WANT)} widths '
      f'({len(failures)} failure(s){crd_note})')
raise SystemExit(1 if failures else 0)
