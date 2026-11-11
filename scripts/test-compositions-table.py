#!/usr/bin/env python3
"""The compositions table's Coverage cell, and that its cells still match its columns.

WHY COVERAGE EXISTS. core-provider's rollup reports Ready=True with reason
`AvailablePartialCoverage` when most children could not be health-evaluated — so the green pill is
correct and carries far less evidence than the identical pill on a fully-read composition. On
krateo-057 that was 30 of 85 compositions, with portal-agents at 118 of 121 children unevaluated.

THE RULE THE CELL ENCODES, and the one that is easy to get wrong: the three scalars are ABSENT when
the rollup did not run, and absent is not zero. `(.evaluated // 0) == (.managed // 0)` is true of a
composition nobody assessed and reads as perfect coverage. So presence is checked before equality,
and both "no reading" and "full reading" render blank — a number here always means "look".

AND THE ALIGNMENT. A Table renders `dataSource` cells positionally against `columns`. A cell list
that disagrees with the column list does not error: every row silently shifts. Adding a column
without its cell, or a cell without its column, is a one-line change that nothing else here
catches, so the agreement is asserted rather than eyeballed.

CHILDREN UNREADABLE. Ready=Unknown with reason `ChildrenForbidden` (core-provider 2.13.82) means the
controller was refused reading the children: an RBAC defect, not a composition converging. The
table's reconcile tag says "Children unreadable" (`warning`) rather than "Pending", it leaves the
Pending chip and the Observability Pending stat, and ?status=unreadable filters to it. ZeroEvidence
and every other Unknown stay Pending.
"""
import json
import os
import re
import subprocess
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
JQ = os.environ.get('JQ', 'jq')

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f'{label}: {got!r} != {want!r}')


def render(chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', os.path.join(REPO, 'helm', chart), copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy], capture_output=True, text=True, check=False)
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


def unwrap(expr):
    expr = expr.strip()
    assert expr.startswith('${') and expr.endswith('}'), expr
    return expr[2:-1]


# --- the coverage rule, read out of the RA rather than restated here ----------------------------
ra = named(render('portal'), 'compositions-list')['spec']['filter']
# A REPORTED FAILURE, not an assert: if this stops matching, the test must say so in the same
# vocabulary as every other failure rather than ending in a traceback. I found that out by
# controlling it — the control changed the expression, the regex stopped matching, and the script
# died with an AssertionError that my own grep for 'FAIL' did not see. A test whose failure is
# invisible to the thing reading its output is the shape this whole file exists to prevent.
match = re.search(r'coverage: (\(if \(?\.evaluated.*?end\))', ra)
if match is None:
    print('FAIL  the coverage expression is no longer recognisable in compositions-list — '
          'if it was renamed or restructured, update this test; it is not asserting anything now')
    raise SystemExit(1)
coverage = match.group(1)

for data, want, label in [
    ({'evaluated': 3, 'managed': 121}, '3/121', 'partial shows the number'),
    ({'evaluated': 0, 'managed': 121}, '0/121', 'nothing evaluated still shows the number'),
    ({'evaluated': 12, 'managed': 12}, '', 'full coverage is blank'),
    # THE ONE THAT MATTERS: absent is not zero. `// 0` here would render this as full coverage.
    ({}, '', 'ABSENT scalars are blank, not 0/0'),
    ({'evaluated': 0, 'managed': 0}, '', 'zero and equal is blank'),
    ({'managed': 121}, '', 'half-absent is blank rather than guessed'),
    ({'evaluated': 121, 'managed': 121, 'unreadable': 0}, '', 'unreadable=0 with full coverage is blank'),
]:
    check(f'coverage: {label}', jq(coverage, data), want)

# --- the columns and the cells must agree, in order ---------------------------------------------
table = named(render('compositions-page'), 'compositions-table')
columns = [c['valueKey'] for c in table['spec']['widgetData']['columns']]
source = unwrap(next(e['expression'] for e in table['spec']['widgetDataTemplate']
                     if e['forPath'] == 'dataSource'))

row = {
    'av': 'composition.krateo.io/v0-1-0', 'conditions': [
        {'reason': 'AvailablePartialCoverage', 'status': 'True', 'type': 'Ready'},
        {'reason': 'ReconcileSuccess', 'status': 'True', 'type': 'Synced'},
    ],
    'coverage': '3/121', 'kind': 'PortalAgents', 'name': 'portal-agents', 'ns': 'krateo-system',
    'railState': 'healthy', 'state': 'healthy', 'ts': '2026-11-04T10:00:00Z', 'uid': 'u1',
}
rows = jq(source, {'filtered': [row], 'list': [row]})
cells = [c['valueKey'] for c in rows[0]]
check('cells match columns in order', cells, columns)
check('coverage cell carries the number', [c['stringValue'] for c in rows[0] if c['valueKey'] == 'coverage'], ['3/121'])

blank = dict(row, coverage='')
blank_rows = jq(source, {'filtered': [blank], 'list': [blank]})
check('coverage cell is empty when the RA says blank',
      [c['stringValue'] for c in blank_rows[0] if c['valueKey'] == 'coverage'], [''])
check('a blank coverage does not change the cell count', len(blank_rows[0]), len(columns))

# A condition's REASON decides the words, because the status alone misreads two states. These are
# the values core-provider emits, and the paused pair is the shape seen live on krateo-057.
def pills(conditions, rail='healthy'):
    one = dict(row, conditions=conditions, railState=rail)
    got = jq(source, {'filtered': [one], 'list': [one]})[0]
    cell = next(c for c in got if c['valueKey'] == 'status')
    tag = next(c for c in got if c['valueKey'] == 'reconcile')
    return [(p['type'], p['color']) for p in json.loads(cell['stringValue'])], (tag['stringValue'], tag['color'])


PAUSED = [{'reason': 'ReconcilePaused', 'status': 'True', 'type': 'Ready'},
          {'reason': 'ReconcilePaused', 'status': 'False', 'type': 'Synced'}]
check('paused reads as one Paused pill, not Ready beside NotSynced', pills(PAUSED, 'paused'),
      ([('Paused', 'gray')], ('Paused', 'gray')))
check('gracefully paused reads the same', pills([{'reason': 'ReconcileGracefullyPaused', 'status': 'True', 'type': 'Ready'},
      {'reason': 'ReconcileGracefullyPaused', 'status': 'False', 'type': 'Synced'}], 'paused'),
      ([('Paused', 'gray')], ('Paused', 'gray')))
check('creating is amber, and Synced keeps its own pill',
      pills([{'reason': 'Creating', 'status': 'False', 'type': 'Ready'},
             {'reason': 'ReconcileSuccess', 'status': 'True', 'type': 'Synced'}], 'pending'),
      ([('Creating', 'orange'), ('Synced', 'green')], ('Pending', 'orange')))
check('unavailable is still NotReady',
      pills([{'reason': 'Unavailable', 'status': 'False', 'type': 'Ready'}], 'failed'),
      ([('NotReady', 'red')], ('Failed', 'red')))
check('unknown is still gray',
      pills([{'reason': 'ZeroEvidence', 'status': 'Unknown', 'type': 'Ready'}], 'pending'),
      ([('ReadyUnknown', 'gray')], ('Pending', 'orange')))
check('children unreadable is its own reconcile tag, in the attention colour',
      pills([{'reason': 'ChildrenForbidden', 'status': 'Unknown', 'type': 'Ready'}], 'unreadable'),
      ([('ReadyUnknown', 'gray')], ('Children unreadable', 'warning')))

# The RA filter is where a composition is counted, so the dashboard's numbers come from here.
portal = render('portal')
ra = named(portal, 'compositions-list')
ra_filter = ra['spec']['filter']


def state_of(conditions, **extra):
    item = {'uid': 'u', 'name': 'n', 'ns': 'krateo-system', 'ts': '2026-11-04T10:00:00Z', 'kind': 'K',
            'conditions': conditions, **extra}
    out = jq(ra_filter, {'allCompositions': [item]})
    only = out['list'][0]
    return only['state'], only['railState']


check('available is healthy', state_of([{'reason': 'Available', 'status': 'True', 'type': 'Ready'},
      {'reason': 'ReconcileSuccess', 'status': 'True', 'type': 'Synced'}]), ('healthy', 'healthy'))
check('creating is pending, not failed', state_of([{'reason': 'Creating', 'status': 'False', 'type': 'Ready'}]),
      ('pending', 'pending'))
check('unavailable is failed', state_of([{'reason': 'Unavailable', 'status': 'False', 'type': 'Ready'}]),
      ('failed', 'failed'))
check('paused is not drift', state_of(PAUSED), ('healthy', 'paused'))
check('a real drift is still drift', state_of([{'reason': 'Available', 'status': 'True', 'type': 'Ready'},
      {'reason': 'ReconcileError', 'status': 'False', 'type': 'Synced'}]), ('healthy', 'drift'))
check('unknown is pending', state_of([{'reason': 'ZeroEvidence', 'status': 'Unknown', 'type': 'Ready'}]),
      ('pending', 'pending'))
check('children forbidden is unreadable, not pending',
      state_of([{'reason': 'ChildrenForbidden', 'status': 'Unknown', 'type': 'Ready'}]), ('unreadable', 'unreadable'))
check('inventory not recorded stays pending',
      state_of([{'reason': 'InventoryNotRecorded', 'status': 'Unknown', 'type': 'Ready'}]), ('pending', 'pending'))
check('a near-miss spelling stays pending',
      state_of([{'reason': 'Forbidden', 'status': 'Unknown', 'type': 'Ready'}]), ('pending', 'pending'))
check('children forbidden on Ready=False is failed',
      state_of([{'reason': 'ChildrenForbidden', 'status': 'False', 'type': 'Ready'}]), ('failed', 'failed'))

# --- where an unreadable composition is counted and filtered: the Compositions chips + filter, and the
# Observability Pending stat. One of each Ready shape that lands in Pending or near it.
def comp(name, status, reason):
    return {'uid': name, 'name': name, 'ns': 'krateo-system', 'ts': '2026-11-04T10:00:00Z', 'kind': 'K',
            'conditions': [{'reason': reason, 'status': status, 'type': 'Ready'}]}


MIX = [comp('available', 'True', 'Available'), comp('zero-evidence', 'Unknown', 'ZeroEvidence'),
       comp('forbidden', 'Unknown', 'ChildrenForbidden'), comp('inventory', 'Unknown', 'InventoryNotRecorded'),
       comp('creating', 'False', 'Creating'), comp('unavailable', 'False', 'Unavailable')]


def listing(items, **params):
    return jq(ra_filter, {'allCompositions': items, **params})


out = listing(MIX)
check('counts: pending leaves children unreadable out, keeps the other Unknowns and Creating',
      (out['counts']['pending'], out['counts'].get('unreadable')), (3, 1))
check('?status=pending lists no unreadable composition',
      sorted(i['name'] for i in listing(MIX, status='pending')['filtered']), ['creating', 'inventory', 'zero-evidence'])
check('?status=unreadable lists exactly the unreadable ones',
      [i['name'] for i in listing(MIX, status='unreadable')['filtered']], ['forbidden'])
check('?status=all still lists every composition', len(listing(MIX, status='all')['filtered']), len(MIX))

chips_doc = named(render('compositions-page'), 'compositions-status-chips')
chips_expr = unwrap(next(e['expression'] for e in chips_doc['spec']['widgetDataTemplate'] if e['forPath'] == 'dataSource'))


def chips(out):
    return [(c['label'], c['count'], c['active']) for c in jq(chips_expr, out)]


check('chips: Children unreadable appears after Pending with its count while some are unreadable',
      chips(out), [('All', 6, True), ('Healthy', 1, False), ('Drift', 0, False), ('Pending', 3, False),
                   ('Children unreadable', 1, False), ('Degraded', 0, False), ('Failed', 1, False)])
check('chips: its route filters to them',
      [c['route'] for c in jq(chips_expr, out) if c['label'] == 'Children unreadable'],
      ['/compositions?status=unreadable&scope=workloads'])
none_out = listing([i for i in MIX if i['name'] != 'forbidden'])
check('chips: no Children unreadable chip when none is',
      [c[0] for c in chips(none_out)], ['All', 'Healthy', 'Drift', 'Pending', 'Degraded', 'Failed'])
check('chips: kept while it is the selected filter, even at 0',
      [c for c in chips(listing([MIX[0]], status='unreadable')) if c[0] == 'Children unreadable'],
      [('Children unreadable', 0, True)])

obs_pending = named(render('observability-page'), 'obs-stat-pending')
obs_expr = unwrap(next(e['expression'] for e in obs_pending['spec']['widgetDataTemplate'] if e['forPath'] == 'value'))
check('observability Pending leaves children unreadable out (ZeroEvidence and InventoryNotRecorded stay)',
      jq(obs_expr, out), 2)
check('observability Pending is unchanged without an unreadable composition', jq(obs_expr, none_out), 2)

# Every railState must be in the dashboard rail's colour map: a value missing there renders near-black.
rail_list = named(render('dashboard-page'), 'rail-list')
rail_map = rail_list['spec']['widgetData']['itemTemplate']['bar']['color']['map'] \
    if 'bar' in rail_list['spec']['widgetData']['itemTemplate'] else {}
check('the dashboard rail colours every railState',
      sorted({'healthy', 'drift', 'pending', 'degraded', 'failed', 'paused', 'unreadable'} - set(rail_map)), [])

for line in failures:
    print(f'FAIL  {line}')
print(f'{"FAILED" if failures else "ok"}: compositions table — {len(columns)} columns, coverage rule'
      f' ({len(failures)} failure(s))')
raise SystemExit(1 if failures else 0)
