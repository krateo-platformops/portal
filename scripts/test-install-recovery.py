#!/usr/bin/env python3
"""The automatic retries of a first install (core-provider #636), over compositions-page's OWN jq.

Shown only while the composition is not Ready (Diego, 2026-11-23; approved mockup
https://claude.ai/artifact/6yK98K3k8CGHzeZEGewt3f). The inputs are core-provider's samples from a test on
the real runtime (PR #643, 2026-11-24), with the final field names. Runs the Status card's item list and
the two Paragraphs as they ship. A control drops the not-Ready condition and must show a retry on a Ready
composition. JQ=gojq runs the same checks on gojq. Exit code = failed checks.
"""
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tmv', os.path.join(HERE, 'test-migrated-values.py'))
tmv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tmv)
failures = 0


def jq(expression, data):
    proc = subprocess.run([tmv.JQ, '-c', expression.strip()[2:-1]], input=json.dumps(data),
                          capture_output=True, text=True, check=False)
    if proc.returncode:
        raise SystemExit(f'{tmv.JQ}: {proc.stderr}')
    return json.loads(proc.stdout)


def template(doc, path):
    [expr] = [t['expression'] for t in doc['spec']['widgetDataTemplate'] if t['forPath'] == path]
    return expr


def check(name, got, want):
    global failures
    failures += got != want
    print(f"[{'PASS' if got == want else 'FAIL'}] {name}" + ('' if got == want else f': got {got!r}, want {want!r}'))


ERR = 'install failed: failed post-install: 1 error occurred'
RECOVERY = {'kind': 'Retried', 'count': 5, 'lastAt': '2026-11-23T21:59:13Z', 'reason': 'Failed', 'message': 'prose'}
FAILS = {'count': 5, 'lastAt': '2026-11-23T21:59:12Z', 'lastError': ERR}


def comp(status, reason, recovery=None, fails=None):
    st = {'conditions': [{'type': 'Ready', 'status': status, 'reason': reason}]}
    if recovery:
        st['installRecovery'] = recovery
    if fails:
        st['installFailures'] = fails
    return {'detail': {'metadata': {'name': 'shop'}, 'status': st}}


BASE = ['composition-detail-status-verdict', 'list-composition-detail-rail', 'composition-detail-status-facts',
        'list-composition-detail-problems', 'composition-incidents']
RETRY, ERROR = 'composition-detail-install-retry', 'composition-detail-install-error'
docs = tmv.render(tmv.COMPOSITIONS)
card = docs[('Card', 'card-composition-detail-status')]
items, retry, error = template(card, 'items'), template(docs[('Paragraph', RETRY)], 'text'), template(docs[('Paragraph', ERROR)], 'text')


def shown(data):
    ids = [i['resourceRefId'] for i in jq(items, data)]
    return {'extra': [i for i in ids if i not in BASE], 'base': [i for i in ids if i in BASE] == BASE,
            'first': ids[0], 'retry': jq(retry, data), 'error': jq(error, data)}


def want(extra=(), retry_text='', error_text=''):
    return {'extra': list(extra), 'base': True, 'first': BASE[0], 'retry': retry_text, 'error': error_text}


FIVE = 'Install retried 5 times, last on 23 Nov 21:59 UTC. The attempt before it failed.'
check('failing: the retry line, then the chart’s own error', shown(comp('False', 'InstallFailing', RECOVERY, FAILS)),
      want([RETRY, ERROR], FIVE, f'Last error: {ERR}'))
check('creating, first retry, interrupted: the line alone',
      shown(comp('False', 'Creating', {**RECOVERY, 'count': 1, 'reason': 'Interrupted', 'lastAt': '2026-11-23T21:57:00Z'}, {**FAILS, 'count': 1})),
      want([RETRY], 'Install retried once, on 23 Nov 21:57 UTC. The attempt before it was interrupted.'))
check('recovered (Ready): nothing, though the count is kept', shown(comp('True', 'Available', {**RECOVERY, 'count': 6})), want())
check('InventoryNotRecorded for a moment before Ready: the line, no error', shown(comp('Unknown', 'InventoryNotRecorded', RECOVERY)), want([RETRY], FIVE))
check('never retried: nothing', shown(comp('False', 'Creating')), want())
check('a failed install with no retry yet: nothing to say', shown(comp('False', 'Creating', None, {**FAILS, 'count': 1})), want())
check('InstallFailing without a lastError: the line alone', shown(comp('False', 'InstallFailing', RECOVERY)), want([RETRY], FIVE))
check('an unknown reason and no time: the count alone', shown(comp('False', 'Creating', {'count': 2, 'reason': 'Other'})), want([RETRY], 'Install retried 2 times.'))
check('a malformed field says nothing', shown(comp('False', 'Creating', 'x')), want())
check('no composition at all: the list is unchanged', shown({'detail': None}), want())
ids = {i['id'] for i in card['spec']['resourcesRefs']['items']}
check('every listed item has a ref', {RETRY, ERROR} | set(BASE) <= ids, True)
check('the static list is the list without a retry', [i['resourceRefId'] for i in card['spec']['widgetData']['items']], BASE)
broken = items.replace('== "True" then null elif', '== "Never" then null elif')
check('control: without the not-Ready condition a Ready composition shows its retries',
      broken != items and RETRY in [i['resourceRefId'] for i in jq(broken, comp('True', 'Available', RECOVERY))], True)
print(f'install recovery: {failures} failure(s)')
sys.exit(failures)
