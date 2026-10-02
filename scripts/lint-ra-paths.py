#!/usr/bin/env python3
"""
lint-ra-paths — a RESTAction step must not dial a half-built path when its input is missing.

WHAT HAPPENED. snowplow resolves widgets with NO request extras on its Phase-1 walk (snowplow
internal/handlers/dispatchers/phase1_walk.go: "extras=nil at prewarm"), and a page's RESTAction
reads its route params from those extras. A step path like
    ${ "/apis/…/builderpublishes/review-" + ((.name // "") | ltrimstr("p-")) }
turns "no name" into a valid-looking path and dials it: on krateo-057 (portal 1.8.55, snowplow
1.12.21) one pod made 601 GETs of `…/builderpublishes/review-`, 358 of `/apis/none.krateo.io/v1/none`
and 67 of `/api/v1/namespaces//configmaps/-architecture` in 20 minutes, every one a 404 that
continueOnError hid. Changing the fallback value fixes nothing: the step must not RUN.

THE INPUT GATE. snowplow 1.12.21 has one way to skip a step: a `dependsOn.iterator` that yields an
empty array. createRequestOptions builds one request per iterator element and none for an empty
array or a non-array (resolvers/restactions/api/setup.go:43-62; a null upstream is logged at DEBUG,
setup.go:51), and the stage loop skips a stage with no requests (resolve.go:467-472, :1482-1485),
leaving its key absent from the dict. The path, payload and headers are evaluated against the
ELEMENT, not the dict (setup.go:44, :69-81). So a gated step reads:

    - name: claim
      dependsOn:
        name: bp            # the step whose output it reads; "" when it reads only the extras
        iterator: '[ { apiVersion: (.bp.status.apiVersion // ""), … } | select(.apiVersion != "" …) ]'
      path: ${ "/apis/" + .apiVersion + "/…" }

The `//` defaults live in the iterator, where an absent input becomes an empty array; the path only
concatenates fields the iterator guarantees. `dependsOn.name: ""` is a step with no upstream step:
topologicalSort skips an empty name (resolvers/restactions/api/sort.go:21), and the CRD only
requires the key. A step that also needs a prior step's data names that step, which also orders it
(a step with no dependsOn runs in map order against its siblings, sort.go:28).

WHAT THIS CHECKS, on every RESTAction every chart in helm/ renders (portal also with its optional
snowplow SLI page on):
  1. `//` inside a `${ … }` path expression is an error, unless ALLOWED below with its reason.
  2. Each step is evaluated the way snowplow would with NO extras and no upstream output (Phase-1):
     iterator (a non-array or a jq error is zero requests), then the path per element, or once
     against the dict. Any path it would dial is an error if it has an empty segment, a trailing
     slash, a segment that starts or ends with `-` or `.`, a `null` segment, or is a jq error
     string (snowplow dials evalJQ's error text as the path, setup.go:100-102).

Uses the `jq` binary. Usage: lint-ra-paths.py. Exit code = findings.
"""
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NS = 'krateo-system'
JQ = os.environ.get('JQ', 'jq')

# Charts rendered with extra values, so every RESTAction template is linted.
EXTRA_VALUES = {'portal': ['observability.snowplowSli=true']}

# `//` in a path that is safe: the default is a real, complete value and the call is useful without
# the input. (RESTAction glob, step) -> reason.
ALLOWED = {
    ('sp-*', 'q'): 'ClickHouse query window: `.range` defaults to the page\'s own 24h; a query '
                   'parameter, never a path segment',
    ('obs-*', '*'): 'ClickHouse query window and optional filters (`.range`, `.limit`, '
                    '`.serviceName`), each defaulted to the page\'s own default and omitted from '
                    'the SQL when empty; a query parameter, never a path segment',
    ('agents-token-*', '*'): 'ClickHouse query window: `.range` defaults to the page\'s own 24h',
    ('alert-detail', 'incidents'): 'a LIST of the namespace\'s Incidents; the release namespace is '
                                   'the page\'s own default. An iterator gate would opt the stage '
                                   'into snowplow\'s cluster-list collapse (resolvers/restactions/'
                                   'api/cluster_list.go:195) and change how it is served',
    ('incident-detail', 'incidents'): 'a LIST of the namespace\'s Incidents (same collapse reason as '
                                      'alert-detail/incidents)',
    ('agent-detail', 'deployments'): 'a LIST of the namespace\'s Deployments; the release namespace '
                                     'is the page\'s own default (same collapse reason as '
                                     'alert-detail/incidents)',
}


def render(chart_dir, extra):
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(chart_dir, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        open(meta, 'w', encoding='utf-8').write(
            text.replace('CHART_VERSION', '0.0.0-dev').replace('APP_VERSION', '0.0.0-dev'))
        cmd = ['helm', 'template', 'lint', staged, '--namespace', NS]
        for kv in extra:
            cmd += ['--set', kv]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise SystemExit(f'helm template {chart_dir} failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def jq(program, data):
    """snowplow-shaped evaluation: one value, or ('error', message)."""
    proc = subprocess.run([JQ, '-c', program], input=json.dumps(data), capture_output=True,
                          text=True, check=False)
    if proc.returncode != 0:
        return ('error', proc.stderr.strip())
    outs = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return outs[0] if len(outs) == 1 else ('error', f'{len(outs)} values')


def query(path):
    """The jq inside a `${ … }` path, or None for a literal path."""
    s = (path or '').strip()
    return s[2:-1] if s.startswith('${') and s.endswith('}') else None


def allowed(ra, step):
    for (ra_glob, step_glob), reason in ALLOWED.items():
        if fnmatch.fnmatch(ra, ra_glob) and fnmatch.fnmatch(step, step_glob):
            return reason
    return None


def order(steps):
    """snowplow's topologicalSort (sort.go): dependents after the step they name."""
    names = {s['name'] for s in steps}
    done, out = set(), []
    while len(out) < len(steps):
        progressed = False
        for s in steps:
            dep = (s.get('dependsOn') or {}).get('name') or ''
            if s['name'] not in done and (dep == '' or dep in done or dep not in names):
                done.add(s['name'])
                out.append(s)
                progressed = True
        if not progressed:
            raise SystemExit('cyclic dependsOn')
    return out


def dialled(step, data):
    """The paths snowplow would request for this step over `data`."""
    expr = query(step.get('path'))
    it = (step.get('dependsOn') or {}).get('iterator')
    if it:
        items = jq(it, data)
        if not isinstance(items, list):
            return []
    else:
        items = [data]
    if expr is None:
        return [step.get('path', '')] * len(items)
    out = []
    for item in items:
        got = jq(expr, item)
        out.append(got[1] if isinstance(got, tuple) else got)
    return out


def malformed(path):
    if not isinstance(path, str):
        return f'not a string: {path!r}'
    base = path.split('?', 1)[0]
    if base == '/':
        return None
    if not base.startswith('/'):
        return 'does not start with / (a jq error string?)'
    if '//' in base:
        return 'empty segment'
    if base.endswith('/'):
        return 'trailing slash'
    for seg in base.strip('/').split('/'):
        if seg == 'null':
            return 'null segment'
        if seg[:1] in '-.' or seg[-1:] in '-.':
            return f'segment {seg!r} starts or ends with - or .'
    return None


def main():
    findings = 0
    charts = sorted(d for d in os.listdir(os.path.join(REPO, 'helm'))
                    if os.path.isfile(os.path.join(REPO, 'helm', d, 'Chart.yaml')))
    for chart in charts:
        docs = render(os.path.join(REPO, 'helm', chart), EXTRA_VALUES.get(chart, []))
        ras = [d for d in docs if d.get('kind') == 'RESTAction']
        print(f'{chart}: {len(ras)} RESTActions')
        for ra in ras:
            name = ra['metadata']['name']
            steps = ra['spec'].get('api') or []
            for step in steps:
                expr = query(step.get('path'))
                if expr is not None and '//' in expr and not allowed(name, step['name']):
                    findings += 1
                    print(f'  FAIL {name}/{step["name"]}: `//` in the path expression — gate the step '
                          f'on its input instead (see this script\'s docstring)')
            for step in order(steps):
                for path in dialled(step, {}):
                    why = malformed(path)
                    if why:
                        findings += 1
                        print(f'  FAIL {name}/{step["name"]}: with no extras it dials {path!r} ({why})')
    print('lint-ra-paths:', 'OK' if findings == 0 else f'{findings} finding(s)')
    return findings


if __name__ == '__main__':
    sys.exit(min(main(), 125))
