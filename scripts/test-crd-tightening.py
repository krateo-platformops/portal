#!/usr/bin/env python3
"""
test-crd-tightening — the comparison in lint-crd-tightening.py, each clause fired and each
NON-finding pinned.

WHY THIS EXISTS BESIDE THE GATE. Five of the gate's six clauses can be controlled end to end by
perturbing a chart and running it, and they were. The sixth cannot: dropping
`x-kubernetes-preserve-unknown-fields` requires a BASELINE that sets it, and no CRD this repo owns
does (widget-crd-schemas-strict is the rule). An uncontrolled clause is a clause that may not work,
so it is exercised here rather than shipped on the strength of reading it.

The negatives matter as much as the positives. This gate is deliberately one-directional — a
LOOSENING is not a finding — and a gate that fired on both directions would be rejected within a
week for noise, taking the tightening check with it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from importlib import import_module

compare = import_module('lint-crd-tightening').compare
PRESERVE = 'x-kubernetes-preserve-unknown-fields'


def hits(old, new):
    found = []
    compare(old, new, 'root', lambda p, why: found.append((p, why)))
    return found


CASES = [
    # --- TIGHTENINGS: each must produce exactly one finding at the stated path ---
    ('a new required name',
     {'type': 'object', 'properties': {'a': {'type': 'string'}}},
     {'type': 'object', 'required': ['a'], 'properties': {'a': {'type': 'string'}}},
     1, 'root'),
    ('an enum value removed',
     {'enum': ['x', 'y', 'z']}, {'enum': ['x', 'y']}, 1, 'root'),
    ('an enum introduced where nothing constrained',
     {'type': 'string'}, {'type': 'string', 'enum': ['x']}, 1, 'root'),
    ('a type change',
     {'type': 'string'}, {'type': 'integer'}, 1, 'root'),
    ('a format introduced',
     {'type': 'string'}, {'type': 'string', 'format': 'date-time'}, 1, 'root'),
    ('a format changed',
     {'type': 'string', 'format': 'date'}, {'type': 'string', 'format': 'date-time'}, 1, 'root'),
    # The clause no chart in this repo can control, which is the reason for this file.
    ('preserve-unknown dropped (true -> absent)',
     {'type': 'object', PRESERVE: True}, {'type': 'object'}, 1, 'root'),
    ('preserve-unknown dropped (true -> false)',
     {'type': 'object', PRESERVE: True}, {'type': 'object', PRESERVE: False}, 1, 'root'),

    # --- BOUNDS: the direction table is where an inverted comparison hides, so every one of the
    #     four keys is pinned in BOTH directions. A `minimum` tightens by rising, a `maximum` by
    #     falling, and getting one of them backwards would silently pass the real tightening while
    #     failing every harmless widening.
    ('minimum raised is a tightening',
     {'type': 'integer', 'minimum': 1}, {'type': 'integer', 'minimum': 5}, 1, 'root'),
    ('minimum LOWERED is a loosening',
     {'type': 'integer', 'minimum': 5}, {'type': 'integer', 'minimum': 1}, 0, None),
    ('maximum lowered is a tightening',
     {'type': 'integer', 'maximum': 100}, {'type': 'integer', 'maximum': 10}, 1, 'root'),
    ('maximum RAISED is a loosening',
     {'type': 'integer', 'maximum': 10}, {'type': 'integer', 'maximum': 100}, 0, None),
    ('minLength raised is a tightening',
     {'type': 'string', 'minLength': 0}, {'type': 'string', 'minLength': 1}, 1, 'root'),
    ('minLength LOWERED is a loosening',
     {'type': 'string', 'minLength': 1}, {'type': 'string', 'minLength': 0}, 0, None),
    ('maxLength lowered is a tightening',
     {'type': 'string', 'maxLength': 255}, {'type': 'string', 'maxLength': 64}, 1, 'root'),
    ('maxLength RAISED is a loosening',
     {'type': 'string', 'maxLength': 64}, {'type': 'string', 'maxLength': 255}, 0, None),
    ('a bound introduced where none existed is a tightening',
     {'type': 'string'}, {'type': 'string', 'maxLength': 64}, 1, 'root'),
    ('a bound REMOVED is a loosening',
     {'type': 'string', 'maxLength': 64}, {'type': 'string'}, 0, None),
    ('an unchanged bound says nothing',
     {'type': 'integer', 'minimum': 1, 'maximum': 10},
     {'type': 'integer', 'minimum': 1, 'maximum': 10}, 0, None),

    # --- PATTERN: reported on any change, because regex containment is undecidable ---
    ('a pattern introduced is a tightening',
     {'type': 'string'}, {'type': 'string', 'pattern': '^a+$'}, 1, 'root'),
    ('a pattern CHANGED is reported, even when it may be a widening',
     {'type': 'string', 'pattern': '^a$'}, {'type': 'string', 'pattern': '^a+$'}, 1, 'root'),
    ('a pattern removed is a loosening',
     {'type': 'string', 'pattern': '^a+$'}, {'type': 'string'}, 0, None),

    # --- A REMOVED PROPERTY: the one clause whose harm is silent data loss, not a refused write ---
    ('a property removed is a tightening',
     {'type': 'object', 'properties': {'a': {'type': 'string'}, 'b': {'type': 'string'}}},
     {'type': 'object', 'properties': {'a': {'type': 'string'}}}, 1, 'root'),
    ('a property removed under preserve-unknown is NOT a finding',
     {'type': 'object', PRESERVE: True, 'properties': {'a': {'type': 'string'}}},
     {'type': 'object', PRESERVE: True, 'properties': {}}, 0, None),
    ('a property removed nested two levels down is found, with its path',
     {'properties': {'a': {'type': 'object',
                           'properties': {'b': {'type': 'string'}, 'c': {'type': 'string'}}}}},
     {'properties': {'a': {'type': 'object', 'properties': {'b': {'type': 'string'}}}}},
     1, 'root.a'),

    # --- LOOSENINGS AND NO-OPS: each must produce NOTHING ---
    ('a required name dropped is a loosening',
     {'required': ['a'], 'properties': {'a': {'type': 'string'}}},
     {'properties': {'a': {'type': 'string'}}}, 0, None),
    ('an enum value added is a loosening',
     {'enum': ['x']}, {'enum': ['x', 'y']}, 0, None),
    ('an enum removed entirely is a loosening',
     {'type': 'string', 'enum': ['x']}, {'type': 'string'}, 0, None),
    ('a format removed is a loosening',
     {'type': 'string', 'format': 'date-time'}, {'type': 'string'}, 0, None),
    ('preserve-unknown gained is a loosening',
     {'type': 'object'}, {'type': 'object', PRESERVE: True}, 0, None),
    ('a NEW property is not a finding — nothing stored uses it',
     {'type': 'object', 'properties': {'a': {'type': 'string'}}},
     {'type': 'object', 'properties': {'a': {'type': 'string'}, 'b': {'type': 'string'}}},
     0, None),
    ('identical schemas say nothing',
     {'type': 'object', 'required': ['a'], 'properties': {'a': {'type': 'string', 'enum': ['x']}}},
     {'type': 'object', 'required': ['a'], 'properties': {'a': {'type': 'string', 'enum': ['x']}}},
     0, None),
    ('a type ABSENT on one side is not a change — an untyped node is not a retype',
     {'properties': {'a': {}}}, {'properties': {'a': {'type': 'string'}}}, 0, None),

    # --- RECURSION: the walk must reach nested nodes, items, and additionalProperties ---
    ('a tightening nested two levels down is found, with its path',
     {'properties': {'a': {'properties': {'b': {'type': 'string'}}}}},
     {'properties': {'a': {'properties': {'b': {'type': 'integer'}}}}},
     1, 'root.a.b'),
    ('a tightening inside array items is found',
     {'type': 'array', 'items': {'type': 'object', 'properties': {'a': {'type': 'string'}}}},
     {'type': 'array', 'items': {'type': 'object', 'required': ['a'],
                                 'properties': {'a': {'type': 'string'}}}},
     1, 'root[]'),
    ('a tightening inside additionalProperties is found',
     {'type': 'object', 'additionalProperties': {'type': 'string'}},
     {'type': 'object', 'additionalProperties': {'type': 'integer'}},
     1, 'root{*}'),
    # The 1.8.20 defect itself, in the shape it shipped: a required name on items.
    ('the 1.8.20 shape — items gains required',
     {'properties': {'resourcesRefsTemplate': {
         'type': 'array', 'items': {'type': 'object', 'properties': {
             'iterator': {'type': 'string'}, 'template': {'type': 'object'}}}}}},
     {'properties': {'resourcesRefsTemplate': {
         'type': 'array', 'items': {'type': 'object', 'required': ['id'], 'properties': {
             'iterator': {'type': 'string'}, 'template': {'type': 'object'}}}}}},
     1, 'root.resourcesRefsTemplate[]'),
]


HINT_CASES = [
    # The removed-property finding carries a QUERY, not an answer: the count of live objects still
    # setting the field is what tells a reviewer a dead field from a load-bearing one, and both look
    # identical in a diff. The count needs a cluster, which CI does not have and should not require.
    ('a top-level spec property', 'tickets.servicedesk.krateo.io/v1alpha1.spec', ['onBehalfOf'],
     "kubectl get tickets.servicedesk.krateo.io -A -o json | jq '[.items[] "
     "| select(.spec.onBehalfOf != null)] | length'"),
    ('nested, with an array on the way — [] becomes jq\'s []?',
     'buttons.widgets.templates.krateo.io/v1beta1.spec.resourcesRefsTemplate[]', ['iterator'],
     "select(.spec.resourcesRefsTemplate[]?.iterator != null)"),
    ('an object map — {*} also becomes []?, which iterates values in jq',
     'x.example.io/v1.spec.data{*}', ['k'], "select(.spec.data[]?.k != null)"),
]

# A hint that is wrong is worse than none, because it looks like evidence.
NO_HINT = [
    ('a path with no CRD segment', 'no-slash-in-this-path', ['a']),
    ('a path that would break the shell quoting', "x.example.io/v1.spec.we'ird", ['a']),
]


def main():
    failed = 0
    live_count_hint = import_module('lint-crd-tightening').live_count_hint
    for label, path, lost, want in HINT_CASES:
        got = live_count_hint(path, lost)
        if want not in got:
            print(f'FAIL hint {label}: want substring {want!r}, got {got!r}')
            failed += 1
    for label, path, lost in NO_HINT:
        got = live_count_hint(path, lost)
        if got != '':
            print(f'FAIL hint {label}: must produce no hint, got {got!r}')
            failed += 1
    for label, old, new, expect, path in CASES:
        found = hits(old, new)
        if len(found) != expect:
            print(f'FAIL {label}: expected {expect} finding(s), got {len(found)}: '
                  f'{[f"{p} {w[:60]}" for p, w in found]}')
            failed += 1
        elif path is not None and found and found[0][0] != path:
            print(f'FAIL {label}: finding is at {found[0][0]!r}, expected {path!r}')
            failed += 1
    total = len(CASES) + len(HINT_CASES) + len(NO_HINT)
    print(f'{total - failed} of {total} cases pass')
    return failed


if __name__ == '__main__':
    sys.exit(main())
