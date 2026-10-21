#!/usr/bin/env python3
"""
lint-pinned-crds — a widget this chart authors must be admitted by the frontend-crds version the
PLATFORM PINS, not merely by whatever is latest.

WHAT HAPPENED, and it came within seconds of shipping. Widget CRDs are strict: no
`x-kubernetes-preserve-unknown-fields`, so a field the CRD does not declare is not tolerated.
Every other check in this repo validates widgets against `frontend-crds` pulled with NO `--version`
— i.e. whatever is latest when CI runs. The cluster runs whatever the installer PINS. When those
differ, CI is green on a chart the live apiserver will refuse.

On 2026-10-20 portal#313 added `ellipsis: true` to three Listy row templates
(list-composition-detail-conditions, list-composition-detail-events, composition-incidents — one
line per row with the full text on hover). `ellipsis` exists in frontend-crds 1.8.9, which was
published, so CI validated against it and passed. krateo-057 was pinned at 1.8.8, which does not
declare the field. The next portal tag would have delivered those widgets to a CRD that refuses
them, and nothing in CI could have said so. It was caught by a person reading a cross-session
message, and the ordering then held by two seconds.

This is the `data before consumer` ordering — the CRD must be released AND PINNED before the chart
that uses the field — enforced rather than remembered.

WHERE THE PINNED VERSION COMES FROM. krateo-platformops/installer, `chart/files/component-pins.yaml`,
the `.components[]` entry named `frontend-crd` (its `chart:` is `frontend-crds`, plural — the pin
name and the chart name differ, which is why this reads the entry rather than guessing either). The
repo is public, so CI needs no secret beyond the default token. Override with
FRONTEND_CRDS_VERSION=<v> to check against a specific version, or PINNED_CRDS_DIR=<dir> to use an
already-unpacked chart.

WHAT IT DOES NOT DO. It validates the widget CRs this repo AUTHORS, as rendered — not resolved
widgets. A resolved widget can carry fields a template never writes (a widgetDataTemplate adds
them at request time), and those are already covered against latest by test-ra-input-gates and
friends. This gate answers one question: does the platform's own pinned CRD admit what we ship?

Usage: lint-pinned-crds.py. Exit code = findings.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NS = 'krateo-system'
# DISCOVERED, not listed. The page-per-namespace split (#261) added eleven charts in three
# batches and a hardcoded list went stale each time — twice silently, once as a confusing failure.
# Every chart under helm/ is checked except builder-publish, which is a BLUEPRINT chart that CDC
# renders with a composition's own values and whose closed schema rejects the values used here.
CHARTS = tuple(sorted(os.path.basename(os.path.dirname(p))
                      for p in glob.glob(os.path.join(REPO, 'helm', '*', 'Chart.yaml'))
                      if os.path.basename(os.path.dirname(p)) != 'builder-publish'))
EXTRA_VALUES = {'portal': ['observability.snowplowSli=true']}
PINS_URL = ('https://raw.githubusercontent.com/krateo-platformops/installer/main/'
            'chart/files/component-pins.yaml')
WIDGET_GROUP = 'widgets.templates.krateo.io'


def pinned_version():
    """The frontend-crds version the platform pins, or None when it cannot be determined."""
    if os.environ.get('FRONTEND_CRDS_VERSION'):
        return os.environ['FRONTEND_CRDS_VERSION'], 'FRONTEND_CRDS_VERSION'
    # Three attempts: a transient blip should not red an unrelated PR, but a gate that cannot
    # read the pin must NOT pass. See the exit contract in main().
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(PINS_URL, timeout=30) as fh:
                pins = yaml.safe_load(fh.read())
            break
        except Exception as exc:                              # noqa: BLE001 - any fetch failure
            last = f'{type(exc).__name__}: {exc}'
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    else:
        return None, f'could not read the installer pins after 3 attempts: {last}'
    for comp in (pins or {}).get('components') or []:
        if isinstance(comp, dict) and comp.get('name') == 'frontend-crd':
            ver = comp.get('version')
            if ver:
                return str(ver), 'installer component-pins.yaml (.components[] name=frontend-crd)'
            return None, 'the frontend-crd pin entry carries no version'
    return None, 'no .components[] entry named frontend-crd in the installer pins'


def unpack(version):
    d = tempfile.mkdtemp()
    out = subprocess.run(['helm', 'pull', 'oci://ghcr.io/krateo-platformops/charts/frontend-crds',
                          '--version', version, '--untar', '-d', d],
                         capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f'could not pull frontend-crds {version}:\n{out.stderr[-800:]}')
    return os.path.join(d, 'frontend-crds')


def schemas(crds_dir):
    """{Kind: {version: spec-schema}} for every widget CRD in the chart."""
    out = {}
    for path in glob.glob(os.path.join(crds_dir, 'templates', '*.crd.yaml')):
        doc = yaml.safe_load(open(path, encoding='utf-8'))
        if not doc or doc.get('kind') != 'CustomResourceDefinition':
            continue
        if (doc.get('spec') or {}).get('group') != WIDGET_GROUP:
            continue
        kind = doc['spec']['names']['kind']
        for ver in doc['spec'].get('versions') or []:
            sch = ((ver.get('schema') or {}).get('openAPIV3Schema') or {}).get('properties', {}).get('spec')
            if sch:
                out.setdefault(kind, {})[ver['name']] = sch
    return out


def render(chart):
    d = tempfile.mkdtemp()
    dst = os.path.join(d, 'c')
    shutil.copytree(os.path.join(REPO, 'helm', chart), dst)
    meta = os.path.join(dst, 'Chart.yaml')
    text = open(meta, encoding='utf-8').read()
    open(meta, 'w', encoding='utf-8').write(
        text.replace('CHART_VERSION', '0.0.0').replace('APP_VERSION', '0.0.0'))
    cmd = ['helm', 'template', 'x', dst, '--namespace', NS]
    for v in EXTRA_VALUES.get(chart, []):
        cmd += ['--set', v]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f'helm template {chart} failed:\n{out.stderr[-900:]}')
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def undeclared(value, schema, path=''):
    """Paths present in `value` that `schema` does not declare and does not preserve."""
    bad = []
    if not isinstance(schema, dict):
        return bad
    if schema.get('x-kubernetes-preserve-unknown-fields'):
        return bad
    if isinstance(value, dict):
        props = schema.get('properties') or {}
        extra_ok = schema.get('additionalProperties')
        for key, sub in value.items():
            if key in props:
                bad += undeclared(sub, props[key], f'{path}.{key}')
            elif extra_ok in (None, False) and not props and extra_ok is None:
                # a schema with neither properties nor additionalProperties is opaque; say nothing
                continue
            elif key not in props and extra_ok in (None, False):
                bad.append(f'{path}.{key}' if path else key)
    elif isinstance(value, list):
        items = schema.get('items')
        if items:
            for i, sub in enumerate(value):
                bad += undeclared(sub, items, f'{path}[{i}]')
    return bad


def main():
    version, source = pinned_version()
    if not version:
        # A GATE THAT CANNOT READ THE PIN MUST NOT PASS. Exiting 0 here would make the whole check
        # vacuous the day the installer renames that file or moves the entry — green CI, nothing
        # guarded, and no one looking. That failure mode (a marker that can only pass) is the one
        # this repo keeps getting caught by, so it is refused structurally.
        print(f'  lint-pinned-crds: CANNOT DETERMINE THE PINNED VERSION — {source}')
        print(f'  Nothing was checked, so this is a FAILURE and not a skip.')
        print(f'  Unblock either by fixing the source above, or by pinning the check explicitly:')
        print(f'      FRONTEND_CRDS_VERSION=<version> python3 scripts/lint-pinned-crds.py')
        print(f'  (pins are read from {PINS_URL})')
        return 1
    crds = os.environ.get('PINNED_CRDS_DIR') or unpack(version)
    sch = schemas(crds)
    if not sch:
        print(f'  lint-pinned-crds: frontend-crds {version} renders no {WIDGET_GROUP} CRDs — refusing '
              f'to report a pass on an empty schema set')
        return 1

    findings, checked, kinds_missing = [], 0, set()
    for chart in CHARTS:
        for doc in render(chart):
            api = str(doc.get('apiVersion', ''))
            if not api.startswith(WIDGET_GROUP + '/'):
                continue
            kind, apiver = doc.get('kind'), api.split('/', 1)[1]
            name = (doc.get('metadata') or {}).get('name')
            if kind not in sch:
                kinds_missing.add(f'{kind} ({chart})')
                continue
            spec_schema = sch[kind].get(apiver)
            if not spec_schema:
                kinds_missing.add(f'{kind}/{apiver} ({chart})')
                continue
            checked += 1
            for p in undeclared(doc.get('spec') or {}, spec_schema):
                findings.append(f'{chart}: {kind}/{name} spec{p} — frontend-crds {version} is '
                                f'PINNED on the platform and does not declare it. Release AND pin a '
                                f'frontend-crds that does, before merging the widget that uses it.')

    for k in sorted(kinds_missing):
        findings.append(f'{k}: frontend-crds {version} serves no such kind/version — the pinned CRD '
                        f'chart cannot admit this widget at all.')
    for f in findings:
        print(f'  {f}')
    print(f'\nlint-pinned-crds: frontend-crds {version} (from {source}); {checked} authored widget(s) '
          f'checked, {len(findings)} finding(s)')
    return len(findings)


if __name__ == '__main__':
    sys.exit(main())
