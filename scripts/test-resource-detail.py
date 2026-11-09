#!/usr/bin/env python3
"""The resources page's detail subtitle, over the chart's OWN jq.

A forbidden read is not a missing resource. The `object` stage is continueOnError with errorKey
`objectError`, so a 403 left `.object` empty and the subtitle fell through to "Not found" — which
tells the user the resource does not exist when in fact they may not read it. `devs` holds zero
ClusterRoleBindings by design (#261), so that was the normal answer for a non-admin on any
resource outside their namespaces.

Renders helm/resources-page and runs the RESTAction's own filter rather than keeping a copy of the
program here: a test holding its own copy passes while the chart drifts away from it.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART = os.path.join(REPO, 'helm', 'resources-page')
JQ = os.environ.get('JQ', 'jq')


def render(chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', chart, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text.replace('version: CHART_VERSION', 'version: 0.0.0-test'))
        proc = subprocess.run(['helm', 'template', 'r', copy, '--set', 'namespace=krateo-resources'],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def restaction_filter(docs, name):
    for d in docs:
        if d.get('kind') == 'RESTAction' and d.get('metadata', {}).get('name') == name:
            return d['spec']['filter']
    raise AssertionError(f'RESTAction {name} not in the rendered chart')


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


def req(**over):
    """A resource-detail request for a namespaced ConfigMap that the read did not return."""
    base = {
        'group': '', 'version': 'v1', 'plural': 'configmaps',
        'name': 'some-cm', 'namespace': 'other-team',
        'disco': {'resources': [{'name': 'configmaps', 'namespaced': True, 'kind': 'ConfigMap'}]},
        'object': {},
    }
    base.update(over)
    return base


CASES = [
    # THE FIX: a 403 must say so, not claim the resource is absent.
    ('403 forbidden, as an object', req(objectError={'code': 403, 'reason': 'Forbidden'}),
     'Not readable with your access'),
    # AN errorKey CAN HOLD A LIST. An iterated stage records one error per item, and my first
    # version of this test only fed an object — so `.objectError.code` passed here and failed
    # lint-ra-paths, which feeds an EMPTY ARRAY for an absent input. `[] | .code` is a hard jq
    # error, and a jq error in this filter fails the whole resolve: the page would break for
    # everyone, not just for the caller who was denied.
    ('403 forbidden, as a list', req(objectError=[{'code': 403, 'reason': 'Forbidden'}]),
     'Not readable with your access'),
    ('an empty list, which is how an absent error arrives', req(objectError=[]),
     'Not found in namespace other-team'),
    ('a list with no 403 in it', req(objectError=[{'code': 404}, {'code': 500}]),
     'Not found in namespace other-team'),
    # CONTROL: a genuine absence must still read as Not found, or the fix has just relabelled
    # every empty read as a permission problem.
    ('404 absent', req(objectError={'code': 404, 'reason': 'NotFound'}),
     'Not found in namespace other-team'),
    ('no error at all', req(), 'Not found in namespace other-team'),
    # CONTROL: the code decides, not the upstream's self-reported reason (#714) — a 500 that
    # calls itself Forbidden is still a server fault, not an access decision.
    ('500 calling itself Forbidden', req(objectError={'code': 500, 'reason': 'Forbidden'}),
     'Not found in namespace other-team'),
    # CONTROL: a resource the portal does not read keeps its own distinct message.
    ('unfetchable kind', req(plural='secrets',
                             disco={'resources': [{'name': 'secrets', 'namespaced': True, 'kind': 'Secret'}]}),
     'Not read: the portal does not read secrets'),
]


def main():
    program = restaction_filter(render(CHART), 'resource-detail')
    failures = []
    for label, data, want in CASES:
        got = jq(program, data)
        if got.get('subtitle') != want:
            failures.append(f'{label}: subtitle {got.get("subtitle")!r} != {want!r}')
        if got.get('found') is not False:
            failures.append(f'{label}: found should be false when nothing was read')
    for f in failures:
        print(f'FAIL  {f}')
    print(f'{len(CASES) - len(failures)}/{len(CASES)} cases ok')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
