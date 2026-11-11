#!/usr/bin/env python3
"""The agent control plane's "Ask Agentiko →" links (/agents and /agents/{namespace}/{name}), over
the chart's OWN jq.

The prompt may claim no more than the page does. The page says "every agent you can READ", never
"every agent on this cluster", because an empty table has three causes (a failed read, a caller the
userAccessFilter refilter served nothing, a cluster with no agents) — and it says an agent is
registered at the gateway only when the gateway check actually ran. So: a failed Agent read asks
what the error means and describes no fleet; another failed read is stated first; an unchecked
gateway is said as a gap, not as "all registered"; and an empty table is not "no agents".

Renders helm/portal-agents and runs the RESTActions' own filters rather than keeping copies here:
a test holding its own copy of the jq passes while the chart drifts away from it. JQ=<gojq> runs the
same checks on the engine snowplow uses.
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHART = os.path.join(REPO, 'helm', 'portal-agents')
JQ = os.environ.get('JQ', 'jq')
NS = 'krateo-system'


def render(chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', chart, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'APP_VERSION', 'SOURCE_REF'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'a', copy, '--namespace', NS],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def named(docs, kind, name):
    for d in docs:
        if d.get('kind') == kind and d.get('metadata', {}).get('name') == name:
            return d
    raise AssertionError(f'{kind}/{name} not in the rendered chart')


def jq(program, data):
    with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run([JQ, '-c', '-f', path], input=json.dumps(data),
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise AssertionError(f'jq failed: {proc.stderr.strip()}')
        return json.loads(proc.stdout)
    finally:
        os.unlink(path)


def ask(href):
    path, _, query = href.partition('?')
    return path, urllib.parse.parse_qs(query).get('ask', [''])[0]


def agent(name, conds, tools=(), description='', model='default-model-config'):
    return {'apiVersion': 'kagent.dev/v1alpha2', 'kind': 'Agent',
            'metadata': {'name': name, 'namespace': NS, 'labels': {'krateo.io/composition-id': 'x'}},
            'spec': {'description': description, 'type': 'Declarative',
                     'declarative': {'modelConfig': model, 'tools': list(tools)}},
            'status': {'conditions': conds}}


READY = [{'type': 'Ready', 'status': 'True'}, {'type': 'Accepted', 'status': 'True'}]
NOT_ACCEPTED = [{'type': 'Ready', 'status': 'True'}, {'type': 'Accepted', 'status': 'False'}]
ORCH = agent('autopilot', READY, tools=[{'type': 'Agent', 'agent': {'name': 'k8s-agent'}},
                                        {'type': 'McpServer', 'mcpServer': {'name': 'kubernetes', 'toolNames': ['get']}}],
             description='Answers questions about the platform.')
K8S = agent('k8s-agent', NOT_ACCEPTED)
MODELS = {'items': [{'metadata': {'name': 'default-model-config', 'namespace': NS},
                     'spec': {'provider': 'Anthropic', 'model': 'claude-sonnet-5-5'}}]}


def route(*hosts):
    return {'metadata': {'name': 'agents', 'namespace': NS}, 'spec': {
        'parentRefs': [{'name': 'agentgateway'}],
        'rules': [{'matches': [{'headers': [{'name': 'x-kagent-host', 'value': h}]}],
                   'backendRefs': [{'name': h.split('.')[0], 'port': 8080}]} for h in hosts]}}


def policy(*hosts):
    exprs = []
    for h in hosts:
        exprs += [f"request.headers['x-kagent-host'] == '{h}' && request.path.endsWith('/.well-known/agent-card.json')",
                  f"request.headers['x-kagent-host'] == '{h}'"]
    return {'metadata': {'name': 'p', 'namespace': NS},
            'spec': {'traffic': {'authorization': {'action': 'Allow', 'policy': {'matchExpressions': exprs}}}}}


GATEWAY = {'routes': {'items': [route('autopilot.krateo-system')]},
           'policies': {'items': [policy('autopilot.krateo-system')]}}

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f'{label}: {got!r} != {want!r}')


docs = render(CHART)
lst = named(docs, 'RESTAction', 'agents-list')['spec']['filter']
det = named(docs, 'RESTAction', 'agent-detail')['spec']['filter']

# --- /agents ------------------------------------------------------------------------------------
out = jq(lst, {'agents': {'items': [ORCH, K8S]}, 'models': MODELS, 'servers': {'items': []}, **GATEWAY})
path, prompt = ask(out['askHref'])
check('list -> self page', path, '/agents')
check('list, problems -> prompt', prompt,
      'Review my agent control plane: 2 agents, 1 orchestrator. 1 cannot be reached through the gateway '
      '(1. k8s-agent — not routed · no Allow rule) and 1 reports a problem (1. k8s-agent — NotAccepted). '
      'Explain what is wrong with each and how to fix it, starting with the one that breaks the most delegation.')

healthy = dict(GATEWAY, routes={'items': [route('autopilot.krateo-system', 'k8s-agent.krateo-system')]},
               policies={'items': [policy('autopilot.krateo-system', 'k8s-agent.krateo-system')]})
out = jq(lst, {'agents': {'items': [ORCH, agent('k8s-agent', READY)]}, 'models': MODELS, 'servers': {'items': []}, **healthy})
_, prompt = ask(out['askHref'])
check('list, nothing wrong -> prompt', prompt,
      'Review my agent control plane: 2 agents, 1 orchestrator. All 2 are registered at the gateway and report '
      'no problem. Explain how they delegate to one another, and anything that looks risky.')

# The gateway check could not run (no route visible: the silent refilter). "Registered" is NOT said.
out = jq(lst, {'agents': {'items': [ORCH]}, 'models': MODELS, 'servers': {'items': []},
               'routes': {'items': []}, 'policies': {'items': []}})
_, prompt = ask(out['askHref'])
check('list, gateway unchecked -> no "registered" claim', 'registered at the gateway' in prompt, False)
check('list, gateway unchecked -> the gap said', 'gateway registration could not be checked (no HTTPRoute is visible to you' in prompt, True)

# Empty table: not "no agents".
out = jq(lst, {'agents': {'items': []}, 'models': MODELS, 'servers': {'items': []}, **GATEWAY})
_, prompt = ask(out['askHref'])
check('list, empty -> what it can prove', prompt,
      'I can read no agents on this cluster. Explain whether that means none is installed or that my permissions '
      'do not cover them, and how to tell.')

# A FAILED AGENT READ describes no fleet.
out = jq(lst, {'agentsError': [{'code': 500, 'message': 'etcdserver: request timed out'}], 'models': MODELS,
               'servers': {'items': []}, **GATEWAY})
_, prompt = ask(out['askHref'])
check('list, failed Agent read -> asks about the error', prompt,
      'The agent control plane could not be read: Agents (etcdserver: request timed out). Explain what that error '
      'means and what an administrator has to fix.')
# Another read failing is stated FIRST, and the clean variant is never used.
out = jq(lst, {'agents': {'items': [ORCH, agent('k8s-agent', READY)]}, 'modelsError': 'forbidden',
               'servers': {'items': []}, **healthy})
_, prompt = ask(out['askHref'])
check('list, a side read failed -> stated first', prompt.startswith(
    'Some of the reads behind my agent control plane failed — ModelConfigs (forbidden); what is missing is missing for that reason.'), True)
check('list, a side read failed -> no "All 2 are registered"', 'All 2 are' in prompt, False)

many = [agent(f'a{i}', NOT_ACCEPTED) for i in range(10)]
_, prompt = ask(jq(lst, {'agents': {'items': many}, 'models': MODELS, 'servers': {'items': []}, **GATEWAY})['askHref'])
check('list -> eight at most per list', (prompt.count('8. a'), prompt.count('9. a')), (2, 0))

# --- /agents/{namespace}/{name} ----------------------------------------------------------------
base = {'name': 'autopilot', 'namespace': NS, 'agent': ORCH, 'models': MODELS, 'servers': {'items': []},
        'peers': {'items': [ORCH, K8S]}, 'deployments': [], **GATEWAY}
out = jq(det, base)
path, prompt = ask(out['askHref'])
check('detail -> self page', path, f'/agents/{NS}/autopilot')
check('detail -> prompt', prompt,
      f'Explain agent "autopilot" in namespace "{NS}": Answers questions about the platform. It runs on '
      'Anthropic · claude-sonnet-5-5, may call 1 tool server and hand work to 1 sub-agent, and its status is '
      'Ready · Accepted. Is it working end to end, and if not, what should I fix first?')
out = jq(det, dict(base, name='k8s-agent', agent=K8S))
_, prompt = ask(out['askHref'])
check('detail, unregistered -> the gateway finding, then the conditions',
      'its status is "This agent is not registered at the gateway" and NotAccepted.' in prompt, True)
_, prompt = ask(jq(det, dict(base, agent=agent('autopilot', [], description='')))['askHref'])
check('detail, no conditions -> not a healthy claim', 'its status is not reported (the Agent carries no conditions)' in prompt, True)
check('detail, no description -> no empty clause', prompt.startswith(f'Explain agent "autopilot" in namespace "{NS}". It runs on'), True)
_, prompt = ask(jq(det, dict(base, models={'items': []}, modelsError='forbidden'))['askHref'])
check('detail, ModelConfig read failed -> said', 'It runs on ModelConfig default-model-config, which could not be read,' in prompt, True)
check('detail, not found -> no link', jq(det, dict(base, agent={}))['askHref'], '')
check('detail, failed read -> no link', jq(det, dict(base, agent={}, agentError='forbidden'))['askHref'], '')

# --- the buttons and where they sit ------------------------------------------------------------
for name, ra, ke in (('agents-ask-agentiko', 'agents-list', None), ('agent-detail-ask-agentiko', 'agent-detail', ['name', 'namespace'])):
    b = named(docs, 'Button', name)
    expr = [e for e in b['spec']['widgetDataTemplate'] if e['forPath'] == 'actions'][0]['expression'].strip()[2:-1]
    check(f'{name} -> askHref', jq(expr, {'askHref': '/x?ask=y'})['navigate'][0]['path'], '/x?ask=y')
    check(f'{name} -> fallback, never a 404', jq(expr, {})['navigate'][0]['path'], '/agents')
    check(f'{name} -> reads', b['spec']['apiRef']['name'], ra)
    check(f'{name} -> keyExtras', b['spec'].get('keyExtras'), ke)
    check(f'{name} -> label, intent', (b['spec']['widgetData']['label'], b['spec']['widgetData']['intent']),
          ('Ask Agentiko →', 'agentiko'))
check('/agents header -> Add policy, Ask, New agent',
      [i['resourceRefId'] for i in named(docs, 'PageHeader', 'agents-page-header')['spec']['widgetData']['items']],
      ['agents-policy-new-header', 'agents-ask-agentiko', 'agents-new'])
check('agent header -> the button',
      [i['resourceRefId'] for i in named(docs, 'PageHeader', 'agent-detail-page-header')['spec']['widgetData']['items']],
      ['agent-detail-ask-agentiko'])

for f in failures:
    print(f'FAIL  {f}')
print(f'{"FAILED" if failures else "ok"}: agents page Ask Agentiko ({len(failures)} failure(s))')
sys.exit(1 if failures else 0)
