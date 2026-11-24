#!/usr/bin/env python3
"""
test-access-preview — /settings/access/<username> checks the user WITH the groups they have.

The `userinfo` stage looked the user up by `.username`, but a stage filter sees {extras, <stage>}: the
request's params are under `.extras` there. It matched nobody, so every local user was checked without their
groups and an administrator read "No access" ten times (visual audit, 2026-11-24, krateo-057).

Resolved by test-ra-input-gates' model of snowplow's resolver: the SubjectAccessReviews it would post, and what
the page reads. The control looks the user up as before. JQ=gojq runs the same checks on gojq.
Exit code = failed checks.
"""
import copy
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('rig', os.path.join(HERE, 'test-ra-input-gates.py'))
rig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rig)
tbi = rig.tbi
failures = 0


def check(name, ok, detail=''):
    global failures
    failures += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'' if ok else ': ' + str(detail)[:400]}")


USERS = {'items': [{'metadata': {'name': 'admin'}, 'spec': {'groups': ['admins']}},
                   {'metadata': {'name': 'cyberjoker'}, 'spec': {'groups': ['devs']}}]}


def preview(ra, username, users=USERS):
    """(the groups each review is posted with, the page's data). Reviews of `admins` are allowed."""
    responses = {'userinfo': users}
    sars = [s['name'] for s in ra['spec']['api'] if s['name'].startswith('sar_')]
    # First pass: what is asked. A review is answered by who was asked about.
    for name in sars:
        responses[name] = {'status': {'allowed': False}}
    requests, _, _ = rig.resolve(ra, {'username': username}, responses)
    posted = {n: p['spec'] for n, _, p in requests if n in sars}
    for name in sars:
        responses[name] = {'status': {'allowed': 'admins' in posted[name]['groups']}}
    _, _, out = rig.resolve(ra, {'username': username}, responses)
    return posted, out


docs = tbi.render(os.path.join(HERE, '..', 'helm', 'settings-page'))
[ra] = [d for d in docs if d.get('kind') == 'RESTAction' and d['metadata']['name'] == 'access-preview']

posted, out = preview(ra, 'admin')
check('a local user is reviewed with the groups they have',
      len(posted) == 10 and all(p['user'] == 'admin' and p['groups'] == ['admins', 'system:authenticated'] for p in posted.values()),
      posted)
check('and the page says which groups', (out['username'], out['groups'], out['strategy']) == ('admin', ['admins'], 'basic'), out)
check('and what they may do', all(r['allowed'] for r in out['rows']), out['rows'])
posted, out = preview(ra, 'cyberjoker')
check('another user, theirs', all(p['groups'] == ['devs', 'system:authenticated'] for p in posted.values()) and out['groups'] == ['devs'], posted)
posted, out = preview(ra, 'someone@idp')
check('a user of an identity provider: no groups, and said', all(p['groups'] == ['system:authenticated'] for p in posted.values())
      and (out['groups'], out['strategy']) == ([], 'unknown'), out)
check('no username asks about nobody', preview(ra, '')[1]['groups'] == [], '')
before = copy.deepcopy(ra)
for step in before['spec']['api']:
    if step['name'] == 'userinfo':
        assert '.extras.username' in step['filter']
        step['filter'] = step['filter'].replace('(.extras.username)', '.username')
posted, out = preview(before, 'admin')
check('control: looked up by .username, the administrator has no group', out['groups'] == [] and not any(r['allowed'] for r in out['rows']), out)
print(f'access preview: {failures} failure(s)')
sys.exit(failures)
