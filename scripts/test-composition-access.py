#!/usr/bin/env python3
"""The composition Access card, over the chart's OWN jq.

composition-detail's `access` object decides what a tenant sees, and the widgets only show it. So
this runs the RESTAction's real filter over every state in the approved mockup, plus the gate's edges.

THE GATE IS AN ALLOW-LIST. Ready must be True with reason Available or AvailablePartialCoverage, so a
reason nobody has heard of shows no button. `reason == Available` alone would have hidden the button
on 14 of 82 compositions on krateo-057, and a paused composition reports Ready=True. The reason
strings are the values core-provider emits.

`ApiResolved` is "not False" rather than "True", because a composition with no apiRef has no such
condition. A projected field is never cleared, so ApiResolved=False is the only sign an address is
stale.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHART = os.path.join(REPO, 'helm', 'compositions-page')
JQ = os.environ.get('JQ', 'jq')

URL = 'https://acme.example'
FUTURE = '2099-11-08T14:00:00Z'
PAST = '2001-11-08T14:00:00Z'
AVAILABLE = ('True', 'Available')


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
        proc = subprocess.run(['helm', 'template', 'c', copy, '--set', 'namespace=krateo-composition'],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def jq(program, data):
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


def named(docs, name):
    return next(d for d in docs if d.get('metadata', {}).get('name') == name)


def unwrap(expr):
    return expr.strip().removeprefix('${').removesuffix('}').strip()


def access(program, ready=AVAILABLE, api=None, **status):
    conditions = [] if ready is None else [{'type': 'Ready', 'status': ready[0], 'reason': ready[1]}]
    if api is not None:
        conditions.append({'type': 'ApiResolved', 'status': api, 'reason': 'x'})
    composition = {'kind': 'K', 'apiVersion': 'composition.krateo.io/v1', 'metadata': {'name': 'n', 'namespace': 'ns'},
                   'status': {'conditions': conditions, **status}}
    return jq(program, {'name': 'n', 'namespace': 'ns', 'found': [composition], 'crds': []})


def main():
    docs = render()
    program = named(docs, 'composition-detail')['spec']['filter']
    failures = []

    def check(label, got, want):
        if got != want:
            failures.append(f'{label}: got {got!r}, want {want!r}')

    def state(label, want, **kw):
        a = access(program, **kw)['access']
        check(label, {k: a[k] for k in want}, want)

    # Every state in the mockup.
    state('setting up', {'visible': True, 'open': False, 'plainUrl': '', 'plan': 'plan: trial',
                         'message': 'Setting up. The link appears here when it’s ready.'},
          ready=('False', 'Creating'), plan='trial', expiresAt=FUTURE)
    state('ready on a trial', {'open': True, 'url': URL, 'plainUrl': '', 'label': 'Open your Krateo', 'message': '',
                               'plan': 'plan: trial', 'planColor': 'blue', 'when': 'ends 8 Nov, 14:00 UTC'},
          url=URL, plan='trial', expiresAt=FUTURE, urlLabel='Open your Krateo')
    state('ready on a paid plan: empty means no deadline', {'open': True, 'when': '', 'plan': 'plan: team'},
          url=URL, plan='team', expiresAt='')
    state('ready with unhealthy workloads', {'open': True, 'url': URL,
                                             'message': 'Some workloads are unhealthy, so parts of it may not respond.'},
          url=URL, plan='trial', workloadHealthy=False)
    state('trial ended', {'open': False, 'url': '', 'plainUrl': '', 'plan': 'Trial ended', 'planColor': 'gray',
                          'when': 'ended 8 Nov, 14:00 UTC', 'message': 'This has ended, so there’s nothing to open.'},
          url=URL, plan='trial', expiresAt=PAST)
    state('not ready after being ready', {'open': False, 'url': '', 'plainUrl': URL,
                                          'message': 'Unavailable. This address won’t work until it’s back.'},
          ready=('False', 'Unavailable'), url=URL, plan='trial')
    state('address may be out of date', {'open': False, 'plainUrl': URL,
                                         'message': 'The address couldn’t be refreshed, so this one may be out of date.'},
          api='False', url=URL, plan='trial')
    for reason in ('ReconcilePaused', 'ReconcileGracefullyPaused'):
        state(f'paused: {reason}', {'open': False, 'plainUrl': URL,
                                    'message': 'Reconciliation is paused, so this address can’t be confirmed.'},
              ready=('True', reason), url=URL, plan='trial')

    # The gate's edges.
    state('partial coverage is still available', {'open': True}, ready=('True', 'AvailablePartialCoverage'), url=URL)
    state('ApiResolved True opens', {'open': True}, api='True', url=URL)
    state('no ApiResolved condition opens', {'open': True}, url=URL)
    state('ApiResolved Unknown opens: the gate is not False, not True', {'open': True}, api='Unknown', url=URL)
    state('a reason nobody knows blocks', {'open': False, 'plainUrl': URL}, ready=('True', 'SomethingNew'), url=URL)
    state('Ready Unknown blocks', {'open': False, 'plainUrl': URL,
                                   'message': 'This address can’t be confirmed right now.'},
          ready=('Unknown', 'ZeroEvidence'), url=URL)
    state('no Ready condition blocks', {'open': False, 'plainUrl': URL}, ready=None, url=URL)
    state('http is not https', {'open': False, 'plainUrl': 'http://acme.example',
                                'message': 'This address isn’t https, so it can’t be opened from here.'},
          url='http://acme.example')
    state('a script address never opens', {'open': False, 'url': ''}, url='javascript:alert(1)')
    state('no label means Open', {'label': 'Open'}, url=URL)
    state('an unparseable date is no deadline', {'open': True, 'when': ''}, url=URL, expiresAt='soon')
    state('a plan with no address still shows', {'visible': True, 'open': False, 'message': ''}, plan='team')
    state('a composition publishing neither gets no card', {'visible': False}, plan='')

    # The page mounts the card only when visible, and leaves its other rows alone.
    page = named(docs, 'page-composition-detail') if any(d.get('metadata', {}).get('name') == 'page-composition-detail' for d in docs) \
        else next(d for d in docs if d.get('kind') == 'Flex' and 'composition-detail-page-header' in str(d['spec']['widgetData'].get('items')))
    expr = unwrap(next(t['expression'] for t in page['spec']['widgetDataTemplate'] if t['forPath'] == 'items'))
    static = [i['resourceRefId'] for i in page['spec']['widgetData']['items']]
    check('the page without the card is the static list',
          [i['resourceRefId'] for i in jq(expr, {'access': {'visible': False}})], static)
    check('the page keeps every row and only adds the card', sorted(
        i['resourceRefId'] for i in jq(expr, {'access': {'visible': True}})), sorted(static + ['composition-detail-access']))
    check('the page refs include the card', 'composition-detail-access' in
          [r['id'] for r in page['spec']['resourcesRefs']['items']], True)

    # The card's items, for the Button above all: it must not exist unless the gate passed.
    card = named(docs, 'composition-detail-access')
    items = unwrap(next(t['expression'] for t in card['spec']['widgetDataTemplate'] if t['forPath'] == 'items'))
    declared = {r['id'] for r in card['spec']['resourcesRefs']['items']}

    def mounted(**kw):
        return [i['resourceRefId'].removeprefix('composition-detail-access-') for i in jq(items, access(program, **kw))]

    check('ready: meta, button, link', mounted(url=URL, plan='trial'), ['meta', 'open', 'url'])
    check('stale: no button and no link', mounted(api='False', url=URL, plan='trial'), ['meta', 'plain-url', 'message'])
    check('ended: nothing to open', mounted(url=URL, plan='trial', expiresAt=PAST), ['meta', 'message'])
    check('setting up: no address', mounted(ready=('False', 'Creating'), plan='trial'), ['meta', 'message'])
    for kw in ({'url': URL, 'plan': 'trial'}, {'api': 'False', 'url': URL}, {'url': URL, 'expiresAt': PAST, 'plan': 'p'}):
        for item in jq(items, access(program, **kw)):
            check('every item is declared', item['resourceRefId'] in declared, True)

    # The plain address must not be a widget that links it.
    plain = named(docs, 'composition-detail-access-plain-url')
    check('the plain address is not a Paragraph', plain['kind'], 'Descriptions')

    # The Button reads the gated url, never status.url.
    button = named(docs, 'composition-detail-access-open')
    actions = unwrap(next(t['expression'] for t in button['spec']['widgetDataTemplate'] if t['forPath'] == 'actions'))
    check('the Button opens access.url', jq(actions, {'access': {'url': URL}})['navigate'][0]['path'], URL)
    check('the Button reads the gate, not the status', 'status' in actions, False)

    for line in failures:
        print(f'FAIL  {line}')
    print(f'{"FAILED" if failures else "ok"}: composition access ({len(failures)} failure(s))')
    return min(len(failures), 125)


if __name__ == '__main__':
    sys.exit(main())
