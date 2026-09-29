#!/usr/bin/env python3
"""
test-builder-install — the Install step of the builder publish chain, evaluated end to end.

WHAT IT GUARDS. A chart published from the Portal or Blueprint builder becomes installable when a
person opens its row on /portal-builder or /blueprint-builder and submits the install form at
/marketplace/<chart>/install. That path is four RESTActions and three widgets of jq, and every
defect found in it so far was a jq branch nobody had evaluated:
  - a builder claim that shared a helm-index name re-pointed that index chart's official
    marketplace card at the claim's chart;
  - a publish with no registration file was offered Install, and the form then opened on a
    helm-index chart of the same name;
  - rows sent people to the form while nothing on the form linked back to the change request
    they were told to check;
  - a page set was told to "Register" when its merge had released nothing;
  - the same merged PR wore a different colour on each list;
  - the row said "Register" and the page it opened said "Install";
  - the install header read a lone "v" for every chart outside the helm index.
Each is pinned below by what the person would see, on synthetic fixtures shaped like the
krateo-057 objects they were found on.

The same builder pages also list drafts: "Your drafts" (the caller's draft records, owned by the
server-injected username sanitized exactly as the frontend kernel's draftOwner) and "Unowned
drafts" (page previews from before drafts had owners). Those RESTActions are pinned here too — who
sees which rows, and that the prewarm resolves them to nothing rather than to a jq error.

HOW. Renders helm/portal with helm (in a tempdir, with the CHART_VERSION placeholder stamped, as
lint-keyextras.py does), then models snowplow's RESTAction resolution closely enough to run the
chart's own jq: each api step's response is placed under the step's name, the step's own `filter`
runs over {<step>: response} and replaces it, a failed continueOnError step leaves only its
errorKey, the route's extras are merged in, and the top-level filter runs over the result. A
widget's `${ … }` expression is then evaluated over that output, as the frontend receives it.

Uses the `jq` binary (preinstalled on GitHub's ubuntu runners). snowplow runs gojq; JQ=gojq runs
the same checks on it.

With --crds DIR (a frontend-crds chart, as `helm pull --untar` leaves it), every Form the checks
resolve is also validated, as resolved, against its CRD, closed the way the apiserver's strict field
validation closes it: snowplow refuses a resolved widget its CRD does not admit (HTTP 400), and portal
1.8.45 shipped exactly that — an object where the Form CRD wants a string — for every install.

Usage: test-builder-install.py [chart-dir] [--crds DIR]   (default helm/portal). Exit code = failed checks.
"""
import copy
import glob
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

NS = 'krateo-system'
JQ = os.environ.get('JQ', 'jq')
ABSENT = object()


# ---------------------------------------------------------------------------------------------
# Rendering and evaluation
# ---------------------------------------------------------------------------------------------

def render(chart_dir):
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(chart_dir, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        open(meta, 'w', encoding='utf-8').write(
            text.replace('CHART_VERSION', '0.0.0-dev').replace('APP_VERSION', '0.0.0-dev'))
        proc = subprocess.run(['helm', 'template', 'portal', staged, '--namespace', NS],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise SystemExit(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


class Chart:
    def __init__(self, docs):
        self.docs = docs

    def get(self, kind, name):
        for d in self.docs:
            if d.get('kind') == kind and (d.get('metadata') or {}).get('name') == name:
                return d
        raise LookupError(f'the chart renders no {kind} {name}')

    def expression(self, kind, name, path):
        doc = self.get(kind, name)
        for entry in (doc['spec'].get('widgetDataTemplate') or []):
            if entry.get('forPath') == path:
                expr = entry['expression'].strip()
                assert expr.startswith('${') and expr.endswith('}'), expr
                return expr[2:-1]
        raise LookupError(f'{kind} {name} has no widgetDataTemplate for {path}')


def jq(program, data):
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
    return json.loads(proc.stdout)


def resolve(chart, name, responses, extras):
    """Model snowplow's resolution of RESTAction `name`. `responses` maps a step name to its raw
    response, or to the string ERROR for a failed call; a step with no entry was not served."""
    ra = chart.get('RESTAction', name)
    data = copy.deepcopy(extras)
    for step in ra['spec']['api']:
        step_name = step['name']
        if step_name not in responses:
            continue
        raw = responses[step_name]
        if raw == 'ERROR':
            if not step.get('continueOnError'):
                raise RuntimeError(f'{name}: step {step_name} failed and does not continue on error')
            data[step.get('errorKey', 'error')] = {'error': 'simulated 403 forbidden'}
            continue
        data[step_name] = jq(step['filter'], {step_name: raw}) if step.get('filter') else raw
    return jq(ra['spec']['filter'], data)


def widget(chart, kind, name, path, ra_output, extras):
    """A widget expression over its RA's output, with the extras merged in underneath."""
    data = dict(extras)
    data.update(ra_output if isinstance(ra_output, dict) else {})
    return jq(chart.expression(kind, name, path), data)


# Every widget a check resolved, as snowplow would serve it: validated against its CRD with --crds.
RESOLVED = []


def set_path(target, path, value):
    """Write `value` at a widgetDataTemplate forPath: dotted keys with [i] indexes."""
    keys = [int(k) if k.isdigit() else k for k in re.findall(r'[^.\[\]]+', path)]
    for key, nxt in zip(keys, keys[1:]):
        if isinstance(key, int):
            target = target[key]
        else:
            target = target.setdefault(key, [] if isinstance(nxt, int) else {})
    target[keys[-1]] = value


def resolved_widget(chart, kind, name, ra_output, extras, label):
    """The widget CR with every widgetDataTemplate entry written into widgetData — what snowplow
    validates — recorded for the CRD check."""
    doc = copy.deepcopy(chart.get(kind, name))
    for entry in doc['spec'].get('widgetDataTemplate') or []:
        set_path(doc['spec'].setdefault('widgetData', {}), entry['forPath'],
                 widget(chart, kind, name, entry['forPath'], ra_output, extras))
    RESOLVED.append((label, doc))
    return doc


# ---------------------------------------------------------------------------------------------
# Fixtures, shaped like the krateo-057 objects (github-provider-kog 0.3.1, builder-publish 1.8.x)
# ---------------------------------------------------------------------------------------------

def registration_file(name, url, version):
    return (f'# REGISTERS this chart. Install it from the portal once its release is green.\n'
            f'#   kubectl apply -f https://github.com/krateo-blueprints/{name}/releases/download/<tag>/compositiondefinition.yaml\n'
            f'apiVersion: core.krateo.io/v1alpha1\nkind: CompositionDefinition\nmetadata:\n'
            f'  name: {name}\n  namespace: {NS}\nspec:\n  chart:\n    url: {url}\n    version: {version}\n')


def local_resource(publish, builder, file_name, path='/', content='x: 1\n'):
    return {'metadata': {'name': f'{publish}-{file_name}-{path.strip("/") or "root"}',
                         'namespace': NS,
                         'creationTimestamp': '2026-09-20T10:00:00Z',
                         'labels': {'krateo.io/publish': publish, 'krateo.io/builder': builder}},
            'spec': {'fromResource': {'fileName': file_name, 'fromString': content},
                     'toRepo': {'url': f'https://github.com/krateo-blueprints/{publish[8:]}.git',
                                'branch': f'builder/{publish[8:]}', 'path': path}},
            'status': {'conditions': [{'type': 'Synced', 'status': 'True', 'reason': 'ReconcileSuccess'}]}}


def pull_request(publish, number, state, merged=ABSENT, repo=None, head=None):
    chart = publish[8:] if publish else (head or '').replace('builder/', '')
    status = {'number': number, 'state': state,
              'html_url': f'https://github.com/krateo-blueprints/{repo or chart}/pull/{number}'}
    if merged is not ABSENT:
        status['merged'] = merged
    labels = {'krateo.io/publish': publish} if publish else {}
    return {'metadata': {'name': f'{publish or chart}-pr', 'namespace': NS, 'labels': labels,
                         'creationTimestamp': '2026-09-20T10:05:00Z'},
            'spec': {'owner': 'krateo-blueprints', 'repo': repo or chart,
                     'head': head or f'builder/{chart}', 'title': f'feat: {chart}'},
            'status': status}


def index(entries):
    return {'apiVersion': 'v1', 'entries': entries}


def catalog_configmap():
    blueprints = index({'aws-ec2-instance': [{
        'name': 'aws-ec2-instance', 'version': '0.3.0',
        'urls': ['https://krateo-blueprints.github.io/charts/blueprints/aws-ec2-instance-0.3.0.tgz']}]})
    operators = index({'ec2-chart': [{
        'name': 'ec2-chart', 'version': '1.15.2',
        'urls': ['https://krateo-blueprints.github.io/charts/operators/ec2-chart-1.15.2.tgz']}]})
    return {'apiVersion': 'v1', 'kind': 'ConfigMap',
            'data': {'blueprints-index.json': json.dumps(blueprints),
                     'operators-index.json': json.dumps(operators)}}


def compositiondefinition_crd():
    spec = {'type': 'object', 'properties': {
        'chart': {'type': 'object', 'required': ['url'], 'properties': {
            'url': {'type': 'string'}, 'repo': {'type': 'string'},
            'version': {'type': 'string', 'description': 'needed for oci charts'},
            'credentials': {'type': 'object', 'required': ['username'],
                            'properties': {'username': {'type': 'string'}}}}},
        'deploy': {'type': 'object', 'properties': {'targetRef': {
            'type': 'object', 'required': ['name'], 'properties': {'name': {'type': 'string'}}}}},
        'statusDataTemplate': {'type': 'array'}}}
    return {'spec': {'versions': [{'name': 'v1alpha1',
                                   'schema': {'openAPIV3Schema': {'properties': {'spec': spec}}}}]}}


# The builder publishes on the fixture cluster:
#   my-bp            a blueprint with its root registration file (and a templates/ decoy CD)
#   aws-ec2-instance a blueprint with a root registration file, sharing a blueprints-index name,
#                    at an owner of the publisher's choosing
#   old-bp           a blueprint published before blueprints wrote a registration file: only its
#                    own templates/compositiondefinition.yaml, which is NOT a registration file
#   pages-a          a page set, whose registration file carries the CHART_VERSION placeholder
#   pet              a controller publish (never registered through a CompositionDefinition)
LOCAL_RESOURCES = [
    local_resource('publish-my-bp', 'blueprint', 'Chart.yaml'),
    local_resource('publish-my-bp', 'blueprint', 'compositiondefinition.yaml', '/',
                   registration_file('my-bp', 'oci://ghcr.io/krateo-blueprints/charts/my-bp', '0.1.0')),
    local_resource('publish-my-bp', 'blueprint', 'compositiondefinition.yaml', '/templates',
                   registration_file('my-bp', 'oci://ghcr.io/decoy/charts/my-bp', '9.9.9')),
    local_resource('publish-aws-ec2-instance', 'blueprint', 'Chart.yaml'),
    local_resource('publish-aws-ec2-instance', 'blueprint', 'compositiondefinition.yaml', '/',
                   registration_file('aws-ec2-instance', 'oci://ghcr.io/someone-else/charts/aws-ec2-instance', '0.1.0')),
    local_resource('publish-old-bp', 'blueprint', 'Chart.yaml'),
    local_resource('publish-old-bp', 'blueprint', 'compositiondefinition.yaml', '/templates',
                   registration_file('old-bp', 'oci://ghcr.io/krateo-blueprints/charts/old-bp', '0.1.0')),
    local_resource('publish-pages-a', 'page', 'Chart.yaml'),
    local_resource('publish-pages-a', 'page', 'compositiondefinition.yaml', '/',
                   registration_file('pages-a', 'oci://ghcr.io/krateo-blueprints/charts/pages-a', 'CHART_VERSION')),
    local_resource('publish-pet', 'controller', 'pet.yaml'),
]

CLAIMS = ['publish-my-bp', 'publish-aws-ec2-instance', 'publish-old-bp', 'publish-pages-a', 'publish-pet']


def prs(state, merged=ABSENT):
    """One PR per claim, all in the same state, plus one LEGACY PR (no krateo.io/publish label) to
    the krateo-blueprints repo, as the pre-claim builder opened them."""
    items = [pull_request(p, n + 1, state, merged) for n, p in enumerate(CLAIMS)]
    items.append(pull_request('', 40, state, merged, repo='krateo-blueprints', head='builder/legacy-bp'))
    return {'items': items}


def responses(pr_list, cds=()):
    return {
        'crd': compositiondefinition_crd(),
        'catalog': catalog_configmap(),
        'operators': catalog_configmap(),
        'builderCds': {'items': LOCAL_RESOURCES},
        'builderPrs': pr_list,
        'publishes': {'items': LOCAL_RESOURCES},
        'prs': pr_list,
        'cds': {'items': [{'metadata': {'name': n}, 'spec': {'chart': {'version': '0.1.0'}},
                           'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}} for n in cds]},
        'namespaces': {'items': [{'metadata': {'name': NS}}]},
        'targets': {'items': []},
    }


CLOSED = responses(prs('closed'))                  # github-provider-kog 0.3.1: no `merged` field
MERGED = responses(prs('closed', merged=True))     # a provider that reports it
REJECTED = responses(prs('closed', merged=False))
OPEN = responses(prs('open'))


def rows(chart, ra, resp):
    return resolve(chart, ra, resp, {})['rows']


def row(chart, ra, resp, key, value):
    hits = [r for r in rows(chart, ra, resp) if r.get(key) == value]
    if len(hits) != 1:
        raise AssertionError(f'{ra}: expected one row with {key}={value!r}, got {len(hits)}')
    return hits[0]


def pr_url(chart_name, number):
    return f'https://github.com/krateo-blueprints/{chart_name}/pull/{number}'


# ---------------------------------------------------------------------------------------------
# Checks — each returns a list of failure messages
# ---------------------------------------------------------------------------------------------

def expect(failures, label, got, want):
    if got != want:
        failures.append(f'{label}: got {got!r}, want {want!r}')


def check_index_name_keeps_its_index_chart(chart):
    """The install route is also the official marketplace card: a builder claim that shares a
    helm-index name must not re-point it at the claim's chart."""
    f = []
    out = resolve(chart, 'blueprint-install-formdef', CLOSED, {'name': 'aws-ec2-instance'})
    expect(f, 'index name: chart pre-fill', out['initialValues']['chart'],
           {'url': 'https://krateo-blueprints.github.io/charts/blueprints', 'repo': 'aws-ec2-instance', 'version': '0.3.0'})
    expect(f, 'index name: chart.required (index behaviour)', out['schemaSpec']['properties']['chart'].get('required'), None)
    out = resolve(chart, 'blueprint-install-formdef', CLOSED, {'name': 'my-bp'})
    expect(f, 'builder-only name: chart pre-fill (root file, not the templates/ decoy)', out['initialValues']['chart'],
           {'url': 'oci://ghcr.io/krateo-blueprints/charts/my-bp', 'version': '0.1.0'})
    expect(f, 'builder-only name: chart.required', out['schemaSpec']['properties']['chart'].get('required'), ['url', 'version'])
    out = resolve(chart, 'blueprint-install-formdef', CLOSED, {'name': 'ec2-chart'})
    expect(f, 'operator name: chart pre-fill', out['initialValues']['chart'],
           {'url': 'https://krateo-blueprints.github.io/charts/operators', 'repo': 'ec2-chart', 'version': '1.15.2'})
    return f


def check_install_page_links_the_change_request(chart):
    """Rows lead to the install form while the provider cannot say whether the PR merged; the
    page they lead to must name that PR, as a link, and say what is known about how it ended."""
    f = []
    header, note, form = 'blueprint-install-page-header', 'blueprint-install-origin', 'blueprint-install-form'
    cases = [
        (CLOSED, 'my-bp', 'Published by ' + pr_url('my-bp', 1) + ', now closed. Install it only if that change request merged and its release is green.'),
        (MERGED, 'my-bp', 'Published by ' + pr_url('my-bp', 1) + ', which merged. Install it once its release is green.'),
        (REJECTED, 'my-bp', 'Published by ' + pr_url('my-bp', 1) + ', which closed without merging: nothing was released, so there is nothing to install.'),
        (OPEN, 'my-bp', 'Published by ' + pr_url('my-bp', 1) + ', still open. Install it once it merges and its release is green.'),
        (MERGED, 'pages-a', 'Published by ' + pr_url('pages-a', 4) + ', which merged. Install it once a release you tagged on main is green.'),
        # An index name: the form holds the index chart, and the author the row sent here is told so.
        (CLOSED, 'aws-ec2-instance', 'Published by ' + pr_url('aws-ec2-instance', 2) + ' under a name the marketplace already has. '
                                     'This form installs the marketplace chart of that name, not the published one; '
                                     'to install that one, publish it under another name.'),
        (CLOSED, 'ec2-chart', ''),
        (CLOSED, 'old-bp', ''),             # no registration file: the form has no builder chart
    ]
    for resp, name, want in cases:
        extras = {'name': name}
        out = resolve(chart, 'blueprint-install-origin', resp, extras)
        got = widget(chart, 'Paragraph', note, 'text', out, extras)
        expect(f, f'note for {name}', got, want)
        items = [i['resourceRefId'] for i in widget(chart, 'Flex', 'page-blueprint-install', 'items', out, extras)]
        expect(f, f'page items for {name}', items, [header, note, form] if want else [header, form])
    # Every step failing (a person who may read none of it) still renders the page, without a note.
    failing = {k: 'ERROR' for k in ('catalog', 'operators', 'builderCds', 'builderPrs')}
    out = resolve(chart, 'blueprint-install-origin', failing, {'name': 'my-bp'})
    items = [i['resourceRefId'] for i in widget(chart, 'Flex', 'page-blueprint-install', 'items', out, {'name': 'my-bp'})]
    expect(f, 'page items when every origin step fails', items, [header, form])
    # And the rows keep leading there without a `merged` field: the Install step stays reachable.
    expect(f, 'builder-prs: closed, merge unreported -> install form',
           row(chart, 'builder-prs', CLOSED, 'name', 'publish-my-bp')['url'], '/marketplace/my-bp/install')
    expect(f, 'deliverables: closed, merge unreported -> install form',
           row(chart, 'blueprint-builder-deliverables', CLOSED, 'chart', 'my-bp')['rowHref'], '/marketplace/my-bp/install')
    return f


def check_install_needs_the_registration_file(chart):
    """The form pre-fills from the publish's ROOT compositiondefinition.yaml alone; a publish
    without one must keep linking to its change request instead of opening the form."""
    f = []
    for resp, label in ((CLOSED, 'closed'), (MERGED, 'merged')):
        r = row(chart, 'blueprint-builder-deliverables', resp, 'chart', 'old-bp')
        expect(f, f'deliverables old-bp ({label}): next', r['next'], '')
        expect(f, f'deliverables old-bp ({label}): rowHref', r['rowHref'], pr_url('old-bp', 3))
        r = row(chart, 'builder-prs', resp, 'name', 'publish-old-bp')
        expect(f, f'builder-prs old-bp ({label}): next', r['next'], '')
        expect(f, f'builder-prs old-bp ({label}): url', r['url'], pr_url('old-bp', 3))
        r = row(chart, 'blueprint-builder-deliverables', resp, 'chart', 'legacy-bp')
        expect(f, f'deliverables legacy PR ({label}): next', r['next'], '')
        expect(f, f'deliverables legacy PR ({label}): rowHref', r['rowHref'], pr_url('krateo-blueprints', 40))
        r = row(chart, 'blueprint-builder-deliverables', resp, 'chart', 'my-bp')
        expect(f, f'deliverables my-bp ({label}): rowHref', r['rowHref'], '/marketplace/my-bp/install')
    # A registered chart goes to its blueprint page, as before.
    registered = responses(prs('closed', merged=True), cds=['my-bp'])
    r = row(chart, 'blueprint-builder-deliverables', registered, 'chart', 'my-bp')
    expect(f, 'deliverables my-bp registered: rowHref', r['rowHref'], f'/blueprints/{NS}/my-bp')
    expect(f, 'deliverables my-bp registered: next', r['next'], '')
    return f


def check_page_set_next_step_is_a_tag(chart):
    """A page set's merge releases nothing: its next step starts with pushing a tag, and the form
    says how, in the person's terms."""
    f = []
    expect(f, 'builder-prs page row, merge unreported', row(chart, 'builder-prs', CLOSED, 'name', 'publish-pages-a')['next'],
           'If merged: tag a release, then install')
    expect(f, 'builder-prs page row, merged', row(chart, 'builder-prs', MERGED, 'name', 'publish-pages-a')['next'],
           'Tag a release, then install')
    out = resolve(chart, 'blueprint-install-formdef', MERGED, {'name': 'pages-a'})
    expect(f, 'page set pre-fill', out['initialValues']['chart'],
           {'url': 'oci://ghcr.io/krateo-blueprints/charts/pages-a', 'version': ''})
    desc = out['schemaSpec']['properties']['chart']['properties']['version'].get('description', '')
    for needle in ("merge releases nothing", 'git tag 0.1.0 origin/main && git push origin 0.1.0', 'type the tag here'):
        if needle not in desc:
            f.append(f'page set version description lacks {needle!r}: {desc!r}')
    for jargon in ('CHART_VERSION', 'registration file'):
        if jargon in desc:
            f.append(f'page set version description still says {jargon!r}: {desc!r}')
    return f


def check_merged_is_one_colour(chart):
    """The same merged PR is listed on both builder pages; it wears one colour on both."""
    f = []
    d = row(chart, 'blueprint-builder-deliverables', MERGED, 'chart', 'my-bp')
    p = row(chart, 'builder-prs', MERGED, 'name', 'publish-my-bp')
    expect(f, 'deliverables merged status', (d['status'], d['statusColor']), ('Merged', 'green'))
    expect(f, 'builder-prs merged status', (p['stateLabel'], p['stateColor']), ('Merged', 'green'))
    return f


def check_the_step_is_called_install(chart):
    """The row names the step the page it opens performs: Install, the portal's verb for
    creating a CompositionDefinition."""
    f = []
    expect(f, 'deliverables blueprint, merged', row(chart, 'blueprint-builder-deliverables', MERGED, 'chart', 'my-bp')['next'], 'Install')
    expect(f, 'deliverables blueprint, merge unreported', row(chart, 'blueprint-builder-deliverables', CLOSED, 'chart', 'my-bp')['next'], 'Install if merged')
    expect(f, 'builder-prs blueprint, merged', row(chart, 'builder-prs', MERGED, 'name', 'publish-my-bp')['next'], 'Install')
    expect(f, 'builder-prs blueprint, merge unreported', row(chart, 'builder-prs', CLOSED, 'name', 'publish-my-bp')['next'], 'Install if merged')
    for resp in (CLOSED, MERGED, REJECTED, OPEN):
        for ra in ('blueprint-builder-deliverables', 'builder-prs'):
            for r in rows(chart, ra, resp):
                if 'regist' in (r.get('next') or '').lower():
                    f.append(f'{ra}: a Next step still says {r["next"]!r}')
    for name in ('blueprint-builder-deliverables-note', 'builder-change-requests-note'):
        text = chart.get('Paragraph', name)['spec']['widgetData']['text']
        if 'installs it' not in text or 'registers it' in text:
            f.append(f'{name} does not name the step Install: {text!r}')
    return f


def check_install_header_has_no_lone_v(chart):
    """marketplace-detail resolves helm-index entries only; for any other chart `.version` is ""."""
    f = []
    cases = [({'name': 'pod-sizing-e3', 'version': ''}, ''),
             ({'name': 'pod-sizing-e3'}, ''),
             ({'name': 'x', 'maturity': 'beta', 'version': ''}, 'beta'),
             ({'name': 'keystone', 'maturity': 'stable', 'version': '0.2.0'}, 'stable  ·  v0.2.0'),
             ({'name': 'y', 'version': '1.0.0'}, 'v1.0.0')]
    for data, want in cases:
        got = widget(chart, 'PageHeader', 'blueprint-install-page-header', 'subtitle', data, {})
        expect(f, f'install header subtitle for {data}', got, want)
    return f


def check_install_applies_the_status_projection(chart):
    """S12 (decision D1): a builder blueprint that publishes status-projection.json installs as two
    writes — its <chart>-status RESTAction, then the CompositionDefinition carrying that apiRef and
    its rows. Every other install keeps the one plain write, and a malformed bundle is no bundle.

    And EVERY templated payloadToOverride value is a STRING, in every case: snowplow validates the
    resolved widgetData against the Form CRD, where that field is a string, and an object there
    refused the whole form for every install (portal 1.8.45-1.8.46 on krateo-057)."""
    f = []
    restaction = {'apiVersion': 'templates.krateo.io/v1', 'kind': 'RESTAction',
                  'metadata': {'name': 'my-bp-status', 'namespace': NS},
                  'spec': {'api': [{'name': 'arch', 'path': '${ "/api/v1/namespaces/" + .compositionNamespace + "/configmaps" }', 'verb': 'GET'}],
                           'filter': '.'}}
    bundle = {'apiRef': {'name': 'my-bp-status', 'namespace': NS}, 'restaction': restaction,
              'statusDataTemplate': [{'forPath': 'architectureReady', 'expression': '${ .api.allReady == true }', 'type': 'boolean'}]}
    extras = {'name': 'my-bp'}
    SPEC = 'actions.rest[1].ops[1].payloadToOverride[2].value'

    def install(projection_file):
        resp = responses(prs('closed', merged=True))
        resp['builderProjections'] = {'items': [local_resource('publish-my-bp', 'blueprint', 'status-projection.json', content=projection_file)]} if projection_file is not None else {'items': []}
        out = resolve(chart, 'blueprint-install-formdef', resp, extras)
        resolved_widget(chart, 'Form', 'blueprint-install', out, extras,
                        f'blueprint-install ({"no bundle" if projection_file is None else "bundle"})')
        return out, {path: widget(chart, 'Form', 'blueprint-install', path, out, extras) for path in (
            'submitActionId', 'actions.rest[1].ops[0].payload', SPEC)}

    # What the form submits for this chart (the values the person reviewed).
    submitted = {'name': 'my-bp', 'namespace': NS, 'chart': {'url': 'oci://ghcr.io/krateo-blueprints/charts/my-bp', 'version': '0.1.0'}}
    out, w = install(json.dumps(bundle))
    expect(f, 'projected: action', w['submitActionId'], 'submit-projected')
    expect(f, 'projected: RESTAction op payload', w['actions.rest[1].ops[0].payload'], restaction)
    # The ref's payload base is merged OVER the op's payload, blanking metadata.name (057): the op sets
    # it again, as a string, from the bundle.
    expect(f, 'projected: RESTAction name override', widget(chart, 'Form', 'blueprint-install', 'actions.rest[1].ops[0].payloadToOverride[0].value', out, extras), 'my-bp-status')
    expect(f, 'projected: RESTAction namespace override', widget(chart, 'Form', 'blueprint-install', 'actions.rest[1].ops[0].payloadToOverride[1].value', out, extras), NS)
    expect(f, 'projected: spec override is a ${ } string', isinstance(w[SPEC], str) and w[SPEC].startswith('${') and w[SPEC].endswith('}'), True)
    spec = jq(w[SPEC][2:-1], {'json': submitted})
    expect(f, 'projected: spec keeps the form', spec.get('chart'), submitted['chart'])
    expect(f, 'projected: spec.apiRef', spec.get('apiRef'), bundle['apiRef'])
    expect(f, 'projected: spec.statusDataTemplate', spec.get('statusDataTemplate'), bundle['statusDataTemplate'])
    expect(f, 'projected: chart prefill unchanged', out['initialValues']['chart'], submitted['chart'])
    for label, content in (('no bundle', None), ('malformed bundle', '{not json'), ('bundle without a RESTAction', json.dumps({**bundle, 'restaction': None}))):
        out, w = install(content)
        expect(f, f'{label}: action', w['submitActionId'], 'submit')
        expect(f, f'{label}: projection', out.get('projection'), None)
        expect(f, f'{label}: the templated override is still a string', isinstance(w[SPEC], str), True)
        expect(f, f'{label}: the name override is still a string', isinstance(widget(chart, 'Form', 'blueprint-install', 'actions.rest[1].ops[0].payloadToOverride[0].value', out, extras), str), True)
    # The form's own shape: the projected action writes the RESTAction FIRST, and each op's ref exists.
    form = chart.get('Form', 'blueprint-install')
    projected = [a for a in form['spec']['widgetData']['actions']['rest'] if a['id'] == 'submit-projected'][0]
    expect(f, 'projected: op order', [op['resourceRefId'] for op in projected['ops']], ['create-status-restaction', 'create-compdef'])
    refs = {r['id']: r for r in form['spec']['resourcesRefs']['items']}
    expect(f, 'projected: RESTAction ref', {k: refs['create-status-restaction'][k] for k in ('apiVersion', 'resource', 'verb')},
           {'apiVersion': 'templates.krateo.io/v1', 'resource': 'restactions', 'verb': 'POST'})
    # Every templated forPath under a payloadToOverride value must be one — and nothing may template
    # a non-string there (the CRD's type).
    for t in form['spec']['widgetDataTemplate']:
        if 'payloadToOverride' in t['forPath']:
            expect(f, f'template {t["forPath"]} targets a value', t['forPath'].endswith('.value'), True)
    return f

def check_create_form_says_when_the_blueprint_is_not_registered_yet(chart):
    """The create form (/blueprints/<ns>/<name>/create) right after an Install, before core-provider
    has generated the kind: the CompositionDefinition has no status.apiVersion yet. That used to fail
    the whole widget (`split cannot be applied to: null`, and on krateo-057 ~1,500 lines an hour from
    the background prewarm, which resolves it with no blueprint named). It must resolve, and say the
    blueprint is still being registered — and, once registered, render the chart's fields."""
    f = []
    extras = {'name': 'my-bp', 'namespace': NS, 'username': 'admin'}
    cd = {'apiVersion': 'core.krateo.io/v1alpha1', 'kind': 'CompositionDefinition',
          'metadata': {'name': 'my-bp', 'namespace': NS}}
    registered = {**cd, 'status': {'apiVersion': 'composition.krateo.io/v0-1-0', 'kind': 'MyBp', 'resource': 'mybps'}}
    crd = {'spec': {'versions': [{'name': 'v0-1-0', 'schema': {'openAPIV3Schema': {'properties': {'spec': {
        'type': 'object', 'properties': {'replicas': {'type': 'integer', 'title': 'Replicas'}}}}}}}]}}
    schema_cm = {'data': {'values.schema.json': json.dumps({'type': 'object', 'properties': {'replicas': {'type': 'integer', 'title': 'Replicas'}}})}}
    namespaces = {'items': [{'metadata': {'name': NS}}]}

    def create(label, responses, extras=extras):
        out = resolve(chart, 'blueprint-formdef', responses, extras)
        return out, resolved_widget(chart, 'Form', 'blueprint-create', out, extras, f'blueprint-create ({label})')

    not_ready = {
        'no status yet': {'compdef': cd, 'jsonschema': 'ERROR', 'crd': 'ERROR', 'namespaces': namespaces},
        'no status, core-provider says why': {'compdef': {**cd, 'status': {'conditions': [
            {'type': 'Ready', 'status': 'False', 'reason': 'ReconcileError', 'message': 'chart not found: oci://x/my-bp:0.1.0'}]}},
            'jsonschema': 'ERROR', 'crd': 'ERROR', 'namespaces': namespaces},
        'status, CRD not served yet': {'compdef': registered, 'jsonschema': 'ERROR', 'crd': 'ERROR', 'namespaces': namespaces},
        'no blueprint named (prewarm)': {'compdef': {'apiVersion': 'v1', 'kind': 'List', 'items': []},
                                         'jsonschema': 'ERROR', 'crd': 'ERROR', 'namespaces': namespaces},
    }
    for label, responses in not_ready.items():
        out, doc = create(label, responses, extras if 'prewarm' not in label else {'username': 'admin'})
        schema = doc['spec']['widgetData']['schema']
        expect(f, f'{label}: says it is being registered', schema.get('title'), 'This blueprint is still being registered')
        expect(f, f'{label}: no fields to fill', schema.get('properties'), None)
        expect(f, f'{label}: resource refs are strings', [out['apiVersion'], out['resource']], ['', ''])
    out, _ = create('core-provider says why', not_ready['no status, core-provider says why'])
    expect(f, 'the reason core-provider gives is shown', 'chart not found' in out['schemaSpec']['description'], True)

    out, doc = create('registered, no values.schema.json', {'compdef': registered, 'jsonschema': 'ERROR', 'crd': crd, 'namespaces': namespaces})
    expect(f, 'registered without a jsonschema ConfigMap: stringSchema is "" (the CRD wants a string)', doc['spec']['widgetData']['stringSchema'], '')
    out, doc = create('registered', {'compdef': registered, 'jsonschema': schema_cm, 'crd': crd, 'namespaces': namespaces})
    schema = doc['spec']['widgetData']['schema']
    expect(f, 'registered: the chart\'s fields', sorted(schema['properties']), ['__composition_name__', '__composition_namespace__', 'replicas'])
    expect(f, 'registered: the kind it creates', [out['apiVersion'], out['resource']], ['composition.krateo.io/v0-1-0', 'mybps'])
    return f


def check_marketplace_detail_resolves_without_a_name(chart):
    """The marketplace detail RESTAction behind /marketplace/<name> and the Install page header. The
    background prewarm resolves it with no ?extras.name, and `.entries[null]` raised "expected a
    string for object key but got: null" — every widget on it failed, ~670 an hour on krateo-057.
    With no name it must resolve (found: false); with a name, exactly as before. Every widget on it
    is recorded for the CRD validation, in both cases."""
    f = []
    index = {'entries': {'my-bp': [{'name': 'my-bp', 'version': '0.2.0', 'description': 'A blueprint',
                                     'urls': ['oci://ghcr.io/krateo-blueprints/charts/my-bp'],
                                     'annotations': {'krateo.io/category': 'blueprint'}}]}}
    responses = {'catalog': {'data': {'blueprints-index.json': json.dumps(index)}},
                 'compdefs': {'items': [{'metadata': {'name': 'my-bp', 'namespace': NS}, 'spec': {'chart': {'version': '0.1.0'}}}]}}
    widgets = [('Button', 'marketplace-detail-action'), ('Descriptions', 'descriptions-marketplace-detail-about'),
               ('PageHeader', 'blueprint-install-page-header'), ('PageHeader', 'marketplace-detail-page-header'),
               ('Paragraph', 'marketplace-detail-description'), ('Paragraph', 'marketplace-detail-links')]
    for label, extras in (('no name (prewarm)', {}), ('named', {'name': 'my-bp'})):
        out = resolve(chart, 'marketplace-detail', responses, extras)
        for kind, name in widgets:
            resolved_widget(chart, kind, name, out, extras, f'{name} ({label})')
        if label == 'named':
            expect(f, 'named: found', out['found'], True)
            expect(f, 'named: installed, at the installed version', [out['installed'], out['installedVersion']], [True, '0.1.0'])
        else:
            expect(f, 'no name: not found, and nothing installed', [out['found'], out['installed']], [False, False])
    return f


def check_render_draft_reads_the_chart_from_its_configmap(chart):
    """blueprint-render-draft: the composer's Preview writes the chart into the preview sandbox as a
    ConfigMap and renders it BY NAME — a chart in ?extras rides the URL, and the gateway refuses an
    HTTP/2 request whose headers pass 16 KB (431 for a 12 KB chart). The render body must be the
    ConfigMap's chart.json, narrowed to what /render reads; every way of having no chart must be a
    sentence, never a render of nothing."""
    f = []
    ra = chart.get('RESTAction', 'blueprint-render-draft')
    render_step = [s for s in ra['spec']['api'] if s['name'] == 'render'][0]
    payload = render_step['payload'].strip()
    expect(f, 'the payload is one ${ } program', payload.startswith('${') and payload.endswith('}'), True)
    files = {'Chart.yaml': 'apiVersion: v2\nname: big\nversion: 0.1.0\n',
             'values.schema.json': json.dumps({'type': 'object', 'properties': {f'p{i}': {'type': 'string', 'title': 'x' * 200} for i in range(80)}})}
    stubs = [{'apiVersion': 'v1', 'kind': 'ConfigMap', 'object': {}}]
    cm = {'data': {'chart.json': json.dumps({'rawTemplates': files, 'values': {'a': 1}, 'lookupStubs': stubs, 'ignored': True})}}
    body = json.loads(jq(payload[2:-1], {'draft': cm}))
    expect(f, 'the body is the chart, its values and its stand-ins — nothing else', body,
           {'rawTemplates': files, 'values': {'a': 1}, 'lookupStubs': stubs})
    expect(f, 'a chart far past the 16 KB URL limit travels whole', len(files['values.schema.json']) > 16000, True)
    named = {'namespace': 'krateo-preview', 'name': 'bp-preview-big-x1'}
    out = resolve(chart, 'blueprint-render-draft', {'draft': cm, 'render': {'objects': [{'kind': 'Deployment', 'name': 'r-app', 'yaml': 'kind: Deployment\n'}],
                                                                          'lookups': [{'apiVersion': 'v1', 'kind': 'ConfigMap', 'namespace': 'ns', 'name': 'c', 'stubbed': True}]}}, named)
    expect(f, 'rendered: the objects', [o['kind'] for o in out['objects']], ['Deployment'])
    expect(f, 'rendered: the lookups report rides along', len(out.get('lookups', [])), 1)
    expect(f, 'rendered: no error', 'error' in out, False)
    for label, responses, extras, says in (
            ('no draft named', {}, {}, 'no draft named'),
            ('the ConfigMap could not be read', {'draft': 'ERROR', 'render': 'ERROR'}, named, 'could not be read'),
            ('a ConfigMap with no chart', {'draft': {'data': {}}, 'render': {'error': 'chart: one of url, files or rawTemplates is required'}}, named, 'holds no chart')):
        out = resolve(chart, 'blueprint-render-draft', responses, extras)
        expect(f, f'{label}: says so', says in (out.get('error') or ''), True)
        expect(f, f'{label}: renders nothing', out['objects'], [])
    return f


def check_review_proposals_are_not_builder_publishes(chart):
    """A nightly-review proposal rides the builder-publish chain with krateo.io/builder: review. It is
    not a builder's publish, so the builders' change-request feed must not list it — while a
    blueprint publish in the same response still is listed."""
    f = []
    resp = responses(prs('open'))
    review = [local_resource('publish-nightly-0928', 'review', 'proposals/0928.md', path='/docs')]
    resp['publishes'] = {'items': LOCAL_RESOURCES + review}
    names = [r.get('name') for r in rows(chart, 'builder-prs', resp)]
    expect(f, 'review publish is not a builder row', 'publish-nightly-0928' in names, False)
    expect(f, 'a blueprint publish still is', 'publish-my-bp' in names, True)
    return f


def draft_owner(username):
    """The frontend kernel's draftOwner (ui/src/components/Autopilot/draftRecord.ts), ported line
    for line, so the RA's jq is held to the browser's sanitizer rather than to itself."""
    cleaned = re.sub(r'[^a-z0-9-]+', '-', (username or '').lower())
    cleaned = re.sub(r'^-+|-+$', '', cleaned)[:40]
    cleaned = re.sub(r'-+$', '', cleaned)
    return cleaned or 'unknown'


def draft_record(kind, owner, name, updated, state='open', previewed=False, thread=None, publish=None, files=None):
    """A draft record ConfigMap as draftRecordConfigMap() writes it."""
    files = files if files is not None else (
        {'Chart.yaml': f'apiVersion: v2\nname: {name}\nversion: "0.2.0"\n', 'values.yaml': 'a: 1\n', 'templates/cm.yaml': 'kind: ConfigMap\n'}
        if kind == 'blueprint' else {'page.yaml': 'kind: Flex\n', 'header.yaml': 'kind: PageHeader\n'})
    body = {'version': 1, 'kind': kind, 'name': name, 'files': files, 'updatedAt': updated, 'state': state}
    if thread:
        body['threadId'] = thread
    if publish:
        body['publish'] = publish
    return {'apiVersion': 'v1', 'kind': 'ConfigMap',
            'metadata': {'name': f'draft-{kind}-{owner}-{name}'[:63].rstrip('-'), 'namespace': 'krateo-preview',
                         'creationTimestamp': '2026-09-01T00:00:00Z',
                         'labels': {'krateo.io/purpose': 'draft-record', 'krateo.io/draft-owner': owner,
                                    'krateo.io/draft-kind': kind, 'krateo.io/draft-state': state,
                                    'krateo.io/draft-previewed': 'true' if previewed else 'false'},
                         'annotations': {'krateo.io/draft-name': name, 'krateo.io/draft-updated-at': updated}},
            'data': {'draft.json': json.dumps(body)}}


MY_DRAFT_WIDGETS = [('Listy', f'my-drafts-{kind}{suffix}') for kind in ('blueprint', 'page') for suffix in ('', '-published')] \
    + [('Card', 'blueprint-builder-drafts-card'), ('Card', 'portal-builder-drafts-card')]


def listy_placeholders_resolve(f, chart, name, rows, label):
    """Every `{key}` the row template reads and every `${key}` a row action interpolates must be a
    key on each row: an unresolved one renders literally, or navigates to the literal braces."""
    wd = chart.get('Listy', name)['spec']['widgetData']
    keys = set(re.findall(r'\{(\w+)\}', json.dumps(wd.get('itemTemplate') or {})))
    for action in (wd.get('actions') or {}).get('navigate') or []:
        keys |= set(re.findall(r'\$\{(\w+)\}', action.get('path') or ''))
    for action in (wd.get('actions') or {}).get('rest') or []:
        for entry in action.get('payloadToOverride') or []:
            keys |= set(re.findall(r'\.json\.(\w+)', entry['value']))
    for row in rows:
        missing = sorted(k for k in keys if k not in row)
        expect(f, f'{label}: {name} rows carry every key the list reads', missing, [])


def check_my_drafts_are_the_callers_own(chart):
    """restaction.my-drafts behind "Your drafts" on /blueprint-builder and /portal-builder. The owner
    is the caller's server-injected username, sanitized exactly as the frontend kernel's draftOwner;
    another person's records, records owned by "unknown", and ConfigMaps that are not records never
    appear; the prewarm (no username, no extras, no response) resolves to no rows, never an error.
    Every widget on it is resolved and recorded for the CRD validation."""
    f = []
    me = 'Diego.Braga@Example.com'
    owner = draft_owner(me)
    items = [
        draft_record('blueprint', owner, 'catalog-service', '2026-09-29T10:58:00Z', thread='thread-1'),
        draft_record('blueprint', owner, 'payments-api', '2026-09-28T17:42:00Z', previewed=True),
        draft_record('blueprint', owner, 'aws-vpc-network', '2026-09-26T09:10:00Z', state='published', previewed=True,
                     publish={'repo': 'krateo-blueprints/aws-vpc-network', 'prUrl': 'https://github.com/krateo-blueprints/aws-vpc-network/pull/1'}),
        draft_record('page', owner, 'service-catalog', '2026-09-29T10:50:00Z', previewed=True),
        draft_record('blueprint', 'alice', 'catalog-service', '2026-09-29T11:00:00Z'),
        draft_record('page', 'unknown', 'pod-sizing', '2026-09-24T18:49:00Z'),
        {'metadata': {'name': 'bp-preview-big-x1', 'labels': {'krateo.io/purpose': 'blueprint-render'}},
         'data': {'chart.json': '{}'}},
        {'metadata': {'name': 'kube-root-ca.crt'}, 'data': {'ca.crt': 'x'}},
    ]
    responses = {'records': {'items': items}}
    out = resolve(chart, 'my-drafts', responses, {'username': me})
    rows = out['items']
    expect(f, 'named: the owner is the kernel\'s draftOwner of the username', out['owner'], owner)
    expect(f, 'named: only my records, newest first', [r['recordName'] for r in rows],
           [f'draft-blueprint-{owner}-catalog-service', f'draft-page-{owner}-service-catalog',
            f'draft-blueprint-{owner}-payments-api', f'draft-blueprint-{owner}-aws-vpc-network'])
    expect(f, 'named: another person\'s record with the same name is not mine',
           any('alice' in r['recordName'] for r in rows), False)
    by = {r['name'] + '/' + r['kind']: r for r in rows}
    expect(f, 'the three states say what the mockup says',
           [by['catalog-service/blueprint']['statusLabel'], by['payments-api/blueprint']['statusLabel'], by['aws-vpc-network/blueprint']['statusLabel']],
           ['Preview needed', 'Previewed · ready to publish', 'Published · awaiting merge'])
    expect(f, 'started from: a thread, or by hand',
           [by['catalog-service/blueprint']['startedFrom'], by['payments-api/blueprint']['startedFrom']],
           ['Autopilot thread', 'Composed by hand'])
    expect(f, 'Resume opens each kind\'s composer on the record',
           [by['catalog-service/blueprint']['resumePath'], by['service-catalog/page']['resumePath']],
           [f'/blueprint-builder/compose?resume=draft-blueprint-{owner}-catalog-service',
            f'/portal-builder/compose?resume=draft-page-{owner}-service-catalog'])
    expect(f, 'published: the PR link rides along', by['aws-vpc-network/blueprint']['prUrl'],
           'https://github.com/krateo-blueprints/aws-vpc-network/pull/1')
    expect(f, 'the chart version and file count, from the tree', [by['payments-api/blueprint']['version'], by['payments-api/blueprint']['files']], ['0.2.0', 3])
    expect(f, 'the body itself is never returned', any('body' in r or 'data' in r or isinstance(r.get('files'), dict) for r in rows), False)
    kinds = resolve(chart, 'my-drafts', responses, {'username': me, 'kind': 'page'})['items']
    expect(f, 'extras kind narrows to one kind', [r['kind'] for r in kinds], ['page'])

    # The sanitizer, against the kernel, on names that exercise every step of it.
    for username in ('Diego.Braga@Example.com', 'ADMIN', '--a__b--', 'x' * 39 + '-yyyy', 'élodie', 'cyberjoker'):
        got = resolve(chart, 'my-drafts', {'records': {'items': []}}, {'username': username})['owner']
        expect(f, f'owner of {username!r} matches the kernel', got, draft_owner(username))

    for label, resp, extras in (('no username (prewarm)', responses, {}),
                                ('no username, no response', {}, {}),
                                ('empty username', responses, {'username': ''}),
                                ('a username the sanitizer empties', responses, {'username': '!!!'}),
                                ('the sandbox could not be read', {'records': 'ERROR'}, {'username': me})):
        expect(f, f'{label}: no rows', resolve(chart, 'my-drafts', resp, extras)['items'], [])

    for kind, name in MY_DRAFT_WIDGETS:
        for label, ra_out in (('named', out), ('prewarm', resolve(chart, 'my-drafts', {}, {}))):
            doc = resolved_widget(chart, kind, name, ra_out, {}, f'{name} ({label})')
            if kind == 'Listy':
                listy_placeholders_resolve(f, chart, name, doc['spec']['widgetData']['dataSource'], label)
    rendered = {name: resolved_widget(chart, kind, name, out, {}, f'{name} (named, again)')['spec']['widgetData']
                for kind, name in MY_DRAFT_WIDGETS}
    expect(f, 'blueprint list: the open drafts, their meta line', [(r['name'], r['meta'], r['statusKey']) for r in rendered['my-drafts-blueprint']['dataSource']],
           [('catalog-service', '0.2.0 · 3 files · Autopilot thread', 'preview-needed'),
            ('payments-api', '0.2.0 · 3 files · Composed by hand', 'previewed')])
    expect(f, 'blueprint published list: the change request, named', [r['meta'] for r in rendered['my-drafts-blueprint-published']['dataSource']],
           ['0.2.0 · published · krateo-blueprints/aws-vpc-network #1'])
    expect(f, 'page list: the page draft only', [(r['name'], r['meta']) for r in rendered['my-drafts-page']['dataSource']],
           [('service-catalog', 'page · 2 files · Composed by hand')])
    expect(f, 'card counts: blueprint and page', [rendered['blueprint-builder-drafts-card']['extra'], rendered['portal-builder-drafts-card']['extra']],
           ['3 drafts · only you see these', '1 draft · only you see these'])

    # Discard: a DELETE of the row's record in the sandbox, never of a name that could be one.
    for kind, name in MY_DRAFT_WIDGETS[:4]:
        spec = chart.get('Listy', name)['spec']
        ref = spec['resourcesRefs']['items'][0]
        rest = spec['widgetData']['actions']['rest'][0]
        overrides = {e['name']: e['value'] for e in rest['payloadToOverride']}
        expect(f, f'{name}: Discard deletes a ConfigMap in the sandbox', [ref['verb'], ref['resource'], ref['namespace']], ['DELETE', 'configmaps', 'krateo-preview'])
        expect(f, f'{name}: the ref\'s own name is never a record', ref['name'].startswith('draft-'), False)
        expect(f, f'{name}: the row names the record', overrides, {'metadata.name': '${ .json.recordName }', 'metadata.namespace': 'krateo-preview'})
    return f


def check_unowned_drafts_are_the_legacy_roots(chart):
    """restaction.unowned-drafts behind "Unowned drafts" on /portal-builder: the page roots a
    preview wrote before drafts had owners. A root with an owner, a child Flex, and anything that
    is not a preview never appear; nothing to read is no rows. Every widget is CRD-validated."""
    f = []

    def flex(name, created, owner=None, purpose='preview-draft', children=()):
        labels = {'krateo.io/purpose': purpose, 'krateo.io/preview-session': 'unattributed'}
        if owner:
            labels['krateo.io/draft-owner'] = owner
        return {'metadata': {'name': name, 'labels': labels, 'creationTimestamp': created},
                'spec': {'resourcesRefs': {'items': [{'id': c, 'name': c, 'resource': r, 'namespace': 'krateo-preview'} for r, c in children]}}}

    items = [
        flex('page-agentprobe3', '2026-09-21T14:12:00Z', children=[('pageheaders', 'page-agentprobe3-header'), ('flexes', 'page-agentprobe3-body')]),
        flex('page-agentprobe3-body', '2026-09-21T14:12:00Z'),
        flex('page-pod-sizing', '2026-09-24T18:49:00Z', children=[('pageheaders', 'h'), ('cards', 'c'), ('tables', 't'), ('rows', 'r')]),
        flex('page-alert-catalog', '2026-09-16T10:05:00Z', children=[('pageheaders', 'h')]),
        flex('page-mine', '2026-09-29T10:00:00Z', owner='diego'),
        flex('page-shipped', '2026-09-20T10:00:00Z', purpose='page'),
        flex('layout-row', '2026-09-20T10:00:00Z'),
    ]
    out = resolve(chart, 'unowned-drafts', {'roots': {'items': items}}, {})
    rows = out['items']
    expect(f, 'only ownerless preview roots, newest first', [r['rootName'] for r in rows],
           ['page-pod-sizing', 'page-agentprobe3', 'page-alert-catalog'])
    expect(f, 'slug, widgets and adopt path', rows[1], {'rootName': 'page-agentprobe3', 'slug': 'agentprobe3', 'created': '2026-09-21T14:12:00Z',
                                                         'widgets': 2, 'adoptPath': '/portal-builder/compose?adopt=page-agentprobe3'})
    for label, resp in (('prewarm, no response', {}), ('the sandbox could not be read', {'roots': 'ERROR'})):
        expect(f, f'{label}: no rows', resolve(chart, 'unowned-drafts', resp, {})['items'], [])

    card = resolved_widget(chart, 'Card', 'unowned-drafts-card', out, {}, 'unowned-drafts-card (three)')['spec']['widgetData']
    expect(f, 'card: how many, over which days', card['extra'], '3 drafts · 16–24 Sep')
    for label, roots, says in (('none', [], 'none left'),
                               ('one', [items[3]], '1 draft · 16 Sep'),
                               ('across months', [items[3], flex('page-old', '2026-08-30T08:00:00Z')], '2 drafts · 30 Aug – 16 Sep')):
        got = resolved_widget(chart, 'Card', 'unowned-drafts-card', resolve(chart, 'unowned-drafts', {'roots': {'items': roots}}, {}), {},
                              f'unowned-drafts-card ({label})')['spec']['widgetData']['extra']
        expect(f, f'card, {label}: {says}', got, says)
    for label, ra_out in (('three', out), ('prewarm', resolve(chart, 'unowned-drafts', {}, {}))):
        doc = resolved_widget(chart, 'Listy', 'unowned-drafts', ra_out, {}, f'unowned-drafts ({label})')
        listy_placeholders_resolve(f, chart, 'unowned-drafts', doc['spec']['widgetData']['dataSource'], label)
    listed = resolved_widget(chart, 'Listy', 'unowned-drafts', out, {}, 'unowned-drafts (three, again)')['spec']['widgetData']['dataSource']
    expect(f, 'list: widget counts read as words', [r['widgetsLabel'] for r in listed], ['4 widgets', '2 widgets', '1 widget'])
    spec = chart.get('Listy', 'unowned-drafts')['spec']
    ref = spec['resourcesRefs']['items'][0]
    expect(f, 'Discard deletes a Flex in the sandbox, never by a root\'s own name', [ref['verb'], ref['resource'], ref['namespace'], ref['name'].startswith('page-')],
           ['DELETE', 'flexes', 'krateo-preview', False])
    return f


CHECKS = [
    check_index_name_keeps_its_index_chart,
    check_install_page_links_the_change_request,
    check_install_needs_the_registration_file,
    check_page_set_next_step_is_a_tag,
    check_merged_is_one_colour,
    check_the_step_is_called_install,
    check_install_header_has_no_lone_v,
    check_install_applies_the_status_projection,
    check_review_proposals_are_not_builder_publishes,
    check_create_form_says_when_the_blueprint_is_not_registered_yet,
    check_marketplace_detail_resolves_without_a_name,
    check_render_draft_reads_the_chart_from_its_configmap,
    check_my_drafts_are_the_callers_own,
    check_unowned_drafts_are_the_legacy_roots,
]


def strict(schema):
    """Close an openAPIV3Schema the way the apiserver's strict field validation does (the same
    closure test-composition-architecture.py applies)."""
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


def validate_resolved(crds_dir):
    """Every widget the checks resolved, against its CRD. Returns the problems."""
    import jsonschema
    crds = {}
    for path in glob.glob(os.path.join(crds_dir, '**', '*.yaml'), recursive=True):
        for doc in yaml.safe_load_all(open(path)):
            if isinstance(doc, dict) and doc.get('kind') == 'CustomResourceDefinition':
                crds[doc['spec']['names']['kind']] = doc
    problems = []
    for label, doc in RESOLVED:
        crd = crds.get(doc['kind'])
        if crd is None:
            problems.append(f'{label}: no CRD for kind {doc["kind"]} in {crds_dir}')
            continue
        version = doc['apiVersion'].split('/')[1]
        served = [v for v in crd['spec']['versions'] if v['name'] == version]
        if not served:
            problems.append(f'{label}: the {doc["kind"]} CRD serves no {version}')
            continue
        schema = strict(copy.deepcopy(served[0]['schema']['openAPIV3Schema']))
        schema.setdefault('properties', {}).update({'metadata': {'type': 'object'},
                                                    'apiVersion': {'type': 'string'}, 'kind': {'type': 'string'}})
        for err in sorted(jsonschema.Draft7Validator(schema).iter_errors(doc), key=lambda e: list(e.path)):
            problems.append(f'{label}: {"/".join(map(str, err.path))}: {err.message[:200]}')
    return problems


def main():
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        i = args.index('--crds')
        crds_dir = args[i + 1]
        del args[i:i + 2]
    chart_dir = args[0] if args else 'helm/portal'
    chart = Chart(render(chart_dir))
    failed = 0
    for check in CHECKS:
        try:
            problems = check(chart)
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        name = check.__name__.replace('check_', '').replace('_', ' ')
        print(f'[{"FAIL" if problems else "PASS"}] {name}')
        for problem in dict.fromkeys(problems):     # one line per distinct problem, in order
            print(f'        {problem}')
        failed += bool(problems)
    total = len(CHECKS)
    if crds_dir:
        total += 1
        problems = validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(RESOLVED)} resolved widgets validate against their CRDs')
        for problem in dict.fromkeys(problems):
            print(f'        {problem}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({JQ}{"" if crds_dir else "; resolved widgets NOT validated (no --crds)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
