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
     krateo-platformops/frontend (main, or --frontend-ref) — the frontend's builderFixtures.test.ts
     pins those bytes by sha256 — and the chart renders each as a Builder of the same name, in the
     release namespace, with an equal spec, and renders no Builder the frontend does not declare.
  2. The CRs agree with the chart: each draftKind is a portal.draftBuilders kind whose route the
     Builder's route sits under, each spec.portal.draftsCard names a widget the chart renders, and
     each preview.restActionRef names a RESTAction the chart renders.
  3. A Role in the release namespace bound to system:authenticated grants get and list on
     builders — and nothing that writes — with the chart's defaults AND with the demo users off,
     so the frontend's /list?category=builders reads them as any signed-in caller (ADR 0001).

  When the frontend changes a fixture, check 1 goes red here until the chart's copy follows: that
  is the point, not a flake.

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


CHECKS = [check_the_crs_are_the_frontends, check_the_crs_agree_with_the_chart, check_every_signed_in_user_can_read_builders]


def main():
    args = sys.argv[1:]
    fixtures_dir, ref = None, 'main'
    if '--fixtures' in args:
        i = args.index('--fixtures')
        fixtures_dir = args[i + 1]
        del args[i:i + 2]
    if '--frontend-ref' in args:
        i = args.index('--frontend-ref')
        ref = args[i + 1]
        del args[i:i + 2]
    docs = tbi.render(args[0] if args else CHART_DIR)
    fx = fixtures(fixtures_dir, ref)
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
    print(f'\n{len(CHECKS) - failed} of {len(CHECKS)} checks passed '
          f'(fixtures: {fixtures_dir or "krateo-platformops/frontend@" + ref})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
