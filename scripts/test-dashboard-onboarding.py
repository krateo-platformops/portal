#!/usr/bin/env python3
"""The Dashboard's Welcome card (onboarding-empty) is gated by `dashboard-compositions-count`, which
must answer exactly what the card used to read from `compositions-list`: `(.list | length) == 0`.

WHY. The card needs one bit (does the caller see any composition?) and used to read the whole
compositions-list RESTAction, whose final filter builds facets, rail state, counts and a prompt
over every composition. The count RA runs the same stages (CompositionDefinitions the caller may
list, one cluster LIST per kind under the same userAccessFilter) and counts, so the card is shown
for exactly the callers it was shown for.

WHAT IT GUARDS, each case run through BOTH RAs' own stage filters and final filters, over the same
simulated upstream (snowplow's userAccessFilter refilter is simulated: a LIST keeps the items in
namespaces the caller may list):
  - nothing visible; one visible in one kind; visible only in a namespace the caller may list (the
    same kind has an item in a namespace they may not, which must not count); visible only in a
    namespace they may NOT list (not counted); a kind whose read failed (counts nothing, the other
    kinds still count); the CompositionDefinition read failing (nothing); the shell's scope
    (?projects, ?project, ?namespace) narrowing the count as it narrows compositions-list's `.list`.
  - the new answer `count == 0` equals `(compositions-list .list | length) == 0` in every case.
  - the card reads the new RA, no longer references compositions-list, keeps hideWhenEmpty, and its
    expression emits the same four tiles at zero and nothing otherwise (or when `.count` is absent,
    as before when `.list` was).

Usage: test-dashboard-onboarding.py
"""
import json
import os
import subprocess
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
JQ = os.environ.get('JQ', 'jq')
NS = 'krateo-system'

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f'{label}: {got!r} != {want!r}')


def render(chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy_dir = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', os.path.join(REPO, 'helm', chart), copy_dir], check=True)
        meta = os.path.join(copy_dir, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy_dir, '--namespace', NS],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template {chart} failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def named(docs, kind, name):
    for d in docs:
        if d.get('kind') == kind and d.get('metadata', {}).get('name') == name:
            return d
    raise AssertionError(f'{kind}/{name} not in the rendered chart')


def jq(program, data):
    with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run([JQ, '-c', '-f', path], input=json.dumps(data),
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise AssertionError(f'jq failed: {proc.stderr.strip()[:300]}')
        return json.loads(proc.stdout)
    finally:
        os.unlink(path)


def query(text):
    start = text.find('${')
    if start < 0:
        return None
    depth, i = 1, start + 2
    while i < len(text):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[start + 2:i].strip()
        i += 1
    return None


PORTAL = render('portal')
DASH = render('dashboard-page')
OLD = named(PORTAL, 'RESTAction', 'compositions-list')['spec']
NEW = named(DASH, 'RESTAction', 'dashboard-compositions-count')['spec']
CARD = named(DASH, 'Listy', 'onboarding-empty')


def stage(ra, name):
    return next(a for a in ra['api'] if a['name'] == name)


def cd(plural, version='v1-0-0', ns=NS):
    return {'metadata': {'namespace': ns}, 'status': {'resource': plural, 'apiVersion': f'composition.krateo.io/{version}'}}


def comp(name, ns, kind='Widget'):
    return {'apiVersion': 'composition.krateo.io/v1-0-0', 'kind': kind,
            'metadata': {'name': name, 'namespace': ns, 'uid': 'u-' + name, 'creationTimestamp': '2026-11-01T00:00:00Z'},
            'status': {'conditions': [{'type': 'Ready', 'status': 'True', 'reason': 'Available'}]}}


def resolve(ra, defs, kinds, listable, extras=None, failed=(), defs_failed=False):
    """What snowplow hands the RA's final filter: each stage's own filter over its (refiltered) read.
    `kinds` maps a plural to the items its cluster LIST returns; `listable` is the caller's namespaces."""
    d = dict(extras or {})
    if defs_failed:
        d['crdsError'] = [{'code': 403}]
    else:
        d['crds'] = jq(stage(ra, 'crds')['filter'], {'crds': {'items': defs}})
    flt = stage(ra, 'allCompositions')['filter']
    rows = []
    for c in d.get('crds', []):
        if c['plural'] in failed:
            d['allCompositionsError'] = [{'code': 500}]
            continue
        items = [i for i in kinds.get(c['plural'], []) if i['metadata']['namespace'] in listable]
        rows += jq(flt, {'allCompositions': {'items': items}})
    d['allCompositions'] = rows
    return jq(ra['filter'], d)


CASES = {
    'nothing visible': dict(defs=[cd('widgets')], kinds={'widgets': []}, listable={'a'}),
    'no kinds at all': dict(defs=[], kinds={}, listable={'a'}),
    'one visible, one kind': dict(defs=[cd('widgets')], kinds={'widgets': [comp('w1', 'a')]}, listable={'a'}),
    'visible in a second kind only': dict(defs=[cd('widgets'), cd('gadgets')],
                                          kinds={'widgets': [], 'gadgets': [comp('g1', 'a', 'Gadget')]}, listable={'a'}),
    'only in the namespace the caller may list': dict(defs=[cd('widgets')],
                                                      kinds={'widgets': [comp('w1', 'other'), comp('w2', 'mine')]}, listable={'mine'}),
    'only in a namespace the caller may NOT list': dict(defs=[cd('widgets')],
                                                        kinds={'widgets': [comp('w1', 'other')]}, listable={'mine'}),
    'a kind read failed, none else visible': dict(defs=[cd('widgets')], kinds={'widgets': [comp('w1', 'a')]},
                                                  listable={'a'}, failed=('widgets',)),
    'a kind read failed, another visible': dict(defs=[cd('widgets'), cd('gadgets')],
                                                kinds={'widgets': [comp('w1', 'a')], 'gadgets': [comp('g1', 'a', 'Gadget')]},
                                                listable={'a'}, failed=('widgets',)),
    'definitions read failed': dict(defs=[], kinds={'widgets': [comp('w1', 'a')]}, listable={'a'}, defs_failed=True),
    'scope ?projects names a namespace with some': dict(defs=[cd('widgets')], kinds={'widgets': [comp('w1', 'a'), comp('w2', 'b')]},
                                                         listable={'a', 'b'}, extras={'projects': 'b'}),
    'scope ?projects names a namespace with none': dict(defs=[cd('widgets')], kinds={'widgets': [comp('w1', 'a')]},
                                                         listable={'a', 'b'}, extras={'projects': 'b,c'}),
    'scope ?project': dict(defs=[cd('widgets')], kinds={'widgets': [comp('w1', 'a')]}, listable={'a', 'b'}, extras={'project': 'b'}),
    'scope ?namespace': dict(defs=[cd('widgets')], kinds={'widgets': [comp('w1', 'a')]}, listable={'a', 'b'}, extras={'namespace': 'a'}),
    'scope ?projects empty string': dict(defs=[cd('widgets')], kinds={'widgets': [comp('w1', 'a')]}, listable={'a'}, extras={'projects': ''}),
}
EXPECT_EMPTY = {'nothing visible': True, 'no kinds at all': True, 'one visible, one kind': False,
                'visible in a second kind only': False, 'only in the namespace the caller may list': False,
                'only in a namespace the caller may NOT list': True, 'a kind read failed, none else visible': True,
                'a kind read failed, another visible': False, 'definitions read failed': True,
                'scope ?projects names a namespace with some': False, 'scope ?projects names a namespace with none': True,
                'scope ?project': True, 'scope ?namespace': False, 'scope ?projects empty string': False}

card_expr = query(next(e for e in CARD['spec']['widgetDataTemplate'] if e['forPath'] == 'dataSource')['expression'])

for label, kw in CASES.items():
    old = resolve(OLD, **kw)
    new = resolve(NEW, **kw)
    old_empty = len(old['list']) == 0
    check(f'{label}: new count == 0 equals compositions-list .list empty', new['count'] == 0, old_empty)
    check(f'{label}: the visibility answer', old_empty, EXPECT_EMPTY[label])
    check(f'{label}: the card shows for the same callers', len(jq(card_expr, new)) > 0, old_empty)

# --- the card -------------------------------------------------------------------------------------
check('the card reads dashboard-compositions-count in the page namespace', CARD['spec']['apiRef'],
      {'name': 'dashboard-compositions-count', 'namespace': named(DASH, 'RESTAction', 'dashboard-compositions-count')['metadata']['namespace']})
check('the card no longer references compositions-list', 'compositions-list' in json.dumps(CARD['spec']), False)
check('the card still hides when empty', CARD['spec']['widgetData']['hideWhenEmpty'], True)
check('the card header is unchanged', CARD['spec']['widgetData']['header'], 'Welcome to Krateo — what do you want to build?')
tiles = jq(card_expr, {'count': 0})
check('zero: the four tiles', [t['name'] for t in tiles], ['Help me choose', 'Databases', 'Networking', 'Observability'])
for bad in ({'count': 1}, {'count': 7}, {}, {'count': None}, {'count': '0'}, {'list': []}):
    check(f'{bad}: nothing', jq(card_expr, bad), [])
check('no widget in the dashboard chart reads compositions-list',
      [d['metadata']['name'] for d in DASH if d.get('spec', {}).get('apiRef', {}).get('name') == 'compositions-list'], [])
check('the RA is in the page namespace readers can read (restactions get)',
      named(DASH, 'RESTAction', 'dashboard-compositions-count')['metadata']['namespace'],
      named(DASH, 'Role', next(d['metadata']['name'] for d in DASH if d.get('kind') == 'Role' and 'read-dashboard-pages' in d['metadata']['name']))['metadata']['namespace'])

for line in failures:
    print(f'FAIL  {line}')
print(f'{"FAILED" if failures else "ok"}: dashboard onboarding gate ({len(failures)} failure(s), {len(CASES)} cases, JQ={JQ})')
raise SystemExit(1 if failures else 0)
