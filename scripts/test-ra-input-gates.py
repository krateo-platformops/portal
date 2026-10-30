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
Two things snowplow does to the requests are modelled too, because the page broke on them while
this test passed (portal 1.8.56: the composition detail page resolved no composition):
  - the cluster-list collapse (resolvers/restactions/api/cluster_list.go:156): an iterator whose
    FIRST element renders a namespaced LIST (/…/namespaces/<ns>/<plural>) is replaced, for a caller
    who may list that kind cluster-wide, by ONE cluster-scope list of that first element's kind
    (the GVR is derived from element 0 alone, :664-740). Its cell is warm on a running portal
    (compositions-list keeps one per kind), so the collapse is taken;
  - a userAccessFilter keeps only the items in namespaces the caller may read (refilter.go).
Each case runs as an admin (collapse on, every namespace) unless it names a tenant's namespaces.

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


def apiserver_path(path):
    """(prefix, namespace, resource, name) of an apiserver path, None for any other
    (cache.ParseAPIServerPathToDep)."""
    p = (path or '').split('?')[0].rstrip('/').split('/')
    if len(p) < 3 or p[0] != '' or p[1] not in ('api', 'apis'):
        return None
    head = 3 if p[1] == 'api' else 4           # /api/<v> | /apis/<g>/<v>
    prefix, rest = '/'.join(p[:head]), p[head:]
    if not rest:
        return None
    if rest[0] == 'namespaces' and len(rest) >= 3:
        return prefix, rest[1], rest[2], (rest[3] if len(rest) > 3 else '')
    return prefix, '', rest[0], (rest[1] if len(rest) > 1 else '')


def collapsed(step, paths, caller):
    """The requests snowplow makes for an iterator stage's `paths` (cluster_list.go:156, :664)."""
    first = apiserver_path(paths[0]) if paths else None
    if caller['namespaces'] is not None or step.get('endpointRef') or first is None:
        return paths
    prefix, ns, resource, name = first
    if ns == '' or name != '':                 # already cluster-scope, or a GET by name
        return paths
    return [f'{prefix}/{resource}']


ADMIN = {'namespaces': None}


def tenant(grants):
    """A caller who may list each API group only in the namespaces given: {group: [namespace]}."""
    return {'namespaces': {g: set(ns) for g, ns in grants.items()}}


def refiltered(step, resp, caller):
    """userAccessFilter: the items in namespaces where the caller may read the filter's group."""
    uaf = step.get('userAccessFilter')
    if not uaf or caller['namespaces'] is None or not isinstance(resp, dict):
        return resp
    allowed = caller['namespaces'].get(uaf.get('group'), set())
    return dict(resp, items=[i for i in resp.get('items') or []
                             if (i.get('metadata') or {}).get('namespace') in allowed])


def resolve(ra, extras, responses, caller=ADMIN):
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
        calls = []
        for item in items:
            expr = query(step.get('path'))
            path = tbi.jq(expr, item) if expr is not None else step['path']
            payload = step.get('payload')
            if payload is not None and query(payload) is not None:
                payload = json.loads(tbi.jq(query(payload), item))
            calls.append((path, payload))
        if it and (step.get('verb') or 'GET') == 'GET':
            calls = [(p, None) for p in collapsed(step, [p for p, _ in calls], caller)]
        for path, payload in calls:
            requests.append((name, path, payload))
            resp = responses.get(name)
            if isinstance(resp, dict) and resp and all(k.startswith('/') for k in resp):
                resp = resp.get(path)
            if resp is None:
                if not step.get('continueOnError'):
                    return requests, d, None       # snowplow truncates the resolve (R-3)
                d.setdefault(step.get('errorKey', 'error'), []).append(f'{path}: not found')
                continue
            pig = {name: refiltered(step, copy.deepcopy(resp), caller)}
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
CONTROLLER_DRAFT = {'data': {'draft.json': json.dumps({'restDefinitions': [{'kind': 'RestDefinition'}],
                                                          'oas': {'configmap://ns/cm/k': 'openapi: 3.0.0'}})}}
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
    'controller-render-draft': {
        'gated': ['draft', 'render'],
        'free': {},
        'cases': [
            ('named', {'namespace': NS, 'name': 'ctl-1'}, {'draft': CONTROLLER_DRAFT, 'render': {'crds': []}}, [
                ('draft', f'/api/v1/namespaces/{NS}/configmaps/ctl-1'),
                ('render', '/render', {'restDefinitions': [{'kind': 'RestDefinition'}], 'oas': {'configmap://ns/cm/k': 'openapi: 3.0.0'}})]),
            ('a draft with no RestDefinition is not rendered (the filter already said so)', {'namespace': NS, 'name': 'ctl-1'},
             {'draft': {'data': {}}}, [('draft', f'/api/v1/namespaces/{NS}/configmaps/ctl-1')])],
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

# composition-detail and the three RESTActions shaped like it find the composition by listing every
# composition kind. The fixture is what snowplow serves on a running portal: more than one kind, the
# composition's own NOT first (on krateo-057 it is one of 46), a composition of the same name in
# another namespace, and every list readable cluster-wide by an admin and per namespace by a tenant.
# 1.8.56 listed each kind per namespace; the collapse turned that into one list of the first kind,
# and the page found nothing while the requests this test checked were all generated.
CD_OTHER = compdef('aaa-other', api='composition.krateo.io/v0-0-9', resource='agentgatewaycontrollers')
CD_OTHER['status']['kind'] = 'AgentgatewayController'
OTHER = {'apiVersion': 'composition.krateo.io/v0-0-9', 'kind': 'AgentgatewayController',
         'metadata': {'name': 'gw', 'namespace': NS, 'uid': '11111111-2222-4333-8444-555555555555'}}
MANAGED = '/api/v1/namespaces/team-a/configmaps/c1-values'
COMP_MANAGED = dict(COMP, status={'managed': [{'apiVersion': 'v1', 'resource': 'configmaps', 'name': 'c1-values',
                                                'path': MANAGED}], 'conditions': []})
TWIN = {'apiVersion': COMP['apiVersion'], 'kind': COMP['kind'],
        'metadata': {'name': 'c1', 'namespace': 'team-b', 'uid': '99999999-8888-4777-8666-555555555555'},
        'status': {'managed': [{'path': '/api/v1/namespaces/team-b/configmaps/c1-values'}], 'conditions': []}}
FOUND = {
    '/apis/composition.krateo.io/v0-0-9/agentgatewaycontrollers': {'items': [OTHER]},
    f'/apis/composition.krateo.io/v0-0-9/namespaces/{NS}/agentgatewaycontrollers': {'items': [OTHER]},
    '/apis/composition.krateo.io/v0-0-9/namespaces/team-a/agentgatewaycontrollers': EMPTY,
    '/apis/composition.krateo.io/v0-1-0/keystonedemoes': {'items': [COMP_MANAGED, TWIN]},
    '/apis/composition.krateo.io/v0-1-0/namespaces/team-a/keystonedemoes': {'items': [COMP_MANAGED]},
}
LISTS = [('found', '/apis/composition.krateo.io/v0-0-9/agentgatewaycontrollers'),
         ('found', '/apis/composition.krateo.io/v0-1-0/keystonedemoes')]


def found_subject(ra, extras, out, caller=ADMIN):
    """composition-detail's output is the composition the extras name, or nothing when the caller may
    not read it; the other three show what they found in their requests."""
    if ra != 'composition-detail':
        return None
    subject = next((c for c in (COMP_MANAGED, TWIN) if c['metadata']['namespace'] == extras['namespace']), None)
    if caller['namespaces'] is not None and \
            extras['namespace'] not in caller['namespaces'].get('composition.krateo.io', set()):
        subject = None
    meta, gvr = (out.get('detail') or {}).get('metadata') or {}, out.get('gvr') or {}
    want = (subject['metadata']['uid'], 'keystonedemoes', 'keystone') if subject else (None, '', '')
    if (meta.get('uid'), gvr.get('resource'), out.get('blueprintName')) != want:
        return f'resolved {meta.get("namespace")}/{meta.get("name")} {meta.get("uid")}, gvr {gvr}'
    return None


TENANT = tenant({'core.krateo.io': [NS], 'composition.krateo.io': ['team-a']})
for _ra, _gated, _more in (('composition-detail', ['found'], {}),
                           ('composition-editdef', ['found', 'jsonschema'], {}),
                           ('composition-events', ['found', 'getEvents'], {'allcrds': EMPTY}),
                           ('composition-resources', ['found', 'resources'], {})):
    _then = []
    if _ra == 'composition-events':
        _then.append(('getEvents', f'/events?limit=200&composition_id={UID}'))
    if _ra == 'composition-editdef':
        _then.append(('jsonschema', f'/api/v1/namespaces/{NS}/configmaps/keystonedemoes-v0-1-0-jsonschema-configmap'))
    if _ra == 'composition-resources':
        _then.append(('resources', MANAGED))
    _then_b = [(s, p.replace(UID, TWIN['metadata']['uid']).replace('team-a', 'team-b')) for s, p in _then]
    _then_none = [(s, '/events?limit=200') for s, _ in _then if s == 'getEvents']
    _responses = {'found': FOUND, 'getEvents': {'events': []},
                  'resources': {MANAGED: {}, MANAGED.replace('team-a', 'team-b'): {}}}
    GATES[_ra] = {'gated': _gated, 'free': dict({'crds': {'items': [CD_OTHER, CD]}}, **_more),
                  'check': found_subject,
                  'cases': [('named, as an admin', {'namespace': 'team-a', 'name': 'c1'}, _responses, LISTS + _then),
                            ('named, as a tenant of team-a', {'namespace': 'team-a', 'name': 'c1'}, _responses,
                             LISTS + _then, TENANT),
                            ("another namespace's composition of that name, as an admin",
                             {'namespace': 'team-b', 'name': 'c1'}, _responses, LISTS + _then_b),
                            ("another namespace's composition of that name, as a tenant of team-a: not read",
                             {'namespace': 'team-b', 'name': 'c1'}, _responses, LISTS + _then_none, TENANT)]}


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
    """With the page's extras: exactly the requests listed, each well-formed, and what they find is
    the page's subject."""
    f = []
    for name, spec in sorted(GATES.items()):
        ra = charts[name].get('RESTAction', name)
        for label, extras, responses, want, *caller in spec['cases']:
            reqs, _, out = resolve(ra, extras, dict(spec['free'], **responses), *caller)
            got = gated_requests(ra, reqs, spec['gated'])
            if want is None:      # a ClickHouse query: one request, naming both inputs
                ok = len(got) == 1 and "%27c1%27" in got[0][1] and "%27team-a%27" in got[0][1]
                if not ok:
                    f.append(f'{name} {label}: {got}')
            elif got != want:
                f.append(f'{name} {label}:\n          got  {got}\n          want {want}')
            if out is None:
                f.append(f'{name} {label}: the resolve was truncated')
            elif spec.get('check') and spec['check'](name, extras, out, *caller):
                f.append(f'{name} {label}: {spec["check"](name, extras, out, *caller)}')
    return f


def check_degraded_reads(charts):
    """A read the page cannot do still resolves to something it can render (a filter with no value
    fails the whole resolve): alert-editdef with its CRD read failing is a form that says why."""
    f = []
    chart = charts['alert-editdef']
    ra = chart.get('RESTAction', 'alert-editdef')
    extras = {'name': 'a1', 'namespace': 'team-a'}
    alert = {'metadata': {'name': 'a1', 'namespace': 'team-a'}, 'spec': {'displayName': 'x'}}
    for label, crd in (('CRD read failed', None), ('CRD serves no v1alpha1', {'spec': {'versions': [{'name': 'v2'}]}})):
        responses = {'alert': alert} if crd is None else {'alert': alert, 'crd': crd}
        try:
            _, _, out = resolve(ra, extras, responses)
        except RuntimeError as exc:
            f.append(f'alert-editdef, {label}: {exc}')
            continue
        schema = (out or {}).get('schemaSpec') or {}
        if schema.get('title') != 'The Alert schema could not be read' or not schema.get('description'):
            f.append(f'alert-editdef, {label}: schemaSpec {schema}')
        if out.get('values') != alert['spec'] or out.get('gvr', {}).get('name') != 'a1':
            f.append(f'alert-editdef, {label}: values/gvr {out.get("values")} {out.get("gvr")}')
        tbi.resolved_widget(chart, 'Form', 'alert-edit', out, extras, f'alert-editdef, {label}: Form alert-edit')
    return f



def nav_namespaces(*names):
    """A /api/v1/namespaces LIST response. The UAF prunes it by .metadata.namespace, so each
    namespace object carries its own name there as well as in .name (refilter.go reads the
    namespace of the ITEM; for a Namespace object the two are the same thing)."""
    return {'items': [{'metadata': {'name': n, 'namespace': n}} for n in names]}


def nav_flexes(chart, extra=()):
    """The page roots a LIST of flexes returns, per namespace: every nav-declaring Flex the chart
    renders (so adding a page to the chart extends this test's input), plus any `extra` roots
    planted in another namespace as a page chart installed there would be."""
    out = {}
    for d in chart.docs:
        if d.get('kind') != 'Flex' or not d.get('apiVersion', '').startswith('widgets.templates.krateo.io/'):
            continue
        md = d['metadata']
        if not ((md.get('annotations') or {}).get('krateo.io/nav-path') or ''):
            continue
        out.setdefault(md['namespace'], []).append({'metadata': {
            'name': md['name'], 'namespace': md['namespace'],
            'annotations': md.get('annotations') or {}}})
    for ns, name, annotations in extra:
        out.setdefault(ns, []).append({'metadata': {
            'name': name, 'namespace': ns, 'annotations': annotations}})
    return out


NAV_GROUP = 'widgets.templates.krateo.io'
# The builders' scratch namespace (previewSandbox.namespace). Never a page source — see the
# `the preview sandbox is never listed` case.
NS_SANDBOX = 'krateo-preview'
NAV_PATH = '/apis/widgets.templates.krateo.io/v1beta1/namespaces/%s/flexes'
NAV_CLUSTER_PATH = '/apis/widgets.templates.krateo.io/v1beta1/flexes'


def check_nav_discovery(charts):
    """THE SIDEBAR IS THE ONE PAGE THERE IS NO FALLING BACK FROM.

    sidebar-nav no longer takes its namespaces from values (#261 removed the `tiers` trio): it
    discovers them, through snowplow's userAccessFilter, as the namespaces where THE CALLER may
    list page roots. That makes the whole sidebar depend on one UAF stage — a path with live
    defects (snowplow#777, #659) — so every way that stage can go is checked here:

      - a caller who may read only the release namespace gets exactly what the pre-#261 nav gave
        them (the regression gate: the single-namespace install must be unchanged);
      - a page root installed into a SECOND namespace reaches the sidebar, with its own namespace
        in `refs` (the thing the tiers model could never do: a fourth namespace was invisible);
      - the namespaces stage FAILING leaves the release namespace's pages, not a blank sidebar
        (the `if length > 0 then . else [release] end` fallback in the iterator);
      - a caller who may read page roots NOWHERE gets an empty sidebar and not a failed resolve
        (an unserved LIST is an errorKey, and the final filter must still yield its one value);
      - an admin, for whom snowplow replaces the fan-out with ONE cluster-scope list
        (cluster_list.go:156 — gate 5 permits it only for a caller who may list cluster-wide),
        sees the union, which for that caller is what the per-namespace walk would have returned.
    """
    f = []
    chart = charts['blueprint-formdef']            # the portal chart
    ra = chart.get('RESTAction', 'sidebar-nav')
    other = ('team-a', 'page-team-a-thing', {'krateo.io/nav-path': '/team-a-thing',
                                             'krateo.io/nav-label': 'Team A thing',
                                             'krateo.io/nav-order': '500'})
    roots = nav_flexes(chart, extra=[other])
    # HOME is the namespace holding most nav roots. It used to be the release namespace, because
    # every page lived with the shell; after #261 the shell renders NO nav root at all and each
    # page has its own namespace, so the check derives its subject rather than assuming one. The
    # assertion is unchanged in substance: a caller who may read exactly one page namespace sees
    # exactly that namespace's pages.
    HOME = max((ns for ns in roots if ns != other[0]), key=lambda n: len(roots[n]))
    serve = {ns: {'items': items} for ns, items in roots.items()}
    everywhere = {'items': [i for items in roots.values() for i in items]}
    cluster_ns = nav_namespaces(*sorted(set(roots) | {NS, 'team-a', 'kube-system'}))

    def run(label, caller, ns_resp, pages):
        try:
            reqs, _, out = resolve(ra, {}, {'namespaces': ns_resp, 'pages': pages}
                                   if ns_resp is not None else {'pages': pages}, caller)
        except RuntimeError as exc:
            f.append(f'sidebar-nav, {label}: {exc}')
            return None, None, None
        if out is None:
            # snowplow truncated the resolve (a step with no continueOnError whose call was not
            # served): the nav resolves to NOTHING, which is the blank sidebar this all exists to
            # prevent. Never a skipped assertion.
            f.append(f'sidebar-nav, {label}: the resolve was truncated — the sidebar is blank')
        return reqs, None, out

    nowhere = tenant({NAV_GROUP: []})

    _, _, base = run('one page namespace only', tenant({NAV_GROUP: [HOME]}), cluster_ns,
                      {NAV_PATH % HOME: serve[HOME]})
    if base is not None:
        if not base.get('items') or not base.get('refs'):
            f.append(f'sidebar-nav, one page namespace only: {len(base.get("items") or [])} items, '
                     f'{len(base.get("refs") or [])} refs — the sidebar is empty')
        if any(r['namespace'] != HOME for r in base.get('refs') or []):
            f.append(f'sidebar-nav, one page namespace only: a ref outside {HOME}')
        if any(i.get('path') == other[2]['krateo.io/nav-path'] for i in base.get('items') or []):
            f.append('sidebar-nav, one page namespace only: served a page from a namespace the '
                     'caller cannot read')

    reqs, _, out = run('two namespaces', tenant({NAV_GROUP: [HOME, 'team-a']}), cluster_ns,
                       {NAV_PATH % ns: serve[ns] for ns in (HOME, 'team-a')})
    if out is not None:
        paths = sorted(p for n, p, _ in reqs if n == 'pages')
        if paths != sorted(NAV_PATH % ns for ns in (HOME, 'team-a')):
            f.append(f'sidebar-nav, two namespaces: listed {paths}')
        if not any(i.get('path') == '/team-a-thing' for i in out['items']):
            f.append('sidebar-nav, two namespaces: the page installed in team-a is not in the nav')
        if {'id': other[1], 'name': other[1], 'namespace': 'team-a'} not in out['refs']:
            f.append("sidebar-nav, two namespaces: team-a's root has no ref carrying its namespace")
        if base is not None and len(out['items']) != len(base['items']) + 1:
            f.append(f'sidebar-nav, two namespaces: {len(out["items"])} items, expected '
                     f'{len(base["items"]) + 1}')

    # THE RELEASE-NAMESPACE FALLBACK IS NOW VESTIGIAL, AND THAT IS THE POINT OF THIS CASE.
    #
    # sidebar-nav's iterator falls back to the RELEASE namespace when the userAccessFilter stage
    # yields nothing (1.8.75): `if length > 0 then . else [release] end`. That was a real safety
    # net while the pages lived with the shell — a snowplow-side failure cost the pages in other
    # namespaces and left the release namespace's.
    #
    # After #261 the shell renders NO nav root at all. So the fallback now lists a namespace with
    # no pages in it, and a userAccessFilter failure means an EMPTY SIDEBAR rather than a reduced
    # one. There is no longer a single namespace worth falling back to, because the pages are
    # distributed by design.
    #
    # This asserts the real behaviour rather than the old intent: the fallback still dials exactly
    # the release namespace (so the stage is still gated and still makes one well-formed request,
    # which is what lint-ra-paths cares about) and the result is empty. If someone later gives the
    # nav a better degraded mode, this case should fail and be rewritten — it is the record of a
    # known, accepted gap, not of desired behaviour.
    reqs, _, out = run('the namespaces stage failed', tenant({NAV_GROUP: [HOME]}), None,
                       {NAV_PATH % HOME: serve[HOME]})
    if out is not None:
        if [p for n, p, _ in reqs if n == 'pages'] != [NAV_PATH % NS]:
            f.append(f'sidebar-nav, the namespaces stage failed: the fallback dialled '
                     f'{[p for n, p, _ in reqs if n == "pages"]}, not the release namespace')
        if out.get('items') or out.get('refs'):
            f.append(f'sidebar-nav, the namespaces stage failed: expected an EMPTY sidebar (the '
                     f'release namespace holds no page since #261) but got '
                     f'{len(out["items"])} items')

    # DENIED, not empty: the fallback still dials the release namespace and the apiserver refuses
    # it (`pages` carries no userAccessFilter — it is the caller's own LIST). A path-keyed
    # `responses` with no entry for that path is how this model spells a request nobody served.
    _, _, out = run('no namespace is readable', nowhere, cluster_ns, {'/served-to-nobody': {}})
    if out is not None and (out.get('items') or out.get('refs')):
        f.append(f'sidebar-nav, no namespace is readable: {out}')

    # THE PREVIEW SANDBOX IS NOT A PAGE SOURCE. 1.8.75 listed it and admins got ten scratch pages in
    # the sidebar on krateo-057 — Demo Day, Pod sizing, Probe and SEVEN entries labelled `Event
    # sites`, every one a `page-…-<hash>` preview CR in krateo-preview. Only admins saw them,
    # because only admins may list flexes there: discovery worked as designed and the design was
    # wrong. The fixture is shaped like what was actually there, duplicate label included, because
    # the duplicate is what made it obvious in the UI and a single entry would not have.
    sandbox = NS_SANDBOX
    sb_roots = [(sandbox, f'page-dry-event-sites-{i}-b2da9489',
                 {'krateo.io/nav-path': f'/dry-event-sites-{i}', 'krateo.io/nav-label': 'Event sites',
                  'krateo.io/nav-order': '100'}) for i in range(1, 6)]
    sb_roots.append((sandbox, 'page-probe-ds-b2da9489',
                     {'krateo.io/nav-path': '/probe-ds', 'krateo.io/nav-label': 'Probe',
                      'krateo.io/nav-order': '100'}))
    sb = nav_flexes(chart, extra=[other] + sb_roots)
    _, _, out = run('the preview sandbox is never listed', tenant({NAV_GROUP: [HOME, sandbox]}),
                    nav_namespaces(HOME, sandbox),
                    {NAV_PATH % ns: {'items': sb[ns]} for ns in sb})
    if out is not None:
        leaked = [i for i in out['items'] if (i.get('path') or '').startswith(('/dry-event-sites', '/probe-ds'))]
        if leaked:
            f.append(f'sidebar-nav, the preview sandbox is never listed: {len(leaked)} scratch '
                     f'page(s) reached the nav: {[i.get("path") for i in leaked]}')
        if any(r['namespace'] == sandbox for r in out['refs']):
            f.append('sidebar-nav, the preview sandbox is never listed: a ref in the sandbox')
        if base is not None and len(out['items']) != len(base['items']):
            f.append(f'sidebar-nav, the preview sandbox is never listed: {len(out["items"])} items, '
                     f'expected {HOME}\'s {len(base["items"])}')

    reqs, _, out = run('admin (cluster-list collapse)', ADMIN, cluster_ns,
                       {NAV_CLUSTER_PATH: everywhere})
    if out is not None:
        paths = [p for n, p, _ in reqs if n == 'pages']
        if paths != [NAV_CLUSTER_PATH]:
            f.append(f'sidebar-nav, admin: expected one cluster-scope list, got {paths}')
        if not any(i.get('path') == '/team-a-thing' for i in out['items']):
            f.append('sidebar-nav, admin: the collapse lost the page installed in team-a')
        # NOT base+1 any more: /incidents, /alerts and /marketplace are their own charts in their
        # own namespaces (#261), so the union an admin can read legitimately carries their roots
        # too. Assert the RELATIONSHIP rather than a count — everything the release namespace
        # offers, plus every root in another namespace, and nothing invented.
        if base is not None:
            base_paths = {i.get('path') for i in base['items']}
            got_paths = {i.get('path') for i in out['items']}
            missing = base_paths - got_paths
            if missing:
                f.append(f'sidebar-nav, admin: lost {sorted(missing)[:4]} that the release '
                         f'namespace alone offers')
            want_other = {(a or {}).get('krateo.io/nav-path')
                          for ns, _n, a in [other] if ns != NS}
            if not want_other <= got_paths:
                f.append(f'sidebar-nav, admin: missing the out-of-namespace page(s) '
                         f'{sorted(want_other - got_paths)}')
            # A root contributes its nav-path AND every comma-separated krateo.io/nav-alias, so
            # the legitimate set is both. (A divider has no path and drops out with the None.)
            declared = set()
            for items in roots.values():
                for r in items:
                    a = r['metadata']['annotations'] or {}
                    declared.add(a.get('krateo.io/nav-path'))
                    declared |= {x for x in (a.get('krateo.io/nav-alias') or '').split(',') if x}
            invented = got_paths - declared - {None}
            if invented:
                f.append(f'sidebar-nav, admin: invented path(s) {sorted(invented)[:4]}')
    return f


def check_edit_form_loads_field_options(charts):
    """The composition Edit form reads the same values.schema.json as the create form, so a field
    with `autocomplete` needs the same GET ref there: composition-editdef emits one per source
    (a bare name in the CompositionDefinition's namespace), and form.composition-edit iterates it."""
    chart = charts['composition-editdef']
    ra = chart.get('RESTAction', 'composition-editdef')
    schema = {'type': 'object', 'properties': {
        'region': {'type': 'string', 'autocomplete': {'resourceRefId': 'regions'}},
        'ns': {'type': 'string', 'autocomplete': {'resourceRefId': f'{NS}/form-options-namespaces'}},
        'bad': {'type': 'string', 'autocomplete': {'resourceRefId': 'Not A Name!'}}}}
    responses = {'crds': {'items': [CD_OTHER, CD]}, 'found': FOUND,
                 'jsonschema': {'data': {'values.schema.json': json.dumps(schema)}}}
    _, _, out = resolve(ra, {'namespace': 'team-a', 'name': 'c1'}, responses)
    problems = []
    want = [{'id': f'{NS}/form-options-namespaces', 'name': 'form-options-namespaces', 'namespace': NS},
            {'id': 'regions', 'name': 'regions', 'namespace': NS}]
    if out.get('optionRefs') != want:
        problems.append(f'optionRefs: {out.get("optionRefs")!r}, want {want!r}')
    form = chart.get('Form', 'composition-edit')
    iterators = [e for e in form['spec'].get('resourcesRefsTemplate') or [] if 'optionRefs' in (e.get('iterator') or '')]
    if len(iterators) != 1 or iterators[0]['template'].get('verb') != 'GET' or iterators[0]['template'].get('resource') != 'restactions':
        problems.append('form.composition-edit has no GET restactions ref per optionRefs entry')
    return problems


def main():
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        i = args.index('--crds')
        crds_dir = args[i + 1]
        del args[i:i + 2]
    portal = tbi.Chart(tbi.render_portal_and_pages())
    agents = tbi.Chart(tbi.render(os.path.join(HERE, '..', 'helm', 'portal-agents')))
    charts = {name: (agents if name == 'agent-detail' else portal) for name in GATES}
    failed, total = 0, 0
    for check in (check_no_input, check_with_input, check_degraded_reads, check_nav_discovery,
                  check_edit_form_loads_field_options):
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
