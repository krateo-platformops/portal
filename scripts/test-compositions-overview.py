#!/usr/bin/env python3
"""
test-compositions-overview — /compositions as a dashboard (the /incidents layout, mockup A4), over the
charts' own jq.

WHAT IT GUARDS:
  - a ReconcilePaused composition is Paused: its own state, never counted Ready or healthy (it
    reports Ready=True on purpose, and the old RA counted it healthy);
  - the state chips are the page's filter: Needs attention (NotReady, Degraded, Children unreadable,
    Drift, Paused) by default, then each state some composition is in, in triage order, then All,
    each counted over the scope; ?status= keeps the old values (healthy, failed, degraded, drift,
    pending, unreadable, paused), any case, several, and `all`;
  - the five figures: NotReady, Degraded, Drift, Paused, Ready (in how many kinds), coloured only
    above 0, "—" and "could not be read" when nothing was read;
  - one card per composition in the state, triage order, the first 48 and a line for the rest; the
    sentence in their place when there is none, or the read failed;
  - the charts: by state, by kind (a row narrows to the kind), needing attention longest (only while
    there are any);
  - a kind or a search a link carries narrows everything and is a removable chip; Clear all keeps
    the scope, the state and the view;
  - ?view=table keeps today's server-paged table, filtered; the view and scope chips keep every
    param; Ask Agentiko keeps every param and names the view;
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


def comp(name, kind, ready='True', reason='Available', synced='True', wh=None, platform=False, days=10, msg='', since=None):
    """A composition as allCompositions' stage filter leaves it; `since` (days) dates its Ready and
    Synced transitions, absent as on a controller that does not set them."""
    at = {'lastTransitionTime': ago(since)} if since is not None else {}
    return {'uid': 'uid-' + name, 'name': name, 'ns': 'team-a', 'ts': ago(days), 'kind': kind,
            'av': 'composition.krateo.io/v1-0-0',
            'conditions': [{'type': 'Ready', 'status': ready, 'reason': reason, 'message': msg, **at},
                           {'type': 'Synced', 'status': synced, 'reason': 'ReconcileSuccess' if synced == 'True' else 'ReconcileError', **at}],
            'evaluated': None, 'managed': None, 'unreadable': None, 'wh': wh, 'whMsg': '', 'platform': platform}


WORK = ([comp(f'pub-{i:02d}', 'BuilderPublish', days=30 - i) for i in range(17)]
        + [comp('event-feedback', 'EventFeedback', reason='AvailablePartialCoverage', days=40)]
        + [comp('acme', 'AcmeFeedback', reason='ReconcilePaused', synced='False', days=38, msg='paused by an operator', since=2)]
        + [comp('orders', 'WebApp', ready='False', reason='Unavailable', msg='Deployment orders has no ready replica', since=0.25)]
        + [comp('web', 'WebApp', wh=False)]
        + [comp('cache', 'Redis', synced='False', since=3.5)]
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


def items(widget):
    return [i['resourceRefId'] for i in widget['items']]


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def chips(out):
    return [(c['label'], c['count'], c['active']) for c in out['stateChips']]


def check_paused_is_not_ready():
    f = []
    out = resolve({'status': 'all'})
    acme = [i for i in out['list'] if i['name'] == 'acme'][0]
    expect(f, 'ReconcilePaused: state and railState paused', (acme['state'], acme['railState']), ('paused', 'paused'))
    expect(f, 'not counted healthy anywhere', ('acme' in [i['name'] for i in out['list'] if i['state'] == 'healthy']), False)
    stats = {t['title']: t['value'] for t in out['stats']}
    expect(f, 'the figures count it Paused, and Ready without it', (stats['Paused'], stats['Ready']), ('1', '18'))
    expect(f, '?status=healthy does not hold it', 'acme' in [i['name'] for i in resolve({'status': 'healthy'})['filtered']], False)
    return f


def check_state_chips():
    f = []
    out = resolve({})
    expect(f, 'no ?status: Needs attention, and the chips in triage order with their counts', chips(out),
           [('Needs attention', 5, True), ('NotReady', 1, False), ('Degraded', 1, False), ('Children unreadable', 1, False),
            ('Drift', 1, False), ('Paused', 1, False), ('Pending', 2, False), ('Ready', 18, False), ('All', 25, False)])
    expect(f, 'Needs attention holds the five attention states', sorted(i['name'] for i in out['filtered']),
           ['acme', 'cache', 'locked', 'orders', 'web'])
    expect(f, 'each chip keeps the scope and the view, Needs attention drops ?status',
           [c['route'] for c in resolve({'scope': 'all', 'view': 'table', 'projects': 'team-a'})['stateChips']][:2],
           ['/compositions?projects=team-a&scope=all&view=table', '/compositions?projects=team-a&scope=all&status=failed&view=table'])
    names = lambda out: sorted(i['name'] for i in out['filtered'])
    expect(f, '?status=failed', names(resolve({'status': 'failed'})), ['orders'])
    expect(f, '?status=Failed (any case)', names(resolve({'status': 'Failed'})), ['orders'])
    expect(f, '?status=drift,degraded (several): no chip is active', (names(resolve({'status': 'drift,degraded'})), [c[0] for c in chips(resolve({'status': 'drift,degraded'})) if c[2]]),
           (['cache', 'web'], []))
    out = resolve({'status': 'all'})
    expect(f, '?status=all: every composition, All active', (len(out['filtered']), [c[0] for c in chips(out) if c[2]]), (len(WORK), ['All']))
    quiet = [c for c in WORK if c['name'].startswith('pub-')]
    expect(f, 'a state with none is left out, Needs attention and All stay', chips(resolve({}, quiet)),
           [('Needs attention', 0, True), ('Ready', 17, False), ('All', 17, False)])
    expect(f, 'the picked state stays at 0', [c for c in chips(resolve({'status': 'paused'}, quiet)) if c[0] == 'Paused'], [('Paused', 0, True)])
    return f


def check_stats_and_empty():
    f = []
    out = resolve({})
    rows = [(t['title'], t['value'], t['caption'], t['tone']) for t in w('Listy', 'compositions-stats', out)['dataSource']]
    expect(f, 'the figures, as drawn', rows, [('NotReady', '1', 'of 25', 'bad'), ('Degraded', '1', 'workload unhealthy', 'bad'),
                                              ('Drift', '1', 'Synced=False', 'warn'), ('Paused', '1', 'ReconcilePaused', 'warn'),
                                              ('Ready', '18', '2 kinds', '')])
    expect(f, 'the figures do not follow the state chip', out['stats'], resolve({'status': 'healthy'})['stats'])
    quiet = [c for c in WORK if c['name'].startswith('pub-')]
    out = resolve({}, quiet)
    expect(f, 'quiet: neutral figures', [t['tone'] for t in out['stats']], [''] * 5)
    expect(f, 'quiet: the sentence in place of the cards', (items(w('Card', 'compositions-list-card', out)), w('Paragraph', 'compositions-empty', out)['text']),
           (['compositions-state-chips', 'compositions-empty'],
            'Nothing needs attention in My workloads: none is NotReady, Degraded, drifting, paused or with unreadable children.'))
    out = resolve({}, [], err={'message': 'compositiondefinitions is forbidden'})
    empty = w('Paragraph', 'compositions-empty', out)
    expect(f, 'nothing read: said, as a warning', (empty['text'], empty['type']),
           ('The compositions could not be read (compositiondefinitions is forbidden), so nothing on this page counts them.', 'warning'))
    expect(f, 'nothing read: the figures are "—", never 0', {(t['value'], t['caption']) for t in out['stats']}, {('', 'could not be read')})
    expect(f, 'nothing read: no slice, no kind', (out['byState'], out['byKind']), ([], []))
    return f


def check_cards():
    f = []
    out = resolve({'status': 'all'})
    cards = w('Listy', 'compositions-cards', out)['dataSource']
    expect(f, 'triage order, then kind and name', [c['name'] for c in cards][:8],
           ['orders', 'web', 'locked', 'cache', 'acme', 'new-one', 'silent', 'pub-00'])
    by = {c['name']: c for c in cards}
    expect(f, 'NotReady: the controller\'s message, since its Ready transition',
           (by['orders']['stateLabel'], by['orders']['tone'], by['orders']['description'], by['orders']['foot'].split(' · ')[1].startswith('NotReady since ')),
           ('NotReady', 'failed', 'Deployment orders has no ready replica', True))
    expect(f, 'Degraded: the workload condition, no transition to show', (by['web']['description'], by['web']['foot'].split(' · ')[1].startswith('created ')),
           ('Workload=False · Unhealthy', True))
    expect(f, 'Children unreadable: the refused children', by['locked']['description'], 'Ready=Unknown · ChildrenForbidden: cannot list pods')
    expect(f, 'Drift: since its Synced transition', by['cache']['foot'].split(' · ')[1].startswith('Drift since '), True)
    expect(f, 'Ready: no message, a dash', by['pub-00']['description'], '—')
    expect(f, 'a card opens the composition', (by['acme']['routeNs'], by['acme']['routeName']), ('team-a', 'acme'))
    many = WORK + [comp(f'down-{i:02d}', 'WebApp', ready='False', reason='Unavailable') for i in range(50)]
    out = resolve({}, many)
    expect(f, 'past 48: the first 48, and a line for the rest',
           (len(out['rows']), out['more'], items(w('Card', 'compositions-list-card', out)), w('Paragraph', 'compositions-more', out)['text']),
           (48, 7, ['compositions-state-chips', 'compositions-cards', 'compositions-more'], '48 of 55 shown here. Full list has all 55.'))
    return f


def check_charts():
    f = []
    ex = {'projects': 'team-a'}
    out = resolve(ex)
    expect(f, 'by state: a slice per state that has any, triage order', [(s['state'], s['count']) for s in w('PieChart', 'compositions-by-state', out)['data']],
           [('NotReady', 1), ('Degraded', 1), ('Children unreadable', 1), ('Drift', 1), ('Paused', 1), ('Pending', 2), ('Ready', 18)])
    kinds = w('Listy', 'compositions-by-kind', out)['dataSource']
    expect(f, 'by kind: largest first, the attention count, a row narrows to the kind in every state',
           [(k['name'], k['count'], k['percent'], k['tone'], k['href']) for k in kinds][:3],
           [('BuilderPublish', '17', 100, 'ok', '/compositions?projects=team-a&status=all&kind=BuilderPublish'),
            ('Probe', '2', 11, 'ok', '/compositions?projects=team-a&status=all&kind=Probe'),
            ('Redis', '2 · 2 need attention', 11, 'bad', '/compositions?projects=team-a&status=all&kind=Redis')])
    expect(f, 'needing attention longest: oldest first, Degraded and undated left out',
           [(r['name'], r['why'], r['href']) for r in w('Listy', 'compositions-longest', out)['dataSource']],
           [('cache', 'Drift · 3 d 12 h', '/compositions/team-a/cache'), ('acme', 'Paused · 2 d', '/compositions/team-a/acme'),
            ('orders', 'NotReady · 6 h', '/compositions/team-a/orders')])
    expect(f, 'the side shows it while there are any', items(w('Flex', 'compositions-side', out)),
           ['compositions-by-state-card', 'compositions-by-kind-card', 'compositions-longest-card'])
    out = resolve(ex, [c for c in WORK if c['name'] != 'cache' and c['name'] != 'acme' and c['name'] != 'orders'])
    expect(f, 'and not without', items(w('Flex', 'compositions-side', out)), ['compositions-by-state-card', 'compositions-by-kind-card'])
    return f


def check_narrowing():
    f = []
    ex = {'scope': 'all', 'kind': 'WebApp'}
    out = resolve(ex)
    expect(f, 'Observability\'s by-controller link (?scope=all&kind=): the kind narrows, in attention by default',
           (sorted(i['name'] for i in out['filtered']), chips(out)[0]), (['orders', 'web'], ('Needs attention', 2, True)))
    expect(f, 'its chip removes it, Clear all keeps scope and state', [(c['label'], c['href']) for c in out['chips']],
           [('Kind: WebApp', '/compositions?scope=all'), ('Clear all', '/compositions?scope=all')])
    expect(f, 'the chips sit under the scope', items(w('Flex', 'page-compositions', out, ex)),
           ['compositions-page-header', 'compositions-scope-group', 'compositions-active-filters', 'compositions-stats', 'compositions-main'])
    expect(f, 'no narrowing, no chips row', items(w('Flex', 'page-compositions', resolve({}))),
           ['compositions-page-header', 'compositions-scope-group', 'compositions-stats', 'compositions-main'])
    out = resolve({'q': 'PUB-0', 'status': 'all'})
    expect(f, 'Observability\'s by-kind link (?q=): name or kind, any case; a removable chip',
           (len(out['filtered']), [c['label'] for c in out['chips']]), (10, ['Search: PUB-0', 'Clear all']))
    out = resolve({'scope': 'platform', 'status': 'all'})
    expect(f, 'Platform scope: the platform set', sorted(i['name'] for i in out['filtered']), ['authn', 'snowplow'])
    return f


def check_table_view():
    f = []
    ex = {'projects': 'team-a', 'status': 'failed,degraded', 'view': 'table'}
    out = resolve(ex)
    expect(f, '?view=table: the table in place of the cards', items(w('Card', 'compositions-list-card', out, ex)),
           ['compositions-state-chips', 'compositions-table'])
    rows = w('Table', 'compositions-table', dict(out, slice={'page': 1, 'perPage': 50, 'offset': 0}), ex)['dataSource']
    expect(f, '?view=table: the server-paged table holds what the state matches', sorted({c['valueKey']: c['stringValue'] for c in r}['name'] for r in rows), ['orders', 'web'])
    expect(f, 'the view chips keep every param (the states normalized: deduplicated, sorted)',
           [(c['label'], c['active'], c['route']) for c in w('Listy', 'compositions-view-chips', out, ex)['dataSource']],
           [('Summary', False, '/compositions?projects=team-a&status=degraded%2Cfailed'),
            ('Full list', True, '/compositions?projects=team-a&status=degraded%2Cfailed&view=table')])
    chips_ = w('Listy', 'compositions-scope-chips', out, ex)['dataSource']
    expect(f, 'the scope chips keep every param', [c['route'] for c in chips_],
           ['/compositions?projects=team-a&status=degraded%2Cfailed&view=table', '/compositions?projects=team-a&scope=platform&status=degraded%2Cfailed&view=table',
            '/compositions?projects=team-a&scope=all&status=degraded%2Cfailed&view=table'])
    href = resolve({'projects': 'team-a', 'kind': 'Redis', 'q': 'c'})['askHref']
    path, _, query = href.partition('?')
    params = parse_qs(query)
    expect(f, 'Ask keeps every param, in the page order', [k for k in params], ['projects', 'kind', 'q', 'ask'])
    expect(f, 'Ask names the view', params['ask'][0].split(':')[0:2],
           ['Review my compositions (State', ' Needs attention; Kind'])
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


CHECKS = [check_paused_is_not_ready, check_state_chips, check_stats_and_empty, check_cards, check_charts,
          check_narrowing, check_table_view, check_filter_keys]


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
