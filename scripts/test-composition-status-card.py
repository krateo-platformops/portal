#!/usr/bin/env python3
"""The composition detail Status card (approved redesign, step 1), over the chart's OWN jq.

The card answers "is it OK, and if not, what" in one place. Its three computed lines all read
composition-resources, so they must agree with each other and with the bar beside them:

  verdict   one sentence: nothing readable yet / N of M need attention / all M match / K of M ready
  facts     the counts behind it, each named (never colour alone)
  problems  ONLY failed, then drifted, resources, each with its kind, name, state and link

Renders helm/compositions-page, takes each widget's widgetDataTemplate expression exactly as it ships,
and runs it with jq over representative composition-resources outputs. A control re-runs the problems
expression with its filter widened to every resource and requires that to FAIL the healthy case, so the
cases are known to look at the filter.
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


def render():
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', CHART, copy], check=True)
        chart = os.path.join(copy, 'Chart.yaml')
        with open(chart, encoding='utf-8') as handle:
            text = handle.read().replace('CHART_VERSION', '0.0.0')
        with open(chart, 'w', encoding='utf-8') as handle:
            handle.write(text)
        out = subprocess.run(['helm', 'template', 't', copy], capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def program(docs, kind, name, path):
    doc = next(d for d in docs if d.get('kind') == kind and d['metadata']['name'] == name)
    expr = next(t['expression'] for t in doc['spec']['widgetDataTemplate'] if t['forPath'] == path)
    return expr.strip().removeprefix('${').removesuffix('}').strip()


def run(prog, data):
    proc = subprocess.run([JQ, '-c', prog], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(f'jq failed: {proc.stderr.strip()}')
    return json.loads(proc.stdout)


def res(kind, name, state, label, href=''):
    return {'kind': kind, 'name': name, 'state': state, 'stateLabel': label, 'navHref': href}


HEALTHY = {'total': 3, 'healthyCount': 3, 'failedCount': 0, 'driftCount': 0, 'pendingCount': 0, 'convergingCount': 0, 'unreadCount': 0, 'converged': True,
           'resources': [res('Deployment', 'api', 'healthy', 'Healthy'), res('Service', 'api', 'healthy', 'Healthy'), res('Ingress', 'api', 'healthy', 'Healthy')]}
BROKEN = {'total': 9, 'healthyCount': 7, 'failedCount': 1, 'driftCount': 1, 'pendingCount': 0, 'convergingCount': 0, 'unreadCount': 0, 'converged': False,
          'resources': [res('ConfigMap', 'env', 'drift', 'NotSynced', '/resources/team-a/v1/configmaps/env'),
                        res('Deployment', 'api', 'failed', 'Unavailable', '/resources/team-a/apps/v1/deployments/api'),
                        res('Service', 'api', 'healthy', 'Healthy')]}
CONVERGING = {'total': 4, 'healthyCount': 2, 'failedCount': 0, 'driftCount': 0, 'pendingCount': 1, 'convergingCount': 1, 'unreadCount': 1, 'converged': False,
              'resources': [res('Deployment', 'api', 'converging', 'Progressing'), res('Job', 'migrate', 'pending', 'Pending')]}
EMPTY = {'total': 0, 'healthyCount': 0, 'resources': []}


def main():
    docs = render()
    verdict = program(docs, 'Paragraph', 'composition-detail-status-verdict', 'text')
    facts = program(docs, 'Paragraph', 'composition-detail-status-facts', 'text')
    problems = program(docs, 'Listy', 'list-composition-detail-problems', 'dataSource')
    bad = 0

    def check(what, got, want):
        nonlocal bad
        if got != want:
            bad += 1
            print(f'FAIL {what}: got {got!r}, want {want!r}')

    check('verdict healthy', run(verdict, HEALTHY), 'All 3 resources match the desired state')
    check('verdict broken', run(verdict, BROKEN), '2 of 9 resources need attention')
    check('verdict converging', run(verdict, CONVERGING), '2 of 4 resources ready, the rest still converging')
    check('verdict empty', run(verdict, EMPTY), 'This composition manages no resources it can read yet')
    check('facts broken', run(facts, BROKEN), '7 healthy · 1 failed · 1 drifted from the values')
    check('facts converging', run(facts, CONVERGING), '2 healthy · 2 converging · 1 not readable with your access')
    check('problems healthy (none)', run(problems, HEALTHY), [])
    got = run(problems, BROKEN)
    check('problems broken: failed first, then drifted, nothing healthy', [(p['kind'], p['name'], p['state']) for p in got],
          [('Deployment', 'api', 'failed'), ('ConfigMap', 'env', 'drift')])
    check('problems broken: each links to its resource', [p['navHref'] for p in got],
          ['/resources/team-a/apps/v1/deployments/api', '/resources/team-a/v1/configmaps/env'])
    check('problems converging (none: converging is not a problem)', run(problems, CONVERGING), [])

    # Control: with the filter widened to every resource, the healthy case must stop being empty.
    widened = problems.replace('select(.state == "failed")', 'select(true)')
    if widened == problems or run(widened, HEALTHY) == []:
        bad += 1
        print('FAIL control: widening the problems filter did not change the healthy case, so the cases do not look at it')

    print(f'composition status card: {bad} failure(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
