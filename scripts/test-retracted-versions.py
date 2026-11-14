#!/usr/bin/env python3
"""retracted-versions.yaml is well formed, names only this repo's charts, and points at real releases.

installer's CI reads that file and refuses a pin on a listed version. A malformed entry would not fail
there: it would match nothing, and the pin would go through. So the file is checked here, where it is
written, the way krateo-platformops/frontend checks its own (crdgen/retracted_test.go):

  - the top level is `retracted:` and nothing else, and the list is not empty (a parse that reads
    nothing must not pass);
  - each entry has exactly version, charts, use and reason; version and use are plain semver and differ;
  - every chart is one this repo publishes: a `name:` in helm/*/Chart.yaml, read from the tree, not a
    list kept here. A component name, or another repo's chart, would match nothing in installer;
  - (online, the default) the retracted version and `use` are both PUBLISHED for every chart named, by
    `helm show chart` against the registry. Retracting a version nobody can pin is noise, and a `use`
    that does not exist sends the pinning session to a dead end.

Usage: test-retracted-versions.py [--offline]. Exit code = number of failures. Runs its controls first:
an unknown chart, a component name, a bad semver and use == version must each be refused.
"""
import glob
import os
import re
import subprocess
import sys

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCI = 'oci://ghcr.io/krateo-platformops/charts'
SEMVER = re.compile(r'^\d+\.\d+\.\d+$')
KEYS = {'version', 'charts', 'use', 'reason'}


def published_charts():
    names = set()
    for cy in glob.glob(os.path.join(REPO, 'helm', '*', 'Chart.yaml')):
        with open(cy, encoding='utf-8') as fh:
            name = (yaml.safe_load(fh) or {}).get('name')
        if name:
            names.add(name)
    return names


def check(doc, known):
    """Structural failures for a parsed file."""
    if not isinstance(doc, dict) or set(doc) != {'retracted'}:
        return ['the file must hold exactly one key, `retracted`']
    entries = doc['retracted']
    if not isinstance(entries, list) or not entries:
        return ['no entries: a parse that reads nothing must not pass']
    out = []
    for e in entries:
        label = repr(e.get('version')) if isinstance(e, dict) else repr(e)
        if not isinstance(e, dict) or set(e) != KEYS:
            out.append(f'{label}: an entry has exactly {sorted(KEYS)}')
            continue
        if not (isinstance(e['version'], str) and SEMVER.match(e['version'])) or not (isinstance(e['use'], str) and SEMVER.match(e['use'])):
            out.append(f'{label}: version and use must be plain semver strings, got use {e["use"]!r}')
        if e['version'] == e['use']:
            out.append(f'{label}: use names the retracted version itself')
        if not isinstance(e['reason'], str) or not e['reason'].strip():
            out.append(f'{label}: a retraction needs a reason')
        if not isinstance(e['charts'], list) or not e['charts']:
            out.append(f'{label}: names no chart')
            continue
        for chart in e['charts']:
            if chart not in known:
                out.append(f'{label}: {chart!r} is not a chart this repo publishes (a component name, or another repo\'s chart, matches nothing in installer)')
    return out


def published(chart, version):
    r = subprocess.run(['helm', 'show', 'chart', f'{OCI}/{chart}', '--version', version], capture_output=True, text=True)
    return r.returncode == 0


def controls(known):
    good = {'version': '1.8.119', 'charts': ['alerts-page'], 'use': '1.8.120', 'reason': 'x'}
    cases = {
        'an unknown chart': {**good, 'charts': ['not-a-chart']},
        'a component name (alerts, not alerts-page)': {**good, 'charts': ['alerts']},
        'a bad semver': {**good, 'version': 'v1.8.119'},
        'use == version': {**good, 'use': '1.8.119'},
        'a missing reason key': {k: v for k, v in good.items() if k != 'reason'},
    }
    problems = []
    if 'alerts-page' not in known:
        return ['control: alerts-page is not among the discovered charts, so the controls mean nothing']
    if check({'retracted': [good]}, known):
        problems.append('control: a valid entry was refused')
    if not check({'retracted': []}, known):
        problems.append('control: an empty list was accepted')
    for name, entry in cases.items():
        if not check({'retracted': [entry]}, known):
            problems.append(f'control: {name} was NOT refused')
    return problems


def main():
    offline = '--offline' in sys.argv[1:]
    known = published_charts()
    problems = controls(known)
    if problems:
        for p in problems:
            print(f'[FAIL] {p}')
        return len(problems)
    print('[PASS] controls: an unknown chart, a component name, a bad semver, use == version, a missing key and an empty list are refused')
    with open(os.path.join(REPO, 'retracted-versions.yaml'), encoding='utf-8') as fh:
        doc = yaml.safe_load(fh)
    failures = check(doc, known)
    if not failures and not offline:
        for e in doc['retracted']:
            for chart in e['charts']:
                for v, role in ((e['version'], 'retracted version'), (e['use'], 'use')):
                    if not published(chart, v):
                        failures.append(f'{e["version"]}: {chart} {v} ({role}) is not published at {OCI}/{chart}')
    for f in failures:
        print(f'[FAIL] {f}')
    if not failures:
        n = len(doc['retracted'])
        print(f'[PASS] retracted-versions.yaml: {n} entr{"y" if n == 1 else "ies"}, charts from {len(known)} published charts'
              + ('' if offline else ', every version and use published'))
    return min(len(failures), 125)


if __name__ == '__main__':
    sys.exit(main())
