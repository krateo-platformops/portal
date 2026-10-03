#!/usr/bin/env python3
r"""
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
"secret" — after percent-decoding (%73ecrets), decoding jq's \u escapes (\u0073ecrets),
lower-casing (SECRETS, ascii_downcase) and dropping every character that is not a letter, so a
word assembled from pieces ("sec" + "rets", "s-e-c-r-e-t-s" | split("-") | join("")) still reads
as one. A field that is a list or an object is read as its JSON, keys and values alike. A
RESTAction a values flag leaves unrendered is checked as source instead: its template, comments
removed, must not mention it at all. endpointRef is not flagged: snowplow resolves endpoint
Secrets through its own AUTHN-namespace-scoped informer, not this path.

NO PATH CLIMBS. A path literal that contains "..", or "%2e" in any case, or whose literals joined
contain ".." ("." + "."), fails: a literal "/apis/" or ".../configmaps/" fixes the resource only
while nothing after it can walk back out of it ("/apis/../api/v1/" + …).

DATA-DRIVEN PATHS. A path built from DATA names no word to match — `${ ._getpath }` from a
composition's status.managed[], `${ .path }` from a route's group/version/plural — and can reach a
Secret at runtime. So a step without an endpointRef whose path is a ${ } expression that does not
start with a literal "/apis/…" (a non-core group: no Secret lives there) or a literal core path with
a literal resource segment ("/api/v1/namespaces/" + .ns + "/configmaps/" + .name) is DATA-DRIVEN.
So is any ${ } path that builds characters (implode, ascii, @base64d, @base32d, fromjson, a \u
escape, a string repeated with *): what it spells is not in its text. A data-driven step passes
only when
  - its path is one field of the iterator's item, `${ .<field> }`;
  - its dependsOn.iterator BEGINS with exactly the defs portal.fetchableDefs renders
    (_fetchable.tpl, compared whitespace-normalized), so no def of its own can shadow tostring,
    split or index under them;
  - nothing after them defines fetchablePath or fetchableCore again (a later def shadows) or
    builds characters;
  - and it applies the guard to that field: `(.<field> | fetchablePath)`.
The canonical fetchableCore must itself name no secret. A step with an endpointRef calls another
service, not the apiserver, and is not this lint's concern.

SELF-TEST. Before the charts, every bypass this lint has been shown (a guard defined by the step
itself, a guard redefined after the canonical one, "/apis/../api/v1/", ".../configmaps/" + "../",
%2E, \u escapes, "." + ".", [46,46]|implode after a literal resource, a resourcesFrom that is an
object or a list) is planted in a step and must be caught, and the shapes the charts use must pass.

WHAT IT STILL CANNOT SEE: what DATA holds at runtime (an extras .name of "../secrets/x", an
.apiVersion of "../api/v1"); and jq is a full language, so characters built some way not listed
above. This is a tripwire for the ways a step has been written, not a sandbox.

ALLOWED lists steps that predate this lint. It is empty and stays empty.

Usage: lint-ra-secrets.py. Exit code = violations.
"""
import glob
import json
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

# (chart, RESTAction, step) — empty; never to be added to.
ALLOWED = set()
ALLOWED_SOURCES = set()
LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"')
CORE_RESOURCE_LITERAL = re.compile(r'^/[a-z][a-z0-9]*(/|$)')
COMMENTS = re.compile(r'\{\{-?\s*/\*.*?\*/\s*-?\}\}|^\s*#.*$|\s#\s.*$', re.S | re.M)
JQ_UNICODE = re.compile(r'\\u([0-9a-fA-F]{4})')
# jq that builds characters: what such a path spells is not in its text.
CONSTRUCTED = re.compile(r'\bimplode\b|\bascii\b|@base64d|@base32d|\bfromjson\b|\\u[0-9a-fA-F]{4}|"\s*\*')
FIELD_PATH = re.compile(r'^\$\{\s*\.([A-Za-z_][A-Za-z0-9_]*)\s*\}$')


def as_text(value):
    """A field as one string: a list or an object as its JSON, so its keys and values all count."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def decoded(text):
    """`text` with percent-encoding and jq \\u escapes undone (each up to three layers deep)."""
    for _ in range(3):
        text = JQ_UNICODE.sub(lambda m: chr(int(m.group(1), 16)), urllib.parse.unquote(text))
    return text


def names_secrets(value):
    """Whether `value` spells "secret", however it is encoded, cased or cut into pieces."""
    return 'secret' in re.sub(r'[^a-z]', '', decoded(as_text(value)).lower())


def normalized(text):
    return re.sub(r'\s+', ' ', text).strip()


def canonical_defs():
    """portal.fetchableDefs as _fetchable.tpl defines it: static jq, whitespace-normalized."""
    tpl = open(os.path.join(HERE, '..', 'helm', 'portal', 'templates', '_fetchable.tpl'), encoding='utf-8').read()
    m = re.search(r'\{\{-?\s*define\s+"portal\.fetchableDefs"\s*-?\}\}(.*?)\{\{-?\s*end\s*-?\}\}', tpl, re.S)
    if not m or '{{' in m.group(1):
        raise SystemExit('_fetchable.tpl: portal.fetchableDefs is missing or no longer static jq')
    return normalized(m.group(1))


CANONICAL = canonical_defs()
_CORE = re.search(r'def fetchableCore: (\[[^\]]*\])', CANONICAL)
if not _CORE or any(names_secrets(c) for c in json.loads(_CORE.group(1))):
    raise SystemExit('_fetchable.tpl: fetchableCore is not an array literal, or it names secrets')


def climbs(step):
    """A path literal that could walk out of the resource it names: "..", %2e, or "." + "."."""
    path = str(step.get('path') or '').strip()
    if step.get('endpointRef') or not path:
        return ''
    literals = LITERAL.findall(path) if path.startswith('${') else [path]
    for lit in literals:
        if '%2e' in lit.lower() or '..' in decoded(lit):
            return f'path literal "{lit}" climbs (.. or %2e)'
    if '..' in decoded(''.join(literals)):
        return 'path literals joined climb (..)'
    return ''


def data_driven(step):
    """A ${ } path to the apiserver whose resource is not fixed by a literal."""
    path = str(step.get('path') or '').strip()
    if step.get('endpointRef') or not path.startswith('${'):
        return False
    if CONSTRUCTED.search(path):
        return True
    literals = LITERAL.findall(path)
    first = path[2:].lstrip()
    if not first.startswith('"') or not literals:
        return True
    if literals[0].startswith('/apis/'):
        return False
    if literals[0].startswith('/api/'):
        # The core group: fixed only when a later literal is the resource segment.
        fixed_in_first = re.match(r'^/api/[^/]+/(namespaces/[^/]+/[a-z]+|(?!namespaces)[a-z]+)', literals[0])
        return not (fixed_in_first or any(CORE_RESOURCE_LITERAL.match(l) for l in literals[1:]))
    return True


def guarded(step):
    """The path is one field of the item, and the iterator applies portal.fetchableDefs, verbatim, to it."""
    field = FIELD_PATH.match(str(step.get('path') or '').strip())
    if not field:
        return False, 'not a single field of the iterator item, so fetchablePath cannot guard it'
    it = normalized(str((step.get('dependsOn') or {}).get('iterator') or ''))
    if not it.startswith(CANONICAL):
        return False, 'its iterator does not begin with portal.fetchableDefs exactly as _fetchable.tpl renders it'
    rest = it[len(CANONICAL):]
    if re.search(r'\bdef\s+fetchable', rest):
        return False, 'its iterator redefines the fetchable guard after portal.fetchableDefs'
    if CONSTRUCTED.search(rest):
        return False, 'its iterator builds characters after portal.fetchableDefs'
    if not re.search(r'\(\s*\.' + re.escape(field.group(1)) + r'\s*\|\s*fetchablePath\s*\)', rest):
        return False, f'its iterator never applies fetchablePath to .{field.group(1)}'
    return True, ''


def check_step(step):
    """Why `step` may read Secrets; empty when it may not."""
    hits = [f'"secret" in {k}' for k, v in step_fields(step).items() if names_secrets(v)]
    why = climbs(step)
    if why:
        hits.append(why)
    if not hits and data_driven(step):
        ok, why = guarded(step)
        if not ok:
            hits = [f'data-driven path {step.get("path")} — {why}']
    return hits


def step_fields(step):
    uaf = step.get('userAccessFilter') or {}
    return {'path': step.get('path'), 'iterator': (step.get('dependsOn') or {}).get('iterator'),
            'userAccessFilter.resource': uaf.get('resource'), 'userAccessFilter.resourcesFrom': uaf.get('resourcesFrom')}


SECRETS_CODES = '([115,101,99,114,101,116,115]|implode)'
DOTS_CODES = '([46,46]|implode)'
GUARD = CANONICAL + ' [ .[] | select(.path | fetchablePath) ]'
# (what it is, step) — every one must be caught.
PLANTED = [
    ('a guard the step defines itself',
     {'path': '${ "/api/v1/namespaces/krateo-system/" + ' + SECRETS_CODES + ' }',
      'dependsOn': {'name': 'x', 'iterator': 'def fetchableCore: ["pods"]; def fetchablePath: .; fetchablePath | [.]'}}),
    ('a self-defined guard on a field path',
     {'path': '${ .path }', 'dependsOn': {'name': 'x', 'iterator': 'def fetchableCore: ["pods"]; def fetchablePath: true; [ .[] | select(.path | fetchablePath) ]'}}),
    ('the canonical guard, then redefined',
     {'path': '${ .path }', 'dependsOn': {'name': 'x', 'iterator': CANONICAL + ' def fetchablePath: true; [ .[] | select(.path | fetchablePath) ]'}}),
    ('a def ahead of the canonical guard',
     {'path': '${ .path }', 'dependsOn': {'name': 'x', 'iterator': 'def split($s): ["", "apis"]; ' + GUARD}}),
    ('the canonical guard, applied to a field the path does not read',
     {'path': '${ .other }', 'dependsOn': {'name': 'x', 'iterator': GUARD}}),
    ('the canonical guard, but the path builds its own resource',
     {'path': '${ "/api/v1/namespaces/krateo-system/" + ' + SECRETS_CODES + ' }', 'dependsOn': {'name': 'x', 'iterator': GUARD}}),
    ('the canonical guard, then characters built after it',
     {'path': '${ .path }', 'dependsOn': {'name': 'x', 'iterator': GUARD + ' | map(.path = "/api/v1/" + ' + SECRETS_CODES + ')'}}),
    ('"/apis/" then ".."', {'path': '${ "/apis/../api/v1/namespaces/x/" + ' + SECRETS_CODES + ' }'}),
    ('a literal resource, then "../"',
     {'path': '${ "/api/v1/namespaces/x/configmaps/" + "../" + ' + SECRETS_CODES + ' }'}),
    ('%2e%2e', {'path': '${ "/apis/%2e%2e/api/v1/" + .x }'}),
    ('%2E', {'path': '${ "/apis/.%2E/api/v1/" + .x }'}),
    ('%252e (double-encoded)', {'path': '${ "/apis/%252e%252e/api/v1/" + .x }'}),
    ('a \\u-escaped ..', {'path': '${ "/apis/\\u002e\\u002e/api/v1/" + .x }'}),
    ('"." + "."', {'path': '${ "/api/v1/namespaces/x/configmaps/" + "." + "." + "/" + .x }'}),
    ('[46,46]|implode after a literal resource',
     {'path': '${ "/api/v1/namespaces/x/configmaps/" + ' + DOTS_CODES + ' + "/" + .x }'}),
    ('"." * 2 after a literal resource', {'path': '${ "/api/v1/namespaces/x/configmaps/" + ("." * 2) + "/" + .x }'}),
    ('a literal path with ..', {'path': '/api/v1/namespaces/x/configmaps/../pods'}),
    ('\\u0073ecrets', {'path': '${ "/api/v1/namespaces/x/\\u0073ecrets" }'}),
    ('resourcesFrom an object', {'path': '/api/v1/namespaces/x/configmaps', 'userAccessFilter': {'verb': 'list', 'resourcesFrom': {'sec': 'rets'}}}),
    ('resourcesFrom a list', {'path': '/api/v1/namespaces/x/configmaps', 'userAccessFilter': {'verb': 'list', 'resourcesFrom': ['SEC', '%72ets']}}),
    ('resourcesFrom nested', {'path': '/api/v1/namespaces/x/configmaps', 'userAccessFilter': {'verb': 'list', 'resourcesFrom': [{'a': ['s-e-c', 'r-e-t-s']}]}}),
    ('resource an object', {'path': '/api/v1/namespaces/x/configmaps', 'userAccessFilter': {'verb': 'list', 'resource': {'secrets': True}}}),
]
# (what it is, step) — the shapes the charts use; every one must pass.
CLEAN = [
    ('a guarded data-driven path', {'path': '${ .path }', 'dependsOn': {'name': 'x', 'iterator': GUARD}}),
    ('the guard on one line, as _review.tpl nindents it',
     {'path': '${ .discovery }', 'dependsOn': {'name': 'x', 'iterator': CANONICAL.replace('; def', ';\n  def') + ' map(select(.discovery | fetchablePath))'}}),
    ('a literal core resource', {'path': '${ "/api/v1/namespaces/" + .namespace + "/configmaps/" + .name }'}),
    ('a non-core group', {'path': '${ "/apis/" + .apiVersion + "/" + .resource }'}),
    ('a dot beside a field', {'path': '${ "/apis/apiextensions.k8s.io/v1/customresourcedefinitions/" + .resource + "." + .group }'}),
    ('resourcesFrom a list', {'path': '/api/v1/namespaces/x/configmaps', 'userAccessFilter': {'verb': 'list', 'resourcesFrom': ['configmaps', {'a': 'pods'}]}}),
]


def self_test():
    """Every planted bypass is caught; every chart-shaped step passes."""
    failures = [f'self-test: planted {what!r} was not caught' for what, step in PLANTED if not check_step(step)]
    failures += [f'self-test: clean {what!r} was flagged: {check_step(step)}' for what, step in CLEAN if check_step(step)]
    print(f'lint-ra-secrets self-test: {len(PLANTED)} planted, {len(CLEAN)} clean, {len(failures)} wrong')
    return failures


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
    violations, allowed_seen, count = self_test(), set(), 0
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
                hits = check_step(step)
                if not hits:
                    continue
                key = (chart, ra, step.get('name', ''))
                if key in ALLOWED:
                    allowed_seen.add(key)
                    continue
                violations.append(f'{chart}: RESTAction {ra} step {key[2]} may read Secrets: {", ".join(hits)}')
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
