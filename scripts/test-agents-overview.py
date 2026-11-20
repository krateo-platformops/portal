#!/usr/bin/env python3
"""
test-agents-overview — /agents in the A4 format (mockup A4, as /incidents), every tab, over the chart's own jq.

WHAT IT GUARDS.
THE OVERVIEW TAB:
  - the filters (root-pages slice 3a): the agents and the tool servers are one base set; Kind, Status
    (Registered/NotRegistered for an agent, by the gateway check; Accepted/NotAccepted for a tool
    server) and Owner count under every OTHER filter; a facet with one value is left out; an agent
    gets no Status while the gateway could not be checked;
  - the toolbar: the facets on the left (18/24), the search on the right (6/24), stacked when narrow;
    the search (?q=) narrows the figures, the chips, the cards, the charts and the table, every route
    keeps it, and an empty result says `No agent or tool server matches "<q>".`;
  - the five figures: Agents (blue), Ready and registered (green), Needs attention, Gateway gaps, Tool
    servers NotAccepted (red), coloured zero or not, with the cards' colour map; a gateway not checked
    is "—", "not checked", orange; a failed read is "—", "could not be read", orange;
  - the main row, 16/8: the agents card (the group chips, Needs attention by default while anything
    needs it, else All; the cards, at most 24 then a line; ?view=table the 12-column agents table of
    the group; a line when there is nothing to list) and the charts (health, pattern, model, tokens);
  - Ask Agentiko keeps every filter and names the view.
THE OTHER TABS:
  - Tool servers: its figures, its state chips (?group=: Needs attention, Healthy, Accepted, NotAccepted,
    Advisories, All), a card per
    server or the full table, its charts; the matrix and the bindings stay below;
  - Policies: its four figures, its state chips (?group=: All, Healthy, Not healthy, Not attached) which
    narrow the role cards (a line when none is left), above the role cards; a state tile routes as its chip;
  - Token usage: the range, the notice, the spend figures, the spend charts side by side, then spend per
    agent, user and group (the spend itself: test-agents-cost); the Overview's token
    card reads the token totals over its own range, one point per bucket ending now;
  - every widget reachable from the page declares the page's filter keys (also lint-keyextras), and
    the Token usage tab and the widgets shown with it also the day picker's `from` and `to`.

Usage: test-agents-overview.py [--crds DIR]. JQ=gojq runs the same on snowplow's engine.
"""
import importlib.util
import os
import sys
import time
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

NS = 'krateo-system'
KX = ['projects', 'kind', 'status', 'owner', 'q', 'view', 'range', 'group']


def agent(name, delegates=0, ready=True, owner='agentiko', description=None, bench=False, model='default-model-config'):
    tools = [{'type': 'Agent', 'agent': {'name': f'sub-{i}'}} for i in range(delegates)]
    tools.append({'type': 'McpServer', 'mcpServer': {'name': 'docs', 'toolNames': ['search', 'read']}})
    labels = {'krateo.io/composition-name': owner}
    if not bench:
        labels['krateo.io/composition-id'] = 'x'
    return {'metadata': {'name': name, 'namespace': NS, 'labels': labels},
            'spec': {'description': description or f'{name} does one job', 'declarative': {'modelConfig': model, 'tools': tools}},
            'status': {'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}, {'type': 'Accepted', 'status': 'True'}]}}


def server(name, accepted=True, tools=3, owner='kagent', url=None):
    return {'metadata': {'name': name, 'namespace': NS, 'labels': {'app.kubernetes.io/managed-by': owner}},
            'spec': {'description': f'{name} tools', 'url': url or f'http://{name}.{NS}.svc:8080/mcp', 'protocol': 'STREAMABLE_HTTP', 'timeout': '30s'},
            'status': {'discoveredTools': [{'name': f't{i}'} for i in range(tools)],
                       'conditions': [{'type': 'Accepted', 'status': 'True' if accepted else 'False',
                                       'reason': 'Accepted' if accepted else 'ReconcileFailed',
                                       'message': '' if accepted else 'failed to connect client for toolServer'}]}}


def route(*hosts):
    return {'metadata': {'name': 'r', 'namespace': NS},
            'spec': {'rules': [{'matches': [{'headers': [{'name': 'X-Kagent-Host', 'value': h}]}]} for h in hosts]}}


def policy(*hosts):
    exprs = []
    for h in hosts:
        exprs.append(f"request.headers['x-kagent-host'] == '{h}' && request.path.endsWith('/.well-known/agent-card.json')")
        exprs.append(f"request.headers['x-kagent-host'] == '{h}'")
    return {'metadata': {'name': 'p', 'namespace': NS},
            'spec': {'traffic': {'authorization': {'action': 'Allow', 'policy': {'matchExpressions': exprs}}}}}


AGENTS = [agent('agentiko', delegates=9), agent('frontend-agent', delegates=1), agent('k8s-agent'), agent('helm-agent', bench=True),
          agent('nightly-review-agent', owner='nightly-review'), agent('broken-agent', ready=False)]
REGISTERED = [f'{a["metadata"]["name"]}.{NS}' for a in AGENTS if a['metadata']['name'] != 'nightly-review-agent']
SERVERS = [server('docs'), server('clickhouse', tools=10), server('cujbench-evidence', accepted=False, tools=13, owner='—x')]
MODELS = {'items': [{'metadata': {'name': 'default-model-config'}, 'spec': {'model': 'gemini'}}]}


def resolve(extras, routes=True, agents=None, servers=None, agents_error=False):
    r = {'agents': 'ERROR' if agents_error else {'items': AGENTS if agents is None else agents}, 'models': MODELS,
         'servers': {'items': SERVERS if servers is None else servers},
         'routes': {'items': [route(*REGISTERED)] if routes else []}, 'policies': {'items': [policy(*REGISTERED)]}}
    return tbi.resolve(CH, 'agents-list', r, extras)


def w(kind, name, out, extras=None):
    return tbi.resolved_widget(CH, kind, name, out, extras or {}, f'{kind} {name}')['spec']['widgetData']


def items(kind, name, out, extras=None):
    return [i['resourceRefId'] for i in w(kind, name, out, extras)['items']]


def static(kind, name):
    return tbi.resolved_widget(CH, kind, name, {}, {}, f'{kind} {name}')['spec']['widgetData']


def facet(out, key):
    return {v['value']: v['count'] for v in [f for f in out['facets'] if f['key'] == key][0]['values']}


def tiles(listy):
    return [(t['title'], t['value'], t['caption'], t['tone']) for t in listy['dataSource']]


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


# ---------------------------------------------------------------- the Overview tab

def check_facets():
    f = []
    out = resolve({})
    expect(f, 'Kind: agents and tool servers, one base set', facet(out, 'kind'), {'agent': 6, 'server': 3})
    expect(f, 'Status: by the gateway for an agent, by Accepted for a server', facet(out, 'status'),
           {'registered': 5, 'notregistered': 1, 'accepted': 2, 'notaccepted': 1})
    expect(f, 'Owner: the composition, else managed-by', facet(out, 'owner'), {'agentiko': 5, 'nightly-review': 1, 'kagent': 2, '—x': 1})
    out = resolve({'kind': 'server'})
    expect(f, '?kind=server: the kind counts ignore it', facet(out, 'kind'), {'agent': 6, 'server': 3})
    expect(f, '?kind=server: the status counts apply it', facet(out, 'status'), {'registered': 0, 'notregistered': 0, 'accepted': 2, 'notaccepted': 1})
    out = resolve({}, routes=False)
    expect(f, 'gateway not checked: no agent has a Status, only the servers', facet(out, 'status'), {'accepted': 2, 'notaccepted': 1})
    out = resolve({})
    expect(f, 'the filter bar: the facets with two values or more', items('Flex', 'agents-filter-bar', out), ['agents-kind', 'agents-status', 'agents-owner'])
    one = [agent('solo'), agent('duo')]
    out = resolve({}, agents=one, servers=[])
    expect(f, 'one value each: every facet left out', items('Flex', 'agents-filter-bar', out), [])
    return f


CARD_MAP = {'all': 'blue', 'ok': 'green', 'bad': 'red', 'warn': 'orange', 'unchecked': 'orange', 'unread': 'orange'}


def check_figures():
    f = []
    out = resolve({})
    expect(f, 'the five figures', tiles(w('Listy', 'agents-stats', out)), [
        ('Agents', '6', '2 orchestrators · 4 specialists', 'all'),
        ('Ready and registered', '4', 'of 6 agents', 'ok'),
        ('Needs attention', '3', 'unreachable, not Ready or not Accepted', 'bad'),
        ('Gateway gaps', '1', 'not routed or not allowed', 'bad'),
        ('Tool servers NotAccepted', '1', 'of 3 · 13 tools discovered', 'bad')])
    calm = [a for a in AGENTS if a['metadata']['name'] not in ('broken-agent', 'nightly-review-agent')]
    rows = tiles(w('Listy', 'agents-stats', resolve({}, agents=calm, servers=SERVERS[:2])))
    expect(f, 'zero is still coloured by its state', [(t[0], t[1], t[3]) for t in rows[2:]],
           [('Needs attention', '0', 'bad'), ('Gateway gaps', '0', 'bad'), ('Tool servers NotAccepted', '0', 'bad')])
    rows = tiles(w('Listy', 'agents-stats', resolve({}, routes=False)))
    expect(f, 'gateway not checked: "Ready", and "—" not checked in orange, never 0 gaps', (rows[1][0], rows[1][2], rows[3]),
           ('Ready', 'of 6 agents; gateway not checked', ('Gateway gaps', '', 'not checked', 'unchecked')))
    rows = tiles(w('Listy', 'agents-stats', resolve({}, agents_error=True)))
    expect(f, 'a failed Agent read: "—", could not be read, orange; the servers tile still counts', rows, [
        ('Agents', '', 'could not be read', 'unread'), ('Ready and registered', '', 'could not be read', 'unread'),
        ('Needs attention', '', 'could not be read', 'unread'), ('Gateway gaps', '', 'could not be read', 'unread'),
        ('Tool servers NotAccepted', '1', 'of 3 · 13 tools discovered', 'bad')])
    stats_map = static('Listy', 'agents-stats')['itemTemplate']['color']['map']
    cards_map = static('Listy', 'agents-cards')['itemTemplate']['color']['map']
    expect(f, 'the figures and the cards share one colour map', (stats_map, cards_map), (CARD_MAP, CARD_MAP))
    return f


def qs(route):
    """A route's query as a dict (the page reads its extras from it)."""
    from urllib.parse import urlsplit, parse_qsl
    return dict(parse_qsl(urlsplit(route).query))


def routes(out):
    return {t['title']: t['route'] for t in out['stats']}


def check_figure_routes():
    """A state tile opens the Overview filtered to that state, keeping every other param; the total,
    an unchecked gateway and a failed read are plain tiles (route "")."""
    f = []
    ex = {'projects': 'p', 'q': 'agent', 'view': 'table', 'range': '7d', 'owner': 'agentiko', 'group': 'orchestrators'}
    out = resolve(ex)
    r = routes(out)
    chips = {c['label']: c['route'] for c in w('Listy', 'agents-group-chips', out, ex)['dataSource']}
    expect(f, 'Needs attention routes as its group chip', r['Needs attention'], chips['Needs attention'])
    expect(f, 'Needs attention keeps q, view, range and the facets (attention is the default: no ?group)',
           r['Needs attention'], '/agents?projects=p&owner=agentiko&q=agent&view=table&range=7d')
    expect(f, 'Ready and registered: the new ?group=ready, the other params kept', r['Ready and registered'],
           '/agents?projects=p&owner=agentiko&q=agent&view=table&range=7d&group=ready')
    expect(f, 'Gateway gaps: the Status facet NotRegistered, the group cleared', r['Gateway gaps'],
           '/agents?projects=p&status=notregistered&owner=agentiko&q=agent&view=table&range=7d')
    expect(f, 'Tool servers NotAccepted: the Status facet NotAccepted, the group cleared', r['Tool servers NotAccepted'],
           '/agents?projects=p&status=notaccepted&owner=agentiko&q=agent&view=table&range=7d')
    expect(f, 'Agents (a total) is not clickable', r['Agents'], '')
    # Each route lands on exactly what its tile counts.
    base = resolve({})
    want = {t['title']: t['value'] for t in base['stats']}
    for title in ('Ready and registered', 'Needs attention', 'Gateway gaps', 'Tool servers NotAccepted'):
        q = qs(routes(base)[title])
        expect(f, f'{title}: the route lists what the tile counts',
               str(len(w('Listy', 'agents-cards', resolve(q), q)['dataSource'])), want[title])
    ready = resolve({'group': 'ready'})
    expect(f, '?group=ready: the healthy agents, orchestrators first', (ready['group'], [c['title'] for c in ready['cards']]),
           ('ready', ['agentiko', 'frontend-agent', 'helm-agent', 'k8s-agent']))
    expect(f, '?group=ready: its chip is active', [c['label'] for c in ready['groupChips'] if c['active']], ['Ready and registered'])
    # Every state tile with a chip of its own: the same route (q, view, range and the facets kept) and the same count.
    chip = {c['label']: c for c in w('Listy', 'agents-group-chips', out, ex)['dataSource']}
    tile = {t['title']: t for t in out['stats']}
    for title in ('Ready and registered', 'Needs attention'):
        expect(f, f'{title}: the tile routes as its chip', tile[title]['route'], chip[title]['route'])
        expect(f, f'{title}: the chip counts what the tile counts', str(chip[title]['count']), tile[title]['value'])
    for title in ('Ready and registered', 'Needs attention'):
        expect(f, f'{title} (no filter): the chip counts what the tile counts',
               str({c['label']: c['count'] for c in base['groupChips']}[title]), want[title])
    expect(f, 'gateway not checked: the chip is named as its tile, Ready', [c['label'] for c in resolve({}, routes=False)['groupChips']][1], 'Ready')
    calm = [a for a in AGENTS if a['metadata']['name'] not in ('broken-agent', 'nightly-review-agent')]
    zero = resolve({}, agents=calm, servers=SERVERS[:2])
    expect(f, 'a zero tile is still clickable', (routes(zero)['Gateway gaps'], routes(zero)['Needs attention']),
           ('/agents?status=notregistered', '/agents?group=attention'))
    expect(f, 'an active tile still routes to its filter (no toggle)', routes(resolve({'group': 'ready'}))['Ready and registered'], '/agents?group=ready')
    expect(f, 'gateway not checked: Gateway gaps is not clickable', routes(resolve({}, routes=False))['Gateway gaps'], '')
    bad = resolve({}, agents_error=True)
    expect(f, 'a failed read is not clickable', [t['route'] for t in bad['stats'] if t['tone'] == 'unread'], [''] * 4)
    expect(f, 'the figures Listy navigates to {route}', static('Listy', 'agents-stats')['itemTemplate'].get('navigateTo'), '{route}')
    return f


def check_chips_and_cards():
    f = []
    out = resolve({})
    chips = [(c['label'], c['count'], c['active'], c['route']) for c in w('Listy', 'agents-group-chips', out)['dataSource']]
    expect(f, 'the group chips, Needs attention by default', chips, [
        ('Needs attention', 3, True, '/agents'), ('Ready and registered', 4, False, '/agents?group=ready'),
        ('Orchestrators', 2, False, '/agents?group=orchestrators'),
        ('Specialists', 2, False, '/agents?group=specialists'), ('All', 9, False, '/agents?group=all')])
    cards = w('Listy', 'agents-cards', out)['dataSource']
    expect(f, 'Needs attention: the agents (by name), then the tool servers', [(c['title'], c['tag'], c['tone'], c['href']) for c in cards],
           [('broken-agent', 'NotReady', 'bad', f'/agents/{NS}/broken-agent'),
            ('nightly-review-agent', 'NotRegistered', 'bad', f'/agents/{NS}/nightly-review-agent'),
            ('cujbench-evidence', 'NotAccepted', 'bad', '')])
    expect(f, 'a tool server card is inert: no page to open', (cards[2]['href'], cards[2]['cta'], cards[2]['kind']), ('', '', 'Tool server'))
    expect(f, 'the gap is named', cards[1]['foot'], 'Gateway: not routed · no Allow rule · owner nightly-review')
    ex = {'group': 'orchestrators'}
    orch = w('Listy', 'agents-cards', resolve(ex), ex)['dataSource']
    expect(f, 'Orchestrators: most delegates first, with delegates, tools and servers, green', [(c['title'], c['tag'], c['tone'], c['foot']) for c in orch],
           [('agentiko', 'Registered', 'ok', '9 delegates · 2 tools on 1 server'), ('frontend-agent', 'Registered', 'ok', '1 delegate · 2 tools on 1 server')])
    ex = {'group': 'all'}
    out = resolve(ex)
    expect(f, 'All: triage order, every agent and tool server in view', [c['title'] for c in w('Listy', 'agents-cards', out, ex)['dataSource']],
           ['broken-agent', 'nightly-review-agent', 'cujbench-evidence', 'agentiko', 'frontend-agent', 'helm-agent', 'k8s-agent', 'docs', 'clickhouse'])
    expect(f, 'All: the chip routes keep it, the default is left out', [c['route'] for c in out['groupChips']][0], '/agents')
    calm = [a for a in AGENTS if a['metadata']['name'] not in ('broken-agent', 'nightly-review-agent')]
    out = resolve({}, agents=calm, servers=SERVERS[:2])
    expect(f, 'nothing needs attention: All is the default', [(c['label'], c['active'], c['route']) for c in out['groupChips']],
           [('Needs attention', False, '/agents?group=attention'), ('Ready and registered', False, '/agents?group=ready'),
            ('Orchestrators', False, '/agents?group=orchestrators'),
            ('Specialists', False, '/agents?group=specialists'), ('All', True, '/agents')])
    ex = {'group': 'healthy'}
    expect(f, "another tab's group falls back to the default", resolve(ex)['group'], 'attention')
    many = AGENTS + [agent(f'cold-{i:02}', ready=False) for i in range(30)]
    out = resolve({'projects': 'p'}, agents=many)
    card = items('Card', 'agents-list-card', out, {'projects': 'p'})
    expect(f, 'past 24: the cards, then a line', (len(w('Listy', 'agents-cards', out)['dataSource']), card),
           (24, ['agents-group-chips', 'agents-cards', 'agents-more']))
    expect(f, 'the line says where the rest are', w('Paragraph', 'agents-more', out)['text'],
           '24 of 33 shown here. Full list has the 32 agents; the tool servers are on the Tool servers tab.')
    expect(f, 'every route keeps the project scope', [c['route'] for c in out['viewChips']], ['/agents?projects=p', '/agents?projects=p&view=table'])
    return f


def check_layout():
    f = []
    out = resolve({})
    page = CH.get('Flex', 'page-agents')['spec']['widgetData']
    expect(f, 'the page: the header, then the tabs', [i['resourceRefId'] for i in page['items']], ['agents-page-header', 'agents-main'])
    expect(f, 'the tabs', [i['label'] for i in CH.get('Tabs', 'agents-main')['spec']['widgetData']['items']],
           ['Overview', 'Topology', 'Tool servers', 'Policies', 'Token usage', 'Request flow'])
    expect(f, 'the Overview', items('Flex', 'agents-overview', out), ['agents-toolbar', 'agents-stats', 'agents-overview-main'])
    ex = {'status': 'notaccepted'}
    expect(f, 'a filter applied: its chips under the toolbar', items('Flex', 'agents-overview', resolve(ex), ex),
           ['agents-toolbar', 'agents-active-filters', 'agents-stats', 'agents-overview-main'])
    unchecked = resolve({}, routes=False)
    expect(f, 'gateway not checked: the notice above the toolbar', items('Flex', 'agents-overview', unchecked),
           ['agents-gateway-notice', 'agents-toolbar', 'agents-stats', 'agents-overview-main'])
    notice = w('Paragraph', 'agents-gateway-notice', unchecked)
    expect(f, 'the notice is a warning that says why', (notice['text'].startswith('Gateway registration could not be checked ('), notice['type']), (True, 'warning'))
    bar = w('Row', 'agents-toolbar', out)
    expect(f, 'the toolbar: the facets left, the search in a quarter, stacked when narrow',
           [(i['resourceRefId'], i['size'], i['xs'], i['lg']) for i in bar['items']], [('agents-filter-bar', 18, 24, 18), ('agents-search-wrap', 6, 24, 6)])
    expect(f, 'the search wrap holds the search', items('Flex', 'agents-search-wrap', out), ['agents-search'])
    search = static('Input', 'agents-search')
    expect(f, 'the search writes ?q= on Enter', (search['queryParam'], search['allowClear'], search['size'], search['placeholder']),
           ('q', True, 'middle', 'Search agents and tool servers · press Enter'))
    row = w('Row', 'agents-overview-main', out)
    expect(f, 'two thirds and one third, stacked when narrow', [(i['resourceRefId'], i['size'], i['xs'], i['lg']) for i in row['items']],
           [('agents-list-card', 16, 24, 16), ('agents-side', 8, 24, 8)])
    card = w('Card', 'agents-list-card', out)
    expect(f, 'the agents card: chips, then the cards; Summary | Full list top right',
           ([i['resourceRefId'] for i in card['items']], card['extraRefId'], card['title'], card['titleVariant']),
           (['agents-group-chips', 'agents-cards'], 'agents-view-chips', 'Agents', 'eyebrow'))
    expect(f, 'the view chips', [(c['label'], c['active']) for c in w('Listy', 'agents-view-chips', out)['dataSource']],
           [('Summary', True), ('Full list', False)])
    ex = {'view': 'table'}
    tbl = resolve(ex)
    expect(f, '?view=table: the 12-column table instead', items('Card', 'agents-list-card', tbl, ex), ['agents-group-chips', 'agents-list'])
    expect(f, '?view=table: Full list active', [c['active'] for c in tbl['viewChips']], [False, True])
    expect(f, 'the charts', items('Flex', 'agents-side', out), ['agents-health-card', 'agents-pattern-card', 'agents-models-card', 'agents-token-trend-card'])
    failed = resolve({}, agents_error=True)
    expect(f, 'a failed Agent read: a warning line, and only the token chart', (items('Card', 'agents-list-card', failed), items('Flex', 'agents-side', failed)),
           (['agents-group-chips', 'agents-empty'], ['agents-token-trend-card']))
    empty = w('Paragraph', 'agents-empty', failed)
    expect(f, 'the warning says why', (empty['text'].startswith('The agents could not be read ('), empty['type']), (True, 'warning'))
    for kind, name in (('Card', 'agents-health-card'), ('Card', 'agents-pattern-card'), ('Card', 'agents-models-card')):
        static(kind, name)
    hdr = CH.get('PageHeader', 'agents-page-header')['spec']['widgetData']
    expect(f, 'the header keeps its buttons', [i['resourceRefId'] for i in hdr['items']], ['agents-policy-new-header', 'agents-ask-agentiko', 'agents-new'])
    gone = [d['metadata']['name'] for d in CH.docs if d['metadata'].get('name', '') in (
        'agents-strip', 'agents-calm', 'agents-groups', 'agents-view-toggle', 'agents-servers-calm', 'agents-stats-row',
        'agents-servers-head', 'agents-servers-title', 'agents-servers-subtitle', 'agents-token-charts')]
    expect(f, 'the replaced widgets are gone', gone, [])
    return f


def check_search():
    f = []
    ex = {'q': ' HELM '}
    out = resolve(ex)
    expect(f, 'search: name and description, any case, trimmed', [a['name'] for a in out['agentsInView']], ['helm-agent'])
    expect(f, 'the figures follow it', [t['value'] for t in out['stats']], ['1', '1', '0', '0', '0'])
    expect(f, 'the chips follow it', [c['count'] for c in out['groupChips']], [0, 1, 0, 1, 1])
    expect(f, 'every route keeps it', [c['route'] for c in out['groupChips']],
           ['/agents?q=%20HELM%20&group=attention', '/agents?q=%20HELM%20&group=ready', '/agents?q=%20HELM%20&group=orchestrators', '/agents?q=%20HELM%20&group=specialists', '/agents?q=%20HELM%20'])
    expect(f, 'nothing in attention: All is the default, with the agent', [c['title'] for c in w('Listy', 'agents-cards', out, ex)['dataSource']], ['helm-agent'])
    expect(f, 'the charts follow it', (out['byHealth'], [(p['name'], p['count']) for p in out['byPattern']]),
           ([{'state': 'Healthy', 'count': 1}], [('Specialists', 1), ('Bench (no composition)', 1)]))
    ex = {'q': 'nothing-like-this'}
    out = resolve(ex)
    expect(f, 'no match: said, with the search', (items('Card', 'agents-list-card', out, ex), w('Paragraph', 'agents-empty', out, ex)['text']),
           (['agents-group-chips', 'agents-empty'], 'No agent or tool server matches "nothing-like-this".'))
    path = w('Button', 'agents-ask-agentiko', out, ex)['actions']['navigate'][0]['path']
    expect(f, 'Ask says the view is searched', 'search "nothing-like-this"' in parse_qs(path.partition('?')[2])['ask'][0], True)
    return f


def check_side():
    f = []
    out = resolve({})
    expect(f, 'health: everything in view', out['byHealth'], [{'state': 'Healthy', 'count': 6}, {'state': 'Needs attention', 'count': 3}])
    pie = static('PieChart', 'agents-health')
    expect(f, 'the donut wears the cards\' colours', pie['colorMap'], {'Healthy': CARD_MAP['ok'], 'Needs attention': CARD_MAP['bad']})
    expect(f, 'pattern: orchestrators, specialists, bench, against every agent', [(p['name'], p['label'], p['percent'], p['tone']) for p in out['byPattern']],
           [('Orchestrators', '2', 33, 'orchestrator'), ('Specialists', '4', 67, 'specialist'), ('Bench (no composition)', '1', 17, 'bench')])
    models = [agent('a', model='m1'), agent('b', model='m1'), agent('c', model='m2')]
    mc = {'items': [{'metadata': {'name': 'm1'}, 'spec': {'model': 'gemini'}}, {'metadata': {'name': 'm2'}, 'spec': {'model': 'claude'}}]}
    r = {'agents': {'items': models}, 'models': mc, 'servers': {'items': []}, 'routes': {'items': []}, 'policies': {'items': []}}
    out = tbi.resolve(CH, 'agents-list', r, {})
    expect(f, 'models: the resolved model, most first', [(m['name'], m['label'], m['percent']) for m in w('Listy', 'agents-models', out)['dataSource']],
           [('gemini', '2', 67), ('claude', '1', 33)])
    w('Listy', 'agents-pattern', out)
    return f


def check_page_and_table():
    f = []
    ex = {'status': 'notregistered,notaccepted', 'view': 'table'}
    out = resolve(ex)
    expect(f, '?view=table: the card holds the 12-column table', items('Card', 'agents-list-card', out, ex), ['agents-group-chips', 'agents-list'])
    names = [{c['valueKey']: c.get('stringValue') for c in r}['name'] for r in w('Table', 'agents-list', out, ex)['dataSource']]
    expect(f, '?view=table: the agents of the group the filters match', names, ['nightly-review-agent'])
    expect(f, 'Summary keeps every filter', out['viewChips'][0]['route'], '/agents?status=notregistered%2Cnotaccepted')
    ex2 = {'view': 'table', 'group': 'orchestrators'}
    names = [{c['valueKey']: c.get('stringValue') for c in r}['name'] for r in w('Table', 'agents-list', resolve(ex2), ex2)['dataSource']]
    expect(f, '?view=table&group=orchestrators: the orchestrators', names, ['agentiko', 'frontend-agent'])
    ex3 = {'view': 'table', 'kind': 'server'}
    out3 = resolve(ex3)
    expect(f, '?view=table with only tool servers: a line, not an empty table', (items('Card', 'agents-list-card', out3, ex3), out3['emptyText']),
           (['agents-group-chips', 'agents-empty'], 'The Full list has agents only; the tool servers in this group are on the Tool servers tab.'))
    path = w('Button', 'agents-ask-agentiko', out, ex)['actions']['navigate'][0]['path']
    params = parse_qs(path.partition('?')[2])
    expect(f, 'Ask keeps every filter', [k for k in params], ['status', 'view', 'ask'])
    expect(f, 'Ask names the view and what is wrong in it', params['ask'][0].startswith(
        'Review the part of my agent control plane on screen (Status: NotAccepted, NotRegistered): 1 agent and 1 tool server. '
        '1. nightly-review-agent cannot be reached through the gateway (not routed · no Allow rule) 2. cujbench-evidence is not Accepted (ReconcileFailed).'), True)
    expect(f, 'unfiltered Ask is unchanged', parse_qs(resolve({})['askHref'].partition('?')[2])['ask'][0].startswith('Review my agent control plane: 6 agents, 2 orchestrators.'), True)
    return f


# ---------------------------------------------------------------- the Tool servers tab

def servers_out(extras, servers=None, agents_error=False, servers_error=False):
    r = {'servers': 'ERROR' if servers_error else {'items': SERVERS + [server('flux-schema', tools=2, url='https://schemas.example.com/mcp')] if servers is None else servers},
         'agents': 'ERROR' if agents_error else {'items': AGENTS}, 'backends': {'items': []}, 'routes': {'items': []}}
    return tbi.resolve(CH, 'agents-servers', r, extras)


def check_servers_tab():
    f = []
    expect(f, 'the tab', items('Flex', 'agents-servers', {}), ['agents-servers-notice', 'agents-servers-stats', 'agents-servers-main', 'agents-servers-bindings-head',
                                                            'agents-servers-matrix-legend', 'agents-servers-matrix-card', 'agents-servers-bindings-detail-title',
                                                            'agents-servers-bindings-card'])
    out = servers_out({})
    expect(f, 'the five figures', tiles(w('Listy', 'agents-servers-stats', out)), [
        ('Tool servers', '4', '1 server bound by an agent', 'all'), ('Accepted', '3', 'of 4 servers', 'ok'),
        ('NotAccepted', '1', 'kagent could not connect or list its tools', 'bad'),
        ('Advisories', '1', 'external endpoint, no gateway route or unknown', 'warn'),
        ('Tools discovered', '28', '0 tools in use by agents', 'all')])
    expect(f, 'the figures and the cards share one colour map', static('Listy', 'agents-servers-stats')['itemTemplate']['color']['map'],
           static('Listy', 'agents-servers-cards')['itemTemplate']['color']['map'])
    expect(f, 'the state chips, Needs attention by default', [(c['label'], c['count'], c['active'], c['route']) for c in w('Listy', 'agents-servers-chips', out)['dataSource']],
           [('Needs attention', 2, True, '/agents'), ('Healthy', 2, False, '/agents?group=healthy'), ('Accepted', 3, False, '/agents?group=accepted'),
            ('NotAccepted', 1, False, '/agents?group=notaccepted'), ('Advisories', 1, False, '/agents?group=advisory'), ('All', 4, False, '/agents?group=all')])
    cards = w('Listy', 'agents-servers-cards', out)['dataSource']
    expect(f, 'Needs attention: NotAccepted, then the advisories', [(c['title'], c['tag'], c['tone']) for c in cards],
           [('cujbench-evidence', 'NotAccepted', 'bad'), ('flux-schema', 'external endpoint', 'warn')])
    expect(f, 'a card says what it serves and who binds it', cards[0]['foot'], '13 tools discovered · no agent binds it')
    expect(f, 'the main row: 16/8', [(i['resourceRefId'], i['size'], i['xs']) for i in w('Row', 'agents-servers-main', out)['items']],
           [('agents-servers-list-card', 16, 24), ('agents-servers-side', 8, 24)])
    card = w('Card', 'agents-servers-list-card', out)
    expect(f, 'the servers card', ([i['resourceRefId'] for i in card['items']], card['extraRefId']),
           (['agents-servers-chips', 'agents-servers-cards'], 'agents-servers-view-chips'))
    ex = {'view': 'table', 'group': 'healthy'}
    out = servers_out(ex)
    expect(f, '?view=table: the full table of the picked state', (items('Card', 'agents-servers-list-card', out, ex),
           [{c['valueKey']: c.get('stringValue') for c in r}['server'] for r in w('Table', 'agents-servers-list', out, ex)['dataSource']]),
           (['agents-servers-chips', 'agents-servers-list'], ['clickhouse', 'docs']))
    expect(f, 'the view chips keep the group', [c['route'] for c in out['viewChips']], ['/agents?group=healthy', '/agents?view=table&group=healthy'])
    out = servers_out({})
    expect(f, 'the charts', items('Flex', 'agents-servers-side', out), ['agents-servers-by-state-card', 'agents-servers-tools-card', 'agents-servers-consumers-card'])
    expect(f, 'by state', out['byState'], [{'state': 'Healthy', 'count': 2}, {'state': 'Advisory', 'count': 1}, {'state': 'NotAccepted', 'count': 1}])
    expect(f, 'tools per server, most first', [(t['name'], t['label'], t['percent']) for t in w('Listy', 'agents-servers-tools', out)['dataSource']],
           [('cujbench-evidence', '13', 100), ('clickhouse', '10', 77), ('docs', '3', 23), ('flux-schema', '2', 15)])
    w('Listy', 'agents-servers-consumers', out)
    w('PieChart', 'agents-servers-by-state', out)
    failed = servers_out({}, servers_error=True)
    expect(f, 'a failed server read: "—" in orange, and a warning line', ([t[3] for t in tiles(w('Listy', 'agents-servers-stats', failed))],
           items('Card', 'agents-servers-list-card', failed), w('Paragraph', 'agents-servers-empty', failed)['type']),
           (['unread'] * 5, ['agents-servers-chips', 'agents-servers-empty'], 'warning'))
    return f


def check_servers_figure_routes():
    f = []
    ex = {'projects': 'p', 'q': 'x', 'view': 'table', 'range': '7d', 'group': 'healthy'}
    out = servers_out(ex)
    r = routes(out)
    chip = {c['label']: c['route'] for c in w('Listy', 'agents-servers-chips', out, ex)['dataSource']}['Healthy']
    expect(f, 'the chips route through the same href (q, view, range kept)', chip, '/agents?projects=p&q=x&view=table&range=7d&group=healthy')
    expect(f, 'each state tile routes as a chip would, on its own group', (r['Accepted'], r['NotAccepted'], r['Advisories']),
           tuple(chip.replace('group=healthy', 'group=' + g) for g in ('accepted', 'notaccepted', 'advisory')))
    expect(f, 'the totals are not clickable', (r['Tool servers'], r['Tools discovered']), ('', ''))
    base = servers_out({})
    for title in ('Accepted', 'NotAccepted', 'Advisories'):
        q = qs(routes(base)[title])
        landed = servers_out(q)
        expect(f, f'{title}: the route lists what the tile counts', str(len(w('Listy', 'agents-servers-cards', landed, q)['dataSource'])),
               {t['title']: t['value'] for t in base['stats']}[title])
    # Every state tile has its chip: the same route (q, view, range kept), the same count, active once landed.
    chips = {c['label']: c for c in w('Listy', 'agents-servers-chips', out, ex)['dataSource']}
    for title in ('Accepted', 'NotAccepted', 'Advisories'):
        expect(f, f'{title}: the tile routes as its chip', r[title], chips[title]['route'])
        expect(f, f'{title}: the chip counts what the tile counts', str(chips[title]['count']), {t['title']: t['value'] for t in out['stats']}[title])
        expect(f, f'{title} (no filter): the chip counts what the tile counts', str({c['label']: c['count'] for c in base['serverChips']}[title]),
               {t['title']: t['value'] for t in base['stats']}[title])
        landed = servers_out(qs(r[title]))
        expect(f, f'{title}: landing from the tile, its chip is active', [c['label'] for c in landed['serverChips'] if c['active']], [title])
    expect(f, 'a zero tile is still clickable', routes(servers_out({}, servers=SERVERS[:2]))['NotAccepted'], '/agents?group=notaccepted')
    expect(f, 'a failed read is not clickable', {t['route'] for t in servers_out({}, servers_error=True)['stats']}, {''})
    expect(f, 'the figures Listy navigates to {route}', static('Listy', 'agents-servers-stats')['itemTemplate'].get('navigateTo'), '{route}')
    return f


# ---------------------------------------------------------------- the Policies tab

POLS = [{'metadata': {'name': 'agentgateway-policies-cors', 'namespace': NS},
         'spec': {'targetRefs': [{'kind': 'Gateway', 'name': 'agentgateway'}], 'traffic': {'cors': {'allowMethods': ['GET']}}}},
        {'metadata': {'name': 'agentgateway-policies-retry', 'namespace': NS},
         'spec': {'traffic': {'retry': {'attempts': 2}}},
         'status': {'ancestors': [{'conditions': [{'type': 'Accepted', 'status': 'False'}]}]}},
        {'metadata': {'name': 'agentgateway-policies-jwt', 'namespace': NS},
         'spec': {'targetRefs': [{'kind': 'Gateway', 'name': 'agentgateway'}], 'traffic': {'jwtAuthentication': {'mode': 'Strict'}}}}]


def policies_out(extras, pols=None, error=False):
    return tbi.resolve(CH, 'agents-policies', {'policies': 'ERROR' if error else {'items': POLS if pols is None else pols}, 'routes': {'items': []}}, extras)


def role_rows(out, extras):
    """{role: [policy names]} as the tab renders them: the role cards the groups Flex includes, each table's rows."""
    shown = {}
    for ref in items('Flex', 'agents-policies-groups', out, extras):
        role = ref.removeprefix('agents-policies-card-')
        rows = w('Table', f'agents-policies-table-{role}', out, extras)['dataSource']
        shown[role] = ([{c['valueKey']: c.get('stringValue') for c in r}['name'] for r in rows], w('Card', ref, out, extras)['extra'])
    return shown


def check_policies_tab():
    f = []
    out = policies_out({})
    expect(f, 'the tab: figures, state chips, then the role cards', (items('Col', 'agents-tab-policies', {}), items('Flex', 'agents-policies-tab', out)),
           (['agents-policies-tab'], ['agents-policies-stats', 'agents-policies-chips', 'agents-policies-groups']))
    expect(f, 'the four figures', tiles(w('Listy', 'agents-policies-stats', out)), [
        ('Policies', '3', 'in 3 roles', 'all'), ('Healthy', '2', 'accepted and attached by the gateway', 'ok'),
        ('Not healthy', '1', 'a condition not True', 'bad'), ('Not attached', '1', 'no targetRef or selector: enforces nothing', 'warn')])
    expect(f, 'the state chips, All by default', [(c['label'], c['count'], c['active'], c['route']) for c in w('Listy', 'agents-policies-chips', out)['dataSource']],
           [('All', 3, True, '/agents'), ('Healthy', 2, False, '/agents?group=healthy'),
            ('Not healthy', 1, False, '/agents?group=nothealthy'), ('Not attached', 1, False, '/agents?group=notattached')])
    expect(f, 'All: every role card, every policy', role_rows(out, {}),
           {'identity': (['jwt'], '1 policy'), 'resiliency': (['retry'], '1 policy'), 'cors': (['cors'], '1 policy')})
    # With q, view and range set (and another tab's facet), a state tile routes as its chip and counts what it counts.
    ex = {'projects': 'p', 'status': 'notaccepted', 'q': 'x', 'view': 'table', 'range': '7d', 'group': 'healthy'}
    out = policies_out(ex)
    tile = {t['title']: t for t in out['stats']}
    chip = {c['label']: c for c in w('Listy', 'agents-policies-chips', out, ex)['dataSource']}
    expect(f, 'the chips keep every other param', chip['Not healthy']['route'], '/agents?projects=p&status=notaccepted&q=x&view=table&range=7d&group=nothealthy')
    expect(f, 'All leaves ?group out', chip['All']['route'], '/agents?projects=p&status=notaccepted&q=x&view=table&range=7d')
    for title in ('Healthy', 'Not healthy', 'Not attached'):
        expect(f, f'{title}: the tile routes as its chip', tile[title]['route'], chip[title]['route'])
        expect(f, f'{title}: the chip counts what the tile counts', str(chip[title]['count']), tile[title]['value'])
        q = qs(tile[title]['route'])
        landed = policies_out(q)
        expect(f, f'{title}: landing from the tile, its chip is active', [c['label'] for c in landed['stateChips'] if c['active']], [title])
        expect(f, f'{title}: the role tables list what the tile counts', str(sum(len(v[0]) for v in role_rows(landed, q).values())), tile[title]['value'])
    expect(f, 'the totals are not clickable', tile['Policies']['route'], '')
    expect(f, 'the figures do not follow the pick (the whole surface)', [t['value'] for t in out['stats']], ['3', '2', '1', '1'])
    ex = {'group': 'nothealthy'}
    out = policies_out(ex)
    expect(f, 'Not healthy: only its role card, its count follows', role_rows(out, ex), {'resiliency': (['retry'], '1 policy')})
    expect(f, '... the tab shows the cards', items('Flex', 'agents-policies-tab', out, ex), ['agents-policies-stats', 'agents-policies-chips', 'agents-policies-groups'])
    ex = {'group': 'healthy'}
    expect(f, 'Healthy: two role cards', role_rows(policies_out(ex), ex), {'identity': (['jwt'], '1 policy'), 'cors': (['cors'], '1 policy')})
    expect(f, "another tab's value (attention) is All", policies_out({'group': 'attention'})['group'], 'all')
    # Nothing in the picked state: the role cards give way to a line that says so.
    calm = [p for p in POLS if p['metadata']['name'] != 'agentgateway-policies-retry']
    ex = {'group': 'nothealthy'}
    out = policies_out(ex, pols=calm)
    expect(f, 'none matches: a line instead of the cards', (items('Flex', 'agents-policies-tab', out, ex), w('Paragraph', 'agents-policies-empty', out, ex)['text'],
           w('Paragraph', 'agents-policies-empty', out, ex)['type']),
           (['agents-policies-stats', 'agents-policies-chips', 'agents-policies-empty'], 'Every policy is healthy.', 'secondary'))
    expect(f, 'a zero tile is still clickable', {t['title']: t['route'] for t in out['stats']}['Not healthy'], '/agents?group=nothealthy')
    expect(f, 'an active tile still routes to its filter (no toggle)', {t['title']: t['route'] for t in out['stats']}['Not healthy'],
           [c['route'] for c in out['stateChips'] if c['active']][0])
    # /agents/policies reads the same RESTAction with no ?group: every policy, as before.
    out = policies_out({})
    expect(f, '/agents/policies: every role card', items('Flex', 'page-agents-policies', out), ['agents-policies-page-header', 'agents-policies-groups'])
    expect(f, '/agents/policies: the groups unfiltered, rows and byRole unchanged', (out['shown'] == out['rows'], out['shownByRole'] == out['byRole'], out['total']),
           (True, True, 3))
    out = policies_out({}, error=True)
    expect(f, 'a failed read: "—" in orange, not clickable', [t[1:] + (r,) for t, r in zip(tiles(w('Listy', 'agents-policies-stats', out)), [t['route'] for t in out['stats']])],
           [('', 'could not be read', 'unread', '')] * 4)
    expect(f, 'a failed read: a warning line instead of the cards', (items('Flex', 'agents-policies-tab', out), w('Paragraph', 'agents-policies-empty', out)['type']),
           (['agents-policies-stats', 'agents-policies-chips', 'agents-policies-empty'], 'warning'))
    expect(f, 'the figures Listy navigates to {route}', static('Listy', 'agents-policies-stats')['itemTemplate'].get('navigateTo'), '{route}')
    return f


# ---------------------------------------------------------------- the Token usage tab

def series_row(t, inp=1000, out=100, cached=400):
    # ClickHouse writes UInt64 sums as strings.
    return {'t': t, 'input_total': str(inp), 'output_total': str(out), 'cached_total': str(cached)}


def check_token_usage_tab():
    f = []
    expect(f, 'the tab: the notice, the toolbar, the figures, the main row', (items('Col', 'agents-tab-usage', {}), items('Flex', 'agents-cost', {})),
           (['agents-cost'], ['agents-cost-notice', 'agents-cost-toolbar', 'agents-cost-stats', 'agents-cost-main']))
    expect(f, 'the toolbar: the range 18, the search 6', [(i['resourceRefId'], i['size'], i['xs']) for i in static('Row', 'agents-cost-toolbar')['items']],
           [('agents-cost-range', 18, 24), ('agents-cost-search-wrap', 6, 24)])
    search = static('Input', 'agents-cost-search')
    expect(f, 'the search writes ?q on Enter, at the standard size', (search['queryParam'], search['size'], search['allowClear']), ('q', 'middle', True))
    expect(f, 'the main row: the tables 16, the charts 8', [(i['resourceRefId'], i['size'], i['xs']) for i in static('Row', 'agents-cost-main')['items']],
           [('agents-cost-tables', 16, 24), ('agents-cost-side', 8, 24)])
    expect(f, 'the tables: agents, users, groups', items('Flex', 'agents-cost-tables', {}),
           ['agents-cost-agents-card', 'agents-cost-users-card', 'agents-cost-groups-card'])
    # The Overview's token card.
    hour = int(time.time() // 3600 * 3600)
    rows = [series_row(hour), series_row(hour - 3 * 3600, inp=3000, out=300, cached=0), series_row(hour - 30 * 3600)]
    out = tbi.resolve(CH, 'agents-token-timeseries', {'series': {'data': rows}}, {'projects': 'p'})
    totals = [p for p in out['totals'] if p['series'] == 'input']
    expect(f, '24h: 24 hourly points, the newest the current hour, 0 where nothing ran', (len(totals), totals[-1]['t'], totals[-1]['d'], totals[-4]['d'], totals[-2]['d'], out['unit']),
           (24, hour, 1000, 3000, 0, 'hour'))
    expect(f, 'the card: the chart, the range top right', (items('Card', 'agents-token-trend-card', out), w('Card', 'agents-token-trend-card', out)['extra']),
           (['agents-token-totals'], 'last 24 hours'))
    ex = {'range': '7d', 'group': 'all', 'q': 'x'}
    out7 = tbi.resolve(CH, 'agents-token-timeseries', {'series': {'data': []}}, ex)
    expect(f, '7d: 28 six-hour points, a day axis; nothing ran: said', (len([p for p in out7['totals'] if p['series'] == 'cached']), out7['unit'],
           w('LineChart', 'agents-token-totals', out7, ex)['xTimeUnit'], items('Card', 'agents-token-trend-card', out7, ex), out7['emptyText']),
           (28, 'day', 'day', ['agents-token-none'], 'No LLM call went through the gateway in the last 7 days.'))
    expect(f, 'a range only the Token usage tab offers: the card says 24 hours', [tbi.resolve(CH, 'agents-token-timeseries', {'series': {'data': []}}, {'range': r})['rangeLabel'] for r in ('12h', '3d', 'custom')],
           ['last 24 hours'] * 3)
    bad = tbi.resolve(CH, 'agents-token-timeseries', {'series': 'ERROR'}, {})
    expect(f, 'a failed read: the reason, as a warning', (w('Paragraph', 'agents-token-none', bad)['type'], items('Card', 'agents-token-trend-card', bad)),
           ('warning', ['agents-token-none']))
    return f


# ---------------------------------------------------------------- every widget

def check_filter_keys():
    f = []
    expect(f, 'the root names them', CH.get('Flex', 'page-agents')['metadata']['annotations'].get('krateo.io/filter-keys'), ','.join(KX))
    widgets = {}
    for d in CH.docs:
        if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/'):
            widgets.setdefault(d['metadata']['name'], []).append(d)
    seen, stack = set(), ['page-agents']
    while stack:
        n = stack.pop()
        if n in seen or n not in widgets:
            continue
        seen.add(n)
        for d in widgets[n]:
            stack += [r['name'] for r in ((d['spec'].get('resourcesRefs') or {}).get('items') or [])]
    expect(f, 'the walk reaches every tab', all(n in seen for n in ('agents-stats', 'agents-servers-stats', 'agents-policies-stats', 'agents-cost-stats',
                                                                    'agents-topology', 'agents-flow-card')), True)
    # The Token usage tab's day picker (`from`, `to`): its subtree, and the widgets shown with it.
    usage, stack = set(), ['agents-tab-usage']
    while stack:
        n = stack.pop()
        if n in usage or n not in widgets:
            continue
        usage.add(n)
        for d in widgets[n]:
            stack += [r['name'] for r in ((d['spec'].get('resourcesRefs') or {}).get('items') or [])]
    usage |= {'page-agents', 'agents-page-header', 'agents-ask-agentiko', 'agents-new', 'agents-policy-new-header', 'agents-main'}
    expect(f, 'the Token usage tab names the day picker too', CH.get('Col', 'agents-tab-usage')['metadata']['annotations'].get('krateo.io/filter-keys'),
           ','.join(KX + ['from', 'to']))
    for n in sorted(seen):
        for d in widgets[n]:
            expect(f, f'{d["kind"]} {n} declares the filter keys, in order', d['spec'].get('keyExtras'), KX + (['from', 'to'] if n in usage else []))
    return f


CHECKS = [check_facets, check_figures, check_figure_routes, check_chips_and_cards, check_layout, check_search, check_side, check_page_and_table,
          check_servers_tab, check_servers_figure_routes, check_policies_tab, check_token_usage_tab, check_filter_keys]


def main():
    global CH
    args = sys.argv[1:]
    crds_dir = args[args.index('--crds') + 1] if '--crds' in args else None
    CH = tbi.Chart(tbi.render(os.path.join(HERE, '..', 'helm', 'portal-agents')))
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
