#!/usr/bin/env python3
"""The portal's Ready-reason classification matches core-provider's generated table.

core-provider generates the complete, closed list of a composition's Ready reasons
(docs/ready-reasons.md, core-provider#515), each with its status and how a parent's rollup reads it
(`parent verdict`). The portal classifies reasons in several charts from ONE definition,
portal.readyReasonDefs (helm/portal/templates/_ready-reasons.tpl, copied byte-identical into the page
charts that need it). Two reasons sat red NotReady for exactly the lack of this check:
HelmOperationInProgress and Deleting are converging, and the portal only knew Creating.

Checks, in order:
  1. ready-reasons.yaml's `table` is the table core-provider publishes now, row for row:
     (reason, status, writer, parent verdict), columns found by header name. A reason added, removed
     or reclassified fails here,
     with the difference, so the portal's handling is decided before the snapshot is updated.
  2. The Ready=False rows whose verdict is `converging` are exactly the keys of readyFalseConverging
     in _ready-reasons.tpl, and every label it maps to is a non-empty string.

Usage: test-ready-reasons.py [--table FILE]   (default: fetch the `source` URL in ready-reasons.yaml)
Exit code = number of failures. Runs its controls first: an added reason, a reclassified reason and a
definition missing a converging key must each be caught.
"""
import json
import os
import re
import sys
import urllib.request

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOT = os.path.join(REPO, 'ready-reasons.yaml')
DEFS = os.path.join(REPO, 'helm', 'portal', 'templates', '_ready-reasons.tpl')
COLUMNS = ('reason', 'status', 'writer', 'parent verdict')   # read BY HEADER NAME, never by position


def cells(line):
    return [c.strip() for c in line.strip().strip('|').split('|')]


def parse_table(markdown):
    """{(reason, writer): (status, verdict)} from core-provider's markdown table.

    Columns are found by their HEADER, so a column added anywhere (core-provider#523 added
    `expected to clear` after `parent verdict`) changes nothing here. A header that lacks one of
    COLUMNS raises: a table this cannot read must fail the gate, not parse as empty."""
    lines = markdown.splitlines()
    # The header is the table row that NAMES a `reason` column, wherever it stands: core-provider#528
    # put a `condition` column before it, and a header test on the first cell refused the table.
    head = next((i for i, l in enumerate(lines) if l.lstrip().startswith('|') and 'reason' in cells(l)), None)
    if head is None:
        raise ValueError('no table header with a `reason` column')
    names = cells(lines[head])
    missing = [c for c in COLUMNS if c not in names]
    if missing:
        raise ValueError(f'table header {names} lacks {missing}')
    at = {c: names.index(c) for c in COLUMNS}
    # Since core-provider#528 the table lists EVERY condition a composition carries (ApiResolved,
    # ChartRenderable, RBACComplete, Ready, Synced), with a `condition` column. The portal classifies
    # Ready reasons, so only Ready rows count; a table without the column is all Ready (the old shape).
    cond = names.index('condition') if 'condition' in names else None
    rows = {}
    for line in lines[head + 2:]:                      # skip the |---| separator
        if not line.lstrip().startswith('|'):
            break
        row = cells(line)
        if len(row) != len(names):
            raise ValueError(f'a row has {len(row)} cells, the header {len(names)}: {line[:80]}')
        if cond is not None and row[cond].strip('`') != 'Ready':
            continue
        reason = row[at['reason']].strip('`')
        rows[(reason, row[at['writer']])] = (row[at['status']], row[at['parent verdict']])
    return rows


def snapshot_rows(doc):
    return {(r['reason'], r['writer']): (str(r['status']), r['verdict']) for r in doc['table']}


def converging_keys(defs_text):
    m = re.search(r'def readyFalseConverging:\s*(\{.*?\});', defs_text)
    if not m:
        return None
    return json.loads(m.group(1))


def check(live, snap, defs):
    out = []
    if not live:
        return ['core-provider table: no rows parsed (a parse that reads nothing must not pass)']
    for key in sorted(set(live) | set(snap)):
        if key not in snap:
            out.append(f'core-provider added {key[0]} ({key[1]}): {live[key]}; decide how the portal shows it, then add it to ready-reasons.yaml')
        elif key not in live:
            out.append(f'core-provider removed {key[0]} ({key[1]}); drop it from the portal and ready-reasons.yaml')
        elif live[key] != snap[key]:
            out.append(f'core-provider reclassified {key[0]} ({key[1]}): {snap[key]} -> {live[key]}')
    if defs is None:
        return out + ['_ready-reasons.tpl: no readyFalseConverging definition found']
    want = {reason for (reason, _), (status, verdict) in snap.items() if status == 'False' and verdict == 'converging'}
    if set(defs) != want:
        out.append(f'readyFalseConverging keys {sorted(defs)} != the converging Ready=False reasons {sorted(want)}')
    out += [f'readyFalseConverging[{k!r}] maps to {v!r}, not a label' for k, v in defs.items() if not (isinstance(v, str) and v)]
    return out


TABLE_CONTROL = """intro
| reason | status | writer | parent verdict | written by |
|---|---|---|---|---|
| `Deleting` | False | unstructured-runtime | converging | Delete is in progress |
"""


def parser_controls():
    """The parser reads columns by name: an inserted column changes nothing, a missing one fails."""
    problems = []
    want = {('Deleting', 'unstructured-runtime'): ('False', 'converging')}
    if parse_table(TABLE_CONTROL) != want:
        problems.append('control: the plain table did not parse')
    inserted = TABLE_CONTROL.replace('| reason | status |', '| reason | since | status |').replace(
        '|---|---|', '|---|---|---|', 1).replace('| `Deleting` | False |', '| `Deleting` | 2.13.0 | False |')
    if parse_table(inserted) != want:
        problems.append('control: a column inserted before `status` changed the parse')
    # core-provider#528's shape: a `condition` column FIRST, and rows for other conditions too.
    by_condition = ('intro\n| condition | reason | status | writer | parent verdict | written by |\n|---|---|---|---|---|---|\n'
                    '| Ready | `Deleting` | False | unstructured-runtime | converging | Delete is in progress |\n'
                    '| Synced | `ReconcileError` | False | unstructured-runtime | failed | the reconcile failed |\n')
    if parse_table(by_condition) != want:
        problems.append('control: a leading `condition` column, or a non-Ready row, changed the parse')
    for label, broken in (('a header without `parent verdict`', TABLE_CONTROL.replace('parent verdict', 'verdict')),
                          ('no table at all', 'intro only')):
        try:
            parse_table(broken)
            problems.append(f'control: {label} was parsed instead of refused')
        except ValueError:
            pass
    return problems


def controls(snap, defs):
    problems = parser_controls()
    if check(dict(snap), snap, defs):
        problems.append('control: the snapshot against itself was refused')
    added = dict(snap)
    added[('NewReason2028', 'composition-dynamic-controller')] = ('False', 'converging')
    if not check(added, snap, defs):
        problems.append('control: an added reason was NOT caught')
    reclassified = dict(snap)
    reclassified[('Deleting', 'unstructured-runtime')] = ('False', 'failed')
    if not check(reclassified, snap, defs):
        problems.append('control: a reclassified reason was NOT caught')
    missing = {k: v for k, v in defs.items() if k != 'Deleting'}
    if not check(dict(snap), snap, missing):
        problems.append('control: a definition missing a converging key was NOT caught')
    if check({}, snap, defs) == []:
        problems.append('control: an empty table was accepted')
    return problems


def main():
    with open(SNAPSHOT, encoding='utf-8') as fh:
        doc = yaml.safe_load(fh)
    snap = snapshot_rows(doc)
    with open(DEFS, encoding='utf-8') as fh:
        defs = converging_keys(fh.read())
    problems = controls(snap, defs or {})
    if problems:
        for p in problems:
            print(f'[FAIL] {p}')
        return len(problems)
    print('[PASS] controls: an added reason, a reclassified reason, a missing converging key and an empty table are caught; '
          'columns are read by header (an inserted column, or a leading condition column with other conditions\' rows, '
          'parses the same; a missing column is refused)')
    args = sys.argv[1:]
    if '--table' in args:
        with open(args[args.index('--table') + 1], encoding='utf-8') as fh:
            markdown, where = fh.read(), args[args.index('--table') + 1]
    else:
        with urllib.request.urlopen(doc['source'], timeout=30) as resp:
            markdown, where = resp.read().decode('utf-8'), doc['source']
    try:
        live = parse_table(markdown)
    except ValueError as exc:
        print(f'[FAIL] core-provider table at {where}: {exc}')
        return 1
    failures = check(live, snap, defs)
    for f in failures:
        print(f'[FAIL] {f}')
    if not failures:
        print(f'[PASS] {len(snap)} Ready reasons match {where}; converging Ready=False: {", ".join(sorted(defs))}')
    return min(len(failures), 125)


if __name__ == '__main__':
    sys.exit(main())
