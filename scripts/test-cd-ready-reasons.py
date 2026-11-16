#!/usr/bin/env python3
"""A blueprint's state matches core-provider's generated table of a CompositionDefinition's reasons.

core-provider generates the complete, closed list of a CompositionDefinition's condition reasons
(docs/cd-ready-reasons.md, core-provider#557): each Ready reason with its status and how a parent
composition's rollup reads it (`parent verdict`), and the Synced reasons. The portal classifies a
blueprint from ONE definition, cdState in portal.cdReadyDefs (helm/portal/templates/_cd-ready-reasons.tpl,
copied byte-identical into blueprints-page), read by the blueprint cards and the detail header pill.
Before it, a card was red only on Synced=False, so a failed Ready read "still installing".

Checks, in order:
  1. cd-ready-reasons.yaml's `table` is the table core-provider publishes now, row for row:
     (condition, reason, writer) -> (status, parent verdict), columns found by header name. A reason
     added, removed or reclassified fails here, so the portal's handling is decided before the
     snapshot is updated.
  2. cdReadyVerdict maps every Ready reason in the snapshot to its verdict (first word), and nothing
     else; a reason two writers write must carry one status and verdict. cdSyncedPaused names only
     Synced reasons the snapshot has.
  3. The cards' own jq (the rendered blueprints-cards RESTAction), run over one CompositionDefinition
     per Ready row with Synced=True, shows the state that row's verdict means; and the cases where
     Synced decides (paused, a failed pass, drift) show theirs, with the reason core-provider wrote.

Usage: test-cd-ready-reasons.py [--table FILE]   (default: fetch the `source` URL in cd-ready-reasons.yaml)
Exit code = number of failures. Runs its controls first: an added reason, a reclassified reason, a
verdict map missing a reason and a card jq without the verdict table must each be caught.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SNAPSHOT = os.path.join(REPO, 'cd-ready-reasons.yaml')
DEFS = os.path.join(REPO, 'helm', 'portal', 'templates', '_cd-ready-reasons.tpl')
COLUMNS = ('condition', 'reason', 'status', 'writer', 'parent verdict')   # read BY HEADER NAME
JQ = os.environ.get('JQ', 'jq')

_spec = importlib.util.spec_from_file_location('ready_reasons', os.path.join(HERE, 'test-ready-reasons.py'))
ready_reasons = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ready_reasons)
cells = ready_reasons.cells

# What each verdict means on a card (readyState), with Synced=True.
STATE_OF = {'healthy': 'ready', 'converging': 'installing', 'failed': 'failed', 'unevaluated': 'unobserved'}
VERDICTS = re.compile(r'def cdReadyVerdict: (\{[^}]*\});')
PAUSED = re.compile(r'def cdSyncedPaused: (\[[^\]]*\]);')


def short(verdict):
    return verdict.split(' ', 1)[0]


def parse_table(markdown):
    """{(condition, reason, writer): (status, verdict)} from core-provider's markdown table."""
    lines = markdown.splitlines()
    head = next((i for i, l in enumerate(lines) if l.lstrip().startswith('|') and 'reason' in cells(l)), None)
    if head is None:
        raise ValueError('no table header with a `reason` column')
    names = cells(lines[head])
    missing = [c for c in COLUMNS if c not in names]
    if missing:
        raise ValueError(f'table header {names} lacks {missing}')
    at = {c: names.index(c) for c in COLUMNS}
    rows = {}
    for line in lines[head + 2:]:
        if not line.lstrip().startswith('|'):
            break
        row = cells(line)
        if len(row) != len(names):
            raise ValueError(f'a row has {len(row)} cells, the header {len(names)}: {line[:80]}')
        key = (row[at['condition']].strip('`'), row[at['reason']].strip('`'), row[at['writer']])
        rows[key] = (row[at['status']], row[at['parent verdict']])
    return rows


def snapshot_rows(doc):
    return {(r['condition'], r['reason'], r['writer']): (str(r['status']), r['verdict']) for r in doc['table']}


def read_defs(text):
    v, p = VERDICTS.search(text), PAUSED.search(text)
    return (json.loads(v.group(1)) if v else None), (json.loads(p.group(1)) if p else None)


def check_table(live, snap):
    out = []
    if not live:
        return ['core-provider table: no rows parsed (a parse that reads nothing must not pass)']
    for key in sorted(set(live) | set(snap)):
        if key not in snap:
            out.append(f'core-provider added {key[0]}/{key[1]} ({key[2]}): {live[key]}; decide how the portal shows it, then add it to cd-ready-reasons.yaml')
        elif key not in live:
            out.append(f'core-provider removed {key[0]}/{key[1]} ({key[2]}); drop it from cdState and cd-ready-reasons.yaml')
        elif live[key] != snap[key]:
            out.append(f'core-provider reclassified {key[0]}/{key[1]} ({key[2]}): {snap[key]} -> {live[key]}')
    return out


def check_defs(snap, verdicts, paused):
    if verdicts is None or paused is None:
        return ['_cd-ready-reasons.tpl: no cdReadyVerdict or cdSyncedPaused definition found']
    out, want = [], {}
    for (cond, reason, _), (status, verdict) in snap.items():
        if cond != 'Ready':
            continue
        if reason in want and want[reason] != (status, short(verdict)):
            out.append(f'Ready/{reason} has two statuses or verdicts across writers {want[reason]} / {(status, short(verdict))}: '
                       'cdReadyVerdict is keyed by reason alone and cannot express it')
        want[reason] = (status, short(verdict))
    have = {r: v for r, v in verdicts.items()}
    if set(have) != set(want):
        out.append(f'cdReadyVerdict keys {sorted(have)} != the table\'s Ready reasons {sorted(want)}')
    out += [f'cdReadyVerdict[{r!r}] = {have[r]!r}, the table says {want[r][1]!r}'
            for r in sorted(set(have) & set(want)) if have[r] != want[r][1]]
    out += [f'cdReadyVerdict[{r!r}] = {v!r}, not a verdict the cards show' for r, v in have.items() if v not in STATE_OF]
    synced = {reason for (cond, reason, _) in snap if cond == 'Synced'}
    out += [f'cdSyncedPaused names {p!r}, which is not a Synced reason in the table' for p in paused if p not in synced]
    return out


# ---- the cards' own jq ---------------------------------------------------------------------------

def render_portal():
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', os.path.join(REPO, 'helm', 'portal'), copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template portal failed:\n{proc.stderr.strip()}')
        docs = [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]
    ra = next(d for d in docs if d.get('kind') == 'RESTAction' and d['metadata']['name'] == 'blueprints-cards')
    return next(a for a in ra['spec']['api'] if a['name'] == 'compdefs')['filter']


def cond(type_, status, reason, message=''):
    return {'type': type_, 'status': status, 'reason': reason, 'message': message}


def run_cards(program, conditions_list):
    items = [{'metadata': {'name': f'cd{i}', 'namespace': 'ns'}, 'spec': {'chart': {'version': '1.0.0'}},
              'status': ({'conditions': c} if c is not None else {})} for i, c in enumerate(conditions_list)]
    with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run([JQ, '-c', '-f', path], input=json.dumps({'compdefs': {'items': items}}),
                              capture_output=True, text=True, check=False)
    finally:
        os.unlink(path)
    if proc.returncode != 0:
        raise AssertionError(f'jq failed: {proc.stderr.strip()}')
    return json.loads(proc.stdout)


SYNCED_OK = cond('Synced', 'True', 'ReconcileSuccess')
# (label, conditions, readyState, readyColor, readyReason) where Synced decides, or nothing is known.
SYNCED_CASES = [
    ('a failed Ready with Synced=False: the tooltip is Ready\'s message, not "observe failed"',
     [cond('Ready', 'False', 'SchemaDefaultContradiction', '#/properties/security: default {}: missing property'),
      cond('Synced', 'False', 'ReconcileError', 'observe failed: #/properties/security: default {}: missing property')],
     'failed', 'red', 'SchemaDefaultContradiction · #/properties/security: default {}: missing property'),
    ('paused: gray, never failed, names Ready',
     [cond('Ready', 'True', 'Available'), cond('Synced', 'False', 'ReconcilePaused')], 'paused', 'gray', 'Paused · Ready: Available'),
    ('not observed with its error on Synced: failed, with Synced\'s message',
     [cond('Ready', 'Unknown', 'NotObserved'), cond('Synced', 'False', 'ReconcileError', 'connect: refused')],
     'failed', 'red', 'ReconcileError · connect: refused'),
    ('drift: Ready=True, the last pass failed',
     [cond('Ready', 'True', 'Available'), cond('Synced', 'False', 'ReconcileError', 'boom')], 'failed', 'red', 'ReconcileError · boom'),
    ('a converging Ready with a failed pass: failed', [cond('Ready', 'False', 'Creating'), cond('Synced', 'False', 'ReconcileError', 'x')],
     'failed', 'red', 'ReconcileError · x'),
    ('no conditions yet: installing', None, 'installing', 'orange', ''),
    ('a Ready=False reason this portal does not know: failed, never progress',
     [cond('Ready', 'False', 'SomethingNew2028', 'm'), SYNCED_OK], 'failed', 'red', 'SomethingNew2028 · m'),
]


def check_cards(program, snap):
    out, cases = [], []
    for (c, reason, writer), (status, verdict) in sorted(snap.items()):
        if c != 'Ready':
            continue
        want = STATE_OF.get(short(verdict))
        cases.append((f'Ready={status}/{reason} ({writer}), verdict {short(verdict)}',
                      [cond('Ready', status, reason, 'msg'), SYNCED_OK], want, None,
                      (reason + ' · msg') if want == 'failed' else reason))
    cases += SYNCED_CASES
    got = run_cards(program, [c[1] for c in cases])
    for (label, _, state, color, reason), card in zip(cases, got):
        have = (card.get('readyState'), card.get('readyColor') if color else None, card.get('readyReason'))
        if have != (state, color, reason):
            out.append(f'card, {label}: got {have}, want {(state, color, reason)}')
    return out


TABLE_CONTROL = """intro
| condition | reason | status | writer | parent verdict | written by |
|---|---|---|---|---|---|
| Ready | `Creating` | False | provider-runtime | converging | the create path |
| Synced | `ReconcilePaused` | False | provider-runtime | — | paused |
"""


def controls(snap, defs_text, program):
    problems = []
    want = {('Ready', 'Creating', 'provider-runtime'): ('False', 'converging'),
            ('Synced', 'ReconcilePaused', 'provider-runtime'): ('False', '—')}
    if parse_table(TABLE_CONTROL) != want:
        problems.append('control: the plain table did not parse')
    for label, broken in (('a header without `condition`', TABLE_CONTROL.replace('| condition |', '| kind |')),
                          ('no table at all', 'intro only')):
        try:
            parse_table(broken)
            problems.append(f'control: {label} was parsed instead of refused')
        except ValueError:
            pass
    if check_table(dict(snap), snap):
        problems.append('control: the snapshot against itself was refused')
    added = dict(snap)
    added[('Ready', 'NewReason2028', 'core-provider')] = ('False', 'failed')
    if not check_table(added, snap):
        problems.append('control: an added reason was NOT caught')
    reclassified = dict(snap)
    reclassified[('Ready', 'Creating', 'core-provider')] = ('False', 'failed')
    if not check_table(reclassified, snap):
        problems.append('control: a reclassified reason was NOT caught')
    if check_table({}, snap) == []:
        problems.append('control: an empty table was accepted')
    verdicts, paused = read_defs(defs_text)
    if check_defs(snap, verdicts, paused):
        problems.append('control: the defs against the snapshot were refused')
    if not check_defs(snap, {k: v for k, v in verdicts.items() if k != 'SchemaDefaultContradiction'}, paused):
        problems.append('control: a verdict map missing a reason was NOT caught')
    if not check_defs(snap, {**verdicts, 'Deleting': 'failed'}, paused):
        problems.append('control: a verdict map disagreeing with the table was NOT caught')
    if check_cards(program, snap):
        problems.append('control: the cards against the snapshot were refused')
    # Mutant: the card jq with the verdict table emptied must fail (a failed Ready=False with
    # Synced=True must not pass by falling back on status; converging ones would read failed).
    if not check_cards(VERDICTS.sub('def cdReadyVerdict: {};', program), snap):
        problems.append('control: the cards WITHOUT the verdict table passed, so the cases do not test it')
    return problems


def main():
    with open(SNAPSHOT, encoding='utf-8') as fh:
        doc = yaml.safe_load(fh)
    snap = snapshot_rows(doc)
    with open(DEFS, encoding='utf-8') as fh:
        defs_text = fh.read()
    program = render_portal()
    problems = controls(snap, defs_text, program)
    if problems:
        for p in problems:
            print(f'[FAIL] {p}')
        return len(problems)
    print('[PASS] controls: an added and a reclassified reason, a verdict map missing or contradicting a reason, '
          'an empty table and the cards without the verdict table are caught; a header without `condition` is refused')
    failures = check_defs(snap, *read_defs(defs_text)) + check_cards(program, snap)
    args = sys.argv[1:]
    if '--table' in args:
        where = args[args.index('--table') + 1]
        with open(where, encoding='utf-8') as fh:
            markdown = fh.read()
    else:
        where = doc['source']
        try:
            with urllib.request.urlopen(where, timeout=30) as resp:
                markdown = resp.read().decode('utf-8')
        except urllib.error.URLError as exc:
            markdown = None
            failures.append(f'core-provider table at {where}: could not be read ({exc})')
    if markdown is not None:
        try:
            failures += check_table(parse_table(markdown), snap)
        except ValueError as exc:
            failures.append(f'core-provider table at {where}: {exc}')
    for f in failures:
        print(f'[FAIL] {f}')
    if not failures:
        ready = sum(1 for k in snap if k[0] == 'Ready')
        print(f'[PASS] {len(snap)} CompositionDefinition reasons match {where}; the cards show each of the {ready} '
              f'Ready rows as its verdict means, and the {len(SYNCED_CASES)} cases where Synced decides')
    return min(len(failures), 125)


if __name__ == '__main__':
    sys.exit(main())
