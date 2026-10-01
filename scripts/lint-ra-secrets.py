#!/usr/bin/env python3
"""
lint-ra-secrets — no RESTAction in any chart here reads Secrets.

WHY. snowplow (1.12.30, cache on — the default) serves an api step's GET or LIST of core/v1
secrets from a dynamic informer it registers lazily, CLUSTER-WIDE, on first touch
(informer_dispatch.go gate 6 → EnsureResourceType; deps_extract.go likewise). That informer holds
every Secret in the cluster with its data, ignores the step's Accept header (so a
PartialObjectMetadata request still comes back whole), and the response can land in the
identity-free apistage L1 cache. snowplow's own secrets_snapshot.go calls widening the dynamic
factory to Secrets catastrophic. So a step that only wanted to know a Secret EXISTS would pull
every Secret's data into snowplow. Nothing in snowplow denylists it; this lint does, here.

WHAT. Every chart under helm/ is rendered; every RESTAction api step fails the lint when its path
names a `secrets` segment (static or inside a ${ } expression) or its userAccessFilter's resource
is `secrets`. endpointRef is not a read of this kind (snowplow resolves endpoint Secrets through
its own AUTHN-namespace-scoped informer), so it is not flagged.

A RESTAction a values flag leaves unrendered is still checked: every template that declares
`kind: RESTAction` is also scanned as SOURCE for a path or userAccessFilter naming secrets.

ALLOWED lists the steps that predate this lint. Each is a known instance of the hazard, kept so
the lint can gate everything new; removing one from the chart means removing it here.

Usage: lint-ra-secrets.py. Exit code = violations.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
NS = 'krateo-system'
SECRETS = re.compile(r'(^|/|")secrets($|[/?"])')

# (chart, RESTAction, step) — pre-existing, to be removed, never to be added to.
ALLOWED = {
    # Settings > Users lists the release namespace's *-clientconfig Secrets for their names and
    # ages (portal#88). It predates this lint; replacing it is its own change.
    ('portal', 'settings-users', 'clientconfigs'),
}
ALLOWED_SOURCES = {('portal', 'restaction.settings-users.yaml')}
SOURCE_LINE = re.compile(r'^\s*(-\s+)?(path:.*(/|")secrets($|[/?"\s])|resource:\s*["\']?secrets["\']?\s*$)')


def render(chart_dir):
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(chart_dir, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        open(meta, 'w', encoding='utf-8').write(
            text.replace('CHART_VERSION', '0.0.0-dev').replace('APP_VERSION', '0.0.0-dev'))
        proc = subprocess.run(['helm', 'template', 'lint', staged, '--namespace', NS],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise SystemExit(f'helm template {chart_dir} failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def main():
    violations, allowed_seen, count = [], set(), 0
    for meta in sorted(glob.glob(os.path.join(HERE, '..', 'helm', '*', 'Chart.yaml'))):
        chart_dir = os.path.dirname(meta)
        chart = os.path.basename(chart_dir)
        for doc in render(chart_dir):
            if doc.get('kind') != 'RESTAction':
                continue
            count += 1
            ra = (doc.get('metadata') or {}).get('name', '')
            for step in (doc.get('spec') or {}).get('api') or []:
                path = step.get('path') or ''
                uaf = (step.get('userAccessFilter') or {}).get('resource') or ''
                if not (SECRETS.search(path) or uaf == 'secrets'):
                    continue
                key = (chart, ra, step.get('name', ''))
                if key in ALLOWED:
                    allowed_seen.add(key)
                    continue
                violations.append(f'{chart}: RESTAction {ra} step {key[2]} reads Secrets ({path or "userAccessFilter secrets"})')
        for tpl in sorted(glob.glob(os.path.join(chart_dir, 'templates', '*.yaml'))):
            text = open(tpl, encoding='utf-8').read()
            if 'kind: RESTAction' not in text or (chart, os.path.basename(tpl)) in ALLOWED_SOURCES:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if SOURCE_LINE.search(line):
                    violations.append(f'{chart}: {os.path.basename(tpl)}:{n} reads Secrets ({line.strip()})')
    for key in sorted(ALLOWED - allowed_seen):
        violations.append(f'ALLOWED entry {key} no longer exists in the chart: remove it from lint-ra-secrets.py')
    for v in violations:
        print(f'  {v}')
    print(f'lint-ra-secrets: {count} RESTActions, {len(violations)} violation(s), {len(allowed_seen)} pre-existing allowed')
    return len(violations)


if __name__ == '__main__':
    sys.exit(main())
