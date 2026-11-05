#!/usr/bin/env python3
"""
lint-form-keywords — no form relies on a schema keyword the frontend no longer reads.

WHY THIS EXISTS. Frontend 1.8.18 moved field configuration out of `x-krateo-*` schema keywords and
into the `widgetData` lists of the Form (`autocomplete`, `dependencies`, `displayingDependencies`,
`objectFields`, `suggestions`, `notice`). A keyword left behind fails SILENTLY: the field renders,
nothing errors, and the behaviour is simply absent. service-desk's new-ticket form lost the rule
hiding a required field that way, and its comment went on promising the opposite.

WHAT IT CHECKS:
  1. A rendered Form carries none of RETIRED. A Form's schema is read as written, so the frontend
     sees the keyword directly and ignores it. The list is the way to say it.
  2. No chart or example values.schema.json carries UNTRANSLATED. That file reaches a form through
     `blueprint-formdef`, which translates `x-krateo-options` and `x-krateo-notice` into the lists
     and nothing else, so those two are legal there and the rest do nothing.

WHAT IT DOES NOT CHECK. Charts outside this repo: a blueprint's own values.schema.json is read at
run time. Other `x-krateo-*` names are not form keywords (`x-krateo-cmp-rules` is a validator's), so
the lists below are closed on purpose rather than matching the prefix.

Usage: lint-form-keywords.py. Exit code = findings.
"""
import glob
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('lpc', os.path.join(HERE, 'lint-pinned-crds.py'))
lpc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lpc)

REPO = lpc.REPO
# `blueprint-formdef` translates the first two (see docs/form-options.md).
TRANSLATED = ('x-krateo-options', 'x-krateo-notice')
UNTRANSLATED = ('x-krateo-row-title', 'x-krateo-suggestions', 'x-krateo-visible-when')
RETIRED = TRANSLATED + UNTRANSLATED


def form_findings(chart, docs):
    forms = [d for d in docs if d.get('kind') == 'Form']
    out = []
    for form in forms:
        text = json.dumps(form.get('spec') or {})
        for keyword in RETIRED:
            if keyword in text:
                out.append(f"{chart}: Form/{form['metadata']['name']} carries {keyword}, which the "
                           'frontend does not read. Use the widgetData list for it.')
    return len(forms), out


def keys_in(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from keys_in(value)
    elif isinstance(node, list):
        for value in node:
            yield from keys_in(value)


def schema_findings(path):
    with open(path, encoding='utf-8') as fh:
        keys = set(keys_in(json.load(fh)))
    return [f'{os.path.relpath(path, REPO)}: {keyword} does nothing here: blueprint-formdef does '
            'not translate it.' for keyword in UNTRANSLATED if keyword in keys]


def main():
    findings = []
    forms = 0
    for chart in lpc.CHARTS:
        count, found = form_findings(chart, lpc.render(chart))
        forms += count
        findings += found
    schemas = sorted(glob.glob(os.path.join(REPO, 'helm', '*', 'values.schema.json'))
                     + glob.glob(os.path.join(REPO, 'examples', '**', 'values.schema.json'),
                                 recursive=True))
    for path in schemas:
        findings += schema_findings(path)
    for finding in findings:
        print(f'  {finding}')
    print(f'\nlint-form-keywords: {forms} rendered Form(s) in {len(lpc.CHARTS)} chart(s) and '
          f'{len(schemas)} values.schema.json checked, {len(findings)} finding(s)')
    if not forms or not schemas:
        print('  Nothing was checked, so this is a FAILURE and not a pass.')
        return 1
    return len(findings)


if __name__ == '__main__':
    sys.exit(min(main(), 125))
