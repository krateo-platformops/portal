#!/usr/bin/env python3
"""compositions-list's "not assessable" compositions: who is in the bucket, over the RA's own jq.

WHY. core-provider reports Ready=Unknown with reason `ZeroEvidence` when none of a composition's
managed children has readiness semantics it can read (unstructured-runtime v1.12.0 condition.go).
That is permanent and healthy, so compositions-list marks it `assessable: false` and keeps it out of
the Pending figures (`assessment`, `conditionCoverage`), and its `rail` says "No signal" with the
reason (`rail.reason`, "Ready=Unknown · ZeroEvidence: <the controller's message>").

`ChildrenForbidden` (core-provider 2.13.82, cdc childhealth.go) is the other Unknown that cannot be
judged: the controller was REFUSED reading every child it could not otherwise assess, an RBAC
defect. Since Diego's decision of 2026-11-10 it is set aside too (not Pending: nothing converges),
but labelled as the defect it is: `.state` and `.railState` are `unreadable` (the Compositions page
says "Children unreadable"), its rail state is `unreadable`, and its reason is kept.

THE RULE, and the one that is easy to get wrong: the bucket is matched on the REASON, exactly as
core-provider spells it. Unknown also comes with `InventoryNotRecorded` (transient by design), and
the set of reasons is open. So an Unknown with any other reason must stay Pending. A match on
`status == "Unknown"` alone passes the 057 case and fails the defect case below.

This was test-dashboard-assessable.py. The Dashboard's stat cards, coverage list and rail that drew
these fields are gone (the Dashboard is the platform inbox now, and test-dashboard-inbox.py holds
its own ZeroEvidence rule); the RA keeps the fields, so their rule is kept here, on the RA's output.

HOW. The fixture is krateo-057's 82 compositions as read on 2026-11-08 (65 Available, 14
AvailablePartialCoverage, 1 ReconcilePaused, 2 ZeroEvidence), trimmed to the fields the RA reads.
The RA's own jq (both stages) runs over it.

Usage: test-compositions-assessable.py
"""
import copy
import json
import os
import subprocess
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
JQ = os.environ.get('JQ', 'jq')
FIXTURE = os.path.join(HERE, 'fixtures', 'dashboard-compositions-057.json')

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
        proc = subprocess.run(['helm', 'template', 'c', copy_dir], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template {chart} failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def named(docs, name):
    for d in docs:
        if d.get('metadata', {}).get('name') == name:
            return d
    raise AssertionError(f'{name} not in the rendered chart')


def jq(program, data):
    with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run([JQ, '-c', '-f', path], input=json.dumps(data),
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise AssertionError(f'jq failed: {proc.stderr.strip()}')
        return json.loads(proc.stdout)
    finally:
        os.unlink(path)


def query(text):
    """plumbing jqutil.MaybeQuery: the jq between `${` and its matching `}`, or None for a literal."""
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


# --- the RA, both stages ----------------------------------------------------------------------
PORTAL = render('portal')
RA = named(PORTAL, 'compositions-list')['spec']
STAGE = next(a for a in RA['api'] if a['name'] == 'allCompositions')['filter']


def compositions_list(items):
    projected = jq(STAGE, {'allCompositions': {'items': items}})
    return jq(RA['filter'], {'allCompositions': projected})


def coverage(out, t='Ready'):
    row = next((r for r in out['conditionCoverage'] if r['type'] == t), None)
    return row and (row['label'], row['percent'], row['note'])


def rail(out):
    return {i['name']: i['rail'] for i in out['list']}


with open(FIXTURE, encoding='utf-8') as fh:
    LIVE = json.load(fh)


def unknown(name, reason):
    return {'apiVersion': 'composition.krateo.io/v0-1-0', 'kind': 'Synthetic',
            'metadata': {'name': name, 'namespace': 'krateo-system', 'uid': f'uid-{name}',
                         'creationTimestamp': '2026-11-01T00:00:00Z'},
            'status': {'conditions': [{'type': 'Ready', 'status': 'Unknown', 'reason': reason, 'message': ''},
                                      {'type': 'Synced', 'status': 'True', 'reason': 'ReconcileSuccess'}]}}


def ready_reason(item):
    return next((c['reason'] for c in item['status']['conditions'] if c['type'] == 'Ready'), None)


# core-provider 2.13.82's message for this reason (childrenForbiddenMessage + withUnreadable).
FORBIDDEN_MSG = ('Composition health is unknown: none of its 3 managed children could be health-evaluated '
                 '(3 of 3 managed children could NOT BE READ — this controller\'s ServiceAccount was refused '
                 'access, which is a gap in the generated RBAC rather than a problem with the children: '
                 'Deployment/web, Service/web, ConfigMap/web-config)')
UNREADABLE = unknown('children-forbidden', 'ChildrenForbidden')
UNREADABLE['status']['conditions'][0]['message'] = FORBIDDEN_MSG
DEFECTS = [unknown('inventory-not-recorded', 'InventoryNotRecorded'), unknown('some-new-reason', 'SomeNewReason')]
CREATING = unknown('creating', 'Creating')
CREATING['status']['conditions'][0]['status'] = 'False'

check('the fixture is the 057 mix', sorted({r: [ready_reason(i) for i in LIVE].count(r)
                                            for r in map(ready_reason, LIVE)}.items()),
      [('Available', 65), ('AvailablePartialCoverage', 14), ('ReconcilePaused', 1), ('ZeroEvidence', 2)])
SILENT = {'event-health', 'model-configs'}
check('the fixture\'s ZeroEvidence pair', sorted(i['metadata']['name'] for i in LIVE if ready_reason(i) == 'ZeroEvidence'), sorted(SILENT))

# --- 1. krateo-057 as it is -----------------------------------------------------------------------
out = compositions_list(LIVE)
check('057: assessment', out['assessment'],
      {'total': 82, 'assessable': 80, 'notAssessable': 2, 'pending': 0, 'healthyPercent': 100, 'pendingPercent': 0})
check('057: coverage Ready counts the assessable, and says how many it left out',
      coverage(out), ('80/80', 100, '+ 2 not assessable (no readiness signal)'))
check('057: no other coverage row carries a note', sorted(r['type'] for r in out['conditionCoverage'] if r['type'] != 'Ready' and r['note']), [])
REASONS = {n: f'Ready=Unknown · ZeroEvidence: none of its {k} managed children could be health-evaluated '
              '(no readiness semantics this controller knows how to read)'
           for n, k in [('event-health', 5), ('model-configs', 3)]}
for name in sorted(SILENT):
    check(f'057: {name} rail is No signal, with its reason', rail(out)[name],
          {'percent': 0, 'state': 'notAssessable', 'label': 'No signal', 'reason': REASONS[name]})
check('057: only the not assessable rows carry rail.reason', sorted(n for n, r in rail(out).items() if r.get('reason')), sorted(SILENT))
check('057: the RA keeps .state and .railState pending for them',
      sorted({(i['state'], i['railState']) for i in out['list'] if i['name'] in SILENT}), [('pending', 'pending')])

# --- 2. ChildrenForbidden is set aside too, and says it is a defect; other Unknowns stay Pending --
out = compositions_list(LIVE + [UNREADABLE] + DEFECTS)
SET_ASIDE = SILENT | {'children-forbidden'}
check('defects: assessment', out['assessment'],
      {'total': 85, 'assessable': 82, 'notAssessable': 3, 'pending': 2, 'healthyPercent': 97, 'pendingPercent': 2})
check('defects: coverage Ready', coverage(out), ('80/82', 97, '+ 3 not assessable (no readiness signal)'))
item = next(i for i in out['list'] if i['name'] == 'children-forbidden')
check('defects: children-forbidden is not assessable, and unreadable', (item['assessable'], item['state'], item['railState']),
      (False, 'unreadable', 'unreadable'))
check('defects: children-forbidden rail names the defect', item['rail'],
      {'percent': 0, 'state': 'unreadable', 'label': 'Children unreadable',
       'reason': 'Ready=Unknown · ChildrenForbidden: ' + FORBIDDEN_MSG.removeprefix('Composition health is unknown: ')})
for d in DEFECTS:
    name = d['metadata']['name']
    item = next(i for i in out['list'] if i['name'] == name)
    check(f'defects: {name} is assessable and pending', (item['assessable'], item['state'], item['railState']), (True, 'pending', 'pending'))
    check(f'defects: {name} keeps its pending bar, no reason', item['rail'], {'percent': 50, 'state': 'pending', 'label': '50%'})
check('defects: only the not assessable rows carry rail.reason', sorted(n for n, r in rail(out).items() if r.get('reason')), sorted(SET_ASIDE))
item = compositions_list([CREATING])['list'][0]
check('creating: assessable and pending', (item['assessable'], item['state'], item['railState']), (True, 'pending', 'pending'))
CREATING2 = copy.deepcopy(CREATING)
CREATING2['metadata'].update(name='creating-2', uid='uid-creating-2')
out = compositions_list(LIVE + [UNREADABLE] + DEFECTS + [CREATING, CREATING2])
check('creating: counted pending with the two other Unknowns, and pendingPercent from the same set',
      (out['assessment']['pending'], out['assessment']['pendingPercent'], out['assessment']['healthyPercent']), (4, 4, 95))
only_creating = compositions_list([CREATING])['assessment']
check('only Creating: one pending, all of the assessable', (only_creating['pending'], only_creating['pendingPercent']), (1, 100))

# --- 3. nothing to set aside ------------------------------------------------------------------------
NONE = [i for i in LIVE if ready_reason(i) != 'ZeroEvidence'] + DEFECTS
out = compositions_list(NONE)
check('none: nothing set aside', (out['assessment']['notAssessable'], coverage(out)), (0, ('80/82', 97, '')))
check('none: no row carries rail.reason', sorted(i['name'] for i in out['list'] if 'reason' in i['rail']), [])

# --- 4. edges -------------------------------------------------------------------------------------
out = compositions_list([])
check('empty: no division by zero', out['assessment'],
      {'total': 0, 'assessable': 0, 'notAssessable': 0, 'pending': 0, 'healthyPercent': 0, 'pendingPercent': 0})
only_silent = [i for i in LIVE if ready_reason(i) == 'ZeroEvidence']
check('all silent: nothing assessable, nothing pending',
      {k: compositions_list(only_silent)['assessment'][k] for k in ('assessable', 'notAssessable', 'pending')},
      {'assessable': 0, 'notAssessable': 2, 'pending': 0})
odd = copy.deepcopy(only_silent[0])
odd['status']['conditions'][0]['status'] = 'False'
check('ZeroEvidence on Ready=False is not set aside', compositions_list([odd])['list'][0]['assessable'], True)
check('ZeroEvidence on Ready=False carries no rail.reason', 'reason' in compositions_list([odd])['list'][0]['rail'], False)
odd = copy.deepcopy(UNREADABLE)
odd['status']['conditions'][0]['status'] = 'False'
check('ChildrenForbidden on Ready=False is not set aside, and failed',
      (compositions_list([odd])['list'][0]['assessable'], compositions_list([odd])['list'][0]['rail']['state']), (True, 'failed'))
check('Unknown/Forbidden is not ChildrenForbidden', compositions_list([unknown('forbidden', 'Forbidden')])['list'][0]['assessable'], True)
bare = copy.deepcopy(only_silent[0])
bare['status']['conditions'][0]['message'] = ''
check('ZeroEvidence with no message: the reason is the condition alone',
      compositions_list([bare])['list'][0]['rail']['reason'], 'Ready=Unknown · ZeroEvidence')
bare['status']['conditions'][0].pop('message')
check('ZeroEvidence with the message absent: the reason is the condition alone',
      compositions_list([bare])['list'][0]['rail']['reason'], 'Ready=Unknown · ZeroEvidence')
bare['status']['conditions'][0]['message'] = 'no child reports readiness'
check('ZeroEvidence with another message: kept whole',
      compositions_list([bare])['list'][0]['rail']['reason'], 'Ready=Unknown · ZeroEvidence: no child reports readiness')

for line in failures:
    print(f'FAIL  {line}')
print(f'{"FAILED" if failures else "ok"}: compositions-list not-assessable bucket ({len(failures)} failure(s))')
raise SystemExit(1 if failures else 0)
