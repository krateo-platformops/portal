#!/usr/bin/env python3
"""
lint-crd-tightening — a CRD this repo owns must not become STRICTER than the one in the field.

THE ASYMMETRY THIS RESTS ON. Stored objects are not revalidated when a CRD changes, so a tightening
is silent at the roll: the CRD step reports success, every existing CR keeps sitting in etcd, and the
failure arrives at the NEXT write to one of them — attributed to whoever made that write, several
layers from the cause. A loosening cannot do that. So this gate is deliberately one-directional:
it fails on a constraint that got stricter and says nothing about one that got looser.

WHAT IT COST LAST TIME. frontend-crds 1.8.20 added `required: [id]` to a node defining only
`iterator` and `template`, across all 44 widget CRDs. 29 live CRs became unappliable on demo, and
the recovery was a rollback. Three gates were green: drift (compares the chart to the generator —
both wrong identically), the apiserver's own CRD validation (a required-but-undefined property is
legal OpenAPI), and a property-path diff (a new `required` entry adds no path and removes none).
None of them was asking whether the new schema still admits what the old one accepted.

THE EXPOSURE HERE, which is why this lives in this repo and not only in frontend. The Ticket CRD's
own note says its schema "will churn while people learn what a ticket needs". A churning schema on
a kind holding people's submitted requests is exactly the shape that incident had, and nothing in
this repo looked at it.

THE BASELINE IS THE VERSION WITH DATA IN IT, not merely the previous commit:
  - the installer's pin when the chart has one (`portal` -> component-pins.yaml), because that is
    the CRD live clusters are running and therefore the CRs that exist;
  - otherwise the latest published version of that chart, because no pin means delivery is not
    pinned, so latest-published is what an install gets. `service-desk-page` has no pin entry.
  - a chart with no published version at all has no baseline. That is reported as "no baseline",
    never as a pass: a gate that silently checks nothing is the failure mode this file exists to
    describe.

THE CONSTRAINTS CHECKED:
  required  a name required now and not before
  enum      an enum introduced, or values removed from one
  type      a type changed
  format    a format introduced or changed
  bounds    minimum/minLength raised, maximum/maxLength lowered, or any of them introduced
  pattern   introduced OR changed (see below)
  properties   a declared property REMOVED (see below)
  x-kubernetes-preserve-unknown-fields   true before, not true now (the subtree stops tolerating)

TWO OF THOSE NEED THEIR REASONING STATED, because both are deliberately louder than the others.

`pattern` reports a CHANGE, not only an introduction. Regex containment is undecidable in general,
so there is no cheap way to tell a widened pattern from a narrowed one — and treating "changed" as
safe is the assumption that loses data. The cost is a finding on a genuine widening, which a
reviewer reads once and waves through.

A REMOVED property is the only clause here whose harm is not a refused write. Structural pruning
drops an undeclared field SILENTLY and admits the object, so an existing CR carrying it loses that
data on its next write with nothing reported anywhere — strictly worse than the loud failure every
other clause describes. It is exempted under a node that tolerates unknown content, where the field
was never declared-and-validated to begin with.

Env: PORTAL_BASELINE_VERSION=<v> pins the baseline for every chart (useful for reproducing a
finding). Exit code = findings. A baseline that cannot be FETCHED is a finding, not a skip.
"""
import glob
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
OCI = 'oci://ghcr.io/krateo-platformops/charts'
PINS_URL = ('https://raw.githubusercontent.com/krateo-platformops/installer/main/'
            'chart/files/component-pins.yaml')
CHARTS = tuple(sorted(os.path.basename(os.path.dirname(p))
                      for p in glob.glob(os.path.join(REPO, 'helm', '*', 'Chart.yaml'))
                      if os.path.basename(os.path.dirname(p)) != 'builder-publish'))
PRESERVE = 'x-kubernetes-preserve-unknown-fields'


def pins():
    """{chart: version} from the installer pins, or None when they cannot be read."""
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(PINS_URL, timeout=30) as fh:
                doc = yaml.safe_load(fh.read())
            return {str(c['chart']): str(c['version'])
                    for c in (doc or {}).get('components') or []
                    if isinstance(c, dict) and c.get('chart') and c.get('version')}, None
        except Exception as exc:                              # noqa: BLE001 - any fetch failure
            last = f'{type(exc).__name__}: {exc}'
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    return None, f'could not read the installer pins after 3 attempts: {last}'


def render(chart):
    """CRDs this chart renders, as {crd-name: {version: openAPIV3Schema}}."""
    work = tempfile.mkdtemp()
    try:
        dst = os.path.join(work, chart)
        shutil.copytree(os.path.join(REPO, 'helm', chart), dst)
        cy = os.path.join(dst, 'Chart.yaml')
        with open(cy, encoding='utf-8') as fh:
            text = fh.read()
        with open(cy, 'w', encoding='utf-8') as fh:
            fh.write(re.sub(r'CHART_VERSION|SOURCE_REF|APP_VERSION', '0.0.0', text))
        out = subprocess.run(['helm', 'template', chart, dst], capture_output=True, text=True)
        if out.returncode:
            return None, (out.stderr.strip().splitlines() or ['render failed'])[-1]
        return crds_in(yaml.safe_load_all(out.stdout)), None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def pull(chart, version):
    """The same, from a PUBLISHED chart version."""
    d = tempfile.mkdtemp()
    out = subprocess.run(['helm', 'pull', f'{OCI}/{chart}', '--version', version,
                          '--untar', '-d', d], capture_output=True, text=True)
    if out.returncode:
        shutil.rmtree(d, ignore_errors=True)
        return None, (out.stderr.strip().splitlines() or ['pull failed'])[-1]
    try:
        cy = os.path.join(d, chart, 'Chart.yaml')
        with open(cy, encoding='utf-8') as fh:
            text = fh.read()
        with open(cy, 'w', encoding='utf-8') as fh:
            fh.write(re.sub(r'CHART_VERSION|SOURCE_REF|APP_VERSION', '0.0.0', text))
        out = subprocess.run(['helm', 'template', chart, os.path.join(d, chart)],
                             capture_output=True, text=True)
        if out.returncode:
            return None, (out.stderr.strip().splitlines() or ['render failed'])[-1]
        return crds_in(yaml.safe_load_all(out.stdout)), None
    finally:
        shutil.rmtree(d, ignore_errors=True)


def crds_in(docs):
    out = {}
    for doc in docs:
        if not isinstance(doc, dict) or doc.get('kind') != 'CustomResourceDefinition':
            continue
        name = (doc.get('metadata') or {}).get('name')
        for ver in (doc.get('spec') or {}).get('versions') or []:
            schema = (ver.get('schema') or {}).get('openAPIV3Schema')
            if schema:
                out.setdefault(name, {})[ver.get('name')] = schema
    return out


def latest_published(chart):
    out = subprocess.run(['helm', 'show', 'chart', f'{OCI}/{chart}'],
                         capture_output=True, text=True)
    if out.returncode:
        return None
    for line in out.stdout.splitlines():
        if line.startswith('version:'):
            return line.split(':', 1)[1].strip()
    return None


def compare(old, new, path, report):
    """Report every TIGHTENING of `new` relative to `old` at and below `path`."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return

    gained = sorted(set(new.get('required') or []) - set(old.get('required') or []))
    if gained:
        report(path, f'now requires {gained}, which the baseline did not. Existing objects '
                     f'without those fields can no longer be written.')

    old_enum, new_enum = old.get('enum'), new.get('enum')
    if new_enum is not None and old_enum is None:
        report(path, f'gained an enum {sorted(map(str, new_enum))} where the baseline constrained '
                     f'nothing. Any other stored value is now invalid.')
    elif new_enum is not None and old_enum is not None:
        lost = [v for v in old_enum if v not in new_enum]
        if lost:
            report(path, f'dropped {sorted(map(str, lost))} from its enum. Objects holding those '
                         f'values can no longer be written.')

    if old.get('type') and new.get('type') and old['type'] != new['type']:
        report(path, f'changed type from {old["type"]!r} to {new["type"]!r}.')

    if new.get('format') and new.get('format') != old.get('format'):
        report(path, f'gained format {new["format"]!r} (baseline: {old.get("format")!r}). A value '
                     f'the baseline accepted may no longer validate.')

    if old.get(PRESERVE) is True and new.get(PRESERVE) is not True:
        report(path, f'no longer sets {PRESERVE}. This subtree tolerated arbitrary content in the '
                     f'baseline and now prunes or refuses it.')

    # NARROWED BOUNDS. Each pair is (key, the direction that makes the constraint stricter).
    # `minimum` and `minLength` tighten by going UP, `maximum` and `maxLength` by going DOWN; a
    # bound introduced where none existed is a tightening in either direction, which the `is None`
    # arm covers. Written as a table rather than four near-identical blocks because the only thing
    # that differs between them is the comparison, and four hand-written copies is where the
    # inverted one hides.
    for key, stricter_when_greater in (('minimum', True), ('minLength', True),
                                       ('maximum', False), ('maxLength', False)):
        o, n = old.get(key), new.get(key)
        if n is None or not isinstance(n, (int, float)) or isinstance(n, bool):
            continue
        if o is None:
            report(path, f'gained {key} {n} where the baseline had no bound. A stored value '
                         f'outside it can no longer be written.')
        elif isinstance(o, (int, float)) and not isinstance(o, bool) and (
                n > o if stricter_when_greater else n < o):
            report(path, f'narrowed {key} from {o} to {n}. Objects outside the new bound can no '
                         f'longer be written.')

    if new.get('pattern') and new.get('pattern') != old.get('pattern'):
        # A CHANGED pattern is reported, not only an introduced one, because regex containment is
        # undecidable in general: there is no cheap way to tell a widened pattern from a narrowed
        # one, and treating "changed" as safe is the assumption that loses data. The cost is a
        # finding on a genuine widening, which a reviewer can read and wave through.
        report(path, f'changed pattern from {old.get("pattern")!r} to {new.get("pattern")!r}. '
                     f'Whether that admits everything the baseline did cannot be decided here, so '
                     f'it is reported rather than assumed safe.')

    # A PROPERTY REMOVED, which is the worst of the three and the reason it is here. Structural
    # pruning does not REFUSE the write — it silently drops the field and admits the object, so an
    # existing CR carrying it loses that data on its next write with no error anywhere. The
    # exception is a node that tolerates unknown content: there the field was never declared-and-
    # validated to begin with, so removing the declaration changes nothing about what is stored.
    lost = sorted(set(old.get('properties') or {}) - set(new.get('properties') or {}))
    if lost and new.get(PRESERVE) is not True:
        report(path, f'no longer declares {lost}. Structural pruning drops an undeclared field '
                     f'SILENTLY and admits the object, so a stored value is lost on the next '
                     f'write with no error reported anywhere.')

    for key in sorted(set(old.get('properties') or {}) & set(new.get('properties') or {})):
        compare(old['properties'][key], new['properties'][key], f'{path}.{key}', report)
    if isinstance(old.get('items'), dict) and isinstance(new.get('items'), dict):
        compare(old['items'], new['items'], f'{path}[]', report)
    for node in ('additionalProperties',):
        if isinstance(old.get(node), dict) and isinstance(new.get(node), dict):
            compare(old[node], new[node], f'{path}{{*}}', report)


def main():
    findings = 0
    pinned, pin_err = pins()
    if pinned is None:
        print(f'FAIL: {pin_err}')
        print('A gate that cannot read the baseline has not checked anything.')
        return 1

    forced = os.environ.get('PORTAL_BASELINE_VERSION')
    crd_owning = 0
    compared = 0

    for chart in CHARTS:
        new, err = render(chart)
        if new is None:
            print(f'FAIL {chart}: did not render, so its CRDs could NOT be checked: {err}')
            findings += 1
            continue
        if not new:
            continue
        crd_owning += 1

        if forced:
            version, source = forced, 'PORTAL_BASELINE_VERSION'
        elif chart in pinned:
            version, source = pinned[chart], 'installer pin'
        else:
            version = latest_published(chart)
            source = 'latest published (no installer pin)'

        if not version:
            print(f'NOTE {chart}: owns {len(new)} CRD(s) but has no published version to compare '
                  f'against — nothing in the field can be broken by it yet.')
            continue

        old, err = pull(chart, version)
        if old is None:
            print(f'FAIL {chart}: could not read the baseline {version} ({source}): {err}')
            findings += 1
            continue

        for crd_name, versions in sorted(new.items()):
            for ver, schema in sorted(versions.items()):
                base = (old.get(crd_name) or {}).get(ver)
                if base is None:
                    print(f'NOTE {chart}: {crd_name} {ver} is not in baseline {version} — a new '
                          f'CRD or a new stored version has no objects to break.')
                    continue
                compared += 1
                hits = []
                compare(base, schema, f'{crd_name}/{ver}',
                        lambda p, why, _h=hits: _h.append((p, why)))
                for p, why in hits:
                    print(f'FAIL {chart}: {p} {why}')
                    findings += 1

        print(f'  {chart}: baseline {version} ({source})')

    print(f'checked {len(CHARTS)} chart(s); {crd_owning} own a CRD; '
          f'{compared} CRD version(s) compared against a baseline; {findings} finding(s)')
    if crd_owning and compared == 0 and findings == 0:
        print('FAIL: a chart owns a CRD but no version was compared against anything — '
              'this gate checked nothing')
        return 1
    return findings


if __name__ == '__main__':
    sys.exit(main())
