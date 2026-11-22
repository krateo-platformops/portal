#!/usr/bin/env python3
"""lint-pinned-crds' widgetDataTemplate target walk (undeclared_target), case by case.

The gate checked only what a chart AUTHORS, so a field written through widgetDataTemplate (blueprints-page's
Form `notices`, frontend 1.8.68) passed at a floor that does not declare it. These cases pin what the walk
reports and what it tolerates, with the same rules as undeclared(): a preserve-unknown or opaque node admits
anything below it, additionalProperties admits any name, and an index steps into `items`.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('lint_pinned_crds', os.path.join(HERE, 'lint-pinned-crds.py'))
lint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lint)

WD = {'type': 'object', 'properties': {
    'notice': {'type': 'string'},
    'schema': {'type': 'object', 'x-kubernetes-preserve-unknown-fields': True},
    'labels': {'type': 'object', 'additionalProperties': {'type': 'string'}},
    'payload': {'type': 'object'},
    'actions': {'type': 'object', 'properties': {'rest': {'type': 'array', 'items': {'type': 'object', 'properties': {
        'payload': {'type': 'object', 'x-kubernetes-preserve-unknown-fields': True},
        'payloadToOverride': {'type': 'array', 'items': {'type': 'object', 'properties': {'value': {'type': 'string'}}}}}}}}},
}}

CASES = [
    ('declared top level', 'notice', None),
    ('undeclared top level: the notices case', 'notices', 'widgetData.notices'),
    ('below a preserve-unknown node', 'schema.anything.at.all', None),
    ('a name under additionalProperties', 'labels.team', None),
    ('below an opaque object', 'payload.metadata.labels', None),
    ('indexed into items, declared', 'actions.rest[0].payloadToOverride[2].value', None),
    ('indexed into items, undeclared', 'actions.rest[0].successMesage', 'widgetData.actions.rest[0].successMesage'),
    ('below a preserve-unknown item field', 'actions.rest[0].payload.metadata.annotations', None),
    ('undeclared under a declared object', 'actions.navigate', 'widgetData.actions.navigate'),
]

bad = 0
for label, fp, want in CASES:
    got = lint.undeclared_target(fp, WD)
    if got != want:
        bad += 1
        print(f'FAIL {label}: undeclared_target({fp!r}) = {got!r}, want {want!r}')
print(f'lint-pinned-crds targets: {len(CASES)} cases, {bad} failure(s)')
sys.exit(1 if bad else 0)
