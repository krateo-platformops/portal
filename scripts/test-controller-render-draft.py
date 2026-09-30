#!/usr/bin/env python3
"""
test-controller-render-draft — the Controller Builder's preview, resolved on the answers it can get.

WHAT IT GUARDS. controller-render-draft reads a controller draft (RestDefinitions + the OAS documents
they point at) from a preview-sandbox ConfigMap under the caller's identity and POSTs it to
oasgen-render (oasgen-provider >= 0.25.0, render.enabled=true). The frontend reads its answer with
the verdict handling it already has for blueprint-render-draft — `objects` and `error` — so every
way the preview can go must land there as a sentence, never as a jq error or an empty success:
  - ok: the CRDs are the objects, warnings and skipped security schemes ride beside them;
  - errors: a severity:error finding means the controller would apply nothing for that
    RestDefinition, so it is a problem and the preview fails;
  - service down: oasgen-render not installed, disabled or unreachable says what it needs;
  - no draft: every way of having nothing to render is a sentence, and posts nothing.
The fixture responses are shaped as oasgen-provider docs/api.md (tag 0.25.0) documents /render, and
a failed call's errorKey as snowplow 1.12 writes it (an accumulating list, resolve.go
accumulateErrorKey).

HOW. test-builder-install.py's model of snowplow's RESTAction resolution over the rendered chart.
Uses the `jq` binary; JQ=gojq runs the same checks on it.
Usage: test-controller-render-draft.py [chart-dir]   (default helm/portal). Exit code = failed checks.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

RA = 'controller-render-draft'
NAMED = {'namespace': 'krateo-preview', 'name': 'ctl-preview-gh-repo-x1'}
OAS_PATH = 'configmap://gh-system/repo/repo.yaml'
RD = {'apiVersion': 'ogen.krateo.io/v1alpha1', 'kind': 'RestDefinition',
      'metadata': {'name': 'gh-repo', 'namespace': 'gh-system'},
      'spec': {'oasPath': OAS_PATH, 'resourceGroup': 'github.ogen.krateo.io',
               'resource': {'kind': 'Repo', 'verbsDescription': [{'action': 'get', 'method': 'GET', 'path': '/repos/{owner}/{repo}'}]}}}
OAS = {OAS_PATH: 'openapi: 3.0.0\ninfo: {title: gh, version: "1"}\npaths: {}\n'}
DRAFT = {'data': {'draft.json': json.dumps({'restDefinitions': [RD], 'oas': OAS, 'ignored': True})}}


def crd(name):
    return {'apiVersion': 'apiextensions.k8s.io/v1', 'kind': 'CustomResourceDefinition',
            'metadata': {'name': name}, 'spec': {'group': 'github.ogen.krateo.io'}}


OK = {'crds': [crd('repoes.github.ogen.krateo.io')],
      'configurationCrds': [crd('repoconfigurations.github.ogen.krateo.io')],
      'errors': [{'restDefinition': 'gh-system/gh-repo', 'field': 'spec.resource.verbsDescription[0]',
                  'message': 'response schema has no properties', 'severity': 'warning'}],
      'skippedSecuritySchemes': [{'restDefinition': 'gh-system/gh-repo', 'scheme': 'oauth (type: oauth2, in: )'}]}
ERRORS = {'crds': [crd('repoes.github.ogen.krateo.io')], 'configurationCrds': [],
          'errors': [{'restDefinition': 'gh-system/gh-issue', 'field': 'spec.resource.verbsDescription[0].async.poll.path',
                      'message': 'poll path must be a path of the same API', 'severity': 'error'}],
          'skippedSecuritySchemes': []}
DOWN = ['Post "http://oasgen-provider-render.krateo-system.svc:80/render": dial tcp: '
        'lookup oasgen-provider-render.krateo-system.svc on 34.118.224.10:53: no such host']
NEEDS = 'Controller preview needs oasgen-render (oasgen-provider ≥0.25.0 with render.enabled)'


def check_the_payload_is_the_draft(chart):
    f = []
    step = [s for s in chart.get('RESTAction', RA)['spec']['api'] if s['name'] == 'render'][0]
    tbi.expect(f, 'the render call is the in-cluster oasgen-render endpoint', step.get('endpointRef', {}).get('name'), 'oasgen-render-endpoint')
    tbi.expect(f, 'it POSTs /render', (step['verb'], step['path']), ('POST', '/render'))
    draft = [s for s in chart.get('RESTAction', RA)['spec']['api'] if s['name'] == 'draft'][0]
    tbi.expect(f, 'the ConfigMap is read as the caller (no endpointRef)', 'endpointRef' in draft, False)
    payload = step['payload'].strip()
    tbi.expect(f, 'the payload is one ${ } program', payload.startswith('${') and payload.endswith('}'), True)
    body = json.loads(tbi.jq(payload[2:-1], {'draft': DRAFT}))
    tbi.expect(f, 'the body is the RestDefinitions and their documents — nothing else', body, {'restDefinitions': [RD], 'oas': OAS})
    endpoint = chart.get('Secret', 'oasgen-render-endpoint')
    tbi.expect(f, 'the endpoint is the oasgen-provider chart\'s render Service', endpoint['stringData']['server-url'],
               f'http://oasgen-provider-render.{tbi.NS}.svc:80')
    return f


def check_ok(chart):
    f = []
    out = tbi.resolve(chart, RA, {'draft': DRAFT, 'render': OK}, NAMED)
    tbi.expect(f, 'no error', 'error' in out, False)
    tbi.expect(f, 'no problems', out['problems'], [])
    tbi.expect(f, 'the objects are every CRD, by name', [o['name'] for o in out['objects']],
               ['repoes.github.ogen.krateo.io', 'repoconfigurations.github.ogen.krateo.io'])
    tbi.expect(f, 'an object has blueprint-render-draft\'s shape', sorted(out['objects'][0]), ['apiVersion', 'kind', 'name', 'namespace', 'yaml'])
    tbi.expect(f, 'its yaml is the CRD', json.loads(out['objects'][0]['yaml']), OK['crds'][0])
    tbi.expect(f, 'crds and configurationCrds pass through', (out['crds'], out['configurationCrds']), (OK['crds'], OK['configurationCrds']))
    tbi.expect(f, 'a warning is a warning, not a problem', out['warnings'][0],
               'gh-system/gh-repo spec.resource.verbsDescription[0]: response schema has no properties')
    tbi.expect(f, 'a skipped security scheme is a warning', 'oauth (type: oauth2, in: )' in out['warnings'][1], True)
    tbi.expect(f, 'skipped passes through', out['skipped'], OK['skippedSecuritySchemes'])
    return f


def check_errors(chart):
    f = []
    out = tbi.resolve(chart, RA, {'draft': DRAFT, 'render': ERRORS}, NAMED)
    says = 'gh-system/gh-issue spec.resource.verbsDescription[0].async.poll.path: poll path must be a path of the same API'
    tbi.expect(f, 'the finding is the problem', out['problems'], [says])
    tbi.expect(f, 'and the error', out.get('error'), says)
    tbi.expect(f, 'a failed preview lists no objects, as blueprint-render-draft', out['objects'], [])
    tbi.expect(f, 'what did generate is still in crds', [c['metadata']['name'] for c in out['crds']], ['repoes.github.ogen.krateo.io'])
    tbi.expect(f, 'errors pass through', out['errors'], ERRORS['errors'])
    return f


def check_service_down(chart):
    f = []
    for label, responses, extras in (
            ('unreachable (snowplow\'s errorKey list)', {'draft': DRAFT}, dict(NAMED, renderError=DOWN)),
            ('unreachable (the harness\'s errorKey object)', {'draft': DRAFT, 'render': 'ERROR'}, NAMED),
            ('something else answered /render', {'draft': DRAFT, 'render': {'status': 'ok'}}, NAMED)):
        out = tbi.resolve(chart, RA, responses, extras)
        tbi.expect(f, f'{label}: says what it needs', (out.get('error') or '').startswith(NEEDS), True)
        tbi.expect(f, f'{label}: one problem', len(out['problems']), 1)
        tbi.expect(f, f'{label}: renders nothing', (out['objects'], out['crds']), ([], []))
    out = tbi.resolve(chart, RA, {'draft': DRAFT}, dict(NAMED, renderError=[{'message': 'request body too large (413)'}]))
    tbi.expect(f, 'too large says so', (out.get('error') or '').startswith('the controller draft is too large'), True)
    return f


def check_no_draft(chart):
    f = []
    empty_rds = {'data': {'draft.json': json.dumps({'restDefinitions': [], 'oas': OAS})}}
    no_oas = {'data': {'draft.json': json.dumps({'restDefinitions': [RD]})}}
    for label, responses, extras, says in (
            ('no draft named', {}, {}, 'no draft named'),
            ('the ConfigMap could not be read', {'draft': 'ERROR', 'render': OK}, NAMED, 'could not be read'),
            ('a ConfigMap with no draft', {'draft': {'data': {}}, 'render': OK}, NAMED, 'holds no controller draft'),
            ('a draft with no RestDefinition', {'draft': empty_rds, 'render': OK}, NAMED, 'has no RestDefinition'),
            ('a draft with no OAS documents', {'draft': no_oas, 'render': OK}, NAMED, 'has no OAS documents')):
        out = tbi.resolve(chart, RA, responses, extras)
        tbi.expect(f, f'{label}: says so', says in (out.get('error') or ''), True)
        tbi.expect(f, f'{label}: one problem, not the service\'s', len(out['problems']), 1)
        tbi.expect(f, f'{label}: renders nothing', out['objects'], [])
    render = [s for s in chart.get('RESTAction', RA)['spec']['api'] if s['name'] == 'render'][0]
    for label, draft in (('no draft', {'data': {}}), ('no RestDefinition', empty_rds), ('no OAS', no_oas), ('a draft', DRAFT)):
        posts = len(tbi.jq(render['dependsOn']['iterator'], dict(NAMED, draft=draft)))
        tbi.expect(f, f'{label}: posts {"once" if draft is DRAFT else "nothing"}', posts, 1 if draft is DRAFT else 0)
    return f


CHECKS = [check_the_payload_is_the_draft, check_ok, check_errors, check_service_down, check_no_draft]


def main():
    chart = tbi.Chart(tbi.render(sys.argv[1] if len(sys.argv) > 1 else 'helm/portal'))
    failed = 0
    for check in CHECKS:
        try:
            problems = check(chart)
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {check.__name__.replace("check_", "").replace("_", " ")}')
        for problem in dict.fromkeys(problems):
            print(f'        {problem}')
        failed += bool(problems)
    print(f'\n{len(CHECKS) - failed} of {len(CHECKS)} checks passed ({tbi.JQ})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
