#!/usr/bin/env python3
"""The resources page's detail subtitle and its "Ask Agentiko →" link, over the chart's OWN jq.

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
import urllib.parse

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


def ask(href):
    path, _, query = href.partition('?')
    return path, urllib.parse.parse_qs(query).get('ask', [''])[0]


def found(conds=None, **over):
    """A namespaced Deployment the read DID return."""
    obj = {'apiVersion': 'apps/v1', 'kind': 'Deployment',
           'metadata': {'name': 'orders-api', 'namespace': 'team-alpha', 'creationTimestamp': '2026-11-01T00:00:00Z'},
           'status': {'conditions': conds or []}}
    base = {'group': 'apps', 'version': 'v1', 'plural': 'deployments', 'name': 'orders-api', 'namespace': 'team-alpha',
            'disco': {'resources': [{'name': 'deployments', 'namespaced': True, 'kind': 'Deployment'}]}, 'object': obj}
    base.update(over)
    return base


def ask_checks(docs, program, failures):
    """"Ask Agentiko →": the self-href is rebuilt from the five route extras, the prompt names the
    False conditions, says "cluster-scoped" for a cluster-scoped kind, and there is no link at all
    when the object was not read."""
    def check(label, got, want):
        if got != want:
            failures.append(f'ask: {label}: {got!r} != {want!r}')

    conds = [{'type': 'Available', 'status': 'False', 'reason': 'MinimumReplicasUnavailable'},
             {'type': 'Progressing', 'status': 'True', 'reason': 'NewReplicaSetAvailable'},
             {'type': 'ReplicaFailure', 'status': 'False', 'reason': ''}]
    path, prompt = ask(jq(program, found(conds))['askHref'])
    check('found -> the same page', path, '/resources/team-alpha/apps/v1/deployments/orders-api')
    check('found -> prompt', prompt,
          'Diagnose the Deployment "orders-api" in namespace "team-alpha" (apps/v1): is it healthy? Explain its '
          'conditions (2 of 3 False: Available — MinimumReplicasUnavailable, ReplicaFailure) and how to fix what is wrong.')
    _, prompt = ask(jq(program, found([conds[1]]))['askHref'])
    check('found, nothing False -> said', prompt.endswith(
        'Explain its conditions (none of 1 is False) and whether anything still needs attention.'), True)
    _, prompt = ask(jq(program, found())['askHref'])
    check('found, no conditions -> said', prompt.endswith(
        'It reports no status conditions, so explain what its manifest and status say and whether anything is wrong.'), True)
    # Cluster-scoped: the route's namespace segment is the link builders' literal `cluster`, kept
    # verbatim so the link is THIS page; the prompt says cluster-scoped and names no namespace.
    cr = {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole', 'metadata': {'name': 'viewer'}}
    out = jq(program, {'group': 'rbac.authorization.k8s.io', 'version': 'v1', 'plural': 'clusterroles', 'name': 'viewer',
                       'namespace': 'cluster', 'object': cr,
                       'disco': {'resources': [{'name': 'clusterroles', 'namespaced': False, 'kind': 'ClusterRole'}]}})
    path, prompt = ask(out['askHref'])
    check('cluster-scoped -> the same page', path, '/resources/cluster/rbac.authorization.k8s.io/v1/clusterroles/viewer')
    check('cluster-scoped -> prompt', prompt.startswith(
        'Diagnose the ClusterRole "viewer" (cluster-scoped, rbac.authorization.k8s.io/v1): is it healthy?'), True)
    check('cluster-scoped -> no namespace named', 'namespace' in prompt, False)
    # NOT READ -> no link: absent, forbidden and unfetchable alike.
    for label, data, _want in CASES:
        check(f'{label} -> no link', jq(program, data).get('askHref'), '')

    b = [d for d in docs if d.get('kind') == 'Button' and d['metadata']['name'] == 'resource-detail-ask-agentiko'][0]
    expr = [e for e in b['spec']['widgetDataTemplate'] if e['forPath'] == 'actions'][0]['expression'].strip()[2:-1]
    check('button -> askHref', jq(expr, {'askHref': '/x?ask=y'})['navigate'][0]['path'], '/x?ask=y')
    check('button -> fallback, never a 404', jq(expr, {'askHref': ''})['navigate'][0]['path'], '/compositions')
    check('button -> keyExtras', b['spec']['keyExtras'], ['namespace', 'group', 'version', 'plural', 'name'])
    check('button -> label, intent', (b['spec']['widgetData']['label'], b['spec']['widgetData']['intent']),
          ('Ask Agentiko →', 'agentiko'))
    header = [d for d in docs if d.get('kind') == 'PageHeader' and d['metadata']['name'] == 'resource-detail-page-header'][0]
    check('header -> the button', [i['resourceRefId'] for i in header['spec']['widgetData']['items']],
          ['resource-detail-ask-agentiko'])


def main():
    docs = render(CHART)
    program = restaction_filter(docs, 'resource-detail')
    failures = []
    ask_checks(docs, program, failures)
    for label, data, want in CASES:
        got = jq(program, data)
        if got.get('subtitle') != want:
            failures.append(f'{label}: subtitle {got.get("subtitle")!r} != {want!r}')
        if got.get('found') is not False:
            failures.append(f'{label}: found should be false when nothing was read')
    for f in failures:
        print(f'FAIL  {f}')
    asks = [f for f in failures if f.startswith('ask: ')]
    print(f'{len(CASES) - (len(failures) - len(asks))}/{len(CASES)} cases ok; Ask Agentiko: {len(asks)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
