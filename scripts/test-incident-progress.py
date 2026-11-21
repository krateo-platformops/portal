#!/usr/bin/env python3
"""The incident header's progress line (PageHeader `progress`), over the chart's OWN jq.

Diego re-ran an analysis and the page showed "no sign of life" for 17 minutes (rate-limited model
calls, observability). The header now says what is in progress and since when; the browser ticks the
minutes while `live`. This runs restaction.incident-detail's filter, exactly as it ships, over one
incident per state and checks the line it emits:

  Analyzing, re-analysis   "Re-analysis since" status.reanalysis.at, live, with the hint
  Analyzing, first         "Analyzing since" the incident's creation, live, with the hint
  Analyzing, stale reAt    a reanalysis.at older than completedAt is a past one: first-analysis rule
  Verifying                "Checking the fix since" lastChecks.precondition.since, live, no hint
  Open                     "Analyzed at" completedAt, not live
  Resolved                 "Resolved at" resolution.at, not live
  Closed / missing         no line
A control: with the Analyzing branch forced off, the re-analysis case must fail.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.environ.get('INCIDENTS_CHART') or os.path.join(os.path.dirname(HERE), 'helm', 'incidents-page')
JQ = os.environ.get('JQ', 'jq')
HINT = 'Usually 2–3 min; longer when the model is rate-limited.'


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
        out = subprocess.run(['helm', 'template', 't', copy], capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def incident(state, **status):
    return {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Incident',
            'metadata': {'name': 'sre-pod-oomkilled-20261120-153217', 'namespace': 'krateo-system', 'uid': 'u1',
                         'creationTimestamp': '2026-11-20T15:32:17Z',
                         'labels': {'observability.krateo.io/alert': 'sre-pod-oomkilled'}},
            'spec': {'alertRef': {'name': 'sre-pod-oomkilled', 'namespace': 'krateo-system'}, 'trigger': 'alert',
                     'triggeredAt': '2026-11-20T15:32:17Z'},
            'status': dict({'state': state}, **status)}


CASES = [
    ('Analyzing, re-analysis', incident('Analyzing', completedAt='2026-11-21T10:00:00Z',
                                        reanalysis={'at': '2026-11-21T11:54:15Z', 'cause': 'Person', 'count': 4}),
     {'label': 'Re-analysis since', 'since': '2026-11-21T11:54:15Z', 'live': True, 'hint': HINT}),
    ('Analyzing, first', incident('Analyzing'),
     {'label': 'Analyzing since', 'since': '2026-11-20T15:32:17Z', 'live': True, 'hint': HINT}),
    ('Analyzing, a past reanalysis.at', incident('Analyzing', completedAt='2026-11-21T12:11:19Z',
                                                 reanalysis={'at': '2026-11-21T11:54:15Z'}),
     {'label': 'Analyzing since', 'since': '2026-11-20T15:32:17Z', 'live': True, 'hint': HINT}),
    ('Verifying', incident('Verifying', completedAt='2026-11-21T12:11:19Z',
                           lastChecks={'precondition': {'exit': 0, 'since': '2026-11-21T12:11:22Z'}}),
     {'label': 'Checking the fix since', 'since': '2026-11-21T12:11:22Z', 'live': True}),
    ('Open', incident('Open', completedAt='2026-11-21T12:11:19Z'),
     {'label': 'Analyzed at', 'since': '2026-11-21T12:11:19Z', 'live': False}),
    ('Resolved', incident('Resolved', completedAt='2026-11-21T12:11:19Z',
                          resolution={'at': '2026-11-21T12:21:24Z', 'by': 'verify'}),
     {'label': 'Resolved at', 'since': '2026-11-21T12:21:24Z', 'live': False}),
    ('Closed', incident('Closed', completedAt='2026-11-21T12:11:19Z'), None),
]


def run(prog, inc):
    data = {'name': 'sre-pod-oomkilled-20261120-153217', 'namespace': 'krateo-system',
            'incident': inc, 'incidents': {'items': [inc] if inc else []}}
    proc = subprocess.run([JQ, '-c', prog + ' | .headerProgress'], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip())
    return json.loads(proc.stdout)


def main():
    docs = render()
    prog = next(d for d in docs if d['kind'] == 'RESTAction' and d['metadata']['name'] == 'incident-detail')['spec']['filter']
    header = next(d for d in docs if d['kind'] == 'PageHeader' and d['metadata']['name'] == 'incident-detail-page-header')
    bad = 0
    if not any(t['forPath'] == 'progress' for t in header['spec'].get('widgetDataTemplate', [])):
        bad += 1
        print('FAIL the incident PageHeader does not template `progress`')
    for label, inc, want in CASES:
        got = run(prog, inc)
        if got != want:
            bad += 1
            print(f'FAIL {label}: got {got!r}, want {want!r}')
    if run(prog, {}) is not None:
        bad += 1
        print('FAIL a missing incident must have no progress line')
    broken = prog.replace('elif $state == "Analyzing" then', 'elif false then', 1)
    if broken == prog or run(broken, CASES[0][1]) == CASES[0][2]:
        bad += 1
        print('FAIL control: forcing the Analyzing branch off did not change the re-analysis case')
    print(f'incident progress: {len(CASES) + 1} cases, {bad} failure(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
