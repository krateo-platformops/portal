#!/usr/bin/env python3
"""lint-values-schema — every helm/*/values.schema.json must agree with its values.yaml.

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
  - a key an INSTALL must be able to override declares no schema default at all
    (authorization: readerGroups/requesterGroups; per-install policy: deskActions, servicePicker*)

Exit 1 on any disagreement. Run: python3 scripts/lint-values-schema.py
"""
import io
import glob
import json
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# DISCOVERED, not hardcoded. This gate checked `helm/portal` alone for its whole life, so when
# #261 moved every page into its own chart the fourteen new schemas were covered by NOTHING -- the
# `readerGroups` defaults were stripped from all fourteen on a green run of this lint, because it
# never looked at them. Five other gates in this repo had hardcoded chart lists that went stale
# during the same split; this is the sixth.
# builder-publish is excluded for the same reason the other five gates exclude it: it is a
# BLUEPRINT chart that CDC renders with a COMPOSITION's own values, so the installer never
# applies its schema defaults and this check's whole premise is false for it. Its five
# schema-described keys with no values.yaml default are legitimate -- the composition supplies
# them -- and its schema is closed, unlike every chart here.
# DISCOVERED FROM Chart.yaml, NOT FROM values.schema.json — and that distinction is the whole
# reason service-desk-page shipped without a schema at all on green runs of this gate. Globbing
# `helm/*/values.schema.json` means the gate finds charts BY THE ARTIFACT IT CHECKS: a chart with
# no schema is not "failing", it is INVISIBLE. The comment above describes the hardcoded-list
# version of this same defect; replacing a stale list with a glob over the artifact kept the shape
# and only changed how it was spelled.
#
# Chart.yaml is the population. Every chart must have a schema, and a missing one now FAILS.
EXCLUDED = ('builder-publish',)
CHARTS = sorted(os.path.dirname(p) for p in glob.glob(os.path.join(ROOT, 'helm', '*', 'Chart.yaml'))
                if os.path.basename(os.path.dirname(p)) not in EXCLUDED)

# Keys whose value is AUTHORIZATION, not structure. crdgen copies a chart schema `default:` into
# the generated composition CRD, and the apiserver then materializes it into every CR spec -- so a
# default here is injected by the APISERVER and silently beats anything the installer sets through
# componentValues. A CMP tenant grant would have been re-asserted to ["admins"] with the page
# simply vanishing for that tenant. Measured on 057: the live marketplacepages CRD at v1-8-78
# carried `readerGroups` default ["admins"] and the live CR had it materialized with nothing having
# written it. `namespace` is deliberately NOT here: a namespace is structure, and defaulting it
# saves the installer transcribing one per component.
#
# installer `check-fill-defaults` does NOT catch a reintroduced default (confirmed by the installer
# session with a negative control -- it just counts one more defaulted leaf), so this is the only
# gate on either side that fails.
# `deskActions` and the two servicePicker keys are not authorization, but they share the property
# that matters here: AN INSTALL MUST BE ABLE TO OVERRIDE THEM, and a materialized default cannot be
# overridden. `deskActions` is the sharpest case — its entire purpose is to be false in the CMP,
# where assignment comes from Zendesk, so a defaulted `true` is precisely the install that cannot
# turn the buttons off, and the symptom is a write that gets overwritten with nothing erroring.
# An empty-string default is still a default: materialized, it stops an install ENABLING the
# service picker. A key absent from a chart is simply not checked, so listing them is free for the
# other sixteen.
NO_SCHEMA_DEFAULT = ('deskActions', 'readerGroups', 'requesterGroups',
                     'servicePickerName', 'servicePickerNamespace')
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
        if key in NO_SCHEMA_DEFAULT and 'default' in sub:
            problems.append(f'{here}: the schema declares a `default:` for a key AN INSTALL MUST '
                            f'BE ABLE TO OVERRIDE — crdgen copies it into the composition CRD and '
                            f'the apiserver materializes it into every CR, silently beating '
                            f'componentValues. Keep the default in values.yaml only.')
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
    if not CHARTS:
        print('lint-values-schema: FAILED — discovered ZERO charts under helm/*/Chart.yaml. '
              'A run that checked nothing is not a clean run.', file=sys.stderr)
        return 1
    total = 0
    for chart in CHARTS:
        name = os.path.basename(chart)
        path = os.path.join(chart, 'values.schema.json')
        # The check this gate could not make while it discovered charts by this very file. The
        # installer enforces the SCHEMA, so a chart without one cannot be pinned at all: crdgen
        # derives the CompositionDefinition kind from it, and there is nothing to derive from.
        if not os.path.isfile(path):
            print(f'  {name}: has no values.schema.json — the installer enforces the schema and '
                  f'crdgen derives the composition kind from it, so this chart cannot be pinned')
            total += 1
            continue
        schema = json.load(io.open(path, encoding='utf-8'))
        values = yaml.safe_load(io.open(os.path.join(chart, 'values.yaml'), encoding='utf-8').read()) or {}
        problems = walk(schema, values)
        total += len(problems)
        for line in problems:
            print(f'  {name}: {line}')
    print(f'lint-values-schema: {len(CHARTS)} chart(s) checked, '
          f'{total} disagreement(s) between values.yaml and values.schema.json')
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
