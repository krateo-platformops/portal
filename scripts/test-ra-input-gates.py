#!/usr/bin/env python3
"""
test-ra-input-gates — a RESTAction step that needs its page's input makes no call without it.

WHAT IT GUARDS. snowplow resolves every widget with NO request extras on its Phase-1 walk
(internal/handlers/dispatchers/phase1_walk.go, "extras=nil at prewarm"). A step whose path is
built from the route params used to dial a half-built path then — `…/builderpublishes/review-`,
`/apis/none.krateo.io/v1/none`, `/api/v1/namespaces//configmaps/-architecture` were measured on
krateo-057 — and continueOnError hid every 404. Each of those steps is now GATED: a
`dependsOn.iterator` that yields nothing when the input is absent, which snowplow turns into zero
requests (resolvers/restactions/api/setup.go:43-62, resolve.go:467-472). See lint-ra-paths.py for
the idiom; that lint checks every RESTAction statically. This checks the gated ones by resolving
them:
  - with no extras (and every input-free step served): no gated step is requested, the final
    filter still evaluates (the page renders its empty state), and every widget reading the
    RESTAction resolves over that output — validated against its CRD with --crds;
  - with the extras a page passes: the gated steps request exactly the paths (and POST bodies)
    listed below — what they requested before the gate, except where a path was wrong (named in
    the case).

HOW. helm/portal and helm/portal-agents are rendered (test-builder-install.py's render) and each
RESTAction is resolved by a model of snowplow's resolver: steps in dependsOn order (sort.go); an
iterator yields its elements, none for a non-array; path, payload and headers are evaluated against
the element; a step filter sees {extras, <stage>: response}; the first response is stored as-is,
later filter-produced arrays are spliced; an unserved request fails, recorded under the errorKey.

Uses the `jq` binary; JQ=gojq runs the same checks on it.
Usage: test-ra-input-gates.py [--crds DIR]. Exit code = failed checks.
"""
import copy
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

NS = tbi.NS


def query(s):
    s = (s or '').strip()
    return s[2:-1] if s.startswith('${') and s.endswith('}') else None


def ordered(steps):
    """snowplow's topologicalSort (resolvers/restactions/api/sort.go): after the step named."""
    names, done, out = {s['name'] for s in steps}, set(), []
    while len(out) < len(steps):
        before = len(out)
        for s in steps:
            dep = (s.get('dependsOn') or {}).get('name') or ''
            if s['name'] not in done and (dep == '' or dep in done or dep not in names):
                done.add(s['name'])
                out.append(s)
        if len(out) == before:
            raise RuntimeError('cyclic dependsOn')
    return out


def store(d, key, value, filtered):
    if key not in d:
        d[key] = value
        return
    got = d[key] if isinstance(d[key], list) else [d[key]]
    d[key] = got + value if (filtered and isinstance(value, list)) else got + [value]


def resolve(ra, extras, responses):
    """(requests, dict, output). `responses` maps a step to its response for any path, or to
    {path: response}; a request with no response fails."""
    d, requests = copy.deepcopy(extras), []
    for step in ordered(ra['spec']['api']):
        name, it = step['name'], (step.get('dependsOn') or {}).get('iterator')
        if it:
            try:
                items = tbi.jq(it, d)
            except RuntimeError as exc:
                # An iterator over an absent upstream key is zero requests, as for an empty one
                # (snowplow support/jq iteration.go IsBenignNilIteration); any other error is a fault.
                if not re.search(r'iterate over:? null', str(exc), re.I):     # jq and gojq wordings
                    raise
                items = []
            items = items if isinstance(items, list) else []
        else:
            items = [d]
        for item in items:
            expr = query(step.get('path'))
            path = tbi.jq(expr, item) if expr is not None else step['path']
            payload = step.get('payload')
            if payload is not None and query(payload) is not None:
                payload = json.loads(tbi.jq(query(payload), item))
            requests.append((name, path, payload))
            resp = responses.get(name)
            if isinstance(resp, dict) and resp and all(k.startswith('/') for k in resp):
                resp = resp.get(path)
            if resp is None:
                if not step.get('continueOnError'):
                    return requests, d, None       # snowplow truncates the resolve (R-3)
                d.setdefault(step.get('errorKey', 'error'), []).append(f'{path}: not found')
                continue
            pig = {name: copy.deepcopy(resp)}
            if extras:
                pig['extras'] = extras
            store(d, name, tbi.jq(step['filter'], pig) if step.get('filter') else resp, bool(step.get('filter')))
    try:
        return requests, d, tbi.jq(ra['spec']['filter'], d)
    except ValueError:       # zero values: snowplow fails the resolve ("yielded no value")
        raise RuntimeError('the final filter yielded no value') from None


# ---------------------------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------------------------

def compdef(name, ns=NS, api=None, resource=None, chart=None):
    cd = {'apiVersion': 'core.krateo.io/v1alpha1', 'kind': 'CompositionDefinition',
          'metadata': {'name': name, 'namespace': ns, 'creationTimestamp': '2026-09-01T10:00:00Z'},
          'spec': {'chart': chart or {'url': 'oci://ghcr.io/krateo-blueprints/charts/' + name, 'version': '0.1.0'}}}
    if api:
        cd['status'] = {'apiVersion': api, 'resource': resource, 'kind': 'KeystoneDemo',
                        'conditions': [{'type': 'Ready', 'status': 'True'}]}
    return cd


CD = compdef('keystone', api='composition.krateo.io/v0-1-0', resource='keystonedemoes')
CD_TENANT = compdef('keystone', ns='krateo-blueprints', api='composition.krateo.io/v0-1-0', resource='keystonedemoes')
CD_UNRECONCILED = compdef('keystone')
UID = '0f5e6c4e-3b1a-4c2d-9e8f-123456789abc'
COMP = {'apiVersion': 'composition.krateo.io/v0-1-0', 'kind': 'KeystoneDemo',
        'metadata': {'name': 'c1', 'namespace': 'team-a', 'uid': UID},
        'status': {'managed': [], 'conditions': []}}
DRAFT = {'data': {'chart.json': json.dumps({'rawTemplates': {'templates/cm.yaml': 'kind: ConfigMap'},
                                            'values': {'a': 1}})}}
EMPTY = {'items': []}

# RESTAction -> {gated: its gated steps, free: responses of its input-free steps,
#                cases: [(label, extras, responses of the gated steps, the gated requests expected)]}
# A request is (step, path) or (step, path, payload).
GATES = {
    'review-proposal': {
        'gated': ['proposal', 'claim'],
        'free': {'all': EMPTY, 'apis': {'groups': []}, 'kinds': [], 'prs': EMPTY,
                 'bp': {'status': {'apiVersion': 'composition.krateo.io/v1-8-55', 'resource': 'builderpublishes'}}},
        'cases': [('named', {'name': 'p-abc'}, {'proposal': {'kind': 'Proposal', 'metadata': {'name': 'p-abc'}, 'spec': {}}}, [
            ('proposal', f'/apis/review.krateo.io/v1alpha1/namespaces/{NS}/proposals/p-abc'),
            ('claim', f'/apis/composition.krateo.io/v1-8-55/namespaces/{NS}/builderpublishes/review-abc')])],
    },
    'review-run': {
        'gated': ['run'],
        'free': {'all': EMPTY, 'apis': {'groups': []}, 'kinds': []},
        'cases': [('named', {'name': 'rr-1'}, {},
                   [('run', f'/apis/review.krateo.io/v1alpha1/namespaces/{NS}/reviewruns/rr-1')])],
    },
    'alert-detail': {
        'gated': ['alert'],
        'free': {'reports': EMPTY},
        'cases': [
            ('named', {'name': 'a1', 'namespace': 'team-a'}, {},
             [('alert', '/apis/observability.krateo.io/v1alpha1/namespaces/team-a/alerts/a1')]),
            ('no namespace: the release namespace, as before', {'name': 'a1'}, {},
             [('alert', f'/apis/observability.krateo.io/v1alpha1/namespaces/{NS}/alerts/a1')])],
    },
    'alert-editdef': {
        'gated': ['alert'],
        'free': {'crd': {'spec': {'versions': [{'name': 'v1alpha1', 'schema': {'openAPIV3Schema': {
            'properties': {'spec': {'type': 'object', 'properties': {}}}}}}]}}},
        'cases': [('named', {'name': 'a1', 'namespace': 'team-a'}, {},
                   [('alert', '/apis/observability.krateo.io/v1alpha1/namespaces/team-a/alerts/a1')])],
    },
    'blueprint-detail': {
        'gated': ['compdef', 'comps'],
        'free': {'catalog': tbi.catalog_configmap()},
        'cases': [
            ('named', {'namespace': NS, 'name': 'keystone'}, {'compdef': CD, 'comps': EMPTY}, [
                ('compdef', f'/apis/core.krateo.io/v1alpha1/namespaces/{NS}/compositiondefinitions/keystone'),
                ('comps', '/apis/composition.krateo.io/v0-1-0/keystonedemoes')]),
            ('unreconciled CD: no instances to list, and no /apis/none.krateo.io/v1/none',
             {'namespace': NS, 'name': 'keystone'}, {'compdef': CD_UNRECONCILED}, [
                ('compdef', f'/apis/core.krateo.io/v1alpha1/namespaces/{NS}/compositiondefinitions/keystone')])],
    },
    'blueprint-formdef': {
        'gated': ['compdef', 'jsonschema', 'crd'],
        'free': {'namespaces': EMPTY},
        'cases': [
            ('named, CD outside the release namespace', {'namespace': 'krateo-blueprints', 'name': 'keystone'},
             {'compdef': CD_TENANT}, [
                ('compdef', '/apis/core.krateo.io/v1alpha1/namespaces/krateo-blueprints/compositiondefinitions/keystone'),
                ('jsonschema', '/api/v1/namespaces/krateo-blueprints/configmaps/keystonedemoes-v0-1-0-jsonschema-configmap'),
                ('crd', '/apis/apiextensions.k8s.io/v1/customresourcedefinitions/keystonedemoes.composition.krateo.io')]),
            ('unreconciled CD: no --jsonschema-configmap, no customresourcedefinitions/.',
             {'namespace': NS, 'name': 'keystone'}, {'compdef': CD_UNRECONCILED}, [
                ('compdef', f'/apis/core.krateo.io/v1alpha1/namespaces/{NS}/compositiondefinitions/keystone')])],
    },
    'fleet-rollout-formdef': {
        'gated': ['compdef'],
        'free': {'targets': EMPTY},
        'cases': [('named', {'namespace': NS, 'name': 'keystone'}, {'compdef': CD}, [
            ('compdef', f'/apis/core.krateo.io/v1alpha1/namespaces/{NS}/compositiondefinitions/keystone')])],
    },
    'upgrade-impact': {
        'gated': ['compdef', 'impact'],
        'free': {},
        'cases': [('named', {'namespace': NS, 'name': 'keystone', 'to': '0.2.0'}, {'compdef': CD, 'impact': {}}, [
            ('compdef', f'/apis/core.krateo.io/v1alpha1/namespaces/{NS}/compositiondefinitions/keystone'),
            ('impact', '/diff', {'base': {'url': 'oci://ghcr.io/krateo-blueprints/charts/keystone', 'version': '0.1.0'},
                                 'head': {'url': 'oci://ghcr.io/krateo-blueprints/charts/keystone', 'version': '0.2.0'},
                                 'values': {}, 'releaseName': 'keystone', 'namespace': NS})])],
    },
    'blueprint-render-draft': {
        'gated': ['draft', 'render'],
        'free': {},
        'cases': [
            ('named', {'namespace': NS, 'name': 'draft-1'}, {'draft': DRAFT, 'render': {'objects': []}}, [
                ('draft', f'/api/v1/namespaces/{NS}/configmaps/draft-1'),
                ('render', '/render', {'rawTemplates': {'templates/cm.yaml': 'kind: ConfigMap'}, 'values': {'a': 1}})]),
            ('a draft with no chart is not rendered (the filter already said so)', {'namespace': NS, 'name': 'draft-1'},
             {'draft': {'data': {}}}, [('draft', f'/api/v1/namespaces/{NS}/configmaps/draft-1')])],
    },
    'component-detail': {
        'gated': ['dep'],
        'free': {'pods': EMPTY, 'events': EMPTY},
        'cases': [('named', {'name': 'snowplow'}, {'dep': {'metadata': {'name': 'snowplow'}}}, [
            ('dep', f'/apis/apps/v1/namespaces/{NS}/deployments/snowplow')])],
    },
    'composition-architecture': {
        'gated': ['arch', 'comp', 'objs'],
        'free': {},
        'cases': [
            ('page extras', {'namespace': 'team-a', 'name': 'c1'}, {},
             [('arch', '/api/v1/namespaces/team-a/configmaps/c1-architecture')]),
            ('CDC extras', {'compositionNamespace': 'team-a', 'compositionName': 'c1'}, {},
             [('arch', '/api/v1/namespaces/team-a/configmaps/c1-architecture')])],
    },
    'composition-reconcile-perf': {
        'gated': ['perf'],
        'free': {},
        'cases': [('named', {'namespace': 'team-a', 'name': 'c1'}, {'perf': {'data': []}}, None)],
    },
    'kog-oas-operations': {
        'gated': ['cm'],
        'free': {'bp': {}},
        'cases': [
            ('named', {'oas': 'github-oas'}, {}, [('cm', f'/api/v1/namespaces/{NS}/configmaps/github-oas')]),
            ('named, namespace', {'oas': 'github-oas', 'ns': 'team-a'}, {},
             [('cm', '/api/v1/namespaces/team-a/configmaps/github-oas')])],
    },
    'resource-detail': {
        'gated': ['disco', 'object'],
        'free': {},
        'cases': [
            ('namespaced core kind', {'group': '', 'version': 'v1', 'plural': 'pods', 'namespace': 'team-a', 'name': 'p1'},
             {'disco': {'resources': [{'name': 'pods', 'namespaced': True, 'kind': 'Pod'}]}},
             [('disco', '/api/v1'), ('object', '/api/v1/namespaces/team-a/pods/p1')]),
            ('cluster-scoped kind: read at the cluster path, whatever namespace the route carried',
             {'group': 'rbac.authorization.k8s.io', 'version': 'v1', 'plural': 'clusterroles', 'namespace': 'x', 'name': 'admin'},
             {'disco': {'resources': [{'name': 'clusterroles', 'namespaced': False, 'kind': 'ClusterRole'}]}},
             [('disco', '/apis/rbac.authorization.k8s.io/v1'),
              ('object', '/apis/rbac.authorization.k8s.io/v1/clusterroles/admin')]),
            ('namespaced kind with no namespace: no /namespaces//', {'group': '', 'version': 'v1', 'plural': 'pods', 'name': 'p1'},
             {'disco': {'resources': [{'name': 'pods', 'namespaced': True, 'kind': 'Pod'}]}},
             [('disco', '/api/v1')])],
    },
    'agent-detail': {
        'gated': ['agent'],
        'free': {'models': EMPTY, 'servers': EMPTY, 'peers': EMPTY, 'routes': EMPTY, 'deployments': EMPTY,
                 'policies': EMPTY},
        'cases': [('named', {'namespace': 'team-a', 'name': 'k8s-agent'}, {}, [
            ('agent', '/apis/kagent.dev/v1alpha2/namespaces/team-a/agents/k8s-agent')])],
    },
}

# composition-detail and the three RESTActions shaped like it: the per-kind LIST now carries the
# route's namespace (it read /namespaces//<plural>, every namespace, before).
for _ra, _gated, _more in (('composition-detail', ['found'], {}),
                           ('composition-editdef', ['found', 'jsonschema'], {}),
                           ('composition-events', ['found', 'getEvents'], {'allcrds': EMPTY}),
                           ('composition-resources', ['found', 'resources'], {})):
    _want = [('found', '/apis/composition.krateo.io/v0-1-0/namespaces/team-a/keystonedemoes')]
    if _ra == 'composition-events':
        _want.append(('getEvents', f'/events?limit=200&composition_id={UID}'))
    if _ra == 'composition-editdef':
        _want.append(('jsonschema', f'/api/v1/namespaces/{NS}/configmaps/keystonedemoes-v0-1-0-jsonschema-configmap'))
    GATES[_ra] = {'gated': _gated, 'free': dict({'crds': {'items': [CD]}}, **_more),
                  'cases': [('named', {'namespace': 'team-a', 'name': 'c1'},
                             {'found': {'items': [COMP]}, 'getEvents': {'events': []}}, _want)]}


# ---------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------

def gated_requests(ra, reqs, gated):
    return [r if r[2] is not None else r[:2] for r in reqs if r[0] in gated]


def check_no_input(charts):
    """No extras: no gated step is requested, the output evaluates, and its widgets resolve."""
    f = []
    for name, spec in sorted(GATES.items()):
        chart = charts[name]
        ra = chart.get('RESTAction', name)
        for extras in ({}, {'name': '', 'namespace': ''}):
            try:
                reqs, _, out = resolve(ra, extras, dict(spec['free']))
            except RuntimeError as exc:
                f.append(f'{name} {extras}: {exc}')
                continue
            got = gated_requests(ra, reqs, spec['gated'])
            if got:
                f.append(f'{name} {extras}: requested {got}')
            if out is None:
                f.append(f'{name} {extras}: the resolve was truncated')
                continue
            for d in chart.docs:
                if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/') and \
                        ((d.get('spec') or {}).get('apiRef') or {}).get('name') == name:
                    try:
                        tbi.resolved_widget(chart, d['kind'], d['metadata']['name'], out, extras,
                                            f'{name} no input: {d["kind"]} {d["metadata"]["name"]}')
                    except RuntimeError as exc:
                        f.append(f'{name} {extras}: {d["kind"]} {d["metadata"]["name"]}: {exc}')
    return f


def check_with_input(charts):
    """With the page's extras: exactly the requests listed, each well-formed."""
    f = []
    for name, spec in sorted(GATES.items()):
        ra = charts[name].get('RESTAction', name)
        for label, extras, responses, want in spec['cases']:
            reqs, _, out = resolve(ra, extras, dict(spec['free'], **responses))
            got = gated_requests(ra, reqs, spec['gated'])
            if want is None:      # a ClickHouse query: one request, naming both inputs
                ok = len(got) == 1 and "%27c1%27" in got[0][1] and "%27team-a%27" in got[0][1]
                if not ok:
                    f.append(f'{name} {label}: {got}')
            elif got != want:
                f.append(f'{name} {label}:\n          got  {got}\n          want {want}')
            if out is None:
                f.append(f'{name} {label}: the resolve was truncated')
    return f


def main():
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        i = args.index('--crds')
        crds_dir = args[i + 1]
        del args[i:i + 2]
    portal = tbi.Chart(tbi.render(os.path.join(HERE, '..', 'helm', 'portal')))
    agents = tbi.Chart(tbi.render(os.path.join(HERE, '..', 'helm', 'portal-agents')))
    charts = {name: (agents if name == 'agent-detail' else portal) for name in GATES}
    failed, total = 0, 0
    for check in (check_no_input, check_with_input):
        total += 1
        try:
            problems = check(charts)
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {check.__name__.replace("check_", "").replace("_", " ")}')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    if crds_dir:
        total += 1
        problems = tbi.validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} resolved widgets validate against their CRDs')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}, {len(GATES)} RESTActions'
          f'{"" if crds_dir else "; resolved widgets NOT validated (no --crds)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
