#!/usr/bin/env python3
"""builder-publish's GitHub repository is orphaned on delete, never deleted on GitHub.

Each publish's `Repository` holds the published blueprint or page's source and its history. Once
github-provider-kog declares GitHub's `DELETE /repos/{owner}/{repo}` on that kind, deleting the CR
deletes the real repository -- on every uninstall of a publish and every prune of the CR. The runtime
(unstructured-runtime >= v1.6.1, carrying the #23 fix) skips the vendor call when the CR carries
`krateo.io/deletion-policy: orphan`; with no annotation it defaults to `delete`. Decided 2026-11-10:
keep the repository.

Renders the chart and asserts every github.krateo.io Repository / UserRepository it emits carries the
annotation. Then runs its own CONTROL: the same check over a copy of the chart with the annotation
removed from the template must FAIL. A check whose control passes is not looking at the annotation.
"""
import os
import re
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART = os.path.join(REPO, 'helm', 'builder-publish')
KEY = 'krateo.io/deletion-policy'
WANT = 'orphan'
# Both kinds address a GitHub repository; github-provider-kog is adding the DELETE verb to both.
REPO_KINDS = ('Repository', 'UserRepository')
ANNOTATION_LINE = re.compile(r'^\s*krateo\.io/deletion-policy:\s*orphan\s*\n', re.M)


def copy_chart(tmp):
    copy = os.path.join(tmp, 'builder-publish')
    subprocess.run(['cp', '-r', CHART, copy], check=True)
    meta = os.path.join(copy, 'Chart.yaml')
    with open(meta, encoding='utf-8') as fh:
        text = fh.read()
    with open(meta, 'w', encoding='utf-8') as fh:
        fh.write(text.replace('CHART_VERSION', '0.0.0-test'))
    return copy


def render(chart, *sets):
    args = ['helm', 'template', 'r', chart, '-n', 'krateo-system']
    for s in sets:
        args += ['--set', s]
    proc = subprocess.run(args, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(f'helm template failed:\n{proc.stderr.strip()}')
    return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def check(chart):
    """Failures for one chart directory, across the value sets that change the Repository."""
    failures = []
    # Default values, and both visibility branches: the Repository must be orphaned whichever renders.
    for sets in ((), ('target.visibility=private',), ('target.visibility=public',)):
        label = ' '.join(sets) or 'defaults'
        repos = [d for d in render(chart, *sets)
                 if d.get('kind') in REPO_KINDS
                 and str(d.get('apiVersion', '')).startswith('github.krateo.io/')]
        if not repos:
            failures.append(f'[{label}] no github.krateo.io Repository rendered -- nothing was checked')
            continue
        for d in repos:
            name = f"{d['kind']}/{d.get('metadata', {}).get('name')}"
            got = (d.get('metadata', {}).get('annotations') or {}).get(KEY)
            if got != WANT:
                failures.append(f'[{label}] {name}: {KEY}={got!r}, want {WANT!r} -- deleting this CR '
                                f'would delete the repository on GitHub')
    return failures


def main():
    with tempfile.TemporaryDirectory() as tmp:
        failures = check(copy_chart(tmp))
    for f in failures:
        print(f'FAIL {f}')
    if failures:
        return 1
    print(f'ok   every rendered Repository carries {KEY}: {WANT}')

    # CONTROL: strip the annotation from the template and the check must fail.
    with tempfile.TemporaryDirectory() as tmp:
        copy = copy_chart(tmp)
        tpl = os.path.join(copy, 'templates', 'repository.yaml')
        with open(tpl, encoding='utf-8') as fh:
            text = fh.read()
        stripped, n = ANNOTATION_LINE.subn('', text)
        if n != 1:
            print(f'FAIL control: expected exactly one `{KEY}: {WANT}` line in templates/repository.yaml, '
                  f'found {n} -- the control cannot remove what it cannot find')
            return 1
        with open(tpl, 'w', encoding='utf-8') as fh:
            fh.write(stripped)
        control = check(copy)
    if not control:
        print('FAIL control: the check still passed with the annotation removed -- it is not looking at it')
        return 1
    print(f'ok   control: with the annotation removed the check fails ({len(control)} failures, e.g. '
          f'{control[0]})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
