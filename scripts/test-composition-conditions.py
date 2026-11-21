#!/usr/bin/env python3
"""A composition's conditions are coloured by what they mean, and a paused one says so, over the chart's OWN jq.

UX review round 3: a paused composition showed Ready=True in green and Synced=False in red, both
ReconcilePaused, which read as broken. Approved mockup: https://claude.ai/artifact/2DzX7yxTtVAb72mTgs1YE7
(section 1, and 1b option A for the note). This runs the shipped expressions of
list-composition-detail-conditions (row `tone`), paragraph composition-detail-paused (the note) and
card-composition-detail-health (whether the note leads the card) over:

  paused (krateo-057 acme-feedback-controller)   both rows `paused`; the note says since when
  healthy                                         Available / ReconcileSuccess -> healthy
  converging                                      Ready=False Creating (core-provider policy run C4) -> converging, never failed
  failing                                         Synced=False ReconcileError, Workload=False -> failed
  not observed                                    Ready=Unknown -> unknown (gray)
A control: colouring by the raw status again must fail the paused case.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.environ.get('COMPOSITIONS_CHART') or os.path.join(os.path.dirname(HERE), 'helm', 'compositions-page')
JQ = os.environ.get('JQ', 'jq')
NOTE_TAIL = ". Krateo isn't changing this composition, so these are the last values before the pause."


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
        out = subprocess.run(['helm', 'template', 't', copy], capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def expr(doc, for_path):
    e = next(t['expression'] for t in doc['spec']['widgetDataTemplate'] if t['forPath'] == for_path).strip()
    assert e.startswith('${') and e.endswith('}'), e[:40]
    return e[2:-1]


def jq(prog, data):
    proc = subprocess.run([JQ, '-c', prog], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip())
    return json.loads(proc.stdout)


def c(type_, status, reason, at='2026-11-04T10:10:16Z', message=''):
    return {'type': type_, 'status': status, 'reason': reason, 'lastTransitionTime': at, 'message': message}


PAUSED = [c('Ready', 'True', 'ReconcilePaused'), c('Synced', 'False', 'ReconcilePaused')]
ROWS = [
    ('paused', PAUSED, ['paused', 'paused']),
    ('healthy', [c('Ready', 'True', 'Available'), c('Synced', 'True', 'ReconcileSuccess')], ['healthy', 'healthy']),
    ('converging', [c('Ready', 'False', 'Creating', message='1 of 2 managed children are not ready'),
                    c('Synced', 'True', 'ReconcileSuccess')], ['converging', 'healthy']),
    ('failing', [c('Ready', 'True', 'Available'), c('Synced', 'False', 'ReconcileError'),
                 c('Workload', 'False', 'Unhealthy')], ['healthy', 'failed', 'failed']),
    ('not observed', [c('Ready', 'Unknown', 'NotObserved')], ['unknown']),
]


def main():
    docs = render()
    by = {(d['kind'], d['metadata']['name']): d for d in docs}
    rows_prog = expr(by[('Listy', 'list-composition-detail-conditions')], 'dataSource')
    note_prog = expr(by[('Paragraph', 'composition-detail-paused')], 'text')
    card = by[('Card', 'card-composition-detail-health')]
    items_prog = expr(card, 'items')
    color = by[('Listy', 'list-composition-detail-conditions')]['spec']['widgetData']['itemTemplate']['color']
    bad = []

    def check(label, got, want):
        if got != want:
            bad.append(label)
            print(f'FAIL {label}: got {got!r}, want {want!r}')

    check('colour follows tone', color['value'], '{tone}')
    check('tone palette', color['map'], {'healthy': 'green', 'failed': 'red', 'converging': 'orange', 'paused': 'gray'})
    for label, conds, want in ROWS:
        got = jq(rows_prog, {'conditions': conds})
        check(f'{label}: tones', [r['tone'] for r in got], want)
        check(f'{label}: True/False kept', [r['status'] for r in got], [x['status'] for x in conds])

    detail = lambda conds, paused: {'detail': {'metadata': {'annotations': {'krateo.io/paused': 'true'} if paused else {}},
                                               'status': {'conditions': conds}}}
    check('note: since when', jq(note_prog, detail(PAUSED, True)),
          '❚❚ Reconciliation is paused since 4 Nov 10:10 UTC' + NOTE_TAIL)
    check('note: no ReconcilePaused condition yet', jq(note_prog, detail([c('Ready', 'True', 'Available')], True)),
          '❚❚ Reconciliation is paused' + NOTE_TAIL)
    ids = lambda out: [i['resourceRefId'] for i in out]
    check('card: paused leads with the note', ids(jq(items_prog, detail(PAUSED, True))),
          ['paragraph-composition-detail-paused', 'list-composition-detail-conditions'])
    check('card: not paused, conditions only', ids(jq(items_prog, detail(ROWS[1][1], False))),
          ['list-composition-detail-conditions'])
    refs = {r['id'] for r in card['spec']['resourcesRefs']['items']}
    check('card: both refs declared', sorted(refs), ['list-composition-detail-conditions', 'paragraph-composition-detail-paused'])

    control = rows_prog.replace('if $r == "ReconcilePaused" then "paused" elif', 'if', 1)
    if control == rows_prog or [r['tone'] for r in jq(control, {'conditions': PAUSED})] == ['paused', 'paused']:
        bad.append('control')
        print('FAIL control: colouring by the raw status did not change the paused case')

    print(f'composition conditions: {len(ROWS)} row cases + note + card, {len(bad)} failure(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
