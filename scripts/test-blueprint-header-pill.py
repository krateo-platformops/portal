#!/usr/bin/env python3
"""The blueprint header's health pill, over the chart's OWN jq.

A CompositionDefinition's health is Ready plus Synced (core-provider). Its other conditions can be
False on a healthy definition, so they must never make the pill read Degraded:

  AuthnMappingApplied  False/AuthnNotInstalled: no authn CRD on the controller's cluster (core-provider#504)
  ControllerActive     False/ScaledToZero: an idle opted-in controller; Ready reads True/ScaledToZero
  SchemaFullyEnforced  False/ConstraintsNotEnforced: informational (krateo-057's installer CD carries it)
  AuthnDelegation      False/DelegationUnsupported: informational
  ApiRefRBACComplete   False only explains a Synced=False, which Ready/Synced already carry

The old rule was "any condition False -> Degraded", which read a converging definition (Ready
Unknown, or no Ready yet) carrying one of these as Degraded instead of Pending. The cases below pin
the new rule, and the run starts with a control: the old rule, swapped into the same rendered
expression, must FAIL the informational cases, or the cases are not testing the change.

THE STRINGS ARE THE VALUES core-provider EMITS (its condition types and reasons, as core-provider
listed them for this change). A misspelt type would pass silently, because an unknown type is simply
ignored by the new rule.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHART = os.path.join(REPO, 'helm', 'blueprints-page')
JQ = os.environ.get('JQ', 'jq')

NEW_ARM = '($c | map(select(.type == "Ready" and .status == "False")) | length) > 0'
OLD_ARM = '($c | map(select(.status == "False")) | length) > 0'


def c(type_, status, reason=''):
    return {'type': type_, 'status': status, 'reason': reason}


# (name, conditions, label, color). The INFORMATIONAL cases are the ones the old rule got wrong.
CASES = [
    ('no conditions', [], 'Unknown', 'gray'),
    ('healthy', [c('Ready', 'True', 'Available'), c('Synced', 'True', 'ReconcileSuccess')], 'Healthy', 'green'),
    ('drift', [c('Ready', 'True', 'Available'), c('Synced', 'False', 'ReconcileError')], 'Drift', 'violet'),
    ('ready false', [c('Ready', 'False', 'Unavailable'), c('Synced', 'True', 'ReconcileSuccess')], 'Degraded', 'red'),
    ('ready false, synced false', [c('Ready', 'False', 'Unavailable'), c('Synced', 'False', 'ReconcileError'),
                                   c('ApiRefRBACComplete', 'False', 'Missing')], 'Degraded', 'red'),
    ('ready unknown', [c('Ready', 'Unknown', 'NotObserved')], 'Pending', 'orange'),
    ('healthy + authn not installed', [c('Ready', 'True', 'Available'), c('Synced', 'True', 'ReconcileSuccess'),
                                       c('AuthnMappingApplied', 'False', 'AuthnNotInstalled')], 'Healthy', 'green'),
    ('scaled to zero', [c('Ready', 'True', 'ScaledToZero'), c('Synced', 'True', 'ReconcileSuccess'),
                        c('ControllerActive', 'False', 'ScaledToZero')], 'Healthy', 'green'),
    # --- informational: the old rule read each of these as Degraded/red ---
    ('INFORMATIONAL converging + authn not installed', [c('Ready', 'Unknown', 'NotObserved'),
                                                       c('AuthnMappingApplied', 'False', 'AuthnNotInstalled')], 'Pending', 'orange'),
    ('INFORMATIONAL converging + scaled to zero', [c('Ready', 'Unknown', 'NotObserved'),
                                                  c('ControllerActive', 'False', 'ScaledToZero')], 'Pending', 'orange'),
    ('INFORMATIONAL converging + schema not fully enforced', [c('Ready', 'Unknown', 'NotObserved'),
                                                             c('SchemaFullyEnforced', 'False', 'ConstraintsNotEnforced')], 'Pending', 'orange'),
    ('INFORMATIONAL no Ready yet + delegation unsupported', [c('AuthnDelegation', 'False', 'DelegationUnsupported')], 'Pending', 'orange'),
]


def render():
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', CHART, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'b', copy, '--set', 'namespace=krateo-blueprints'],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def tags_program():
    header = next(d for d in render() if d.get('metadata', {}).get('name') == 'blueprint-detail-page-header')
    expr = next(t['expression'] for t in header['spec']['widgetDataTemplate'] if t['forPath'] == 'tags')
    return expr.strip().removeprefix('${').removesuffix('}').strip()


def pill(program, conditions):
    data = {'detail': {'status': {'conditions': conditions}}}
    proc = subprocess.run([JQ, '-c', program], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(f'jq failed: {proc.stderr.strip()}')
    got = json.loads(proc.stdout)[0]
    return got['label'], got['color']


def failures(program, verbose):
    bad = []
    for name, conditions, label, color in CASES:
        got = pill(program, conditions)
        if got != (label, color):
            bad.append(name)
            if verbose:
                print(f'FAIL {name}: got {got[0]}/{got[1]}, want {label}/{color}')
    return bad


def main():
    program = tags_program()
    if program.count(NEW_ARM) != 1:
        print(f'FAIL the Degraded arm is not the Ready=False rule exactly once: {NEW_ARM}')
        return 1
    # Control: the old rule in the same expression must fail every INFORMATIONAL case and nothing else.
    old = failures(program.replace(NEW_ARM, OLD_ARM), verbose=False)
    informational = [name for name, *_ in CASES if name.startswith('INFORMATIONAL')]
    if old != informational:
        print(f'FAIL control: the old "any False" rule failed {old}, expected exactly {informational}')
        return 1
    print(f'[PASS] control: the old "any False" rule fails the {len(informational)} informational cases')
    bad = failures(program, verbose=True)
    print(f'{len(CASES) - len(bad)}/{len(CASES)} blueprint pill cases ok')
    return min(len(bad), 125)


if __name__ == '__main__':
    sys.exit(main())
