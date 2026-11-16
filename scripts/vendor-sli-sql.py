#!/usr/bin/env python3
"""Vendor the SLI statements the observability page's SLOs tab runs, from their single source.

The SLI definitions live in krateo-platformops/observability, ops/slo-alert/sli-span-metrics.sql:
the cmp-slo-* alerts, the HyperDX dashboard and this tab all read them, and a reader that needs a
different number changes THAT file, never its own copy. The portal keeps a byte-exact copy of each
statement in helm/observability-page/files/slo/, cut from the file at the commit in SOURCE.

  vendor-sli-sql.py            re-cut the statements from the pinned commit and write them
  vendor-sli-sql.py --check    fail if the vendored files differ from that cut (CI)
  vendor-sli-sql.py --pin SHA  move the pin to SHA, then re-cut

THE CUT. A statement runs from its first line that is neither blank nor a `--` comment to the line
that ends with `;`, which is dropped. Comments between statements (each statement's heading) are
not part of it; a comment INSIDE a statement is, so the text sent is the text reviewed.
"""
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(os.path.dirname(HERE), 'helm', 'observability-page', 'files', 'slo')
SOURCE = os.path.join(DIR, 'SOURCE')
REPO, PATH = 'krateo-platformops/observability', 'ops/slo-alert/sli-span-metrics.sql'
# One name per statement, in file order. A statement added upstream fails the cut until it is named
# here, so the tab never silently gains or loses one.
NAMES = ['1-availability', '2-api-latency', '3-ui-latency', '4-widget-fetch', '5-burn']


def pinned():
    for line in open(SOURCE, encoding='utf-8'):
        if line.startswith('commit: '):
            return line.split(':', 1)[1].strip()
    raise SystemExit(f'{SOURCE}: no "commit:" line')


def fetch(sha):
    url = f'https://raw.githubusercontent.com/{REPO}/{sha}/{PATH}'
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode('utf-8')


def cut(text):
    out, buf = [], []
    for line in text.splitlines():
        if not buf and (not line.strip() or line.lstrip().startswith('--')):
            continue
        buf.append(line)
        if line.rstrip().endswith(';'):
            buf[-1] = buf[-1].rstrip()[:-1].rstrip()
            out.append('\n'.join(buf) + '\n')
            buf = []
    if buf:
        raise SystemExit('the file ends inside a statement (no closing `;`)')
    return out


def main(argv):
    if '--pin' in argv:
        sha = argv[argv.index('--pin') + 1]
        with open(SOURCE, 'w', encoding='utf-8') as fh:
            fh.write(f'# Cut by scripts/vendor-sli-sql.py; edit only through it.\n'
                     f'repo: {REPO}\npath: {PATH}\ncommit: {sha}\n')
    sha = pinned()
    statements = cut(fetch(sha))
    if len(statements) != len(NAMES):
        raise SystemExit(f'{PATH}@{sha[:12]} has {len(statements)} statements; this tab names {len(NAMES)} '
                         f'({", ".join(NAMES)}). Name the new one in NAMES and give it a tile, or drop it there.')
    problems = []
    for name, stmt in zip(NAMES, statements):
        path = os.path.join(DIR, name + '.sql')
        if '--check' in argv:
            have = open(path, encoding='utf-8').read() if os.path.exists(path) else None
            if have != stmt:
                problems.append(f'{name}.sql differs from {PATH}@{sha[:12]}: run scripts/vendor-sli-sql.py')
        else:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(stmt)
    for p in problems:
        print(f'[FAIL] {p}')
    if not problems:
        print(f'[PASS] {len(NAMES)} statements {"match" if "--check" in argv else "written from"} {PATH}@{sha[:12]}')
    return len(problems)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
