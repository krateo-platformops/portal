#!/usr/bin/env python3
"""
test-incident-apply — the incident page's Apply button, evaluated end to end.

WHAT IT GUARDS. An Incident's status.howToFix.applyAction (alert-provider >= 0.2.45) is the fix as ONE
Kubernetes API write: verb (patch | create | delete), apiVersion, resource, namespace, name, payload.
The "Review & apply" step offers Apply for it: one `ops` set that sends exactly that write through
snowplow /call as the signed-in user, then marks the incident applied (spec.applied: true) the way
"I applied it" does. The rules, each pinned below by what a reader would see:
  - Apply shows only for an Open incident with an actionable applyAction (a known verb, apiVersion,
    resource and name, and a payload object unless it deletes) and no click waiting for the
    controller; "I applied it" stays for a fix that is only a script, and never shows beside Apply;
  - the first op targets exactly the applyAction's object with the HTTP method its verb names, and
    carries its payload unchanged (none for a delete); the second PATCHes this incident with
    {spec: {applied: true}};
  - only the ref the verb names is emitted, so a widget never carries a target it will not write;
  - the step's text names the verb, the object and, for a patch, each field it sets;
  - a composition (composition.krateo.io) and a Deployment both work, and so does the delete
    krateo-057 carries today;
  - a fix that is only a script offers Run apply beside "I applied it" when incidentapplies can be
    listed and no run of this incident is unfinished: one POST of an IncidentApply naming only the
    incident, to `<incident>-apply-<unix seconds>` in its namespace, that waits for its ApplyFinished
    Event and shows its message;
  - the Apply runs tab lists this incident's runs (and no other's), newest first, with each output;
  - Check history shows an apply check's exit when it has one, and "applied by hand" when not.

HOW. helm/portal is rendered, incident-detail is resolved over fixtures shaped like krateo-057's
Incidents by test-platform-review's model of snowplow's resolver, and the remediation widgets are
evaluated over its output. A resourcesRefsTemplate entry runs its iterator, then its template over
each element, as snowplow's resourcesrefstemplate.Resolve does.

With --crds DIR (a frontend-crds chart), every widget resolved here is validated against its CRD.
Usage: test-incident-apply.py [--crds DIR]. Exit code = failed checks.
"""
import copy
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(mod, file):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tpr = _load('tpr', 'test-platform-review.py')
tbi = tpr.tbi
NS = tbi.NS
expr, expect = tpr.expr, tpr.expect
WIDGETS = (('Flex', 'incident-rem-step-1'), ('Markdown', 'incident-rem-step-md-1'),
           ('Button', 'incident-rem-apply'), ('Button', 'incident-rem-applied'),
           ('Flex', 'incident-rem-script-actions'), ('Button', 'incident-rem-run-apply'),
           ('Tabs', 'incident-detail-tabs'), ('Table', 'incident-checks'),
           ('Table', 'incident-apply-runs'), ('Markdown', 'incident-apply-runs-output'))


def incident(name, state='Open', apply_action=None, applied=False, apply_script=True):
    fix = {'precondition': '#!/usr/bin/env bash\n# Holds while the alert fires.\nexit 1\n',
           'verify': '#!/usr/bin/env bash\n# Fixed once it stops.\nexit 0\n'}
    if apply_script:
        fix['apply'] = '#!/usr/bin/env bash\n# Patch the object.\nset -euo pipefail\nkubectl patch ...\n'
    if apply_action is not None:
        fix['applyAction'] = apply_action
    spec = {'alertRef': {'name': 'sre-pod-crashloop', 'namespace': NS}, 'trigger': 'alert',
            'triggeredAt': '2026-10-05T07:00:21Z'}
    if applied:
        spec['applied'] = True
    return {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Incident',
            'metadata': {'name': name, 'namespace': NS, 'creationTimestamp': '2026-10-05T07:00:21Z',
                         'labels': {'observability.krateo.io/alert': 'sre-pod-crashloop'}},
            'spec': spec,
            'status': {'state': state, 'howToFix': fix,
                       'rootCause': {'category': 'config', 'confidence': '0.85', 'statement': 'The replica count is too low.'},
                       'checks': [{'at': '2026-10-05T08:32:08Z', 'exit': 1, 'script': 'precondition'}],
                       'conditions': [{'type': 'Reproduced', 'status': 'True'}],
                       'analyzedResources': [{'gvr': 'apps/v1/deployments', 'name': 'web', 'namespace': 'demo-system',
                                              'whatWasRead': 'status'}]}}


COMPOSITION = {'verb': 'patch', 'apiVersion': 'composition.krateo.io/v1-2-0', 'resource': 'githubscaffoldingwithcompositionpages',
               'namespace': 'demo-system', 'name': 'demo-app', 'payload': {'spec': {'app': {'replicaCount': 2}}}}
DEPLOYMENT = {'verb': 'patch', 'apiVersion': 'apps/v1', 'resource': 'deployments', 'namespace': 'demo-system',
              'name': 'web', 'payload': {'spec': {'template': {'spec': {'containers': [{'name': 'web', 'image': 'nginx:1.27'}]}}}}}
# As krateo-057 carries it (krateo-platform-crashloop-20261004-214557).
POD_DELETE = {'verb': 'delete', 'apiVersion': 'v1', 'resource': 'pods', 'namespace': NS,
              'name': 'otel-collector-daemonset-opentelemetry-collector-agent-hmsp4'}


# The incidentapplies list when the CRD is not installed: snowplow keeps the error, no list.
NO_CRD = 'ERROR:the server could not find the requested resource'


def run(incident_name, name, phase=None, exit_code=None, started='2026-10-05T09:00:00Z', output='', message='',
        script=None, user='admin'):
    status = {}
    if phase:
        status = {'phase': phase, 'startedAt': started, 'message': message, 'output': output}
        if exit_code is not None:
            status['exitCode'] = exit_code
        if script is not None:
            status['script'] = script
    return {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'IncidentApply',
            'metadata': {'name': name, 'namespace': NS, 'creationTimestamp': started},
            'spec': {'incidentRef': {'name': incident_name}, 'requestedBy': {'username': user, 'groups': ['admins']}},
            'status': status}


def runs_list(*items):
    return {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'IncidentApplyList', 'items': list(items)}


def resolve(chart, inc, label, applies=NO_CRD):
    ex = {'name': inc['metadata']['name'], 'namespace': NS}
    out = tpr.resolved(chart, 'incident-detail', {'incident': inc, 'incidents': {'items': [inc]},
                                                   'incidentApplies': applies,
                                                   'alert': 'ERROR:alerts "sre-pod-crashloop" not found'}, ex)
    w = {n: tbi.resolved_widget(chart, k, n, out, ex, f'{label}: {k} {n}')['spec'].get('widgetData', {})
         for k, n in WIDGETS}
    return out, w


def refs(chart, kind, name, output):
    """A widget's resourcesRefsTemplate, resolved: iterator (when set), then the template per element."""
    got = []
    for entry in chart.get(kind, name)['spec'].get('resourcesRefsTemplate') or []:
        els = tbi.jq(expr(entry['iterator']), output) if entry.get('iterator') else [output]
        for el in els if isinstance(els, list) else []:
            got.append({k: (tbi.jq(expr(v), el) if isinstance(v, str) and expr(v) is not None else v)
                        for k, v in entry['template'].items()})
    return got


def items(w, flex='incident-rem-step-1'):
    return [i['resourceRefId'] for i in w[flex]['items']]


def check_apply_patches_a_composition(chart):
    f = []
    out, w = resolve(chart, incident('comp-1', apply_action=COMPOSITION), 'composition')
    expect(f, 'Apply, not "I applied it"', items(w), ['incident-rem-step-md-1', 'incident-rem-apply'])
    act = w['incident-rem-apply']['actions']['rest'][0]
    expect(f, 'ops', act['ops'], [{'resourceRefId': 'apply-target-patch', 'payload': COMPOSITION['payload']},
                                   {'resourceRefId': 'apply-mark-incident', 'payload': {'spec': {'applied': True}}}])
    expect(f, 'top-level ref is the first op', act['resourceRefId'], 'apply-target-patch')
    r = {x['id']: x for x in refs(chart, 'Button', 'incident-rem-apply', out)}
    expect(f, 'only the patch target and the incident', sorted(r), ['apply-mark-incident', 'apply-target-patch'])
    expect(f, 'target', {k: r['apply-target-patch'][k] for k in ('apiVersion', 'resource', 'name', 'namespace', 'verb')},
           {'apiVersion': COMPOSITION['apiVersion'], 'resource': COMPOSITION['resource'], 'name': 'demo-app',
            'namespace': 'demo-system', 'verb': 'PATCH'})
    expect(f, 'incident', {k: r['apply-mark-incident'][k] for k in ('apiVersion', 'resource', 'name', 'namespace', 'verb')},
           {'apiVersion': 'observability.krateo.io/v1alpha1', 'resource': 'incidents', 'name': 'comp-1',
            'namespace': NS, 'verb': 'PATCH'})
    md = w['incident-rem-step-md-1']['markdown']
    want = ('**update** `githubscaffoldingwithcompositionpages/demo-app` in `demo-system`, '
            'setting `spec.app.replicaCount` to `2`')
    if want not in md:
        f.append(f'apply step does not say what it writes: {md[:300]!r}')
    expect(f, 'apply step in progress', out['fixStepsForWidget'][1]['status'], 'process')
    return f


def check_apply_patches_a_deployment(chart):
    f = []
    out, w = resolve(chart, incident('dep-1', apply_action=DEPLOYMENT), 'deployment')
    expect(f, 'Apply', items(w), ['incident-rem-step-md-1', 'incident-rem-apply'])
    ops = w['incident-rem-apply']['actions']['rest'][0]['ops']
    expect(f, 'payload unchanged', ops[0]['payload'], DEPLOYMENT['payload'])
    r = [x for x in refs(chart, 'Button', 'incident-rem-apply', out) if x['id'] == 'apply-target-patch']
    expect(f, 'target', [(x['apiVersion'], x['resource'], x['name'], x['namespace'], x['verb']) for x in r],
           [('apps/v1', 'deployments', 'web', 'demo-system', 'PATCH')])
    md = w['incident-rem-step-md-1']['markdown']
    if '`spec.template.spec.containers` to `[{"image":"nginx:1.27","name":"web"}]`' not in md \
            and '`spec.template.spec.containers` to `[{"name":"web","image":"nginx:1.27"}]`' not in md:
        f.append(f'an array is one field, set whole: {md[:400]!r}')
    return f


def check_apply_deletes_without_a_body(chart):
    f = []
    out, w = resolve(chart, incident('del-1', apply_action=POD_DELETE), 'delete')
    ops = w['incident-rem-apply']['actions']['rest'][0]['ops']
    expect(f, 'delete op has no payload', ops[0], {'resourceRefId': 'apply-target-delete'})
    r = [x for x in refs(chart, 'Button', 'incident-rem-apply', out) if x['id'].startswith('apply-target-')]
    expect(f, 'one DELETE target', [(x['id'], x['resource'], x['name'], x['verb']) for x in r],
           [('apply-target-delete', 'pods', POD_DELETE['name'], 'DELETE')])
    if '**delete** `pods/' + POD_DELETE['name'] + '` in `' + NS + '`' not in w['incident-rem-step-md-1']['markdown']:
        f.append('the delete is not named in the step')
    create = dict(POD_DELETE, verb='create', resource='configmaps', name='x',
                  payload={'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': 'x'}, 'data': {'a': 'b'}})
    out, w = resolve(chart, incident('cre-1', apply_action=create), 'create')
    r = [x for x in refs(chart, 'Button', 'incident-rem-apply', out) if x['id'].startswith('apply-target-')]
    expect(f, 'create is a POST', [(x['id'], x['verb']) for x in r], [('apply-target-post', 'POST')])
    expect(f, 'create body unchanged', w['incident-rem-apply']['actions']['rest'][0]['ops'][0]['payload'], create['payload'])
    return f


def check_script_only_keeps_i_applied_it(chart):
    f = []
    out, w = resolve(chart, incident('scr-1'), 'script-only')
    expect(f, 'script buttons', items(w), ['incident-rem-step-md-1', 'incident-rem-script-actions'])
    expect(f, 'no incidentapplies CRD: "I applied it" only', items(w, 'incident-rem-script-actions'), ['incident-rem-applied'])
    expect(f, 'no target ref', [x['id'] for x in refs(chart, 'Button', 'incident-rem-apply', out)], ['apply-mark-incident'])
    for bad, why in ((dict(COMPOSITION, verb='scale'), 'unknown verb'), (dict(COMPOSITION, name=''), 'no name'),
                     ({k: v for k, v in COMPOSITION.items() if k != 'payload'}, 'patch without a payload')):
        out, w = resolve(chart, incident('bad-1', apply_action=bad), why)
        expect(f, f'{why}: script path', items(w), ['incident-rem-step-md-1', 'incident-rem-script-actions'])
        expect(f, f'{why}: no applyAction', out['applyAction'], None)
    return f


def check_no_button_once_applied_or_closed(chart):
    f = []
    for label, inc in (('applied', incident('ap-1', apply_action=COMPOSITION, applied=True)),
                       ('resolved', incident('rs-1', state='Resolved', apply_action=COMPOSITION)),
                       ('closed', incident('cl-1', state='Closed', apply_action=COMPOSITION)),
                       ('verifying', incident('vf-1', state='Verifying', apply_action=COMPOSITION))):
        out, w = resolve(chart, inc, label)
        expect(f, f'{label}: no button', items(w), ['incident-rem-step-md-1'])
    # applyAction without an apply script still renders the fix section and offers Apply.
    out, w = resolve(chart, incident('na-1', apply_action=COMPOSITION, apply_script=False), 'no-script')
    expect(f, 'no script: fix section', out['sections']['fix'], True)
    expect(f, 'no script: Apply', items(w), ['incident-rem-step-md-1', 'incident-rem-apply'])
    return f


def check_run_apply_posts_an_incidentapply(chart):
    f = []
    out, w = resolve(chart, incident('scr-2'), 'run-apply', runs_list())
    expect(f, 'Run apply beside "I applied it"', items(w, 'incident-rem-script-actions'),
           ['incident-rem-run-apply', 'incident-rem-applied'])
    acts = w['incident-rem-run-apply']['actions']['rest']
    expect(f, 'one rest action', len(acts), 1)
    act = acts[0]
    expect(f, 'body names only the incident', act['payload'],
           {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'IncidentApply',
            'spec': {'incidentRef': {'name': 'scr-2'}}})
    expect(f, 'json content type', act['headers'], ['Content-Type: application/json'])
    expect(f, 'waits for ApplyFinished, then reopens the incident', act['onEventNavigateTo'],
           {'eventReason': 'ApplyFinished', 'timeout': 180, 'loadingMessage': 'Running the apply script as you…',
            'url': f'/incidents/{NS}/scr-2'})
    expect(f, 'shows the Event\'s message', act['successMessage'], '${ .event.message }')
    for k in ('ops', 'fanOutPath', 'onSuccessNavigateTo', 'payloadToOverride'):
        if k in act:
            f.append(f'{k} cannot ride with onEventNavigateTo: {act[k]!r}')
    r = refs(chart, 'Button', 'incident-rem-run-apply', out)
    expect(f, 'one ref', [x['id'] for x in r], ['run-apply-incident'])
    expect(f, 'POST to incidentapplies in the incident\'s namespace',
           {k: r[0][k] for k in ('apiVersion', 'resource', 'namespace', 'verb')},
           {'apiVersion': 'observability.krateo.io/v1alpha1', 'resource': 'incidentapplies', 'namespace': NS, 'verb': 'POST'})
    if not re.fullmatch(r'scr-2-apply-[0-9]+', str(r[0]['name'])):
        f.append(f'name is not <incident>-apply-<unix seconds>: {r[0]["name"]!r}')
    if '**Run apply**' not in w['incident-rem-step-md-1']['markdown']:
        f.append('the apply step does not mention Run apply')

    # Another incident's unfinished run does not hide it; this incident's does, and the step says so.
    out, w = resolve(chart, incident('scr-2'), 'other run', runs_list(run('scr-9', 'scr-9-apply-1', 'Running')))
    expect(f, "another incident's run", items(w, 'incident-rem-script-actions'), ['incident-rem-run-apply', 'incident-rem-applied'])
    for label, r in (('running', run('scr-2', 'scr-2-apply-1', 'Running')), ('not started', run('scr-2', 'scr-2-apply-1'))):
        out, w = resolve(chart, incident('scr-2'), label, runs_list(r))
        expect(f, f'{label}: no Run apply', items(w, 'incident-rem-script-actions'), ['incident-rem-applied'])
        if 'Run apply in progress, as admin' not in w['incident-rem-step-md-1']['markdown']:
            f.append(f'{label}: the apply step does not say a run is in progress')
    out, w = resolve(chart, incident('scr-2'), 'finished', runs_list(run('scr-2', 'scr-2-apply-1', 'Failed', 1)))
    expect(f, 'a finished run does not hide it', items(w, 'incident-rem-script-actions'), ['incident-rem-run-apply', 'incident-rem-applied'])
    if 'Run apply failed · exit 1' not in w['incident-rem-step-md-1']['markdown']:
        f.append('the apply step does not name the failed run')
    out, w = resolve(chart, incident('scr-2'), '403', 'ERROR:incidentapplies is forbidden')
    expect(f, 'a 403 hides it', items(w, 'incident-rem-script-actions'), ['incident-rem-applied'])
    out, w = resolve(chart, incident('scr-2', apply_action=COMPOSITION), 'applyAction', runs_list())
    expect(f, 'an applyAction offers Apply, not Run apply', items(w), ['incident-rem-step-md-1', 'incident-rem-apply'])
    return f


def check_apply_runs_tab(chart):
    f = []
    out, w = resolve(chart, incident('scr-3'), 'no runs', runs_list())
    expect(f, 'no runs: no tab', out['sections']['applyRuns'], False)
    if 'incident-apply-runs-flex' in items(w, 'incident-detail-tabs'):
        f.append('no runs: the tab shows')
    changed = '#!/usr/bin/env bash\nkubectl scale deploy/web --replicas=2\n'
    applies = runs_list(
        run('scr-3', 'scr-3-apply-1', 'Rejected', None, '2026-10-05T09:00:00Z', '', 'another apply is running for this incident'),
        run('scr-3', 'scr-3-apply-3', 'Succeeded', 0, '2026-10-05T09:20:00Z', 'deployment.apps/web patched\n',
            'the apply script exited 0', incident('x')['status']['howToFix']['apply']),
        run('other', 'other-apply-1', 'Succeeded', 0, '2026-10-05T09:30:00Z'),
        run('scr-3', 'scr-3-apply-2', 'Failed', 1, '2026-10-05T09:10:00Z', 'Error from server (Forbidden): ```x```',
            'the apply script exited 1', changed, user='sre-1'))
    out, w = resolve(chart, incident('scr-3'), 'runs', applies)
    expect(f, 'tab', out['sections']['applyRuns'], True)
    expect(f, 'tab label', out['applyRunsLabel'], 'Apply runs · 3')
    if 'incident-apply-runs-flex' not in items(w, 'incident-detail-tabs'):
        f.append('the Apply runs tab does not show')
    expect(f, 'rows, newest first, this incident only',
           [(r['phase'], r['exit'], r['who'], r['message']) for r in out['applyRunRows']],
           [('Succeeded', '0', 'admin', 'the apply script exited 0'),
            ('Failed', '1', 'sre-1', 'the apply script exited 1'),
            ('Rejected', '—', 'admin', 'another apply is running for this incident')])
    cells = [[c['stringValue'] for c in row] for row in w['incident-apply-runs']['dataSource']]
    expect(f, 'table cells', [c[1:] for c in cells],
           [['admin', 'Succeeded', '0', 'the apply script exited 0'],
            ['sre-1', 'Failed', '1', 'the apply script exited 1'],
            ['admin', 'Rejected', '—', 'another apply is running for this incident']])
    md = w['incident-apply-runs-output']['markdown']
    for want in ('#### Succeeded · exit 0 · admin', '```text\ndeployment.apps/web patched\n```',
                 '#### Failed · exit 1 · sre-1', '````text\nError from server (Forbidden): ```x```\n````',
                 '**Script** (not the apply step\'s current one)', 'kubectl scale deploy/web', '#### Rejected · admin'):
        if want not in md:
            f.append(f'output markdown lacks {want!r}: {md[:600]!r}')
    if md.count('**Script**') != 1:
        f.append('a run of the current apply script repeats it')
    if md.index('#### Succeeded') > md.index('#### Failed') or md.index('#### Failed') > md.index('#### Rejected'):
        f.append('output is not newest first')
    return f


def check_history_shows_an_apply_exit(chart):
    f = []
    inc = incident('scr-4', state='Verifying')
    inc['status']['checks'] = [{'at': '2026-10-05T08:00:00Z', 'script': 'apply'},
                               {'at': '2026-10-05T09:00:00Z', 'script': 'apply', 'exit': 0}]
    # A run that failed before the last recorded apply is not the step's last run.
    out, w = resolve(chart, inc, 'apply checks', runs_list(run('scr-4', 'scr-4-apply-1', 'Failed', 1, '2026-10-05T08:30:00Z')))
    expect(f, 'check rows', [(r['script'], r['exit'], r['outcome']) for r in out['checkRows']],
           [('apply', '0', 'applied'), ('apply', '—', 'applied by hand')])
    expect(f, 'table exit column', [row[2]['stringValue'] for row in w['incident-checks']['dataSource']], ['0', '—'])
    md = w['incident-rem-step-md-1']['markdown']
    if 'exit 0 · applied' not in md or 'Run apply' in md.split('**Last run:**')[1].split('\n')[0]:
        f.append('the apply step\'s last run does not show the exit')
    return f


CHECKS = [
    check_apply_patches_a_composition,
    check_apply_patches_a_deployment,
    check_apply_deletes_without_a_body,
    check_script_only_keeps_i_applied_it,
    check_no_button_once_applied_or_closed,
    check_run_apply_posts_an_incidentapply,
    check_apply_runs_tab,
    check_history_shows_an_apply_exit,
]


def main():
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        i = args.index('--crds')
        crds_dir = args[i + 1]
        del args[i:i + 2]
    chart = tbi.Chart(tbi.render(args[0] if args else os.path.join(HERE, '..', 'helm', 'portal')))
    failed = 0
    for check in CHECKS:
        try:
            problems = check(chart)
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {check.__name__.replace("check_", "").replace("_", " ")}')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    total = len(CHECKS)
    if crds_dir:
        total += 1
        problems = tbi.validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} resolved widgets validate against their CRDs')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}{"" if crds_dir else "; resolved widgets NOT validated (no --crds)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
