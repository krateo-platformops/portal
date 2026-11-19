#!/usr/bin/env python3
"""
test-observability-a4 — /observability laid out as the A4 dashboard, tab by tab, over the chart's own jq.

WHAT IT GUARDS.
THE PAGE: the header (no subtitle), the toolbar (the time range left, the search in a quarter right,
stacked when narrow), the tabs; krateo.io/filter-keys on the root, and every widget the root reaches
(every flag on) declaring exactly those keys (F6).
EACH LISTING TAB (Reconciliation, Telemetry, Components, Traces):
  - the figures: titles, values, captions and tones, each tile coloured by the state it counts
    with the same palette colour its cards give that state, zero or not; a failed read is "",
    "could not be read", orange — never a 0;
  - the layout: figures, then the main row (list card 16 | charts 8, stacked when narrow); the list
    card's items (state chips, then the cards, ?view=table the full list, or the empty line) and its
    "Summary | Full list" chips (extraRefId); the side cards, a card with no data left out;
  - the search (?q=, case-insensitive, trimmed): the rows, the counts and the figures follow it;
    every route the tab builds carries it; nothing matching is said, "No <thing> matches "<q>"."
RECONCILIATION (obs-reconciliation): the state is compositions-list's railState; Needs attention is
NotReady, Degraded, Children unreadable, Drift and Paused; with reconcile tracing, a composition is
dated from its reconcile spans (kind|ns|name, trace-only rows dropped) and Overdue when it has none
in the window or none for 5 min; unreadable or empty traces date nothing and flag nothing; the
full list's columns follow the data.
TELEMETRY (obs-error-log-digest, obs-log-stream, obs-resource-usage): the grouped errors as cards,
the stream as the full list, errors by service and the pods with the most memory beside them.
COMPONENTS (obs-components): Ready, Not ready, Scaled to zero; the runtime table and the declared
pins as the full list.
TRACES (obs-traces): spans, errors, services, users; the status chips in the list card.

SLOs and the snowplow SLI tabs keep their own layout (charts and cards only): test-obs-slo.py.
No trend is bucketed here: the line charts on these tabs (reconcile throughput, resource usage) are
bucketed by ClickHouse and guarded by their own tests.

Uses the `jq` binary; JQ=gojq runs the same checks on it.
Usage: test-observability-a4.py [--crds DIR]. Exit code = failed checks.
"""
import copy
import importlib.util
import json
import os
import sys
from urllib.parse import unquote

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


traces = _load('traces', 'test-observability-traces.py')
gates, tbi = traces.gates, traces.tbi

KX = ['projects', 'range', 'tab', 'serviceName', 'limit', 'traceService', 'traceUser', 'traceStatus', 'latencyBy',
      'status', 'componentStatus', 'q', 'view']
NOW_S = traces.NOW_S
SINCE_S = traces.SINCE_S


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def wd(c, kind, name, out, extras=None, label=None):
    return tbi.resolved_widget(c, kind, name, out, extras or {}, label or f'{kind} {name}')['spec']['widgetData']


def items(c, kind, name, out, extras=None):
    return [i['resourceRefId'] for i in wd(c, kind, name, out, extras)['items']]


def static_items(c, kind, name):
    return [i['resourceRefId'] for i in c.get(kind, name)['spec']['widgetData']['items']]


def ch(rows):
    return traces.ch(rows)


# ---------------------------------------------------------------------------------------------
# Fixtures: compositions in every state
# ---------------------------------------------------------------------------------------------

def cond(t, status, reason='', message=''):
    return {'type': t, 'status': status, 'reason': reason, 'message': message}


def comp(kind, ns, name, created, conds, wh=None):
    st = {'conditions': conds}
    if wh is not None:
        st['workloadHealthy'] = wh
    return {'apiVersion': 'composition.krateo.io/v0-3-0', 'kind': kind,
            'metadata': {'name': name, 'namespace': ns, 'creationTimestamp': created}, 'status': st}


READY = [cond('Ready', 'True', 'Available'), cond('Synced', 'True')]
CDS = {'items': [traces.cdef('fireworks', 'krateo-blueprints', 'composition.krateo.io/v0-3-0', 'fireworksapps', 'FireworksApp'),
                 traces.cdef('portal', 'krateo-system', 'composition.krateo.io/v1-8-106', 'portals', 'Portal')]}
FW = '/apis/composition.krateo.io/v0-3-0/fireworksapps'
PT = '/apis/composition.krateo.io/v1-8-106/portals'
LISTS = {
    FW: {'items': [
        comp('FireworksApp', 'team-a', 'ok-1', '2026-11-01T09:00:00Z', READY),
        comp('FireworksApp', 'team-a', 'broken', '2026-11-02T09:00:00Z',
             [cond('Ready', 'False', 'Unavailable', 'deployment orders-api has 0 of 2 ready'), cond('Synced', 'True')]),
        comp('FireworksApp', 'team-b', 'drifted', '2026-11-03T09:00:00Z', [cond('Ready', 'True', 'Available'),
                                                                            cond('Synced', 'False', 'ReconcileError', 'values rejected')]),
        comp('FireworksApp', 'team-b', 'creating', '2026-11-10T13:50:00Z', [cond('Ready', 'False', 'Creating')]),
        comp('FireworksApp', 'team-b', 'sick', '2026-11-04T09:00:00Z', READY, wh=False)]},
    PT: {'items': [comp('Portal', 'krateo-system', 'portal', '2026-08-25T10:00:00Z', READY)]},
}


def recon(kind, ns, name, last, p95=None, n=0):
    r = {'kind': kind, 'ns': ns, 'name': name, 'last': last, 'first': SINCE_S, 'now': NOW_S}
    if p95 is not None:
        r.update({'p95_ms': p95, 'n': n})
    return r


SPANS = [recon('Portal', 'krateo-system', 'portal', NOW_S - 70, 1189, 40),
         recon('FireworksApp', 'team-a', 'ok-1', NOW_S - 30, 830, 12),
         recon('FireworksApp', 'team-a', 'broken', NOW_S - 7200, 41234, 3),
         recon('FireworksApp', 'team-b', 'drifted', NOW_S - 20),
         recon('FireworksApp', 'team-b', 'sick', NOW_S - 40),
         recon('FireworksApp', 'team-z', 'deleted', NOW_S - 50)]          # no live composition


def recon_out(c, extras=None, spans=SPANS, lists=None, ch_down=False, crds=CDS):
    resp = {'crds': crds, 'comps': LISTS if lists is None else lists}
    if not ch_down and spans is not None:
        resp['last'] = ch(spans)
    return gates.resolve(c.get('RESTAction', 'obs-reconciliation'), extras or {}, resp)


CARD_MAP = {'failed': 'red', 'degraded': 'red', 'unreadable': 'orange', 'drift': 'orange', 'paused': 'gray',
            'pending': 'blue', 'healthy': 'green'}


# ---------------------------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------------------------

def check_page(c, on):
    f = []
    expect(f, 'the page', static_items(c, 'Flex', 'page-observability'), ['observability-page-header', 'obs-toolbar', 'obs-main'])
    bar = c.get('Row', 'obs-toolbar')['spec']['widgetData']
    expect(f, 'the toolbar: the range left, the search in a quarter, stacked when narrow',
           [(i['resourceRefId'], i['size'], i['xs'], i['lg']) for i in bar['items']],
           [('obs-range-group', 18, 24, 18), ('obs-search-wrap', 6, 24, 6)])
    expect(f, 'the search wrap holds the search', static_items(c, 'Flex', 'obs-search-wrap'), ['obs-search'])
    search = c.get('Input', 'obs-search')['spec']['widgetData']
    expect(f, 'the search writes ?q= on Enter', (search['queryParam'], search['allowClear'], search['size'], search['placeholder']),
           ('q', True, 'middle', 'Search this page · press Enter'))
    hdr = c.get('PageHeader', 'observability-page-header')['spec']['widgetData']
    expect(f, 'the header: no subtitle', 'subtitle' in hdr, False)
    tabs = [i['label'] for i in on.get('Tabs', 'obs-main')['spec']['widgetData']['items']]
    expect(f, 'the tabs', tabs, ['Reconciliation', 'Telemetry', 'Components', 'Traces', 'SLOs',
                                 'SP · Cache', 'SP · Invalidation', 'SP · Informers', 'SP · HTTP & logs'])
    rng = wd(c, 'Listy', 'obs-range-chips', {}, {'q': 'fire', 'view': 'table', 'status': 'attention', 'projects': 'a'})['dataSource']
    expect(f, 'a range chip keeps the search, the view and the state', rng[0]['route'],
           '/observability?range=1h&projects=a&status=attention&q=fire&view=table')
    return f


def check_filter_keys(c, on):
    f = []
    for chart in (c, on):
        root = chart.get('Flex', 'page-observability')
        expect(f, 'the root names the page params', root['metadata']['annotations'].get('krateo.io/filter-keys'), ','.join(KX))
        by = {d['metadata']['name']: d for d in chart.docs if d.get('apiVersion', '').startswith('widgets.')}
        seen, todo = set(), ['page-observability']
        while todo:
            n = todo.pop()
            if n in seen:
                continue
            if n not in by:
                f.append(f'{n} is referenced and does not render')
                continue
            seen.add(n)
            if by[n]['spec'].get('keyExtras') != KX:
                f.append(f'{by[n]["kind"]} {n}: keyExtras {by[n]["spec"].get("keyExtras")}')
            todo += [r['name'] for r in (by[n]['spec'].get('resourcesRefs') or {}).get('items') or []]
        if chart is on and len(seen) < 170:
            f.append(f'only {len(seen)} widgets reached with every flag on')
    return f


# ---------------------------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------------------------

def check_recon_figures(c, on):
    f = []
    _, _, out = recon_out(on)
    expect(f, 'the five figures, with tracing',
           [(s['title'], s['value'], s['caption'], s['tone']) for s in out['stats']],
           [('Compositions', '6', '2 kinds', 'total'), ('Healthy', '2', 'Ready and in sync', 'healthy'),
            ('Needs attention', '3', '1 NotReady · 1 Degraded · 1 Drift', 'attention'),
            ('Pending', '1', 'converging, no verdict yet', 'pending'),
            ('Reconcile overdue', '2', '1 with none in the trace window', 'overdue')])
    _, _, off = recon_out(c, spans=None)
    expect(f, 'without tracing: four figures, no overdue', [s['title'] for s in off['stats']],
           ['Compositions', 'Healthy', 'Needs attention', 'Pending'])
    stats = wd(on, 'Listy', 'obs-recon-stats', out)
    cmap = wd(on, 'Listy', 'obs-recon-cards', out)['itemTemplate']['color']['map']
    smap = stats['itemTemplate']['color']['map']
    expect(f, 'the cards colour each state as /compositions does', cmap, CARD_MAP)
    expect(f, 'each figure wears its state\'s card colour',
           (smap['healthy'], smap['pending'], smap['attention']), (cmap['healthy'], cmap['pending'], cmap['failed']))
    expect(f, 'overdue is the cards\' clock colour', smap['overdue'], wd(on, 'Listy', 'obs-recon-cards', out)['dataSource'][0]['statusColor'])
    expect(f, 'a failed read is orange; Compositions is neutral', (smap['unread'], 'total' in smap), ('orange', False))
    for label, kw, want in (('ClickHouse down', {'ch_down': True}, ('', 'traces could not be read', 'unread')),
                            ('no reconcile span', {'spans': []}, ('', 'no reconcile traces in 7 days', 'overdue'))):
        _, _, o = recon_out(on, **kw)
        s = o['stats'][4]
        expect(f, f'{label}: the overdue figure says why', (s['value'], s['caption'], s['tone']), want)
        expect(f, f'{label}: nothing dated, nothing overdue', (o['counts']['overdue'], [r['statusIcon'] for r in o['rows']]),
               (0, [''] * 6))
        expect(f, f'{label}: the note says it', 'The compositions themselves are not affected.' in o['note'], True)
        expect(f, f'{label}: the note shows', 'obs-recon-note' in items(on, 'Card', 'obs-recon-list-card', o), True)
    _, _, bad = recon_out(on, crds={'__status__': {'kind': 'Status', 'code': 403, 'message': 'compositiondefinitions is forbidden'}}, lists={})
    expect(f, 'a failed read: every figure "could not be read", never 0',
           {(s['value'], s['caption'], s['tone']) for s in bad['stats']}, {('', 'could not be read', 'unread')})
    expect(f, 'a failed read: the line, as a warning', (items(on, 'Card', 'obs-recon-list-card', bad)[-1],
                                                         wd(on, 'Paragraph', 'obs-recon-empty', bad)['type']), ('obs-recon-empty', 'warning'))
    expect(f, 'a failed read: no chart', (bad['byState'], bad['byKind'], bad['byNamespace']), ([], [], []))
    _, _, part = recon_out(on, lists={PT: LISTS[PT]})
    expect(f, 'a list unread: what was read is shown, and the note says some may be missing',
           (part['failed'], part['live'], 'may be missing' in part['note']), (False, 1, True))
    return f


def check_recon_layout(c, on):
    f = []
    expect(f, 'the tab, without tracing', static_items(c, 'Col', 'obs-tab-reconciliation'),
           ['obs-sec-reconciliation-desc', 'obs-recon-stats', 'obs-recon-main'])
    expect(f, 'the tab, with tracing: the latency card closes it', static_items(on, 'Col', 'obs-tab-reconciliation'),
           ['obs-sec-reconciliation-desc', 'obs-recon-stats', 'obs-recon-main', 'obs-latency-row'])
    row = c.get('Row', 'obs-recon-main')['spec']['widgetData']
    expect(f, 'two thirds and one third', [(i['resourceRefId'], i['size'], i['xs']) for i in row['items']],
           [('obs-recon-list-card', 16, 24), ('obs-recon-side', 8, 24)])
    _, _, out = recon_out(on)
    card = wd(on, 'Card', 'obs-recon-list-card', out)
    expect(f, 'the list card: chips, then the cards; Summary | Full list top right',
           ([i['resourceRefId'] for i in card['items']], card['extraRefId'], card['title'], card['titleVariant']),
           (['obs-recon-state-chips', 'obs-recon-cards'], 'obs-recon-view-chips', 'Compositions', 'eyebrow'))
    _, _, tbl = recon_out(on, {'view': 'table'})
    expect(f, '?view=table: the full list', items(on, 'Card', 'obs-recon-list-card', tbl, {'view': 'table'}),
           ['obs-recon-state-chips', 'obs-recon-table'])
    expect(f, 'the view chips', [(v['label'], v['active'], v['route']) for v in tbl['viewChips']],
           [('Summary', False, '/observability?tab=Reconciliation'), ('Full list', True, '/observability?view=table&tab=Reconciliation')])
    expect(f, 'the side, with tracing', items(on, 'Flex', 'obs-recon-side', out),
           ['obs-recon-by-state-card', 'obs-throughput-card', 'obs-phases-card', 'obs-by-kind-card', 'obs-by-namespace-card'])
    _, _, off = recon_out(c, spans=None)
    expect(f, 'the side, without tracing', items(c, 'Flex', 'obs-recon-side', off),
           ['obs-recon-by-state-card', 'obs-by-kind-card', 'obs-by-namespace-card'])
    expect(f, 'where reconcile time goes: p50/p95/p99 over the phases', static_items(on, 'Card', 'obs-phases-card'),
           ['obs-perf-stat-row', 'obs-reconcile-phases'])
    expect(f, 'by state, as /compositions\' donut', wd(on, 'PieChart', 'obs-recon-by-state', out)['data'],
           [{'state': 'NotReady', 'count': 1}, {'state': 'Degraded', 'count': 1}, {'state': 'Drift', 'count': 1},
            {'state': 'Pending', 'count': 1}, {'state': 'Ready', 'count': 2}])
    kinds = wd(on, 'Listy', 'obs-by-kind-list', out)['dataSource']
    expect(f, 'by kind: largest first, attention counted, a row searches for it',
           [(k['name'], k['count'], k['tone'], k['route']) for k in kinds],
           [('FireworksApp', '5 · 3 need attention', 'bad', '/observability?q=FireworksApp&tab=Reconciliation'),
            ('Portal', '1', 'ok', '/observability?q=Portal&tab=Reconciliation')])
    expect(f, 'by namespace', [(k['name'], k['percent']) for k in wd(on, 'Listy', 'obs-by-namespace-list', out)['dataSource']],
           [('team-b', 100), ('team-a', 66), ('krateo-system', 33)])
    many = {FW: {'items': [comp('FireworksApp', 'team-a', f'c{i:02d}', '2026-11-01T09:00:00Z', READY) for i in range(30)]}, PT: {'items': []}}
    _, _, big = recon_out(c, lists=many, spans=None)
    expect(f, 'more than 24: the cards and the line under them',
           (len(big['rows']), items(c, 'Card', 'obs-recon-list-card', big)[-2:], wd(c, 'Paragraph', 'obs-recon-more', big)['text']),
           (24, ['obs-recon-cards', 'obs-recon-more'], '24 of 30 shown here. Full list has all 30.'))
    for kind, name in (('Card', 'obs-recon-by-state-card'), ('Card', 'obs-by-kind-card'), ('Card', 'obs-by-namespace-card'),
                       ('Paragraph', 'obs-sec-reconciliation-desc'), ('Row', 'obs-recon-main'), ('Col', 'obs-tab-reconciliation')):
        wd(on, kind, name, out)
    return f


def check_recon_cards_and_table(c, on):
    f = []
    _, _, out = recon_out(on)
    expect(f, 'attention first (triage order), then overdue, then pending, then healthy',
           [r['name'] for r in out['rows']], ['broken', 'sick', 'drifted', 'creating', 'portal', 'ok-1'])
    rows = {r['name']: r for r in wd(on, 'Listy', 'obs-recon-cards', out)['dataSource']}
    expect(f, 'a NotReady card: the reason, the reconcile health, the clock',
           (rows['broken']['stateLabel'], rows['broken']['description'], rows['broken']['foot'], rows['broken']['statusIcon']),
           ('NotReady', 'deployment orders-api has 0 of 2 ready', 'team-a · reconciled 2 h ago · p95 41.2 s', 'fa-clock'))
    expect(f, 'a composition with no span in the window', (rows['creating']['foot'], rows['creating']['statusTooltip']),
           ('team-b · no reconcile in the trace window', 'No reconcile in the trace window'))
    expect(f, 'a healthy one: no clock', (rows['portal']['foot'], rows['portal']['statusIcon'], rows['portal']['description']),
           ('krateo-system · reconciled 1 min ago · p95 1.2 s', '', 'Ready and in sync.'))
    tpl = wd(on, 'Listy', 'obs-recon-cards', out)['itemTemplate']
    expect(f, 'a card opens the composition', (tpl['rowVariant'], tpl['navigateTo']), ('card', '/compositions/{ns}/{name}'))
    _, _, off = recon_out(c, spans=None)
    expect(f, 'without tracing: when it was created', {r['name']: r['foot'] for r in off['rows']}['ok-1'], 'team-a · created 2026-11-01')
    t = wd(on, 'Table', 'obs-recon-table', out, {'view': 'table'})
    expect(f, 'the full list, with tracing', [c_['title'] for c_ in t['columns']],
           ['Composition', 'Kind', 'State', 'Last reconcile', 'p95', 'Created'])
    first = {x['valueKey']: x for x in t['dataSource'][0]}
    expect(f, 'its first row', (first['composition']['stringValue'], first['state']['stringValue'], first['state']['color'],
                                first['last']['stringValue'], first['p95']['stringValue']),
           ('team-a/broken', 'NotReady', 'red', '2026-11-10T12:00:00Z', '41.2 s'))
    for row in t['dataSource']:
        if len(row) - 2 != len(t['columns']):
            f.append(f'a row has {len(row) - 2} visible cells for {len(t["columns"])} columns')
    t0 = wd(c, 'Table', 'obs-recon-table', off, {'view': 'table'})
    expect(f, 'the full list, without tracing: no column of dashes', [c_['title'] for c_ in t0['columns']],
           ['Composition', 'Kind', 'State', 'Created'])
    expect(f, 'a row opens the composition', traces.rownav.build_row_path(t['rowNavigateTo'], t['dataSource'][0]), '/compositions/team-a/broken')
    expect(f, 'trace-only rows are dropped', 'deleted' in [r['name'] for r in out['table']], False)
    return f


def check_recon_chips_and_search(c, on):
    f = []
    _, _, out = recon_out(on, {'projects': 'team-a,team-b,krateo-system'})
    expect(f, 'the chips: All, Needs attention, Overdue, then the states some composition is in',
           [(x['label'], x['count'], x['active']) for x in out['chips']],
           [('All', 6, True), ('Needs attention', 3, False), ('Overdue', 2, False), ('NotReady', 1, False),
            ('Degraded', 1, False), ('Drift', 1, False), ('Pending', 1, False), ('Ready', 2, False)])
    expect(f, 'a chip keeps the scope', out['chips'][1]['route'],
           '/observability?projects=team-a%2Cteam-b%2Ckrateo-system&status=attention&tab=Reconciliation')
    _, _, att = recon_out(on, {'status': 'Attention'})
    expect(f, '?status= in any case', [r['name'] for r in att['rows']], ['broken', 'sick', 'drifted'])
    _, _, od = recon_out(on, {'status': 'overdue'})
    expect(f, 'overdue: none in the window, or the last over 5 min ago', [r['name'] for r in od['rows']], ['broken', 'creating'])
    _, _, foo = recon_out(on, {'status': 'foo'})
    expect(f, 'not a state: nothing, the chips still drawn, said',
           (foo['rows'], len(foo['chips']), wd(on, 'Paragraph', 'obs-recon-empty', foo)['text']),
           ([], 8, 'No composition is in this state.'))
    _, _, scoped = recon_out(on, {'projects': 'team-a'})
    expect(f, '?projects= scopes the tab', scoped['live'], 2)
    _, _, q = recon_out(on, {'q': ' TEAM-B ', 'status': 'attention'})
    expect(f, 'the search: case-insensitive, trimmed, over the namespace too', (q['q'], [r['name'] for r in q['rows']]), ('TEAM-B', ['sick', 'drifted']))
    expect(f, 'the counts and figures follow it', (q['counts']['all'], [s['value'] for s in q['stats'][:4]]), (3, ['3', '0', '2', '1']))
    expect(f, 'every route keeps it', (q['chips'][0]['route'], q['viewChips'][1]['route']),
           ('/observability?q=TEAM-B&tab=Reconciliation', '/observability?status=attention&q=TEAM-B&view=table&tab=Reconciliation'))
    _, _, why = recon_out(on, {'q': 'values rejected'})
    expect(f, 'the search reads the reason', [r['name'] for r in why['rows']], ['drifted'])
    _, _, none = recon_out(on, {'q': 'zzz'})
    expect(f, 'nothing matches: said', (items(on, 'Card', 'obs-recon-list-card', none)[-1], wd(on, 'Paragraph', 'obs-recon-empty', none)['text']),
           ('obs-recon-empty', 'No composition matches "zzz".'))
    expect(f, 'nothing matches: no chart', items(on, 'Flex', 'obs-recon-side', none), ['obs-throughput-card', 'obs-phases-card'])
    return f


def check_recon_query(on):
    f = []
    reqs, _, _ = recon_out(on)
    by = {}
    for name, path, _ in reqs:
        by.setdefault(name, []).append(path)
    expect(f, 'one CompositionDefinition list', by.get('crds'), ['/apis/core.krateo.io/v1alpha1/compositiondefinitions'])
    expect(f, 'one list per kind', sorted(by.get('comps') or []), sorted([FW, PT]))
    sql = unquote(by['last'][0][len('/?query='):])
    expect(f, 'one bounded ClickHouse aggregate, with the p95', sql,
           "SELECT SpanAttributes['k8s.object.kind'] AS kind, SpanAttributes['k8s.object.namespace'] AS ns, "
           "SpanAttributes['k8s.object.name'] AS name, toUnixTimestamp(max(Timestamp)) AS last, "
           "toUnixTimestamp(min(Timestamp)) AS first, toUnixTimestamp(now()) AS now, "
           "round(quantile(0.95)(Duration)/1e6, 0) AS p95_ms, count() AS n FROM otel_traces "
           "WHERE ServiceName = 'composition-dynamic-controller' AND SpanName = 'reconcile' "
           "AND Timestamp > now() - INTERVAL 7 DAY GROUP BY kind, ns, name FORMAT JSON")
    for step in on.get('RESTAction', 'obs-reconciliation')['spec']['api']:
        if not step.get('endpointRef') and not step.get('userAccessFilter'):
            f.append(f'{step["name"]}: an apiserver stage with no userAccessFilter')
        if not step.get('continueOnError') or not step.get('errorKey'):
            f.append(f'{step["name"]}: needs continueOnError and an errorKey')
    _, _, quoted = recon_out(on, spans=[{k: (str(v) if isinstance(v, int) else v) for k, v in r.items()} for r in SPANS])
    _, _, plain = recon_out(on)
    expect(f, 'quoted 64-bit integers read the same', quoted['rows'], plain['rows'])
    expect(f, 'the trace window, in the caption', (plain['extra'], wd(on, 'Paragraph', 'obs-sec-reconciliation-desc', plain)['text'].split(' Reconcile ')[-1]),
           ('trace window: since 3 Nov, 14:02 (7 days)', 'trace window: since 3 Nov, 14:02 (7 days).'))
    return f


# ---------------------------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------------------------

DIGEST = [{'service': 'snowplow', 'msg': 'context deadline exceeded', 'occurrences': '120', 'last_ts': '2026-11-10T13:00:00Z'},
          {'service': 'core-provider', 'msg': 'reconcile failed: forbidden', 'occurrences': '40', 'last_ts': '2026-11-10T12:00:00Z'},
          {'service': 'snowplow', 'msg': 'informer resync slow', 'occurrences': '10', 'last_ts': '2026-11-10T11:00:00Z'},
          {'service': '', 'msg': 'unlabeled error', 'occurrences': '5', 'last_ts': '2026-11-10T10:00:00Z'}]
TOT = ch([{'total': '1495', 'distinct_problems': '15'}])


def digest(c, extras=None, rows=DIGEST, tot=TOT):
    resp = {'tot': tot} if tot is not None else {}
    if rows is not None:
        resp['digest'] = ch(rows)
    return gates.resolve(c.get('RESTAction', 'obs-error-log-digest'), extras or {}, resp)[2]


def check_telemetry(c, on):
    f = []
    expect(f, 'the tab', static_items(on, 'Col', 'obs-tab-telemetry'), ['obs-sec-logs-desc', 'obs-tel-stats', 'obs-tel-main'])
    row = c.get('Row', 'obs-tel-main')['spec']['widgetData']
    expect(f, 'two thirds and one third', [(i['resourceRefId'], i['size'], i['xs']) for i in row['items']],
           [('obs-errors-card', 16, 24), ('obs-tel-side', 8, 24)])
    out = digest(c, {'range': '7d'})
    expect(f, 'the figures', [(s['title'], s['value'], s['caption'], s['tone']) for s in out['stats']],
           [('Error lines', '1495', 'in the last 7 days', 'error'), ('Distinct problems', '15', 'grouped by service and message', 'error'),
            ('Services erroring', '2', 'among the 40 most frequent problems', 'error'), ('Window read', '7 d', 'the time range', 'window')])
    smap = wd(c, 'Listy', 'obs-tel-stats', out)['itemTemplate']['color']['map']
    cmap = wd(c, 'Listy', 'obs-error-log-list', out)['itemTemplate']['color']['map']
    expect(f, 'an error figure wears the error cards\' colour; the window is neutral', (smap['error'], 'window' in smap, smap['unread']),
           (cmap['ERROR'], False, 'orange'))
    six = digest(c, {'range': '6h'})
    expect(f, '6h: the window the SQL read, said', (six['stats'][3]['value'], six['stats'][3]['caption']),
           ('24 h', 'the logs are read per 1 h, 24 h or 7 d'))
    down = digest(c, rows=None, tot=None)
    expect(f, 'a failed read: "could not be read", never 0',
           [(s['value'], s['tone']) for s in down['stats']], [('', 'unread')] * 3 + [('24 h', 'window')])
    expect(f, 'a failed read: the line, as a warning', (items(c, 'Card', 'obs-errors-card', down), wd(c, 'Paragraph', 'obs-tel-empty', down)['type']),
           (['obs-tel-empty'], 'warning'))
    card = wd(c, 'Card', 'obs-errors-card', out)
    expect(f, 'the list card: the error cards; Summary | Full list top right',
           ([i['resourceRefId'] for i in card['items']], card['extraRefId'], card['title']),
           (['obs-error-log-list'], 'obs-tel-view-chips', 'Recurring errors'))
    tbl = digest(c, {'view': 'table'})
    card = wd(c, 'Card', 'obs-errors-card', tbl, {'view': 'table'})
    expect(f, '?view=table: the chronological stream, its service filter and row limit',
           ([i['resourceRefId'] for i in card['items']], card['title']),
           (['obs-svc-filter-note', 'obs-limit-group', 'obs-log-stream'], 'Error & warning stream'))
    expect(f, 'the view chips', [(v['label'], v['active'], v['route']) for v in tbl['viewChips']],
           [('Summary', False, '/observability?tab=Telemetry'), ('Full list', True, '/observability?view=table&tab=Telemetry')])
    cards = wd(c, 'Listy', 'obs-error-log-list', out)
    first = cards['dataSource'][0]
    expect(f, 'an error card: service, count, last seen, message; it opens the stream for its service',
           (first['title'], first['occ'], first['seen'].startswith('last seen '), first['msg'], first['route'], cards['itemTemplate']['rowVariant']),
           ('snowplow', '×120', True, 'context deadline exceeded', '/observability?range=7d&serviceName=snowplow&view=table&tab=Telemetry', 'card'))
    expect(f, 'a group with no service: named so, inert', (cards['dataSource'][3]['title'], cards['dataSource'][3]['route']), ('unknown service', ''))
    expect(f, 'errors by service', [(s['name'], s['count'], s['percent']) for s in out['byService']],
           [('snowplow', '130 lines', 100), ('core-provider', '40 lines', 30)])
    expect(f, 'the side', items(c, 'Flex', 'obs-tel-side', out), ['obs-errors-by-service-card', 'obs-resource-trend-card', 'obs-resource-card'])
    clean = digest(c, rows=[], tot=ch([{'total': '0', 'distinct_problems': '0'}]))
    expect(f, 'no error: the line, no by-service card', (wd(c, 'Paragraph', 'obs-tel-empty', clean)['text'], items(c, 'Flex', 'obs-tel-side', clean)),
           ('No error was logged in the last 24 hours.', ['obs-resource-trend-card', 'obs-resource-card']))
    # The search.
    q = digest(c, {'q': ' SNOWPLOW '})
    expect(f, 'the search: the matching groups', [r['msg'] for r in q['rows']], ['context deadline exceeded', 'informer resync slow'])
    expect(f, 'the figures follow it, and say so', [(s['value'], s['caption']) for s in q['stats'][:2]],
           [('130', 'matching the search, in the 40 most frequent'), ('2', 'grouped by service and message')])
    expect(f, 'the routes keep it', (q['viewChips'][1]['route'], q['rows'][0]['route']),
           ('/observability?q=SNOWPLOW&view=table&tab=Telemetry', '/observability?serviceName=snowplow&q=SNOWPLOW&view=table&tab=Telemetry'))
    href, prompt = traces.ask_of(q['askHref'])
    expect(f, 'Ask Agentiko keeps it and says so', (href, prompt.endswith('these are only the errors that match it, among the 40 most frequent.')),
           ('/observability?q=%20SNOWPLOW%20', True))
    none = digest(c, {'q': 'zzz'})
    expect(f, 'nothing matches: said', wd(c, 'Paragraph', 'obs-tel-empty', none)['text'], 'No error matches "zzz".')
    expect(f, 'nothing matches: the prompt does not claim no errors', traces.ask_of(none['askHref'])[1].startswith('No error my platform logged'), True)
    stream = gates.resolve(c.get('RESTAction', 'obs-log-stream'), {'q': 'warn'},
                           {'stream': ch([{'ts': 't', 'svc': 'a', 'lvl': 'WARN', 'msg': 'x'}, {'ts': 't', 'svc': 'b', 'lvl': 'ERROR', 'msg': 'y'}])})[2]
    expect(f, 'the stream follows the search', ([r['svc'] for r in stream['rows']], stream['read']), (['a'], 2))
    pods = gates.resolve(c.get('RESTAction', 'obs-resource-usage'), {},
                         {'pods': ch([{'ns': 'krateo-system', 'pod': 'snowplow-1', 'cpu_m': 120, 'mem_mib': 1500},
                                      {'ns': 'krateo-system', 'pod': 'frontend-1', 'cpu_m': 5, 'mem_mib': 600},
                                      {'ns': 'other', 'pod': 'x-1', 'cpu_m': 1, 'mem_mib': '150'}]),
                          'totals': ch([{'cpu_cores': 1.2, 'mem_gib': 2.2, 'pods': 3}])})[2]
    top = wd(c, 'Listy', 'obs-top-pods', pods)['dataSource']
    expect(f, 'the pods with the most memory: bars tinted by size, each opening its logs',
           [(p['name'], p['count'], p['percent'], p['tone']) for p in top] + [top[0]['route']],
           [('snowplow-1', '1500 MiB · 120 m CPU', 100, 'high'), ('frontend-1', '600 MiB · 5 m CPU', 40, 'mid'),
            ('x-1', '150 MiB · 1 m CPU', 10, 'low'), '/observability?serviceName=snowplow-1&view=table&tab=Telemetry'])
    expect(f, 'its card names the totals', wd(c, 'Card', 'obs-resource-card', pods)['extra'], '1.2 cores · 2.2 GiB · 3 pods')
    narrowed = gates.resolve(c.get('RESTAction', 'obs-resource-usage'), {'q': 'other'}, {'pods': ch([{'ns': 'other', 'pod': 'x-1', 'cpu_m': 1, 'mem_mib': 1},
                                                                                                    {'ns': 'k', 'pod': 'y', 'cpu_m': 1, 'mem_mib': 1}])})[2]
    expect(f, 'the pods follow the search', [p['name'] for p in narrowed['top']], ['x-1'])
    for kind, name in (('Card', 'obs-errors-by-service-card'), ('Listy', 'obs-errors-by-service'), ('Listy', 'obs-tel-view-chips'),
                       ('Paragraph', 'obs-sec-logs-desc'), ('Col', 'obs-tab-telemetry')):
        wd(c, kind, name, out)
    return f


# ---------------------------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------------------------

def dep(name, want, ready, image='ghcr.io/krateoplatformops/x:1.0', cond_msg=None):
    st = {'readyReplicas': ready}
    if cond_msg:
        st['conditions'] = [{'type': 'Available', 'status': 'False', 'message': cond_msg}]
    return {'metadata': {'name': name, 'namespace': 'krateo-system', 'managedFields': [{}]},
            'spec': {'replicas': want, 'template': {'spec': {'containers': [{'image': image}]}}}, 'status': st}


DEPS = {'items': [dep('snowplow', 2, 2, 'ghcr.io/krateoplatformops/snowplow:1.12.74'), dep('frontend', 1, 0, cond_msg='Deployment does not have minimum availability.'),
                  dep('core-provider', 1, 1), dep('old-thing', 0, 0)]}


def comps(c, extras=None, deps=DEPS):
    resp = {} if deps is None else {'deployments': deps}
    return gates.resolve(c.get('RESTAction', 'obs-components'), extras or {}, resp)[2]


def check_components(c):
    f = []
    expect(f, 'the tab', static_items(c, 'Col', 'obs-tab-components'), ['obs-sec-components-desc', 'obs-components-stats', 'obs-components-main'])
    row = c.get('Row', 'obs-components-main')['spec']['widgetData']
    expect(f, 'two thirds and one third', [(i['resourceRefId'], i['size'], i['xs']) for i in row['items']],
           [('obs-components-list-card', 16, 24), ('obs-components-side', 8, 24)])
    out = comps(c)
    expect(f, 'the figures', [(s['title'], s['value'], s['tone']) for s in out['stats']],
           [('Deployments', '4', 'total'), ('Ready', '2', 'ready'), ('Not ready', '1', 'notready'), ('Scaled to zero', '1', 'scaled')])
    smap = wd(c, 'Listy', 'obs-components-stats', out)['itemTemplate']['color']['map']
    cmap = wd(c, 'Listy', 'obs-components-cards', out)['itemTemplate']['color']['map']
    expect(f, 'each figure wears its state\'s card colour', {k: v for k, v in smap.items() if k != 'unread'}, cmap)
    expect(f, 'the donut colours the states as the cards', wd(c, 'PieChart', 'obs-components-by-state', out)['colorMap'],
           {'Ready': cmap['ready'], 'Not ready': cmap['notready'], 'Scaled to zero': cmap['scaled']})
    down = comps(c, deps=None)
    expect(f, 'a failed read: "could not be read", orange', ({(s['value'], s['tone']) for s in down['stats']}, smap['unread']),
           ({('', 'unread')}, 'orange'))
    card = wd(c, 'Card', 'obs-components-list-card', out)
    expect(f, 'the list card', ([i['resourceRefId'] for i in card['items']], card['extraRefId']),
           (['obs-components-state-chips', 'obs-components-cards'], 'obs-components-view-chips'))
    expect(f, '?view=table: the runtime table, then the declared versions', items(c, 'Card', 'obs-components-list-card', comps(c, {'view': 'table'}), {'view': 'table'}),
           ['obs-components-state-chips', 'obs-components', 'obs-sec-pins-label', 'obs-sec-pins-desc', 'platform-pins'])
    cards = wd(c, 'Listy', 'obs-components-cards', out)
    expect(f, 'Not ready first; each card its state, image, replicas, reason',
           [(x['name'], x['stateLabel'], x['image'], x['foot']) for x in cards['dataSource']],
           [('frontend', 'Not ready', 'x:1.0', '0/1 ready'), ('core-provider', 'Ready', 'x:1.0', '1/1 ready'),
            ('snowplow', 'Ready', 'snowplow:1.12.74', '2/2 ready'), ('old-thing', 'Scaled to zero', 'x:1.0', '0/0 ready')])
    expect(f, 'the reason is the condition\'s message', cards['dataSource'][0]['description'], 'Deployment does not have minimum availability.')
    expect(f, 'a card opens the component', cards['itemTemplate']['navigateTo'], '/observability/components/{name}')
    expect(f, 'the chips', [(x['label'], x['count'], x['active']) for x in out['chips']],
           [('All', 4, True), ('Not ready', 1, False), ('Ready', 2, False), ('Scaled to zero', 1, False)])
    nr = comps(c, {'componentStatus': 'notready', 'q': 'f'})
    expect(f, 'a state and a search together, and the table follows', ([x['name'] for x in nr['cards']], [d['metadata']['name'] for d in nr['list']]),
           (['frontend'], ['frontend']))
    expect(f, 'routes keep both', (nr['chips'][0]['route'], nr['viewChips'][1]['route']),
           ('/observability?q=f&tab=Components', '/observability?componentStatus=notready&q=f&view=table&tab=Components'))
    expect(f, 'the table rows follow the picked state', [r[0]['stringValue'] for r in wd(c, 'Table', 'obs-components', nr)['dataSource']], ['frontend'])
    expect(f, 'nothing matches: said', wd(c, 'Paragraph', 'obs-components-empty', comps(c, {'q': 'zzz'}))['text'], 'No component matches "zzz".')
    expect(f, 'the side', items(c, 'Flex', 'obs-components-side', out), ['obs-components-by-state-card', 'obs-components-readiness-card'])
    expect(f, 'replicas ready', [(x['name'], x['count'], x['percent'], x['tone']) for x in out['readiness']],
           [('snowplow', '2/2', 100, 'ready'), ('core-provider', '1/1', 100, 'ready'), ('frontend', '0/1', 0, 'notready')])
    for kind, name in (('Card', 'obs-components-by-state-card'), ('Card', 'obs-components-readiness-card'), ('Listy', 'obs-components-readiness'),
                       ('Listy', 'obs-components-state-chips'), ('Listy', 'obs-components-view-chips'), ('Paragraph', 'obs-sec-components-desc')):
        wd(c, kind, name, out)
    return f


# ---------------------------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------------------------

def spans(c, extras=None, rows=None):
    resp = {} if rows is False else {'spans': ch(traces.LIST_ROWS if rows is None else rows)}
    return gates.resolve(c.get('RESTAction', 'obs-traces'), extras or {}, resp)[2]


def check_traces(c):
    f = []
    expect(f, 'the tab', static_items(c, 'Col', 'obs-tab-traces'), ['obs-sec-traces-desc', 'obs-traces-filters', 'obs-traces-stats', 'obs-traces-main'])
    expect(f, 'the filter bar: service, user, the hidden note (the range is the page\'s, the status chips the card\'s)',
           static_items(c, 'Flex', 'obs-traces-filters'), ['obs-traces-service-group', 'obs-traces-user-group', 'obs-traces-hidden-note'])
    row = c.get('Row', 'obs-traces-main')['spec']['widgetData']
    expect(f, 'two thirds and one third', [(i['resourceRefId'], i['size'], i['xs']) for i in row['items']],
           [('obs-traces-list-card', 16, 24), ('obs-traces-side', 8, 24)])
    out = spans(c)
    expect(f, 'the figures', [(s['title'], s['value'], s['caption'], s['tone']) for s in out['stats']],
           [('Spans', '6', 'in the time range', 'spans'), ('Errors', '1', 'spans with StatusCode Error', 'error'),
            ('Services', '2', 'most: agentgateway', 'services'), ('Users', '2', 'with a jwt.sub (AI calls)', 'users')])
    smap = wd(c, 'Listy', 'obs-traces-stats', out)['itemTemplate']['color']['map']
    cmap = wd(c, 'Listy', 'obs-traces-cards', out)['itemTemplate']['color']['map']
    expect(f, 'Errors wears the Error cards\' colour', (smap['error'], smap['unread']), (cmap['Error'], 'orange'))
    expect(f, 'the donut colours the statuses as the cards', wd(c, 'PieChart', 'obs-traces-by-status', out)['colorMap'],
           {'Error': cmap['Error'], 'OK': cmap['Ok'], 'Unset': cmap['Unset']})
    down = spans(c, rows=False)
    expect(f, 'a failed read', ({(s['value'], s['caption'], s['tone']) for s in down['stats']}, items(c, 'Card', 'obs-traces-list-card', down)),
           ({('', 'could not be read', 'unread')}, ['obs-traces-status-chips', 'obs-traces-empty']))
    bad = spans(c, {'traceUser': "x' OR 1=1"}, rows=[])
    expect(f, 'a refused filter: said, not counted', (bad['stats'][0]['caption'], wd(c, 'Paragraph', 'obs-traces-empty', bad)['text']),
           ('a filter was refused', 'The user filter holds a character it may not, so no span was read.'))
    card = wd(c, 'Card', 'obs-traces-list-card', out)
    expect(f, 'the list card: the status chips, then the cards',
           ([i['resourceRefId'] for i in card['items']], card['extraRefId'], card['title']),
           (['obs-traces-status-chips', 'obs-traces-cards'], 'obs-traces-view-chips', 'Spans'))
    expect(f, '?view=table: the span table', items(c, 'Card', 'obs-traces-list-card', spans(c, {'view': 'table'}), {'view': 'table'}),
           ['obs-traces-status-chips', 'obs-traces'])
    cards = wd(c, 'Listy', 'obs-traces-cards', out)
    first = cards['dataSource'][3]
    expect(f, 'a span card', (first['span'], first['stateLabel'], first['tone'], first['service'], first['foot'], first['description']),
           ('GET /call restactions/obs-log-stream', 'Error', 'Error', 'snowplow', '2026-11-08 20:50:12.118 UTC · 1.94 s',
            'User cyberjoker · trace 2a88…c3f0'))
    expect(f, 'a card opens the trace', cards['itemTemplate']['navigateTo'], '/observability/traces/{traceId}')
    expect(f, 'the view chips keep the filters', [v['route'] for v in spans(c, {'traceUser': 'admin'})['viewChips']],
           ['/observability?range=24h&traceUser=admin&tab=Traces', '/observability?range=24h&traceUser=admin&view=table&tab=Traces'])
    expect(f, 'the side', items(c, 'Flex', 'obs-traces-side', out), ['obs-traces-by-status-card', 'obs-traces-by-service-card', 'obs-traces-slowest-card'])
    expect(f, 'by service: a row sets the Service filter', [(s['name'], s['count'], s['tone'], s['route']) for s in out['byService']],
           [('agentgateway', '3', 'ok', '/observability?range=24h&traceService=agentgateway&tab=Traces'),
            ('snowplow', '3 · 1 in error', 'bad', '/observability?range=24h&traceService=snowplow&tab=Traces')])
    expect(f, 'the slowest, each opening its trace', [(s['count'], s['route'].split('/')[-1][:4]) for s in out['slowest']][:2],
           [('11.2 s', '71b5'), ('5.81 s', '4f1c')])
    q = spans(c, {'q': ' CYBERJOKER '})
    expect(f, 'the search: over user, and the figures follow', ([r['traceId'][:4] for r in q['rows']], q['stats'][0]['caption'], q['stats'][1]['value']),
           (['2a88'], 'matching the search, of 6 read', '1'))
    expect(f, 'the routes keep it', (q['viewChips'][0]['route'], q['byService'][0]['route']),
           ('/observability?range=24h&q=CYBERJOKER&tab=Traces', '/observability?range=24h&traceService=snowplow&q=CYBERJOKER&tab=Traces'))
    st = wd(c, 'Listy', 'obs-traces-status-chips', {}, {'q': 'x', 'view': 'table'})['dataSource']
    expect(f, 'the status chips keep the search and the view', st[1]['route'], '/observability?range=24h&traceStatus=Error&q=x&view=table&tab=Traces')
    none = spans(c, {'q': 'zzz'})
    expect(f, 'nothing matches: said', wd(c, 'Paragraph', 'obs-traces-empty', none)['text'], 'No span matches "zzz".')
    many = [dict(traces.LIST_ROWS[0], trace_id=f'{i:032x}') for i in range(30)]
    big = spans(c, rows=many)
    expect(f, 'more than 24', (len(big['cards']), items(c, 'Card', 'obs-traces-list-card', big)[-1], wd(c, 'Paragraph', 'obs-traces-more', big)['text']),
           (24, 'obs-traces-more', '24 of 30 shown here. Full list has all 30.'))
    for kind, name in (('Card', 'obs-traces-by-status-card'), ('Card', 'obs-traces-by-service-card'), ('Card', 'obs-traces-slowest-card'),
                       ('Listy', 'obs-traces-by-service'), ('Listy', 'obs-traces-slowest'), ('Listy', 'obs-traces-view-chips')):
        wd(c, kind, name, out)
    return f


def check_unchanged_tabs(c, on):
    """SLOs and the SP tabs keep their layout: charts and cards, no list to summarise."""
    f = []
    expect(f, 'SLOs', static_items(c, 'Col', 'obs-tab-slos'),
           ['obs-slo-desc', 'obs-slo-window', 'obs-slo-note', 'obs-slo-avail-card', 'obs-slo-latency-card'])
    for name in ('sp-tab-cache', 'sp-tab-invalidation', 'sp-tab-informers', 'sp-tab-http'):
        kinds = {r['resource'] for r in on.get('Col', name)['spec']['resourcesRefs']['items']}
        expect(f, f'{name}: cards only', kinds, {'cards'})
    return f


def check_authored(c, on):
    for chart, label in ((c, 'default'), (on, 'every flag')):
        for d in chart.docs:
            if d.get('apiVersion', '').startswith('widgets.') and d['metadata']['name'].startswith(('obs-', 'page-observability')):
                tbi.RESOLVED.append((f'{d["kind"]} {d["metadata"]["name"]} as rendered ({label})', copy.deepcopy(d)))
    return []


def main():
    args = sys.argv[1:]
    crds_dir = args[args.index('--crds') + 1] if '--crds' in args else None
    c = traces.render()
    on = traces.render(values={'observability': {'reconcileTracing': True, 'snowplowSli': True}})
    checks = [
        ('the page: header, toolbar (range | search), tabs', lambda: check_page(c, on)),
        ('filter-keys: every widget the root reaches declares them', lambda: check_filter_keys(c, on)),
        ('Reconciliation: the figures and their colours; failed and partial reads', lambda: check_recon_figures(c, on)),
        ('Reconciliation: the layout, the side, the cap', lambda: check_recon_layout(c, on)),
        ('Reconciliation: the cards and the full list', lambda: check_recon_cards_and_table(c, on)),
        ('Reconciliation: the chips, the scope and the search', lambda: check_recon_chips_and_search(c, on)),
        ('Reconciliation: the reads', lambda: check_recon_query(on)),
        ('Telemetry: figures, errors as cards, the stream as the full list, the side, the search', lambda: check_telemetry(c, on)),
        ('Components: figures, chips, cards, the full list, the side, the search', lambda: check_components(c)),
        ('Traces: figures, status chips, cards, the full list, the side, the search', lambda: check_traces(c)),
        ('SLOs and the SP tabs keep their layout', lambda: check_unchanged_tabs(c, on)),
    ]
    if crds_dir:
        checks.append(('(authored widgets queued for validation)', lambda: check_authored(c, on)))
    failed = 0
    for label, run in checks:
        try:
            problems = run()
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError, ValueError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {label}')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    total = len(checks)
    if crds_dir:
        total += 1
        problems = tbi.validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} widgets (rendered and resolved) validate against the CRDs')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}{"" if crds_dir else "; widgets NOT validated (no --crds)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
