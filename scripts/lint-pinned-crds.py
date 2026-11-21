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

THE FLOORS. The pin check above guards one direction: what portal ships against what is pinned
NOW. The other direction is a later pin moving frontend-crds DOWN below what a chart stores, and
a downgrade triggers nothing in this repo. So each chart that needs a minimum declares it in its
Chart.yaml, where installer's check-chart-requires-floors reads it on every pin change, in both
directions:

    annotations:
      krateo.io/requires-frontend-crd: ">= 1.8.54"

The key names the installer COMPONENT (frontend-crd), not the chart (frontend-crds); installer's
gate refuses a component it does not pin. A floor written by hand goes stale the day the chart
starts storing a newer field, so this script also checks every declared floor: each chart's
widgets are linted against frontend-crds AT ITS FLOOR, and any finding means the floor no longer
admits what the chart ships. Only ">= X.Y.Z" is accepted; anything else is refused, because a
floor this gate cannot evaluate has not been checked.

Usage: lint-pinned-crds.py. Exit code = findings.
"""
import glob
import json
import os
import re
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
# Values that switch ON widgets a default render leaves out, so the gate sees them. A widget behind an
# off-by-default flag is still shipped: the day an install sets the flag, the apiserver must admit it.
# observability-page reads these two flags; `portal` no longer does (the page split, #261), yet this
# map still set one of them on `portal` — so the ~70 widgets they gate (the Performance section and
# the snowplow SLI tab) were never checked against the pinned CRDs. Both flags, on the chart that reads them.
EXTRA_VALUES = {'observability-page': ['observability.snowplowSli=true',
                                       'observability.reconcileTracing=true']}
INSTALLER_GIT = 'https://github.com/krateo-platformops/installer'
PINS_URL = ('https://raw.githubusercontent.com/krateo-platformops/installer/{ref}/'
            'chart/files/component-pins.yaml')
WIDGET_GROUP = 'widgets.templates.krateo.io'
FLOOR_KEY = 'krateo.io/requires-frontend-crd'
FLOOR_RE = re.compile(r'^>=\s*(\d+\.\d+\.\d+)$')


def installer_main_sha():
    """The commit installer main is at. The pins are read AT this commit, not at `main`, so the
    version this gate reports can be checked later: `main` moves, and could move between resolving
    it and reading the file."""
    out = subprocess.run(['git', 'ls-remote', INSTALLER_GIT, 'refs/heads/main'],
                         capture_output=True, text=True, timeout=30,
                         env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    fields = out.stdout.split()
    if out.returncode or not fields:
        raise RuntimeError(f'git ls-remote {INSTALLER_GIT}: {out.stderr.strip() or "no main ref"}')
    return fields[0]


def pinned_version():
    """The frontend-crds version the platform pins, or None when it cannot be determined."""
    if os.environ.get('FRONTEND_CRDS_VERSION'):
        return os.environ['FRONTEND_CRDS_VERSION'], 'FRONTEND_CRDS_VERSION'
    return pinned_component_version('frontend-crd')


def pinned_component_version(component):
    """(version, where it was read) of one installer component pin; version None when it cannot
    be determined."""
    # Three attempts: a transient blip should not red an unrelated PR, but a gate that cannot
    # read the pin must NOT pass. See the exit contract in main().
    last = None
    for attempt in range(3):
        try:
            sha = installer_main_sha()
            with urllib.request.urlopen(PINS_URL.format(ref=sha), timeout=30) as fh:
                pins = yaml.safe_load(fh.read())
            break
        except Exception as exc:                              # noqa: BLE001 - any fetch failure
            last = f'{type(exc).__name__}: {exc}'
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    else:
        return None, f'could not read the installer pins after 3 attempts: {last}'
    for comp in (pins or {}).get('components') or []:
        if isinstance(comp, dict) and comp.get('name') == component:
            ver = comp.get('version')
            if ver:
                return str(ver), f'installer main @ {sha[:7]}, not a release: chart/files/component-pins.yaml'
            return None, f'the {component} pin entry carries no version'
    return None, f'no .components[] entry named {component} in the installer pins'


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


def check(version, sch, rendered):
    """(findings, checked) for the rendered widgets of each chart in `rendered` against `sch`."""
    findings, checked, kinds_missing = [], 0, set()
    for chart, docs in rendered.items():
        for doc in docs:
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
                findings.append((chart, f'{kind}/{name} spec{p}'))
    for k in sorted(kinds_missing):
        findings.append((k.rsplit(' (', 1)[1].rstrip(')'), f'{k.rsplit(" (", 1)[0]}: no such kind/version served'))
    return findings, checked


def floor_of(chart):
    """(version or None, error or None) from the chart's krateo.io/requires-frontend-crd."""
    meta = yaml.safe_load(open(os.path.join(REPO, 'helm', chart, 'Chart.yaml'), encoding='utf-8')) or {}
    raw = (meta.get('annotations') or {}).get(FLOOR_KEY)
    if raw is None:
        return None, None
    m = FLOOR_RE.match(str(raw).strip())
    if not m:
        return None, (f'{chart}: {FLOOR_KEY}: {raw!r} is not ">= X.Y.Z". Refused: a floor this gate '
                      f'cannot evaluate has not been checked.')
    return m.group(1), None


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
        print(f'  (pins are read from {PINS_URL.format(ref="main")}, at the commit main resolves to)')
        return 1
    crds = os.environ.get('PINNED_CRDS_DIR') or unpack(version)
    sch = schemas(crds)
    if not sch:
        print(f'  lint-pinned-crds: frontend-crds {version} renders no {WIDGET_GROUP} CRDs — refusing '
              f'to report a pass on an empty schema set')
        return 1

    rendered = {chart: render(chart) for chart in CHARTS}
    found, checked = check(version, sch, rendered)
    findings = [f'{chart}: {what} — frontend-crds {version} is PINNED on the platform and does not '
                f'declare it. Release AND pin a frontend-crds that does, before merging the widget '
                f'that uses it.' for chart, what in found]

    floors, floored = {}, 0
    for chart in CHARTS:
        floor, err = floor_of(chart)
        if err:
            findings.append(err)
        elif floor:
            floors.setdefault(floor, []).append(chart)
    for floor, charts in sorted(floors.items()):
        floor_sch = schemas(unpack(floor))
        if not floor_sch:
            findings.append(f'{", ".join(charts)}: frontend-crds {floor} (declared floor) renders no '
                            f'{WIDGET_GROUP} CRDs; refusing to report a pass on an empty schema set')
            continue
        floored += len(charts)
        found, _ = check(floor, floor_sch, {c: rendered[c] for c in charts})
        findings += [f'{chart}: {what} — frontend-crds {floor} is this chart\'s DECLARED FLOOR '
                     f'({FLOOR_KEY}) and does not declare it, so the floor no longer admits what the '
                     f'chart ships. Raise the floor to the first frontend-crds that does.'
                     for chart, what in found]

    for f in findings:
        print(f'  {f}')
    print(f'\nlint-pinned-crds: frontend-crds {version} (from {source}); {checked} authored widget(s) '
          f'checked; {floored} chart floor(s) checked at their declared version; '
          f'{len(findings)} finding(s)')
    return len(findings)


if __name__ == '__main__':
    sys.exit(main())
