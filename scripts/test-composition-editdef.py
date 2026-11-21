#!/usr/bin/env python3
"""The composition Edit form leaves out the controller's own global keys, over the chart's OWN jq.

composition-dynamic-controller writes ten keys into spec.global on every render (plumbing
helm/utils/values.go InjectGlobalValues) and they win over a user's value, so editing one does
nothing. The UX review found them as ten empty inputs. restaction.composition-editdef removes them
from the form schema BY PATH (.properties.global only) and keeps every other key. This runs that
filter, exactly as it ships, over:

  controller keys only     no fields left: "Nothing to edit", and no `properties` key (no Save button)
  controller + user keys   global keeps imageRegistry; `required` loses the removed keys
  a top-level field named compositionName   stays: removal is by path, not by name
  no schema (CM missing)   unchanged: an empty schema, which stays the Form's error, not "Nothing to edit"
  values                   untouched, injected keys included, so a save keeps them
A control: with the key list emptied, the controller-keys-only case must fail.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.environ.get('COMPOSITIONS_CHART') or os.path.join(os.path.dirname(HERE), 'helm', 'compositions-page')
JQ = os.environ.get('JQ', 'jq')
INJECTED = ['compositionApiVersion', 'compositionGroup', 'compositionId', 'compositionInstalledVersion',
            'compositionKind', 'compositionName', 'compositionNamespace', 'compositionResource',
            'gracefullyPaused', 'krateoNamespace']


def render():
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', CHART, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        out = subprocess.run(['helm', 'template', 't', copy], capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def controller_global(extra=None, required=None):
    props = {k: {'type': 'string'} for k in INJECTED}
    props.update(extra or {})
    node = {'type': 'object', 'title': 'Global values', 'properties': props}
    if required is not None:
        node['required'] = required
    return node


SPEC = {'global': {'compositionId': 'u1', 'imageRegistry': ''}, 'alertPatcherGroups': ['sre']}


def run(prog, schema):
    comp = {'apiVersion': 'composition.krateo.io/v0-2-85', 'kind': 'AlertProvider',
            'metadata': {'name': 'alert-provider', 'namespace': 'krateo-system'}, 'spec': SPEC}
    data = {'name': 'alert-provider', 'namespace': 'krateo-system',
            'crds': [{'group': 'composition.krateo.io', 'kind': 'AlertProvider', 'plural': 'alertproviders',
                      'version': 'v0-2-85', 'cdNamespace': 'krateo-system'}],
            'found': [comp],
            'jsonschema': {'data': {'values.schema.json': json.dumps(schema)}} if schema is not None else {}}
    proc = subprocess.run([JQ, '-c', prog], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip())
    return json.loads(proc.stdout)


def main():
    docs = render()
    prog = next(d for d in docs if d['kind'] == 'RESTAction' and d['metadata']['name'] == 'composition-editdef')['spec']['filter']
    bad = []

    def check(label, got, want):
        if got != want:
            bad.append(label)
            print(f'FAIL {label}: got {got!r}, want {want!r}')

    only = run(prog, {'type': 'object', 'properties': {'global': controller_global()}})['schemaSpec']
    check('controller keys only: no properties key', 'properties' in only, False)
    check('controller keys only: says why', only.get('title'), 'Nothing to edit')

    mixed_schema = {'type': 'object', 'required': ['global'],
                    'properties': {'alertPatcherGroups': {'type': 'array', 'items': {'type': 'string'}},
                                   'imagePullSecrets': {'type': 'array', 'title': 'Image pull secrets'},
                                   'global': controller_global({'imageRegistry': {'type': 'string'}},
                                                               required=['compositionId', 'imageRegistry'])}}
    mixed = run(prog, mixed_schema)
    g = mixed['schemaSpec']['properties']['global']
    check('mixed: global keeps only imageRegistry', sorted(g['properties']), ['imageRegistry'])
    check('mixed: global.required loses the removed keys', g.get('required'), ['imageRegistry'])
    check('mixed: other fields untouched', sorted(mixed['schemaSpec']['properties']),
          ['alertPatcherGroups', 'global', 'imagePullSecrets'])
    check('mixed: root required keeps global', mixed['schemaSpec'].get('required'), ['global'])
    check('mixed: values untouched', mixed['values'], SPEC)

    named = run(prog, {'type': 'object', 'required': ['global'],
                       'properties': {'compositionName': {'type': 'string', 'title': 'Display name'},
                                      'global': controller_global()}})['schemaSpec']
    check('a top-level compositionName stays', sorted(named['properties']), ['compositionName'])
    check('an emptied global leaves root required', named.get('required'), [])

    missing = run(prog, None)['schemaSpec']
    check('no schema: unchanged (no "Nothing to edit")', missing, {})

    control = re.sub(r'\["compositionApiVersion".*?\] as \$injected', '[] as $injected', prog, count=1, flags=re.S)
    if '[] as $injected' not in control:
        bad.append('control')
        print('FAIL control: could not empty the key list (the filter text moved)')
    elif 'properties' not in run(control, {'type': 'object', 'properties': {'global': controller_global()}})['schemaSpec']:
        bad.append('control')
        print('FAIL control: with no keys removed, the controller-only schema still lost its fields')

    print(f'composition editdef: {9 + 1} checks + control, {len(bad)} failure(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
