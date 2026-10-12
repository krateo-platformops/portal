#!/usr/bin/env python3
"""composition-events: the per-event deep-link (`route`) survives every shape `apiresources` can take.

This filter had no coverage, and its own comments record why that mattered: a jq ERROR here fails the
stage, continueOnError swallows it, and the feed goes EMPTY for every composition. The kind->plural map
now comes from per-group discovery (/apis/<group>/<version>) instead of a cluster-wide CRD list, and an
iterated stage can hand the final filter a flat array, a nested one, an empty one, or nothing at all.

Run: python3 -I scripts/test-composition-events-routes.py
"""
import json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL = 'templates/restaction.composition-events.yaml'


def rendered_filter() -> str:
    """The final filter as the chart actually ships it (not the template source)."""
    chart = ROOT / 'helm/portal/Chart.yaml'
    original = chart.read_text(encoding='utf-8')
    chart.write_text(original.replace('version: CHART_VERSION', 'version: 0.0.0-test'), encoding='utf-8')
    try:
        out = subprocess.run(['helm', 'template', 'portal', str(ROOT / 'helm/portal'),
                              '--namespace', 'krateo-system', '-s', TPL],
                             capture_output=True, text=True, check=True).stdout
    finally:
        chart.write_text(original, encoding='utf-8')
    import yaml
    return yaml.safe_load(out)['spec']['filter']


def event(api, kind, name, ns):
    return {'metadata': {'name': name + '.1'},
            'involvedObject': {'apiVersion': api, 'kind': kind, 'name': name, 'namespace': ns},
            'reason': 'R', 'message': 'm', 'type': 'Normal', 'source': {},
            'eventTime': '2026-10-12T00:00:00Z', 'compositionId': 'CID'}


EVENTS = [
    event('apps/v1', 'Deployment', 'dep1', 'demo-system'),                        # $builtin
    event('v1', 'Pod', 'pod1', 'demo-system'),                                    # $builtin, core group
    event('composition.krateo.io/v0-1-0', 'EventSite', 'es1', 'demo-system'),     # -> /compositions/
    event('core.krateo.io/v1alpha1', 'CompositionDefinition', 'cd1', 'krateo-system'),  # needs discovery
    event('nope.example.com/v1', 'Ghost', 'g1', 'demo-system'),                   # unresolvable -> inert
]
DISCOVERED = [{'group': 'core.krateo.io', 'kind': 'CompositionDefinition', 'plural': 'compositiondefinitions'},
              {'group': 'composition.krateo.io', 'kind': 'EventSite', 'plural': 'eventsites'}]

RESOLVED = '/resources/krateo-system/core.krateo.io/v1alpha1/compositiondefinitions/cd1'
BASE = ['/resources/demo-system/apps/v1/deployments/dep1',
        '/resources/demo-system/core/v1/pods/pod1',
        '/compositions/demo-system/es1']

# shape of `.apiresources` -> the routes the five events must produce
CASES = {
    'flat':        (DISCOVERED,                       BASE + [RESOLVED, '']),
    'nested':      ([[DISCOVERED[0]], [DISCOVERED[1]]], BASE + [RESOLVED, '']),
    'deep-nested': ([[[DISCOVERED[0]]], [DISCOVERED[1]]], BASE + [RESOLVED, '']),
    # discovery unavailable (403, or no events referencing a group): CRD kinds go inert,
    # everything else still resolves. Degrading is correct; erroring is not.
    'empty':       ([],   BASE + ['', '']),
    'null':        (None, BASE + ['', '']),
    'garbage':     ([{'unexpected': True}, 'string', 7], BASE + ['', '']),
}


def main() -> int:
    if not shutil.which('helm') or not shutil.which('jq'):
        print('SKIP: helm and jq are both required'); return 0
    filt = rendered_filter()
    with tempfile.TemporaryDirectory() as td:
        fp = Path(td) / 'f.jq'; fp.write_text(filt, encoding='utf-8')
        failures = 0
        for name, (shape, want) in CASES.items():
            doc = {'name': 'spring-summit', 'namespace': 'demo-system',
                   'found': [{'name': 'spring-summit', 'namespace': 'demo-system',
                              'uid': 'UID', 'compositionId': 'CID'}],
                   'getEvents': EVENTS, 'apiresources': shape}
            ip = Path(td) / 'in.json'; ip.write_text(json.dumps(doc), encoding='utf-8')
            r = subprocess.run(['jq', '-f', str(fp), str(ip)], capture_output=True, text=True)
            if r.returncode != 0:
                print(f'  [FAIL] {name}: jq errored — the feed would be EMPTY for every composition')
                print('         ' + r.stderr.strip().splitlines()[0]); failures += 1; continue
            got = [e.get('route', '') for e in json.loads(r.stdout)['list']]
            if got != want:
                print(f'  [FAIL] {name}:\n         got  {got}\n         want {want}'); failures += 1
            else:
                print(f'  [PASS] {name}')
        if failures:
            print(f'\n{failures} of {len(CASES)} shapes failed'); return 1
        print(f'\nall {len(CASES)} shapes passed')
        return 0


if __name__ == '__main__':
    sys.exit(main())
