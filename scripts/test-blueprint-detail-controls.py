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

And the /blueprints header's words (UX audit BP-3): the page's verb is Create, never the
Marketplace's Install; every blueprint on it is installed already.

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
CHART = os.path.join(os.path.dirname(HERE), 'helm', 'blueprints-page')
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
    right = template(get(docs, 'Flex', 'blueprint-detail-col-right')['spec']['widgetDataTemplate'][0]['expression'])
    base = {'availableVersions': ['1.0.0', '1.1.0'], 'currentVersion': '1.0.0'}
    for fleet_ok, uninstall_ok, update_ok in itertools.product([True, False], repeat=3):
        out = dict(base, may={'fleet': fleet_ok, 'uninstall': uninstall_ok, 'update': update_ok})
        got_a = [i['resourceRefId'] for i in jq(actions, out)]
        want_a = (['blueprint-detail-marketplace'] + (['blueprint-detail-fleet'] if fleet_ok else [])
                  + ['blueprint-detail-ask-agentiko', 'blueprint-detail-create']
                  + (['blueprint-detail-delete'] if uninstall_ok else []))
        if got_a != want_a:
            f.append(f'actions, may={out["may"]}: {got_a} != {want_a}')
        got_r = [i['resourceRefId'] for i in jq(right, out)]
        # "What it creates" leads the column for EVERY reader (UX review round 3); the update flow follows `may`.
        want_r = ['card-blueprint-detail-contents'] + (['card-blueprint-detail-update', 'card-blueprint-impact-picker'] if update_ok else [])
        if got_r != want_r:
            f.append(f'right column, may={out["may"]}: {got_r} != {want_r}')
    for label, out in [('no may', dict(base)), ('may with nulls', dict(base, may={'fleet': None, 'uninstall': None, 'update': None}))]:
        if len(jq(actions, out)) != 5:
            f.append(f'actions, {label}: {jq(actions, out)} — every control must stay')
        if [i['resourceRefId'] for i in jq(right, out)] != ['card-blueprint-detail-contents', 'card-blueprint-detail-update', 'card-blueprint-impact-picker']:
            f.append(f'right column, {label}: {jq(right, out)} — the contents card, then the update flow, must stay')

    subtitle = get(docs, 'PageHeader', 'blueprints-page-header')['spec']['widgetData']['subtitle']
    if 'install a blueprint' in subtitle.lower() or 'Create a composition' not in subtitle:
        f.append(f'/blueprints subtitle: {subtitle!r} — the page creates compositions; installing is the Marketplace\'s')

    for x in f:
        print(f'FAIL  {x}')
    print(f'{"PASS" if not f else "FAIL"}: probes match their controls; Flexes mount what `may` allows ({len(f)} failure(s))')
    return min(len(f), 125)


if __name__ == '__main__':
    sys.exit(main())
