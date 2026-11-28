#!/usr/bin/env python3
"""A blueprint's form defaults against its chart's values (core-provider 2.13.137, portal#576).

core-provider writes the CompositionDefinition's status.schemaDefaults
    { differ: [{path, schema?, values?, kind}] (sorted, at most 10), dropped }
only when the form's schema defaults and the chart's values differ. The blueprint page shows ONE neutral
notice under the header: title "Form defaults differ from the chart's values", core-provider's action
sentence, and the fields (an open list of 3 or fewer, otherwise folded under "Show the N fields").
Approved mockup: https://claude.ai/artifact/WmME7NoHKtz4LE78EVPk35.

This runs, over the chart as rendered, blueprint-detail's filter, the page's mounted items, the Alert and
the Collapse, with the widgetDataTemplate expressions as they ship:
  absent            no schemaDefaults key: nothing mounted, nothing drawn
  one row per kind  the three row wordings
  JSON values       false, 3, {"a":1}, "text" shown as JSON (strings quoted), never [object Object]
  3 rows            open, no "Show"; 4 and more folded, the label counts differ + dropped
  dropped           "+N more" as the last row, only when above 0
  action            the sentence is the generated table's (core-provider's JSON via the portal chart),
                    not a string written in the blueprints chart
Controls: the Alert's description is also checked against the table with its entry removed (the page then
says nothing instead of a stale sentence), and a chart that does not carry the notice fails every case.
BLUEPRINTS_CHART=<dir> runs it against another checkout of the chart (the failing control).
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CHART = os.environ.get('BLUEPRINTS_CHART') or os.path.join(ROOT, 'helm', 'blueprints-page')
PORTAL_ACTIONS = os.path.join(ROOT, 'helm', 'portal', 'templates', '_reason-actions.tpl')
JQ = os.environ.get('JQ', 'jq')

DIFFER = {'path': 'replicaCount', 'schema': 1, 'values': 2, 'kind': 'valueDiffers'}
NOVALUE = {'path': 'ingress.enabled', 'schema': False, 'kind': 'schemaDefaultWithoutValuesKey'}
INVALID = {'path': 'image.pullPolicy', 'values': 'Sometimes', 'kind': 'valuesValueInvalid'}


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


def compdef(schema_defaults):
    status = {'apiVersion': 'composition.krateo.io/v2-0-0', 'kind': 'Shop', 'resource': 'shops',
              'conditions': [{'type': 'Ready', 'status': 'True', 'reason': 'Available'}]}
    if schema_defaults is not None:
        status['schemaDefaults'] = schema_defaults
    return {'apiVersion': 'core.krateo.io/v1alpha1', 'kind': 'CompositionDefinition',
            'metadata': {'name': 'shop', 'namespace': 'krateo-system', 'creationTimestamp': '2026-11-23T00:00:00Z'},
            'spec': {'chart': {'repo': 'shop', 'version': '2.0.0'}}, 'status': status}


def generated_action():
    """The SchemaDefaultsDiffer sentence as the portal chart's generated table holds it."""
    with open(PORTAL_ACTIONS, encoding='utf-8') as fh:
        text = fh.read()
    body = text.split('{{- define "portal.reasonActionsDefs" -}}', 1)[1].rsplit('{{- end -}}', 1)[0]
    prog = body + ' {type: "SchemaDefaultsMatchValues", status: "False", reason: "SchemaDefaultsDiffer"} | cdReasonAction | .action'
    return jq(prog, None)


def main():
    docs = render(CHART)
    ra = docs[('RESTAction', 'blueprint-detail')]['spec']['filter']
    page_items = expr(docs[('Flex', 'page-blueprint-detail')], 'items')
    alert = docs[('Alert', 'alert-blueprint-detail-defaults')]
    collapse = docs[('Collapse', 'collapse-blueprint-detail-defaults')]
    flex = docs[('Flex', 'blueprint-detail-defaults')]
    want_action = generated_action()
    bad = []

    def check(label, got, want):
        if got != want:
            bad.append(label)
            print(f'FAIL {label}:\n  got  {got!r}\n  want {want!r}')

    def page(sd, prog=ra):
        out = jq(prog, {'name': 'shop', 'namespace': 'krateo-system', 'compdef': compdef(sd),
                        'catalog': {'entries': {}}, 'comps': {'items': []}})
        mounted = [i['resourceRefId'] for i in jq(page_items, out)]
        alert_wd = {t['forPath']: jq(expr(alert, t['forPath']), out) for t in alert['spec']['widgetDataTemplate']}
        coll_wd = {t['forPath']: jq(expr(collapse, t['forPath']), out) for t in collapse['spec']['widgetDataTemplate']}
        return out, mounted, alert_wd, coll_wd

    def rows(coll_wd):
        return [r['line'] for it in coll_wd['items'] for r in it['dataSource']]

    check('the generated action is a sentence about the file to change', want_action.startswith('Align values.schema.json'), True)

    # absent: nothing
    out, mounted, _, coll = page(None)
    check('absent: the key is null', out.get('schemaDefaults'), None)
    check('absent: the notice is not mounted', 'blueprint-detail-defaults' in mounted, False)
    check('absent: the page keeps its header and grid', mounted, ['blueprint-detail-page-header', 'blueprint-detail-grid'])
    check('absent: the Collapse holds nothing', coll['items'], [])
    out, mounted, _, _ = page({'differ': [], 'dropped': 0})
    check('present but empty (nothing differs, nothing dropped): nothing', (out.get('schemaDefaults'), 'blueprint-detail-defaults' in mounted), (None, False))

    # one row of each kind
    for label, row, line in (
            ('valueDiffers', DIFFER, 'replicaCount: form 1 · chart 2'),
            ('schemaDefaultWithoutValuesKey', NOVALUE, 'ingress.enabled: form default false · no chart value'),
            ('valuesValueInvalid', INVALID, 'image.pullPolicy: chart value "Sometimes" isn\'t valid for the form')):
        out, mounted, al, coll = page({'differ': [row], 'dropped': 0})
        check(f'{label}: mounted between the header and the grid', mounted, ['blueprint-detail-page-header', 'blueprint-detail-defaults', 'blueprint-detail-grid'])
        check(f'{label}: the row', rows(coll), [line])
        check(f'{label}: neutral title and the generated action', (al['title'], al['description']), ("Form defaults differ from the chart's values", want_action))
        check(f'{label}: open', (coll['defaultActiveKey'], [i['label'] for i in coll['items']]), (['fields'], ['1 field']))
    check('the Alert is the neutral info notice with its icon', (alert['spec']['widgetData']['type'], alert['spec']['widgetData']['showIcon']), ('info', True))
    check('the Flex holds only the Alert and the Collapse', [i['resourceRefId'] for i in flex['spec']['widgetData']['items']],
          ['alert-blueprint-detail-defaults', 'collapse-blueprint-detail-defaults'])

    # JSON values, never [object Object]
    for value, shown in ((False, 'false'), (3, '3'), ({'a': 1}, '{"a":1}'), ('text', '"text"'), (None, 'null'), ([1, 'x'], '[1,"x"]')):
        _, _, _, coll = page({'differ': [{'path': 'p', 'schema': value, 'values': value, 'kind': 'valueDiffers'}], 'dropped': 0})
        check(f'JSON value {value!r}', rows(coll), [f'p: form {shown} · chart {shown}'])

    # open at 3, folded above
    three = [{'path': f'a.{i}', 'schema': i, 'values': i + 1, 'kind': 'valueDiffers'} for i in range(3)]
    _, _, al, coll = page({'differ': three, 'dropped': 0})
    check('3 rows: open, no "Show"', (coll['defaultActiveKey'], coll['items'][0]['label'], len(rows(coll))), (['fields'], '3 fields', 3))
    four = three + [{'path': 'a.3', 'schema': 3, 'values': 4, 'kind': 'valueDiffers'}]
    _, _, _, coll = page({'differ': four, 'dropped': 0})
    check('4 rows: folded, counts them', (coll['defaultActiveKey'], coll['items'][0]['label'], len(rows(coll))), ([], 'Show the 4 fields', 4))
    two_dropped = {'differ': three[:2], 'dropped': 2}
    _, _, _, coll = page(two_dropped)
    check('2 listed + 2 dropped = 4: folded on the total, "+2 more" last', (coll['defaultActiveKey'], coll['items'][0]['label'], rows(coll)[-1], len(rows(coll))),
          ([], 'Show the 4 fields', '+2 more', 3))
    ten = [{'path': f'p.{i:02d}', 'schema': i, 'values': i + 1, 'kind': 'valueDiffers'} for i in range(10)]
    _, _, al, coll = page({'differ': ten, 'dropped': 4})
    check('10 + 4 dropped: "Show the 14 fields", ten rows then "+4 more"', (coll['items'][0]['label'], len(rows(coll)), rows(coll)[-1]), ('Show the 14 fields', 11, '+4 more'))
    _, _, _, coll = page({'differ': ten, 'dropped': 0})
    check('dropped 0: no "+N more"', ('more' in rows(coll)[-1], len(rows(coll))), (False, 10))
    _, _, _, coll = page({'differ': [DIFFER], 'dropped': 1})
    check('1 listed + 1 dropped: 2 fields, open, "+1 more"', (coll['defaultActiveKey'], coll['items'][0]['label'], rows(coll)), (['fields'], '2 fields', ['replicaCount: form 1 · chart 2', '+1 more']))

    # the action comes from the table: with the entry gone, the chart has no sentence of its own
    entry = ra.replace('"SchemaDefaultsDiffer": {"action": ', '"SchemaDefaultsDifferGone": {"action": ', 1)
    if entry == ra:
        bad.append('control')
        print('FAIL control: the SchemaDefaultsDiffer entry is not in blueprint-detail\'s filter')
    else:
        _, _, al, _ = page({'differ': [DIFFER], 'dropped': 0}, entry)
        check('control: without the table entry the sentence is empty, not a stale one', al['description'], '')

    print(f'blueprint defaults notice: {len(bad)} failure(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
