#!/usr/bin/env python3
"""
test-composition-architecture — a composition's topology on its detail page, evaluated end to end.

WHAT IT GUARDS. A composition whose chart ships templates/architecture.yaml shows, on
/compositions/<namespace>/<name>, the chart's resources as a graph, the state the composition is in
and what it waits on. That chain is one chart template, one RESTAction of nine jq programs and five
widgets of jq, run by three different components (Helm in CDC, snowplow's resolver, snowplow's widget
resolver). Nothing in `helm template` evaluates any of it. Each of these was wrong in a draft of this
RESTAction, and read correctly until it met a realistic input:
  - the join between the descriptor and the live objects was on apiVersion, and an object snowplow
    serves from its informer has an EMPTY apiVersion, so every node read as unreadable;
  - a denied object was named from the error's `details`, which snowplow's copy of an apiserver
    Status does not have, so a denial read as "could not be read" instead of "not readable with your
    access";
  - an empty descriptor indexed states[] with a null level and failed the whole resolve.
So this renders the charts in this repo and runs their own jq over krateo-057 objects.

HOW.
  1. builder-publish is rendered the way CDC renders it: the composition's `spec` as values plus the
     `global` block CDC injects (plumbing helm/utils/values.go InjectGlobalValues), under the
     composition's release name. The ConfigMap it produces must name exactly the objects the
     composition's status.managed lists — all of them for a composition that finished, a subset for
     one still being built, since a withheld node has not rendered yet.
  2. That ConfigMap, stamped with CDC's post-renderer labels, joins the composition and its managed
     objects in a fixture cluster (path -> object). Each case then mutates the cluster the way a real
     caller would meet it: an object this caller is denied (as the apiserver's 403, or as snowplow's
     own Forbidden string), one that is gone, one that failed, one the informer served without
     TypeMeta.
  3. helm/portal is rendered and its composition-architecture RESTAction is resolved over the cluster
     by a model of snowplow 1.12.13's resolver (resolvers/restactions/api): the extras seed the dict;
     a stage's iterator runs over the whole dict and its path over each element; a step filter sees
     {extras, <stage>: response}; the first result is stored as-is and later filter-produced arrays
     are spliced; a failed call appends an error under the stage's errorKey — an apiserver error as
     plumbing's response.Status (kind, apiVersion, status, message, reason, code — no details).
     Iterator items run concurrently in snowplow, so every case is resolved twice, in both orders,
     and must give one answer.
  4. Every widget on the topology card is evaluated over each result, and the gate Row over
     composition-detail's own output for each detail fixture.
  5. With --crds DIR (a frontend-crds chart, as `helm pull --untar` leaves it), every widget CR, as
     authored and as resolved, is validated against its CRD, closed the way the apiserver's strict
     field validation closes it. A widget that does not validate is refused by snowplow at render
     time (HTTP 400), which is why this PR can only merge once the FlowChart CRD with `variant` is
     live.

Every jq program in the RESTAction is also kept verbatim in the fixtures directory, and this checks
the copies still equal the chart, so the fixtures can be read, and run by hand, next to the code.

Uses the `jq` binary (preinstalled on GitHub's ubuntu runners). snowplow runs krateo-platformops/gojq;
JQ=<a gojq built against that fork> runs the same checks on it.

Usage: test-composition-architecture.py [--crds DIR] [--update]
  --update rewrites the expected files from this run. Read the diff before committing it.
Exit code = failed checks.
"""
import argparse
import copy
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(REPO, 'helm', 'portal', 'tests', 'composition-architecture')
NS = 'krateo-system'
JQ = os.environ.get('JQ', 'jq')
RA = 'composition-architecture'

# The five widgets that read composition-architecture, and the field each one templates.
WIDGETS = [
    ('Row', 'row-composition-detail-architecture-body', 'items'),
    ('Steps', 'steps-composition-detail-architecture', 'items'),
    ('Steps', 'steps-composition-detail-architecture', 'current'),
    ('FlowChart', 'flowchart-composition-detail-architecture', 'data'),
    ('Descriptions', 'descriptions-composition-detail-architecture-where', 'items'),
]
GATE = ('Row', 'composition-detail-architecture-row', 'items')

# helm/builder-publish/templates/architecture.yaml is not authored here. It is the Blueprint Composer's
# golden for this chart (frontend ui/src/pages/BlueprintComposer/__fixtures__/builder-publish/
# expected.architecture-template.yaml, S11a, frontend 71216cd), copied byte for byte: the composer owns
# the compiled `krateo:graph` block and refuses a descriptor whose block is not byte-equal to its own
# compile (lint L3). A hand edit here would drift from what the composer would write, so it fails
# until the golden is regenerated there and copied again.
COMPOSER_GOLDEN_SHA256 = 'b7c9ec9de8e095d5d925afa2a55bfdfbfcee299b67fdd3d6c416fc4ab8d5efe0'


# ---------------------------------------------------------------------------------------------
# Rendering and evaluation
# ---------------------------------------------------------------------------------------------

def render(chart, release='portal', values=None):
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(os.path.join(REPO, 'helm', chart), staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text.replace('CHART_VERSION', '0.0.0-dev').replace('APP_VERSION', '0.0.0-dev'))
        args = ['helm', 'template', release, staged, '--namespace', NS]
        if values is not None:
            path = os.path.join(tmp, 'values.json')
            with open(path, 'w') as fh:
                json.dump(values, fh)
            args += ['-f', path]
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template {chart} failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def find(docs, kind, name):
    for d in docs:
        if d.get('kind') == kind and (d.get('metadata') or {}).get('name') == name:
            return d
    raise LookupError(f'the chart renders no {kind} {name}')


def query(text):
    """plumbing jqutil.MaybeQuery: the jq between `${` and its matching `}`, or None for a literal."""
    start = text.find('${')
    if start < 0:
        return None
    depth, i = 1, start + 2
    while i < len(text):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[start + 2:i].strip()
        i += 1
    return None


def jq(program, data):
    """Run a jq program; snowplow keeps one value, and a program that yields none or several fails."""
    with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run([JQ, '-c', '-f', path], input=json.dumps(data),
                              capture_output=True, text=True, check=False)
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        raise RuntimeError(f'{JQ} failed: {proc.stderr.strip()}')
    outs = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    if len(outs) != 1:
        raise RuntimeError(f'{JQ} yielded {len(outs)} values, snowplow needs exactly one')
    return outs[0]


# ---------------------------------------------------------------------------------------------
# The RESTAction's programs, as the chart renders them
# ---------------------------------------------------------------------------------------------

def programs(portal):
    ra = find(portal, 'RESTAction', RA)['spec']
    api = {step['name']: step for step in ra['api']}
    progs = {'filter.jq': ra['filter']}
    for name in ('arch', 'comp', 'objs'):
        step = api[name]
        progs[f'{name}.path.jq'] = query(step['path'])
        progs[f'{name}.filter.jq'] = step['filter']
        if step.get('dependsOn'):
            progs[f'{name}.iterator.jq'] = step['dependsOn']['iterator']
    return {k: v if v.endswith('\n') else v + '\n' for k, v in progs.items()}


# ---------------------------------------------------------------------------------------------
# The fixture cluster
# ---------------------------------------------------------------------------------------------

def load(name):
    return json.load(open(os.path.join(FIXTURES, name)))


def cdc_values(comp, resource):
    """The values CDC renders a composition's chart with: its spec, plus the injected globals."""
    group, version = comp['apiVersion'].split('/')
    values = copy.deepcopy(comp.get('spec') or {})
    values['global'] = {
        'compositionName': comp['metadata']['name'],
        'compositionNamespace': comp['metadata']['namespace'],
        'compositionId': comp['metadata']['uid'],
        'compositionApiVersion': comp['apiVersion'],
        'compositionGroup': group,
        'compositionKind': comp['kind'],
        'compositionResource': resource,
        'compositionInstalledVersion': version,
        'gracefullyPaused': 'false',
        'krateoNamespace': NS,
    }
    return values


def architecture_configmap(comp, resource):
    """builder-publish's ConfigMap for this composition, as it lands in the cluster."""
    release = comp['metadata']['labels']['krateo.io/release-name']
    docs = render('builder-publish', release, cdc_values(comp, resource))
    cms = [d for d in docs if d.get('kind') == 'ConfigMap' and
           'krateo.io/architecture' in ((d.get('metadata') or {}).get('labels') or {})]
    if len(cms) != 1:
        raise AssertionError(f'builder-publish rendered {len(cms)} architecture ConfigMaps, want 1')
    cm = cms[0]
    group, version = comp['apiVersion'].split('/')
    meta = cm['metadata']
    # CDC's post-renderer (plumbing helm/utils/postrenderer.go) and Helm's own ownership marks.
    meta['labels'].update({
        'app.kubernetes.io/managed-by': 'Helm',
        'krateo.io/composition-id': comp['metadata']['uid'],
        'krateo.io/composition-group': group,
        'krateo.io/composition-installed-version': version,
        'krateo.io/composition-resource': resource,
        'krateo.io/composition-name': comp['metadata']['name'],
        'krateo.io/composition-namespace': comp['metadata']['namespace'],
        'krateo.io/composition-kind': comp['kind'],
        'krateo.io/krateo-namespace': NS,
    })
    meta['annotations'] = {'meta.helm.sh/release-name': release, 'meta.helm.sh/release-namespace': NS}
    return cm


def gvr_of(path):
    """(group, resource, name) of an apiserver object path."""
    parts = path.strip('/').split('/')
    if parts[0] == 'api':
        return '', parts[-2], parts[-1]
    return parts[1], parts[-2], parts[-1]


def status(path, code):
    """What snowplow keeps of an apiserver error: the body decoded into plumbing's response.Status,
    which has no `details` and no `metadata` (plumbing http/request Do + response.AsMap)."""
    group, resource, name = gvr_of(path)
    gr = f'{resource}.{group}' if group else resource
    if code == 404:
        return {'kind': 'Status', 'apiVersion': 'v1', 'status': 'Failure', 'code': 404, 'reason': 'NotFound',
                'message': f'{gr} "{name}" not found'}
    return {'kind': 'Status', 'apiVersion': 'v1', 'status': 'Failure', 'code': 403, 'reason': 'Forbidden',
            'message': f'{gr} "{name}" is forbidden: User "cyberjoker" cannot get resource "{resource}" '
                       f'in API group "{group}" in the namespace "{NS}"'}


class Failure:
    """A call that fails: `value` is what snowplow appends under the stage's errorKey."""
    def __init__(self, value):
        self.value = value


def cluster_for(fixture):
    comp = load(fixture['composition'])
    cm = architecture_configmap(comp, fixture['resource'])
    group_version = comp['apiVersion']
    cluster = {
        f'/api/v1/namespaces/{NS}/configmaps/{cm["metadata"]["name"]}': cm,
        f'/apis/{group_version}/namespaces/{NS}/{fixture["resource"]}/{comp["metadata"]["name"]}': comp,
    }
    by_name = {o['metadata']['name']: o for o in load(fixture['objects'])}
    for entry in comp['status']['managed']:
        cluster[entry['path']] = copy.deepcopy(by_name[entry['name']])
    return cluster, comp


def mutate(cluster, op):
    """Apply one case mutation; every object in a fixture cluster has a distinct name."""
    def path_of(name):
        hits = [p for p in cluster if p.rsplit('/', 1)[-1] == name]
        if len(hits) != 1:
            raise AssertionError(f'mutation names {name!r}, which is {len(hits)} objects in the cluster')
        return hits[0]

    kind = op['op']
    if kind == 'strip-typemeta':
        # An object snowplow serves from its informer arrives without apiVersion and kind.
        targets = [p for p in cluster if '/configmaps/' in p] if op['target'] == 'configmap' else \
                  [p for p in cluster if '/configmaps/' not in p and not p.startswith('/apis/composition.')][::2]
        for p in targets:
            if not isinstance(cluster[p], Failure):
                cluster[p].pop('apiVersion', None)
                cluster[p].pop('kind', None)
    elif kind == 'deny':
        p = path_of(op['name'])
        if op['as'] == 'status':      # the apiserver's 403, read with the caller's own token
            cluster[p] = Failure(status(p, 403))
        else:                         # snowplow's own re-gate (#256): apierrors.NewForbidden, as a string
            group, resource, name = gvr_of(p)
            gr = f'{resource}.{group}' if group else resource
            cluster[p] = Failure(f'{gr} "{name}" is forbidden: user not authorized to get {NS}/{name}')
    elif kind == 'delete':
        p = path_of(op['name'])
        cluster[p] = Failure(status(p, 404))
    elif kind == 'fail':              # a call that never got an answer: response.New(500, err)
        p = path_of(op['name'])
        cluster[p] = Failure({'kind': 'Status', 'apiVersion': 'v1', 'status': 'Failure', 'code': 500,
                              'reason': 'InternalError',
                              'message': f'Get "https://10.96.0.1:443{p}": context deadline exceeded'})
    elif kind == 'drop':              # served with no error, and nothing in it
        cluster[path_of(op['name'])] = {}
    elif kind == 'label':
        cm = next(o for p, o in cluster.items() if '/configmaps/' in p)
        cm['metadata']['labels'][op['key']] = op['value']
    elif kind == 'graph':
        cm = next(o for p, o in cluster.items() if '/configmaps/' in p)
        graph = json.loads(cm['data']['graph'])
        graph.update(op['set'])
        cm['data']['graph'] = json.dumps(graph)
    else:
        raise AssertionError(f'unknown mutation {kind!r}')


def extras_for(kind, comp):
    if kind == 'page':
        return {'name': comp['metadata']['name'], 'namespace': comp['metadata']['namespace']}
    if kind == 'cdc':      # core-provider apiresolver.go sends these to a CDC apiRef
        return {'compositionName': comp['metadata']['name'],
                'compositionNamespace': comp['metadata']['namespace'],
                'compositionId': comp['metadata']['uid']}
    return {}


# ---------------------------------------------------------------------------------------------
# A model of snowplow 1.12.13's RESTAction resolve
# ---------------------------------------------------------------------------------------------

def store(d, key, value, filtered):
    """handler.go jsonHandlerCore: the first result is stored as-is; later ones append, and a
    filter-produced array is spliced (an empty one contributes nothing)."""
    if key not in d:
        d[key] = value
        return
    got = d[key]
    if not isinstance(got, list):
        got = [got]
    if isinstance(value, list):
        if filtered:
            d[key] = got + value
        else:
            d[key] = got + [value]
    else:
        d[key] = got + [value]


def resolve(ra, extras, cluster, reverse=False):
    d = copy.deepcopy(extras)
    for step in ra['spec']['api']:
        name, it = step['name'], (step.get('dependsOn') or {}).get('iterator')
        if it:
            items = jq(it, d)
            if not isinstance(items, list):
                raise RuntimeError(f'{name}: iterator must return an array, got {type(items).__name__}')
        else:
            items = [d]
        if reverse:
            items = list(reversed(items))
        for item in items:
            expr = query(step['path'])
            path = jq(expr, item) if expr is not None else step['path']
            res = cluster.get(path, Failure(status(path, 404)))
            if isinstance(res, Failure):
                if not step.get('continueOnError'):
                    raise RuntimeError(f'{name}: {path} failed and the stage does not continue on error')
                d.setdefault(step.get('errorKey', 'error'), []).append(res.value)
                continue
            pig = {name: copy.deepcopy(res)}
            if extras:
                pig['extras'] = extras
            value = jq(step['filter'], pig) if step.get('filter') else pig[name]
            store(d, name, value, bool(step.get('filter')))
    return d, jq(ra['spec']['filter'], d)


def widget(portal, kind, name, path, data):
    doc = find(portal, kind, name)
    for entry in doc['spec'].get('widgetDataTemplate') or []:
        if entry.get('forPath') == path:
            return jq(query(entry['expression']), data)
    raise LookupError(f'{kind} {name} has no widgetDataTemplate for {path}')


def widgets_over(portal, out):
    return {f'{kind} {name} {path}': widget(portal, kind, name, path, out) for kind, name, path in WIDGETS}


def detail_for(portal, comp, resource, rendered):
    """composition-detail's own output for this composition, from its own step and final filters."""
    if comp is None:
        return jq(find(portal, 'RESTAction', 'composition-detail')['spec']['filter'],
                  {'name': 'gone', 'namespace': NS, 'crds': [], 'found': []})
    comp = copy.deepcopy(comp)
    if rendered:      # what CDC records once a chart with templates/architecture.yaml has rendered
        cm = f'{comp["metadata"]["name"]}-architecture'
        comp['status']['managed'].append({'apiVersion': 'v1', 'name': cm, 'namespace': NS,
                                          'path': f'/api/v1/namespaces/{NS}/configmaps/{cm}',
                                          'resource': 'configmaps'})
    ra = find(portal, 'RESTAction', 'composition-detail')['spec']
    found = next(s for s in ra['api'] if s['name'] == 'found')
    group, version = comp['apiVersion'].split('/')
    d = {'name': comp['metadata']['name'], 'namespace': comp['metadata']['namespace'],
         'crds': [{'plural': resource, 'version': version, 'kind': comp['kind'], 'group': group,
                   'cdName': 'x', 'cdNamespace': NS}],
         'found': jq(found['filter'], {'found': {'items': [comp]}})}
    return jq(ra['filter'], d)


# ---------------------------------------------------------------------------------------------
# Checks — each returns a list of failure messages
# ---------------------------------------------------------------------------------------------

def expect(failures, label, got, want):
    if got != want:
        failures.append(f'{label}:\n          got  {json.dumps(got, sort_keys=True)[:600]}\n'
                        f'          want {json.dumps(want, sort_keys=True)[:600]}')


def expected_file(name, value, update):
    path = os.path.join(FIXTURES, name)
    if update:
        with open(path, 'w') as fh:
            json.dump(value, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write('\n')
    if not os.path.exists(path):
        raise AssertionError(f'{name} is missing — run with --update, then read what it wrote')
    return json.load(open(path))


def check_programs_are_verbatim(ctx):
    """The .jq files next to the fixtures are the chart's own programs, byte for byte."""
    f = []
    progs = programs(ctx['portal'])
    for name, text in sorted(progs.items()):
        path = os.path.join(FIXTURES, name)
        if ctx['update']:
            with open(path, 'w') as fh:
                fh.write(text)
        if not os.path.exists(path):
            f.append(f'{name} is missing from the fixtures')
        elif open(path).read() != text:
            f.append(f'{name} differs from the RESTAction the chart renders — re-extract it (--update)')
    extra = {os.path.basename(p) for p in glob.glob(os.path.join(FIXTURES, '*.jq'))} - set(progs)
    f += [f'{name} is in the fixtures but not in the RESTAction' for name in sorted(extra)]
    return f


def check_builder_publish_names_what_it_renders(ctx):
    """builder-publish's architecture.yaml is the composer's golden, and, rendered as CDC renders it,
    names exactly the objects the composition manages: all 13 for demo (finished), a superset of
    what v1b manages (its withheld nodes have not rendered yet)."""
    f = []
    template = os.path.join(REPO, 'helm', 'builder-publish', 'templates', 'architecture.yaml')
    expect(f, 'templates/architecture.yaml is the composer golden (sha256)',
           hashlib.sha256(open(template, 'rb').read()).hexdigest(), COMPOSER_GOLDEN_SHA256)
    for key, fixture in ctx['cases']['compositions'].items():
        if 'names' not in fixture:
            continue
        comp = load(fixture['composition'])
        cm = architecture_configmap(comp, fixture['resource'])
        graph = json.loads(cm['data']['graph'])
        name = comp['metadata']['name']
        expect(f, f'{key}: ConfigMap name', cm['metadata']['name'], f'{name}-architecture')
        expect(f, f'{key}: graph shape', (graph.get('v'), graph.get('chart')), (1, 'builder-publish'))
        expect(f, f'{key}: graph composition', graph['composition'], {
            'apiVersion': comp['apiVersion'], 'resource': fixture['resource'], 'name': name,
            'namespace': NS, 'uid': comp['metadata']['uid']})
        names = sorted(n for node in graph['nodes'] if not node.get('lifecycle') for n in node['names'])
        managed = sorted(m['name'] for m in comp['status']['managed'] if m['resource'] != 'configmaps')
        expect(f, f'{key}: node names', names, sorted(fixture['names']))
        if fixture.get('finished'):
            expect(f, f'{key}: node names == status.managed', names, managed)
        else:
            missing = sorted(set(managed) - set(names))
            expect(f, f'{key}: status.managed names the graph does not', missing, [])
    # Nil safety: no source, and no CDC globals (helm template, the composer's preview).
    comp = load(ctx['cases']['compositions']['v1b']['composition'])
    values = cdc_values(comp, 'builderpublishes')
    values.pop('source', None)
    docs = render('builder-publish', 'no-source', values)
    graph = json.loads(next(d for d in docs if d.get('kind') == 'ConfigMap')['data']['graph'])
    repo = next(n for n in graph['nodes'] if n['id'] == 'repo')
    expect(f, 'no source: repo node', (repo['present'], repo['names']), (False, []))
    values.pop('global')
    docs = render('builder-publish', 'no-globals', values)
    cm = next(d for d in docs if d.get('kind') == 'ConfigMap')
    expect(f, 'no globals: ConfigMap falls back to the release name', cm['metadata']['name'], 'no-globals-architecture')
    expect(f, 'no globals: composition coordinates are empty', json.loads(cm['data']['graph'])['composition'],
           {'apiVersion': '', 'resource': '', 'name': '', 'namespace': NS, 'uid': ''})
    return f


def check_resolved_cases(ctx):
    """Each case resolves, in either iterator order, to its expected output."""
    f = []
    ra = find(ctx['portal'], 'RESTAction', RA)
    for case in ctx['cases']['cases']:
        fixture = ctx['cases']['compositions'][case['composition']]
        cluster, comp = cluster_for(fixture)
        for op in case.get('mutate', []):
            mutate(cluster, op)
        extras = extras_for(case['extras'], comp)
        _, out = resolve(ra, extras, copy.deepcopy(cluster))
        _, out_rev = resolve(ra, extras, copy.deepcopy(cluster), reverse=True)
        expect(f, f'{case["name"]}: output depends on iterator order', out_rev, out)
        want = expected_file(f'expected.{case["name"]}.json', out, ctx['update'])
        expect(f, f'{case["name"]}: RESTAction output', out, want)
        ctx['outputs'][case['name']] = out
        got = widgets_over(ctx['portal'], out)
        ctx['widgets'][case['name']] = got
        want = expected_file(f'widgets.{case["name"]}.json', got, ctx['update'])
        expect(f, f'{case["name"]}: widgets', got, want)
    return f


def check_cdc_extras_read_the_same(ctx):
    """CDC's extras (compositionName/compositionNamespace, F5) and the page's give one answer."""
    f = []
    expect(f, 'v1b-cdc-extras vs v1b', ctx['outputs'].get('v1b-cdc-extras'), ctx['outputs'].get('v1b'))
    return f


def check_what_the_page_says(ctx):
    """The resolved example of the spec (§4) and the unreadable rules (§7), by what a person sees."""
    f = []
    w, o = ctx['widgets'], ctx['outputs']
    body = lambda c: [i['resourceRefId'] for i in w[c]['Row row-composition-detail-architecture-body items']]
    graph = 'flex-composition-detail-architecture-graph'
    where = 'descriptions-composition-detail-architecture-where'
    unreadable = 'paragraph-composition-detail-architecture-unreadable'
    nodes = lambda c: {n['uid']: n for n in w[c]['FlowChart flowchart-composition-detail-architecture data']}
    rows = lambda c: {i['label']: i['value'] for i in w[c]['Descriptions descriptions-composition-detail-architecture-where items']}

    # v1b, live: S2 seeding, the Repo is current and NotSynced, the rest waits.
    expect(f, 'v1b: body', body('v1b'), [graph, where])
    expect(f, 'v1b: steps', [s['status'] for s in w['v1b']['Steps steps-composition-detail-architecture items']],
           ['finish', 'process', 'wait', 'wait'])
    expect(f, 'v1b: current step', w['v1b']['Steps steps-composition-detail-architecture current'], 1)
    expect(f, 'v1b: node states', {k: v['state'] for k, v in nodes('v1b').items()},
           {'arch:repository': 'done', 'arch:repo': 'waiting', 'arch:localresources': 'withheld', 'arch:pullrequest': 'withheld'})
    expect(f, 'v1b: repository detail', nodes('v1b')['arch:repository']['detail'], 'default_branch · main')
    expect(f, 'v1b: repo exception', nodes('v1b')['arch:repo'].get('exception', {}).get('label'), 'NotSynced')
    expect(f, 'v1b: no exception on a done node', 'exception' in nodes('v1b')['arch:repository'], False)
    expect(f, 'v1b: withheld detail', nodes('v1b')['arch:pullrequest']['detail'], 'waits for all 10 LocalResource')
    expect(f, 'v1b: where it is', rows('v1b'),
           {'State': 'S2 · seeding', 'Since': o['v1b']['since'], 'Waiting on': 'Repo', 'Next': 'LocalResource'})
    # demo, finished: every step done, the graph alone, no "Where it is".
    expect(f, 'demo: body', body('demo'), [graph])
    expect(f, 'demo: steps', [s['status'] for s in w['demo']['Steps steps-composition-detail-architecture items']],
           ['finish'] * 4)
    expect(f, 'demo: every node done', {v['state'] for v in nodes('demo').values()}, {'done'})
    # §7: what a caller who cannot read something sees.
    expect(f, 'configmap forbidden: body', body('configmap-forbidden'), [unreadable])
    expect(f, 'configmap forbidden: access', o['configmap-forbidden'].get('access'), 'forbidden')
    expect(f, 'composition forbidden: body', body('composition-forbidden'), [unreadable])
    expect(f, 'current node denied (apiserver 403): repo', nodes('v1b-denied-current')['arch:repo']['state'], 'unreadable')
    expect(f, 'current node denied: waiting on', rows('v1b-denied-current')['Waiting on'], 'Repo (not readable with your access)')
    expect(f, 'current node denied: level still known', rows('v1b-denied-current')['State'], 'S2 · seeding')
    expect(f, 'proven node denied (snowplow 403): repository', (nodes('v1b-denied-repository')['arch:repository']['state'],
           nodes('v1b-denied-repository')['arch:repository']['detail']), ('done', 'not readable with your access'))
    expect(f, 'two files denied: localresources', (nodes('demo-denied-two-files')['arch:localresources']['state'],
           nodes('demo-denied-two-files')['arch:localresources']['detail']), ('done', 'not readable with your access'))
    expect(f, 'two files denied: still all ready', body('demo-denied-two-files'), [graph])
    for c in ('v1b-current-dropped', 'v1b-current-failed'):
        expect(f, f'{c}: repo', (nodes(c)['arch:repo']['state'], nodes(c)['arch:repo']['detail']),
               ('unavailable', 'could not be read'))
        expect(f, f'{c}: waiting on', rows(c)['Waiting on'], 'Repo (could not be read)')
    expect(f, 'repo deleted: waiting, not unreadable', nodes('v1b-current-deleted')['arch:repo']['state'], 'waiting')
    expect(f, 'empty descriptor: body draws nothing', body('empty-descriptor'), [])
    for c in ('no-configmap', 'label-mismatch', 'no-extras'):
        expect(f, f'{c}: no architecture', o[c], {'architecture': False, 'access': None})
    return f


def check_gate(ctx):
    """The page's Row shows the card only for a composition whose status.managed lists its
    architecture ConfigMap — so composition-architecture is never called for any other."""
    f = []
    for gate in ctx['cases']['gate']:
        fixture = ctx['cases']['compositions'].get(gate['composition'])
        comp = load(fixture['composition']) if fixture else None
        detail = detail_for(ctx['portal'], comp, fixture['resource'] if fixture else '', gate.get('rendered', False))
        got = widget(ctx['portal'], *GATE, detail)
        expect(f, f'gate {gate["name"]}', got, gate['items'])
        ctx['gates'][gate['name']] = (detail, got)
    return f


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


def check_widget_crs(ctx):
    """Every topology widget CR, as authored and as resolved over every case, validates against the
    frontend-crds CRDs. snowplow refuses a resolved widget that does not (HTTP 400)."""
    if not ctx['crds']:
        return []
    import jsonschema
    f = []
    crds = {}
    for path in glob.glob(os.path.join(ctx['crds'], '**', '*.yaml'), recursive=True):
        for doc in yaml.safe_load_all(open(path)):
            if isinstance(doc, dict) and doc.get('kind') == 'CustomResourceDefinition':
                crds[doc['spec']['names']['kind']] = doc
    names = [n for n in (GATE[1], 'card-composition-detail-architecture', 'flex-composition-detail-architecture-graph',
                         'paragraph-composition-detail-architecture-unreadable')] + sorted({w[1] for w in WIDGETS})
    docs = {d['metadata']['name']: d for d in ctx['portal'] if (d.get('metadata') or {}).get('name') in names}

    def validate(label, doc):
        crd = crds.get(doc['kind'])
        if crd is None:
            f.append(f'{label}: no CRD for kind {doc["kind"]} in {ctx["crds"]}')
            return
        version = doc['apiVersion'].split('/')[1]
        served = [v for v in crd['spec']['versions'] if v['name'] == version]
        if not served:
            f.append(f'{label}: the {doc["kind"]} CRD serves no {version}')
            return
        schema = strict(copy.deepcopy(served[0]['schema']['openAPIV3Schema']))
        schema.setdefault('properties', {}).update({'metadata': {'type': 'object'},
                                                    'apiVersion': {'type': 'string'}, 'kind': {'type': 'string'}})
        for err in sorted(jsonschema.Draft7Validator(schema).iter_errors(doc), key=lambda e: list(e.path)):
            f.append(f'{label}: {"/".join(map(str, err.path))}: {err.message[:200]}')

    checked = 0
    for name, doc in sorted(docs.items()):
        validate(f'authored {doc["kind"]} {name}', doc)
        checked += 1
    for case, got in ctx['widgets'].items():
        for name in sorted({w[1] for w in WIDGETS}):
            doc = copy.deepcopy(docs[name])
            for kind, name2, path in WIDGETS:
                if name2 == name:
                    doc['spec']['widgetData'][path] = got[f'{kind} {name} {path}']
            validate(f'resolved[{case}] {doc["kind"]} {name}', doc)
            checked += 1
    for gate, (_, items) in ctx['gates'].items():
        doc = copy.deepcopy(docs[GATE[1]])
        doc['spec']['widgetData']['items'] = items
        validate(f'resolved[gate {gate}] Row {GATE[1]}', doc)
        checked += 1
    ctx['crd_checks'] = checked
    return f


CHECKS = [
    check_programs_are_verbatim,
    check_builder_publish_names_what_it_renders,
    check_resolved_cases,
    check_cdc_extras_read_the_same,
    check_what_the_page_says,
    check_gate,
    check_widget_crs,
]


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--crds', help='a frontend-crds chart directory, to validate the widget CRs')
    parser.add_argument('--update', action='store_true', help='rewrite the expected files and .jq copies')
    args = parser.parse_args()
    ctx = {'portal': render('portal'), 'cases': load('cases.json'), 'crds': args.crds, 'update': args.update,
           'outputs': {}, 'widgets': {}, 'gates': {}}
    failed = 0
    for check in CHECKS:
        try:
            problems = check(ctx)
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, StopIteration, ImportError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        name = check.__name__.replace('check_', '').replace('_', ' ')
        print(f'[{"FAIL" if problems else "PASS"}] {name}')
        for problem in dict.fromkeys(problems):
            print(f'        {problem}')
        failed += bool(problems)
    cases = len(ctx['cases']['cases'])
    crd = f', {ctx["crd_checks"]} widget CRs validated against {args.crds}' if args.crds and 'crd_checks' in ctx else \
        ', widget CRs NOT validated (no --crds)'
    print(f'\n{len(CHECKS) - failed} of {len(CHECKS)} checks passed ({JQ}; {cases} cases, both iterator orders{crd})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
