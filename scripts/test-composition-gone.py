#!/usr/bin/env python3
"""The composition detail page when the composition is gone or unreadable (portal#577), over the chart's own jq.

composition-detail lists CompositionDefinitions and then each composition kind, both with
continueOnError, so a deleted composition and one the caller may not read used to arrive as the same
empty object and the page drew every widget on its default ("composition", "Unknown", "created ?",
live Edit values and Refresh). The RESTAction now keeps each read's errors (errorKey, a LIST per stage)
and says which case it is:

  gone        a name and namespace were asked for, nothing matched, both reads succeeded;
  unreadable  the same, but a read failed (any status, a 403 among them): existence is not known;
  neither     a match (whatever other reads failed), or no name/namespace asked for.

The widgets only show that. This runs the RESTAction's real final filter over the three cases and then
the header, the page root, the paragraph and the back link over each, with the present composition
unchanged.

CHART_DIR overrides the chart (the failing control runs the same cases over origin/main's).
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHART = os.environ.get('CHART_DIR', os.path.join(REPO, 'helm', 'compositions-page'))
JQ = os.environ.get('JQ', 'jq')


def render():
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', CHART, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read().replace('CHART_VERSION', '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy, '--set', 'namespace=krateo-composition'],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def jq(program, data):
    if program is None:          # the chart has no such template: a failed check, not a crash
        return '<no template>'
    with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run([JQ, '-c', '-f', path], input=json.dumps(data), capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise AssertionError(f'jq failed: {proc.stderr.strip()}')
        return json.loads(proc.stdout)
    finally:
        os.unlink(path)


MISSING = []


def named(docs, kind, name):
    for d in docs:
        if d.get('kind') == kind and d.get('metadata', {}).get('name') == name:
            return d
    MISSING.append(f'the chart renders no {kind} {name}')
    return {'spec': {'widgetData': {'items': []}, 'resourcesRefs': {'items': []}}}


def unwrap(expr):
    return expr.strip().removeprefix('${').removesuffix('}').strip()


def template(doc, path):
    exprs = [t['expression'] for t in doc['spec'].get('widgetDataTemplate', []) if t['forPath'] == path]
    return unwrap(exprs[-1]) if exprs else None


FORBIDDEN = {'message': 'namespaces is forbidden: User "tenant" cannot list resource "namespaces"', 'code': 403}


def main():
    docs = render()
    ra = named(docs, 'RESTAction', 'composition-detail')['spec']
    program = ra['filter']
    steps = {s['name']: s for s in ra['api']}
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f'{label}: got {got!r}, want {want!r}')

    live = {'kind': 'K', 'apiVersion': 'composition.krateo.io/v1', 'metadata': {'name': 'demo-1125', 'namespace': 'tenant-bob',
            'creationTimestamp': '2026-11-01T10:00:00Z'}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True', 'reason': 'Available'}]}}
    crds = [{'plural': 'ks', 'version': 'v1', 'kind': 'K', 'group': 'composition.krateo.io', 'cdName': 'bp', 'cdNamespace': 'krateo-system'}]

    def resolve(found=(), errors=None, name='demo-1125', namespace='tenant-bob'):
        data = {'crds': crds, 'found': list(found), **(errors or {})}
        if name is not None:
            data['name'] = name
        if namespace is not None:
            data['namespace'] = namespace
        return jq(program, data)

    cases = {
        'deleted': resolve(),
        'unreadable: the found read was refused': resolve(errors={'foundError': [FORBIDDEN]}),
        'unreadable: the definitions read failed': resolve(errors={'crdsError': ['boom']}),
        'present': resolve([live]),
        'present although another kind failed': resolve([live], errors={'foundError': [FORBIDDEN]}),
        'no name or namespace asked for (the Phase-1 walk)': resolve(name=None, namespace=None),
        'a name but no namespace': resolve(namespace=None),
        'empty error lists are not errors': resolve(errors={'crdsError': [], 'foundError': []}),
    }
    want = {'deleted': (True, False), 'unreadable: the found read was refused': (False, True),
            'unreadable: the definitions read failed': (False, True), 'present': (False, False),
            'present although another kind failed': (False, False),
            'no name or namespace asked for (the Phase-1 walk)': (False, False), 'a name but no namespace': (False, False),
            'empty error lists are not errors': (True, False)}
    for label, out in cases.items():
        check(f'RA flags, {label}', (out.get('gone'), out.get('unreadable')), want[label])

    # The errors are kept, as lists, on the two reads.
    check('crds keeps its errors', steps['crds'].get('errorKey'), 'crdsError')
    check('found keeps its errors', steps['found'].get('errorKey'), 'foundError')

    deleted, refused, present = cases['deleted'], cases['unreadable: the found read was refused'], cases['present']

    # The header.
    header = named(docs, 'PageHeader', 'composition-detail-page-header')
    for label, out, tag in (('deleted', deleted, 'Not found'), ('refused', refused, 'Not readable')):
        check(f'{label}: title is the name from the link', jq(template(header, 'title'), out), 'demo-1125')
        check(f'{label}: subtitle is the namespace, no "created ?"', jq(template(header, 'subtitle'), out), 'Composition · tenant-bob')
        check(f'{label}: one grey tag', jq(template(header, 'tags'), out), [{'label': tag, 'color': 'gray'}])
        check(f'{label}: no actions', jq(template(header, 'items'), out), [])
    check('present: title', jq(template(header, 'title'), present), 'demo-1125')
    check('present: subtitle', jq(template(header, 'subtitle'), present).startswith('K · tenant-bob · created '), True)
    check('present: tags', jq(template(header, 'tags'), present), [{'label': 'Ready', 'color': 'green'}])
    check('present: the actions stay', jq(template(header, 'items'), present), [{'resourceRefId': 'composition-detail-actions'}])
    walk = cases['no name or namespace asked for (the Phase-1 walk)']
    check('no extras: the title stays the old word', jq(template(header, 'title'), walk), 'composition')
    check('no extras: the tag stays Unknown', jq(template(header, 'tags'), walk), [{'label': 'Unknown', 'color': 'gray'}])
    check('no extras: the actions stay', jq(template(header, 'items'), walk), [{'resourceRefId': 'composition-detail-actions'}])

    # The page root.
    page = named(docs, 'Flex', 'page-composition-detail')
    items = lambda out: [i['resourceRefId'] for i in jq(template(page, 'items'), out)]
    declared = {r['id'] for r in page['spec']['resourcesRefs']['items']}
    for label, out in (('deleted', deleted), ('refused', refused)):
        check(f'{label}: the page is the header and the one-line card', items(out), ['composition-detail-page-header', 'composition-detail-gone'])
    check('present: the page is as before', items(present),
          ['composition-detail-page-header', 'composition-detail-status-row', 'composition-detail-tabs'])
    check('every item the page can mount is declared', all(i in declared for o in (deleted, present) for i in items(o)), True)

    # The sentence and the way back.
    sentence = template(named(docs, 'Paragraph', 'composition-detail-gone'), 'text')
    check('deleted sentence', jq(sentence, deleted), 'This composition no longer exists.')
    check('refused sentence', jq(sentence, refused), 'You can’t read this composition with your access.')
    back = named(docs, 'Button', 'composition-detail-gone-back')
    def backpath(out):
        got = jq(template(back, 'actions'), out)
        return got['navigate'][0]['path'] if isinstance(got, dict) else got

    check('the back label', back['spec']['widgetData'].get('label'), 'Back to Compositions')
    check('back filters to the namespace', backpath(deleted), '/compositions?projects=tenant-bob')
    check('back with no namespace is the plain list', backpath(walk), '/compositions')
    card = named(docs, 'Card', 'card-composition-detail-gone')
    check('the card holds the sentence and the link only',
          [i['resourceRefId'] for i in card['spec']['widgetData']['items']],
          ['paragraph-composition-detail-gone', 'button-composition-detail-gone-back'])

    failures.extend(MISSING)
    for f in failures:
        print('FAIL', f)
    print(f'composition gone/unreadable: {len(failures)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
