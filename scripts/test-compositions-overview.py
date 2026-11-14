#!/usr/bin/env python3
"""
test-compositions-overview — /compositions as a root-pages overview (slice 3a), over the charts' own jq.

WHAT IT GUARDS (build plan §3.4, mockup screen 02, decisions of 2026-11-13 and 2026-11-14):
  - a ReconcilePaused composition is Paused: its own state, never counted Ready or healthy (it
    reports Ready=True on purpose, and the old RA counted it healthy);
  - State and Kind facets count under every OTHER filter, over the scope (My workloads, Platform,
    Everything); a facet with one value in the scope is left out of the bar unless applied;
  - ?status= keeps the old chip values (healthy, failed, degraded, drift, pending, unreadable) plus
    paused, any case, `all` = no filter, several values; labels are the mockup's (Ready, NotReady …);
  - the strip: Not Ready, Degraded, Drift, Paused, Ready (in how many kinds), red/orange only above 0;
  - the groups, in order: Paused, NotReady, Degraded, Drift, Children unreadable, Pending (folded,
    ZeroEvidence labelled "No signal"), then "Ready, by kind", one card per kind; six cards a group,
    then "See all N →" to the table filtered to that state (Q1); a kind of one opens the composition,
    a larger kind the table filtered to it;
  - the calm line when nothing is NotReady, Degraded or drifting; a failed read is said;
  - ?view=table keeps today's server-paged table, filtered, and the toggle and scope chips keep
    every filter; Ask Agentiko keeps every filter and names the view;
  - every widget of the page declares the page's filter keys.
compositions-list lives in helm/portal; the widgets in helm/compositions-page.

Usage: test-compositions-overview.py [--crds DIR]. JQ=gojq runs the same on snowplow's engine.
"""
import datetime
import importlib.util
import os
import sys
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

NOW = datetime.datetime.now(datetime.timezone.utc)
KX = ['projects', 'namespace', 'scope', 'status', 'kind', 'q', 'view']


def ago(days):
    return (NOW - datetime.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')


def comp(name, kind, ready='True', reason='Available', synced='True', wh=None, platform=False, days=10, msg=''):
    """A composition as allCompositions' stage filter leaves it."""
    return {'uid': 'uid-' + name, 'name': name, 'ns': 'team-a', 'ts': ago(days), 'kind': kind,
            'av': 'composition.krateo.io/v1-0-0',
            'conditions': [{'type': 'Ready', 'status': ready, 'reason': reason, 'message': msg},
                           {'type': 'Synced', 'status': synced, 'reason': 'ReconcileSuccess' if synced == 'True' else 'ReconcileError'}],
            'evaluated': None, 'managed': None, 'unreadable': None, 'wh': wh, 'whMsg': '', 'platform': platform}


WORK = ([comp(f'pub-{i:02d}', 'BuilderPublish', days=30 - i) for i in range(17)]
        + [comp('event-feedback', 'EventFeedback', reason='AvailablePartialCoverage', days=40)]
        + [comp('acme', 'AcmeFeedback', reason='ReconcilePaused', synced='False', days=38, msg='paused by an operator')]
        + [comp('orders', 'WebApp', ready='False', reason='Unavailable', msg='Deployment orders has no ready replica')]
        + [comp('web', 'WebApp', wh=False)]
        + [comp('cache', 'Redis', synced='False')]
        + [comp('locked', 'Redis', ready='Unknown', reason='ChildrenForbidden', msg='Composition health is unknown: cannot list pods')]
        + [comp('silent', 'Probe', ready='Unknown', reason='ZeroEvidence')]
        + [comp('new-one', 'Probe', ready='False', reason='Creating')])
PLATFORM = [comp('snowplow', 'Snowplow', platform=True), comp('authn', 'Authn', platform=True)]


def resolve(extras, items=None, err=None):
    ra = CH.get('RESTAction', 'compositions-list')
    data = dict(extras)
    data['allCompositions'] = WORK + PLATFORM if items is None else items
    if err:
        data['allCompositionsError'] = [err]
    return tbi.jq(ra['spec']['filter'], data)


def w(kind, name, out, extras=None):
    return tbi.resolved_widget(CH, kind, name, out, extras or {}, f'{kind} {name}')['spec']['widgetData']


def facet(out, key):
    return {v['value']: v['count'] for v in [f for f in out['facets'] if f['key'] == key][0]['values']}


def items(widget):
    return [i['resourceRefId'] for i in widget['items']]


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def check_paused_is_not_ready():
    f = []
    out = resolve({})
    acme = [i for i in out['list'] if i['name'] == 'acme'][0]
    expect(f, 'ReconcilePaused: state and railState paused', (acme['state'], acme['railState']), ('paused', 'paused'))
    expect(f, 'not counted healthy anywhere', ('acme' in [i['name'] for i in out['list'] if i['state'] == 'healthy']), False)
    expect(f, 'the strip counts it Paused, and Ready without it', (out['strip']['paused'], out['strip']['ready']), (1, 18))
    expect(f, 'the State facet: Paused 1', facet(out, 'status').get('paused'), 1)
    return f


def check_facets():
    f = []
    out = resolve({})
    expect(f, 'State over My workloads, one state each', facet(out, 'status'),
           {'healthy': 18, 'paused': 1, 'failed': 1, 'degraded': 1, 'drift': 1, 'unreadable': 1, 'pending': 2})
    expect(f, 'Kind over My workloads', facet(out, 'kind'),
           {'BuilderPublish': 17, 'EventFeedback': 1, 'AcmeFeedback': 1, 'WebApp': 2, 'Redis': 2, 'Probe': 2})
    out = resolve({'kind': 'WebApp'})
    expect(f, '?kind=WebApp: the kind counts ignore it', facet(out, 'kind')['BuilderPublish'], 17)
    expect(f, '?kind=WebApp: the state counts apply it', {k: v for k, v in facet(out, 'status').items() if v},
           {'failed': 1, 'degraded': 1})
    out = resolve({'scope': 'platform'})
    expect(f, 'Platform scope: the base is the platform set', facet(out, 'kind'), {'Snowplow': 1, 'Authn': 1})
    expect(f, 'Platform scope, one state: State left out of the bar', items(w('Flex', 'compositions-filter-bar', out, {'scope': 'platform'})),
           ['compositions-scope-chips', 'compositions-kind', 'compositions-search'])
    out = resolve({'q': 'PUB-0'})
    expect(f, 'search: name or kind, any case; the counts follow it', facet(out, 'status'),
           {'healthy': 10, 'paused': 0, 'failed': 0, 'degraded': 0, 'drift': 0, 'unreadable': 0, 'pending': 0})
    opts = [(o['label'], o['value']) for o in w('Select', 'compositions-status', resolve({}))['options']]
    expect(f, 'State options in triage order, the mockup\'s labels', opts,
           [('NotReady', 'failed'), ('Degraded', 'degraded'), ('Drift', 'drift'), ('Children unreadable', 'unreadable'),
            ('Paused', 'paused'), ('Pending', 'pending'), ('Ready', 'healthy')])
    return f


def check_status_back_compat():
    f = []
    names = lambda out: sorted(i['name'] for i in out['filtered'])
    expect(f, '?status=failed', names(resolve({'status': 'failed'})), ['orders'])
    expect(f, '?status=Failed (any case)', names(resolve({'status': 'Failed'})), ['orders'])
    expect(f, '?status=paused', names(resolve({'status': 'paused'})), ['acme'])
    expect(f, '?status=drift,degraded (several)', names(resolve({'status': 'drift,degraded'})), ['cache', 'web'])
    expect(f, '?status=all: no filter, no chip', (len(resolve({'status': 'all'})['filtered']), resolve({'status': 'all'})['chips']), (len(WORK), []))
    expect(f, '?status=healthy no longer holds the paused one', 'acme' in names(resolve({'status': 'healthy'})), False)
    out = resolve({'status': 'failed', 'projects': 'team-a'})
    expect(f, 'a state chip removes itself and keeps the scope', [(c['label'], c['href']) for c in out['chips']],
           [('State: NotReady', '/compositions?projects=team-a'), ('Clear all', '/compositions?projects=team-a')])
    return f


def check_strip_and_calm():
    f = []
    out = resolve({})
    rows = [(t['title'], t['value'], t['caption'], t['tone']) for t in w('Listy', 'compositions-strip', out)['dataSource']]
    expect(f, 'the strip, as drawn', rows, [('Not Ready', '1', 'of 25', 'bad'), ('Degraded', '1', 'workload unhealthy', 'bad'),
                                           ('Drift', '1', 'Synced=False', ''), ('Paused', '1', 'ReconcilePaused', 'warn'),
                                           ('Ready', '18', '2 kinds', '')])
    quiet = [c for c in WORK if c['name'].startswith('pub-')]
    out = resolve({}, quiet)
    expect(f, 'nothing NotReady, Degraded or drifting: the calm line', w('Paragraph', 'compositions-calm', out)['text'],
           'Nothing is NotReady, Degraded or drifting in My workloads.')
    expect(f, 'quiet: neutral strip', [t['tone'] for t in w('Listy', 'compositions-strip', out)['dataSource']], [''] * 5)
    out = resolve({}, [], err={'message': 'compositiondefinitions is forbidden'})
    calm = w('Paragraph', 'compositions-calm', out)
    expect(f, 'nothing read: said, as a warning', (calm['text'], calm['type']),
           ('The compositions could not be read (compositiondefinitions is forbidden).', 'warning'))
    expect(f, 'nothing read: the strip is "—", never 0', {t['value'] for t in w('Listy', 'compositions-strip', out)['dataSource']}, {''})
    return f


def check_groups():
    f = []
    ex = {'projects': 'team-a'}
    out = resolve(ex)
    g = w('Collapse', 'compositions-groups', out, ex)
    expect(f, 'panels in order, exception ones tinted', [(p['key'], p['label'], p['count'], p.get('status')) for p in g['items']],
           [('paused', 'Paused', 1, 'warning'), ('failed', 'NotReady', 1, 'error'), ('degraded', 'Degraded', 1, 'error'),
            ('drift', 'Drift', 1, 'warning'), ('unreadable', 'Children unreadable', 1, 'warning'), ('pending', 'Pending', 2, None),
            ('ready', 'Ready, by kind', 18, None)])
    expect(f, 'every panel open but Pending', g['defaultActiveKey'], ['paused', 'failed', 'degraded', 'drift', 'unreadable', 'ready'])
    pend = [p for p in g['items'] if p['key'] == 'pending'][0]
    expect(f, 'Pending: ZeroEvidence is "No signal", Creating keeps its reason', sorted(c['tag'] for c in pend['dataSource']), ['Creating', 'No signal'])
    paused = [p for p in g['items'] if p['key'] == 'paused'][0]['dataSource'][0]
    expect(f, 'a composition card: reason, message, where, opens it', (paused['title'], paused['tag'], paused['body'], paused['href'], paused['cta']),
           ('acme', 'ReconcilePaused', 'paused by an operator', '/compositions/team-a/acme', 'Open'))
    ready = [p for p in g['items'] if p['key'] == 'ready'][0]
    expect(f, 'Ready, by kind: largest first, a card per kind', [(c['title'], c['cta'], c['href']) for c in ready['dataSource']],
           [('BuilderPublish', 'See all 17', '/compositions?projects=team-a&status=healthy&kind=BuilderPublish&view=table'),
            ('EventFeedback', 'Open', '/compositions/team-a/event-feedback')])
    expect(f, 'a kind card: three newest names, then how many more; a non-Available reason as its tag',
           (ready['dataSource'][0]['body'], ready['dataSource'][1]['tag']),
           ('pub-16, pub-15, pub-14 and 14 more', 'AvailablePartialCoverage ×1'))
    many = WORK + [comp(f'down-{i}', 'WebApp', ready='False', reason='Unavailable') for i in range(7)]
    out = resolve(ex, many)
    nr = [p for p in w('Collapse', 'compositions-groups', out, ex)['items'] if p['key'] == 'failed'][0]
    expect(f, 'past six: See all N to the table filtered to that state (Q1)', (len(nr['dataSource']), nr['footer'], nr['footerNavigateTo']),
           (6, 'See all 8 →', '/compositions?projects=team-a&status=failed&view=table'))
    return f


def check_table_view():
    f = []
    ex = {'projects': 'team-a', 'status': 'failed,degraded', 'view': 'table'}
    out = resolve(ex)
    expect(f, '?view=table: the table in place of the overview', items(w('Flex', 'page-compositions', out, ex)),
           ['compositions-page-header', 'compositions-filter-bar', 'compositions-active-filters', 'compositions-view-toggle', 'compositions-table'])
    rows = w('Table', 'compositions-table', dict(out, slice={'page': 1, 'perPage': 50, 'offset': 0}), ex)['dataSource']
    expect(f, '?view=table: the server-paged table holds what the filters match', sorted({c['valueKey']: c['stringValue'] for c in r}['name'] for r in rows), ['orders', 'web'])
    btn = w('Button', 'compositions-view-toggle', out, ex)
    expect(f, 'back to the overview keeps every filter (the states normalized: deduplicated, sorted)', (btn['label'], btn['actions']['navigate'][0]['path']),
           ('← Back to the overview', '/compositions?projects=team-a&status=degraded%2Cfailed'))
    ex = dict(ex, view='')
    btn = w('Button', 'compositions-view-toggle', resolve(ex), ex)
    expect(f, 'the table link counts the match', (btn['label'], btn['actions']['navigate'][0]['path']),
           ('All 2 workloads in this view as a table →', '/compositions?projects=team-a&status=degraded%2Cfailed&view=table'))
    chips = w('Listy', 'compositions-scope-chips', resolve(ex), ex)['dataSource']
    expect(f, 'the scope chips keep every filter', [c['route'] for c in chips],
           ['/compositions?projects=team-a&status=degraded%2Cfailed', '/compositions?projects=team-a&scope=platform&status=degraded%2Cfailed',
            '/compositions?projects=team-a&scope=all&status=degraded%2Cfailed'])
    href = resolve({'projects': 'team-a', 'kind': 'Redis', 'q': 'c'})['askHref']
    path, _, query = href.partition('?')
    params = parse_qs(query)
    expect(f, 'Ask keeps every filter, in the page order', [k for k in params], ['projects', 'kind', 'q', 'ask'])
    expect(f, 'Ask names the view', params['ask'][0].startswith('Review my compositions (Kind: Redis; namespace team-a; name or type matching "c"): 2 in view'), True)
    return f


def check_filter_keys():
    f = []
    root = CH.get('Flex', 'page-compositions')
    expect(f, 'the root names them', root['metadata']['annotations'].get('krateo.io/filter-keys'), ','.join(KX))
    for d in CH.docs:
        n = d.get('metadata', {}).get('name', '')
        if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/') and (n.startswith('compositions-') or n == 'page-compositions'):
            missing = [k for k in KX if k not in (d['spec'].get('keyExtras') or [])]
            expect(f, f'{d["kind"]} {n} declares every filter key', missing, [])
    return f


CHECKS = [check_paused_is_not_ready, check_facets, check_status_back_compat, check_strip_and_calm, check_groups,
          check_table_view, check_filter_keys]


def main():
    global CH
    args = sys.argv[1:]
    crds_dir = args[args.index('--crds') + 1] if '--crds' in args else None
    CH = tbi.Chart(tbi.render(os.path.join(HERE, '..', 'helm', 'portal')) + tbi.render(os.path.join(HERE, '..', 'helm', 'compositions-page')))
    failed = 0
    for check in CHECKS:
        try:
            problems = check()
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
