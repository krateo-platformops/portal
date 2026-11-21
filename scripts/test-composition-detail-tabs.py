#!/usr/bin/env python3
"""The composition detail page's structure (approved redesign, step 3: tabs), over the rendered chart.

Walks the page from its root and asserts two things a later edit could silently break:
  - the order: header, Status card, (Access when shown), then the four tabs, in their approved order;
  - NO CARD IS LOST: every card the page had before the tabs is still reachable from the root.
"""
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.path.join(os.path.dirname(HERE), 'helm', 'compositions-page')
PLURAL_KIND = {'cards': 'Card', 'flexes': 'Flex', 'rows': 'Row', 'tabs': 'Tabs', 'pageheaders': 'PageHeader', 'listies': 'Listy'}

CARDS = {
    'card-composition-detail-status', 'card-composition-detail-architecture', 'card-composition-detail-relations',
    'card-composition-detail-spec', 'card-composition-detail-manifest', 'card-composition-detail-events',
    'card-composition-detail-reconcile', 'card-composition-detail-health', 'card-composition-detail-metadata',
}
TABS = ['Resources', 'Values', 'Activity', 'Conditions & metadata']


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

    root = docs[('Flex', 'page-composition-detail')]
    check('page order', [i['resourceRefId'] for i in root['spec']['widgetData']['items']],
          ['composition-detail-page-header', 'composition-detail-status-row', 'composition-detail-tabs'])
    tabs = docs[('Tabs', 'composition-detail-tabs')]['spec']['widgetData']['items']
    check('tab labels', [t['label'] for t in tabs], TABS)

    reached, seen = set(), set()
    stack = [('Flex', 'page-composition-detail')]
    while stack:
        key = stack.pop()
        if key in seen or key not in docs:
            continue
        seen.add(key)
        if key[0] == 'Card':
            reached.add(key[1])
        for ref in (docs[key]['spec'].get('resourcesRefs') or {}).get('items') or []:
            kind = PLURAL_KIND.get(ref.get('resource', ''))
            if kind:
                stack.append((kind, ref['name']))
    check('every card still reachable from the page', sorted(CARDS - reached), [])

    print(f'composition detail tabs: {bad} failure(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
