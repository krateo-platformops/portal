#!/usr/bin/env python3
"""The composition detail header's actions (approved redesign, step 2), over the rendered chart.

  - the blueprint is a LINK, labelled with its name and the installed version, not one more button;
  - exactly ONE primary action, "Edit values";
  - Pause and Delete live under the "More actions" Dropdown, never in the row beside Edit;
  - the row's order: blueprint link, Ask Agentiko, Sync, Edit values, the menu.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.path.join(os.path.dirname(HERE), 'helm', 'compositions-page')
JQ = os.environ.get('JQ', 'jq')


def render():
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', CHART, copy], check=True)
        chart = os.path.join(copy, 'Chart.yaml')
        with open(chart, encoding='utf-8') as handle:
            text = handle.read().replace('CHART_VERSION', '0.0.0')
        with open(chart, 'w', encoding='utf-8') as handle:
            handle.write(text)
        out = subprocess.run(['helm', 'template', 't', copy], capture_output=True, text=True, check=True).stdout
    return {(d['kind'], d['metadata']['name']): d for d in yaml.safe_load_all(out) if d}


def main():
    docs = render()
    bad = 0

    def check(what, got, want):
        nonlocal bad
        if got != want:
            bad += 1
            print(f'FAIL {what}: got {got!r}, want {want!r}')

    row = docs[('Flex', 'composition-detail-actions')]['spec']['widgetData']['items']
    check('row order', [i['resourceRefId'] for i in row],
          ['composition-detail-blueprint', 'composition-detail-diagnose', 'composition-detail-sync', 'composition-detail-edit', 'composition-detail-more'])
    menu = docs[('Dropdown', 'composition-detail-more')]['spec']['widgetData']
    check('menu items', [i['resourceRefId'] for i in menu['items']], ['composition-detail-pause', 'composition-detail-delete'])
    check('menu name', menu['ariaLabel'], 'More actions')

    buttons = {name: docs[('Button', name)]['spec']['widgetData'] for name in
               ['composition-detail-blueprint', 'composition-detail-diagnose', 'composition-detail-sync', 'composition-detail-edit']}
    check('one primary, and it is Edit values', [(n, b['label']) for n, b in buttons.items() if b.get('type') == 'primary'],
          [('composition-detail-edit', 'Edit values')])
    check('the blueprint is a link', buttons['composition-detail-blueprint']['type'], 'link')

    tmpl = docs[('Button', 'composition-detail-blueprint')]['spec']['widgetDataTemplate']
    expr = next(t['expression'] for t in tmpl if t['forPath'] == 'label').strip().removeprefix('${').removesuffix('}').strip()
    for data, want in [
        ({'blueprintName': 'example-webapp', 'detail': {'metadata': {}, 'status': {'helmChartVersion': '1.4.2-rc.1'}}}, 'Blueprint example-webapp 1.4.2-rc.1'),
        # A component of the installer: the label is its parent's version (krateo-057, frontend).
        ({'blueprintName': 'frontend', 'detail': {'metadata': {'labels': {'krateo.io/composition-installed-version': 'v0-3-706'}},
                                                  'status': {'helmChartVersion': '1.8.83'}}}, 'Blueprint frontend 1.8.83'),
        ({'blueprintName': 'x', 'detail': {'metadata': {'labels': {'krateo.io/composition-installed-version': 'v0-3-706'}}, 'status': {}}}, 'Blueprint x'),
        ({'blueprintName': '', 'detail': {'metadata': {}}}, 'Blueprint'),
    ]:
        got = json.loads(subprocess.run([JQ, '-c', expr], input=json.dumps(data), capture_output=True, text=True, check=True).stdout)
        check(f'blueprint label {data["blueprintName"] or "(none)"}', got, want)

    tmpl = docs[('Descriptions', 'composition-detail-metadata')]['spec']['widgetDataTemplate']
    expr = next(t['expression'] for t in tmpl if t['forPath'] == 'items').strip().removeprefix('${').removesuffix('}').strip()
    data = {'detail': {'metadata': {'uid': 'u', 'labels': {'krateo.io/composition-installed-version': 'v0-3-706'}}, 'status': {'helmChartVersion': '1.8.83'}}}
    got = json.loads(subprocess.run([JQ, '-c', expr], input=json.dumps(data), capture_output=True, text=True, check=True).stdout)
    check('the metadata tab says the same version', [i['value'] for i in got if i['label'] == 'Installed version'], ['1.8.83'])

    print(f'composition header actions: {bad} failure(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
