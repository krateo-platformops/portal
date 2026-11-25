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
# Visual audit CMP-2: paused, both children deleted. It read "0 of 2 resources ready, the rest still
# converging", "2 converging", both "Pending", under an amber gauge.
PAUSED_MISSING = {'total': 2, 'healthyCount': 0, 'failedCount': 0, 'driftCount': 0, 'pendingCount': 0, 'convergingCount': 0,
                  'notFoundCount': 2, 'unreadCount': 0, 'converged': False, 'convergencePercent': 0,
                  'composition': {'state': 'paused', 'stateLabel': 'Paused', 'pausedSince': '4 Nov 10:10 UTC', 'readyReason': 'ReconcilePaused'},
                  'resources': [res('ConfigMap', 'acme-feedback-oas', 'notFound', 'NotFound', '/resources/krateo-system/core/v1/configmaps/acme-feedback-oas'),
                                res('RestDefinition', 'acme-feedback-form', 'notFound', 'NotFound')]}
PAUSED_OK = dict(HEALTHY, composition={'state': 'paused', 'pausedSince': '', 'readyReason': 'ReconcilePaused'})
PAUSED_PENDING = dict(CONVERGING, unreadCount=0, composition={'state': 'paused', 'pausedSince': '4 Nov 10:10 UTC'})
# Visual audit CMP-7: nothing can assess it (Ready=Unknown, ZeroEvidence); its one child has no readiness.
UNKNOWN = {'total': 1, 'healthyCount': 0, 'failedCount': 0, 'driftCount': 0, 'pendingCount': 0, 'convergingCount': 0, 'noReadinessCount': 1,
           'unreadCount': 0, 'converged': False, 'convergencePercent': 0,
           'composition': {'state': 'unknown', 'readyReason': 'ZeroEvidence'}, 'resources': [res('Bundle', 'krateo-internal-ca', 'noReadiness', 'NoReadyCondition')]}
# Visual audit CMP-6: 5 of 59 children can be judged; the other 54 publish no readiness. It read
# "All 59 resources match" and "59 healthy".
UNJUDGED = {'total': 59, 'healthyCount': 5, 'failedCount': 0, 'driftCount': 0, 'pendingCount': 0, 'convergingCount': 0, 'noReadinessCount': 54,
            'unreadCount': 0, 'converged': False, 'convergencePercent': 8, 'composition': {'state': 'healthy'}, 'resources': []}
MISSING = dict(PAUSED_MISSING, composition={'state': 'failed'})


def main():
    docs = render()
    verdict = program(docs, 'Paragraph', 'composition-detail-status-verdict', 'text')
    facts = program(docs, 'Paragraph', 'composition-detail-status-facts', 'text')
    problems = program(docs, 'Listy', 'list-composition-detail-problems', 'dataSource')
    rail = program(docs, 'Listy', 'list-composition-detail-rail', 'dataSource')
    rail_map = next(d for d in docs if d.get('kind') == 'Listy' and d['metadata']['name'] == 'list-composition-detail-rail')['spec']['widgetData']['itemTemplate']['bar']['color']['map']
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

    check('verdict paused, its children missing (CMP-2)', run(verdict, PAUSED_MISSING), 'Paused since 4 Nov 10:10 UTC; 2 of 2 resources are missing')
    check('facts paused, missing: missing, never converging', run(facts, PAUSED_MISSING), '0 healthy · 2 missing')
    check('verdict paused, all there', run(verdict, PAUSED_OK), 'Paused; all 3 resources match the desired state')
    check('verdict paused, some unread: nothing is "still converging"', run(verdict, PAUSED_PENDING), 'Paused since 4 Nov 10:10 UTC; 2 of 4 resources ready')
    check('facts paused: pending, not converging', run(facts, PAUSED_PENDING), '2 healthy · 2 pending')
    check('verdict missing, not paused', run(verdict, MISSING), '2 of 2 resources are missing')
    check('verdict unknown health (CMP-7)', run(verdict, UNKNOWN), 'Health Unknown (ZeroEvidence); 0 of 1 resources healthy; 1 has no readiness to check')
    check('verdict: no readiness is neither healthy nor converging (CMP-6)', run(verdict, UNJUDGED), '5 of 59 resources healthy; 54 have no readiness to check')
    check('facts: no readiness named', run(facts, UNJUDGED), '5 healthy · 54 with no readiness to check')
    check('problems: missing first, each with a reason', [(p['name'], p['stateLabel'], p['why']) for p in run(problems, PAUSED_MISSING)],
          [('acme-feedback-oas', 'NotFound', 'Not in the cluster'), ('acme-feedback-form', 'NotFound', 'Not in the cluster')])
    gauge = lambda d: (lambda r: (r['railState'], rail_map.get(r['railState'])))(run(rail, d)[0])
    check('gauge paused: gray, not the amber of progress', gauge(PAUSED_MISSING), ('paused', 'gray'))
    check('gauge unknown health: gray', gauge(UNKNOWN), ('unknown', 'gray'))
    check('gauge missing, not paused: a failure', gauge(MISSING), ('failed', 'red'))
    check('gauge converging: unchanged', gauge(CONVERGING), ('pending', 'orange'))
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
