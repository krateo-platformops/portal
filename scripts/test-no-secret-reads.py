#!/usr/bin/env python3
"""
test-no-secret-reads — the RESTActions whose paths come from DATA never GET a Secret, and their
pages say so instead of guessing.

WHAT IT GUARDS. lint-ra-secrets.py proves every data-driven step's iterator applies
portal.fetchableDefs; this resolves them and checks what that means on the page:
  1. composition-resources: a composition managing a Secret (19 on krateo-057) requests every
     managed object EXCEPT the Secret; the Secret is still a row, state "unread", with no link,
     outside the convergence count, and the rail says how many were not read.
  2. resource-detail on /resources/<ns>/core/v1/secrets/<name>: discovery is read, the object is
     not, and the header says the portal does not read Secrets — not "Not found".
  3. fetchablePath itself: non-core groups pass, core passes only for discovery, namespaces and
     the allowlisted kinds.
  Every resolved widget is validated against its CRD with --crds.

Usage: test-no-secret-reads.py [--crds DIR]. Exit code = failed checks.
"""
import copy
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tbi = _load('tbi', 'test-builder-install.py')
gates = _load('gates', 'test-ra-input-gates.py')
expect = tbi.expect
CHART = None

CM = '/api/v1/namespaces/team-a/configmaps/c1-values'
SECRET = '/api/v1/namespaces/team-a/secrets/c1-credentials'
REPO = '/apis/github.krateo.io/v1alpha1/namespaces/team-a/repoes/c1-repo'
COMP = copy.deepcopy(gates.COMP)
COMP['status'] = {'managed': [{'apiVersion': 'v1', 'resource': 'configmaps', 'name': 'c1-values', 'path': CM},
                              {'apiVersion': 'v1', 'resource': 'secrets', 'name': 'c1-credentials', 'path': SECRET},
                              {'apiVersion': 'github.krateo.io/v1alpha1', 'resource': 'repoes', 'name': 'c1-repo', 'path': REPO}],
                  'conditions': []}


def check_composition_resources_skip_the_secret():
    f = []
    ra = CHART.get('RESTAction', 'composition-resources')
    responses = {'crds': {'items': [gates.CD]},
                 'found': {'/apis/composition.krateo.io/v0-1-0/keystonedemoes': {'items': [COMP]}},
                 'resources': {CM: {'kind': 'ConfigMap', 'metadata': {'name': 'c1-values', 'namespace': 'team-a'}},
                               REPO: {'kind': 'Repo', 'metadata': {'name': 'c1-repo', 'namespace': 'team-a'},
                                      'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}},
                               SECRET: {'kind': 'Secret', 'metadata': {'name': 'c1-credentials'}, 'data': {'token': 'eA=='}}}}
    requests, _, out = gates.resolve(ra, {'namespace': 'team-a', 'name': 'c1'}, responses)
    expect(f, 'requested', sorted(p for s, p, _ in requests if s == 'resources'), sorted([CM, REPO]))
    rows = {r['name']: r for r in out['resources']}
    expect(f, 'the Secret is a row, unread, unlinked', {k: rows['c1-credentials'][k] for k in ('kind', 'state', 'navHref')},
           {'kind': 'Secret', 'state': 'unread', 'navHref': ''})
    expect(f, 'the others are read', [rows['c1-values']['state'], rows['c1-repo']['state']], ['healthy', 'healthy'])
    expect(f, 'counts', {k: out[k] for k in ('total', 'healthyCount', 'unreadCount', 'convergencePercent', 'converged')},
           {'total': 2, 'healthyCount': 2, 'unreadCount': 1, 'convergencePercent': 100, 'converged': True})
    rail = tbi.resolved_widget(CHART, 'Listy', 'list-composition-detail-rail', out, {}, 'rail (a Secret managed)')
    expect(f, 'rail label', rail['spec']['widgetData']['dataSource'][0]['label'], '100% match · 2/2 resources · 1 not read')
    rel = tbi.resolved_widget(CHART, 'Listy', 'list-composition-detail-relations', out, {}, 'relations (a Secret managed)')
    expect(f, 'relations: the unread colour', rel['spec']['widgetData']['itemTemplate']['color']['map'].get('unread'), 'gray')
    expect(f, 'relations: the Secret row', [r['stateLabel'] for r in rel['spec']['widgetData']['dataSource'] if r['name'] == 'c1-credentials'], ['Unread'])
    return f


def check_resource_detail_does_not_read_a_secret():
    f = []
    ra = CHART.get('RESTAction', 'resource-detail')
    extras = {'group': 'core', 'version': 'v1', 'plural': 'secrets', 'namespace': 'team-a', 'name': 'c1-credentials'}
    disco = {'resources': [{'name': 'secrets', 'kind': 'Secret', 'namespaced': True}]}
    requests, _, out = gates.resolve(ra, extras, {'disco': disco, 'object': {'kind': 'Secret', 'metadata': {'name': 'c1-credentials'}}})
    expect(f, 'requests', [(s, p) for s, p, _ in requests], [('disco', '/api/v1')])
    expect(f, 'header', [out['found'], out['subtitle'], out['manifestJson']], [False, 'Not read: the portal does not read secrets', ''])
    # A ConfigMap still reads.
    extras = dict(extras, plural='configmaps', name='c1-values')
    requests, _, out = gates.resolve(ra, extras, {'disco': {'resources': [{'name': 'configmaps', 'kind': 'ConfigMap', 'namespaced': True}]},
                                                  'object': {'kind': 'ConfigMap', 'metadata': {'name': 'c1-values', 'namespace': 'team-a'}}})
    expect(f, 'a ConfigMap is read', [p for s, p, _ in requests if s == 'object'], ['/api/v1/namespaces/team-a/configmaps/c1-values'])
    return f


def check_fetchable_path():
    f = []
    defs = CHART.get('RESTAction', 'composition-resources')['spec']['filter'].split('(.name) as $n')[0]
    cases = {'/api/v1': True, '/api/v1/namespaces/x': True, '/api/v1/namespaces': True,
             '/api/v1/namespaces/x/configmaps/y': True, '/api/v1/nodes/n': True,
             '/api/v1/namespaces/x/secrets/y': False, '/api/v1/secrets': False, '/api/v1/namespaces/x/secrets': False,
             '/api/v1/namespaces/x/secrets?labelSelector=a': False, '/api/v1/namespaces/x/serviceaccounts/y/token': True,
             '/apis/apps/v1/namespaces/x/deployments/d': True, '/apis/github.krateo.io/v1alpha1/repoes': True,
             'https://example.com/api/v1/secrets': False, '': False}
    got = tbi.jq(defs + ' map(fetchablePath)', list(cases))
    expect(f, 'fetchablePath', dict(zip(cases, got)), cases)
    return f


CHECKS = [check_composition_resources_skip_the_secret, check_resource_detail_does_not_read_a_secret, check_fetchable_path]


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
