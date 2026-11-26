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
  - Refused (historical) is excluded from every list, and the superseded line says so;
  - THE DASHBOARD (mockup A4; decisions of 2026-11-13): the header (no subtitle), the toolbar (Kind,
    Target repo, New today and the 24h / 7d / 30d range on 18/24, the search on 6/24), the five
    figures, then the proposals card (2/3) beside the charts (1/3). Kind, Target repo and New today
    count the proposals in the picked state under every OTHER filter, and the search (title,
    subject, kind, target repo) narrows the whole page; a facet with one value is left out unless
    applied (Q2); no confidence anywhere, neither facet nor statistic (Q3). The state chips (Open
    by default = Proposed + Change request open, Proposed, Change request open, Merged, Rejected, Failed, All) count every
    proposal the filters match and pick what the cards (or, ?view=table, the Full list) show; each
    figure and card wears its phase's colour (the figures' map is the cards'), the last run its
    run colour; Merged and Rejected count the decisions in the range; the trend has one point per
    bucket and series (hourly for 24h, daily otherwise, ending now); the side cards are left out
    when empty; every route keeps every other param; Ask Agentiko keeps them and names the view;
  - a Failed run's Result is status.error; evidence[].query is never rendered;
  - "Run review now" POSTs the Job `kubectl create job --from=cronjob/nightly-review` creates: the
    CronJob's jobTemplate.spec verbatim, cronjob.kubernetes.io/instantiate: manual, the CronJob as
    controller owner, named <cronjob>-manual-<n>; no CronJob read, no button;
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
import datetime
import importlib.util
import json
import os
import sys
from urllib.parse import parse_qs

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
    expect(f, 'Refused counted for the superseded line', out['refused'], 1)
    expect(f, 'evidence sources summarised', c['sources'], 'clickhouse ×2 · kubernetes')
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'list')
    ds = w['platform-proposals']['dataSource']
    expect(f, 'table rows', len(ds), 4)
    expect(f, 'rows navigate by name', w['platform-proposals']['rowNavigateTo'], '/reviews/proposals/{name}')
    expect(f, 'every row carries its name cell', all(cells(r)['name']['stringValue'] for r in ds), True)
    expect(f, 'header counter', w['reviews-page-header']['counter'], 4)
    expect(f, 'the superseded line says Refused is not listed', w['reviews-superseded']['text'],
           'Superseded: 2 proposals replaced by a newer one for the same finding. 1 historical Refused record is not listed.')
    expect(f, 'page items (no banner on a Completed latest run)',
           [i['resourceRefId'] for i in w['page-reviews']['items']],
           ['reviews-page-header', 'reviews-toolbar', 'reviews-stats', 'reviews-main'])
    expect(f, 'the superseded line sits under the cards', [i['resourceRefId'] for i in w['reviews-list-card']['items']],
           ['reviews-state-chips', 'reviews-cards', 'reviews-superseded'])
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


def check_recent_runs(chart):
    f = []
    out = resolved(chart, 'platform-reviews', list_responses(), {})
    rows = {r['name']: r for r in out['recentRuns']}
    expect(f, 'runs newest first', [r['name'] for r in out['recentRuns']],
           ['rr-20260929-1933', 'rr-20260928-0849', 'rr-20260926-0200'])
    expect(f, 'a Failed run shows status.error', rows['rr-20260926-0200']['result'], RUNS[2]['status']['error'])
    expect(f, 'a Completed run shows its count', rows['rr-20260929-1933']['result'], '2 proposed')
    expect(f, 'took', rows['rr-20260929-1933']['took'], '1 m 19 s')
    expect(f, 'sources not read', rows['rr-20260928-0849']['notRead'],
           'clickhouse: errorPatternsByService: Read timed out. (read timeout=120) · kagent-sessions: kagent returned an empty session list')
    expect(f, 'no ValidationNotes on the list', any('otes' in k for k in out['recentRuns'][0]), False)
    expect(f, 'a row\'s caption: when, how long, what it wrote', rows['rr-20260929-1933']['caption'],
           '2026-09-29 19:33 UTC · 1 m 19 s · 2 proposed')
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'recent-runs')
    tpl = w['reviews-runs']['itemTemplate']
    expect(f, 'a row opens its run', tpl['navigateTo'], '/reviews/runs/{name}')
    expect(f, 'a row wears its run colour (rv_run_color)', {r['phase']: tpl['color']['map'][r['phase']] for r in out['recentRuns']},
           {r['phase']: r['phaseColor'] for r in out['recentRuns']})
    expect(f, 'the card is drawn', 'reviews-runs-card' in items(w, 'reviews-side'), True)
    many = [run(f'rr-2026092{i}-0200', 'Completed', f'2026-09-2{i}T02:00:00+00:00', f'2026-09-2{i}T02:01:00+00:00') for i in range(9)]
    expect(f, 'the six newest', len(resolved(chart, 'platform-reviews', list_responses(runs={'items': many}), {})['recentRuns']), 6)
    out = resolved(chart, 'platform-reviews', list_responses(runs={'items': []}), {})
    expect(f, 'no run: no card', 'reviews-runs-card' in items(resolve_widgets(chart, 'platform-reviews', out, {}, 'no-runs'), 'reviews-side'), False)
    out = resolved(chart, 'platform-reviews', list_responses(runs='ERROR:dial tcp: i/o timeout'), {})
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'runs-unread')
    expect(f, 'runs unread: the card says so', ('reviews-runs-card' in items(w, 'reviews-side'), w['reviews-runs']['header']),
           (True, 'The review runs could not be read, so a run that failed would not show here.'))
    return f


def check_banner_and_empty_states(chart):
    f = []
    out = resolved(chart, 'platform-reviews', list_responses(
        proposals='ERROR:the server could not find the requested resource (404)', runs='ERROR:404 not found',
        apis=APIS), {})
    expect(f, 'not installed', out['banner']['title'], 'The nightly review is not installed on this cluster')
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'not-installed')
    expect(f, 'not installed: no filter bar, no overview', [i['resourceRefId'] for i in w['page-reviews']['items']],
           ['reviews-page-header', 'reviews-banner'])
    out = resolved(chart, 'platform-reviews', list_responses(
        proposals='ERROR:proposals.review.krateo.io is forbidden: User "u" cannot list (403)'), {})
    expect(f, 'denied is content', out['banner']['title'], 'Your role cannot read review proposals')
    out = resolved(chart, 'platform-reviews', list_responses(runs={'items': []}), {})
    expect(f, 'no run yet', out['banner']['title'], 'No review has run yet')
    out = resolved(chart, 'platform-reviews', list_responses(runs={'items': [RUNS[2]]}), {})
    expect(f, 'latest failed', (out['banner']['type'], out['banner']['title']), ('error', 'The last review failed: rr-20260926-0200'))
    out = resolved(chart, 'platform-reviews', list_responses(runs={'items': [RUNS[2]]}), {'state': 'merged'})
    w = resolve_widgets(chart, 'platform-reviews', out, {'state': 'merged'}, 'failed-latest')
    expect(f, 'nothing merged: said in place of the cards', (items(w, 'reviews-list-card')[:2], w['reviews-empty']['text']),
           (['reviews-state-chips', 'reviews-empty'], 'No proposal merged in the last 7 days.'))
    out = resolved(chart, 'platform-reviews', list_responses(runs={'items': [RUNS[2]]}), {'state': 'rejected', 'range': '24h'})
    w = resolve_widgets(chart, 'platform-reviews', out, {'state': 'rejected', 'range': '24h'}, 'none-rejected')
    expect(f, 'nothing rejected: the range said', w['reviews-empty']['text'], 'No proposal rejected in the last 24 hours.')
    decided = PROPOSALS + [proposal('p-1111000000000010', '2026-09-29T20:00:00Z', 'Merged one', phase='Merged',
                                    decidedBy='admin', decidedAt=RECENT,
                                    pullRequest={'url': 'https://github.com/o/r/pull/7', 'number': 7, 'state': 'merged'})]
    out = resolved(chart, 'platform-reviews', list_responses(proposals={'items': decided}), {'state': 'merged'})
    w = resolve_widgets(chart, 'platform-reviews', out, {'state': 'merged'}, 'decided')
    expect(f, 'the Merged chip: the merged proposal, its pull request the outcome', [(r['name'], r['outcome']) for r in out['rows']],
           [('p-1111000000000010', 'https://github.com/o/r/pull/7')])
    expect(f, 'a decided card opens the proposal', [(c['phaseLabel'], c['cta']) for c in w['reviews-cards']['dataSource']],
           [('Merged', 'Open proposal')])
    # A proposals read that failed for any other reason: the page is drawn, and says so, never 0.
    out = resolved(chart, 'platform-reviews', list_responses(proposals='ERROR:dial tcp: i/o timeout'), {})
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'proposals-unread')
    expect(f, 'proposals unread: the figures are "—", in orange',
           [(t['value'], t['caption'], t['tone']) for t in w['reviews-stats']['dataSource'][:4]], [('', 'could not be read', 'unread')] * 4)
    expect(f, 'proposals unread: those figures orange and highlighted',
           [(t['color'], t['highlight']) for t in w['reviews-stats']['dataSource'][:4]], [('orange', True)] * 4)
    expect(f, 'proposals unread: no figure is a link', {t['route'] for t in w['reviews-stats']['dataSource']}, {''})
    expect(f, 'proposals unread: the card says why', (items(w, 'reviews-list-card'), w['reviews-empty']['text'], w['reviews-empty']['type']),
           (['reviews-state-chips', 'reviews-empty'], 'The proposals could not be read (dial tcp: i/o timeout), so nothing on this page counts them.', 'warning'))
    expect(f, 'proposals unread: no chart data', (w['reviews-trend']['data'], items(w, 'reviews-side')),
           ([], ['reviews-trend-card', 'reviews-runs-card']))
    return f


# The nightly-review CronJob as krateo-057 serves it (nightly-review 0.1.29), env cut to a few entries.
CRONJOB = {
    'apiVersion': 'batch/v1', 'kind': 'CronJob',
    'metadata': {'name': 'nightly-review', 'namespace': NS, 'uid': '25fc3c34-e6c4-4c14-8111-5e78b79e1c28',
                 'labels': {'app.kubernetes.io/managed-by': 'Helm'}},
    'spec': {'schedule': '0 2 * * *', 'successfulJobsHistoryLimit': 3, 'failedJobsHistoryLimit': 3,
             'jobTemplate': {'metadata': {}, 'spec': {
                 'activeDeadlineSeconds': 3600, 'backoffLimit': 0,
                 'template': {'metadata': {}, 'spec': {
                     'restartPolicy': 'Never', 'serviceAccountName': 'nightly-review',
                     'containers': [{'name': 'review', 'image': 'ghcr.io/krateo-platformops/nightly-review:0.1.29',
                                     'env': [{'name': 'WINDOW_HOURS', 'value': '24'},
                                             {'name': 'TARGET_CHECK_TOKEN', 'valueFrom': {'secretKeyRef': {
                                                 'key': 'token', 'name': 'gh-token', 'optional': True}}}]}]}}}}},
    'status': {'lastScheduleTime': '2026-10-05T02:00:00Z'},
}


def check_run_review_now(chart):
    f = []
    out = resolved(chart, 'platform-reviews', list_responses(cronjob=CRONJOB), {})
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'run-now')
    # "Ask Agentiko →" sits immediately left of the primary it does not take (P6).
    expect(f, 'header actions', [i['resourceRefId'] for i in w['reviews-header-actions']['items']],
           ['reviews-ask-agentiko', 'reviews-run-now'])
    expect(f, 'the last run is the last figure', w['reviews-stats']['dataSource'][-1],
           {'title': 'Last run (all kinds)', 'value': 'Completed', 'caption': 'rr-20260929-1933 · 19:33 UTC', 'tone': 'Completed', 'route': '',
            'color': '', 'highlight': False})
    act = w['reviews-run-now']['actions']['rest'][0]
    body = act['payload']
    expect(f, 'a batch/v1 Job', (body['apiVersion'], body['kind']), ('batch/v1', 'Job'))
    expect(f, 'spec is the jobTemplate spec, verbatim', body['spec'], CRONJOB['spec']['jobTemplate']['spec'])
    expect(f, 'instantiate annotation', body['metadata']['annotations'], {'cronjob.kubernetes.io/instantiate': 'manual'})
    expect(f, 'controller owner is the CronJob', body['metadata']['ownerReferences'],
           [{'apiVersion': 'batch/v1', 'kind': 'CronJob', 'name': 'nightly-review',
             'uid': CRONJOB['metadata']['uid'], 'controller': True}])
    expect(f, 'sent as JSON', act['headers'], ['Content-Type: application/json'])
    expect(f, 'targets the create-job ref', act['resourceRefId'], 'create-review-job')
    expect(f, 'success names the Job', tbi.jq(expr(act['successMessage']), {'response': {'metadata': {'name': 'nightly-review-manual-1'}}}),
           'Started nightly-review-manual-1. Its run appears under Recent runs once it starts.')
    t = chart.get('Button', 'reviews-run-now')['spec']['resourcesRefsTemplate'][0]
    els = tbi.jq(expr(t['iterator']), out)
    expect(f, 'one ref with the CronJob', len(els), 1)
    ref = {k: (tbi.jq(expr(v), els[0]) if isinstance(v, str) and expr(v) is not None else v) for k, v in t['template'].items()}
    expect(f, 'POST jobs in the CronJob namespace', (ref['apiVersion'], ref['resource'], ref['namespace'], ref['verb']),
           ('batch/v1', 'jobs', NS, 'POST'))
    name = ref['name']
    if not (name.startswith('nightly-review-manual-') and name.rsplit('-', 1)[1].isdigit() and len(name) <= 63):
        f.append(f'job name {name!r} is not nightly-review-manual-<n> within 63 characters')
    out = resolved(chart, 'platform-reviews', list_responses(
        cronjob='ERROR:cronjobs.batch "nightly-review" is forbidden: User "u" cannot get (403)'), {})
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'run-now-denied')
    # No CronJob, no "Run review now" — but Ask Agentiko stays, outside that condition.
    expect(f, 'no CronJob, no button', w['reviews-header-actions']['items'], [{'resourceRefId': 'reviews-ask-agentiko'}])
    expect(f, 'no CronJob, no ref', tbi.jq(expr(t['iterator']), out), [])
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
    # The CLAIM ref's name. Asserted because omitting it broke the form twice over (frontend#475):
    # snowplow synthesises the ref's payload from these fields, so no name meant metadata.name: "",
    # and buildPayload merges the ref payload OVER the op's — the empty string won. The /call path
    # also lost its name=, which snowplow rejects on every verb including a collection POST. This
    # suite checked decide-pr-open's name and never the claim's, which is how it shipped.
    claim_ref = ref_template(chart, 'Form', 'review-open-pr-form', 'create-builder-publish', out)
    expect(f, 'claim target carries its own name', claim_ref.get('name'), 'review-cccc000000000003')
    expect(f, 'claim target verb', claim_ref['verb'], 'POST')
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
    expect(f, 'change request read back', facts.get('Change request', '').endswith('#12 open · https://github.com/krateo-platformops/observability/pull/12'), True)
    resolve_widgets(chart, 'review-proposal', out, ex, 'claimed')
    return f


def check_run_detail(chart):
    f = []
    ex = {'name': 'rr-20260929-1933'}
    out = resolved(chart, 'review-run', {'run': RUNS[0], 'all': {'items': PROPOSALS}, 'apis': APIS, 'kinds': KINDS}, ex)
    expect(f, 'steps', [(s['title'], s['status']) for s in out['steps']],
           [('Gather', 'finish'), ('Ask Agentiko', 'finish'), ('Publish', 'wait')])
    expect(f, 'queries as issued', SQL in out['queriesMarkdown'], True)
    expect(f, 'proposals of this run', [r['name'] for r in out['proposalRows']], ['p-eeee000000000005', 'p-ffff000000000006'])
    facts = {x['label']: x['value'] for x in out['facts']}
    expect(f, 'tokens', facts['Tokens'], '7535 in · 8433 out · 15968 total')
    expect(f, 'no model name row', 'Model' in facts, False)
    w = resolve_widgets(chart, 'review-run', out, ex, 'run-completed')
    # RUN-1 (UX audit, 2026-11-25): horizontal, "Succeeded" broke one letter per line.
    expect(f, 'steps run down the page', w['review-run-steps-list'].get('orientation'), 'vertical')
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


def ask(href):
    """(the path and query the link opens with ?ask removed, the decoded prompt)."""
    path, _, query = href.partition('?')
    params = parse_qs(query, keep_blank_values=True)
    prompt = params.pop('ask', [''])
    kept = '&'.join(f'{k}={v[0]}' for k, v in params.items())
    return (path + ('?' + kept if kept else ''), prompt[0])


def check_agentiko_asks(chart):
    """"Ask Agentiko →" on /reviews and on a run: the prompt is about what the page read, and a
    proposals read that failed — not installed, refused, or anything else — is said as such, never
    as "no open proposals"."""
    f = []
    out = resolved(chart, 'platform-reviews', list_responses(cronjob=CRONJOB), {})
    href, prompt = ask(out['askHref'])
    expect(f, 'list: self page, no filter to keep', href, '/reviews')
    expect(f, 'list: the open proposals, newest first, with kind and phase', prompt,
           'Help me work through the open nightly-review proposals: 4 are open — '
           '1. "Doc for chart-inspector" (Documentation, Proposed) 2. "Policy" (Policy, Proposed) '
           '3. "Legacy widget fix" (Widget, Proposed) 4. "SAR alert v3" (Alert, Proposed). '
           'The last review run is rr-20260929-1933 (Completed, started 2026-09-29 19:33 UTC). '
           'Check each against the platform\'s telemetry, and say which I should act on first, which to reject, and why.')
    w = resolve_widgets(chart, 'platform-reviews', out, {}, 'ask')
    nav = w['reviews-ask-agentiko']['actions']['navigate'][0]
    expect(f, 'button: the RA\'s link', nav['path'], out['askHref'])
    expect(f, 'button: label and intent', (w['reviews-ask-agentiko']['label'], w['reviews-ask-agentiko']['intent']),
           ('Ask Agentiko →', 'agentiko'))
    expect(f, 'button, no data: the page', tbi.jq(expr(chart.get('Button', 'reviews-ask-agentiko')['spec']['widgetDataTemplate'][0]['expression']), {})['navigate'][0]['path'], '/reviews')

    many = [proposal(f'p-{i:04d}aaaaaaaaaaaa', f'2026-09-2{i % 9}T02:00:50Z', 'x' * 150, subject=f's/{i}') for i in range(10)]
    _, prompt = ask(resolved(chart, 'platform-reviews', list_responses(proposals={'items': many}), {})['askHref'])
    expect(f, 'list: eight at most, a title cut at 110', ('8. "' in prompt, '9. "' in prompt, '"' + 'x' * 109 + '…"' in prompt), (True, False, True))

    # Empty: no open proposal, and the last run named.
    _, prompt = ask(resolved(chart, 'platform-reviews', list_responses(proposals={'items': []}), {})['askHref'])
    expect(f, 'list, empty', prompt, 'The nightly review has no open proposals. The last review run is rr-20260929-1933 '
           '(Completed, started 2026-09-29 19:33 UTC). Check the platform\'s telemetry and tell me whether that matches '
           'what you see, or whether the review missed something.')
    _, prompt = ask(resolved(chart, 'platform-reviews', list_responses(proposals={'items': []}, runs={'items': []}), {})['askHref'])
    expect(f, 'list, never run', prompt, 'The nightly review has no open proposals. No review run is recorded. '
           'Explain what the nightly review reads, and when its first proposals will appear.')
    _, prompt = ask(resolved(chart, 'platform-reviews', list_responses(runs='ERROR:dial tcp: i/o timeout'), {})['askHref'])
    expect(f, 'list, runs unread: said, not "no run"', 'The review runs could not be read (dial tcp: i/o timeout).' in prompt, True)

    # The three failed reads of the proposals, each worded for its remedy.
    for label, err, want in (
            ('not installed', 'ERROR:the server could not find the requested resource (404)',
             'The nightly review is not installed on this cluster: it serves no review.krateo.io API, so there are no '
             'proposals to read. Explain what the nightly review does and what installing it would take.'),
            ('refused', 'ERROR:proposals.review.krateo.io is forbidden: User "u" cannot list (403)',
             'I cannot read the nightly review\'s proposals on this platform (proposals.review.krateo.io is forbidden: '
             'User "u" cannot list (403)). Explain what access I need and who can grant it.'),
            ('failed', 'ERROR:dial tcp: i/o timeout',
             'The nightly review\'s proposals could not be read (dial tcp: i/o timeout). Explain what that error means '
             'and what to check.')):
        out = resolved(chart, 'platform-reviews', list_responses(proposals=err), {})
        _, prompt = ask(out['askHref'])
        expect(f, f'list, {label}', prompt, want)
        expect(f, f'list, {label}: no claim about proposals', 'no open proposals' in prompt, False)
        w = resolve_widgets(chart, 'platform-reviews', out, {}, f'ask-{label}')
        expect(f, f'list, {label}: the button is still drawn', w['reviews-header-actions']['items'], [{'resourceRefId': 'reviews-ask-agentiko'}])

    # The run page: what it did, what it did not read, what it wrote.
    for ex, run_doc, want in (
            ({'name': 'rr-20260929-1933'}, RUNS[0],
             'Explain nightly-review run rr-20260929-1933: it completed; 0 of 3 evidence sources were not read, and it wrote '
             '2 proposals. What did it find, how far can I trust it given what it did not read, and what should I do with its proposals?'),
            ({'name': 'rr-20260928-0849'}, RUNS[1],
             'Explain nightly-review run rr-20260928-0849: it completed, but saw only part of the platform; 1 of 3 evidence '
             'sources were not read (clickhouse), 1 returned nothing (kagent-sessions), and it wrote 1 proposal. What did it '
             'find, how far can I trust it given what it did not read, and what should I do with its proposals?'),
            ({'name': 'rr-20260926-0200'}, RUNS[2],
             'Explain nightly-review run rr-20260926-0200: it failed (' + RUNS[2]['status']['error'] + '); 0 of 0 evidence '
             'sources were not read, and it wrote 0 proposals. What did it find, how far can I trust it given what it did not '
             'read, and what should I do with its proposals?')):
        out = resolved(chart, 'review-run', {'run': run_doc, 'all': {'items': PROPOSALS}, 'apis': APIS, 'kinds': KINDS}, ex)
        href, prompt = ask(out['askHref'])
        expect(f, f'run {ex["name"]}: self page', href, '/reviews/runs/' + ex['name'])
        expect(f, f'run {ex["name"]}: prompt', prompt, want)
        w = resolve_widgets(chart, 'review-run', out, ex, f'ask-run-{ex["name"]}')
        expect(f, f'run {ex["name"]}: header carries the button', [i['resourceRefId'] for i in w['review-run-header']['items']],
               ['review-run-ask-agentiko'])
        expect(f, f'run {ex["name"]}: button path', w['review-run-ask-agentiko']['actions']['navigate'][0]['path'], out['askHref'])
    out = resolved(chart, 'review-run', {'run': RUNS[0], 'all': 'ERROR:dial tcp: i/o timeout', 'apis': APIS, 'kinds': KINDS},
                   {'name': 'rr-20260929-1933'})
    expect(f, 'run: proposals unread -> said', 'its proposals could not be listed' in ask(out['askHref'])[1], True)
    ex = {'name': 'rr-nope'}
    out = resolved(chart, 'review-run', {'run': 'ERROR:reviewruns "rr-nope" not found', 'all': {'items': []}, 'apis': APIS}, ex)
    expect(f, 'run not found: no link', out['askHref'], '')
    w = resolve_widgets(chart, 'review-run', out, ex, 'ask-run-missing')
    expect(f, 'run not found: the button falls back to /reviews', w['review-run-ask-agentiko']['actions']['navigate'][0]['path'], '/reviews')
    expect(f, 'run button keyExtras', chart.get('Button', 'review-run-ask-agentiko')['spec'].get('keyExtras'), ['name'])
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
        # The pill says change request, whatever the provider calls it; the phase stays the identifier.
        expect(f, f'{label}: the pill', [t['label'] for t in w['review-proposal-header']['tags']][1],
               'Change request open' if phase == 'PrOpen' else phase)
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

    # The state chips: a spec.decision moves the row at once, before nightly-review mirrors it.
    props = [with_decision(dict(cases[2][1], decidedAt=RECENT)) if x['metadata']['name'] == ex['name'] else x for x in PROPOSALS]
    out = resolved(chart, 'platform-reviews', list_responses(proposals={'items': props}), {'state': 'rejected'})
    expect(f, 'Rejected moves to the Rejected chip', [r['name'] for r in out['rows']], [ex['name']])
    expect(f, 'decided row', (out['rows'][0]['decidedBy'], out['rows'][0]['outcome']), ('admin', 'not here'))
    expect(f, 'and out of Open', ex['name'] in [r['name'] for r in resolved(chart, 'platform-reviews', list_responses(proposals={'items': props}), {})['rows']], False)
    resolve_widgets(chart, 'platform-reviews', out, {'state': 'rejected'}, 'rejected-list')
    props = [with_decision(cases[1][1]) if x['metadata']['name'] == ex['name'] else x for x in PROPOSALS]
    out = resolved(chart, 'platform-reviews', list_responses(proposals={'items': props}), {})
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


# ---------------------------------------------------------------------------------------------
# The dashboard: layout, facets, states and cards, figures, search, charts, the table view
# ---------------------------------------------------------------------------------------------

TODAY = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')
# Decided three hours ago: inside every range, so Merged and Rejected list it.
RECENT = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%SZ')
RKX = ['projects', 'kind', 'repo', 'today', 'q', 'view', 'state', 'range']
MD = {'fmt': 'markdown', 'content': '# notes\n'}
OVP = [
    proposal('p-a1', TODAY + 'T01:00:00Z', 'Alert on kagent upsert failures', kind='Alert', repo='org/obs', subject='s/a1'),
    proposal('p-a2', '2026-11-01T01:00:00Z', 'Alert on pruned fields', kind='Alert', repo='org/obs', subject='s/a2'),
    proposal('p-a3', '2026-10-01T01:00:00Z', 'Exclude cert-manager from expiry', kind='Alert', repo='org/obs', subject='s/a3', phase='PrOpen'),
    proposal('p-a4', '2026-09-01T01:00:00Z', 'Alert on gateway timeouts', kind='Alert', repo='org/agent', subject='s/a4'),
    proposal('p-d1', TODAY + 'T02:00:00Z', 'Document the endpoint Secret', kind='Documentation', repo='org/obs', subject='s/d1', **MD),
    proposal('p-d2', '2026-11-02T00:00:00Z', 'Document trace filters', kind='Documentation', repo='', subject='s/d2', **MD),
    proposal('p-w1', '2026-11-03T00:00:00Z', 'Pod sizing page', kind='Widget', content=FORM, repo='org/portal', subject='s/w1'),
    # superseded: two Alert proposals (a chain onto p-a1), one Documentation
    proposal('p-a0', '2026-08-01T00:00:00Z', 'Alert v0', kind='Alert', repo='org/obs', subject='s/a1', phase='Superseded', superseded_by='p-a1'),
    proposal('p-a00', '2026-07-01T00:00:00Z', 'Alert v00', kind='Alert', repo='org/obs', subject='s/a1', phase='Superseded', superseded_by='p-a0'),
    proposal('p-d0', '2026-08-02T00:00:00Z', 'Doc v0', kind='Documentation', repo='org/obs', subject='s/d1', phase='Superseded', superseded_by='p-d1', **MD),
    # decided, and a historical Refused
    proposal('p-m1', '2026-10-05T00:00:00Z', 'Merged alert', kind='Alert', repo='org/obs', subject='s/m1', phase='Merged',
             decidedAt=RECENT, pullRequest={'url': 'https://github.com/o/r/pull/9', 'number': 9, 'state': 'merged'}),
    proposal('p-r1', '2026-10-06T00:00:00Z', 'Rejected doc', kind='Documentation', repo='org/obs', subject='s/r1', phase='Rejected', reason='no',
             decidedAt=RECENT, **MD),
    proposal('p-x1', '2026-09-28T00:00:00Z', 'Refused alert', phase='Refused'),
]


def ov(chart, extras=None, **over):
    r = list_responses(proposals={'items': OVP}, cronjob=CRONJOB)
    r.update(over)
    ex = extras or {}
    out = resolved(chart, 'platform-reviews', r, ex)
    return out, resolve_widgets(chart, 'platform-reviews', out, ex, f'overview {ex}')


def fcounts(out, key):
    return {v['value']: v['count'] for v in [x for x in out['facets'] if x['key'] == key][0]['values']}


def items(w, name):
    return [i['resourceRefId'] for i in w[name]['items']]


def check_overview_facets(chart):
    f = []
    out, w = ov(chart)
    expect(f, 'kind counts', fcounts(out, 'kind'), {'Alert': 4, 'Documentation': 2, 'Widget': 1})
    expect(f, 'repo counts; a proposal naming none is "(no target repo)"', fcounts(out, 'repo'),
           {'org/obs': 4, 'org/agent': 1, 'org/portal': 1, '(no target repo)': 1})
    expect(f, 'new today counts', fcounts(out, 'today'), {'1': 2, '0': 5})
    expect(f, 'no Confidence facet (Q3)', [x['key'] for x in out['facets']], ['kind', 'repo', 'today'])
    expect(f, 'the bar: Kind, Target repo, New today, the range', items(w, 'reviews-filter-bar'),
           ['reviews-kind', 'reviews-repo', 'reviews-today-toggle', 'reviews-range-group'])
    expect(f, 'the Kind Select', [(o['value'], o['count']) for o in w['reviews-kind']['options']], [('Alert', 4), ('Documentation', 2), ('Widget', 1)])
    expect(f, 'the Kind Select is URL-bound and multiple', (w['reviews-kind']['queryParam'], w['reviews-kind']['mode']), ('kind', 'multiple'))
    out, w = ov(chart, {'kind': 'Alert'})
    expect(f, '?kind=Alert: the kind counts ignore their own filter', fcounts(out, 'kind'), {'Alert': 4, 'Documentation': 2, 'Widget': 1})
    expect(f, '?kind=Alert: the repo counts apply it', fcounts(out, 'repo'), {'org/obs': 3, 'org/agent': 1, 'org/portal': 0, '(no target repo)': 0})
    expect(f, '?kind=Alert: the today counts apply it', fcounts(out, 'today'), {'1': 1, '0': 3})
    expect(f, '?kind=Alert: the rows', [r['name'] for r in out['rows']], ['p-a1', 'p-a2', 'p-a3', 'p-a4'])
    # Hidden: one kind over every open proposal; every one new today.
    one = [proposal(f'p-o{i}', TODAY + f'T0{i}:00:00Z', f'Alert {i}', kind='Alert', repo=f'org/{i % 2}', subject=f's/o{i}') for i in range(4)]
    out, w = ov(chart, proposals={'items': one})
    expect(f, 'one kind, all new today: Kind and New today hidden', [(x['key'], x['show']) for x in out['facets']],
           [('kind', False), ('repo', True), ('today', False)])
    expect(f, 'the bar: Target repo and the range', items(w, 'reviews-filter-bar'), ['reviews-repo', 'reviews-range-group'])
    out, w = ov(chart, {'kind': 'Alert'}, proposals={'items': one})
    expect(f, 'a hidden facet shows while applied', items(w, 'reviews-filter-bar'), ['reviews-kind', 'reviews-repo', 'reviews-range-group'])
    out, w = ov(chart, {'state': 'merged'})
    expect(f, '?state=merged: the facets count the merged ones', fcounts(out, 'kind'), {'Alert': 1})
    return f


def check_overview_layout(chart):
    f = []
    out, w = ov(chart)
    expect(f, 'the page', items(w, 'page-reviews'), ['reviews-page-header', 'reviews-toolbar', 'reviews-stats', 'reviews-main'])
    hdr = w['reviews-page-header']
    expect(f, 'header: no subtitle, its buttons kept', ('subtitle' in hdr, items(w, 'reviews-header-actions')),
           (False, ['reviews-ask-agentiko', 'reviews-run-now']))
    expect(f, 'the toolbar: the filters left, the search in a quarter, stacked when narrow',
           [(i['resourceRefId'], i['size'], i['xs'], i['lg']) for i in w['reviews-toolbar']['items']],
           [('reviews-filter-bar', 18, 24, 18), ('reviews-search-wrap', 6, 24, 6)])
    expect(f, 'the filters: Kind, Target repo, New today, the time range', items(w, 'reviews-filter-bar'),
           ['reviews-kind', 'reviews-repo', 'reviews-today-toggle', 'reviews-range-group'])
    expect(f, 'the time range: its label and chips', (items(w, 'reviews-range-group'), w['reviews-range-label']['text']),
           (['reviews-range-label', 'reviews-range-chips'], 'Time range'))
    expect(f, 'the search wrap holds the search', items(w, 'reviews-search-wrap'), ['reviews-search'])
    s = w['reviews-search']
    expect(f, 'the search writes ?q= on Enter, at the standard size', (s['queryParam'], s['allowClear'], s['size'], s['placeholder']),
           ('q', True, 'middle', 'Search proposals · press Enter'))
    expect(f, 'two thirds and one third, stacked when narrow',
           [(i['resourceRefId'], i['size'], i['xs'], i['lg']) for i in w['reviews-main']['items']],
           [('reviews-list-card', 16, 24, 16), ('reviews-side', 8, 24, 8)])
    card = w['reviews-list-card']
    expect(f, 'the proposals card: state chips, the cards, the superseded line; Summary | Full list top right',
           (card['title'], card['titleVariant'], items(w, 'reviews-list-card'), card['extraRefId']),
           ('Proposals', 'eyebrow', ['reviews-state-chips', 'reviews-cards', 'reviews-superseded'], 'reviews-view-chips'))
    expect(f, 'the view chips', [(c['label'], c['active'], c['route']) for c in w['reviews-view-chips']['dataSource']],
           [('Summary', True, '/reviews'), ('Full list', False, '/reviews?view=table')])
    expect(f, 'the charts', items(w, 'reviews-side'), ['reviews-trend-card', 'reviews-by-state-card', 'reviews-by-kind-card', 'reviews-runs-card'])
    expect(f, 'each chart in an eyebrow card', [(w[c]['title'], w[c]['titleVariant'], items(w, c)) for c in items(w, 'reviews-side')],
           [('Proposed vs decided', 'eyebrow', ['reviews-trend']), ('By state', 'eyebrow', ['reviews-by-state']),
            ('Open by kind', 'eyebrow', ['reviews-by-kind']), ('Recent runs', 'eyebrow', ['reviews-runs'])])
    for name in ('reviews-state-chips', 'reviews-view-chips', 'reviews-range-chips'):
        tpl = w[name]['itemTemplate']
        expect(f, f'{name}: chips that navigate', (tpl['rowVariant'], tpl['navigateTo']), ('chip', '{route}'))
    out, w = ov(chart, {'view': 'table'})
    expect(f, '?view=table: the Full list instead of the cards', items(w, 'reviews-list-card'),
           ['reviews-state-chips', 'platform-proposals', 'reviews-superseded'])
    expect(f, '?view=table: the view chips', [c['active'] for c in w['reviews-view-chips']['dataSource']], [False, True])
    out, w = ov(chart, {'kind': 'Alert'})
    expect(f, 'a filter applied: its chips under the toolbar', items(w, 'page-reviews'),
           ['reviews-page-header', 'reviews-toolbar', 'reviews-active-filters', 'reviews-stats', 'reviews-main'])
    out, w = ov(chart, proposals={'items': []}, runs={'items': []})
    expect(f, 'nothing at all: the trend alone, the empty line', (items(w, 'reviews-side'), items(w, 'reviews-list-card'), w['reviews-empty']['text']),
           (['reviews-trend-card'], ['reviews-state-chips', 'reviews-empty'], 'There are no proposals.'))
    return f


CARD_COLOURS = {'Proposed': 'gray', 'PrOpen': 'blue', 'Merged': 'gray', 'Rejected': 'gray', 'Failed': 'red'}
RUN_COLOURS = {'Completed': 'gray', 'PartiallyCompleted': 'orange', 'Running': 'blue', 'Pending': 'gray', 'Failed': 'red'}


def check_overview_figures(chart):
    f = []
    out, w = ov(chart)
    expect(f, 'the five figures, no confidence (Q3)', [(t['title'], t['value'], t['caption'], t['tone']) for t in w['reviews-stats']['dataSource']], [
        ('Waiting for a decision', '6', '2 new today', 'Proposed'), ('Change request open', '1', 'waiting for its merge', 'PrOpen'),
        ('Merged', '1', 'in the last 7 days', 'Merged'), ('Rejected', '1', 'in the last 7 days', 'Rejected'),
        ('Last run (all kinds)', 'Completed', 'rr-20260929-1933 · 19:33 UTC', 'Completed')])
    tpl = w['reviews-stats']['itemTemplate']
    cmap = w['reviews-cards']['itemTemplate']['color']['map']
    expect(f, 'statistic tiles', (tpl['rowVariant'], tpl['primaryText'], tpl['secondaryText'], tpl['subPrimaryText']),
           ('statistic', '{title}', '{value}', '{caption}'))
    expect(f, 'the strip takes colour and border from the data (portal.tileDefs)', (tpl['color'], tpl.get('highlight')),
           ({'value': '{color}'}, '{highlight}'))
    expect(f, 'a proposal waiting is work, not a fault: the four proposal figures neutral at any count; a completed run too',
           [(t['value'], t['color'], t['highlight']) for t in w['reviews-stats']['dataSource']],
           [('6', '', False), ('1', '', False), ('1', '', False), ('1', '', False), ('Completed', '', False)])
    expect(f, 'the cards\' colours are the table\'s Status tag (rv_phase_color)', cmap, CARD_COLOURS)
    expect(f, 'the run rows are rv_run_color', w['reviews-runs']['itemTemplate']['color']['map'], RUN_COLOURS)
    out, w = ov(chart, {'kind': 'Alert'})
    expect(f, '?kind=Alert: the figures count the filtered set, the last run every kind\'s',
           [t['value'] for t in w['reviews-stats']['dataSource']], ['3', '1', '1', '0', 'Completed'])
    expect(f, '?kind=Alert: the header counter stays every open proposal', w['reviews-page-header']['counter'], 7)
    out, w = ov(chart, runs={'items': [RUNS[2]]})
    last = lambda w: (w['reviews-stats']['dataSource'][-1]['color'], w['reviews-stats']['dataSource'][-1]['highlight'])
    expect(f, 'a failed last run: red (its run row\'s colour), highlighted', last(w), (RUN_COLOURS['Failed'], True))
    out, w = ov(chart, runs={'items': [RUNS[1]]})
    expect(f, 'a partial last run: orange (its run row\'s colour), highlighted', last(w), (RUN_COLOURS['PartiallyCompleted'], True))
    out, w = ov(chart, runs='ERROR:dial tcp: i/o timeout')
    expect(f, 'runs unread: "—", in orange, highlighted', w['reviews-stats']['dataSource'][-1],
           {'title': 'Last run (all kinds)', 'value': '', 'caption': 'could not be read', 'tone': 'unread', 'route': '',
            'color': 'orange', 'highlight': True})
    out, w = ov(chart, runs={'items': []})
    expect(f, 'no run yet: "—", said, neutral', w['reviews-stats']['dataSource'][-1],
           {'title': 'Last run (all kinds)', 'value': '', 'caption': 'no review has run yet', 'tone': 'none', 'route': '',
            'color': '', 'highlight': False})
    expect(f, 'the tiles navigate to their route', tpl.get('navigateTo'), '{route}')
    ex = {'q': 'a', 'view': 'table', 'range': '30d', 'kind': 'Alert', 'today': '1', 'state': 'merged'}
    out, w = ov(chart, ex)
    chip = {c['label']: c['route'] for c in out['stateChips']}
    expect(f, 'a state tile routes as its state chip (q, view, range, kind, today kept); the last run is no link',
           [(t['title'], t['route']) for t in w['reviews-stats']['dataSource']],
           [('Waiting for a decision', chip['Proposed']), ('Change request open', chip['Change request open']), ('Merged', chip['Merged']),
            ('Rejected', chip['Rejected']), ('Last run (all kinds)', '')])
    expect(f, 'the routes keep every param', chip['Change request open'], '/reviews?kind=Alert&today=1&q=a&view=table&state=propen&range=30d')
    out, _ = ov(chart, {}, proposals={'items': []})
    expect(f, 'a state at 0 is still a link', [t['route'] for t in out['stats']][:4],
           ['/reviews?state=proposed', '/reviews?state=propen', '/reviews?state=merged', '/reviews?state=rejected'])
    for rng, merged, rejected in (('24h', '1', '0'), ('7d', '1', '1'), ('30d', '2', '2')):
        o, w = timed(chart, {'range': rng})
        text = {'24h': 'in the last 24 hours', '7d': 'in the last 7 days', '30d': 'in the last 30 days'}[rng]
        expect(f, f'{rng}: Merged and Rejected in the range', [(t['value'], t['caption']) for t in o['stats'][2:4]],
               [(merged, text), (rejected, text)])
        expect(f, f'{rng}: Waiting and Change request open are now', [t['value'] for t in o['stats'][:2]], ['3', '1'])
    # A tile, its chip and the list it opens agree: Merged and Rejected list only what was decided in
    # the range (decidedAt, as the tile reads it), on the cards and on the Full list. A merge with no
    # readable decidedAt is in no range. Waiting and Change request open are current states: they agree too.
    undated = proposal('p-tm3', iso(40 * DAY), 'Merged, no date', subject='t/m3', phase='Merged')
    want = {'24h': {'merged': ['p-tm1'], 'rejected': []}, '7d': {'merged': ['p-tm1'], 'rejected': ['p-tr1']},
            '30d': {'merged': ['p-tm1', 'p-tm2'], 'rejected': ['p-tr1', 'p-tr2']}}
    tiles = {'proposed': 'Waiting for a decision', 'propen': 'Change request open', 'merged': 'Merged', 'rejected': 'Rejected'}
    chips = {'proposed': 'Proposed', 'propen': 'Change request open', 'merged': 'Merged', 'rejected': 'Rejected'}
    for rng in ('24h', '7d', '30d'):
        for state in tiles:
            got = {}
            for view in ('', 'table'):
                ex = {'range': rng, 'state': state, 'view': view}
                o, w = ov(chart, ex, proposals={'items': TIMED + [undated]}, runs={'items': TIMED_RUNS})
                tile = [t for t in w['reviews-stats']['dataSource'] if t['title'] == tiles[state]][0]
                chip = [c for c in w['reviews-state-chips']['dataSource'] if c['label'] == chips[state]][0]
                listed = ([cells(r)['name']['stringValue'] for r in w['platform-proposals']['dataSource']] if view
                          else [c['name'] for c in w['reviews-cards']['dataSource']]) if o['matched'] else []
                got[view or 'cards'] = (tile['value'], chip['count'], sorted(listed))
            if state in want[rng]:
                names = want[rng][state]
                expect(f, f'{rng} {state}: tile == chip == cards == table, decided in the range only', got,
                       {v: (str(len(names)), len(names), names) for v in ('cards', 'table')})
            else:
                n = got['cards'][1]
                expect(f, f'{rng} {state}: tile == chip == cards == table', [(a, b, len(c)) for a, b, c in got.values()],
                       [(str(n), n, n)] * 2)
    return f


def check_overview_states_and_cards(chart):
    f = []
    out, w = ov(chart, {'projects': 'team-a'})
    expect(f, 'the state chips: Open first and active, each counting every proposal the filters match',
           [(c['label'], c['count'], c['active'], c['route']) for c in w['reviews-state-chips']['dataSource']],
           [('Open', 7, True, '/reviews?projects=team-a'), ('Proposed', 6, False, '/reviews?projects=team-a&state=proposed'),
            ('Change request open', 1, False, '/reviews?projects=team-a&state=propen'), ('Merged', 1, False, '/reviews?projects=team-a&state=merged'),
            ('Rejected', 1, False, '/reviews?projects=team-a&state=rejected'), ('Failed', 0, False, '/reviews?projects=team-a&state=failed'),
            ('All', 9, False, '/reviews?projects=team-a&state=all')])
    expect(f, 'Open: Proposed and Change request open, grouped by finding, newest first', [r['name'] for r in out['rows']],
           ['p-d1', 'p-a1', 'p-w1', 'p-d2', 'p-a2', 'p-a3', 'p-a4'])
    c = w['reviews-cards']['dataSource'][0]
    expect(f, 'a card: title, phase, kind, target, the finding, the day, what it replaces',
           (c['title'], c['phaseLabel'], c['kind'], c['target'], c['body'], c['cta']),
           ('Document the endpoint Secret', 'Proposed', 'Documentation', 'org/obs', f's/d1 · proposed {TODAY} · replaces 1 earlier', 'Decide'))
    tpl = w['reviews-cards']['itemTemplate']
    expect(f, 'a card opens the proposal', (tpl['rowVariant'], tpl['navigateTo'], tpl['cardCta'], tpl['secondaryText'], tpl['color']['value']),
           ('card', '/reviews/proposals/{name}', '{cta}', '{phaseLabel}', '{phase}'))
    expect(f, 'a Change request open card says so, in blue', [(x['phaseLabel'], tpl['color']['map'][x['phase']]) for x in w['reviews-cards']['dataSource'] if x['name'] == 'p-a3'],
           [('Change request open', 'blue')])
    expect(f, 'every row\'s phaseColor is its card colour', all(tpl['color']['map'][r['phase']] == r['phaseColor'] for r in out['rows']), True)
    expect(f, 'no confidence on a card (Q3)', 'onfidence' in json.dumps(w['reviews-cards']), False)
    for ex, want in (({'state': 'propen'}, ['p-a3']), ({'state': 'MERGED'}, ['p-m1']), ({'state': 'rejected'}, ['p-r1']),
                     ({'state': 'proposed', 'kind': 'Alert'}, ['p-a1', 'p-a2', 'p-a4'])):
        expect(f, f'{ex}: the rows', [r['name'] for r in ov(chart, ex)[0]['rows']], want)
    out, w = ov(chart, {'state': 'all'})
    expect(f, 'All: every state', (out['matched'], [c['active'] for c in out['stateChips']][-1]), (9, True))
    out, w = ov(chart, {'state': 'bogus'})
    expect(f, 'an unknown state lists nothing, the chips still drawn, none active',
           (out['rows'], len(out['stateChips']), any(c['active'] for c in out['stateChips']), w['reviews-empty']['text']),
           ([], 7, False, 'No proposal is in this state.'))
    out, w = ov(chart, {'state': 'failed'})
    expect(f, 'an empty state: said', (items(w, 'reviews-list-card')[:2], w['reviews-empty']['text']),
           (['reviews-state-chips', 'reviews-empty'], 'No proposal failed.'))
    out, w = ov(chart, {'kind': 'Alert'})
    expect(f, 'superseded under the filter: the two Alert ones', (out['supersededInView'], w['reviews-superseded']['text']),
           (2, 'Superseded: 2 proposals in this view replaced by a newer one for the same finding. 1 historical Refused record is not listed.'))
    many = [proposal(f'p-m{i:02d}', f'2026-10-{i + 1:02d}T00:00:00Z', f'Alert {i}', subject=f's/m{i}') for i in range(30)]
    out, w = ov(chart, proposals={'items': many})
    expect(f, 'past 24: the first 24 as cards, a line for the rest', (len(w['reviews-cards']['dataSource']), out['more'],
           items(w, 'reviews-list-card'), w['reviews-more']['text']),
           (24, 6, ['reviews-state-chips', 'reviews-cards', 'reviews-more'], '24 of 30 shown here. Full list has all 30.'))
    out, w = ov(chart, {'view': 'table'}, proposals={'items': many})
    expect(f, 'the Full list has all of them', len(w['platform-proposals']['dataSource']), 30)
    return f


def check_overview_search(chart):
    f = []
    out, w = ov(chart, {'q': '  DOCUMENT '})
    expect(f, 'case-insensitive, trimmed: title, subject or kind', (out['q'], [r['name'] for r in out['rows']]), ('DOCUMENT', ['p-d1', 'p-d2']))
    expect(f, 'the facet counts follow it', fcounts(out, 'kind'), {'Alert': 0, 'Documentation': 2, 'Widget': 0})
    expect(f, 'the state counts follow it', [(c['label'], c['count']) for c in out['stateChips']],
           [('Open', 2), ('Proposed', 2), ('Change request open', 0), ('Merged', 0), ('Rejected', 1), ('Failed', 0), ('All', 3)])
    expect(f, 'the figures follow it', [t['value'] for t in out['stats'][:2]], ['2', '0'])
    expect(f, 'the charts follow it', (out['byState'], [k['name'] for k in out['byKind']]),
           ([{'state': 'Proposed', 'count': 2}, {'state': 'Rejected', 'count': 1}], ['Documentation']))
    expect(f, 'every route keeps it, trimmed', (out['stateChips'][1]['route'], out['viewChips'][1]['route'], out['rangeChips'][0]['route'], out['byKind'][0]['href']),
           ('/reviews?q=DOCUMENT&state=proposed', '/reviews?q=DOCUMENT&view=table', '/reviews?q=DOCUMENT&range=24h', '/reviews?kind=Documentation&q=DOCUMENT'))
    href, prompt = ask(out['askHref'])
    expect(f, 'Ask Agentiko keeps it and says the view is searched', (href, '(search "DOCUMENT")' in prompt), ('/reviews?q=DOCUMENT', True))
    expect(f, 'by target repo too', [r['name'] for r in ov(chart, {'q': 'org/portal'})[0]['rows']], ['p-w1'])
    out, w = ov(chart, {'q': 'zzz'})
    expect(f, 'nothing matches: said', (items(w, 'reviews-list-card')[:2], w['reviews-empty']['text']),
           (['reviews-state-chips', 'reviews-empty'], 'No proposal matches "zzz".'))
    expect(f, 'nothing matches: the prompt says so, not "no proposals"', ask(out['askHref'])[1].startswith(
        'None of the 7 open nightly-review proposals is in the view on screen (search "zzz").'), True)
    out, w = ov(chart, {'kind': 'Policy'})
    expect(f, 'a filter matching nothing: said', w['reviews-empty']['text'], 'No proposal matches these filters.')
    return f


def iso(delta):
    return (NOW - delta).strftime('%Y-%m-%dT%H:%M:%SZ')


H, DAY = datetime.timedelta(hours=1), datetime.timedelta(days=1)
NOW = datetime.datetime.now(datetime.timezone.utc)
TIMED = [
    proposal('p-t1', iso(2 * H), 'Two hours old', subject='t/1'),
    proposal('p-t2', iso(30 * H), 'Thirty hours old', kind='Widget', content=FORM, subject='t/2'),
    proposal('p-t3', iso(3 * DAY), 'Three days old', subject='t/3', phase='PrOpen'),
    proposal('p-t4', iso(10 * DAY), 'Ten days old', subject='t/4'),
    # decided: merged 5 h and 12 d ago (the service's status.decidedAt; the PrOpen decision before it
    # must not count), rejected 2 d and 20 d ago (spec.decision); all proposed 40 d ago.
    proposal('p-tm1', iso(40 * DAY), 'Merged recently', subject='t/m1', phase='Merged', decidedAt=iso(5 * H)),
    proposal('p-tm2', iso(40 * DAY), 'Merged earlier', subject='t/m2', phase='Merged', decidedAt=iso(12 * DAY)),
    proposal('p-tr1', iso(40 * DAY), 'Rejected recently', subject='t/r1', phase='Rejected'),
    proposal('p-tr2', iso(40 * DAY), 'Rejected earlier', subject='t/r2', phase='Rejected'),
]
TIMED[4]['spec']['decision'] = {'phase': 'PrOpen', 'claim': 'review-tm1', 'decidedAt': iso(35 * DAY)}
TIMED[6]['spec']['decision'] = {'phase': 'Rejected', 'decidedAt': iso(2 * DAY)}
TIMED[7]['spec']['decision'] = {'phase': 'Rejected', 'decidedAt': iso(20 * DAY)}
TIMED_RUNS = [run('rr-t1', 'Failed', iso(1 * H), iso(1 * H)), run('rr-t2', 'Completed', iso(1 * DAY), iso(1 * DAY)),
              run('rr-t3', 'Failed', iso(4 * DAY), iso(4 * DAY))]


def timed(chart, extras=None):
    return ov(chart, extras, proposals={'items': TIMED}, runs={'items': TIMED_RUNS})


def check_overview_trend_and_charts(chart):
    f = []
    for rng, unit, n, want in (('24h', 'hour', 24, (1, 1, 1)), ('7d', 'day', 7, (3, 2, 2)), ('30d', 'day', 30, (4, 4, 2))):
        o, w = timed(chart, {'range': rng})
        by = {s: [p for p in o['trend'] if p['series'] == s] for s in ('Proposed', 'Decided', 'Runs failed')}
        size = 3600 if unit == 'hour' else 86400
        expect(f, f'{rng}: one point per bucket and series, per {unit}', ([len(v) for v in by.values()], o['trendUnit']), ([n] * 3, unit))
        pts = by['Proposed']
        expect(f, f'{rng}: the buckets are consecutive and end now',
               ({b['t'] - a['t'] for a, b in zip(pts, pts[1:])}, pts[-1]['t'] <= NOW.timestamp() < pts[-1]['t'] + size), ({size}, True))
        expect(f, f'{rng}: proposed, decided and failed runs in the range', tuple(sum(p['v'] for p in v) for v in by.values()), want)
        lc = w['reviews-trend']
        expect(f, f'{rng}: the chart', (lc['xTimeUnit'], lc['xField'], lc['yField'], lc['colorField'], len(lc['data'])), (unit, 't', 'v', 'series', 3 * n))
        expect(f, f'{rng}: the range chip', [c['label'] for c in o['rangeChips'] if c['active']], [rng])
    # A decision is blue, not Merged's gray, which is Proposed's line.
    expect(f, 'the trend\'s lines have a colour each', w['reviews-trend']['colorMap'],
           {'Proposed': CARD_COLOURS['Proposed'], 'Decided': 'blue', 'Runs failed': RUN_COLOURS['Failed']})
    o, w = timed(chart, {'range': '1y'})
    expect(f, 'an unknown range is 7d', (o['range'], o['trendUnit'], len(o['trend'])), ('7d', 'day', 21))
    out, w = ov(chart)
    pie = w['reviews-by-state']
    expect(f, 'by state: a slice per state with proposals', pie['data'],
           [{'state': 'Proposed', 'count': 6}, {'state': 'Change request open', 'count': 1}, {'state': 'Merged', 'count': 1}, {'state': 'Rejected', 'count': 1}])
    # The cards' colours, but Merged is slate beside Proposed's gray: two slices of one gray read as one.
    expect(f, 'by state: the cards\' colours', pie['colorMap'],
           dict({('Change request open' if k == 'PrOpen' else k): v for k, v in CARD_COLOURS.items()}, Merged='slate'))
    out, w = ov(chart, {'projects': 'team-a', 'kind': 'Alert', 'state': 'merged'})
    expect(f, 'open by kind: every filter but kind, a bar against the largest, a row opens that kind\'s open ones',
           [(k['name'], k['count'], k['percent'], k['href']) for k in w['reviews-by-kind']['dataSource']],
           [('Alert', 4, 100, '/reviews?projects=team-a&kind=Alert'), ('Documentation', 2, 50, '/reviews?projects=team-a&kind=Documentation'),
            ('Widget', 1, 25, '/reviews?projects=team-a&kind=Widget')])
    tpl = w['reviews-by-kind']['itemTemplate']
    expect(f, 'open by kind: bars that navigate', (tpl['navigateTo'], tpl['bar']['percent'], tpl['bar']['label']), ('{href}', '{percent}', '{count}'))
    out, w = ov(chart, proposals={'items': [p for p in OVP if p['status']['phase'] not in ('Proposed', 'PrOpen')]})
    expect(f, 'no open proposal: no by-kind card', 'reviews-by-kind-card' in items(w, 'reviews-side'), False)
    return f


def check_overview_today_and_table(chart):
    f = []
    out, w = ov(chart, {'projects': 'team-a'})
    expect(f, 'New today: a chip with its count, writing ?today=1', w['reviews-today-toggle']['dataSource'],
           [{'label': 'New today', 'count': 2, 'active': False, 'href': '/reviews?projects=team-a&today=1'}])
    out, w = ov(chart, {'projects': 'team-a', 'today': '1'})
    expect(f, '?today=1: the proposals of today', [r['name'] for r in out['rows']], ['p-d1', 'p-a1'])
    expect(f, '?today=1: the chip active, removing it', w['reviews-today-toggle']['dataSource'][0],
           {'label': 'New today', 'count': 2, 'active': True, 'href': '/reviews?projects=team-a'})
    expect(f, '?today=1: an applied-filter chip too', [c['label'] for c in out['chips']], ['New today: yes', 'Clear all'])
    ex = {'projects': 'team-a', 'kind': 'Alert', 'view': 'table', 'range': '30d'}
    out, w = ov(chart, ex)
    expect(f, '?view=table: the table holds what the filters match', [cells(r)['name']['stringValue'] for r in w['platform-proposals']['dataSource']],
           ['p-a1', 'p-a2', 'p-a3', 'p-a4'])
    expect(f, '?view=table: Summary drops ?view and keeps every other param',
           [(c['label'], c['route']) for c in w['reviews-view-chips']['dataSource']],
           [('Summary', '/reviews?projects=team-a&kind=Alert&range=30d'), ('Full list', '/reviews?projects=team-a&kind=Alert&view=table&range=30d')])
    expect(f, 'the range chips keep every other param, 7d left out', [c['route'] for c in out['rangeChips']],
           ['/reviews?projects=team-a&kind=Alert&view=table&range=24h', '/reviews?projects=team-a&kind=Alert&view=table',
            '/reviews?projects=team-a&kind=Alert&view=table&range=30d'])
    href, prompt = ask(ov(chart, dict(ex, view='', range=''))[0]['askHref'])
    expect(f, 'Ask Agentiko keeps every filter param', href, '/reviews?projects=team-a&kind=Alert')
    expect(f, 'and the prompt names the view', prompt,
           'Help me work through the open nightly-review proposals in the view on screen (Kind: Alert): 4 of 7 open — '
           '1. "Alert on kagent upsert failures" (Alert, Proposed) 2. "Alert on pruned fields" (Alert, Proposed) '
           '3. "Exclude cert-manager from expiry" (Alert, PrOpen) 4. "Alert on gateway timeouts" (Alert, Proposed). '
           'The last review run is rr-20260929-1933 (Completed, started 2026-09-29 19:33 UTC). '
           'Check each against the platform\'s telemetry, and say which I should act on first, which to reject, and why.')
    href, prompt = ask(ov(chart, {'state': 'merged', 'range': '24h'})[0]['askHref'])
    expect(f, 'a decided state: kept, and the prompt reviews it', (href, prompt.startswith(
        'Review the nightly-review proposals in the view on screen (state: Merged in the last 24 hours): 1 of 9 — 1. "Merged alert" (Alert, Merged).')),
        ('/reviews?state=merged&range=24h', True))
    _, prompt = ask(ov(chart, {'kind': 'Policy', 'today': '1'})[0]['askHref'])
    expect(f, 'an empty view: said, with the filter', prompt,
           'None of the 7 open nightly-review proposals is in the view on screen (Kind: Policy; new today). '
           'The last review run is rr-20260929-1933 (Completed, started 2026-09-29 19:33 UTC). Tell me whether that filter hides anything I should look at.')
    names = []
    for d in chart.docs:
        n = d.get('metadata', {}).get('name', '')
        if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/') and (n.startswith('reviews-') or n in ('page-reviews', 'platform-proposals')):
            names.append(n)
            expect(f, f'{d["kind"]} {n} declares every filter key', d['spec'].get('keyExtras'), RKX)
    expect(f, 'the new widgets are among them', {'reviews-toolbar', 'reviews-stats', 'reviews-main', 'reviews-side', 'reviews-trend',
           'reviews-by-state', 'reviews-by-kind', 'reviews-runs', 'reviews-cards', 'reviews-range-chips'} - set(names), set())
    expect(f, 'the page root names them', chart.get('Flex', 'page-reviews')['metadata']['annotations'].get('krateo.io/filter-keys'), ','.join(RKX))
    return f

CHECKS = [
    check_list_groups_by_finding_newest_first,
    check_kind_and_target_checks,
    check_recent_runs,
    check_banner_and_empty_states,
    check_run_review_now,
    check_agentiko_asks,
    check_proposal_detail,
    check_change_request_claim,
    check_run_detail,
    check_decisions,
    check_steps_gated_on_input,
    check_overview_facets,
    check_overview_layout,
    check_overview_figures,
    check_overview_states_and_cards,
    check_overview_search,
    check_overview_trend_and_charts,
    check_overview_today_and_table,
]


def main():
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        i = args.index('--crds')
        crds_dir = args[i + 1]
        del args[i:i + 2]
    chart = tbi.Chart(tbi.render(args[0]) if args else tbi.render_portal_and_pages())
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
