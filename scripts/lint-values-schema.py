#!/usr/bin/env python3
"""lint-values-schema — helm/portal/values.schema.json must agree with values.yaml.

The chart declares its configuration surface TWICE: as `# @schema` annotation blocks in
values.yaml and as the checked-in values.schema.json that the Krateo installer actually enforces
(it applies the schema's DEFAULTS, not values.yaml's). Nothing regenerated or compared them, so
the two could drift silently — and a drifted schema is not a cosmetic problem here: `helm template`
REJECTS a values file that violates it, and the installer ships whatever defaults the schema says.

The frontend repo has exactly this gate for the same duplication (values-schema-drift.yaml). This
is the portal's equivalent, written as a COMPARISON rather than a generator: the portal's schema is
hand-maintained and carries enum/pattern constraints a generator would flatten.

Checks, in both directions:
  - every key with a default in values.yaml is described by the schema
  - every key the schema describes has a default in values.yaml
  - the default's JSON type matches the type the schema declares

Exit 1 on any disagreement. Run: python3 scripts/lint-values-schema.py
"""
import io
import json
import os
import sys

import yaml

CHART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'helm', 'portal')
JSON_TYPE = {dict: 'object', list: 'array', str: 'string', bool: 'boolean',
             int: 'integer', float: 'number', type(None): 'null'}


def walk(schema, values, path=''):
    problems = []
    props = schema.get('properties') or {}
    if not isinstance(values, dict):
        return problems
    for key, value in values.items():
        here = f'{path}.{key}' if path else key
        if key not in props:
            problems.append(f'{here}: has a default in values.yaml but the schema does not describe it')
            continue
        sub = props[key]
        declared = sub.get('type')
        actual = JSON_TYPE.get(type(value))
        # An integer is an acceptable number; everything else must match exactly.
        if declared and actual and actual != declared and not (declared == 'number' and actual == 'integer'):
            problems.append(f'{here}: values.yaml default is {actual}, schema declares {declared}')
        problems += walk(sub, value, here)
    for key in props:
        if key not in values:
            here = f'{path}.{key}' if path else key
            problems.append(f'{here}: the schema describes it but values.yaml gives no default — '
                            f'the installer applies SCHEMA defaults, so this key ships unset')
    return problems


def main():
    schema = json.load(io.open(os.path.join(CHART, 'values.schema.json'), encoding='utf-8'))
    values = yaml.safe_load(io.open(os.path.join(CHART, 'values.yaml'), encoding='utf-8').read()) or {}
    problems = walk(schema, values)
    for line in problems:
        print(f'  {line}')
    print(f'lint-values-schema: {len(problems)} disagreement(s) between values.yaml and values.schema.json')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
