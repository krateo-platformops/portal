#!/usr/bin/env python3
"""The portal's Ready-reason classification matches core-provider's reason JSON.

core-provider publishes the complete, closed list of a composition's condition reasons as JSON
generated from its code (docs/ready-reasons.json, core-provider#667), one entry per (condition,
status, reason) with how a parent's rollup reads it (`verdict`). The portal classifies reasons in
several charts from ONE definition, portal.readyReasonDefs (helm/portal/templates/_ready-reasons.tpl,
copied byte-identical into the page charts that need it), whose readyFalseConverging map
scripts/gen-reason-defs.py generates from the JSON at the core-provider ref it pins. Two reasons sat
red NotReady for exactly the lack of this check: HelmOperationInProgress and Deleting are converging,
and the portal only knew Creating.

The generator's --check says the .tpl is what it would write; this says, reading the .tpl on its own,
that what is there means what the JSON says. Checks, in order:
  1. The keys of readyFalseConverging are exactly the JSON's Ready entries with status False and
     verdict `converging`, and every label is a non-empty string.
  2. Every Ready entry's (status, verdict) is one the portal's states express. The portal reads a
     composition by Ready's status and that one set (portal.compositionStateDefs): True is Ready,
     False is converging when in the set and NotReady otherwise, Unknown is pending, unknown or
     unreadable, never NotReady on its own. So True must be healthy, False converging or failed, and
     Unknown converging or unevaluated; core-provider reclassifying a reason outside that (an Unknown
     that is failed) fails here, so how the portal shows it is decided first.
  3. Every Ready reason portal.compositionStateDefs names (paused, unreadable, unknown) is in the JSON.
  4. portal.reasonActionsDefs (helm/portal/templates/_reason-actions.tpl, generated, included by no
     page yet), rendered by helm inside a single-quoted YAML string as a page would, and run by jq:
     each entry of both tables returns its top-level action, control, annotation and actors; a
     composition's ApiResolved=False with a reason authn sent falls back to the reasonFrom entry, and a
     condition the table does not list returns null.

Usage: test-ready-reasons.py [--json-dir DIR]   (default: fetch the JSON at the generator's pin)
Exit code = number of failures. Runs its controls first: a reclassified reason, an added converging
reason, an Unknown reason that is failed, a removed named reason and a definition missing a converging
key must each be caught.
"""
import copy
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
DEFS = os.path.join(REPO, 'helm', 'portal', 'templates', '_ready-reasons.tpl')
STATE = os.path.join(REPO, 'helm', 'portal', 'templates', '_composition-state.tpl')
JQ = os.environ.get('JQ', 'jq')

_spec = importlib.util.spec_from_file_location('gen_reason_defs', os.path.join(HERE, 'gen-reason-defs.py'))
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)

# What each Ready status can mean in the portal's states (check 2).
EXPRESSIBLE = {'True': {'healthy'}, 'False': {'converging', 'failed'}, 'Unknown': {'converging', 'unevaluated'}}


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def converging_keys(defs_text):
    m = re.search(r'def readyFalseConverging:\s*(\{.*?\});', defs_text)
    return json.loads(m.group(1)) if m else None


def named_reasons(state_text):
    """The Ready reasons portal.compositionStateDefs names (`.reason == "X"` / `.reason != "X"`)."""
    return sorted(set(re.findall(r'\.reason [!=]= "([A-Za-z]+)"', state_text)))


def check(doc, defs, named):
    out = []
    ready = [e for e in doc['reasons'] if e['condition'] == 'Ready']
    if not ready:
        return ['ready-reasons.json: no Ready entries (a table that lists nothing must not pass)']
    if defs is None:
        return out + ['_ready-reasons.tpl: no readyFalseConverging definition found']
    want = {e['reason'] for e in ready if e['status'] == 'False' and e['verdict'] == 'converging'}
    if set(defs) != want:
        out.append(f'readyFalseConverging keys {sorted(defs)} != the JSON\'s converging Ready=False reasons {sorted(want)}')
    out += [f'readyFalseConverging[{k!r}] maps to {v!r}, not a label' for k, v in defs.items() if not (isinstance(v, str) and v)]
    out += [f'Ready={e["status"]}/{e["reason"]} has verdict {e["verdict"]!r}, which the portal\'s states cannot show for '
            f'status {e["status"]} (they show {sorted(EXPRESSIBLE[e["status"]])}): decide how it reads, then change the portal'
            for e in ready if e['verdict'] not in EXPRESSIBLE[e['status']]]
    listed = {e['reason'] for e in ready}
    out += [f'portal.compositionStateDefs names Ready reason {r!r}, which ready-reasons.json does not list' for r in named if r not in listed]
    return out


# ---- portal.reasonActionsDefs, as a page would run it --------------------------------------------

def render_actions_defs():
    """The define's text, rendered by helm inside a single-quoted YAML string (as '${ ... }' is)."""
    with tempfile.TemporaryDirectory() as tmp:
        chart = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', os.path.join(REPO, 'helm', 'portal'), chart], check=True)
        meta = os.path.join(chart, 'Chart.yaml')
        text = read(meta)
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        with open(os.path.join(chart, 'templates', 'zz-reason-actions-probe.yaml'), 'w', encoding='utf-8') as fh:
            fh.write("apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: reason-actions-probe\n"
                     "data:\n  defs: '{{ include \"portal.reasonActionsDefs\" . }}'\n")
        proc = subprocess.run(['helm', 'template', 'c', chart, '--show-only', 'templates/zz-reason-actions-probe.yaml'],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template portal failed:\n{proc.stderr.strip()}')
        return yaml.safe_load(proc.stdout)['data']['defs']


def run_jq(program, data):
    proc = subprocess.run([JQ, '-c', program], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(f'jq failed: {proc.stderr.strip()}')
    return json.loads(proc.stdout)


def step(e):
    return {'action': e.get('action', ''), **{k: e[k] for k in ('control', 'annotation', 'actors') if e.get(k) is not None}}


def check_actions(defs, tables):
    out, cases = [], []
    for fn, name in (('compositionReasonAction', gen.COMPOSITION), ('cdReasonAction', gen.CD)):
        for e in tables[name]['reasons']:
            if e['reason'] is not None:
                cases.append((fn, f'{name} {e["condition"]}={e["status"]}/{e["reason"]}',
                              {'type': e['condition'], 'status': e['status'], 'reason': e['reason']}, step(e)))
            else:
                cases.append((fn, f'{name} {e["condition"]}={e["status"]} with a reason {e["reasonFrom"]} sent',
                              {'type': e['condition'], 'status': e['status'], 'reason': 'SomethingAuthnSent'}, step(e)))
        cases.append((fn, f'{name}: a condition the table does not list', {'type': 'NoSuchCondition2028', 'status': 'False', 'reason': 'X'}, None))
    if not any(e['reason'] is None for e in tables[gen.COMPOSITION]['reasons']):
        out.append('ready-reasons.json has no reasonFrom entry, so the fallback is untested')
    got = run_jq(f'{defs} [ .[] | .fn as $f | .c | if $f == "cd" then cdReasonAction else compositionReasonAction end ]',
                 [{'fn': 'cd' if fn == 'cdReasonAction' else 'composition', 'c': c} for fn, _, c, _ in cases])
    for (fn, label, _, want), have in zip(cases, got):
        if have != want:
            out.append(f'{fn}, {label}: got {have}, want {want}')
    return out


def controls(doc, named):
    """Each mutant of the JSON must be caught. The definitions they are checked against are built from
    the JSON itself, and each mutant picks an entry the JSON has, so the controls mean the same whatever
    the JSON lists and whether or not the .tpl agrees with it. When even the definitions built from the
    JSON are refused, the JSON is one the portal cannot express: the real check after this says how."""
    ready = [e for e in doc['reasons'] if e['condition'] == 'Ready']
    defs = {e['reason']: 'Label' for e in ready if e['status'] == 'False' and e['verdict'] == 'converging'}
    if check(doc, defs, named):
        return []
    problems = []

    def first(pred):
        return next((i for i, e in enumerate(doc['reasons']) if e['condition'] == 'Ready' and pred(e)), None)

    def mutant(label, at, fn):
        if at is None:
            problems.append(f'control: {label}: the JSON has no entry to mutate, so it tests nothing')
            return
        d = copy.deepcopy(doc)
        fn(d['reasons'], at)
        if not check(d, defs, named):
            problems.append(f'control: {label} was NOT caught')

    mutant('a converging Ready=False reason reclassified failed', first(lambda e: e['reason'] in defs),
           lambda rs, i: rs[i].update(verdict='failed'))
    mutant('a converging Ready=False reason added', 0,
           lambda rs, i: rs.append({'condition': 'Ready', 'status': 'False', 'reason': 'NewReason2028', 'verdict': 'converging'}))
    mutant('an Unknown reason reclassified failed', first(lambda e: e['status'] == 'Unknown'),
           lambda rs, i: rs[i].update(verdict='failed'))
    mutant('a reason the state rule names removed', first(lambda e: e['reason'] in named),
           lambda rs, i: rs.__setitem__(slice(None), [e for e in rs if not (e['condition'] == 'Ready' and e['reason'] == rs[i]['reason'])]))
    mutant('no Ready entries', 0, lambda rs, i: rs.__setitem__(slice(None), [e for e in rs if e['condition'] != 'Ready']))
    if not defs or not check(doc, dict(list(defs.items())[1:]), named):
        problems.append('control: a definition missing a converging key was NOT caught')
    if not named:
        problems.append('control: no Ready reason found named in portal.compositionStateDefs, so check 3 reads nothing')
    return problems


def main():
    args = sys.argv[1:]
    json_dir = args[args.index('--json-dir') + 1] if '--json-dir' in args else None
    where = json_dir or f'core-provider {gen.CORE_PROVIDER_REF[:12]} (the pin)'
    try:
        tables = gen.load_tables(json_dir)
    except ValueError as exc:
        print(f'[FAIL] {exc}')
        return 1
    doc = tables[gen.COMPOSITION]
    defs = converging_keys(read(DEFS))
    named = named_reasons(read(STATE))
    problems = controls(doc, named)
    actions_defs = render_actions_defs()
    broken = {**tables, gen.CD: copy.deepcopy(tables[gen.CD])}
    broken[gen.CD]['reasons'][0]['action'] = 'not what the defs say'
    if not check_actions(actions_defs, broken):
        problems.append('control: a reason-actions entry that differs from the JSON was NOT caught')
    if problems:
        for p in problems:
            print(f'[FAIL] {p}')
        return len(problems)
    print('[PASS] controls: a reclassified reason, an added converging reason, an Unknown reason that is failed, a removed '
          'named reason, an empty table, a missing converging key and a reason action that differs are caught')
    failures = check(doc, defs, named) + check_actions(actions_defs, tables)
    for f in failures:
        print(f'[FAIL] {f}')
    if not failures:
        ready = sum(1 for e in doc['reasons'] if e['condition'] == 'Ready')
        n = sum(len(t['reasons']) for t in tables.values())
        print(f'[PASS] {ready} Ready reasons match the reason JSON from {where}; converging Ready=False: '
              f'{", ".join(sorted(defs))}; named by the state rule: {", ".join(named)}; the reason actions of all {n} '
              'entries, the reasonFrom fallback and an unlisted condition read right through jq')
    return min(len(failures), 125)


if __name__ == '__main__':
    sys.exit(main())
