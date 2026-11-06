#!/usr/bin/env python3
"""The composition header's health pill, over the chart's OWN jq.

The pill has to read Ready's REASON, because the status alone misreads two states:

  Creating   is False, but converging rather than broken. Its workloads are usually unhealthy too,
             so the workloadHealthy branch must not reach it first.
  Paused     is True on a composition nothing is reconciling.

THE STRINGS BELOW ARE THE VALUES core-provider EMITS, copied from childhealth.go and condition.go and
seen live on krateo-057. A Go identifier is not a value: `ReasonReconcilePausedReady` emits
`ReconcilePaused`, and no reason is called `Paused`. A misspelling here would pass silently, because
the fallback for an unrecognised reason is simply the old pill.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHART = os.path.join(REPO, 'helm', 'compositions-page')
JQ = os.environ.get('JQ', 'jq')

# (Ready status, Ready reason, workloadHealthy) -> the pill
CASES = [
    ('True', 'Available', True, 'Ready', 'green'),
    ('True', 'Available', None, 'Ready', 'green'),
    ('True', 'AvailablePartialCoverage', True, 'Ready', 'green'),
    ('True', 'Available', False, 'Degraded', 'orange'),
    ('False', 'Creating', None, 'Creating', 'orange'),
    ('False', 'Creating', False, 'Creating', 'orange'),
    ('True', 'ReconcilePaused', True, 'Paused', 'gray'),
    ('True', 'ReconcileGracefullyPaused', None, 'Paused', 'gray'),
    ('True', 'ReconcilePaused', False, 'Paused', 'gray'),
    ('False', 'Unavailable', None, 'NotReady', 'red'),
    ('False', 'Unavailable', False, 'Unhealthy', 'red'),
    ('Unknown', 'ZeroEvidence', None, 'Unknown', 'gray'),
    ('Unknown', 'ChildrenForbidden', None, 'Unknown', 'gray'),
    (None, None, None, 'Unknown', 'gray'),
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
        proc = subprocess.run(['helm', 'template', 'c', copy, '--set', 'namespace=krateo-composition'],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def tags_program():
    header = next(d for d in render() if d.get('metadata', {}).get('name') == 'composition-detail-page-header')
    expr = next(t['expression'] for t in header['spec']['widgetDataTemplate'] if t['forPath'] == 'tags')
    return expr.strip().removeprefix('${').removesuffix('}').strip()


def pill(program, status, reason, healthy):
    conditions = [] if status is None else [{'type': 'Ready', 'status': status, 'reason': reason}]
    data = {'detail': {'status': {'conditions': conditions}}, 'workloadHealthy': healthy}
    proc = subprocess.run([JQ, '-c', program], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(f'jq failed: {proc.stderr.strip()}')
    return json.loads(proc.stdout)[0]


def main():
    program = tags_program()
    failures = 0
    for status, reason, healthy, label, color in CASES:
        got = pill(program, status, reason, healthy)
        if (got['label'], got['color']) != (label, color):
            failures += 1
            print(f'FAIL Ready={status}/{reason} workloadHealthy={healthy}: '
                  f'got {got["label"]}/{got["color"]}, want {label}/{color}')
    print(f'{len(CASES) - failures}/{len(CASES)} pills ok')
    return min(failures, 125)


if __name__ == '__main__':
    sys.exit(main())
