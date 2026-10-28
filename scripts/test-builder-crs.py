#!/usr/bin/env python3
"""
test-builder-crs — the three Builder CRs this chart ships are the frontend's, and a signed-in user
can read them.

WHAT IT GUARDS.
  The frontend's builder engine loads its builders from Builder CRs
  (builders.templates.krateo.io/v1alpha1, CRD shipped by frontend-crds >= 1.6.82). The frontend
  declares them in ui/src/builders/fixtures/<name>.builder.yaml and its own tests run on those
  files; this chart ships them to the cluster. Two copies of one declaration drift unless something
  compares them, so this does:
  1. helm/portal/files/builders/<name>.builder.yaml is BYTE-IDENTICAL to each fixture on
     krateo-platformops/frontend at FRONTEND_REF, the pinned revision these copies were taken from
     — the frontend's builderFixtures.test.ts pins those bytes by sha256 — and the chart renders
     each as a Builder of the same name, in the release namespace, with an equal spec, and renders
     no Builder the frontend does not declare.
  2. The CRs agree with the chart: each draftKind is a portal.draftBuilders kind whose route the
     Builder's route sits under, each spec.portal.draftsCard names a widget the chart renders, and
     each preview.restActionRef names a RESTAction the chart renders.
  3. A Role in the release namespace bound to system:authenticated grants get and list on
     builders — and nothing that writes — with the chart's defaults AND with the demo users off,
     so the frontend's /list?category=builders reads them as any signed-in caller (ADR 0001).

WHY A PINNED REVISION AND NOT main.
  This compared against frontend `main` until 2026-10-28, which turned every branch of this repo
  red the moment the frontend merged a fixture change — before anyone here had decided to adopt it,
  and with no action available in this repo's own PR to make it green. The comparison is about
  PROVENANCE: are these bytes the frontend's declaration? A moving ref cannot answer that. It
  answers "are these the frontend's LATEST declaration?", which is a different claim, and not one
  this chart makes — what the chart ships has to match the frontend the installer deploys beside
  it, which is a pinned version too, not main.

  So FRONTEND_REF names the revision the copies came from, and adopting a newer fixture is a
  deliberate commit here that moves the bytes and the pin together. Check 1 still goes red for a
  copy that drifts from the pin: a hand edit in this repo is exactly what it was built to catch.

  Losing the red-on-their-merge signal would hide a pending adoption, so the gate REPORTS when the
  frontend has moved ahead of the pin. That report never fails the run — adopting a fixture is this
  repo's decision, not one the frontend's merge queue makes for it — but it is printed on every
  run, so the choice is visible rather than discovered at release. To make it blocking for one run,
  pass `--frontend-ref main`.

Usage: test-builder-crs.py [--fixtures DIR | --frontend-ref REF]. Exit code = failed checks.
"""
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART_DIR = os.path.join(HERE, '..', 'helm', 'portal')
CHART_FILES = os.path.join(CHART_DIR, 'files', 'builders')
FIXTURES = ('portal-builder', 'blueprint-builder', 'controller-builder')
RAW = 'https://raw.githubusercontent.com/krateo-platformops/frontend/{ref}/ui/src/builders/fixtures/{name}.builder.yaml'
# The frontend revision helm/portal/files/builders/*.builder.yaml were taken from: 672b22b,
# "feat(controller-builder): round 2 — pinned served version, ids from status, oasgen 0.25
# validator (#434)", the last frontend commit to touch ui/src/builders/fixtures/. Move it in the
# SAME commit that copies new fixture bytes in, never on its own — the pin is this repo's claim
# about where those bytes came from, so the pin without the bytes (or the bytes without the pin)
# makes the claim false. Same convention as hack/design-lint's VENDORED AT stamp.
FRONTEND_REF = '672b22bdab3bb472b91d183afe72ab7760d44f9e'
GROUP = 'builders.templates.krateo.io'
WRITE_VERBS = {'create', 'update', 'patch', 'delete', 'deletecollection', '*'}


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tbi = _load('tbi', 'test-builder-install.py')
NS = tbi.NS


def fixtures(fixtures_dir, ref):
    out = {}
    for name in FIXTURES:
        if fixtures_dir:
            text = open(os.path.join(fixtures_dir, f'{name}.builder.yaml'), encoding='utf-8').read()
        else:
            with urllib.request.urlopen(RAW.format(ref=ref, name=name), timeout=30) as resp:
                text = resp.read().decode('utf-8')
        out[name] = text
    return out


def builders(docs):
    return {d['metadata']['name']: d for d in docs
            if d.get('kind') == 'Builder' and d.get('apiVersion', '').startswith(GROUP + '/')}


def draft_builders():
    text = open(os.path.join(HERE, '..', 'helm', 'portal', 'templates', '_builder.tpl'), encoding='utf-8').read()
    m = re.search(r'define "portal\.draftBuilders" -}}\n(.*?)\{\{- end -}}', text, re.S)
    if not m:
        raise LookupError('_builder.tpl has no portal.draftBuilders define')
    return yaml.safe_load(m.group(1))


def check_the_crs_are_the_frontends(docs, fx):
    problems = []
    chart = builders(docs)
    for name, text in fx.items():
        path = os.path.join(CHART_FILES, f'{name}.builder.yaml')
        if not os.path.exists(path):
            problems.append(f'{name}: no {os.path.relpath(path, os.path.join(HERE, ".."))}')
        else:
            mine = hashlib.sha256(open(path, 'rb').read()).hexdigest()
            theirs = hashlib.sha256(text.encode('utf-8')).hexdigest()
            if mine != theirs:
                problems.append(f'{name}: files/builders bytes sha256 {mine[:12]} != the frontend fixture {theirs[:12]} — copy it')
        want = yaml.safe_load(text)
        got = chart.get(name)
        if got is None:
            problems.append(f'the frontend declares Builder {name}; the chart renders none')
            continue
        if got['apiVersion'] != want['apiVersion'] or got['kind'] != want['kind']:
            problems.append(f'{name}: {got["apiVersion"]} {got["kind"]} != {want["apiVersion"]} {want["kind"]}')
        if got['metadata'].get('namespace') != NS:
            problems.append(f'{name}: namespace {got["metadata"].get("namespace")!r}, want the release namespace {NS!r}')
        if got['spec'] != want['spec']:
            keys = sorted(k for k in set(got['spec']) | set(want['spec']) if got['spec'].get(k) != want['spec'].get(k))
            problems.append(f'{name}: spec differs from the frontend fixture at {keys}')
    for name in sorted(set(chart) - set(fx)):
        problems.append(f'the chart renders Builder {name}; the frontend declares no such fixture')
    return problems


def check_the_crs_agree_with_the_chart(docs, fx):
    problems = []
    kinds = draft_builders()
    named = {(d.get('kind'), (d.get('metadata') or {}).get('name')) for d in docs}
    widget_names = {n for k, n in named if k != 'RESTAction'}
    seen = set()
    for name, cr in builders(docs).items():
        spec = cr['spec']
        kind = spec.get('draftKind')
        seen.add(kind)
        if kind not in kinds:
            problems.append(f'{name}: draftKind {kind!r} is not a portal.draftBuilders kind {sorted(kinds)}')
        elif not spec.get('route', '').startswith(kinds[kind]['route'] + '/'):
            problems.append(f'{name}: route {spec.get("route")!r} is not under portal.draftBuilders {kinds[kind]["route"]!r}')
        card = ((spec.get('portal') or {}).get('draftsCard') or {}).get('name')
        if card and card not in widget_names:
            problems.append(f'{name}: portal.draftsCard {card!r} is not a widget the chart renders')
        ra = ((spec.get('preview') or {}).get('restActionRef') or {}).get('name')
        if ra and ('RESTAction', ra) not in named:
            problems.append(f'{name}: preview.restActionRef {ra!r} is not a RESTAction the chart renders')
    for kind in sorted(set(kinds) - seen):
        problems.append(f'portal.draftBuilders lists {kind!r}; no Builder declares that draftKind')
    return problems


def render(chart_dir, *sets):
    """tbi.render with --set flags: the same staging of the CHART_VERSION placeholders."""
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(chart_dir, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        open(meta, 'w', encoding='utf-8').write(
            text.replace('CHART_VERSION', '0.0.0-dev').replace('APP_VERSION', '0.0.0-dev'))
        cmd = ['helm', 'template', 'portal', staged, '--namespace', NS]
        for s in sets:
            cmd += ['--set', s]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise SystemExit(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def readers(docs):
    """(group, problems): the groups a RoleBinding in the release namespace lets get+list builders,
    and any rule that lets one write them."""
    roles = {d['metadata']['name']: d for d in docs
             if d.get('kind') == 'Role' and d['metadata'].get('namespace') == NS}
    groups, problems = set(), []
    for d in docs:
        if d.get('kind') != 'RoleBinding' or d['metadata'].get('namespace') != NS:
            continue
        role = roles.get(d['roleRef']['name'])
        for rule in (role or {}).get('rules') or []:
            if GROUP in rule.get('apiGroups', []) and {'builders', '*'} & set(rule.get('resources', [])):
                writes = WRITE_VERBS & set(rule.get('verbs', []))
                if writes:
                    problems.append(f'Role {role["metadata"]["name"]} grants {sorted(writes)} on builders')
                if {'get', 'list'} <= set(rule.get('verbs', [])):
                    groups |= {s['name'] for s in d.get('subjects') or [] if s.get('kind') == 'Group'}
    return groups, problems


def check_every_signed_in_user_can_read_builders(docs, fx):
    """ADR 0001: the authenticated-user role reads Builders. Bound to system:authenticated, so it
    holds with any identity provider — and rendered with or without the demo users."""
    problems = []
    for label, rendered in (('defaults', docs),
                            ('enableCyberjokerUser=false, enableAdminUser=false',
                             render(CHART_DIR, 'enableCyberjokerUser=false', 'enableAdminUser=false'))):
        groups, writes = readers(rendered)
        problems += [f'{label}: {w}' for w in writes]
        if 'system:authenticated' not in groups:
            problems.append(f'{label}: no Role bound to system:authenticated in {NS} grants get+list on builders.{GROUP}')
    return problems


def report_pin_drift(fx):
    """Print whether the frontend has moved ahead of FRONTEND_REF. Never fails and never changes the
    exit code: this is the notice that an adoption is pending, not a verdict that one is overdue."""
    try:
        latest = fixtures(None, 'main')
    except OSError as exc:
        print(f'[ ?? ] the pin vs frontend main: could not read main ({type(exc).__name__}: {exc})')
        return
    behind = sorted(name for name, text in latest.items() if text != fx[name])
    if behind:
        print(f'[NOTE] frontend main has moved ahead of the pin at {", ".join(behind)} — adopt it by '
              'copying those fixtures into files/builders and moving FRONTEND_REF in the same commit')
    else:
        print('[ OK ] the pin is current — frontend main declares the same fixture bytes')


CHECKS = [check_the_crs_are_the_frontends, check_the_crs_agree_with_the_chart, check_every_signed_in_user_can_read_builders]


def main():
    args = sys.argv[1:]
    fixtures_dir, ref = None, FRONTEND_REF
    if '--fixtures' in args:
        i = args.index('--fixtures')
        fixtures_dir = args[i + 1]
        del args[i:i + 2]
    if '--frontend-ref' in args:
        i = args.index('--frontend-ref')
        ref = args[i + 1]
        del args[i:i + 2]
    docs = tbi.render(args[0]) if args else tbi.render_portal_and_pages()
    try:
        fx = fixtures(fixtures_dir, ref)
    except OSError as exc:
        # Red, but with the reason: nothing here drifted, the comparison could not be made. Without
        # this the same bad morning ends in a urllib traceback, which reads as a defect in the chart
        # — and a lint that needs the network is a lint that fails on a bad morning, so when it does
        # it should say so (the same reasoning as .github/workflows/design-system.yaml).
        print(f'[FAIL] could not read the frontend fixtures at '
              f'{fixtures_dir or "krateo-platformops/frontend@" + ref} — nothing in this repo '
              f'drifted, the comparison could not be made: {type(exc).__name__}: {exc}')
        return 1
    failed = 0
    for check in CHECKS:
        try:
            problems = check(docs, fx)
        except (AssertionError, LookupError, KeyError, TypeError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {check.__name__.replace("check_", "").replace("_", " ")}')
        for p in problems:
            print(f'        {p}')
        failed += bool(problems)
    if fixtures_dir is None and ref == FRONTEND_REF:
        report_pin_drift(fx)
    print(f'\n{len(CHECKS) - failed} of {len(CHECKS)} checks passed '
          f'(fixtures: {fixtures_dir or "krateo-platformops/frontend@" + ref})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
