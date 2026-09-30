#!/usr/bin/env python3
"""
test-controller-builder — /controller-builder, evaluated: its deliverables ladder, and its alignment
with /blueprint-builder.

WHAT IT GUARDS.
  1. restaction.controller-builder-deliverables joins a controller's BuilderPublish claim to its
     change request, CompositionDefinition, install claim, RestDefinitions and their
     <Kind>Configurations. Each rung of the ladder — Publishing, Publish failed, Pushed, Change
     request open, Merged, Closed, Registering, Registered, Installed, Ready, Failed — and each
     next step (Register, Install, Configure credentials) is a jq branch; every one is resolved
     here on fixtures shaped like the krateo-057 objects (publish-pet, github-provider-kog), with
     the row's destination. The prewarm (no extras, empty lists) resolves to no rows and makes no
     iterator request, and a blueprint publish never becomes a controller row.
  2. The page is built as /blueprint-builder is (the condition the mockup was approved on): the
     page root, header, both buttons, the drafts card and the deliverables card and table are
     compared with their blueprint counterparts with every string blanked. Only names, copy and
     the controller's own columns may differ.
  Every widget the checks resolve is validated, as resolved, against its CRD with --crds.

HOW. The chart is rendered and each RESTAction resolved by test-ra-input-gates.py's model of
snowplow's resolver (dependsOn order, iterators, the cluster-list collapse, userAccessFilter).

Uses the `jq` binary; JQ=gojq runs the same checks on it.
Usage: test-controller-builder.py [--crds DIR]. Exit code = failed checks.
"""
import copy
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tbi = _load('tbi', 'test-builder-install.py')
gates = _load('gates', 'test-ra-input-gates.py')
NS = tbi.NS
RA = 'controller-builder-deliverables'
expect = tbi.expect


# ---------------------------------------------------------------------------------------------
# Fixtures (krateo-057: publish-pet, builder-publish 1.8.57; github-provider-kog 0.3.1 installs)
# ---------------------------------------------------------------------------------------------

def lr(publish, builder='controller', file_name='restdefinition.yaml', path='/apis/pet', synced='True',
       reason='ReconcileSuccess', created='2026-09-30T16:17:00Z'):
    return {'metadata': {'name': f'{publish}-000', 'namespace': NS, 'creationTimestamp': created,
                         'labels': {'krateo.io/builder': builder, 'krateo.io/publish': publish}},
            'spec': {'fromResource': {'fileName': file_name}, 'toRepo': {'path': path, 'branch': 'builder/' + publish[8:]}},
            'status': {'targetBranch': 'builder/' + publish[8:],
                       'conditions': [{'type': 'Synced', 'status': synced, 'reason': reason}]}}


def files(publish, **kw):
    """A controller publish: a RestDefinition file and the root registration file."""
    return [lr(publish, **kw), lr(publish, file_name='compositiondefinition.yaml', path='/', **kw)]


def pr(publish, number=7, state='open', merged=tbi.ABSENT):
    status = {'number': number, 'state': state, 'html_url': f'https://github.com/krateo-platformops/{publish[8:]}/pull/{number}'}
    if merged is not tbi.ABSENT:
        status['merged'] = merged
    return {'metadata': {'name': f'{publish}-pr', 'creationTimestamp': '2026-09-30T16:18:00Z',
                         'labels': {'krateo.io/builder': 'controller', 'krateo.io/publish': publish}},
            'spec': {'title': f'feat(controller): {publish[8:]}'}, 'status': status}


def conditions(ready, reason=None, synced=None, message=''):
    """Ready (and Synced) as crossplane-runtime writes them. Creating() is Ready=False reason
    Creating; a reconcile that errors is Synced=False reason ReconcileError."""
    out = [] if ready is None else [{'type': 'Ready', 'status': ready,
                                     'reason': reason or {'True': 'Available', 'False': 'Creating'}.get(ready, ''),
                                     'message': message}]
    if synced is not None:
        out.append({'type': 'Synced', 'status': synced, 'reason': 'ReconcileSuccess' if synced == 'True' else 'ReconcileError'})
    return out


def cd(name, ready=None, kind='Pet', resource='pets', ns=NS, reason=None, synced=None, message=''):
    status = {'kind': kind, 'apiVersion': 'composition.krateo.io/v0-1-0', 'resource': resource}
    if ready is not None or synced is not None:
        status['conditions'] = conditions(ready, reason, synced, message)
    return {'metadata': {'name': name, 'namespace': ns}, 'spec': {'chart': {'version': '0.1.0'}}, 'status': status}


def claim(name, cd_name, ns='team-a', kind='Pet', synced='True'):
    # No .kind: an informer-served object may carry none, and the join must not need it.
    return {'metadata': {'name': name, 'namespace': ns,
                         'labels': {'krateo.io/composition-definition-name': cd_name,
                                    'krateo.io/composition-definition-namespace': NS}},
            'status': {'conditions': conditions('True' if synced == 'True' else 'False', None, synced)}}


def restdef(name, claim_name, ready='True', auth=False, ns='team-a', comp_kind='Pet', gen='Pet', reason=None, synced=None):
    rd = {'metadata': {'name': name, 'namespace': ns,
                       'labels': {'krateo.io/composition-kind': comp_kind, 'krateo.io/composition-name': claim_name,
                                  'krateo.io/composition-namespace': ns}},
          'spec': {'resourceGroup': 'petstore.example.io'},
          'status': {'resource': {'kind': gen, 'apiVersion': 'petstore.example.io/v1'},
                     'conditions': conditions(ready, reason, synced)}}
    if auth:
        rd['status']['hasSecuritySchemes'] = True
        rd['status']['configuration'] = {'kind': gen + 'Configuration', 'apiVersion': 'petstore.example.io/v1alpha1'}
    return rd


def items(*objs):
    return {'items': list(objs)}


def config_list(*objs, served_by='apiserver', kind='PetConfiguration', plural='petconfigurations',
                api='petstore.example.io/v1alpha1'):
    """A configuration LIST as /call returns it: from the apiserver ("<Kind>List"), or from
    snowplow's informer cache ("<plural>List", informer_dispatch.go listKindForResource), whose
    items may carry no kind at all."""
    list_kind = kind + 'List' if served_by == 'apiserver' else plural[0].upper() + plural[1:] + 'List'
    return {'apiVersion': api, 'kind': list_kind, 'items': list(objs)}


def resolve(responses, extras=None):
    ra = CHART.get('RESTAction', RA)
    requests, _, out = gates.resolve(ra, extras or {}, responses)
    return requests, out


def the_row(out, chart='pet'):
    rows = [r for r in (out or {}).get('rows', []) if r['chart'] == chart]
    return rows[0] if rows else None


# ---------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------

def check_the_ladder():
    f = []
    P = 'publish-pet'
    base = {'publishes': items(*files(P)), 'prs': items(), 'cds': items(), 'installs': items(),
            'restdefs': items(), 'configs': items()}
    cases = [
        ('publishing', {'publishes': items(*files(P, synced='False', reason='Creating'))},
         ('Publishing', 'orange', '', '')),
        ('publish failed', {'publishes': items(*files(P, synced='False', reason='ReconcileError'))},
         ('Publish failed', 'red', '', '')),
        ('pushed, no change request (publish-pet today)', {}, ('Pushed', 'orange', '', '')),
        ('change request open', {'prs': items(pr(P))},
         ('Change request open', 'orange', '', 'https://github.com/krateo-platformops/pet/pull/7')),
        ('merged', {'prs': items(pr(P, state='closed', merged=True))},
         ('Merged', 'green', 'Register', '/marketplace/pet/install')),
        ('closed, merge not reported', {'prs': items(pr(P, state='closed'))},
         ('Closed', 'gray', 'Register if merged', '/marketplace/pet/install')),
        ('closed unmerged', {'prs': items(pr(P, state='closed', merged=False))},
         ('Closed', 'gray', '', 'https://github.com/krateo-platformops/pet/pull/7')),
        ('merged, no registration file: no Register', {'prs': items(pr(P, state='closed', merged=True)), 'publishes': items(lr(P))},
         ('Merged', 'green', '', 'https://github.com/krateo-platformops/pet/pull/7')),
        ('merged, definitions unreadable: no Register', {'prs': items(pr(P, state='closed', merged=True)), 'cds': None},
         ('Merged', 'green', '', 'https://github.com/krateo-platformops/pet/pull/7')),
        ('registering', {'prs': items(pr(P, state='closed', merged=True)), 'cds': items(cd('pet'))},
         ('Registering', 'orange', '', f'/blueprints/{NS}/pet')),
        # core-provider writes Ready=False reason Creating while it works, and Unavailable while
        # the generated CRD does not exist yet: progress, never red.
        ('registering, Creating', {'cds': items(cd('pet', ready='False', synced='True'))}, ('Registering', 'orange', '', f'/blueprints/{NS}/pet')),
        ('registering, CRD not generated yet', {'cds': items(cd('pet', ready='False', reason='Unavailable', synced='True',
                                                                message='crd pets.composition.krateo.io does not exists yet'))},
         ('Registering', 'orange', '', f'/blueprints/{NS}/pet')),
        ('definition failed', {'cds': items(cd('pet', ready='False', synced='False'))}, ('Failed', 'red', '', f'/blueprints/{NS}/pet')),
        ('registered', {'cds': items(cd('pet', ready='True'))}, ('Registered', 'green', 'Install', f'/blueprints/{NS}/pet/new')),
        ('registered, install claims unreadable: no Install', {'cds': items(cd('pet', ready='True')), 'installs': None},
         ('Registered', 'green', '', f'/blueprints/{NS}/pet')),
        ('installed, kinds not ready yet', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
                                            'restdefs': items(restdef('pets-pet', 'pets', ready='Unknown'))},
         ('Installed', 'orange', '', '/compositions/team-a/pets')),
        ('installed, a kind still Creating', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
                                              'restdefs': items(restdef('pets-pet', 'pets'), restdef('pets-store', 'pets', ready='False', gen='Store', synced='True'))},
         ('Installed', 'orange', '', '/compositions/team-a/pets')),
        ('installed, a kind Unavailable', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
                                           'restdefs': items(restdef('pets-pet', 'pets', ready='False', reason='Unavailable'))},
         ('Installed', 'orange', '', '/compositions/team-a/pets')),
        ('install failed', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet', synced='False'))},
         ('Failed', 'red', '', '/compositions/team-a/pets')),
        ('installed, nothing rendered yet', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet'))},
         ('Installed', 'orange', '', '/compositions/team-a/pets')),
        ('ready', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
                   'restdefs': items(restdef('pets-pet', 'pets'), restdef('pets-store', 'pets', gen='Store'))},
         ('Ready', 'green', '', '/compositions/team-a/pets')),
        ('ready, credentials needed', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
                                       'restdefs': items(restdef('pets-pet', 'pets', auth=True))},
         ('Ready', 'green', 'Configure credentials', '/controller-builder/configure/team-a/pets-pet')),
        ('ready, credentials configured', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
                                           'restdefs': items(restdef('pets-pet', 'pets', auth=True)),
                                           'configs': config_list({'kind': 'PetConfiguration', 'apiVersion': 'petstore.example.io/v1alpha1',
                                                                   'metadata': {'name': 'default', 'namespace': 'team-a'}})},
         ('Ready', 'green', '', '/compositions/team-a/pets')),
        ('ready, credentials configured, served from the cache with no item kind', {
            'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
            'restdefs': items(restdef('pets-pet', 'pets', auth=True)),
            'configs': config_list({'metadata': {'name': 'default', 'namespace': 'team-a'}}, served_by='informer')},
         ('Ready', 'green', '', '/compositions/team-a/pets')),
        ('ready, another kind\'s configuration is not this one\'s', {
            'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
            'restdefs': items(restdef('pets-pet', 'pets', auth=True)),
            'configs': config_list({'metadata': {'name': 'x'}}, served_by='informer', kind='StoreConfiguration', plural='storeconfigurations')},
         ('Ready', 'green', 'Configure credentials', '/controller-builder/configure/team-a/pets-pet')),
        ('ready, an empty configuration list', {
            'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
            'restdefs': items(restdef('pets-pet', 'pets', auth=True)), 'configs': config_list()},
         ('Ready', 'green', 'Configure credentials', '/controller-builder/configure/team-a/pets-pet')),
        ('ready, configurations unreadable: no claim either way', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
                                                                   'restdefs': items(restdef('pets-pet', 'pets', auth=True)), 'configs': None},
         ('Ready', 'green', '', '/compositions/team-a/pets')),
        ('a kind failed', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
                           'restdefs': items(restdef('pets-pet', 'pets'), restdef('pets-store', 'pets', ready='False', gen='Store', synced='False'))},
         ('Failed', 'red', '', '/compositions/team-a/pets')),
    ]
    for label, over, (status, color, nxt, href) in cases:
        resp = dict(base)
        for k, v in over.items():
            if v is None:
                resp.pop(k)          # unserved: the request fails, continueOnError records it
            else:
                resp[k] = v
        _, out = resolve(resp)
        row = the_row(out)
        if row is None:
            f.append(f'{label}: no row for pet')
            continue
        expect(f, f'{label}: status, colour, next step, destination', (row['status'], row['statusColor'], row['next'], row['rowHref']),
               (status, color, nxt, href))
        tbi.resolved_widget(CHART, 'Table', RA, out, {}, f'{RA} ({label})')

    # The generated kinds: the first and how many more.
    _, out = resolve(dict(base, cds=items(cd('pet', ready='True')), installs=items(claim('pets', 'pet')),
                          restdefs=items(restdef('pets-pet', 'pets'), restdef('pets-store', 'pets', gen='Store'),
                                         restdef('other', 'someone-else', gen='Other'))))
    row = the_row(out)
    expect(f, 'generated kinds: this install\'s only', row['gvk'], 'Pet · petstore.example.io/v1 +1')
    expect(f, 'version from the definition', row['version'], 'v0.1.0')
    return f


def check_only_controller_publishes():
    f = []
    resp = {'publishes': items(*files('publish-pet'), *files('publish-mongodb', builder='blueprint'),
                               lr('publish-nightly-0928', builder='review')),
            'prs': items(pr('publish-mongodb', state='open')), 'cds': items(cd('mongodb', ready='True', kind='Mongodb', resource='mongodbs')),
            'installs': items(), 'restdefs': items(), 'configs': items()}
    requests, out = resolve(resp)
    expect(f, 'only the controller publish is a row', [r['chart'] for r in out['rows']], ['pet'])
    expect(f, 'no install list for a blueprint\'s definition', [p for s, p, _ in requests if s == 'installs'], [])
    # The stale filter is gone: a PR to krateo-oas with no publish label is not a row.
    legacy = {'metadata': {'name': 'kog-pet', 'labels': {}}, 'spec': {'repo': 'krateo-oas', 'head': 'builder/pet', 'title': 'pet'},
              'status': {'number': 3, 'state': 'open'}}
    _, out = resolve(dict(resp, publishes=items(), prs=items(legacy)))
    expect(f, 'a krateo-oas PR without a claim is not a row', out['rows'], [])
    return f


def check_iterators_request_what_they_need():
    f = []
    resp = {'publishes': items(*files('publish-pet')), 'prs': items(),
            'cds': items(cd('pet', ready='True'), cd('github-provider-kog', ready='True', kind='GithubProviderKog', resource='githubproviderkogs')),
            'installs': items(claim('pets', 'pet')), 'restdefs': items(restdef('pets-pet', 'pets', auth=True),
                                                                        restdef('ghk-repo', 'github-provider-kog', auth=True, ns=NS, comp_kind='GithubProviderKog', gen='Repo')),
            'configs': items()}
    requests, _ = resolve(resp)
    expect(f, 'installs: one cluster list, of the controller\'s kind only',
           [p for s, p, _ in requests if s == 'installs'], ['/apis/composition.krateo.io/v0-1-0/pets'])
    expect(f, 'configs: the controller install\'s configuration kind only',
           [p for s, p, _ in requests if s == 'configs'], ['/apis/petstore.example.io/v1alpha1/petconfigurations'])
    # The prewarm: no extras, every list empty or unserved — no iterator request, no rows.
    for label, resp in (('empty lists', {'publishes': items(), 'prs': items(), 'cds': items(), 'restdefs': items()}),
                        ('nothing served', {})):
        requests, out = resolve(resp)
        expect(f, f'prewarm, {label}: no iterator request', [s for s, _, _ in requests if s in ('installs', 'configs')], [])
        expect(f, f'prewarm, {label}: no rows', out['rows'], [])
        tbi.resolved_widget(CHART, 'Table', RA, out, {}, f'{RA} (prewarm, {label})')
    return f


def check_registry_groups_by_api():
    f = []
    ra = CHART.get('RESTAction', 'kog-restdefinitions')
    rds = items(restdef('b', 'x', ns=NS), restdef('a', 'x', ns=NS),
                dict(restdef('z', 'y', ns=NS), spec={'resourceGroup': 'github.krateo.io'}))
    _, _, out = gates.resolve(ra, {}, {'restdefs': rds})
    expect(f, 'rows by API group, then name', [(r['group'], r['name']) for r in out['rows']],
           [('github.krateo.io', 'z'), ('petstore.example.io', 'a'), ('petstore.example.io', 'b')])
    tbi.resolved_widget(CHART, 'Table', 'kog-restdefinitions', out, {}, 'kog-restdefinitions (three)')
    _, _, out = gates.resolve(ra, {}, {})
    tbi.resolved_widget(CHART, 'Table', 'kog-restdefinitions', out, {}, 'kog-restdefinitions (prewarm)')
    return f


def blank(x):
    """A widget spec with every string blanked: what is left is its structure."""
    if isinstance(x, dict):
        return {k: blank(v) for k, v in x.items()}
    if isinstance(x, list):
        return [blank(v) for v in x]
    return '' if isinstance(x, str) else x


ALIGNED = [
    # (kind, blueprint widget, controller widget, the paths allowed to differ, and why)
    ('Flex', 'page-blueprint-builder', 'page-kog-builder', {'items': 'the registry card', 'resourcesRefs': 'the registry card',
                                                             'annotations': 'the old routes stay aliases'}),
    ('PageHeader', 'blueprint-builder-page-header', 'controller-builder-page-header', {}),
    ('Button', 'blueprint-builder-ask', 'controller-builder-ask', {}),
    ('Button', 'blueprint-builder-compose', 'controller-builder-compose', {}),
    ('Card', 'blueprint-builder-drafts-card', 'controller-builder-drafts-card', {}),
    ('Card', 'blueprint-builder-deliverables-card', 'controller-builder-deliverables-card', {}),
    ('Paragraph', 'blueprint-builder-deliverables-note', 'controller-builder-deliverables-note', {}),
    ('Table', 'blueprint-builder-deliverables', 'controller-builder-deliverables', {}),
]


def check_mirrors_the_blueprint_builder():
    f = []
    for kind, bp, ctl, allowed in ALIGNED:
        a, b = CHART.get(kind, bp), CHART.get(kind, ctl)
        sa, sb = blank(a['spec']), blank(b['spec'])
        if 'items' in allowed:
            for s in (sa, sb):
                s['widgetData'].pop('items', None)
                s.pop('resourcesRefs', None)
        expect(f, f'{kind} {ctl} is {bp} with its strings changed', json.dumps(sb, sort_keys=True), json.dumps(sa, sort_keys=True))
        if 'annotations' not in allowed:
            expect(f, f'{kind} {ctl}: same annotations', sorted(b['metadata'].get('annotations', {})), sorted(a['metadata'].get('annotations', {})))
    # The widgets with nothing to resolve are served as authored: validated as they are.
    for kind, name in [(k, c) for k, _, c, _ in ALIGNED] + [('Card', 'controller-builder-registry-card')]:
        tbi.RESOLVED.append((f'{name} (as authored)', copy.deepcopy(CHART.get(kind, name))))
    # The Table's CELLS, not only its columns: each table's dataSource jq, run on one row, gives
    # the cell list the frontend renders. Same cells in the same order with the same kind, type,
    # format and colour presence; only the sixth may differ — Marketplace there, Generated kinds
    # here, the controller-specific column.
    row = {k: 'x' for k in ('chart', 'title', 'version', 'status', 'statusColor', 'next', 'gvk', 'pr', 'age', 'ns', 'url', 'rowHref', 'marketLabel')}

    def cells(name):
        got = tbi.jq(CHART.expression('Table', name, 'dataSource'), {'rows': [row]})[0]
        return [{k: (v if k in ('kind', 'type', 'format') else '') for k, v in c.items() if k != 'valueKey'} for c in got]
    bp_cells, ctl_cells = cells('blueprint-builder-deliverables'), cells('controller-builder-deliverables')
    expect(f, 'deliverables Table: the same number of cells', len(ctl_cells), len(bp_cells))
    expect(f, 'deliverables Table: the same cell structure, bar the controller\'s own column',
           [c for i, c in enumerate(ctl_cells) if i != 5], [c for i, c in enumerate(bp_cells) if i != 5])
    cols = [c['valueKey'] for c in CHART.get('Table', 'controller-builder-deliverables')['spec']['widgetData']['columns']]
    hidden = [c['valueKey'] for c in tbi.jq(CHART.expression('Table', 'controller-builder-deliverables', 'dataSource'), {'rows': [row]})[0]
              if c['valueKey'] not in cols]
    expect(f, 'hidden cells: the same three the blueprint table carries', hidden, ['ns', 'url', 'rowHref'])
    page = CHART.get('Flex', 'page-kog-builder')['spec']['widgetData']
    expect(f, 'page order: header, drafts, deliverables, then the registry', [i['resourceRefId'] for i in page['items']],
           ['controller-builder-page-header', 'controller-builder-drafts-card', 'controller-builder-deliverables-card',
            'controller-builder-registry-card'])
    expect(f, 'page: no Form, no Flex wrapper, no bare Table', page['allowedResources'], ['pageheaders', 'cards'])
    buttons = {n: CHART.get('Button', n)['spec']['widgetData'] for n in ('controller-builder-ask', 'controller-builder-compose')}
    expect(f, 'one primary, into the composer', (buttons['controller-builder-compose']['type'], buttons['controller-builder-compose']['label'],
                                                  buttons['controller-builder-compose']['actions']['navigate'][0]['path']),
           ('primary', 'Build a controller', '/controller-builder/compose'))
    expect(f, 'Ask Autopilot is a link into the same composer', (buttons['controller-builder-ask']['type'],
                                                                  buttons['controller-builder-ask']['actions']['navigate'][0]['path'].split('?')[0]),
           ('link', '/controller-builder/compose'))
    for kind in ('Form',):
        stale = [d['metadata']['name'] for d in CHART.docs if d.get('kind') == kind and d['metadata']['name'].startswith('kog-')]
        expect(f, 'the two page Forms are gone', stale, [])
    return f



# ---------------------------------------------------------------------------------------------
# Configure credentials (/controller-builder/configure/{namespace}/{name})
# ---------------------------------------------------------------------------------------------

def config_crd(kind='PetConfiguration', group='petstore.example.io'):
    """A <Kind>Configuration CRD as oasgen 0.23.0 generates it (AutolinkConfiguration on krateo-057)."""
    ref = {'type': 'object', 'required': ['key', 'name', 'namespace'],
           'properties': {'key': {'type': 'string'}, 'name': {'type': 'string'}, 'namespace': {'type': 'string'}}}
    spec = {'type': 'object', 'properties': {'authentication': {
        'type': 'object', 'description': 'The authentication methods available for this API.',
        'properties': {'bearer': {'type': 'object', 'required': ['tokenRef'], 'properties': {'tokenRef': ref}}}}}}
    return {'metadata': {'name': kind.lower() + 's.' + group},
            'spec': {'group': group, 'versions': [{'name': 'v1alpha1', 'served': True,
                                                   'schema': {'openAPIV3Schema': {'properties': {'spec': spec}}}}]}}


SECRET = 'correct-horse-battery-staple'


def payload_for(op, values):
    """What useHandleActions.buildPayload sends for one op: the op payload, then each override —
    a ${ } evaluated over {json: <form values>}, anything else verbatim."""
    body = copy.deepcopy(op.get('payload') or {})
    for o in op.get('payloadToOverride') or []:
        v = o['value']
        if isinstance(v, str) and v.startswith('${'):
            v = tbi.jq(v[2:-1], {'json': values})
        tbi.set_path(body, o['name'], v)
    return body


def check_configure_credentials():
    f = []
    ra = CHART.get('RESTAction', 'controller-configure-formdef')
    rd = restdef('pets-pet', 'pets', auth=True)
    extras = {'namespace': 'team-a', 'name': 'pets-pet'}
    requests, _, out = gates.resolve(ra, extras, {'rd': rd, 'crd': config_crd()})
    expect(f, 'reads the RestDefinition, then its configuration CRD, as the caller', [(s, p) for s, p, _ in requests],
           [('rd', '/apis/ogen.krateo.io/v1alpha1/namespaces/team-a/restdefinitions/pets-pet'),
            ('crd', '/apis/apiextensions.k8s.io/v1/customresourcedefinitions/petconfigurations.petstore.example.io')])
    schema = json.loads(out['stringSchema'])
    expect(f, 'field order: the configuration, its spec, then the Secret', list(schema['properties']),
           ['__configuration_name__', 'authentication', '__secret_name__', '__secret_key__', '__secret_value__'])
    expect(f, 'the credential is a password field, write-only', {k: schema['properties']['__secret_value__'].get(k) for k in ('format', 'writeOnly')},
           {'format': 'password', 'writeOnly': True})
    expect(f, 'everything a save needs is required', sorted(schema['required']),
           sorted(['__configuration_name__', 'authentication', '__secret_name__', '__secret_key__', '__secret_value__']))
    expect(f, 'pre-filled: the Secret, and the reference pointing at it', out['initialValues'],
           {'__configuration_name__': 'pets-pet', '__secret_name__': 'pets-pet-credentials', '__secret_key__': 'token',
            'authentication': {'bearer': {'tokenRef': {'name': 'pets-pet-credentials', 'namespace': 'team-a', 'key': 'token'}}}})
    expect(f, 'no credential value anywhere in what the RA serves', SECRET in json.dumps(out), False)

    form = tbi.resolved_widget(CHART, 'Form', 'controller-configure', out, extras, 'controller-configure (named)')
    wd = form['spec']['widgetData']
    expect(f, 'no review page, no local draft', [wd.get('reviewBeforeSubmit', False), 'draftActionId' in wd], [False, False])
    action = wd['actions']['rest'][0]
    expect(f, 'messages and the navigate target are static', [('${' in str(action.get(k, ''))) for k in ('successMessage', 'errorMessage', 'onSuccessNavigateTo')],
           [False, False, False])
    ops = action['ops']
    expect(f, 'Secret first, then the configuration', [o['resourceRefId'] for o in ops], ['create-secret', 'create-configuration'])
    readers = [(i, o['name']) for i, op in enumerate(ops) for o in op['payloadToOverride'] if '__secret_value__' in str(o['value'])]
    expect(f, 'only the Secret\'s stringData reads the credential', readers, [(0, 'stringData')])
    wholes = [o['name'] for op in ops for o in op['payloadToOverride'] if re.search(r'\.json\s*(\||\)|\}|$)', str(o['value']))]
    expect(f, 'no override reads the whole form', wholes, [])

    # The two bodies the submit sends, for a person who kept the pre-fill and typed the token.
    values = dict(out['initialValues'], __secret_value__=SECRET)
    secret, config = (payload_for(op, values) for op in ops)
    expect(f, 'the Secret body', secret, {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                                          'metadata': {'name': 'pets-pet-credentials', 'namespace': 'team-a',
                                                       'labels': {'krateo.io/managed-by': 'controller-builder'}},
                                          'stringData': {'token': SECRET}})
    expect(f, 'the configuration body', config, {'apiVersion': 'petstore.example.io/v1alpha1', 'kind': 'PetConfiguration',
                                                 'metadata': {'name': 'pets-pet', 'namespace': 'team-a'},
                                                 'spec': {'authentication': {'bearer': {'tokenRef': {'name': 'pets-pet-credentials', 'namespace': 'team-a', 'key': 'token'}}}}})
    # A renamed Secret: the reference follows it, whatever the reference field still says.
    renamed = payload_for(ops[1], dict(values, __secret_name__='petstore-token'))
    expect(f, 'the reference follows a renamed Secret', renamed['spec']['authentication']['bearer']['tokenRef']['name'], 'petstore-token')
    refs = {r['id']: r for r in form['spec'].get('resourcesRefsTemplate') and [
        {k: (tbi.jq(v[2:-1], out) if isinstance(v, str) and v.startswith('${') else v) for k, v in t['template'].items()}
        for t in form['spec']['resourcesRefsTemplate']] or []}
    expect(f, 'both write into the install\'s namespace', [(r['resource'], r['namespace'], r['verb']) for r in refs.values()],
           [('secrets', 'team-a', 'POST'), ('petconfigurations', 'team-a', 'POST')])

    # Nothing to configure: no route params (the prewarm), a kind with no security scheme, a CRD
    # not served yet. A form with no fields, no write target, never a failed resolve.
    for label, ex, resp, title in (('prewarm', {}, {}, 'No controller kind selected'),
                                   ('no security scheme', extras, {'rd': restdef('pets-pet', 'pets')}, 'This kind needs no credentials'),
                                   ('CRD not served yet', extras, {'rd': rd}, 'This kind\'s configuration is still being generated')):
        requests, _, o = gates.resolve(ra, ex, resp)
        expect(f, f'{label}: says so', (o['ready'], o['schemaSpec'].get('title')), (False, title))
        if label == 'prewarm':
            expect(f, 'prewarm: no request', requests, [])
        tbi.resolved_widget(CHART, 'Form', 'controller-configure', o, ex, f'controller-configure ({label})')
        tbi.resolved_widget(CHART, 'PageHeader', 'controller-configure-page-header', o, ex, f'controller-configure-page-header ({label})')
    for kind, name in (('Flex', 'page-controller-configure'), ('Card', 'controller-configure-card')):
        tbi.RESOLVED.append((f'{name} (as authored)', copy.deepcopy(CHART.get(kind, name))))
    return f

CHECKS = [check_the_ladder, check_only_controller_publishes, check_iterators_request_what_they_need,
          check_registry_groups_by_api, check_mirrors_the_blueprint_builder, check_configure_credentials]
CHART = None


def main():
    global CHART
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        i = args.index('--crds')
        crds_dir = args[i + 1]
        del args[i:i + 2]
    CHART = tbi.Chart(tbi.render(args[0] if args else os.path.join(HERE, '..', 'helm', 'portal')))
    failed = 0
    for check in CHECKS:
        try:
            problems = check()
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {check.__name__.replace("check_", "").replace("_", " ")}')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    total = len(CHECKS)
    if crds_dir:
        total += 1
        problems = tbi.validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} resolved widgets validate against their CRDs')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}{"" if crds_dir else "; resolved widgets NOT validated (no --crds)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
