#!/usr/bin/env python3
"""A blueprint's state matches core-provider's reason JSON for a CompositionDefinition.

core-provider publishes the complete, closed list of a CompositionDefinition's condition reasons as
JSON generated from its code (docs/cd-ready-reasons.json, core-provider#667): one entry per
(condition, status, reason), each Ready reason with how a parent composition's rollup reads it
(`verdict`), and the Synced reasons. The portal classifies a blueprint from ONE definition, cdState in
portal.cdReadyDefs (helm/portal/templates/_cd-ready-reasons.tpl, copied byte-identical into
blueprints-page), read by the blueprint cards and the detail header pill; its cdReadyVerdict map is
generated from the JSON at the core-provider ref scripts/gen-reason-defs.py pins. Before it, a card
was red only on Synced=False, so a failed Ready read "still installing".

The generator's --check says the .tpl is what it would write; this says, reading the .tpl on its own,
that what is there means what the JSON says. Checks, in order:
  1. cdReadyVerdict maps every Ready reason in the JSON to its verdict, and nothing else; a reason
     with two Ready entries (two statuses) must carry one verdict. cdSyncedPaused names only Synced
     reasons the JSON has.
  2. The cards' own jq (the rendered blueprints-cards RESTAction), run over one CompositionDefinition
     per Ready entry with Synced=True, shows the state that entry's verdict means; and the cases where
     Synced decides (paused, a failed pass, drift) show theirs, with the reason core-provider wrote.

Usage: test-cd-ready-reasons.py [--json-dir DIR]   (default: fetch the JSON at the generator's pin)
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

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFS = os.path.join(REPO, 'helm', 'portal', 'templates', '_cd-ready-reasons.tpl')
JQ = os.environ.get('JQ', 'jq')

_spec = importlib.util.spec_from_file_location('gen_reason_defs', os.path.join(HERE, 'gen-reason-defs.py'))
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

# What each verdict means on a card (readyState), with Synced=True.
STATE_OF = {'healthy': 'ready', 'converging': 'installing', 'failed': 'failed', 'unevaluated': 'unobserved'}
VERDICTS = re.compile(r'def cdReadyVerdict: (\{[^}]*\});')
PAUSED = re.compile(r'def cdSyncedPaused: (\[[^\]]*\]);')


def entries(doc):
    """{(condition, reason, status): verdict} from the JSON, one per entry."""
    return {(e['condition'], e['reason'], e['status']): e['verdict'] for e in doc['reasons']}


def read_defs(text):
    v, p = VERDICTS.search(text), PAUSED.search(text)
    return (json.loads(v.group(1)) if v else None), (json.loads(p.group(1)) if p else None)


def check_defs(snap, verdicts, paused):
    if not snap:
        return ['cd-ready-reasons.json: no entries (a table that lists nothing must not pass)']
    if verdicts is None or paused is None:
        return ['_cd-ready-reasons.tpl: no cdReadyVerdict or cdSyncedPaused definition found']
    out, want = [], {}
    for (cond, reason, status), verdict in snap.items():
        if cond != 'Ready':
            continue
        if reason in want and want[reason] != (status, verdict):
            out.append(f'Ready/{reason} has two statuses with verdicts {want[reason]} / {(status, verdict)}: '
                       'cdReadyVerdict is keyed by reason alone and cannot express it')
        want[reason] = (status, verdict)
    have = {r: v for r, v in verdicts.items()}
    if set(have) != set(want):
        out.append(f'cdReadyVerdict keys {sorted(have)} != the table\'s Ready reasons {sorted(want)}')
    out += [f'cdReadyVerdict[{r!r}] = {have[r]!r}, the JSON says {want[r][1]!r}'
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
    for (c, reason, status), verdict in sorted(snap.items()):
        if c != 'Ready':
            continue
        want = STATE_OF.get(verdict)
        cases.append((f'Ready={status}/{reason}, verdict {verdict}',
                      [cond('Ready', status, reason, 'msg'), SYNCED_OK], want, None,
                      (reason + ' · msg') if want == 'failed' else reason))
    cases += SYNCED_CASES
    got = run_cards(program, [c[1] for c in cases])
    for (label, _, state, color, reason), card in zip(cases, got):
        have = (card.get('readyState'), card.get('readyColor') if color else None, card.get('readyReason'))
        if have != (state, color, reason):
            out.append(f'card, {label}: got {have}, want {(state, color, reason)}')
    return out


def controls(snap, program):
    """Each mutant must be caught. The verdict map they are checked against, and the one put in the
    cards' jq, are built from the JSON itself, and each mutant picks an entry the JSON has, so the
    controls mean the same whatever the JSON lists and whether or not the .tpl agrees with it. When
    even a map built from the JSON is refused, the real check after this says why."""
    verdicts = {reason: verdict for (cond, reason, _), verdict in snap.items() if cond == 'Ready'}
    paused = [r for r in ('ReconcilePaused',) if ('Synced', r, 'False') in snap]
    program = VERDICTS.sub(lambda _: f'def cdReadyVerdict: {json.dumps(verdicts)};', program)
    if check_defs(snap, verdicts, paused) or check_cards(program, snap):
        return []
    problems = []
    ready = sorted(k for k in snap if k[0] == 'Ready')
    other = {'healthy': 'converging', 'converging': 'failed', 'failed': 'converging', 'unevaluated': 'failed'}
    mutants = [('an added Ready reason', {**snap, ('Ready', 'NewReason2028', 'False'): 'failed'}),
               ('a reclassified Ready reason', {**snap, ready[0]: other[snap[ready[0]]]}),
               ('a removed Ready reason', {k: v for k, v in snap.items() if k != ready[0]}),
               ('an empty table', {})]
    if paused:
        mutants.append(('cdSyncedPaused naming a removed Synced reason',
                        {k: v for k, v in snap.items() if k != ('Synced', paused[0], 'False')}))
    for label, mutant in mutants:
        if not check_defs(mutant, verdicts, paused):
            problems.append(f'control: {label} was NOT caught')
    failed = next((r for r, v in verdicts.items() if v == 'failed'), None)
    if not check_defs(snap, {k: v for k, v in verdicts.items() if k != failed}, paused):
        problems.append('control: a verdict map missing a reason was NOT caught')
    if not check_defs(snap, {**verdicts, ready[0][1]: other[verdicts[ready[0][1]]]}, paused):
        problems.append('control: a verdict map disagreeing with the table was NOT caught')
    # Mutant: the card jq with the verdict table emptied must fail (a failed Ready=False with
    # Synced=True must not pass by falling back on status; converging ones would read failed).
    if not check_cards(VERDICTS.sub('def cdReadyVerdict: {};', program), snap):
        problems.append('control: the cards WITHOUT the verdict table passed, so the cases do not test it')
    return problems


def main():
    args = sys.argv[1:]
    json_dir = args[args.index('--json-dir') + 1] if '--json-dir' in args else None
    where = json_dir or f'core-provider {gen.CORE_PROVIDER_REF[:12]} (the pin)'
    try:
        snap = entries(gen.load_tables(json_dir)[gen.CD])
    except ValueError as exc:
        print(f'[FAIL] {exc}')
        return 1
    with open(DEFS, encoding='utf-8') as fh:
        defs_text = fh.read()
    program = render_portal()
    problems = controls(snap, program)
    if problems:
        for p in problems:
            print(f'[FAIL] {p}')
        return len(problems)
    print('[PASS] controls: an added, a reclassified and a removed Ready reason, a paused reason the JSON lacks, an empty '
          'table, a verdict map missing or contradicting a reason and the cards without the verdict table are caught')
    failures = check_defs(snap, *read_defs(defs_text)) + check_cards(program, snap)
    for f in failures:
        print(f'[FAIL] {f}')
    if not failures:
        ready = sum(1 for k in snap if k[0] == 'Ready')
        print(f'[PASS] {len(snap)} CompositionDefinition reasons in the reason JSON from {where}; cdReadyVerdict '
              f'matches the {ready} Ready entries, the cards show each as its verdict means, and the {len(SYNCED_CASES)} '
              'cases where Synced decides')
    return min(len(failures), 125)


if __name__ == '__main__':
    sys.exit(main())
