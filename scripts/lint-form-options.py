#!/usr/bin/env python3
"""
lint-form-options — a RESTAction that feeds a form field's options never splices a typed value into
a path unescaped, and never reads Secrets.

WHAT IT GUARDS. A schema field with `x-krateo-options` (frontend: widgets/Form/fieldOptions.ts)
calls its RESTAction with the value of another field, or with what the person is typing, in
`?extras` — `{"region": "eu-west-1"}`, `{"q": "pay"}`. snowplow hands those extras to the
RESTAction's jq as top-level keys, and a step path built from them is a request to the API server
AS THE CALLER. A path like
    ${ "/api/v1/namespaces/" + .namespace + "/configmaps" }
turns a typed `default/secrets/x?` into a different request; `(.namespace | @uri)` keeps it one
path segment. An options source also never reads Secrets: whatever it fetches lands in snowplow's
cache and, through the filter, in a dropdown.

WHAT THIS CHECKS, on every RESTAction annotated `krateo.io/form-options: "true"` that any chart in
helm/ renders:
  1. Every `.key` a step path (or its iterator) reads is set to a hostile value — `/`, `..`, `?`
     and `#` — and the step evaluated the way snowplow would (iterator, then the path per element,
     or once against the extras). A dialled path that still contains that value, or a `?`/`#` it
     did not have before, is an error: the key needs `@uri`.
  2. No step path names `/secrets`.
  3. The final filter, over steps that answered with nothing, still yields a LIST — the shape the
     field reads (an object or a scalar reads as a failed load).

Uses the `jq` binary (JQ=gojq to use gojq). Usage: lint-form-options.py. Exit code = findings.
"""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

ANNOTATION = 'krateo.io/form-options'
HOSTILE = 'x/../y?z=1#w'
# `.key` at the start of a jq term — not `.a.b`'s `.b`, not a string's content.
KEY = re.compile(r'(?<![\w\]\)."])\.([A-Za-z_][A-Za-z0-9_]*)')


def query(s):
    s = (s or '').strip()
    return s[2:-1] if s.startswith('${') and s.endswith('}') else None


def keys_read(*exprs):
    strip_strings = re.compile(r'"(?:\\.|[^"\\])*"')
    out = set()
    for expr in exprs:
        if expr:
            out.update(KEY.findall(strip_strings.sub('""', expr)))
    return sorted(out)


def dialled(step, extras):
    """The paths snowplow would dial for `step` given `extras` (no upstream steps resolved)."""
    path_expr = query(step.get('path'))
    iterator = (step.get('dependsOn') or {}).get('iterator')
    if iterator:
        try:
            items = tbi.jq(iterator, extras)
        except RuntimeError:
            return []
        items = items if isinstance(items, list) else []
    else:
        items = [extras]
    if path_expr is None:
        return [step.get('path') or ''] * len(items)
    paths = []
    for item in items:
        try:
            paths.append(tbi.jq(path_expr, item))
        except RuntimeError as exc:
            paths.append(f'<jq error: {exc}>')
    return paths


def check(ra):
    findings = []
    name = ra['metadata']['name']
    steps = ra['spec'].get('api') or []
    for step in steps:
        label = f'{name}/{step.get("name")}'
        if '/secrets' in (step.get('path') or ''):
            findings.append(f'{label}: an options source must not read Secrets')
        path_expr = query(step.get('path'))
        if path_expr is None:
            continue
        iterator = (step.get('dependsOn') or {}).get('iterator')
        for key in keys_read(path_expr, iterator):
            for path in dialled(step, {key: HOSTILE}):
                if not isinstance(path, str):
                    continue
                if HOSTILE in path or '/../' in path or ('?z=1' in path) or path.endswith('#w'):
                    findings.append(f'{label}: `.{key}` reaches the path unescaped ({path!r}) — pipe it through @uri')
                    break
    try:
        out = tbi.jq(ra['spec']['filter'], {}) if ra['spec'].get('filter') else None
    except RuntimeError as exc:
        findings.append(f'{name}: the filter fails when its steps answer nothing: {exc}')
    else:
        if not isinstance(out, list):
            findings.append(f'{name}: the filter yields {type(out).__name__}, not a list of options')
    return findings


def main():
    docs = tbi.render_portal_and_pages()
    sources = [d for d in docs if d.get('kind') == 'RESTAction'
               and ((d.get('metadata') or {}).get('annotations') or {}).get(ANNOTATION) == 'true']
    findings = []
    for ra in sources:
        findings += check(ra)
    for f in findings:
        print(f'ERROR {f}')
    print(f'{len(sources)} options source(s) checked, {len(findings)} finding(s) ({tbi.JQ})')
    return len(findings)


if __name__ == '__main__':
    sys.exit(main())
