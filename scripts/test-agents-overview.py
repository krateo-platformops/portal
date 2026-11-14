#!/usr/bin/env python3
"""
test-agents-overview — /agents' Overview tab as a root-pages overview (slice 3a), over the chart's own jq.

WHAT IT GUARDS (build plan §3.4, mockup screen 09, decisions of 2026-11-13 and 2026-11-14):
  - the agents and the tool servers are one base set; Kind (agents, tool servers), Status
    (Registered/NotRegistered for an agent, by the gateway check; Accepted/NotAccepted for a tool
    server) and Owner count under every OTHER filter; a facet with one value is left out of the bar;
  - an agent gets no Status while the gateway could not be checked: nobody is called registered,
    or unreachable, on a check that did not run (the strip says "not checked");
  - the strip, four tiles (the Model tile left out): Agents, Gateway gaps, Tool servers NotAccepted,
    Tools discovered; red only above 0;
  - the groups: Needs attention (red: NotRegistered, an agent not Ready, a tool server not Accepted),
    Orchestrators (most delegates first), Specialists (folded); a tool server's card is inert (it has
    no page); six cards a group, then "See all N →";
  - the page: no stats row above the tabs, the first tab is "Overview", and ?view=table swaps in the
    12-column agents table, listing the agents the filters match; Ask Agentiko keeps every filter and
    names the view;
  - every widget reachable from the page declares the filter keys, in every tab (also guarded by
    lint-keyextras).

Usage: test-agents-overview.py [--crds DIR]. JQ=gojq runs the same on snowplow's engine.
"""
import importlib.util
import os
import sys
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

NS = 'krateo-system'
KX = ['projects', 'kind', 'status', 'owner', 'q', 'view']


def agent(name, delegates=0, ready=True, owner='agentiko', description=None):
    tools = [{'type': 'Agent', 'agent': {'name': f'sub-{i}'}} for i in range(delegates)]
    tools.append({'type': 'McpServer', 'mcpServer': {'name': 'docs', 'toolNames': ['search', 'read']}})
    return {'metadata': {'name': name, 'namespace': NS, 'labels': {'krateo.io/composition-name': owner, 'krateo.io/composition-id': 'x'}},
            'spec': {'description': description or f'{name} does one job', 'declarative': {'modelConfig': 'default-model-config', 'tools': tools}},
            'status': {'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}, {'type': 'Accepted', 'status': 'True'}]}}


def server(name, accepted=True, tools=3, owner='kagent'):
    return {'metadata': {'name': name, 'namespace': NS, 'labels': {'app.kubernetes.io/managed-by': owner}},
            'spec': {'description': f'{name} tools'},
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


AGENTS = [agent('agentiko', delegates=9), agent('frontend-agent', delegates=1), agent('k8s-agent'), agent('helm-agent'),
          agent('nightly-review-agent', owner='nightly-review'), agent('broken-agent', ready=False)]
REGISTERED = [f'{a["metadata"]["name"]}.{NS}' for a in AGENTS if a['metadata']['name'] != 'nightly-review-agent']
SERVERS = [server('docs'), server('clickhouse', tools=10), server('cujbench-evidence', accepted=False, tools=13, owner='—x')]


def resolve(extras, routes=True, agents=None, servers=None):
    r = {'agents': {'items': AGENTS if agents is None else agents}, 'models': {'items': [{'metadata': {'name': 'default-model-config'}, 'spec': {'model': 'gemini'}}]},
         'servers': {'items': SERVERS if servers is None else servers},
         'routes': {'items': [route(*REGISTERED)] if routes else []}, 'policies': {'items': [policy(*REGISTERED)]}}
    return tbi.resolve(CH, 'agents-list', r, extras)


def w(kind, name, out, extras=None):
    return tbi.resolved_widget(CH, kind, name, out, extras or {}, f'{kind} {name}')['spec']['widgetData']


def facet(out, key):
    return {v['value']: v['count'] for v in [f for f in out['facets'] if f['key'] == key][0]['values']}


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


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
    out = resolve({'q': 'HELM'})
    expect(f, 'search: name and description, any case', [a['name'] for a in out['agentsInView']], ['helm-agent'])
    out = resolve({}, routes=False)
    expect(f, 'gateway not checked: no agent has a Status, only the servers', facet(out, 'status'), {'accepted': 2, 'notaccepted': 1})
    one = [agent('solo'), agent('duo')]
    out = resolve({}, agents=one, servers=[])
    expect(f, 'one kind, one owner: Kind and Owner left out', [i['resourceRefId'] for i in w('Flex', 'agents-filter-bar', out)['items']],
           ['agents-search'])
    return f


def check_strip():
    f = []
    rows = [(t['title'], t['value'], t['caption'], t['tone']) for t in w('Listy', 'agents-strip', resolve({}))['dataSource']]
    expect(f, 'the strip: four tiles, no Model', rows, [('Agents', '6', '', ''), ('Gateway gaps', '1', 'not routed or not allowed', 'bad'),
                                                       ('Tool servers NotAccepted', '1', 'of 3', 'bad'), ('Tools discovered', '26', 'on 2 servers', '')])
    rows = [(t['title'], t['value'], t['caption'], t['tone']) for t in w('Listy', 'agents-strip', resolve({}, routes=False))['dataSource']]
    expect(f, 'gateway not checked: "—", never 0 gaps', rows[1], ('Gateway gaps', '', 'not checked', 'warn'))
    calm = w('Paragraph', 'agents-calm', resolve({}, routes=False))
    expect(f, 'gateway not checked: said, as a warning', (calm['text'].startswith('Gateway registration could not be checked ('), calm['type']), (True, 'warning'))
    return f


def check_groups():
    f = []
    g = w('Collapse', 'agents-groups', resolve({}))
    expect(f, 'groups', [(p['key'], p['count'], p.get('status')) for p in g['items']],
           [('attention', 3, 'error'), ('orchestrators', 2, None), ('specialists', 2, None)])
    expect(f, 'Needs attention and Orchestrators open, Specialists folded', g['defaultActiveKey'], ['attention', 'orchestrators'])
    att = g['items'][0]['dataSource']
    expect(f, 'Needs attention: the agents (by name), then the tool servers', [(c['title'], c['tag'], c['href']) for c in att],
           [('broken-agent', 'NotReady', f'/agents/{NS}/broken-agent'), ('nightly-review-agent', 'NotRegistered', f'/agents/{NS}/nightly-review-agent'),
            ('cujbench-evidence', 'NotAccepted', '')])
    expect(f, 'a tool server card is inert: no page to open', (att[2]['href'], att[2]['cta']), ('', ''))
    expect(f, 'the gap is named', att[1]['foot'], 'Gateway: not routed · no Allow rule · owner nightly-review')
    orch = g['items'][1]['dataSource']
    expect(f, 'Orchestrators: most delegates first, with delegates, tools and servers', [(c['title'], c['foot']) for c in orch],
           [('agentiko', '9 delegates · 2 tools on 1 server'), ('frontend-agent', '1 delegate · 2 tools on 1 server')])
    many = AGENTS + [agent(f'cold-{i}', ready=False) for i in range(6)]
    att = w('Collapse', 'agents-groups', resolve({'projects': 'p'}, agents=many), {'projects': 'p'})['items'][0]
    expect(f, 'past six: See all N to the table', (len(att['dataSource']), att['footer'], att['footerNavigateTo']), (6, 'See all 9 →', '/agents?projects=p&view=table'))
    return f


def check_page_and_table():
    f = []
    page = CH.get('Flex', 'page-agents')['spec']['widgetData']
    expect(f, 'no stats row above the tabs', [i['resourceRefId'] for i in page['items']], ['agents-page-header', 'agents-main'])
    expect(f, 'the first tab is Overview', CH.get('Tabs', 'agents-main')['spec']['widgetData']['items'][0]['label'], 'Overview')
    gone = [d['metadata']['name'] for d in CH.docs if d['metadata'].get('name', '') in (
        'agents-stats', 'agents-stat-card-agents', 'agents-stat-card-servers', 'agents-stat-card-gateway',
        'agents-stat-agents', 'agents-stat-servers', 'agents-stat-gateway')]
    expect(f, 'the stats row and its cards are gone', gone, [])
    ex = {'status': 'notregistered,notaccepted', 'view': 'table'}
    out = resolve(ex)
    expect(f, '?view=table: the 12-column table in place of the overview', [i['resourceRefId'] for i in w('Flex', 'agents-overview', out, ex)['items']],
           ['agents-filter-bar', 'agents-active-filters', 'agents-view-toggle', 'agents-list-card'])
    names = [{c['valueKey']: c.get('stringValue') for c in r}['name'] for r in w('Table', 'agents-list', out, ex)['dataSource']]
    expect(f, '?view=table: the agents the filters match', names, ['nightly-review-agent'])
    btn = w('Button', 'agents-view-toggle', out, ex)
    expect(f, 'back keeps every filter', btn['actions']['navigate'][0]['path'], '/agents?status=notregistered%2Cnotaccepted')
    path = w('Button', 'agents-ask-agentiko', out, ex)['actions']['navigate'][0]['path']
    params = parse_qs(path.partition('?')[2])
    expect(f, 'Ask keeps every filter', [k for k in params], ['status', 'view', 'ask'])
    expect(f, 'Ask names the view and what is wrong in it', params['ask'][0].startswith(
        'Review the part of my agent control plane on screen (Status: NotAccepted, NotRegistered): 1 agent and 1 tool server. '
        '1. nightly-review-agent cannot be reached through the gateway (not routed · no Allow rule) 2. cujbench-evidence is not Accepted (ReconcileFailed).'), True)
    expect(f, 'unfiltered Ask is unchanged', parse_qs(resolve({})['askHref'].partition('?')[2])['ask'][0].startswith('Review my agent control plane: 6 agents, 2 orchestrators.'), True)
    return f


def check_filter_keys():
    f = []
    expect(f, 'the root names them', CH.get('Flex', 'page-agents')['metadata']['annotations'].get('krateo.io/filter-keys'), ','.join(KX))
    for d in CH.docs:
        n = d.get('metadata', {}).get('name', '')
        if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/') and n in (
                'page-agents', 'agents-main', 'agents-tab-inventory', 'agents-overview', 'agents-filter-bar', 'agents-kind', 'agents-status',
                'agents-owner', 'agents-search', 'agents-active-filters', 'agents-strip', 'agents-calm', 'agents-groups', 'agents-view-toggle',
                'agents-list-card', 'agents-list', 'agents-ask-agentiko', 'agents-topology', 'agents-policies-card-cors'):
            expect(f, f'{d["kind"]} {n} declares every filter key', [k for k in KX if k not in (d['spec'].get('keyExtras') or [])], [])
    return f


CHECKS = [check_facets, check_strip, check_groups, check_page_and_table, check_filter_keys]


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
