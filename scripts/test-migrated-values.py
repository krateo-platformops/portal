#!/usr/bin/env python3
"""Values a new blueprint version refuses (core-provider #623), over the charts' OWN jq.

After a migration core-provider dry-runs every composition on the served version and writes the
CompositionDefinition's MigratedValuesValid condition and status.migratedValues. Approved mockup:
https://claude.ai/artifact/MbNWkNtpomFXsWJPArjk3U. Inputs are core-provider's envtest sample
(~/krateo/samples/614-migrated-values, inlined), plus the cases the sample cannot show:
  refused       False/ValuesRefusedByNewVersion: default/held refuses spec.features.enabled
  unchecked     Unknown/ValidationIncomplete: default/blocked could not be checked
  allValid      True/AllValid, migratedValues absent: nothing anywhere
  listFailed    Unknown, migratedValues absent: "Not checked yet", never an all-clear
  capped        14 refused, 10 named: the Edit form of an unnamed one on the checked version says it
                may be among them; on another version it says nothing
This runs blueprint-detail's filter, the info Descriptions, the left column's items and the Alert, and
composition-editdef's filter with the Edit Form's notices, as they ship.
A control: dropping the refused branch must lose the Edit form's warning.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BLUEPRINTS = os.environ.get('BLUEPRINTS_CHART') or os.path.join(ROOT, 'helm', 'blueprints-page')
COMPOSITIONS = os.environ.get('COMPOSITIONS_CHART') or os.path.join(ROOT, 'helm', 'compositions-page')
JQ = os.environ.get('JQ', 'jq')

# ~/krateo/samples/614-migrated-values, as read back from a real apiserver.
REFUSED = {'version': 'v2-0-0', 'refusedCount': 1, 'uncheckedCount': 0,
           'refused': [{'namespace': 'default', 'name': 'held', 'fields': ['spec.features.enabled'], 'fieldCount': 1}]}
UNCHECKED = {'version': 'v2-0-0', 'refusedCount': 0, 'uncheckedCount': 1, 'unchecked': [{'namespace': 'default', 'name': 'blocked'}]}
COND = {
    'refused': {'type': 'MigratedValuesValid', 'status': 'False', 'reason': 'ValuesRefusedByNewVersion',
                'message': '1 of 2 instance(s) on version v2-0-0 hold values that version refuses; a write that changes those fields is refused until they satisfy it: default/held (spec.features.enabled)'},
    'unchecked': {'type': 'MigratedValuesValid', 'status': 'Unknown', 'reason': 'ValidationIncomplete',
                  'message': '1 of 1 instance(s) on version v2-0-0 could not be checked (the dry run failed for a reason that is not a spec validation error): default/blocked'},
    'allValid': {'type': 'MigratedValuesValid', 'status': 'True', 'reason': 'AllValid', 'message': 'all 2 instance(s) on version v2-0-0 satisfy its schema'},
    'listFailed': {'type': 'MigratedValuesValid', 'status': 'Unknown', 'reason': 'ValidationIncomplete', 'message': 'instances could not be listed'},
}
CAPPED = {'version': 'v2-0-0', 'refusedCount': 14, 'uncheckedCount': 0,
          'refused': [{'namespace': 'demo', 'name': f'web-{i}', 'fields': ['spec.a'], 'fieldCount': 1} for i in range(10)]}


def render(chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', chart, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        out = subprocess.run(['helm', 'template', 't', copy], capture_output=True, text=True, check=True).stdout
    return {(d['kind'], d['metadata']['name']): d for d in yaml.safe_load_all(out) if isinstance(d, dict)}


def jq(prog, data):
    proc = subprocess.run([JQ, '-c', prog], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip()[:400])
    return json.loads(proc.stdout)


def expr(doc, for_path):
    e = next(t['expression'] for t in doc['spec']['widgetDataTemplate'] if t['forPath'] == for_path).strip()
    assert e.startswith('${') and e.endswith('}'), e[:40]
    return e[2:-1]


def compdef(mv, cond):
    status = {'apiVersion': 'composition.krateo.io/v2-0-0', 'kind': 'Shop', 'resource': 'shops',
              'conditions': [{'type': 'Ready', 'status': 'True', 'reason': 'Available'}] + ([cond] if cond else [])}
    if mv is not None:
        status['migratedValues'] = mv
    return {'apiVersion': 'core.krateo.io/v1alpha1', 'kind': 'CompositionDefinition',
            'metadata': {'name': 'shop', 'namespace': 'krateo-system', 'creationTimestamp': '2026-11-23T00:00:00Z'},
            'spec': {'chart': {'repo': 'shop', 'version': '2.0.0'}}, 'status': status}


def detail(prog, mv, cond):
    return jq(prog, {'name': 'shop', 'namespace': 'krateo-system', 'compdef': compdef(mv, cond), 'catalog': {'entries': {}}, 'comps': {'items': []}})


def editdef(prog, mv, ns, name, version='v2-0-0'):
    comp = {'apiVersion': f'composition.krateo.io/{version}', 'kind': 'Shop', 'metadata': {'name': name, 'namespace': ns}, 'spec': {}}
    return jq(prog, {'name': name, 'namespace': ns, 'found': [comp],
                     'crds': [{'group': 'composition.krateo.io', 'kind': 'Shop', 'plural': 'shops', 'version': version, 'cdNamespace': 'krateo-system', 'migratedValues': mv}],
                     'jsonschema': None})


def main():
    bp = render(BLUEPRINTS)
    cp = render(COMPOSITIONS)
    detail_prog = bp[('RESTAction', 'blueprint-detail')]['spec']['filter']
    info_items = expr(bp[('Descriptions', 'descriptions-blueprint-detail-info')], 'items')
    col_items = expr(bp[('Flex', 'blueprint-detail-col-left')], 'items')
    alert = bp[('Alert', 'alert-blueprint-detail-migrated')]
    edit_prog = cp[('RESTAction', 'composition-editdef')]['spec']['filter']
    edit_notices = expr(cp[('Form', 'composition-edit')], 'notices')
    bad = []

    def check(label, got, want):
        if got != want:
            bad.append(label)
            print(f'FAIL {label}:\n  got  {got!r}\n  want {want!r}')

    def details(mv, cond):
        out = detail(detail_prog, mv, cond)
        rows = jq(info_items, out)
        row = [r['value'] for r in rows if r['label'] == 'Values check']
        column = [i['resourceRefId'] for i in jq(col_items, out)]
        drawn = {t['forPath']: jq(t['expression'].strip()[2:-1], out) for t in alert['spec']['widgetDataTemplate']}
        return row, column, drawn

    def edit(mv, ns, name, version='v2-0-0'):
        return jq(edit_notices, editdef(edit_prog, mv, ns, name, version))

    row, column, drawn = details(REFUSED, COND['refused'])
    check('details, refused: the row names it and its field', row, ['1 composition holds values version v2-0-0 refuses: default/held (spec.features.enabled)'])
    check('details, refused: the Alert leads the column', column[0], 'alert-blueprint-detail-migrated')
    check('details, refused: a warning', (drawn['type'], drawn['title']), ('warning', '1 composition can’t be edited until its values are fixed'))
    check('details, refused: says the composition keeps running', drawn['description'].endswith('The composition keeps running.'), True)

    row, column, drawn = details(UNCHECKED, COND['unchecked'])
    check('details, unchecked: the row', row, ['1 composition couldn’t be checked against v2-0-0: default/blocked'])
    check('details, unchecked: info, not warning', (drawn['type'], drawn['title']), ('info', '1 composition couldn’t be checked'))

    row, column, _ = details(None, COND['allValid'])
    check('details, all valid: no row', row, [])
    check('details, all valid: no Alert in the column', 'alert-blueprint-detail-migrated' in column, False)
    row, column, _ = details(None, None)
    check('details, no migration yet: no row, no Alert', (row, 'alert-blueprint-detail-migrated' in column), ([], False))
    row, column, _ = details(None, COND['listFailed'])
    check('details, list failed (absent + Unknown): "Not checked yet", never an all-clear', row, ['Not checked yet'])
    check('details, list failed: no Alert', 'alert-blueprint-detail-migrated' in column, False)

    held = edit(REFUSED, 'default', 'held')
    check('edit, refused composition: a warning naming its field', held, [{'type': 'warning', 'title': 'Version v2-0-0 refuses this composition’s current value for 1 field',
                                                                         'description': ['Saving a change to spec.features.enabled is refused until its value satisfies v2-0-0. Fix it here, then save.']}])
    check('edit, another composition of the same blueprint: nothing', edit(REFUSED, 'default', 'fine'), [])
    check('edit, unchecked composition: info', [n.get('type', 'info') for n in edit(UNCHECKED, 'default', 'blocked')], ['info'])
    check('edit, all valid: nothing', edit(None, 'default', 'held'), [])
    check('edit, past the cap on the checked version: may be among them',
          [n['title'] for n in edit(CAPPED, 'demo', 'web-12')], ['Version v2-0-0 refuses some compositions’ values'])
    check('edit, past the cap on another version: nothing', edit(CAPPED, 'demo', 'web-12', 'v1-0-0'), [])

    control = edit_prog.replace('| if $mine != null then', '| if false then', 1)
    if control == edit_prog or jq(edit_notices, editdef(control, REFUSED, 'default', 'held')) == held:
        bad.append('control')
        print('FAIL control: dropping the refused branch did not change the refused composition\'s notices')

    print(f'migrated values: details + edit over core-provider\'s sample, {len(bad)} failure(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
