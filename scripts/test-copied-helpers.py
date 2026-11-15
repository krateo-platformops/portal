#!/usr/bin/env python3
"""Copied chart helpers stay byte-identical to their source.

When the pages became their own charts (#261), helpers the pages call were COPIED from
helm/portal/templates into each page chart that needs them: a `define` lives in the chart that
declares it, so a page chart cannot include portal.* from the portal chart. Each copy starts with a
`{{/* COPIED FROM <source path> ... */}}` header saying so. Nothing checked that the code under
that header still matched the source, so a fix made in one copy would silently leave the others
behind. Installer's sweep of the 1.8.118 charts found these files hashing differently per chart;
the code was identical and only the headers differed, but no gate would have said so.

The rules, per page chart's .tpl:
  1. A file with the header names an existing source, with the same file name, that is not itself
     a copy; its content after the header equals the source's byte for byte.
  2. A file that defines any template name a copied source defines must carry the header: a copy
     without the marker would escape rule 1.

Discovery is by the header, so a new copy is guarded the day it is made, with no list to keep.
The helpers with their own drift tests (_facets.tpl in test-facets.py, and the others named in their
tests) are a different family: they have no portal source, and this test leaves them alone.

Usage: test-copied-helpers.py [root]. Exit code = number of failures. Runs its own controls first:
a one-byte edit in a copy and a copy stripped of its header must both be caught.
"""
import difflib
import glob
import os
import re
import shutil
import sys
import tempfile

HEADER = re.compile(r'\A\s*\{\{/\*\s*COPIED FROM (\S+?)\s(.*?)\*/\}\}\s*', re.S)
DEFINE = re.compile(r'\{\{-?\s*define\s+"([^"]+)"')


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def check(root):
    """Returns a list of failures for the tree at root."""
    failures = []
    tpls = sorted(glob.glob(os.path.join(root, 'helm', '*', 'templates', '*.tpl')))
    copies = {}
    for path in tpls:
        m = HEADER.match(read(path))
        if m:
            copies[path] = m
    sources = {}
    for path, m in copies.items():
        rel = os.path.relpath(path, root)
        src_rel = m.group(1).rstrip('.,;:')
        src = os.path.join(root, src_rel)
        if not os.path.isfile(src):
            failures.append(f'{rel}: COPIED FROM {src_rel}, which does not exist')
            continue
        if os.path.basename(src) != os.path.basename(path):
            failures.append(f'{rel}: COPIED FROM {src_rel}, a different file name')
            continue
        if src in copies:
            failures.append(f'{rel}: COPIED FROM {src_rel}, which is itself a copy; name the original')
            continue
        source = read(src)
        body = read(path)[m.end():]
        if body != source:
            diff = list(difflib.unified_diff(source.splitlines(), body.splitlines(), src_rel, rel, lineterm='', n=0))
            failures.append(f'{rel}: differs from {src_rel} under its header:\n      ' + '\n      '.join(diff[2:12]))
        sources[src] = set(DEFINE.findall(source))
    for path in tpls:
        if path in copies or path in sources:
            continue
        shared = set(DEFINE.findall(read(path)))
        for src, names in sources.items():
            overlap = sorted(shared & names)
            if overlap:
                failures.append(f'{os.path.relpath(path, root)}: defines {", ".join(overlap)} like {os.path.relpath(src, root)} '
                                f'but has no COPIED FROM header (a copy without the marker is unguarded)')
    return failures, len(copies)


def controls(root):
    """The check must catch a one-byte edit in a copy and a copy that lost its header."""
    problems = []
    failures, n = check(root)
    if failures:
        return problems  # the real run reports them; controls need a clean tree to mean anything
    if n == 0:
        return ['no COPIED FROM helpers found: a check that reads nothing must not pass']
    copy = next(p for p in sorted(glob.glob(os.path.join(root, 'helm', '*', 'templates', '*.tpl'))) if HEADER.match(read(p)))
    rel = os.path.relpath(copy, root)
    for name, mutate in (('a one-byte edit under the header', lambda s: s[:-2] + ('x' if s[-2] != 'x' else 'y') + s[-1:]),
                         ('the header removed', lambda s: HEADER.sub('', s, count=1))):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copytree(os.path.join(root, 'helm'), os.path.join(tmp, 'helm'))
            target = os.path.join(tmp, rel)
            mutated = mutate(read(target))  # read before opening for write, which truncates
            with open(target, 'w', encoding='utf-8') as fh:
                fh.write(mutated)
            caught, _ = check(tmp)
            if not caught:
                problems.append(f'control: {name} in {rel} was NOT caught')
            else:
                print(f'[PASS] control: {name} in {rel} is caught')
    return problems


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    root = os.path.abspath(root)
    problems = controls(root)
    failures, n = check(root)
    for f in problems + failures:
        print(f'[FAIL] {f}')
    if not problems and not failures:
        print(f'[PASS] {n} copied helper(s) byte-identical to their source under the COPIED FROM header')
    return len(problems) + len(failures)


if __name__ == '__main__':
    sys.exit(main())
