#!/usr/bin/env python3
"""The blueprint page shows a caller only the controls they may use.

acmp, on demo (2026-11-16): a tenant opening a blueprint saw Uninstall, Update version and Deploy to
fleet. The apiserver refused each write, but the page offered them. Diego's decision: hide them.
blueprint-detail asks the authorizer (SelfSubjectAccessReview) about each control's own request, and
the two Flexes that hold the controls mount them from its `may`.

Two things are checked, over the chart as rendered:
  1. THE PROBE IS THE CONTROL'S REQUEST. Each probe's verb, group, resource and namespace are compared
     with the request the control actually makes (its Button or Form action). A probe that asked
     about a different verb or namespace would hide a control the caller can use, or show one they
     cannot, and nothing else would notice.
  2. THE FLEXES MOUNT WHAT `may` ALLOWS. Both widgetDataTemplate expressions are run with jq over
     every combination of `may`, and over an output with no `may` at all (a probe skipped or failed),
     which must keep every control.

And UNINSTALL'S PLACE AND WORDS (UX audit BP-4, approved mockup): Uninstall is under the header's
"⋯" menu (blueprint-detail-more), never in the row beside Create, and the menu, not the row, follows
`may.uninstall`. Its rest action's `confirmation` is the page's (blueprint-detail's `confirmation`):
"Uninstall <name>?", what it removes, an amber Alert listing the compositions created from it with
their state and a link, none when there are none, and "could not be listed" when they were not read.
The Details card has no Ready or Synced row (the header's pill reads both).
BLUEPRINTS_CHART=<origin/main's chart> is the control: those checks fail there.

Uses `helm` and `jq`. Exit code = number of failures.
"""
import itertools
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.environ.get('BLUEPRINTS_CHART') or os.path.join(os.path.dirname(HERE), 'helm', 'blueprints-page')
RELEASE_NS = 'krateo-blueprints-page'


def render():
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', CHART, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read().replace('version: CHART_VERSION', 'version: 0.0.0-test')
        open(meta, 'w', encoding='utf-8').write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy, '--namespace', RELEASE_NS,
                               '--set', 'namespace=krateo-blueprints'], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip())
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def get(docs, kind, name):
    return next(d for d in docs if d.get('kind') == kind and d['metadata']['name'] == name)


def jq(program, data):
    proc = subprocess.run(['jq', '-c', program], input=json.dumps(data), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'jq failed: {proc.stderr.strip()}')
    return json.loads(proc.stdout)


def template(expr):
    expr = expr.strip()
    assert expr.startswith('${') and expr.endswith('}'), expr
    return expr[2:-1]


def probe(ra, name, item):
    """(verb, group, resource, namespace, name) a probe asks about, its payload rendered for `item`."""
    step = next(s for s in ra['spec']['api'] if s['name'] == name)
    payload = step['payload']
    body = jq(template(payload) + ' | fromjson', item) if payload.startswith('${') else json.loads(payload)
    a = body['spec']['resourceAttributes']
    return (a['verb'], a['group'], a['resource'], a.get('namespace'), a.get('name'))


def confirmation_failures(docs, ra):
    """BP-4: Uninstall's confirmation is the page's words, and Details has no Ready or Synced row."""
    f = []
    button = get(docs, 'Button', 'blueprint-detail-delete')['spec']
    tmpl = {t['forPath']: t['expression'] for t in button.get('widgetDataTemplate') or []}
    if tmpl.get('actions.rest[0].confirmation', '').strip() != '${ .confirmation }':
        f.append(f'Uninstall: the rest action\'s confirmation is not blueprint-detail\'s: {tmpl}')
    if button['widgetData']['actions']['rest'][0].get('requireConfirmation') is not True:
        f.append('Uninstall: requireConfirmation is off')
    prog = ra['spec']['filter']

    def comp(name, ready='True', paused=False):
        conds = [{'type': 'Ready', 'status': ready}]
        if paused:
            conds = [{'type': 'Ready', 'status': 'False', 'reason': 'ReconcilePaused'}, {'type': 'Synced', 'status': 'True'}]
        return {'metadata': {'name': name, 'namespace': 'krateo-system', 'creationTimestamp': f'2026-11-{10 + len(name):02d}T00:00:00Z'},
                'status': {'conditions': conds}}

    def page(**more):
        cd = {'metadata': {'name': 'acme-feedback', 'namespace': 'krateo-system'},
              'spec': {'chart': {'url': 'oci://ghcr.io/krateo-blueprints/charts/acme-feedback', 'version': '0.1.0'}},
              'status': {'apiVersion': 'composition.krateo.io/v0-1-0', 'resource': 'acmefeedbacks',
                         'conditions': [{'type': 'Ready', 'status': 'True'}, {'type': 'Synced', 'status': 'True'}]}}
        return jq(prog, dict({'name': 'acme-feedback', 'namespace': 'krateo-system', 'compdef': cd, 'catalog': {'entries': {}}}, **more))

    def check(label, got, want):
        if got != want:
            f.append(f'confirmation, {label}: {got!r} != {want!r}')

    head = {'title': 'Uninstall acme-feedback?', 'message': 'This removes the blueprint acme-feedback from the Platform.', 'okLabel': 'Uninstall'}
    check('one composition (the mockup)', page(comps={'items': [comp('acme-feedback-controller', paused=True)]}).get('confirmation'),
          dict(head, alert={'type': 'warning', 'message': '1 composition was created from it.',
                            'items': [{'label': 'acme-feedback-controller · krateo-system', 'href': '/compositions/krateo-system/acme-feedback-controller', 'tag': 'Paused'}]}))
    two = page(comps={'items': [comp('a'), comp('bb', ready='False')]}).get('confirmation') or {}
    check('two: plural, newest first, each its state', ((two.get('alert') or {}).get('message'), [(i['label'], i['tag']) for i in (two.get('alert') or {}).get('items', [])]),
          ('2 compositions were created from it.', [('bb · krateo-system', 'NotReady'), ('a · krateo-system', 'Ready')]))
    check('none: no Alert', page(comps={'items': []}).get('confirmation'), head)
    check('not listed: says so, never "none"', page(compsError=[{'message': 'forbidden'}]).get('confirmation'),
          dict(head, alert={'type': 'warning', 'message': 'The compositions created from it could not be listed.'}))
    many = page(comps={'items': [comp('c' * (i + 1)) for i in range(12)]}).get('confirmation') or {}
    check('twelve: ten listed, the count is all of them', ((many.get('alert') or {}).get('message'), len((many.get('alert') or {}).get('items', []))),
          ('12 compositions were created from it. The ten most recent are listed.', 10))
    for c in (many, two, head):
        text = json.dumps(c).lower()
        if 'delet' in text or 'orphan' in text or 'keep running' in text:
            f.append(f'confirmation says what happens to the compositions (not verified): {c}')
    info = template(get(docs, 'Descriptions', 'descriptions-blueprint-detail-info')['spec']['widgetDataTemplate'][0]['expression'])
    labels = [i['label'] for i in jq(info, page(comps={'items': []}))]
    check('Details: no Ready or Synced row', [l for l in labels if l in ('Ready', 'Synced')], [])
    return f


def main():
    docs = render()
    f = []
    ra = get(docs, 'RESTAction', 'blueprint-detail')
    item = {'namespace': 'krateo-bp-x', 'name': 'keystone'}

    # 1. Each probe asks about its control's own request.
    delete = get(docs, 'Button', 'blueprint-detail-delete')['spec']['resourcesRefsTemplate'][0]['template']
    update = get(docs, 'Form', 'blueprint-update')['spec']['resourcesRefsTemplate'][0]['template']
    fleet = get(docs, 'Form', 'fleet-rollout')['spec']['resourcesRefs']['items'][0]
    gvr = {'apiVersion': 'core.krateo.io/v1alpha1', 'resource': 'compositiondefinitions', 'name': 'keystone',
           'namespace': 'krateo-bp-x'}       # blueprint-detail's `gvr`, for the page `item` names

    def control(ref):
        def val(v):
            return jq(template(v), {'gvr': gvr}) if isinstance(v, str) and v.startswith('${') else v
        group = val(ref['apiVersion']).split('/')[0]
        verb = {'DELETE': 'delete', 'PATCH': 'patch', 'POST': 'create'}[ref['verb']]
        return (verb, group, val(ref['resource']), val(ref.get('namespace')), val(ref.get('name')))

    for label, stage, ref in [('Uninstall', 'mayDelete', delete), ('Update version', 'mayPatch', update),
                              ('Deploy to fleet', 'mayCreate', fleet)]:
        want, got = control(ref), probe(ra, stage, item)
        if got != want:
            f.append(f'{label}: probe {stage} asks about {got}, the control requests {want}')

    # 2. The Flexes mount exactly what `may` allows; no `may` keeps every control.
    actions = template(get(docs, 'Flex', 'blueprint-detail-actions')['spec']['widgetDataTemplate'][0]['expression'])
    more = [d for d in docs if d.get('kind') == 'Dropdown' and d['metadata']['name'] == 'blueprint-detail-more']
    if not more:
        f.append('no Dropdown blueprint-detail-more: Uninstall is not under "⋯"')
        menu = None
    else:
        menu = template(more[0]['spec']['widgetDataTemplate'][0]['expression'])
        wd = more[0]['spec']['widgetData']
        if (wd.get('ariaLabel'), wd.get('placement'), more[0]['spec'].get('apiRef', {}).get('name')) != ('More actions', 'bottomRight', 'blueprint-detail'):
            f.append(f'menu: {wd.get("ariaLabel")}, {wd.get("placement")}, apiRef {more[0]["spec"].get("apiRef")}')
    right = template(get(docs, 'Flex', 'blueprint-detail-col-right')['spec']['widgetDataTemplate'][0]['expression'])
    base = {'availableVersions': ['1.0.0', '1.1.0'], 'currentVersion': '1.0.0'}
    for fleet_ok, uninstall_ok, update_ok in itertools.product([True, False], repeat=3):
        out = dict(base, may={'fleet': fleet_ok, 'uninstall': uninstall_ok, 'update': update_ok})
        got_a = [i['resourceRefId'] for i in jq(actions, out)]
        want_a = (['blueprint-detail-marketplace'] + (['blueprint-detail-fleet'] if fleet_ok else [])
                  + ['blueprint-detail-ask-agentiko', 'blueprint-detail-create', 'blueprint-detail-more'])
        if got_a != want_a:
            f.append(f'actions, may={out["may"]}: {got_a} != {want_a}')
        if menu is not None:
            got_m = [i['resourceRefId'] for i in jq(menu, out)]
            if got_m != (['blueprint-detail-delete'] if uninstall_ok else []):
                f.append(f'menu, may={out["may"]}: {got_m}')
        got_r = [i['resourceRefId'] for i in jq(right, out)]
        # "What it creates" leads the column for EVERY reader (UX review round 3); the update flow follows `may`.
        want_r = ['card-blueprint-detail-contents'] + (['card-blueprint-detail-update', 'card-blueprint-impact-picker'] if update_ok else [])
        if got_r != want_r:
            f.append(f'right column, may={out["may"]}: {got_r} != {want_r}')
    for label, out in [('no may', dict(base)), ('may with nulls', dict(base, may={'fleet': None, 'uninstall': None, 'update': None}))]:
        if len(jq(actions, out)) != 5:
            f.append(f'actions, {label}: {jq(actions, out)} — every control must stay')
        if menu is not None and [i['resourceRefId'] for i in jq(menu, out)] != ['blueprint-detail-delete']:
            f.append(f'menu, {label}: {jq(menu, out)} — Uninstall must stay')
        if [i['resourceRefId'] for i in jq(right, out)] != ['card-blueprint-detail-contents', 'card-blueprint-detail-update', 'card-blueprint-impact-picker']:
            f.append(f'right column, {label}: {jq(right, out)} — the contents card, then the update flow, must stay')

    f.extend(confirmation_failures(docs, ra))

    for x in f:
        print(f'FAIL  {x}')
    print(f'{"PASS" if not f else "FAIL"}: probes match their controls; Flexes mount what `may` allows ({len(f)} failure(s))')
    return min(len(f), 125)


if __name__ == '__main__':
    sys.exit(main())
