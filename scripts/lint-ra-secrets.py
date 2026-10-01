#!/usr/bin/env python3
"""
lint-ra-secrets — no RESTAction step in any chart here NAMES Secrets.

WHY. snowplow (1.12.30, cache on — the default) serves an api step's GET or LIST of core/v1
secrets from a dynamic informer it registers lazily, CLUSTER-WIDE, on first touch
(informer_dispatch.go gate 6 → EnsureResourceType; deps_extract.go likewise). That informer holds
every Secret in the cluster with its data, ignores the step's Accept header (so a
PartialObjectMetadata request still comes back whole), and the response can land in the
identity-free apistage L1 cache. snowplow's own secrets_snapshot.go calls widening the dynamic
factory to Secrets catastrophic. So a step that only wanted to know a Secret EXISTS would pull
every Secret's data into snowplow. Nothing in snowplow denylists it; this lint does, here.

WHAT IT CATCHES. Every chart under helm/ is rendered, and every RESTAction api step fails when its
path, its dependsOn.iterator, or its userAccessFilter's resource or resourcesFrom mentions
"secret" — after percent-decoding (%73ecrets), lower-casing (SECRETS, ascii_downcase) and dropping
every character that is not a letter, so a word assembled from pieces ("sec" + "rets",
"s-e-c-r-e-t-s" | split("-") | join("")) still reads as one. A RESTAction a values flag leaves
unrendered is checked as source instead: its template, comments removed, must not mention it at
all. endpointRef is not flagged: snowplow resolves endpoint Secrets through its own
AUTHN-namespace-scoped informer, not this path.

WHAT IT DOES NOT CATCH. A path built entirely from DATA — `${ ._getpath }` where _getpath comes
from an object's status (restaction.composition-resources reads every status.managed[] path, and a
composition can manage Secrets), or a resource name assembled from character codes. Such a step
can still reach a Secret at runtime; the lint sees no word to match.

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
import urllib.parse

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
NS = 'krateo-system'

# (chart, RESTAction, step) — pre-existing, to be removed, never to be added to.
ALLOWED = {
    # Settings > Users lists the release namespace's *-clientconfig Secrets for their names and
    # ages (portal#88). It predates this lint; replacing it is its own change.
    ('portal', 'settings-users', 'clientconfigs'),
}
ALLOWED_SOURCES = {('portal', 'restaction.settings-users.yaml')}
COMMENTS = re.compile(r'\{\{-?\s*/\*.*?\*/\s*-?\}\}|^\s*#.*$|\s#\s.*$', re.S | re.M)


def names_secrets(text):
    """Whether `text` spells "secret", however it is encoded, cased or cut into pieces."""
    text = str(text or '')
    for _ in range(3):
        text = urllib.parse.unquote(text)
    return 'secret' in re.sub(r'[^a-z]', '', text.lower())


def step_fields(step):
    uaf = step.get('userAccessFilter') or {}
    return {'path': step.get('path'), 'iterator': (step.get('dependsOn') or {}).get('iterator'),
            'userAccessFilter.resource': uaf.get('resource'), 'userAccessFilter.resourcesFrom': uaf.get('resourcesFrom')}


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
        rendered = set()
        for doc in render(chart_dir):
            if doc.get('kind') != 'RESTAction':
                continue
            count += 1
            ra = (doc.get('metadata') or {}).get('name', '')
            rendered.add(ra)
            for step in (doc.get('spec') or {}).get('api') or []:
                hits = [k for k, v in step_fields(step).items() if names_secrets(v)]
                if not hits:
                    continue
                key = (chart, ra, step.get('name', ''))
                if key in ALLOWED:
                    allowed_seen.add(key)
                    continue
                violations.append(f'{chart}: RESTAction {ra} step {key[2]} names Secrets in {", ".join(hits)}')
        # A RESTAction the default values do not render: its source, comments removed.
        for tpl in sorted(glob.glob(os.path.join(chart_dir, 'templates', '*.yaml'))):
            text = open(tpl, encoding='utf-8').read()
            if not re.search(r'(?m)^kind:\s*RESTAction\s*$', text) or (chart, os.path.basename(tpl)) in ALLOWED_SOURCES:
                continue
            names = re.findall(r'(?m)^  name:\s*["\']?([^\s"\'{]+)', text)
            if names and all(n in rendered for n in names):
                continue
            body = COMMENTS.sub('', text)
            for n, line in enumerate(body.splitlines(), 1):
                if names_secrets(line):
                    violations.append(f'{chart}: {os.path.basename(tpl)} (not rendered by default) names Secrets: {line.strip()}')
    for key in sorted(ALLOWED - allowed_seen):
        violations.append(f'ALLOWED entry {key} no longer exists in the chart: remove it from lint-ra-secrets.py')
    for v in violations:
        print(f'  {v}')
    print(f'lint-ra-secrets: {count} RESTActions, {len(violations)} violation(s), {len(allowed_seen)} pre-existing allowed')
    return len(violations)


if __name__ == '__main__':
    sys.exit(main())
