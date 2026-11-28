#!/usr/bin/env python3
"""Generates the portal's reason definitions from core-provider's reason JSON.

core-provider publishes the complete, closed list of condition reasons it writes, as JSON generated
from its code (core-provider#667, #679): docs/ready-reasons.json for a composition and
docs/cd-ready-reasons.json for a CompositionDefinition. One entry per (condition, status, reason),
each with `verdict` (how a parent's rollup reads it: healthy, converging, failed or unevaluated; null
on a condition the rollup does not read) and the step a person takes (`action`, `control`,
`annotation`, `actors`). The top level of an entry is authoritative for on-screen text; `cases`
carry per-writer detail only.

This script writes, from the JSON at CORE_PROVIDER_REF:
  helm/portal/templates/_ready-reasons.tpl      the `def readyFalseConverging: {...};` map in
                                                portal.readyReasonDefs: the Ready entries with
                                                status False and verdict converging, each mapped to
                                                its pill label (PILL_LABELS, the portal's own words);
  helm/portal/templates/_cd-ready-reasons.tpl   the `def cdReadyVerdict: {...};` map in
                                                portal.cdReadyDefs: every Ready reason to its verdict;
  helm/portal/templates/_reason-actions.tpl     the whole of portal.reasonActionsDefs: each
                                                (condition, status, reason) to {action, control,
                                                annotation, actors}, for both tables.
Only those maps are generated; the rest of each .tpl (its comment and the jq that reads the map) is
hand-written and kept as it is. Every COPIED FROM copy of a source (scripts/test-copied-helpers.py)
is rewritten as its own header followed by the new source, so the copies cannot drift.

THE PIN is CORE_PROVIDER_REF below, the one place it is set: the workflow fetches the JSON with
`--fetch`, which reads it from here. Moving it is the decision to take core-provider's newer reasons:
change it, run this script, and review what the regenerated maps change on screen.

Usage:
  gen-reason-defs.py [--json-dir DIR]          write the .tpl files (default: fetch at the pin)
  gen-reason-defs.py --check [--json-dir DIR]  write nothing; exit 1 when any .tpl differs from what
                                               this would write (runs its own controls first)
  gen-reason-defs.py --fetch DIR [--ref REF]   download the two JSON files at the pin (or REF) to DIR

It refuses a schemaVersion it does not know, and a table it cannot read, rather than writing from it.
"""
import argparse
import difflib
import glob
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request

# core-provider 2.13.137: the first tag that carries the SchemaDefaultsMatchValues rows.
CORE_PROVIDER_REF = '2.13.137'
RAW = 'https://raw.githubusercontent.com/krateo-platformops/core-provider/{ref}/docs/{name}.json'

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
COMPOSITION, CD = 'ready-reasons', 'cd-ready-reasons'
TABLES = (COMPOSITION, CD)
KNOWN_SCHEMA_VERSIONS = (1,)
VERDICTS = ('healthy', 'converging', 'failed', 'unevaluated')
STATUSES = ('True', 'False', 'Unknown')

# The portal's words for a converging Ready=False reason on a pill (Diego, 2026-11-15): Creating and
# Deleting print their own reason; HelmOperationInProgress and PendingHelmOperation, both
# timer-bounded helm operations, read Pending. The generator refuses a converging reason without a
# label, and a label for a reason that is not one, so a reason core-provider adds or reclassifies is
# decided here before it reaches a page.
PILL_LABELS = {
    'Creating': 'Creating',
    'Deleting': 'Deleting',
    'HelmOperationInProgress': 'Pending',
    'PendingHelmOperation': 'Pending',
}

READY_TPL = os.path.join('helm', 'portal', 'templates', '_ready-reasons.tpl')
CD_TPL = os.path.join('helm', 'portal', 'templates', '_cd-ready-reasons.tpl')
ACTIONS_TPL = os.path.join('helm', 'portal', 'templates', '_reason-actions.tpl')
READY_SPAN = re.compile(r'def readyFalseConverging: \{[^}]*\};')
CD_SPAN = re.compile(r'def cdReadyVerdict: \{[^}]*\};')

_spec = importlib.util.spec_from_file_location('copied_helpers', os.path.join(HERE, 'test-copied-helpers.py'))
copied_helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(copied_helpers)


# ---- reading the JSON -----------------------------------------------------------------------------

def validate(doc, name):
    """The table as read, or ValueError: a table this cannot read must not be generated from."""
    if not isinstance(doc, dict):
        raise ValueError(f'{name}.json: not a JSON object')
    if doc.get('schemaVersion') not in KNOWN_SCHEMA_VERSIONS:
        raise ValueError(f'{name}.json: schemaVersion {doc.get("schemaVersion")!r} is not one this generator knows '
                         f'({", ".join(map(str, KNOWN_SCHEMA_VERSIONS))}); read what changed, then teach it')
    if doc.get('table') != name:
        raise ValueError(f'{name}.json: table is {doc.get("table")!r}, not {name!r}')
    reasons = doc.get('reasons')
    if not isinstance(reasons, list) or not reasons:
        raise ValueError(f'{name}.json: no reasons (a table that lists nothing must not pass)')
    seen = set()
    for e in reasons:
        key = (e.get('condition'), e.get('status'), e.get('reason'))
        where = f'{name}.json {key[0]}/{key[1]}/{key[2]}'
        if not isinstance(key[0], str) or key[1] not in STATUSES:
            raise ValueError(f'{where}: no condition, or a status that is not True, False or Unknown')
        if key[2] is None and not e.get('reasonFrom'):
            raise ValueError(f'{where}: no reason and no reasonFrom')
        if key in seen:
            raise ValueError(f'{where}: listed twice')
        seen.add(key)
        verdict = e.get('verdict')
        if key[0] == 'Ready' and verdict not in VERDICTS:
            raise ValueError(f'{where}: verdict {verdict!r} is not one of {", ".join(VERDICTS)}')
        if key[0] != 'Ready' and verdict is not None:
            raise ValueError(f'{where}: a verdict on a condition the rollup does not read')
        if verdict == 'unevaluated' and e.get('rollsUpAs') not in VERDICTS:
            raise ValueError(f'{where}: unevaluated without the verdict it rolls up as')
        if not isinstance(e.get('action', ''), str):
            raise ValueError(f'{where}: action is not a string')
    return doc


def fetch(dest, ref=CORE_PROVIDER_REF):
    os.makedirs(dest, exist_ok=True)
    for name in TABLES:
        url = RAW.format(ref=ref, name=name)
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = resp.read()
        with open(os.path.join(dest, f'{name}.json'), 'wb') as fh:
            fh.write(body)
        print(f'fetched {url}')


def load_tables(json_dir=None):
    """{table name: validated doc}, from json_dir or fetched at the pin."""
    if json_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            fetch(tmp)
            return load_tables(tmp)
    out = {}
    for name in TABLES:
        with open(os.path.join(json_dir, f'{name}.json'), encoding='utf-8') as fh:
            out[name] = validate(json.load(fh), name)
    return out


def ready_entries(doc):
    return [e for e in doc['reasons'] if e['condition'] == 'Ready']


# ---- writing jq -----------------------------------------------------------------------------------

def jq(value):
    """A jq literal on one line, safe to open a jq program in a single-quoted YAML string and inside a
    Helm define: within strings, ' # { } are written as \\u escapes, so the text has no single quote,
    no comment marker and no template delimiter."""
    if isinstance(value, dict):
        return '{' + ', '.join(f'{jq(k)}: {jq(v)}' for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ', '.join(jq(v) for v in value) + ']'
    if isinstance(value, str):
        s = json.dumps(value, ensure_ascii=False)
        for ch in "'#{}":
            s = s.replace(ch, '\\u%04x' % ord(ch))
        return s
    return json.dumps(value)


def ready_false_converging(doc):
    """readyFalseConverging, sorted by reason (it is a set): {reason: pill label}."""
    want = sorted({e['reason'] for e in ready_entries(doc) if e['status'] == 'False' and e['verdict'] == 'converging'})
    unlabelled = [r for r in want if r not in PILL_LABELS]
    if unlabelled:
        raise ValueError(f'core-provider\'s Ready=False reason(s) {", ".join(unlabelled)} are converging and have no '
                         'pill label: decide what the pill prints, then add it to PILL_LABELS')
    stray = sorted(set(PILL_LABELS) - set(want))
    if stray:
        raise ValueError(f'PILL_LABELS names {", ".join(stray)}, which core-provider does not list as a converging '
                         'Ready=False reason: decide how the portal shows it now, then drop the label')
    return {r: PILL_LABELS[r] for r in want}


def cd_ready_verdict(doc):
    """cdReadyVerdict, in the table's order: {Ready reason: verdict}. Keyed by reason alone."""
    out = {}
    for e in ready_entries(doc):
        if e['reason'] in out and out[e['reason']] != e['verdict']:
            raise ValueError(f'Ready/{e["reason"]} carries two verdicts ({out[e["reason"]]}, {e["verdict"]}) for two '
                             'statuses: cdReadyVerdict is keyed by reason alone and cannot express it')
        out[e['reason']] = e['verdict']
    return out


def reason_actions(doc):
    """({condition: {status: {reason: step}}}, {condition: {status: step}} for the reasonFrom entries)."""
    listed, fallback = {}, {}
    for e in doc['reasons']:
        step = {'action': e.get('action', '')}
        for k in ('control', 'annotation', 'actors'):
            if e.get(k) is not None:
                step[k] = e[k]
        if e['reason'] is None:
            fallback.setdefault(e['condition'], {})[e['status']] = step
        else:
            listed.setdefault(e['condition'], {}).setdefault(e['status'], {})[e['reason']] = step
    return listed, fallback


ACTIONS_HEADER = """{{/*
  portal.reasonActionsDefs — GENERATED by scripts/gen-reason-defs.py from core-provider's
  docs/ready-reasons.json (a composition) and docs/cd-ready-reasons.json (a CompositionDefinition) at
  the ref pinned there (CORE_PROVIDER_REF). Do not edit: change the pin and run the script. Its --check
  (the ready-reasons workflow) fails when this file is not what it would write.

  What a person does about a condition's reason, as core-provider words it. compositionReasonAction
  and cdReasonAction: input a condition ({type, status, reason}); output {action, control?,
  annotation?, actors?}, or null for a condition, status and reason the table does not list. action is
  the sentence for the screen ("" when there is nothing to do); control the on-screen label of the
  portal control that does the step; annotation {name, op, value?} the annotation it changes, for
  Details and never in the sentence; actors who must act (you, admin, maintainers). These are the
  entry's top-level fields, which core-provider declares authoritative for on-screen text. A
  reasonFrom entry (a composition's ApiResolved=False, whose reasons are authn's own) is the fallback
  for its condition and status only, when no listed reason matches.

  blueprints-page includes its copy: blueprint-detail's schemaDefaults takes the SchemaDefaultsDiffer
  sentence from it (portal#576). Defs only, on ONE line with no single
  quote, no `#` and no template delimiter, so it can open a jq program anywhere.
*/}}
"""


def actions_tpl(tables):
    defs = []
    for prefix, name in (('composition', COMPOSITION), ('cd', CD)):
        listed, fallback = reason_actions(tables[name])
        defs.append(f'def {prefix}ReasonActions: {jq(listed)};')
        defs.append(f'def {prefix}ReasonActionFrom: {jq(fallback)};')
        defs.append(f'def {prefix}ReasonAction: . as $c | {prefix}ReasonActions[$c.type // ""][$c.status // ""]'
                    f'[$c.reason // ""] // {prefix}ReasonActionFrom[$c.type // ""][$c.status // ""];')
    return ACTIONS_HEADER + '{{- define "portal.reasonActionsDefs" -}}\n' + ' '.join(defs) + '\n{{- end -}}\n'


# ---- the files ------------------------------------------------------------------------------------

def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def replace_span(text, span, new, where):
    found = span.findall(text)
    if len(found) != 1:
        raise ValueError(f'{where}: {len(found)} matches for {span.pattern!r}, want exactly one')
    return span.sub(lambda _: new, text)


def copies_of(root, source_rel):
    """{copy path: its COPIED FROM header}, for every page chart's copy of source_rel."""
    out = {}
    for path in sorted(glob.glob(os.path.join(root, 'helm', '*', 'templates', os.path.basename(source_rel)))):
        m = copied_helpers.HEADER.match(read(path))
        if m and os.path.normpath(m.group(1).rstrip('.,;:')) == os.path.normpath(source_rel):
            out[path] = read(path)[:m.end()]
    return out


def render(root, tables):
    """{absolute path: content this script writes}."""
    sources = {
        READY_TPL: replace_span(read(os.path.join(root, READY_TPL)), READY_SPAN,
                                f'def readyFalseConverging: {jq(ready_false_converging(tables[COMPOSITION]))};', READY_TPL),
        CD_TPL: replace_span(read(os.path.join(root, CD_TPL)), CD_SPAN,
                             f'def cdReadyVerdict: {jq(cd_ready_verdict(tables[CD]))};', CD_TPL),
        ACTIONS_TPL: actions_tpl(tables),
    }
    out = {}
    for rel, content in sources.items():
        out[os.path.join(root, rel)] = content
        for path, header in copies_of(root, rel).items():
            out[path] = header + content
    return out


def check(root, tables):
    """[(path, diff lines)] for every file that is not what render() writes."""
    out = []
    for path, want in render(root, tables).items():
        have = read(path) if os.path.isfile(path) else ''
        if have != want:
            rel = os.path.relpath(path, root)
            out.append((rel, list(difflib.unified_diff(have.splitlines(), want.splitlines(), rel, f'{rel} (generated)',
                                                       lineterm='', n=0))[2:14]))
    out = [(rel, [l if len(l) <= 240 else l[:240] + ' ...' for l in lines]) for rel, lines in out]
    return out


def controls(root, tables):
    """--check must catch a hand-edited map, in a source and in a copy only, and a missing file; the
    reader must refuse an unknown schemaVersion. Run on scratch copies of helm/, each regenerated first,
    so they mean the same whatever state the working tree is in."""
    problems = []

    def scratch(tmp):
        shutil.copytree(os.path.join(root, 'helm'), os.path.join(tmp, 'helm'))
        for path, content in render(tmp, tables).items():
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(content)

    with tempfile.TemporaryDirectory() as tmp:
        scratch(tmp)
        if check(tmp, tables):
            return ['control: a freshly generated tree does not pass --check']
    # Each edit adds a key by hand, so it applies whatever the JSON lists.
    edits = [('a hand-edited readyFalseConverging', READY_TPL, 'def readyFalseConverging: {', 'def readyFalseConverging: {"HandEdited": "Pending", '),
             ('a hand-edited cdReadyVerdict', CD_TPL, 'def cdReadyVerdict: {', 'def cdReadyVerdict: {"HandEdited": "failed", ')]
    copy = next(iter(copies_of(root, READY_TPL)), None)
    if copy:
        edits.append(('a copy edited and its source not', os.path.relpath(copy, root), 'def readyFalseConverging: {',
                      'def readyFalseConverging: {"HandEdited": "Pending", '))
    for label, rel, old, new in edits:
        with tempfile.TemporaryDirectory() as tmp:
            scratch(tmp)
            target = os.path.join(tmp, rel)
            text = read(target)
            if old not in text:
                problems.append(f'control: {label}: {old!r} is not in {rel}, so the control tests nothing')
                continue
            with open(target, 'w', encoding='utf-8') as fh:
                fh.write(text.replace(old, new, 1))
            if not any(r == rel for r, _ in check(tmp, tables)):
                problems.append(f'control: {label} ({rel}) was NOT caught')
    with tempfile.TemporaryDirectory() as tmp:
        scratch(tmp)
        os.unlink(os.path.join(tmp, ACTIONS_TPL))
        if not any(r == ACTIONS_TPL for r, _ in check(tmp, tables)):
            problems.append('control: a missing _reason-actions.tpl was NOT caught')
    for label, doc in (('an unknown schemaVersion', {**tables[CD], 'schemaVersion': 2}),
                       ('a table with no reasons', {**tables[CD], 'reasons': []})):
        try:
            validate(doc, CD)
            problems.append(f'control: {label} was read instead of refused')
        except ValueError:
            pass
    return problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    ap.add_argument('--json-dir', help='read ready-reasons.json and cd-ready-reasons.json from DIR (default: fetch at the pin)')
    ap.add_argument('--check', action='store_true', help='write nothing; fail when a .tpl differs from what this would write')
    ap.add_argument('--fetch', metavar='DIR', help='download the two JSON files at the pin to DIR and exit')
    ap.add_argument('--ref', default=CORE_PROVIDER_REF, help='with --fetch: the core-provider ref (default: the pin)')
    args = ap.parse_args()
    if args.fetch:
        fetch(args.fetch, args.ref)
        return 0
    try:
        tables = load_tables(args.json_dir)
        if args.check:
            problems = controls(REPO, tables)
            for p in problems:
                print(f'[FAIL] {p}')
            if problems:
                return 1
            print('[PASS] controls: a hand-edited map in a source and in a copy only, a missing file, an unknown '
                  'schemaVersion and an empty table are caught')
            diffs = check(REPO, tables)
            for rel, lines in diffs:
                print(f'[FAIL] {rel} is not what scripts/gen-reason-defs.py writes from the reason JSON:\n      '
                      + '\n      '.join(lines))
            if diffs:
                print('Run python3 scripts/gen-reason-defs.py and review the change; never edit the maps by hand.')
                return 1
            where = args.json_dir or f'core-provider {CORE_PROVIDER_REF[:12]} (the pin)'
            print(f'[PASS] {len(render(REPO, tables))} generated .tpl files match the reason JSON from {where}')
            return 0
        for path, content in render(REPO, tables).items():
            if not os.path.isfile(path) or read(path) != content:
                with open(path, 'w', encoding='utf-8') as fh:
                    fh.write(content)
                print(f'wrote {os.path.relpath(path, REPO)}')
        return 0
    except ValueError as exc:
        print(f'[FAIL] {exc}')
        return 1


if __name__ == '__main__':
    sys.exit(main())
