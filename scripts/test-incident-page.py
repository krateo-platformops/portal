#!/usr/bin/env python3
"""
test-incident-page — the incident page's layout, evaluated end to end.

WHAT IT GUARDS:
  - the title: the alert's display name with a decorative emoji dropped and its punctuation (an em
    dash) kept, one space between words.
  - the chips under the title: the alert link, its state, "since", and "Nth incident from this
    alert" (counting earlier incidents only); no composition chip.
  - the Summary: Why, Fix and Who fixes it, as label/value rows.
  - one Reasoning card, its tabs in order: Reasoning trace, Evidence · N, Gaps & assumptions,
    each only with its data.
  - Evidence: "Log lines & metrics" (each source that is not one readable object: its ref, its
    excerpt in full, its kind) above "Resources" (# · Type · Name · Namespace, a row per cited
    object, then the objects read and not cited). One numbering across all of it, log rows
    first, the uncited objects last, and the trace cites by it; an index outside status.sources is dropped. An object ref reads as
    <gvr>/<namespace>/<name>, tolerating "k8s:", "core/" and a missing empty namespace.
  - Gaps & assumptions as two plain lists, not a warning.
  - How to fix: each step opens on its last run (no exit code, no sentence explaining the step);
    an applyAction keeps the one line saying what Apply writes. The script follows inline.
  - the bottom tabs: Incident, Alert (while it exists), each one object.

HOW. As test-incident-tickets: the chart is rendered, incident-detail is resolved over a fixture
shaped like kind-krateo-4's sre-telemetry-pipeline-stalled-20261112-131436, and the widgets are
evaluated over its output.

With --crds DIR or --pinned, every widget resolved here is validated against its CRD.
Usage: test-incident-page.py [--crds DIR | --pinned]. Exit code = failed checks.
"""
import copy
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(mod, file):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tit = _load('tit', 'test-incident-tickets.py')
tbi, expect, Err, ids = tit.tbi, tit.expect, tit.Err, tit.ids

NS = 'krateo-system'
ALERT = 'sre-telemetry-pipeline-stalled'
NAME = f'{ALERT}-20261112-131436'
EX = {'name': NAME, 'namespace': NS}

CH = 'krateo-clickstack-clickhouse-clickhouse-0-0-0'
NODE = 'krateo-4-control-plane'
# Index → number shown: log rows (1, 3, 5, 6) take 1-4, object rows (0, 2, 4) take 5-7.
SOURCES = [
    {'type': 'object', 'ref': f'observability.krateo.io/v1alpha1/alerts/{NS}/{ALERT}',
     'excerpt': "spec: {interval: '15m', threshold: 100, thresholdType: below}, status: {state: OK}"},
    {'type': 'metrics', 'ref': 'Log records per 15 minutes in otel_logs',
     'excerpt': '2026-11-12 13:15:00 count: 10954; no rows between 11:05 and 13:00'},
    {'type': 'object', 'ref': f'k8s:core/v1/pods/{NS}/{CH}', 'excerpt': 'Ready: True, Restart Count: 2'},
    {'type': 'logs', 'ref': 'Previous logs of the otel-collector-deployment pod',
     'excerpt': 'Error: cannot start pipelines: dial tcp: lookup krateo-clickstack-clickhouse-clickhouse-headless.krateo-system.svc: no such host'},
    {'type': 'object', 'ref': f'v1/nodes//{NODE}', 'excerpt': 'Allocatable pods: 250'},
    {'type': 'object', 'ref': f'pod/otel-collector-agent-qzpfv in {NS}', 'excerpt': 'State: Running'},
    {'type': 'events', 'ref': 'BackOff events for pod otel-collector-agent-qzpfv', 'excerpt': 'Back-off restarting failed container'},
]


def incident(name=NAME, created='2026-11-12T13:14:42Z', **status):
    st = {
        'state': 'Open', 'completedAt': '2026-11-12T13:26:46Z',
        'rootCause': {'statement': 'The dead-man switch fired because the cluster had been up for less than its 15-minute window.',
                      'confidence': '0.95', 'category': 'config', 'owner': 'krateo',
                      'ownerEvidence': [{'kind': 'config', 'ref': f'observability.krateo.io/Alert/{NS}/{ALERT}',
                                         'note': 'Default Alert CR deployed by Krateo observability stack without a post-boot grace period.'}]},
        'analyzedResources': [
            {'gvr': 'observability.krateo.io/v1alpha1/alerts', 'name': ALERT, 'namespace': NS,
             'whatWasRead': 'spec and status (threshold 100 below, interval 15m, stat'},
            {'gvr': 'composition.krateo.io/v0-1-45/krateoobservabilities', 'name': 'krateo-observability', 'namespace': NS},
            {'gvr': 'v1/pods', 'name': CH, 'namespace': NS},
            {'gvr': 'v1/nodes', 'name': NODE}],
        'sources': SOURCES,
        'missingContext': ['Logs between 11:05Z and 13:00Z are absent.'],
        'assumptions': ['The composition controller may recreate the Alert.'],
        'reasoningTrace': [{'step': 1, 'statement': 'The alert counts logs over 15 minutes.', 'evidenceRefs': [0]},
                           {'step': 2, 'statement': 'The cluster was stopped from 11:05 to 13:00.', 'evidenceRefs': [3, 1, 1, 9]},
                           {'step': 3, 'statement': 'Nothing cited.', 'evidenceRefs': []},
                           {'step': 4, 'statement': 'ClickHouse and the node are healthy.', 'evidenceRefs': [4, 2, 6]}],
        'howToFix': {'precondition': '#!/usr/bin/env bash\n# Holds while the alert exists.\nexit 1\n',
                     'apply': f'#!/usr/bin/env bash\n# Delete the spurious dead-man switch alert {ALERT}.\nkubectl delete alerts.observability.krateo.io {ALERT} -n {NS}\n',
                     'verify': '#!/usr/bin/env bash\n# Fixed once the alert is gone.\nexit 0\n',
                     'rollback': '#!/usr/bin/env bash\n# Undo apply.\nexit 0\n',
                     'applyAction': {'verb': 'delete', 'apiVersion': 'observability.krateo.io/v1alpha1', 'resource': 'alerts',
                                     'namespace': NS, 'name': ALERT}},
        'lastChecks': {'precondition': {'exit': 1, 'since': '2026-11-12T13:26:49Z'}},
    }
    st.update(status)
    return {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Incident',
            'metadata': {'name': name, 'namespace': NS, 'uid': f'uid-{name}', 'creationTimestamp': created,
                         'labels': {'observability.krateo.io/alert': ALERT},
                         'managedFields': [{'manager': 'kubectl'}]},
            'spec': {'alertRef': {'name': ALERT, 'namespace': NS}, 'triggeredAt': created},
            'status': st}


def alert():
    return {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Alert',
            'metadata': {'name': ALERT, 'namespace': NS, 'managedFields': [{'manager': 'helm'}]},
            'spec': {'displayName': 'Telemetry pipeline stalled', 'interval': '15m', 'threshold': 100, 'thresholdType': 'below'},
            'status': {'state': 'OK', 'okSince': '2026-11-12T13:15:36.197669+00:00'}}


# Three earlier incidents of the same alert, and a later one that must not move the number.
EARLIER = [incident(f'{ALERT}-2026111{d}-0900', f'2026-11-1{d}T09:00:00Z') for d in (0, 1)] + \
          [incident(f'{ALERT}-20261112-0952', '2026-11-12T09:52:34Z')]
LATER = [incident(f'{ALERT}-20261112-1600', '2026-11-12T16:00:00Z')]

WIDGETS = (('Flex', 'page-incident-detail'), ('Flex', 'incident-detail-chips'), ('Tag', 'incident-alert-ordinal'),
           ('Card', 'incident-summary-card'), ('Descriptions', 'incident-summary-desc'), ('Card', 'incident-why-card'),
           ('Tabs', 'incident-why-tabs'), ('Steps', 'incident-trace'), ('Flex', 'incident-evidence'),
           ('Paragraph', 'incident-evidence-logs-title'), ('Listy', 'incident-evidence-logs'),
           ('Paragraph', 'incident-evidence-resources-title'), ('Table', 'incident-evidence-resources'),
           ('Markdown', 'incident-gaps'), ('Tabs', 'incident-detail-tabs'), ('YamlViewer', 'incident-manifest'),
           ('YamlViewer', 'incident-alert-manifest')) + \
          tuple(x for i in range(4) for x in (('Flex', f'incident-rem-step-{i}'), ('Markdown', f'incident-rem-step-md-{i}')))


def page(inc, label, siblings=(), alert_obj=None, chart=None):
    responses = {'incident': inc, 'incidents': {'items': [inc] + list(siblings)},
                 'incidentApplies': Err('the server could not find the requested resource'),
                 'tickets': Err(tit.NO_CRD),
                 'alert': alert_obj if alert_obj is not None else Err(f'alerts.observability.krateo.io "{ALERT}" not found')}
    out, _ = tit.resolve(chart, responses, EX)
    w = {n: tbi.resolved_widget(chart, k, n, out, EX, f'{label}: {k} {n}')['spec'].get('widgetData', {}) for k, n in WIDGETS}
    return out, w


def check_chips(chart):
    f = []
    out, w = page(incident(), 'chips', EARLIER + LATER, alert(), chart)
    expect(f, 'chips: alert link, state, since, ordinal; no composition',
           ids(w['incident-detail-chips']['items']),
           ['incident-alert-link', 'incident-alert-state', 'incident-alert-oksince', 'incident-alert-ordinal'])
    expect(f, 'ordinal counts earlier incidents only', w['incident-alert-ordinal']['label'], '4th incident from this alert')
    out, w = page(incident(), 'first', LATER, alert(), chart)
    expect(f, 'the first one', w['incident-alert-ordinal']['label'], 'First incident from this alert')
    for k in ('ref', 'refRoute'):
        if k in out:
            f.append(f'incident-detail still emits {k}')
    return f


def check_display_name(chart):
    """The title is the alert's display name: a decorative emoji dropped (why the sanitizer exists),
    punctuation such as the em dash kept, whitespace runs collapsed, trimmed."""
    f = []
    for shown, want in (('Krateo — provider cannot reach its target', 'Krateo — provider cannot reach its target'),
                        ('🚨  Telemetry pipeline stalled ', 'Telemetry pipeline stalled')):
        a = alert()
        a['spec']['displayName'] = shown
        out, _ = page(incident(), f'name {shown}', alert_obj=a, chart=chart)
        expect(f, f'title from {shown!r}', (out['title'], out['alertName']), (want, want))
    return f


def check_ordinal_suffixes(chart):
    f = []
    for n, want in ((2, '2nd'), (3, '3rd'), (11, '11th'), (12, '12th'), (13, '13th'), (21, '21st'), (22, '22nd'), (111, '111th')):
        earlier = [incident(f'{ALERT}-e{i:03d}', f'2026-10-01T{i // 60:02d}:{i % 60:02d}:00Z') for i in range(n - 1)]
        out, _ = tit.resolve(chart, {'incident': incident(), 'incidents': {'items': [incident()] + earlier}}, EX)
        expect(f, f'#{n}', out['ordinalLabel'], f'{want} incident from this alert')
    return f


def check_summary(chart):
    f = []
    out, w = page(incident(), 'summary', alert_obj=alert(), chart=chart)
    expect(f, 'summary rows', w['incident-summary-desc']['items'],
           [{'label': 'Why', 'value': 'The dead-man switch fired because the cluster had been up for less than its 15-minute window.'},
            {'label': 'Fix', 'value': f'Delete the spurious dead-man switch alert {ALERT}.'},
            {'label': 'Who fixes it', 'value': 'Krateo: the fix belongs in a component Krateo ships'}])
    expect(f, 'one column', w['incident-summary-desc']['column'], 1)
    return f


def rows(w):
    return [{c['valueKey']: c['stringValue'] for c in r} for r in w['incident-evidence-resources']['dataSource']]


def check_reasoning(chart):
    f = []
    out, w = page(incident(), 'reasoning', alert_obj=alert(), chart=chart)
    root = ids(w['page-incident-detail']['items'])
    expect(f, 'one Reasoning card, after the Summary', root[root.index('incident-summary-card') + 1], 'incident-why-card')
    expect(f, 'the card holds only its tabs', ids(w['incident-why-card']['items']), ['incident-why-tabs'])
    expect(f, 'card title', w['incident-why-card']['title'], 'Reasoning')
    expect(f, 'tabs', [(t['label'], t['resourceRefId']) for t in w['incident-why-tabs']['items']],
           [('Reasoning trace', 'incident-trace'), ('Evidence · 8', 'incident-evidence'), ('Gaps & assumptions', 'incident-gaps')])
    expect(f, 'gaps as two lists', w['incident-gaps']['markdown'],
           '**Could not see**\n\n- Logs between 11:05Z and 13:00Z are absent.\n\n'
           '**Assumed**\n\n- The composition controller may recreate the Alert.')
    # The Incident tab shows the whole object; everywhere else they are gone.
    shown = json.dumps({k: v for k, v in w.items() if k not in ('incident-manifest', 'incident-alert-manifest')})
    expect(f, 'no owner evidence, category, confidence or What was read outside the Incident tab',
           [x for x in ('post-boot grace period', '0.95', '"config"', 'interval 15m, stat') if x in shown], [])
    # Only gaps: the card still renders, with that tab alone.
    out, w = page(incident(sources=[], analyzedResources=[], reasoningTrace=[]), 'gaps only', alert_obj=alert(), chart=chart)
    expect(f, 'gaps only: one tab', [t['label'] for t in w['incident-why-tabs']['items']], ['Gaps & assumptions'])
    # Nothing to reason with: no card.
    out, w = page(incident(sources=[], analyzedResources=[], reasoningTrace=[], missingContext=[], assumptions=[]),
                  'none', alert_obj=alert(), chart=chart)
    expect(f, 'nothing: no Reasoning card', 'incident-why-card' in ids(w['page-incident-detail']['items']), False)
    return f


def check_evidence(chart):
    f = []
    out, w = page(incident(), 'evidence', alert_obj=alert(), chart=chart)
    expect(f, 'two blocks, each under its title', ids(w['incident-evidence']['items']),
           ['incident-evidence-logs-title', 'incident-evidence-logs', 'incident-evidence-resources-title', 'incident-evidence-resources'])
    expect(f, 'titles', (w['incident-evidence-logs-title']['text'], w['incident-evidence-resources-title']['text'],
                         w['incident-evidence-logs-title']['variant']), ('Log lines & metrics', 'Resources', 'eyebrow'))
    expect(f, 'log rows: numbered first, excerpt in full, kind as a tag', w['incident-evidence-logs']['dataSource'],
           [{'title': f'1. {SOURCES[1]["ref"]}', 'excerpt': SOURCES[1]['excerpt'], 'kind': 'Metrics'},
            {'title': f'2. {SOURCES[3]["ref"]}', 'excerpt': SOURCES[3]['excerpt'], 'kind': 'Logs'},
            {'title': f'3. {SOURCES[5]["ref"]}', 'excerpt': SOURCES[5]['excerpt'], 'kind': 'Resource'},
            {'title': f'4. {SOURCES[6]["ref"]}', 'excerpt': SOURCES[6]['excerpt'], 'kind': 'Events'}])
    if w['incident-evidence-logs']['itemTemplate'].get('ellipsis'):
        f.append('log rows are cut to one line')
    expect(f, 'resources columns', [c['title'] for c in w['incident-evidence-resources']['columns']], ['#', 'Type', 'Name', 'Namespace'])
    expect(f, 'resource rows: cited objects numbered, then the uncited read', [(r['num'], r['type'], r['name'], r['namespace'], r['route']) for r in rows(w)],
           [('5', 'alerts', ALERT, NS, f'/resources/{NS}/observability.krateo.io/v1alpha1/alerts/{ALERT}'),
            ('6', 'pods', CH, NS, f'/resources/{NS}/core/v1/pods/{CH}'),
            ('7', 'nodes', NODE, '—', f'/resources/cluster/core/v1/nodes/{NODE}'),
            ('8', 'krateoobservabilities', 'krateo-observability', NS, f'/compositions/{NS}/krateo-observability')])
    expect(f, 'trace cites the numbers shown, sorted, duplicates and out-of-range dropped',
           [s['description'] for s in w['incident-trace']['items']], ['Evidence 5', 'Evidence 1, 2', '', 'Evidence 4, 6, 7'])
    # Only objects, or only log lines: one block, still titled.
    only_logs = [x for x in SOURCES if x['type'] != 'object']
    out, w = page(incident(sources=only_logs, analyzedResources=[], reasoningTrace=[]), 'logs only', alert_obj=alert(), chart=chart)
    expect(f, 'logs only: one block', ids(w['incident-evidence']['items']), ['incident-evidence-logs-title', 'incident-evidence-logs'])
    out, w = page(incident(sources=[], reasoningTrace=[]), 'read only', alert_obj=alert(), chart=chart)
    expect(f, 'no sources: the resources read, numbered from 1',
           ([t['label'] for t in w['incident-why-tabs']['items']][:1], [r['num'] for r in rows(w)]), (['Evidence · 4'], ['1', '2', '3', '4']))
    return f


def check_object_refs(chart):
    """Which refs read as one object: only a type-object source in <gvr>/<namespace>/<name>."""
    f = []
    cases = [('object', 'apps/v1/deployments/shop/web', ('deployments', 'web', 'shop', '/resources/shop/apps/v1/deployments/web')),
             ('object', 'v1/nodes/node-1', ('nodes', 'node-1', '—', '/resources/cluster/core/v1/nodes/node-1')),
             ('object', 'composition.krateo.io/v0-1-45/krateoobservabilities/krateo-system/krateo-observability',
              ('krateoobservabilities', 'krateo-observability', 'krateo-system', '/compositions/krateo-system/krateo-observability')),
             ('object', 'kubectl get deployments.apps web -n shop', None),
             ('object', 'installer/chart/files/component-pins.yaml:4880', None),
             ('object', 'observability.krateo.io/Alert/krateo-system/sre-pod-crashloop', None),
             ('object', 'v1/pods/Shop/Web', None),
             ('logs', 'v1/pods/shop/web-1', None)]
    for typ, ref, want in cases:
        out, w = page(incident(sources=[{'type': typ, 'ref': ref, 'excerpt': 'x'}], analyzedResources=[], reasoningTrace=[]),
                      f'ref {ref}', alert_obj=alert(), chart=chart)
        got = [(r['type'], r['name'], r['namespace'], r['route']) for r in rows(w)] if 'incident-evidence-resources' in ids(w['incident-evidence']['items']) else []
        expect(f, f'{typ} {ref!r}', got, [want] if want else [])
    return f


def check_how_to_fix(chart):
    f = []
    out, w = page(incident(), 'fix', alert_obj=alert(), chart=chart)
    md = [w[f'incident-rem-step-md-{i}']['markdown'] for i in range(4)]
    fix = incident()['status']['howToFix']
    fenced = {k: '```bash\n' + fix[k].rstrip('\n') + '\n```' for k in ('precondition', 'apply', 'verify', 'rollback')}
    expect(f, 'precondition: its last run, then its script', md[0],
           '**Last run:** still holds · since 2026-11-12 13:26 UTC\n\n' + fenced['precondition'])
    expect(f, 'apply: what Apply writes, its last run, its script', md[1],
           f'Apply will **delete** `alerts/{ALERT}` in `{NS}`.\n\n**Last run:** not applied yet\n\n' + fenced['apply'])
    expect(f, 'verify: its last run, then its script', md[2], '**Last run:** never\n\n' + fenced['verify'])
    expect(f, 'rollback: its script alone', md[3], fenced['rollback'])
    expect(f, 'apply step: the text, then its action', ids(w['incident-rem-step-1']['items']), ['incident-rem-step-md-1', 'incident-rem-apply'])
    expect(f, 'other steps: the text alone', [ids(w[f'incident-rem-step-{i}']['items']) for i in (0, 2, 3)],
           [[f'incident-rem-step-md-{i}'] for i in (0, 2, 3)])
    expect(f, 'no Show script widgets', [d['metadata']['name'] for d in chart.docs
                                         if 'incident-rem-script-btn' in d['metadata']['name'] or 'incident-rem-script-md' in d['metadata']['name']], [])
    for word in ('controller runs', 'sandbox', 'exit **', 'exit 1 ·', 'Undoes apply. Run it'):
        if any(word in m for m in md):
            f.append(f'a step still explains itself: {word!r}')
    # A step with no script says so.
    out, w = page(incident(howToFix=dict(fix, verify='')), 'no verify', alert_obj=alert(), chart=chart)
    expect(f, 'no script: said', w['incident-rem-step-md-2']['markdown'], '**Last run:** never\n\n_No verify script._')
    return f


def check_bottom_tabs(chart):
    f = []
    out, w = page(incident(), 'tabs', alert_obj=alert(), chart=chart)
    expect(f, 'Incident, Alert', [t['label'] for t in w['incident-detail-tabs']['items']][:2], ['Incident', 'Alert'])
    inc = json.loads(w['incident-manifest']['json'])
    al = json.loads(w['incident-alert-manifest']['json'])
    expect(f, 'one object each, managedFields stripped',
           (inc['kind'], inc['metadata'].get('managedFields'), al['kind'], al['metadata'].get('managedFields')),
           ('Incident', None, 'Alert', None))
    out, w = page(incident(), 'alert gone', chart=chart)
    expect(f, 'alert gone: no Alert tab', [t['label'] for t in w['incident-detail-tabs']['items']][:2], ['Incident'])
    return f


def main():
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        crds_dir = args[args.index('--crds') + 1]
    elif '--pinned' in args:
        pinned = _load('pinned', 'lint-pinned-crds.py')
        version, why = pinned.pinned_version()
        if not version:
            print(f'cannot determine the pinned frontend-crds version: {why}')
            return 1
        crds_dir = pinned.unpack(version)
        print(f'validating against the pinned frontend-crds {version}')
    chart = tbi.Chart(tbi.render(os.path.join(REPO, 'helm', 'incidents-page')))
    runs = [check_chips, check_display_name, check_ordinal_suffixes, check_summary, check_reasoning, check_evidence, check_object_refs,
            check_how_to_fix, check_bottom_tabs]
    failed = 0
    for check in runs:
        try:
            problems = check(chart)
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError, ValueError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {check.__name__.replace("check_", "").replace("_", " ")}')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    total = len(runs)
    if crds_dir:
        total += 1
        problems = tbi.validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} resolved widgets validate against their CRDs')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}{"" if crds_dir else "; resolved widgets NOT validated (no --crds / --pinned)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
