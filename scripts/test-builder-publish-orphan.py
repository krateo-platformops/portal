#!/usr/bin/env python3
"""builder-publish's GitHub repository is orphaned on delete, never deleted on GitHub.

Each publish's `Repository` holds the published blueprint or page's source and its history. Once
github-provider-kog declares GitHub's `DELETE /repos/{owner}/{repo}` on that kind, deleting the CR
deletes the real repository -- on every uninstall of a publish and every prune of the CR. The runtime
(unstructured-runtime >= v1.6.1, carrying the #23 fix) skips the vendor call when the CR carries
`krateo.io/deletion-policy: orphan`; with no annotation it defaults to `delete`. Decided 2026-11-10:
keep the repository.

Renders the chart across the value variants that change what it emits, and asserts:
  - EVERY rendered kind is classified, in CREATES_REPO or in DOES_NOT_CREATE. An unclassified kind
    fails: a new kind that creates a remote repository (another SCM's, say) must not pass unchecked
    just because the GitHub Repository beside it still carries the annotation.
  - every CREATES_REPO resource carries the annotation;
  - a variant that should create a repository renders at least one (no vacuous pass), and a variant
    that should not (repository.create=false) renders none, so the variant list stays meaningful.

Then runs its own CONTROLS, each over a copy of the chart broken in one way; each must FAIL for the
reason it breaks. A check whose control passes is not looking at what it claims to.

LIMIT. `helm template` disables `lookup`, so kinds this chart gates on live cluster state never render
here: the PullRequest (gated on its CRD and on every LocalResource's commit) and the legacy username
Secret (gated on a live LocalResource still referencing it). They are classified below from the
templates, but the classification check only sees what renders offline.
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
ANNOTATION_LINE = re.compile(r'^\s*krateo\.io/deletion-policy:\s*orphan\s*\n', re.M)
CREATE_GUARD = '{{- if .Values.repository.create }}'

# Every kind the chart can emit, keyed by (API group, kind) -- the version is left out so a served
# version bump (v2022-11-28 today) does not read as a new kind.
#
# Creates a remote repository: deleting the CR deletes the repository unless it is orphaned.
CREATES_REPO = {
    ('github.krateo.io', 'Repository'),      # templates/repository.yaml
    ('github.krateo.io', 'UserRepository'),  # not emitted today; github-provider-kog is adding DELETE to it too
}
# Creates no repository: these act inside one that already exists, or are not git objects at all.
DOES_NOT_CREATE = {
    ('git.krateo.io', 'Repo'),               # templates/repo.yaml: seeds an EXISTING destination (toRepo)
    ('git.krateo.io', 'LocalResource'),      # templates/localresources.yaml: one file commit each
    ('github.krateo.io', 'PullRequest'),     # templates/pullrequest.yaml: the change request (lookup-gated)
    ('', 'ConfigMap'),                       # templates/architecture.yaml: the chart architecture
    ('', 'Secret'),                          # templates/username-secret.yaml: legacy username (lookup-gated)
}

SOURCE = 'source.url=https://github.com/krateo-blueprints/builder-scaffold.git'
# (label, --set values, whether a repository is created). Every value is one values.schema.json
# admits: git.scm's enum is ["github"] alone, repository.create is a boolean, target.visibility is
# public|private. source.url with repository.create=false is what reaches templates/repo.yaml offline
# (with create=true its gate is a live lookup on the Repository).
VARIANTS = (
    ('defaults', (), True),
    ('target.visibility=private', ('target.visibility=private',), True),
    ('target.visibility=public', ('target.visibility=public',), True),
    ('git.scm=github', ('git.scm=github',), True),
    ('repository.create=false', ('repository.create=false',), False),
    ('repository.create=false source.url', ('repository.create=false', SOURCE), False),
)


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
        raise SystemExit(f'helm template failed ({" ".join(sets) or "defaults"}):\n{proc.stderr.strip()}')
    return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def group_kind(doc):
    api = str(doc.get('apiVersion', ''))
    return (api.rpartition('/')[0], doc.get('kind'))


def check(chart):
    """(failures, {label: repository count}) for one chart directory, across VARIANTS."""
    failures, counts = [], {}
    for label, sets, creates in VARIANTS:
        docs = render(chart, *sets)
        repos = []
        for d in docs:
            gk = group_kind(d)
            name = f"{d.get('apiVersion')} {d.get('kind')}/{(d.get('metadata') or {}).get('name')}"
            if gk in CREATES_REPO:
                repos.append(d)
                got = ((d.get('metadata') or {}).get('annotations') or {}).get(KEY)
                if got != WANT:
                    failures.append(f'[{label}] {name}: {KEY}={got!r}, want {WANT!r} -- deleting this CR '
                                    f'would delete the repository on GitHub')
            elif gk not in DOES_NOT_CREATE:
                failures.append(f'[{label}] {name}: unclassified kind -- add {gk!r} to CREATES_REPO (it '
                                f'creates a remote repository, and then needs {KEY}: {WANT}) or to '
                                f'DOES_NOT_CREATE in scripts/test-builder-publish-orphan.py')
        counts[label] = len(repos)
        if creates and not repos:
            failures.append(f'[{label}] no repository rendered -- nothing was checked')
        if not creates and repos:
            failures.append(f'[{label}] {len(repos)} repository resource(s) rendered, want 0 -- this variant '
                            f'creates no repository by design')
    return failures, counts


def control(name, mutate, marker):
    """Break a copy of the chart with mutate(copy); the check must fail with a line containing marker."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = copy_chart(tmp)
        problem = mutate(copy)
        if problem:
            print(f'FAIL control {name}: {problem}')
            return False
        failures, _ = check(copy)
    hits = [f for f in failures if marker in f]
    if not hits:
        print(f'FAIL control {name}: the check did not fail on it (failures: {failures or "none"}) -- '
              f'it is not looking')
        return False
    print(f'ok   control {name}: the check fails ({len(hits)} failures, e.g. {hits[0]})')
    return True


def edit_template(copy, fname, edit):
    path = os.path.join(copy, 'templates', fname)
    with open(path, encoding='utf-8') as fh:
        text = fh.read()
    new, problem = edit(text)
    if problem:
        return problem
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(new)
    return None


def strip_annotation(copy):
    def edit(text):
        new, n = ANNOTATION_LINE.subn('', text)
        if n != 1:
            return None, (f'expected exactly one `{KEY}: {WANT}` line in templates/repository.yaml, found '
                          f'{n} -- the control cannot remove what it cannot find')
        return new, None
    return edit_template(copy, 'repository.yaml', edit)


def ignore_create_flag(copy):
    def edit(text):
        n = text.count(CREATE_GUARD)
        if n != 1:
            return None, (f'expected exactly one `{CREATE_GUARD}` in templates/repository.yaml, found {n} '
                          f'-- the control cannot remove what it cannot find')
        return text.replace(CREATE_GUARD, '{{- if true }}'), None
    return edit_template(copy, 'repository.yaml', edit)


def add_unknown_kind(copy):
    with open(os.path.join(copy, 'templates', 'zz-control-unknown-kind.yaml'), 'w', encoding='utf-8') as fh:
        fh.write('apiVersion: gitlab.krateo.io/v1alpha1\nkind: Project\nmetadata:\n'
                 '  name: {{ .Values.name }}-project\n  namespace: {{ .Release.Namespace }}\n')
    return None


def main():
    with tempfile.TemporaryDirectory() as tmp:
        failures, counts = check(copy_chart(tmp))
    for f in failures:
        print(f'FAIL {f}')
    if failures:
        return 1
    for label, _, creates in VARIANTS:
        print(f'ok   [{label}] {counts[label]} repository resource(s), want {"at least 1" if creates else "0"}')
    print(f'ok   every rendered kind is classified; every rendered repository carries {KEY}: {WANT}')

    ok = all([
        # The annotation removed from the template: every creating variant must fail on it.
        control('annotation removed', strip_annotation, f"{KEY}=None, want '{WANT}'"),
        # The template made to ignore repository.create: the zero-repository variants must fail.
        control('repository.create ignored', ignore_create_flag, 'want 0'),
        # A repository kind the guard has never heard of: it must refuse it, not skip it.
        control('unclassified kind', add_unknown_kind, "unclassified kind -- add ('gitlab.krateo.io', 'Project')"),
    ])
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
