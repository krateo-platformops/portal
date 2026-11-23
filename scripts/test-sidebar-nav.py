#!/usr/bin/env python3
"""
test-sidebar-nav — every sidebar page item says where its page lives.

A ref snowplow could not check comes back with an `error` and no path (snowplow >= 1.12.75). The frontend
then spells the page's address itself: flexes/page-<page> in the item's `namespace`, else the portal's own
namespace, where no page root lives (krateo-platformops/frontend#533). So for every item that carries a
resourceRefId, (namespace, page) must name the very root its ref names: the routes and aliases too.

Runs the chart's own filter over a page in each of two namespaces, with an alias, an unlabelled templated
route and a link-only entry. The control drops `namespace` from the filter and must be caught.

JQ=gojq runs the same checks on gojq. Exit code = failed checks.
"""
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)
JQ = os.environ.get('JQ', 'jq')
failures = 0


def check(name, ok, detail=''):
    global failures
    failures += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'' if ok else ': ' + detail}")


def page(name, namespace, **nav):
    return {'metadata': {'name': name, 'namespace': namespace,
                         'annotations': {f"krateo.io/nav-{k.replace('_', '-')}": v for k, v in nav.items()}}}


PAGES = {'pages': [
    page('page-reviews', 'krateo-reviews', path='/reviews', label='Platform review', order='14', group='Observability'),
    page('page-controller-builder', 'krateo-builders', path='/controller-builder', label='Controller Builder',
         order='23', alias='/kog-builder,/api-builder'),
    page('page-composition-detail', 'krateo-compositions', path='/compositions/{namespace}/{name}'),
    page('page-desk-team-a', 'team-a', path='/service-desk/team-a', label='Desk', link_only='true'),
]}


def run(expr):
    proc = subprocess.run([JQ, '-c', expr], input=json.dumps(PAGES), capture_output=True, text=True, check=False)
    if proc.returncode:
        raise SystemExit(f'{JQ}: {proc.stderr}')
    return json.loads(proc.stdout)


def misplaced(out):
    """Items whose (namespace, page) is not the root their ref names."""
    home = {r['id']: (r['namespace'], r['name']) for r in out['refs']}
    return [i['path'] for i in out['items'] if 'resourceRefId' in i
            and (i.get('namespace'), f"page-{i['page']}") != home.get(i['resourceRefId'])]


docs = tbi.render(os.path.join(HERE, '..', 'helm', 'portal'))
[ra] = [d for d in docs if d.get('kind') == 'RESTAction' and d['metadata']['name'] == 'sidebar-nav']
expr = ra['spec']['filter']
out = run(expr)
paths = sorted(i['path'] for i in out['items'])
check('every root and alias is an item', paths == sorted(
    ['/reviews', '/controller-builder', '/kog-builder', '/api-builder', '/compositions/{namespace}/{name}',
     '/service-desk/team-a']), str(paths))
check('every item with a ref names the root its ref names', misplaced(out) == [], str(misplaced(out)))
check('the link-only entry has neither a ref nor a namespace',
      [i for i in out['items'] if i.get('linkOnly')] == [
          {'path': '/service-desk/team-a', 'page': 'desk-team-a', 'order': 100, 'linkOnly': True, 'label': 'Desk'}],
      str([i for i in out['items'] if i.get('linkOnly')]))
stripped = expr.replace('namespace: $rootNs, ', '')
check('control: the filter without `namespace` is caught', stripped != expr and len(misplaced(run(stripped))) == 5,
      str(misplaced(run(stripped))))
print(f'sidebar nav: {len(out["items"])} items, {failures} failure(s)')
sys.exit(failures)
