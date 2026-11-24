#!/usr/bin/env python3
"""
test-policy-form — Add policy offers the namespaces the caller may create a policy in, by name.

agents-policy-formdef's `namespaces` stage had no filter, so the final filter iterated the List OBJECT
(kind, apiVersion, metadata, items) and the select offered "NamespacesList" and "v1": no policy could be
created (visual audit, 2026-11-24, krateo-057). The stage now yields names, like agent-formdef's.

Resolved by test-ra-input-gates' model of snowplow's resolver. The control drops the filter.
JQ=gojq runs the same checks on gojq. Exit code = failed checks.
"""
import copy
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('rig', os.path.join(HERE, 'test-ra-input-gates.py'))
rig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rig)
tbi = rig.tbi
failures = 0


def check(name, ok, detail=''):
    global failures
    failures += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'' if ok else ': ' + str(detail)[:300]}")


NAMES = ['team-b', rig.NS, 'team-a']
NAMESPACES = {'apiVersion': 'v1', 'kind': 'NamespacesList', 'metadata': {'resourceVersion': '1'},
              # `namespace` too: the resolver's refilter model reads that one (a Namespace has only a name).
              'items': [{'metadata': {'name': n, 'namespace': n}} for n in NAMES]}
CRD = {'spec': {'versions': [{'name': 'v1alpha1', 'served': True}]}}


def offered(ra, namespaces=NAMESPACES, caller=rig.ADMIN):
    """(the namespaces offered, the one chosen by default) of the resolved form."""
    responses = {'crd': CRD, 'namespaces': namespaces}
    _, _, out = rig.resolve(ra, {}, responses, caller)
    found = []

    def walk(value):
        if isinstance(value, dict):
            if 'enum' in value and 'description' in value and 'CREATE an AgentgatewayPolicy' in value['description']:
                found.append((value['enum'], value.get('default')))
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str) and value.startswith('{'):
            import json
            try:
                walk(json.loads(value))
            except ValueError:
                pass
    walk(out)
    return found[0] if found else None


docs = tbi.render(os.path.join(HERE, '..', 'helm', 'portal-agents'))
[ra] = [d for d in docs if d.get('kind') == 'RESTAction' and d['metadata']['name'] == 'agents-policy-formdef']
check('every namespace, by name, the release namespace chosen', offered(ra) == (sorted(NAMES), rig.NS), offered(ra))
check('only those the caller may create a policy in',
      offered(ra, caller=rig.tenant({'agentgateway.dev': ['team-a']})) == (['team-a'], 'team-a'),
      offered(ra, caller=rig.tenant({'agentgateway.dev': ['team-a']})))
check('a list that could not be read offers none', offered(ra, namespaces=None) == ([], ''), offered(ra, namespaces=None))
bare = copy.deepcopy(ra)
for step in bare['spec']['api']:
    if step['name'] == 'namespaces':
        step.pop('filter', None)
try:
    got = offered(bare)
except RuntimeError as exc:
    got = str(exc)
check('control: without the stage filter the names are not offered', got != (sorted(NAMES), rig.NS), got)
print(f'policy form: {failures} failure(s)')
sys.exit(failures)
