#!/usr/bin/env python3
"""
test-agents-cost — the Token usage tab's spend (restaction.agents-cost), over the chart's own jq.

WHAT IT GUARDS:
  - who sees spend: no Gateway `get` (a refused call, or a response the caller's RBAC filtered out)
    means no ClickHouse call at all (both iterators yield nothing) and an empty tab that says what to
    ask for;
  - spend over time with five callers or fewer, and with more (the five biggest by name, the rest as
    "others"); every bucket adds up to the Spend figure;
  - pricing the way the gateway prices a call: cached input at the cache rate, the context tier, and
    a model missing from the price list counted and named, never priced at 0;
  - spend per agent: the agents table (readable agents, the incident comparison, "Other calls") adds
    up to the Spend figure; an agent call with no gateway row is counted from the spans that END
    inside the window, so a call whose gateway row fell just outside it is not called missing;
  - every widget reading the action resolves (and, with --crds, validates against its CRD).

Usage: test-agents-cost.py [--crds DIR]. JQ=gojq runs the same on snowplow's engine.
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

NS = 'krateo-system'
HOUR = 3600
# A bucket well inside the default 24h window, on the hourly bucket the filter draws.
T = (int(time.time()) // HOUR - 2) * HOUR

CATALOG = {'providers': {'gcp.gemini': {'models': {
    'gemini-4-hydrogen': {'rates': {'input': '1.5', 'output': '7.5', 'cacheRead': '0.075'}},
    'gemini-tiered': {'rates': {'input': '2', 'output': '12', 'cacheRead': '0.2'},
                      'tiers': [{'contextOver': 200000, 'rates': {'input': '4', 'output': '18', 'cacheRead': '0.4'}}]}}}}}


def configmap(catalog=CATALOG):
    return {'metadata': {'name': 'agentgateway-policies-model-catalog', 'namespace': NS},
            'data': {'catalog.json': json.dumps(catalog)}}


def agent(name):
    return {'metadata': {'name': name, 'namespace': NS}}


def row(usr, calls=1, inp=1000, cached=0, outp=100, team='devs', principal='human', model='gemini-4-hydrogen',
        cmp=0, tier=0, t=T):
    # ClickHouse FORMAT JSON writes UInt64 as strings.
    return {'t': str(t), 'usr': usr, 'team': team, 'principal': principal, 'model': model, 'provider': 'gcp.gemini',
            'cmp': cmp, 'tier': tier, 'calls': str(calls), 'inp': str(inp), 'cached': str(cached), 'outp': str(outp), 'reas': '0'}


def agent_row(k, app, calls=0, inp=0, cached=0, outp=0, spans=0):
    return {'k': k, 'app': app, 'model': 'gemini-4-hydrogen' if k == 'm' else '', 'provider': 'gcp.gemini' if k == 'm' else '',
            'tier': 0, 'calls': str(calls), 'inp': str(inp), 'cached': str(cached), 'outp': str(outp), 'reas': '0', 'spans': str(spans)}


def responses(gw_rows, ag_rows=(), agents=('agentiko', 'k8s-agent'), access=True, catalog=CATALOG):
    r = {'gatewayAccess': {'metadata': {'name': NS}} if access is True else access,
         'agents': {'items': [agent(a) for a in agents]}, 'catalog': configmap(catalog)}
    if access is True:
        r['gw'] = {'data': list(gw_rows)}
        r['ag'] = {'data': list(ag_rows)}
    return r


def resolve(resp, extras=None):
    return tbi.resolve(CH, 'agents-cost', resp, extras or {})


def iterators(resp, extras=None):
    """What the two ClickHouse stages would be asked to run: snowplow evaluates each iterator over the
    dict the apiserver stages left, and an empty list is no call."""
    ra = CH.get('RESTAction', 'agents-cost')
    data = dict(extras or {})
    for step in ra['spec']['api']:
        if step['name'] in resp and 'dependsOn' not in step:
            raw = resp[step['name']]
            data[step['name']] = tbi.jq(step['filter'], {step['name']: raw}) if step.get('filter') else raw
    return {s['name']: tbi.jq(s['dependsOn']['iterator'], data) for s in ra['spec']['api'] if 'dependsOn' in s}


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def close(f, what, got, want, tol=1e-9):
    if got is None or abs(got - want) > tol:
        f.append(f'{what}: got {got!r}, want {want!r}')


def series_names(out):
    return sorted({p['series'] for p in out['series']})


def check_no_access():
    f = []
    for label, access in (('a refused call', 'ERROR'), ('a response RBAC filtered out', None), ('an empty response', {})):
        resp = responses([row('admin')], access=access)
        expect(f, f'{label}: no ClickHouse call', iterators(resp), {'gw': [], 'ag': []})
        out = resolve(resp)
        expect(f, f'{label}: access', out['access'], 'none')
        expect(f, f'{label}: no figure', {s['caption'] for s in out['stats']}, {'not shared with you'})
        expect(f, f'{label}: no rows', [out['series'], out['agents'], out['users'], out['groups']], [[], [], [], []])
        if access != 'ERROR':
            expect(f, f'{label}: says what to ask for', 'Ask an administrator for read access to Gateways' in out['notice'], True)
    it = iterators(responses([row('admin')]))
    expect(f, 'with access: one gateway query', len(it['gw']), 1)
    expect(f, 'with access: one agent query, for the readable agents only', it['ag'][0]['apps'] if it['ag'] else None,
           "'krateo_system__NS__agentiko', 'krateo_system__NS__k8s_agent'")
    return f


def check_spend_over_time():
    f = []
    five = [row(u, inp=1000 * (i + 1)) for i, u in enumerate(['a', 'b', 'c', 'd', 'e'])]
    out = resolve(responses(five))
    expect(f, 'five callers: each by name, no "others"', series_names(out), ['a', 'b', 'c', 'd', 'e'])
    close(f, 'five callers: the bars add up to Spend', sum(p['v'] for p in out['series']), out['totals']['usd'], 1e-3)
    # Seven callers, the alert provider's comparison and a call with no identity among them: the two
    # smallest go to "others". This used to fail the whole action (gojq: expected an object but got: array).
    seven = five + [row('krateo-alert-provider', inp=500, principal='service', cmp=1), row('', inp=200, team='')]
    out = resolve(responses(seven))
    expect(f, 'seven callers: the five biggest and "others"', series_names(out), ['a', 'b', 'c', 'd', 'e', 'others'])
    others = sum(p['v'] for p in out['series'] if p['series'] == 'others')
    close(f, 'seven callers: "others" is the two smallest', others,
          round((500 + 200) * 1.5 / 1e6 + 2 * 100 * 7.5 / 1e6, 4), 1e-4)
    close(f, 'seven callers: the bars add up to Spend', sum(p['v'] for p in out['series']), out['totals']['usd'], 1e-3)
    expect(f, 'seven callers: users table', sorted(u['name'] for u in out['users']),
           ['(no identity)', 'a', 'b', 'c', 'd', 'e', 'krateo-alert-provider'])
    return f


def check_pricing():
    f = []
    # The gateway's own worked example (agentgateway-policies#101): gemini-4-hydrogen, 20,922 input of
    # which 17,831 cached, 221 output -> $0.007631325.
    out = resolve(responses([row('admin', inp=20922, cached=17831, outp=221)]))
    close(f, 'cached input at the cache rate', out['totals']['usd'], 0.007631325)
    expect(f, 'Spend figure', out['stats'][0]['value'], '$0.008')
    # Above 200k input the tier's rates apply.
    out = resolve(responses([row('admin', model='gemini-tiered', inp=250000, outp=1000, tier=200000)]))
    close(f, 'the context tier', out['totals']['usd'], (250000 * 4 + 1000 * 18) / 1e6)
    # A model missing from the price list is counted and named, never priced at 0.
    out = resolve(responses([row('admin'), row('bob', model='mystery-model', calls=3)]))
    expect(f, 'unpriced: named', 'mystery-model is not on the gateway\'s price list, so its 3 calls are not counted' in out['notice'], True)
    expect(f, 'unpriced: a figure says so', [s['value'] for s in out['stats'] if s['title'] == 'Not priced'], ['3 calls'])
    expect(f, 'unpriced: a caller with only unpriced calls shows no spend', [u['spend'] for u in out['users'] if u['name'] == 'bob'], ['—'])
    # No price list at all: calls are counted, nothing is priced.
    out = resolve(responses([row('admin')], catalog={}))
    expect(f, 'no price list: Spend', out['stats'][0]['value'], '—')
    return f


def check_agents():
    f = []
    agentiko = 'krateo_system__NS__agentiko'
    k8s = 'krateo_system__NS__k8s_agent'
    gw = [row('admin', calls=6, inp=60000, cached=30000, outp=600),
          row('krateo-alert-provider', calls=1, inp=2000, outp=50, principal='service', cmp=1)]
    ag = [agent_row('m', agentiko, calls=2, inp=20000, cached=10000, outp=200, spans=2),
          agent_row('m', k8s, calls=1, inp=10000, cached=5000, outp=100, spans=1),
          # A k8s-agent call whose span ends inside the window and whose gateway row lands just
          # outside it: matched, so not missing, and not in any figure of this window.
          agent_row('m', k8s, calls=0, spans=1),
          # An agent the caller cannot read: the SQL never asks for it; the filter drops it anyway.
          agent_row('m', 'other_ns__NS__secret_agent', calls=1, inp=10000, outp=100, spans=1),
          agent_row('s', agentiko, calls=3), agent_row('s', k8s, calls=2)]
    out = resolve(responses(gw, ag))
    names = [a['name'] for a in out['agents']]
    expect(f, 'agents table rows', sorted(names),
           sorted(['agentiko', 'k8s-agent', 'Incident comparison (alert provider, no agent)', 'Other calls']))
    expect(f, 'agents table calls', {a['name']: a['calls'] for a in out['agents']},
           {'agentiko': '2', 'k8s-agent': '1', 'Incident comparison (alert provider, no agent)': '1', 'Other calls': '3'})
    close(f, 'the agents table adds up to Spend', sum(float(a['spend']) for a in out['agents']), out['totals']['usd'], 1e-3)
    expect(f, 'an agent row links to the agent', [(a['routeNs'], a['routeName']) for a in out['agents'] if a['name'] == 'agentiko'], [(NS, 'agentiko')])
    expect(f, 'unmatched: only the agentiko span with no gateway row', out['totals']['unmatchedAgentCalls'], 1)
    expect(f, 'unmatched: said', out['notice'].startswith("1 model call the agents made in this range never reached the gateway's log, so it is missing"), True)
    return f


def check_widgets():
    f = []
    seven = [row(u, inp=1000 * (i + 1)) for i, u in enumerate(['a', 'b', 'c', 'd', 'e', 'f', 'g'])]
    out = resolve(responses(seven, [agent_row('m', 'krateo_system__NS__agentiko', calls=2, inp=2000, outp=200, spans=2),
                                    agent_row('s', 'krateo_system__NS__agentiko', calls=2)]))
    for kind, name in (('Listy', 'agents-cost-stats'), ('Listy', 'agents-cost-range-chips'), ('Paragraph', 'agents-cost-notice'),
                       ('BarChart', 'agents-cost-trend'), ('PieChart', 'agents-cost-models'), ('Table', 'agents-cost-agents'),
                       ('Table', 'agents-cost-users'), ('Table', 'agents-cost-groups')):
        tbi.resolved_widget(CH, kind, name, out, {}, f'{kind} {name}')
    return f


CHECKS = [check_no_access, check_spend_over_time, check_pricing, check_agents, check_widgets]


def main():
    global CH
    args = sys.argv[1:]
    crds_dir = args[args.index('--crds') + 1] if '--crds' in args else None
    CH = tbi.Chart(tbi.render(os.path.join(HERE, '..', 'helm', 'portal-agents')))
    failed = 0
    for check in CHECKS:
        try:
            problems = check()
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError, ValueError) as exc:
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
