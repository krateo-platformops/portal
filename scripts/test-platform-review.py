#!/usr/bin/env python3
"""
test-platform-review — /reviews and its proposal and run pages, evaluated end to end.

WHAT IT GUARDS. The Platform Review pages are three RESTActions of jq (platform-reviews,
review-proposal, review-run) and ~40 widgets of jq over nightly-review's Proposal and ReviewRun CRs.
`helm template` evaluates none of it. The rules the pages must keep, each pinned below by what a
reader would see:
  - a superseded proposal is not a row: it is counted on the proposal that replaced it, following the
    chain to its end; a legacy proposal without spec.subject stands alone; rows are newest first;
  - confidence and observedCount never reach the list, and the detail page labels them as the model's;
  - "Kind not served" only for a yaml change whose kind discovery positively does not serve; markdown
    and diff read "Kind check n/a"; an unanswered discovery read renders nothing;
  - TargetResolved=False/NotFoundOrPrivate reads "Target unverified", never as a missing repository;
  - Refused (historical) is excluded from both tabs, and the caption says so;
  - a Failed run's Result is status.error; evidence[].query is never rendered;
  - the change-request claim, as the form would POST it, satisfies builder-publish's values schema
    with builder review and repository.create false, and the action retires once the claim exists;
  - decisions: Reject PATCHes exactly {spec: {decision: {phase: Rejected, reason}}} and Open change
    request follows the claim with {spec: {decision: {phase: PrOpen, claim}}}; neither sends
    decidedBy/decidedAt; both bodies meet nightly-review 0.1.23's spec.decision schema and CEL rule
    (vendored in fixtures/); spec.decision is read before status except when status is Merged; the
    actions follow the effective phase (Proposed: Open change request and Reject; PrOpen: Reject;
    Rejected, Merged, Superseded: neither), and a claim with no decision offers "Record as change
    request open".

HOW. helm/portal is rendered (test-builder-install.py's render), and each RESTAction is resolved
over fixtures shaped like the krateo-057 objects (nightly-review 0.1.22) by a model of snowplow's
resolver: extras seed the dict; a stage's iterator runs over the whole dict and its path over each
element; a step filter sees {<stage>: response}; the first result is stored as-is and later
filter-produced arrays are spliced; a failed call appends its error string under the stage's
errorKey. Iterator stages are resolved in both item orders and must agree. Widget expressions are
then evaluated over the RA output with the route's extras merged underneath.

With --crds DIR (a frontend-crds chart), every Platform Review widget, as resolved for every
fixture, is validated against its CRD, closed the way the apiserver's strict validation closes it.

Uses the `jq` binary; JQ=gojq runs the same checks on it.
Usage: test-platform-review.py [--crds DIR]. Exit code = failed checks.
"""
import copy
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

NS = tbi.NS
RAS = ('platform-reviews', 'review-proposal', 'review-run')


# ---------------------------------------------------------------------------------------------
# A model of snowplow's RESTAction resolution, with dependsOn iterators
# ---------------------------------------------------------------------------------------------

def expr(s):
    s = s.strip()
    return s[2:-1] if s.startswith('${') and s.endswith('}') else None


# Every path a resolve requested, as (RESTAction, step, path), in order.
DIALS = []


def by_path(response):
    """An iterator step's response keyed by the path it answers, or None when it answers any path."""
    if isinstance(response, dict) and response and all(k.startswith('/') for k in response):
        return response
    return None


def resolve(chart, name, responses, extras, reverse=False):
    """`responses` maps a step to its raw response, 'ERROR:<message>' for a failed call, or, for an
    iterator step, a dict of path -> response / 'ERROR:<message>'. A step with no entry is not served.
    An iterator (an input gate included) yields its elements, and none for a non-array, as snowplow's
    createRequestOptions does (resolvers/restactions/api/setup.go:43-62)."""
    ra = chart.get('RESTAction', name)
    data = copy.deepcopy(extras)
    for step in ra['spec']['api']:
        sname, key = step['name'], step.get('errorKey', 'error')
        if sname not in responses:
            continue
        it = (step.get('dependsOn') or {}).get('iterator')
        if it:
            items = tbi.jq(it, data)
            items = items if isinstance(items, list) else []
            if reverse:
                items = list(reversed(items))
            for item in items:
                path = tbi.jq(expr(step['path']), item)
                DIALS.append((name, sname, path))
                keyed = by_path(responses[sname])
                raw = (keyed.get(path, 'ERROR:the server could not find the requested resource')
                       if keyed is not None else responses[sname])
                if isinstance(raw, str) and raw.startswith('ERROR:'):
                    data.setdefault(key, []).append(raw[6:])
                    continue
                out = tbi.jq(step['filter'], {sname: raw}) if step.get('filter') else raw
                if sname in data and isinstance(out, list):
                    data[sname] = (data[sname] if isinstance(data[sname], list) else [data[sname]]) + out
                elif sname not in data:
                    data[sname] = out
            continue
        raw = responses[sname]
        DIALS.append((name, sname, tbi.jq(expr(step['path']), data) if expr(step['path']) else step['path']))
        if isinstance(raw, str) and raw.startswith('ERROR:'):
            if not step.get('continueOnError'):
                raise RuntimeError(f'{name}: step {sname} failed and does not continue on error')
            data.setdefault(key, []).append(raw[6:])
            continue
        data[sname] = tbi.jq(step['filter'], {sname: raw}) if step.get('filter') else raw
    return tbi.jq(ra['spec']['filter'], data)


def resolved(chart, name, responses, extras):
    a = resolve(chart, name, responses, extras)
    b = resolve(chart, name, responses, extras, reverse=True)
    if a != b:
        raise AssertionError(f'{name} resolves differently with its iterator items reversed')
    return a


def review_widgets(chart):
    """Every widget CR on the Platform Review pages."""
    out = []
    for d in chart.docs:
        if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/'):
            n = d['metadata']['name']
            if n.startswith(('review', 'reviews-', 'page-review')) or n == 'platform-proposals':
                out.append(d)
    return out


def resolve_widgets(chart, ra_name, output, extras, label):
    """Resolve and record (for --crds) every review widget reading `ra_name`; static ones too."""
    got = {}
    for d in review_widgets(chart):
        ref = (d['spec'].get('apiRef') or {}).get('name')
        if ref not in (ra_name, None):
            continue
        doc = tbi.resolved_widget(chart, d['kind'], d['metadata']['name'], output if ref else {}, extras,
                                  f'{label}: {d["kind"]} {d["metadata"]["name"]}')
        got[d['metadata']['name']] = doc['spec'].get('widgetData', {})
    return got


# ---------------------------------------------------------------------------------------------
# Fixtures, shaped like krateo-057 (nightly-review 0.1.22)
# ---------------------------------------------------------------------------------------------

PROM = ('apiVersion: monitoring.coreos.com/v1\nkind: PrometheusRule\nmetadata:\n  name: snowplow-sar\n'
        'spec:\n  groups: []\n')
FORM = 'apiVersion: widgets.templates.krateo.io/v1beta1\nkind: Form\nmetadata:\n  name: x\n'


def proposal(name, created, title, kind='Alert', fmt='yaml', content=PROM, subject=None, phase='Proposed',
             superseded_by=None, target_reason=None, run='rr-20260929-1328', repo='krateo-platformops/snowplow',
             **status):
    spec = {'kind': kind, 'title': title, 'rationale': f'why {name}', 'confidence': 'high',
            'fingerprint': name[2:], 'producedBy': {'runRef': run, 'agent': 'krateo-autopilot'},
            'target': {'repo': repo, 'path': f'deploy/{name}.yaml'},
            'change': {'format': fmt, 'content': content},
            'evidence': [{'source': 'clickhouse', 'summary': '15302 ERROR logs', 'observedCount': 15302,
                          'query': 'SELECT invented FROM nowhere'},
                         {'source': 'clickhouse', 'summary': 'more', 'observedCount': 12},
                         {'source': 'kubernetes', 'summary': 'events'}]}
    if subject:
        spec['subject'] = subject
    st = {'phase': phase, **status}
    if superseded_by:
        st['supersededBy'] = superseded_by
    if target_reason:
        ok = target_reason == 'RepoFound'
        st['conditions'] = [{'type': 'TargetResolved', 'status': 'True' if ok else (
            'Unknown' if target_reason.startswith('Check') else 'False'), 'reason': target_reason,
            'message': f'No public repository {repo}. It does not exist, or it is private.'}]
    return {'apiVersion': 'review.krateo.io/v1alpha1', 'kind': 'Proposal',
            'metadata': {'name': name, 'namespace': NS, 'creationTimestamp': created}, 'spec': spec, 'status': st}


def run(name, phase, started, finished, **status):
    st = {'phase': phase, 'startedAt': started, 'finishedAt': finished, **status}
    return {'apiVersion': 'review.krateo.io/v1alpha1', 'kind': 'ReviewRun',
            'metadata': {'name': name, 'namespace': NS, 'creationTimestamp': started[:19] + 'Z'},
            'spec': {'window': {'from': '2026-09-28T13:28:41Z', 'to': '2026-09-29T13:28:41Z'},
                     'sources': ['clickhouse', 'kagent-sessions', 'kubernetes']},
            'status': st}


PROPOSALS = [
    # one finding, three nights: a -> b -> c (c open)
    proposal('p-aaaa000000000001', '2026-09-27T02:00:50Z', 'SAR alert v1', subject='snowplow/sar-unauthorized',
             phase='Superseded', superseded_by='p-bbbb000000000002'),
    proposal('p-bbbb000000000002', '2026-09-28T02:00:50Z', 'SAR alert v2', subject='snowplow/sar-unauthorized',
             phase='Superseded', superseded_by='p-cccc000000000003'),
    proposal('p-cccc000000000003', '2026-09-29T02:00:50Z', 'SAR alert v3', subject='snowplow/sar-unauthorized'),
    # legacy (no subject), newest
    proposal('p-dddd000000000004', '2026-09-29T13:29:42Z', 'Legacy widget fix', kind='Widget', content=FORM),
    # markdown, target unverified
    proposal('p-eeee000000000005', '2026-09-29T19:34:58Z', 'Doc for chart-inspector', kind='Documentation',
             fmt='markdown', content='# Troubleshooting\n\n```sh\nkubectl get x\n```\n',
             subject='installer-chart-inspector/unable-to-template-chart', target_reason='NotFoundOrPrivate',
             run='rr-20260929-1933', repo='krateo-platformops/installer-chart-inspector'),
    # target check unanswered: renders nothing
    proposal('p-ffff000000000006', '2026-09-29T19:34:57Z', 'Policy', kind='Policy',
             content='apiVersion: policies.krateo.io/v1alpha1\nkind: AgentLifecyclePolicy\n',
             subject='tk-swarm-ro/idle', target_reason='CheckFailed', run='rr-20260929-1933'),
    # historical Refused
    proposal('p-9999000000000009', '2026-09-28T08:51:52Z', 'Refused alert', phase='Refused', run='rr-20260928-0849'),
]

SQL = "SELECT ServiceName, count() AS n\nFROM otel_logs\nWHERE Timestamp BETWEEN '2026-09-28 19:33:39' AND '2026-09-29 19:33:39'"
RUNS = [
    run('rr-20260929-1933', 'Completed', '2026-09-29T19:33:39.449183+00:00', '2026-09-29T19:34:58.187783+00:00',
        expiresAt='2026-10-29T19:34:58.187790+00:00', summary='The platform had three failure clusters.',
        proposals={'created': 2, 'superseded': 0, 'deduplicated': 0,
                   'refs': ['p-eeee000000000005', 'p-ffff000000000006']},
        model={'inputTokens': 7535, 'outputTokens': 8433, 'totalTokens': 15968},
        steps=[{'name': 'gather', 'phase': 'Succeeded', 'message': 'answered: clickhouse, kagent-sessions, kubernetes',
                'startedAt': '2026-09-29T19:33:39.5+00:00', 'finishedAt': '2026-09-29T19:34:09.8+00:00'},
               {'name': 'ask', 'phase': 'Succeeded'},
               {'name': 'publish', 'phase': 'Skipped', 'message': 'dryRun: pull requests are opened from the portal, per proposal'}],
        evidence={'clickhouse': {'ok': True, 'queried': 1, 'returned': 56, 'queries': {'logVolumeByService': SQL}},
                  'kagent-sessions': {'ok': True, 'queried': 1, 'returned': 15, 'agentsDeployed': 16,
                                      'agentsActiveInWindow': 4, 'agentsIdle7d': 5, 'agentsNeverUsed': 1,
                                      'scope': 'all-users metadata (SELECT on session only; cannot read event/task)'},
                  'kubernetes': {'ok': True, 'queried': 1, 'returned': 25}}),
    run('rr-20260928-0849', 'PartiallyCompleted', '2026-09-28T08:49:19.403648+00:00', '2026-09-28T08:51:53.104318+00:00',
        expiresAt='2026-10-28T08:51:53.104346+00:00', proposals={'created': 0, 'refused': 2},
        conditions=[{'type': 'ValidationNotes', 'status': 'True', 'reason': 'Notes',
                     'message': "REFUSED proposal[0] (Alert): not in the allowlist | REFUSED proposal[1] (Alert): not in the allowlist"}],
        evidence={'clickhouse': {'ok': False, 'queried': 2, 'returned': 45,
                                 'error': 'errorPatternsByService: Read timed out. (read timeout=120); '},
                  'kagent-sessions': {'ok': True, 'empty': True, 'queried': 1, 'returned': 0,
                                      'note': 'kagent returned an empty session list'},
                  'kubernetes': {'ok': True, 'queried': 1, 'returned': 24}}),
    run('rr-20260926-0200', 'Failed', '2026-09-26T02:00:09.825572+00:00', '2026-09-26T02:42:58.712161+00:00',
        error="ConnectionError: HTTPConnectionPool(host='autopilot.krateo-system.svc', port=8080): Read timed out."),
]

APIS = {'groups': [{'name': 'widgets.templates.krateo.io',
                    'versions': [{'groupVersion': 'widgets.templates.krateo.io/v1beta1'}]},
                   {'name': 'review.krateo.io', 'versions': [{'groupVersion': 'review.krateo.io/v1alpha1'}]}]}
KINDS = {'/apis/widgets.templates.krateo.io/v1beta1': {
    'kind': 'APIResourceList', 'groupVersion': 'widgets.templates.krateo.io/v1beta1',
    'resources': [{'name': 'forms', 'kind': 'Form'}, {'name': 'forms/status', 'kind': 'Form'},
                  {'name': 'tables', 'kind': 'Table'}]}}
BP = {'status': {'apiVersion': 'composition.krateo.io/v1-8-53', 'resource': 'builderpublishes'}}


def list_responses(**over):
    r = {'proposals': {'items': PROPOSALS}, 'runs': {'items': RUNS}, 'apis': APIS, 'kinds': KINDS}
    r.update(over)
    return r


def detail_responses(name, **over):
    p = [x for x in PROPOSALS if x['metadata']['name'] == name]
    r = {'proposal': p[0] if p else 'ERROR:proposals.review.krateo.io "%s" not found' % name,
         'all': {'items': PROPOSALS}, 'apis': APIS, 'kinds': KINDS, 'bp': BP,
         'claim': 'ERROR:builderpublishes.composition.krateo.io "x" not found', 'prs': {'items': []}}
    r.update(over)
    return r


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def apply_overrides(payload, overrides, values):
    """The frontend's buildPayload step 3-4: each `${ … }` evaluated over {json: values}, then set."""
    body = copy.deepcopy(payload)
    for o in overrides or []:
        v = tbi.jq(expr(o['value']), {'json': values})
        keys = [int(k) if k.isdigit() else k for k in o['name'].replace('[', '.').replace(']', '').split('.')]
        tgt = body
        for k in keys[:-1]:
            tgt = tgt[k] if isinstance(tgt, list) else tgt.setdefault(k, {})
        tgt[keys[-1]] = v
    return body


def ref_template(chart, kind, name, ref_id, output):
    doc = chart.get(kind, name)
    t = [x['template'] for x in doc['spec']['resourcesRefsTemplate'] if x['template']['id'] == ref_id][0]
    return {k: (tbi.jq(expr(v), output) if isinstance(v, str) and expr(v) is not None else v) for k, v in t.items()}


def cells(row):
    return {c['valueKey']: c for c in row}


# ---------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------

def check_list_groups_by_finding_newest_first(chart):
    f = []
    out = resolved(chart, 'platform-reviews', list_responses(), {})
    names = [r['name'] for r in out['rows']]
    expect(f, 'open rows, newest group first, superseded collapsed, Refused excluded', names,
           ['p-eeee000000000005', 'p-ffff000000000006', 'p-dddd000000000004', 'p-cccc000000000003'])
    c = [r for r in out['rows'] if r['name'] == 'p-cccc000000000003'][0]
    expect(f, 'the chain a -> b -> c is counted on c', c['superseded'], '2 earlier')
    d = [r for r in out['rows'] if r['name'] == 'p-dddd000000000004'][0]
    expect(f, 'a legacy proposal has no subject', d['subject'], '—')
    for r in out['rows']:
        for k in ('confidence', 'observed', 'observedCount', 'priority'):
            expect(f, f'{r["name"]} carries no {k}', k in r, False)
    expect(f, 'Refused counted for the caption', out['refused'], 1)
    expect(f, 'caption says Refused is not listed', 'historical Refused records not listed' in out['openCaption'], True)
    expect(f, 'evidence sources summarised', c['sources'], 'clickhouse ×2 · kubernetes')
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'list')
    ds = w['platform-proposals']['dataSource']
    expect(f, 'table rows', len(ds), 4)
    expect(f, 'rows navigate by name', w['platform-proposals']['rowNavigateTo'], '/reviews/proposals/{name}')
    expect(f, 'every row carries its name cell', all(cells(r)['name']['stringValue'] for r in ds), True)
    expect(f, 'header counter', w['reviews-page-header']['counter'], 4)
    expect(f, 'page items (no banner on a Completed latest run)',
           [i['resourceRefId'] for i in w['page-reviews']['items']], ['reviews-page-header', 'reviews-tabs'])
    return f


def check_kind_and_target_checks(chart):
    f = []
    pills = lambda out: {r['name']: [p['type'] for p in json.loads(r['checks'])] for r in out['rows']}
    got = pills(resolved(chart, 'platform-reviews', list_responses(), {}))
    expect(f, 'PrometheusRule: its group-version is not served', got['p-cccc000000000003'], ['Kind not served'])
    expect(f, 'Form in a served group-version: no pill', got['p-dddd000000000004'], [])
    expect(f, 'markdown: neutral n/a and Target unverified', got['p-eeee000000000005'], ['Kind check n/a', 'Target unverified'])
    expect(f, 'TargetResolved Unknown/CheckFailed renders nothing', got['p-ffff000000000006'], ['Kind not served'])
    got = pills(resolved(chart, 'platform-reviews', list_responses(apis='ERROR:connection refused'), {}))
    expect(f, 'discovery unreachable: no kind claim for yaml', got['p-cccc000000000003'], [])
    got = pills(resolved(chart, 'platform-reviews', list_responses(kinds={}), {}))
    expect(f, 'group-version served but its list errored: no claim', got['p-dddd000000000004'], [])
    out = resolved(chart, 'platform-reviews', list_responses(), {})
    for r in out['rows']:
        expect(f, f'{r["name"]}: no "missing" wording', 'issing' in r['checks'], False)
    return f


def check_runs_tab(chart):
    f = []
    out = resolved(chart, 'platform-reviews', list_responses(), {})
    rows = {r['name']: r for r in out['runRows']}
    expect(f, 'runs newest first', [r['name'] for r in out['runRows']],
           ['rr-20260929-1933', 'rr-20260928-0849', 'rr-20260926-0200'])
    expect(f, 'a Failed run shows status.error', rows['rr-20260926-0200']['result'], RUNS[2]['status']['error'])
    expect(f, 'a Completed run shows its count', rows['rr-20260929-1933']['result'], '2 proposed')
    expect(f, 'took', rows['rr-20260929-1933']['took'], '1 m 19 s')
    expect(f, 'sources not read', rows['rr-20260928-0849']['notRead'],
           'clickhouse: errorPatternsByService: Read timed out. (read timeout=120) · kagent-sessions: kagent returned an empty session list')
    expect(f, 'no ValidationNotes column on the list', any('otes' in k for k in out['runRows'][0]), False)
    return f


def check_banner_and_empty_states(chart):
    f = []
    out = resolved(chart, 'platform-reviews', list_responses(
        proposals='ERROR:the server could not find the requested resource (404)', runs='ERROR:404 not found',
        apis=APIS), {})
    expect(f, 'not installed', out['banner']['title'], 'The nightly review is not installed on this cluster')
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'not-installed')
    expect(f, 'not installed: no tabs', [i['resourceRefId'] for i in w['page-reviews']['items']],
           ['reviews-page-header', 'reviews-banner'])
    out = resolved(chart, 'platform-reviews', list_responses(
        proposals='ERROR:proposals.review.krateo.io is forbidden: User "u" cannot list (403)'), {})
    expect(f, 'denied is content', out['banner']['title'], 'Your role cannot read review proposals')
    out = resolved(chart, 'platform-reviews', list_responses(runs={'items': []}), {})
    expect(f, 'no run yet', out['banner']['title'], 'No review has run yet')
    out = resolved(chart, 'platform-reviews', list_responses(runs={'items': [RUNS[2]]}), {})
    expect(f, 'latest failed', (out['banner']['type'], out['banner']['title']), ('error', 'The last review failed: rr-20260926-0200'))
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'failed-latest')
    expect(f, 'decided empty state', [i['resourceRefId'] for i in w['reviews-decided']['items']], ['reviews-decided-empty'])
    decided = PROPOSALS + [proposal('p-1111000000000010', '2026-09-29T20:00:00Z', 'Merged one', phase='Merged',
                                    decidedBy='admin', decidedAt='2026-09-29T21:00:00Z',
                                    pullRequest={'url': 'https://github.com/o/r/pull/7', 'number': 7, 'state': 'merged'})]
    out = resolved(chart, 'platform-reviews', list_responses(proposals={'items': decided}), {})
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'decided')
    expect(f, 'decided rows', [cells(r)['outcome']['stringValue'] for r in w['reviews-decided-table']['dataSource']],
           ['https://github.com/o/r/pull/7'])
    expect(f, 'decided tab items', [i['resourceRefId'] for i in w['reviews-decided']['items']],
           ['reviews-decided-caption', 'reviews-decided-table'])
    return f


def check_proposal_detail(chart):
    f = []
    ex = {'name': 'p-cccc000000000003'}
    out = resolved(chart, 'review-proposal', detail_responses(ex['name']), ex)
    expect(f, 'found', out['found'], True)
    expect(f, 'model count labelled and formatted', out['evidenceRows'][0]['counted'], 'the model counted 15,302')
    expect(f, 'no count invented', out['evidenceRows'][2]['counted'], '—')
    expect(f, 'evidence[].query never reaches the page', 'invented' in json.dumps(out), False)
    facts = {x['label']: x['value'] for x in out['facts']}
    expect(f, 'confidence is the model\'s', facts['Model says'], 'high (unverified)')
    expect(f, 'kind alert', out['kindAlert'].startswith('The change is a monitoring.coreos.com/v1 PrometheusRule.'), True)
    expect(f, 'replaces two', out['lineageAlert'].startswith('Replaces 2 earlier proposals'), True)
    expect(f, 'earlier rows', [r['name'] for r in out['earlierRows']], ['p-bbbb000000000002', 'p-aaaa000000000001'])
    expect(f, 'yaml renders fenced', out['changeMarkdown'].startswith('```yaml\n'), True)
    w = resolve_widgets(chart, 'review-proposal', out, ex, 'proposal')
    expect(f, 'page items', [i['resourceRefId'] for i in w['page-review-proposal']['items']],
           ['review-proposal-header', 'review-proposal-kind', 'review-proposal-lineage', 'review-proposal-body'])
    expect(f, 'actions', [i['resourceRefId'] for i in w['review-proposal-actions']['items']],
           ['review-proposal-run', 'review-proposal-ask', 'review-proposal-reject', 'review-proposal-open-pr'])
    expect(f, 'run link', w['review-proposal-run']['actions']['navigate'][0]['path'], '/reviews/runs/rr-20260929-1328')
    expect(f, 'ask stays on the page', w['review-proposal-ask']['actions']['navigate'][0]['path'].startswith(
        '/reviews/proposals/p-cccc000000000003?ask='), True)
    expect(f, 'no priority tag', any('riority' in t['label'] for t in w['review-proposal-header']['tags']), False)

    ex = {'name': 'p-bbbb000000000002'}
    out = resolved(chart, 'review-proposal', detail_responses(ex['name']), ex)
    expect(f, 'superseded: lineage names the replacement', out['lineageAlert'].startswith('Superseded by p-cccc000000000003'), True)
    expect(f, 'superseded: no Open change request', out['canOpen'], False)
    resolve_widgets(chart, 'review-proposal', out, ex, 'superseded')

    ex = {'name': 'p-eeee000000000005'}
    out = resolved(chart, 'review-proposal', detail_responses(ex['name']), ex)
    expect(f, 'markdown renders as markdown', out['changeMarkdown'].startswith('# Troubleshooting'), True)
    expect(f, 'markdown: no kind alert', out['kindAlert'], '')
    expect(f, 'target unverified alert', 'did not resolve' in out['targetAlert'], True)
    resolve_widgets(chart, 'review-proposal', out, ex, 'markdown')

    ex = {'name': 'p-nope'}
    out = resolved(chart, 'review-proposal', detail_responses(ex['name']), ex)
    expect(f, 'not found is content', (out['found'], out['title']), (False, 'Proposal not found'))
    w = resolve_widgets(chart, 'review-proposal', out, ex, 'missing')
    expect(f, 'missing page items', [i['resourceRefId'] for i in w['page-review-proposal']['items']],
           ['review-proposal-header', 'review-proposal-missing'])
    return f


def check_change_request_claim(chart):
    """The claim the form POSTs: the fixed half templated from the proposal, the two edited fields
    applied the way the frontend's buildPayload applies payloadToOverride, then checked against the
    builder-publish values schema (a BuilderPublish's spec IS the chart's values)."""
    import jsonschema
    f = []
    ex = {'name': 'p-cccc000000000003'}
    out = resolved(chart, 'review-proposal', detail_responses(ex['name']), ex)
    w = resolve_widgets(chart, 'review-proposal', out, ex, 'claim')['review-open-pr-form']
    act = w['actions']['rest'][0]
    expect(f, 'initial values', w['initialValues'], {'repository': 'krateo-platformops/snowplow',
                                                     'file': 'deploy/p-cccc000000000003.yaml'})
    expect(f, 'two writes: the claim, then the decision', [o['resourceRefId'] for o in act['ops']],
           ['create-builder-publish', 'decide-pr-open'])
    values = {'repository': 'krateo-platformops/observability', 'file': 'alerts/sar.yaml'}
    body = apply_overrides(act['ops'][0]['payload'], act['ops'][0].get('payloadToOverride'), values)
    decision = apply_overrides(act['ops'][1]['payload'], act['ops'][1].get('payloadToOverride'), values)
    expect(f, 'PrOpen body, no decidedBy/decidedAt', decision,
           {'spec': {'decision': {'phase': 'PrOpen', 'claim': 'review-cccc000000000003'}}})
    ref = ref_template(chart, 'Form', 'review-open-pr-form', 'decide-pr-open', out)
    expect(f, 'PrOpen target', (ref['apiVersion'], ref['resource'], ref['name'], ref['verb']),
           ('review.krateo.io/v1alpha1', 'proposals', 'p-cccc000000000003', 'PATCH'))
    spec = body['spec']
    expect(f, 'claim name', (body['metadata']['name'], spec['name']), ('review-cccc000000000003',) * 2)
    expect(f, 'builder', spec['builder'], 'review')
    expect(f, 'never creates a repository', spec['repository'], {'create': False})
    expect(f, 'edited target', (spec['target']['namespace'], spec['target']['repo']), ('krateo-platformops', 'observability'))
    expect(f, 'edited file', spec['files'][0]['path'], 'alerts/sar.yaml')
    expect(f, 'content verbatim', spec['files'][0]['content'], PROM)
    schema = json.load(open(os.path.join(HERE, '..', 'helm', 'builder-publish', 'values.schema.json')))
    for err in jsonschema.Draft7Validator(schema).iter_errors(spec):
        f.append(f'claim spec vs builder-publish values.schema.json: {"/".join(map(str, err.path))}: {err.message[:160]}')
    expect(f, 'POST target is the discovered GVR', w.get('bpApiVersion', out['bpApiVersion']), 'composition.krateo.io/v1-8-53')

    claim = {'kind': 'BuilderPublish', 'metadata': {'name': 'review-cccc000000000003'}, 'spec': {'branch': 'review/alert-cccc00000000'}}
    pr = {'metadata': {'labels': {'krateo.io/publish': 'review-cccc000000000003'}},
          'status': {'number': 12, 'state': 'open', 'html_url': 'https://github.com/krateo-platformops/observability/pull/12'}}
    out = resolved(chart, 'review-proposal', detail_responses(ex['name'], claim=claim, prs={'items': [pr]}), ex)
    expect(f, 'claim exists: the action retires', out['canOpen'], False)
    facts = {x['label']: x['value'] for x in out['facts']}
    expect(f, 'change request read back', facts.get('Change request', '').endswith('pull request #12 open · https://github.com/krateo-platformops/observability/pull/12'), True)
    resolve_widgets(chart, 'review-proposal', out, ex, 'claimed')
    return f


def check_run_detail(chart):
    f = []
    ex = {'name': 'rr-20260929-1933'}
    out = resolved(chart, 'review-run', {'run': RUNS[0], 'all': {'items': PROPOSALS}, 'apis': APIS, 'kinds': KINDS}, ex)
    expect(f, 'steps', [(s['title'], s['status']) for s in out['steps']],
           [('Gather', 'finish'), ('Ask Autopilot', 'finish'), ('Publish', 'wait')])
    expect(f, 'queries as issued', SQL in out['queriesMarkdown'], True)
    expect(f, 'proposals of this run', [r['name'] for r in out['proposalRows']], ['p-eeee000000000005', 'p-ffff000000000006'])
    facts = {x['label']: x['value'] for x in out['facts']}
    expect(f, 'tokens', facts['Tokens'], '7535 in · 8433 out · 15968 total')
    expect(f, 'no model name row', 'Model' in facts, False)
    w = resolve_widgets(chart, 'review-run', out, ex, 'run-completed')
    expect(f, 'completed page items', [i['resourceRefId'] for i in w['page-review-run']['items']],
           ['review-run-header', 'review-run-summary', 'review-run-steps', 'review-run-body', 'review-run-queries', 'review-run-facts'])

    ex = {'name': 'rr-20260928-0849'}
    out = resolved(chart, 'review-run', {'run': RUNS[1], 'all': {'items': PROPOSALS}, 'apis': APIS, 'kinds': KINDS}, ex)
    expect(f, 'validation notes', out['notesMarkdown'].count('\n- ') + 1, 2)
    expect(f, 'partial alert', out['alert']['type'], 'warning')
    w = resolve_widgets(chart, 'review-run', out, ex, 'run-partial')
    expect(f, 'partial page items', [i['resourceRefId'] for i in w['page-review-run']['items']],
           ['review-run-header', 'review-run-alert', 'review-run-body', 'review-run-notes', 'review-run-facts'])

    ex = {'name': 'rr-20260926-0200'}
    out = resolved(chart, 'review-run', {'run': RUNS[2], 'all': {'items': PROPOSALS}, 'apis': APIS, 'kinds': KINDS}, ex)
    expect(f, 'failed alert is status.error', out['alert']['description'], RUNS[2]['status']['error'])
    resolve_widgets(chart, 'review-run', out, ex, 'run-failed')

    ex = {'name': 'rr-nope'}
    out = resolved(chart, 'review-run', {'run': 'ERROR:reviewruns "rr-nope" not found', 'all': {'items': []}, 'apis': APIS}, ex)
    expect(f, 'missing run is content', out['title'], 'Review run not found')
    resolve_widgets(chart, 'review-run', out, ex, 'run-missing')
    return f


def check_steps_gated_on_input(chart):
    """Without a name — snowplow's Phase-1 walk resolves these pages with no extras — the proposal,
    the claim and the run are not requested at all (they used to GET …/proposals/, …/reviewruns/
    and …/builderpublishes/review-), and the pages render their not-found state. With a name the
    requests are the ones they always were; without `bp` the claim is not guessed at a version."""
    f = []
    gated = {('review-proposal', 'proposal'), ('review-proposal', 'claim'), ('review-run', 'run')}
    for ex in ({}, {'name': ''}):
        del DIALS[:]
        out = resolved(chart, 'review-proposal', detail_responses('p-nope'), ex)
        run = resolved(chart, 'review-run', {'run': RUNS[0], 'all': {'items': PROPOSALS}, 'apis': APIS, 'kinds': KINDS}, ex)
        expect(f, f'{ex}: gated steps requested', sorted({d[1] for d in DIALS if d[:2] in gated}), [])
        expect(f, f'{ex}: proposal page', (out['found'], out['title']), (False, 'Proposal not found'))
        expect(f, f'{ex}: run page', (run['found'], run['title']), (False, 'Review run not found'))
        w = resolve_widgets(chart, 'review-proposal', out, ex, f'no name {ex}')
        expect(f, f'{ex}: proposal page items', [i['resourceRefId'] for i in w['page-review-proposal']['items']],
               ['review-proposal-header', 'review-proposal-missing'])
        resolve_widgets(chart, 'review-run', run, ex, f'run, no name {ex}')
    del DIALS[:]
    ex = {'name': 'p-cccc000000000003'}
    resolve(chart, 'review-proposal', detail_responses(ex['name']), ex)
    resolve(chart, 'review-run', {'run': RUNS[0], 'all': {'items': PROPOSALS}, 'apis': APIS, 'kinds': KINDS},
            {'name': RUNS[0]['metadata']['name']})
    expect(f, 'named: requests', sorted(d[2] for d in DIALS if d[:2] in gated), sorted([
        f'/apis/review.krateo.io/v1alpha1/namespaces/{NS}/proposals/p-cccc000000000003',
        f'/apis/composition.krateo.io/v1-8-53/namespaces/{NS}/builderpublishes/review-cccc000000000003',
        f'/apis/review.krateo.io/v1alpha1/namespaces/{NS}/reviewruns/{RUNS[0]["metadata"]["name"]}']))
    del DIALS[:]
    resolve(chart, 'review-proposal', detail_responses(ex['name'], bp='ERROR:compositiondefinitions "builder-publish" not found'), ex)
    expect(f, 'no bp: claim requested', [d[2] for d in DIALS if d[1] == 'claim'], [])
    return f


DECISION = json.load(open(os.path.join(HERE, 'fixtures', 'nightly-review-0.1.23-decision.json')))['schema']


def decision_problems(body, what):
    """A PATCH body against nightly-review 0.1.23's spec.decision: the only key a person may write,
    its schema, the PrOpen-needs-claim CEL rule, and no identity fields (admission stamps those)."""
    import jsonschema
    f = []
    expect(f, f'{what}: touches only spec.decision', (list(body), list(body.get('spec', {}))), (['spec'], ['decision']))
    dec = body.get('spec', {}).get('decision', {})
    for err in jsonschema.Draft7Validator(tbi.strict(copy.deepcopy(DECISION))).iter_errors(dec):
        f.append(f'{what}: {"/".join(map(str, err.path))}: {err.message[:160]}')
    if dec.get('phase') == 'PrOpen' and not dec.get('claim'):
        f.append(f'{what}: PrOpen without claim is refused by the CRD CEL rule')
    for k in ('decidedBy', 'decidedAt'):
        expect(f, f'{what}: never sends {k}', k in dec, False)
    return f


def actions_for(chart, responses, ex):
    out = resolved(chart, 'review-proposal', responses, ex)
    w = resolve_widgets(chart, 'review-proposal', out, ex, f'actions-{out.get("phase")}')
    return out, w, [i['resourceRefId'].replace('review-proposal-', '') for i in w['review-proposal-actions']['items']]


def check_decisions(chart):
    f = []
    ex = {'name': 'p-cccc000000000003'}
    base = [p for p in PROPOSALS if p['metadata']['name'] == ex['name']][0]

    def with_decision(decision=None, status_phase=None):
        p = copy.deepcopy(base)
        if decision is not None:
            p['spec']['decision'] = decision
        if status_phase:
            p['status']['phase'] = status_phase
        return p

    # Buttons by effective phase: Proposed both, PrOpen Reject only, Rejected / Merged / Superseded neither.
    claim = {'kind': 'BuilderPublish', 'metadata': {'name': 'review-cccc000000000003'}, 'spec': {}}
    cases = [
        ('no decision (pre-0.1.23 CRD)', None, None, {}, ['run', 'ask', 'reject', 'open-pr'], 'Proposed'),
        ('PrOpen', {'phase': 'PrOpen', 'claim': 'review-cccc000000000003', 'decidedBy': 'admin',
                    'decidedAt': '2026-09-30T08:00:00Z'}, None, {'claim': claim}, ['run', 'ask', 'reject'], 'PrOpen'),
        ('Rejected', {'phase': 'Rejected', 'reason': 'not here', 'decidedBy': 'admin',
                      'decidedAt': '2026-09-29T22:00:00Z'}, None, {}, ['run', 'ask'], 'Rejected'),
        ('Merged wins over PrOpen', {'phase': 'PrOpen', 'claim': 'review-cccc000000000003'}, 'Merged', {'claim': claim},
         ['run', 'ask'], 'Merged'),
        ('Superseded', None, 'Superseded', {}, ['run', 'ask'], 'Superseded'),
        ('claim but no decision: record it', None, None, {'claim': claim}, ['run', 'ask', 'reject', 'record'], 'Proposed'),
    ]
    for label, dec, st, over, want, phase in cases:
        p = with_decision(dec, st)
        out, w, got = actions_for(chart, detail_responses(ex['name'], proposal=p, **over), ex)
        expect(f, f'{label}: effective phase', out['phase'], phase)
        expect(f, f'{label}: actions', got, want)

    # Decision row: from spec.decision first, then status.
    out, _, _ = actions_for(chart, detail_responses(ex['name'], proposal=with_decision(cases[2][1])), ex)
    expect(f, 'Rejected decision row', {x['label']: x['value'] for x in out['facts']}['Decision'],
           'Rejected by admin · 2026-09-29 22:00 UTC · not here')
    p = with_decision({'phase': 'Rejected', 'reason': 'not here'})
    out, _, _ = actions_for(chart, detail_responses(ex['name'], proposal=p), ex)
    expect(f, 'before admission stamps it', {x['label']: x['value'] for x in out['facts']}['Decision'], 'Rejected · not here')
    p = with_decision(None); p['status'].update(phase='Rejected', decidedBy='mirror', reason='from status')
    out, _, _ = actions_for(chart, detail_responses(ex['name'], proposal=p), ex)
    expect(f, 'status is the fallback', {x['label']: x['value'] for x in out['facts']}['Decision'], 'Rejected by mirror · from status')

    # Tabs: a spec.decision moves the row at once, before nightly-review mirrors it.
    items = [with_decision(cases[2][1]) if x['metadata']['name'] == ex['name'] else x for x in PROPOSALS]
    out = resolved(chart, 'platform-reviews', list_responses(proposals={'items': items}), {})
    expect(f, 'Rejected moves to Decided', [r['name'] for r in out['decided']], [ex['name']])
    expect(f, 'decided row', (out['decided'][0]['decidedBy'], out['decided'][0]['outcome']), ('admin', 'not here'))
    resolve_widgets(chart, 'platform-reviews', out, {}, 'rejected-list')
    items = [with_decision(cases[1][1]) if x['metadata']['name'] == ex['name'] else x for x in PROPOSALS]
    out = resolved(chart, 'platform-reviews', list_responses(proposals={'items': items}), {})
    row = [r for r in out['rows'] if r['name'] == ex['name']][0]
    expect(f, 'PrOpen stays Open', (row['phase'], row['phaseColor']), ('PrOpen', 'blue'))

    # Reject: the exact bodies, with and without a reason, and the target.
    out, w, _ = actions_for(chart, detail_responses(ex['name']), ex)
    act = w['review-reject-form']['actions']['rest'][0]
    expect(f, 'Reject is a merge-patch', act['headers'], ['Content-Type: application/merge-patch+json'])
    body = apply_overrides(act['payload'], act.get('payloadToOverride'), {'reason': 'Alerts here are Alert CRs'})
    expect(f, 'Reject body', body, {'spec': {'decision': {'phase': 'Rejected', 'reason': 'Alerts here are Alert CRs'}}})
    f += decision_problems(body, 'Reject')
    body = apply_overrides(act['payload'], act.get('payloadToOverride'), {})
    expect(f, 'Reject without a reason omits it', body, {'spec': {'decision': {'phase': 'Rejected'}}})
    f += decision_problems(body, 'Reject, no reason')
    expect(f, 'reason is optional, bounded like the CRD',
           (w['review-reject-form']['schema'].get('required'), w['review-reject-form']['schema']['properties']['reason']['maxLength']),
           (None, DECISION['properties']['reason']['maxLength']))
    ref = ref_template(chart, 'Form', 'review-reject-form', 'reject-proposal', out)
    expect(f, 'Reject target', (ref['apiVersion'], ref['resource'], ref['name'], ref['namespace'], ref['verb']),
           ('review.krateo.io/v1alpha1', 'proposals', ex['name'], NS, 'PATCH'))

    # PrOpen: as the second op after the claim, and from the record fallback.
    ops = w['review-open-pr-form']['actions']['rest'][0]['ops']
    body = apply_overrides(ops[1]['payload'], ops[1].get('payloadToOverride'), {'repository': 'o/r', 'file': 'f'})
    expect(f, 'PrOpen body after the claim', body, {'spec': {'decision': {'phase': 'PrOpen', 'claim': 'review-cccc000000000003'}}})
    f += decision_problems(body, 'PrOpen after the claim')
    expect(f, 'the claim op comes first', [o['resourceRefId'] for o in ops], ['create-builder-publish', 'decide-pr-open'])
    out, w, _ = actions_for(chart, detail_responses(ex['name'], claim=claim), ex)
    rec = w['review-proposal-record']['actions']['rest'][0]
    expect(f, 'record fallback body', rec['payload'], {'spec': {'decision': {'phase': 'PrOpen', 'claim': 'review-cccc000000000003'}}})
    f += decision_problems(rec['payload'], 'record fallback')
    expect(f, 'record fallback is a merge-patch', rec['headers'], ['Content-Type: application/merge-patch+json'])
    ref = ref_template(chart, 'Button', 'review-proposal-record', 'record-pr-open', out)
    expect(f, 'record target', (ref['resource'], ref['name'], ref['verb']), ('proposals', ex['name'], 'PATCH'))
    return f


CHECKS = [
    check_list_groups_by_finding_newest_first,
    check_kind_and_target_checks,
    check_runs_tab,
    check_banner_and_empty_states,
    check_proposal_detail,
    check_change_request_claim,
    check_run_detail,
    check_decisions,
    check_steps_gated_on_input,
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
