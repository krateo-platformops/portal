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

HOW. Renders helm/portal with helm (in a tempdir, with the CHART_VERSION placeholder stamped, as
lint-keyextras.py does), then models snowplow's RESTAction resolution closely enough to run the
chart's own jq: each api step's response is placed under the step's name, the step's own `filter`
runs over {<step>: response} and replaces it, a failed continueOnError step leaves only its
errorKey, the route's extras are merged in, and the top-level filter runs over the result. A
widget's `${ … }` expression is then evaluated over that output, as the frontend receives it.

Uses the `jq` binary (preinstalled on GitHub's ubuntu runners). snowplow runs gojq; JQ=gojq runs
the same checks on it.

Usage: test-builder-install.py [chart-dir]   (default helm/portal). Exit code = failed checks.
"""
import copy
import json
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


CHECKS = [
    check_index_name_keeps_its_index_chart,
    check_install_page_links_the_change_request,
    check_install_needs_the_registration_file,
    check_page_set_next_step_is_a_tag,
    check_merged_is_one_colour,
    check_the_step_is_called_install,
    check_install_header_has_no_lone_v,
]


def main():
    chart_dir = sys.argv[1] if len(sys.argv) > 1 else 'helm/portal'
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
    print(f'\n{len(CHECKS) - failed} of {len(CHECKS)} checks passed ({JQ})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
