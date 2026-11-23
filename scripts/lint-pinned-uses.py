#!/usr/bin/env python3
"""lint-pinned-uses — every `uses:` in .github/workflows names a full commit SHA, or a local ./ path.

WHY (Diego, 2026-11-23, extending installer #778 of 2026-11-09). krateo-platformops/.github made
`index_channel` a required input of the shared release-oci at 21:37Z on 2026-11-22. Callers on `@main`
had their release path changed with no commit of their own: frontend 1.8.70 published its image and
none of its charts, behind a green release-tag. A tag (`@v5`) moves the same way. So each `uses:` is
pinned to a 40-hex commit, with the ref it was taken from as a trailing comment
(`# v5 @ 2026-11-23`), and an upgrade is a deliberate change here.

Usage: lint-pinned-uses.py [workflows-dir]. Exit code = findings. A control runs first: a synthetic
unpinned line must be found, or the check is refused as one that cannot see.
"""
import os
import re
import sys

USES = re.compile(r'^\s*-?\s*uses:\s*([^\s#]+)')
PINNED = re.compile(r'@[0-9a-f]{40}$')


def findings(lines, name):
    out = []
    for number, line in enumerate(lines, 1):
        match = USES.match(line)
        if not match:
            continue
        target = match.group(1).strip('\'"')
        if target.startswith('./') or PINNED.search(target):
            continue
        out.append(f'{name}:{number}: `uses: {target}` is not pinned to a commit SHA')
    return out


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.github', 'workflows')
    control = findings(['    - uses: actions/checkout@v5', '    - uses: org/repo/.github/workflows/x.yaml@main', '    - uses: ./local'], 'control')
    if len(control) != 2:
        print(f'  lint-pinned-uses: CONTROL FAILED, it found {len(control)} of 2 unpinned lines; refusing to report a pass')
        return 1
    found, checked = [], 0
    for name in sorted(os.listdir(root)):
        if not name.endswith(('.yml', '.yaml')):
            continue
        with open(os.path.join(root, name), encoding='utf-8') as fh:
            lines = fh.read().splitlines()
        checked += sum(1 for line in lines if USES.match(line))
        found += findings(lines, name)
    for f in found:
        print(f'  {f}')
    print(f'lint-pinned-uses: {checked} uses: line(s) checked, {len(found)} unpinned (control: 2 of 2 found)')
    return len(found)


if __name__ == '__main__':
    sys.exit(main())
