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
import subprocess
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


def pr(publish, number=7, state='open', merged=tbi.ABSENT, merged_at=tbi.ABSENT):
    status = {'number': number, 'state': state, 'html_url': f'https://github.com/krateo-platformops/{publish[8:]}/pull/{number}'}
    if merged is not tbi.ABSENT:
        status['merged'] = merged
    if merged_at is not tbi.ABSENT:
        status['merged_at'] = merged_at
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


def pet_config(name='default', ns='team-a', ref=('pets-pet-credentials', None), kind=False):
    """A PetConfiguration as the Configure credentials form writes it: spec.authentication.bearer.tokenRef
    naming the Secret (krateo-057's shape). ref=None: the authentication names no Secret."""
    obj = {'metadata': {'name': name, 'namespace': ns}, 'spec': {'authentication': {'bearer': {}}}}
    if ref is not None:
        obj['spec']['authentication']['bearer']['tokenRef'] = {'name': ref[0], 'key': 'token', **({'namespace': ref[1]} if ref[1] else {})}
    if kind:
        obj.update(kind='PetConfiguration', apiVersion='petstore.example.io/v1alpha1')
    return obj


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
        # A kind whose API needs credentials is NOT Ready until its Configuration names a Secret:
        # without one it fails at its first reconcile, not here.
        ('kinds ready, credentials needed', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
                                             'restdefs': items(restdef('pets-pet', 'pets', auth=True))},
         ('Configure credentials', 'orange', 'Configure credentials', '/controller-builder/configure/team-a/pets-pet')),
        ('ready, credentials configured', {'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
                                           'restdefs': items(restdef('pets-pet', 'pets', auth=True)),
                                           'configs': config_list(pet_config(kind=True))},
         ('Ready', 'green', '', '/compositions/team-a/pets')),
        ('ready, credentials configured, served from the cache with no item kind', {
            'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
            'restdefs': items(restdef('pets-pet', 'pets', auth=True)),
            'configs': config_list(pet_config(), served_by='informer')},
         ('Ready', 'green', '', '/compositions/team-a/pets')),
        ('kinds ready, the configuration names no Secret', {
            'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
            'restdefs': items(restdef('pets-pet', 'pets', auth=True)),
            'configs': config_list(pet_config(ref=None))},
         ('Configure credentials', 'orange', 'Configure credentials', '/controller-builder/configure/team-a/pets-pet')),
        ('kinds ready, another kind\'s configuration is not this one\'s', {
            'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
            'restdefs': items(restdef('pets-pet', 'pets', auth=True)),
            'configs': config_list(pet_config(), served_by='informer', kind='StoreConfiguration', plural='storeconfigurations')},
         ('Configure credentials', 'orange', 'Configure credentials', '/controller-builder/configure/team-a/pets-pet')),
        ('kinds ready, an empty configuration list', {
            'cds': items(cd('pet', ready='True')), 'installs': items(claim('pets', 'pet')),
            'restdefs': items(restdef('pets-pet', 'pets', auth=True)), 'configs': config_list()},
         ('Configure credentials', 'orange', 'Configure credentials', '/controller-builder/configure/team-a/pets-pet')),
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

    # The credential evidence, in the row and in its Next step cell.
    creds = dict(base, cds=items(cd('pet', ready='True')), installs=items(claim('pets', 'pet')),
                 restdefs=items(restdef('pets-pet', 'pets', auth=True), restdef('pets-store', 'pets', auth=True, gen='Store')))
    for label, configs, note in (
            ('no configuration', items(), 'no PetConfiguration exists yet (+1 more)'),
            ('names no Secret', config_list(pet_config(ref=None)), 'PetConfiguration team-a/default names no Secret (+1 more)'),
            ('both named', None, 'credentials configured (Secrets team-a/pets-pet-credentials, team-a/store-creds not verified)'),
            ('a ref in another namespace', 'other-ns', 'credentials configured (Secrets creds/shared, team-a/store-creds not verified)')):
        if configs is None or configs == 'other-ns':
            pet = pet_config(ref=('shared', 'creds')) if configs == 'other-ns' else pet_config()
            resp = dict(creds, configs={
                '/apis/petstore.example.io/v1alpha1/petconfigurations': config_list(pet),
                '/apis/petstore.example.io/v1alpha1/storeconfigurations': config_list(
                    pet_config(ref=('store-creds', None)), kind='StoreConfiguration', plural='storeconfigurations')})
        else:
            resp = dict(creds, configs=configs)
        _, out = resolve(resp)
        row = the_row(out)
        expect(f, f'credentials evidence, {label}', row['nextNote'], note)
        cells = {c['valueKey']: c for c in tbi.resolved_widget(CHART, 'Table', RA, out, {}, f'{RA} (credentials, {label})')['spec']['widgetData']['dataSource'][0]}
        want_next = (row['next'] + ' · ' + note) if row['next'] else note
        expect(f, f'Next step cell shows the evidence, {label}', cells['next']['stringValue'], want_next)
    # One configured kind: the singular wording.
    _, out = resolve(dict(base, cds=items(cd('pet', ready='True')), installs=items(claim('pets', 'pet')),
                          restdefs=items(restdef('pets-pet', 'pets', auth=True)), configs=config_list(pet_config())))
    expect(f, 'credentials evidence, one Secret', the_row(out)['nextNote'], 'credentials configured (Secret team-a/pets-pet-credentials not verified)')
    # A ref with a name but no key is not a configured credential.
    _, out = resolve(dict(base, cds=items(cd('pet', ready='True')), installs=items(claim('pets', 'pet')),
                          restdefs=items(restdef('pets-pet', 'pets', auth=True)),
                          configs=config_list({'metadata': {'name': 'default', 'namespace': 'team-a'},
                                               'spec': {'authentication': {'bearer': {'tokenRef': {'name': 'pets-pet-credentials'}}}}})))
    expect(f, 'a ref with no key: not configured', (the_row(out)['status'], the_row(out)['nextNote']),
           ('Configure credentials', 'PetConfiguration team-a/default names no Secret'))
    # No Secret is ever read: no step path or access filter names secrets (lint-ra-secrets.py chart-wide).
    steps = CHART.get('RESTAction', RA)['spec']['api']
    expect(f, 'no step reads a Secret', [st['name'] for st in steps if 'secrets' in (st.get('path') or '')
                                         or ((st.get('userAccessFilter') or {}).get('resource') == 'secrets')], [])

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
    expect(f, 'the page action, into the composer', (buttons['controller-builder-compose']['type'], buttons['controller-builder-compose']['label'],
                                                  buttons['controller-builder-compose']['actions']['navigate'][0]['path']),
           ('primary', 'Build a controller', '/controller-builder/compose'))
    # A4 (frontend 1.7.0): the Autopilot entry point is a filled button, "Ask Autopilot →" with the
    # wand, never a link — and it still lands in the same composer.
    ask = buttons['controller-builder-ask']
    expect(f, 'Ask Autopilot is the filled entry point into the same composer',
           (ask['type'], ask['label'], ask['icon'], ask['actions']['navigate'][0]['path'].split('?')[0]),
           ('primary', 'Ask Autopilot →', 'fa-wand-magic-sparkles', '/controller-builder/compose'))
    for kind in ('Form',):
        stale = [d['metadata']['name'] for d in CHART.docs if d.get('kind') == kind and d['metadata']['name'].startswith('kog-')]
        expect(f, 'the two page Forms are gone', stale, [])
    return f



# ---------------------------------------------------------------------------------------------
# Configure credentials (/controller-builder/configure/{namespace}/{name})
# ---------------------------------------------------------------------------------------------

def config_crd(kind='PetConfiguration', group='petstore.example.io', schemes=None, extra=None):
    """A <Kind>Configuration CRD as oasgen 0.23.0 generates it: spec.authentication.bearer.tokenRef
    on all 35 of krateo-057's. `schemes` and `extra` (more spec fields) exercise the general case."""
    ref = {'type': 'object', 'required': ['key', 'name', 'namespace'],
           'properties': {'key': {'type': 'string'}, 'name': {'type': 'string'}, 'namespace': {'type': 'string'}}}
    schemes = schemes or {'bearer': ['tokenRef']}
    auth = {'type': 'object', 'description': 'The authentication methods available for this API.',
            'properties': {s: {'type': 'object', 'required': refs, 'properties': {r: ref for r in refs}} for s, refs in schemes.items()}}
    spec = {'type': 'object', 'properties': dict({'authentication': auth}, **(extra or {}))}
    return {'metadata': {'name': kind.lower() + 's.' + group},
            'spec': {'group': group, 'versions': [{'name': 'v1alpha1', 'served': True,
                                                   'schema': {'openAPIV3Schema': {'properties': {'spec': spec}}}}]}}


SECRET = 'correct-horse-battery-staple'
SECRETJQ = os.path.join(HERE, 'frontend-secretjq')


def frontend_plans(widget_data, values):
    """The frontend's OWN verdict on every override (frontend#424 secretJq.planOverride, vendored in
    scripts/frontend-secretjq): local / jq (with the exact data sent) / refused. Needs
    `npm ci --prefix scripts/frontend-secretjq` (the CI job does it)."""
    tsx = os.path.join(SECRETJQ, 'node_modules', '.bin', 'tsx')
    if not os.path.exists(tsx):
        raise RuntimeError(f'{tsx} is missing: run `npm ci --prefix scripts/frontend-secretjq`')
    proc = subprocess.run([tsx, os.path.join(SECRETJQ, 'plan.ts')], input=json.dumps({'widgetData': widget_data, 'values': values}),
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f'plan.ts failed: {proc.stderr.strip()[:500]}')
    return json.loads(proc.stdout)


def bodies(widget_data, values):
    """The bodies a submit sends, built as buildPayload builds them — each override resolved the way
    the frontend resolves it (locally, or by /jq over the secret-free data it would send) — and
    every refusal, which would stop the whole submit before any request."""
    plans = frontend_plans(widget_data, values)
    action = widget_data['actions']['rest'][0]
    out, refused, sent = [], [], []
    for op, planned in zip(action['ops'], plans['actions'][0]['ops']):
        body = copy.deepcopy(op.get('payload') or {})
        for o in planned['overrides']:
            if o['mode'] == 'refused':
                refused.append(o['name'] + ': ' + o['error'])
                continue
            if o['mode'] == 'jq':
                sent.append((o['name'], o['data']))
                value = tbi.jq(o['expression'].strip()[2:-1], o['data'])
            else:
                value = o['value']
            tbi.set_path(body, o['name'], value)
        out.append(body)
    return out, refused, sent, plans['secretPaths']


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
    # The token reference is DERIVED from the Secret's fields, not shown: on every 057 kind the
    # form is the configuration's name and the Secret.
    expect(f, 'field order: the configuration, then the Secret; the reference is not a field', list(schema['properties']),
           ['__configuration_name__', '__secret_name__', '__secret_key__', '__secret_value__'])
    expect(f, 'the credential is a password field, write-only', {k: schema['properties']['__secret_value__'].get(k) for k in ('format', 'writeOnly')},
           {'format': 'password', 'writeOnly': True})
    expect(f, 'everything a save needs is required', sorted(schema['required']),
           sorted(['__configuration_name__', '__secret_name__', '__secret_key__', '__secret_value__']))
    expect(f, 'pre-filled: the configuration and the Secret, never the credential', out['initialValues'],
           {'__configuration_name__': 'pets-pet', '__secret_name__': 'pets-pet-credentials', '__secret_key__': 'token'})
    expect(f, 'no credential value anywhere in what the RA serves', SECRET in json.dumps(out), False)

    form = tbi.resolved_widget(CHART, 'Form', 'controller-configure', out, extras, 'controller-configure (named)')
    wd = form['spec']['widgetData']
    expect(f, 'no review page, no local draft', [wd.get('reviewBeforeSubmit', False), 'draftActionId' in wd], [False, False])
    action = wd['actions']['rest'][0]
    expect(f, 'no templated message or navigate target', [('${' in str(action.get(k, ''))) for k in ('successMessage', 'errorMessage', 'onSuccessNavigateTo')],
           [False, False, False])
    ops = action['ops']
    expect(f, 'Secret first, then the configuration', [o['resourceRefId'] for o in ops], ['create-secret', 'create-configuration'])

    # THE FRONTEND'S VERDICT (frontend#424, run from its own code): nothing refused, nothing sent
    # to /jq, the credential only ever a value in the Secret's stringData.
    values = dict(out['initialValues'], __secret_value__=SECRET)
    (secret, config), refused, sent, secret_paths = bodies(wd, values)
    expect(f, 'frontend: the credential is the one secret field', secret_paths, [['__secret_value__']])
    expect(f, 'frontend: no override is refused', refused, [])
    if refused:
        return f            # the browser would stop the submit here; there are no bodies to check
    expect(f, 'frontend: no override goes to /jq', [n for n, _ in sent], [])
    expect(f, 'the Secret body', secret, {'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
                                          'metadata': {'name': 'pets-pet-credentials', 'namespace': 'team-a',
                                                       'labels': {'krateo.io/managed-by': 'controller-builder'}},
                                          'stringData': {'token': SECRET}})
    expect(f, 'the configuration body', config, {'apiVersion': 'petstore.example.io/v1alpha1', 'kind': 'PetConfiguration',
                                                 'metadata': {'name': 'pets-pet', 'namespace': 'team-a'},
                                                 'spec': {'authentication': {'bearer': {'tokenRef': {'name': 'pets-pet-credentials', 'namespace': 'team-a', 'key': 'token'}}}}})
    expect(f, 'the credential is only in the Secret\'s stringData', [SECRET in json.dumps(config), SECRET in json.dumps({k: v for k, v in secret.items() if k != 'stringData'})],
           [False, False])
    # A renamed Secret AND a renamed key: the reference follows both.
    (secret2, config2), _, _, _ = bodies(wd, dict(values, __secret_name__='petstore-token', __secret_key__='apiKey'))
    expect(f, 'a renamed Secret and key: the Secret uses them', (secret2['metadata']['name'], secret2['stringData']), ('petstore-token', {'apiKey': SECRET}))
    expect(f, 'a renamed Secret and key: the reference follows both', config2['spec']['authentication']['bearer']['tokenRef'],
           {'name': 'petstore-token', 'namespace': 'team-a', 'key': 'apiKey'})
    refs = {r['id']: r for r in [
        {k: (tbi.jq(v[2:-1], out) if isinstance(v, str) and v.startswith('${') else v) for k, v in t['template'].items()}
        for t in form['spec']['resourcesRefsTemplate']]}
    expect(f, 'both write into the install\'s namespace', [(r['resource'], r['namespace'], r['verb']) for r in refs.values()],
           [('secrets', 'team-a', 'POST'), ('petconfigurations', 'team-a', 'POST')])

    # The general case: a second scheme and another spec field stay in the form and are sent as
    # typed (plain paths — still nothing to /jq); only the first scheme's reference is derived.
    crd = config_crd(schemes={'bearer': ['tokenRef'], 'basic': ['passwordRef']},
                     extra={'baseUrl': {'type': 'string', 'title': 'Base URL'}})
    _, _, out2 = gates.resolve(ra, extras, {'rd': rd, 'crd': crd})
    s2 = json.loads(out2['stringSchema'])
    expect(f, 'general: the other scheme and field stay; the first scheme\'s reference goes',
           (list(s2['properties']), list(s2['properties']['authentication']['properties'])),
           (['__configuration_name__', 'authentication', 'baseUrl', '__secret_name__', '__secret_key__', '__secret_value__'], ['bearer']))
    wd2 = tbi.resolved_widget(CHART, 'Form', 'controller-configure', out2, extras, 'controller-configure (two schemes)')['spec']['widgetData']
    typed = dict(out2['initialValues'], __secret_value__=SECRET, baseUrl='https://api.example.io',
                 authentication={'bearer': {'tokenRef': {'name': 'x', 'namespace': 'y', 'key': 'z'}}})
    (_, config3), refused3, sent3, _ = bodies(wd2, typed)
    expect(f, 'general: nothing refused, nothing to /jq', (refused3, [n for n, _ in sent3]), ([], []))
    expect(f, 'general: the typed fields go through; the first scheme\'s reference is the Secret\'s',
           (config3['spec']['baseUrl'], config3['spec']['authentication']['basic']['passwordRef']),
           ('https://api.example.io', {'name': 'pets-pet-credentials', 'namespace': 'team-a', 'key': 'password'}))
    # The FIRST scheme is the first by name (basic before bearer), whichever jq runs the RA.
    expect(f, 'general: the untouched scheme is sent as typed', config3['spec']['authentication']['bearer'],
           {'tokenRef': {'name': 'x', 'namespace': 'y', 'key': 'z'}})

    # The note names the Secret a half-finished save leaves, from the formdef output only.
    note = tbi.resolved_widget(CHART, 'Paragraph', 'controller-configure-note', out, extras, 'controller-configure-note (named)')
    expect(f, 'partial write: the note names the Secret and the way out', note['spec']['widgetData']['text'],
           'Saving writes the Secret first, then the PetConfiguration. If the PetConfiguration fails, the Secret stays: '
           'before saving again, delete Secret team-a/pets-pet-credentials (or the name you gave it), or give the Secret another name.')

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
        tbi.resolved_widget(CHART, 'Paragraph', 'controller-configure-note', o, ex, f'controller-configure-note ({label})')
    for kind, name in (('Flex', 'page-controller-configure'), ('Card', 'controller-configure-card')):
        tbi.RESOLVED.append((f'{name} (as authored)', copy.deepcopy(CHART.get(kind, name))))
    return f


def check_merged_is_the_providers_word():
    """github-provider-kog 0.3.2 reports `merged` and `merged_at`: merged=true is a merge (with its
    date in the Merged column, also once the row has moved past Merged), merged=false a plain
    Closed with no Register, and an absent field keeps the "Register if merged" hedge."""
    f = []
    P, at = 'publish-pet', '2026-09-30T14:02:11Z'
    base = {'publishes': items(*files(P)), 'cds': items(), 'installs': items(), 'restdefs': items(), 'configs': items()}
    cases = [
        ('merged=true', [pr(P, state='closed', merged=True, merged_at=at)], ('Merged', 'Register', at)),
        ('merged=true, state not closed', [pr(P, state='open', merged=True, merged_at=at)], ('Merged', 'Register', at)),
        ('merged=true, registering', [pr(P, state='closed', merged=True, merged_at=at)], ('Registering', '', at)),
        ('merged=false', [pr(P, state='closed', merged=False, merged_at=None)], ('Closed', '', '')),
        ('merged=false, stray merged_at', [pr(P, state='closed', merged=False, merged_at=at)], ('Closed', '', '')),
        ('merged absent', [pr(P, state='closed')], ('Closed', 'Register if merged', '')),
    ]
    for label, prs, want in cases:
        resp = dict(base, prs=items(*prs))
        if 'registering' in label:
            resp['cds'] = items(cd('pet'))
        _, out = resolve(resp)
        row = the_row(out)
        if row is None:
            f.append(f'{label}: no row for pet')
            continue
        expect(f, f'{label}: status, next step, mergedAt', (row['status'], row['next'], row.get('mergedAt')), want)
        cells = {c['valueKey']: c for c in tbi.resolved_widget(CHART, 'Table', RA, out, {}, f'{RA} (merged_at, {label})')['spec']['widgetData']['dataSource'][0]}
        expect(f, f'{label}: Merged cell', (cells['merged']['stringValue'], cells['merged'].get('format')), (want[2], 'relative'))
    return f


CHECKS = [check_the_ladder, check_merged_is_the_providers_word, check_only_controller_publishes, check_iterators_request_what_they_need,
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
