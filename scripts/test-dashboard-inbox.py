#!/usr/bin/env python3
"""The Dashboard "needs you" inbox (root-pages UX, build plan §3.1), over the chart's OWN jq.

WHAT IT GUARDS.
  - A source is in the inbox only when the caller may open its page (the gate stages). A source
    whose page is not theirs is ABSENT everywhere: no strip tile, no card, no facet value, no
    "fine" clause, nothing in the Agentiko prompt. A userAccessFilter read returns [] to a caller
    without the grant, so without the gate "nothing waiting" would be a claim nobody checked.
  - A gated-in source whose read FAILS (refused, not installed, broken) is never quiet: its tile is
    a muted "—" captioned "could not be read", it gets a "… could not be read" card carrying the
    error, it adds nothing to the calm line, and the prompt asks about it first.
  - An agent gateway that could not be checked (no route or no Allow policy visible) is said, as a
    tile and a card, never as "all registered" (portal.agentsGateway).
  - Not Ready leaves out Ready=Unknown/ZeroEvidence; a paused composition is paused, not not Ready.
    (This carries over test-dashboard-assessable.py's rule, which the removed stat cards held.)
  - ?source= drives the strip, the cards, the calm line and the prompt; the facet counts stay over
    the other filters; the facet is hidden with fewer than two sources (decision Q2); the chips
    remove themselves and keep ?projects.
  - The cap: a card lists at most three names, then "and N more"; Recent changes shows five and
    "See all N →" to /audit on the same 7 days when the week holds more.
  - All quiet: with nothing waiting there is no "Needs you", and the calm line opens "All quiet:".
  - The copies other charts own are byte-identical: _agents-gateway.tpl (portal-agents) and
    _audit.tpl (audit-page).
  - Recent changes asks ClickHouse for the latest five changes of 7 days, narrowed to the caller's
    namespaces (none for a cluster-wide reader), as snowplow's OWN path renderer dials it
    (snowplow-path.py: needs git and go), decoded once.
  - With --crds DIR, every widget, as authored and as resolved in every case, validates against the
    frontend CRDs, closed the way the apiserver's strict field validation is.

HOW. The chart is rendered with helm. Raw objects go through each stage's OWN filter (as snowplow
runs it), then the RESTAction's filter, then each widget's widgetDataTemplate, and Listy/Collapse
rows are resolved the way the frontend's itemTemplate.ts does, so the checks read what a person
sees. JQ=<gojq> runs the same on snowplow's engine. Each case is run on its own, so the control on
main's templates reports every case it breaks rather than stopping at the first.

Usage: test-dashboard-inbox.py [--crds DIR]
"""
import argparse
import copy
import glob
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import urllib.parse

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
JQ = os.environ.get('JQ', 'jq')
NS = 'krateo-system'
FIXTURE = os.path.join(HERE, 'fixtures', 'dashboard-compositions-057.json')
SHARED = [('_agents-gateway.tpl', 'portal-agents'), ('_audit.tpl', 'audit-page')]
GATES = ['Incidents', 'Alerts', 'Agents', 'Reviews', 'Compositions']
TODAY = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%d')

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f'{label}: {got!r} != {want!r}')


def run(label, fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - reported, counted, and the run goes on
        failures.append(f'{label}: {type(exc).__name__}: {str(exc).strip()[:400]}')


def render(chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy_dir = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', os.path.join(REPO, 'helm', chart), copy_dir], check=True)
        meta = os.path.join(copy_dir, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'd', copy_dir, '--namespace', NS],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template {chart} failed:\n{proc.stderr.strip()}')
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
            raise AssertionError(f'jq failed: {proc.stderr.strip()[:300]}')
        return json.loads(proc.stdout)
    finally:
        os.unlink(path)


def query(text):
    """plumbing jqutil.MaybeQuery: the jq between `${` and its matching `}`, or None for a literal."""
    start = text.find('${')
    if start < 0:
        return None
    depth, i = 1, start + 2
    while i < len(text):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[start + 2:i].strip()
        i += 1
    return None


# --- the frontend's row resolution (ui/src/widgets/List/itemTemplate.ts) ---------------------------
def resolve_path(item, path):
    for key in path.split('.'):
        item = item.get(key) if isinstance(item, dict) else None
    return item


def interpolate(template, item):
    if not template:
        return ''

    def one(match):
        for path in match.group(1).split('|'):
            value = resolve_path(item, path.strip())
            if value is not None and value != '':
                if isinstance(value, bool):
                    return 'true' if value else 'false'
                return value if isinstance(value, str) else json.dumps(value)
        return ''
    return re.sub(r'\{([^}]+)\}', one, template)


def accent(spec, item):
    """The statistic/card colour: resolved only through the map (no fallback), as resolveAccent does."""
    if not spec:
        return None
    got = interpolate(spec.get('value'), item)
    return (spec.get('map') or {}).get(got, spec.get('default'))


def rows(template, data_source):
    out = []
    for item in data_source:
        row = {k: interpolate(template.get(k), item) for k in
               ('primaryText', 'secondaryText', 'subPrimaryText', 'subSecondaryText', 'description', 'cardCta', 'navigateTo')}
        row['color'] = accent(template.get('color'), item)
        row['removable'] = item.get('removable') is True
        out.append(row)
    return out


# --- fixtures: raw objects as the apiserver returns them --------------------------------------------
def incident(name, state, alert, at):
    return {'metadata': {'name': name, 'labels': {'observability.krateo.io/alert': alert}},
            'spec': {'triggeredAt': at}, 'status': {'state': state, 'rootCause': {'statement': 'x' * 400}}}


def alert(name, state, display=''):
    return {'metadata': {'name': name}, 'spec': {'displayName': display}, 'status': {'state': state}}


def proposal(name, phase=None, created='2026-11-01T10:00:00Z', decision=None):
    p = {'metadata': {'name': name, 'namespace': NS, 'creationTimestamp': created}, 'spec': {'title': name}, 'status': {}}
    if phase:
        p['status']['phase'] = phase
    if decision:
        p['spec']['decision'] = {'phase': decision}
    return p


def agent(name):
    return {'metadata': {'name': name, 'namespace': NS}, 'spec': {},
            'status': {'conditions': [{'type': 'Ready', 'status': 'True'}, {'type': 'Accepted', 'status': 'True'}]}}


def server(name, accepted='True', reason='Accepted'):
    return {'metadata': {'name': name, 'namespace': NS},
            'status': {'conditions': [{'type': 'Accepted', 'status': accepted, 'reason': reason}]}}


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


def definition(name, ready):
    return {'metadata': {'name': name, 'namespace': NS}, 'status': {'conditions': [{'type': 'Ready', 'status': ready}]}}


def unknown(name, reason, status='Unknown'):
    return {'apiVersion': 'composition.krateo.io/v0-1-0', 'kind': 'Synthetic',
            'metadata': {'name': name, 'namespace': NS, 'uid': f'uid-{name}', 'creationTimestamp': '2026-11-01T00:00:00Z'},
            'status': {'conditions': [{'type': 'Ready', 'status': status, 'reason': reason, 'message': ''},
                                      {'type': 'Synced', 'status': 'True', 'reason': 'ReconcileSuccess'}]}}


with open(FIXTURE, encoding='utf-8') as fh:
    COMPS057 = json.load(fh)

AGENTS = [agent('autopilot'), agent('sre-agent'), agent('nightly-review-agent')]
BASE_RAW = {
    'incidents': {'items': [incident('i1', 'Open', 'probe', '2026-10-02T21:00:09Z'), incident('i2', 'Open', 'probe', '2026-11-01T00:00:00Z'),
                            incident('i3', 'Open', 'probe', '2026-11-02T00:00:00Z'), incident('i4', 'Open', 'latency', '2026-11-03T00:00:00Z'),
                            incident('i5', 'Open', 'latency', '2026-11-04T00:00:00Z'), incident('i6', 'Resolved', 'probe', '2026-09-01T00:00:00Z'),
                            incident('i7', 'Analyzing', 'disk', '2026-11-05T00:00:00Z')]},
    'alerts': {'items': [alert('probe', 'ALERT', 'Probe failing'), alert('latency', 'OK'), alert('disk', 'OK')]},
    'proposals': {'items': [proposal('p1'), proposal('p2', 'Proposed'), proposal('p3', 'Proposed', created=TODAY + 'T01:00:00Z'),
                            proposal('p4', 'PrOpen'), proposal('p5', 'Proposed'), proposal('p6', 'Merged'),
                            proposal('p7', 'Superseded'), proposal('p8', 'Refused'), proposal('p9', 'Proposed', decision='Rejected')]},
    'agents': {'items': AGENTS},
    'servers': {'items': [server('docs'), server('cujbench-evidence', 'False', 'ReconcileFailed')]},
    'routes': {'items': [route(f'autopilot.{NS}', f'sre-agent.{NS}')]},
    'policies': {'items': [policy(f'autopilot.{NS}', f'sre-agent.{NS}')]},
    'crds': {'items': []},
    'definitions': {'items': [definition('a', 'True'), definition('b', 'True'), definition('c', 'False')]},
    'allCompositions': {'items': COMPS057},
}
ERROR_KEYS = {'incidents': ['incidentsError'], 'alerts': ['alertsError'], 'agents': ['agentsError'],
              'reviews': ['proposalsError'], 'compositions': ['allCompositionsError']}
STAGE_OF = {'incidents': 'incidents', 'alerts': 'alerts', 'agents': 'agents', 'reviews': 'proposals',
            'compositions': 'allCompositions'}

# --- the chart ----------------------------------------------------------------------------------------
DASH = []
RA = {}
STAGES = {}


def load():
    DASH[:] = render('dashboard-page')
    RA.update(named(DASH, 'RESTAction', 'dashboard-inbox')['spec'])
    STAGES.update({s['name']: s for s in RA['api']})


def inbox(extras=None, gates=None, raw=None, errors=None):
    """One resolve: gates as the gate stages' filters leave them, each stage's own filter over raw."""
    raw = dict(BASE_RAW, **(raw or {}))
    d = dict(extras or {})
    for g in GATES:
        d['gate' + g] = (gates or {}).get(g, True)
        if (gates or {}).get(g, True) is not True:
            d['gate' + g + 'Error'] = [{'message': f'flexes.widgets.templates.krateo.io "page-{g.lower()}" is forbidden'}]
    for name, st in STAGES.items():
        if name.startswith('gate') or name not in raw:
            continue
        if raw[name] is None:
            continue
        d[name] = jq(st['filter'], {name: raw[name]}) if st.get('filter') else raw[name]
    for key, value in (errors or {}).items():
        d[key] = value
    return jq(RA['filter'], d)


def resolved(name, kind, out):
    doc = named(DASH, kind, name)
    data = copy.deepcopy(doc['spec']['widgetData'])
    for entry in doc['spec'].get('widgetDataTemplate') or []:
        program = query(entry['expression'])
        data[entry['forPath']] = jq(program, out) if program else entry['expression']
    return data


def page(out):
    """What /dashboard shows for one dashboard-inbox output."""
    body = [i['resourceRefId'] for i in resolved('dashboard-inbox-body', 'Flex', out)['items']]
    strip_w = resolved('dashboard-strip', 'Listy', out)
    strip = [(r['primaryText'], r['secondaryText'], r['subPrimaryText'], r['color'])
             for r in rows(strip_w['itemTemplate'], strip_w['dataSource'])]
    needs = resolved('dashboard-needs-you', 'Collapse', out)
    panels = needs['items']
    cards = rows(needs['itemTemplate'], panels[0]['dataSource']) if panels else []
    chips_w = resolved('dashboard-active-filters', 'Listy', out)
    ask = resolved('dashboard-ask-agentiko', 'Button', out)['actions']['navigate'][0]['path']
    href, _, q = ask.partition('?')
    params = dict(urllib.parse.parse_qsl(q))
    return {
        'body': body,
        'strip': strip,
        'panel': {k: panels[0].get(k) for k in ('key', 'label', 'count', 'status')} if panels else None,
        'cards': cards,
        'titles': [c['primaryText'] for c in cards],
        'quiet': resolved('dashboard-quiet', 'Paragraph', out)['text'],
        'chips': [(r['primaryText'], r['navigateTo'], r['removable']) for r in rows(chips_w['itemTemplate'], chips_w['dataSource'])],
        'options': [(o['label'], o['value'], o.get('count')) for o in resolved('dashboard-source', 'Select', out)['options']],
        'ask': (href, params.get('ask', ''), {k: v for k, v in params.items() if k != 'ask'}),
    }


CASES = {}


def case(label):
    def wrap(fn):
        CASES[label] = fn
        return fn
    return wrap


# --- 0. the shared copies ---------------------------------------------------------------------------
@case('shared copies are byte-identical')
def _():
    for tpl, owner in SHARED:
        mine = os.path.join(REPO, 'helm', 'dashboard-page', 'templates', tpl)
        theirs = os.path.join(REPO, 'helm', owner, 'templates', tpl)
        check(f'dashboard-page carries {tpl}', os.path.exists(mine), True)
        if os.path.exists(mine) and os.path.exists(theirs):
            check(f'{tpl} is byte-for-byte {owner}\'s', open(mine, 'rb').read() == open(theirs, 'rb').read(), True)
    agents_list = named(render('portal-agents'), 'RESTAction', 'agents-list')['spec']['filter']
    check('portal-agents agents-list includes the same gateway defs', 'def agw_gap($host; $hosts; $exprs; $known):' in agents_list, True)


# --- 1. every source present ------------------------------------------------------------------------
@case('every source present')
def _():
    out = inbox()
    got = page(out)
    check('present: the body', got['body'], ['dashboard-filter-bar', 'dashboard-strip', 'dashboard-needs-you', 'dashboard-quiet'])
    check('present: the strip, in the mockup order, red only for a fault', got['strip'], [
        ('Open incidents', '5', '2 alerts', 'red'),
        ('Alerts firing', '1', 'of 3', 'red'),
        ('Compositions not Ready', '0', 'of 82', None),
        ('Agent gateway gaps', '1', 'of 3 agents', 'red'),
        ('Waiting for a decision', '5', 'review proposals', None)])
    check('present: one Needs you panel, red, counting its cards', got['panel'],
          {'key': 'needs-you', 'label': 'Needs you', 'count': 5, 'status': 'error'})
    check('present: faults first, then work, in source order', got['titles'], [
        '5 open incidents', '1 alert firing', '1 agent not reachable through the gateway',
        '5 proposals waiting for a decision', '1 composition paused'])
    inc = got['cards'][0]
    check('present: the incidents card', (inc['secondaryText'], inc['subPrimaryText'], inc['description'], inc['subSecondaryText'], inc['cardCta'], inc['navigateTo'], inc['color']),
          ('Open', 'from 2 alerts · oldest opened 2026-10-02', '3 of them come from one alert, probe.', 'Incidents', 'Triage', '/incidents?status=open', 'red'))
    agt = got['cards'][2]
    check('present: the agents card names the gap and the tool servers', (agt['subPrimaryText'], agt['description'], agt['navigateTo']),
          ('nightly-review-agent', 'Gateway: not routed · no Allow rule. Also 1 tool server NotAccepted.', '/agents'))
    rev = got['cards'][3]
    check('present: the review card is work, not a fault', (rev['subPrimaryText'], rev['description'], rev['color']),
          ('1 new today · 1 with a pull request open', '2 proposals decided so far.', None))
    cmp_ = got['cards'][4]
    check('present: the paused composition', (cmp_['subPrimaryText'], cmp_['description'], cmp_['color']),
          ('acme-feedback-controller', 'Ready reason ReconcilePaused.', None))
    check('present: the calm line lists what is fine, beside Needs you', got['quiet'],
          'The rest is fine: 80 compositions Ready · 2 alerts OK · 2 blueprints Ready · 2 of 3 agents registered at the gateway.')
    check('present: the source facet, largest first, counted in items', got['options'], [
        ('Incidents', 'incidents', 5), ('Platform review', 'reviews', 5), ('Agents', 'agents', 2),
        ('Alerts', 'alerts', 1), ('Compositions', 'compositions', 1)])
    check('present: no chips without a filter', got['chips'], [])
    href, prompt, params = got['ask']
    check('present: Ask Agentiko -> this page, no params', (href, params), ('/dashboard', {}))
    check('present: the prompt covers every source', prompt,
          'Brief me on what needs me on my platform: 5 open incidents from 2 alerts, 3 of them from probe; '
          '1 alert firing (Probe failing); 1 agent not reachable through the gateway (nightly-review-agent: not routed · no Allow rule), '
          '1 tool server not Accepted (cujbench-evidence); 5 review proposals waiting for a decision (1 new today, 1 with a pull request open); '
          '1 paused composition (acme-feedback-controller). Tell me what to look at first and why, grounded in the evidence on those pages.')


# --- 2. Not Ready: ZeroEvidence out, other Unknowns and ChildrenForbidden in --------------------------
@case('not Ready leaves out ZeroEvidence')
def _():
    out = inbox()
    check('057: two ZeroEvidence are not counted not Ready', (out['compositions']['notReady'], out['compositions']['notAssessable']), (0, 2))
    extra = [unknown('inventory-not-recorded', 'InventoryNotRecorded'), unknown('children-forbidden', 'ChildrenForbidden'),
             unknown('broken', 'ApplyFailed', 'False'), unknown('creating', 'Creating', 'False')]
    out = inbox(raw={'allCompositions': {'items': COMPS057 + extra}})
    c = out['compositions']
    check('defects: InventoryNotRecorded, ChildrenForbidden, a failure and a Creating are not Ready', c['notReady'], 4)
    check('defects: ZeroEvidence still set aside', c['notAssessable'], 2)
    check('defects: each problem is classed', sorted((p['name'], p['class']) for p in c['problems']), [
        ('acme-feedback-controller', 'paused'), ('broken', 'failed'), ('children-forbidden', 'unreadable'),
        ('creating', 'pending'), ('inventory-not-recorded', 'pending')])
    got = page(out)
    check('defects: the tile goes red', got['strip'][2], ('Compositions not Ready', '4', 'of 86', 'red'))
    card = next(c for c in got['cards'] if c['subSecondaryText'] == 'Compositions')
    check('defects: one card, a fault, names capped at three', (card['primaryText'], card['subPrimaryText'], card['description'], card['color']),
          ('5 compositions need attention', 'inventory-not-recorded, children-forbidden, broken and 2 more',
           '4 not Ready · 1 paused', 'red'))
    zero = [i for i in COMPS057 if i['status']['conditions'][0]['reason'] == 'ZeroEvidence']
    out = inbox(raw={'allCompositions': {'items': zero}})
    check('only ZeroEvidence: nothing not Ready, nothing needs anyone', (out['compositions']['notReady'], out['sources'][4]['items']), (0, 0))


# --- 3. each source absent (its page is not the caller's) -------------------------------------------
@case('a source whose page is not the caller\'s is absent')
def _():
    titles = {'Incidents': 'Open incidents', 'Alerts': 'Alerts firing', 'Agents': 'Agent gateway gaps',
              'Reviews': 'Waiting for a decision', 'Compositions': 'Compositions not Ready'}
    labels = {'Incidents': 'Incidents', 'Alerts': 'Alerts', 'Agents': 'Agents', 'Reviews': 'Platform review', 'Compositions': 'Compositions'}
    for g in GATES:
        out = inbox(gates={g: False})
        got = page(out)
        check(f'gate {g} denied: no tile', titles[g] in [t[0] for t in got['strip']], False)
        check(f'gate {g} denied: no card', labels[g] in [c['subSecondaryText'] for c in got['cards']], False)
        check(f'gate {g} denied: no facet value', labels[g] in [o[0] for o in got['options']], False)
        check(f'gate {g} denied: not in the prompt', MARKER[g] in got['ask'][1], False)
        check(f'gate {g} denied: the source is not listed', [s['key'] for s in out['sources']].count(STAGE_KEY[g]), 0)
    out = inbox(gates={g: False for g in GATES})
    got = page(out)
    check('no page open: only the calm line, saying why', got['body'], ['dashboard-quiet'])
    check('no page open: the sentence', got['quiet'],
          'None of the pages this inbox reads (Incidents, Alerts, Agents, Platform review, Compositions) is open to you.')
    check('no page open: the prompt asks about access', got['ask'][1].startswith('None of the pages behind my Dashboard inbox'), True)
    # A UAF read serves [] to a caller without the grant: with the gate closed it says nothing.
    out = inbox(gates={'Reviews': False}, raw={'proposals': {'items': []}})
    check('gate closed, empty UAF read: no "no proposal waiting" claim', 'proposal' in page(out)['quiet'], False)


# What each source's clause in the prompt always says, and no other clause does.
MARKER = {'Incidents': 'open incident', 'Alerts': 'firing (', 'Agents': 'gateway', 'Reviews': 'waiting for a decision',
          'Compositions': 'composition'}
STAGE_KEY = {'Incidents': 'incidents', 'Alerts': 'alerts', 'Agents': 'agents', 'Reviews': 'reviews', 'Compositions': 'compositions'}


# --- 4. each source's read denied or missing ----------------------------------------------------------
@case('a failed read is never quiet')
def _():
    titles = {'incidents': 'Open incidents', 'alerts': 'Alerts firing', 'agents': 'Agent gateway gaps',
              'reviews': 'Waiting for a decision', 'compositions': 'Compositions not Ready'}
    labels = {'incidents': 'Incidents', 'alerts': 'Alerts', 'agents': 'Agents', 'reviews': 'Platform review', 'compositions': 'Compositions'}
    for key, (msg, why) in {'incidents': ('incidents.observability.krateo.io is forbidden: User "dev" cannot list', 'denied'),
                            'alerts': ('the server could not find the requested resource', 'not installed'),
                            'agents': ('agents.kagent.dev is forbidden', 'denied'),
                            'reviews': ('the server could not find the requested resource (get proposals.review.krateo.io)', 'not installed'),
                            'compositions': ('context deadline exceeded', 'failed')}.items():
        errs = {k: [{'message': msg}] for k in ERROR_KEYS[key]}
        out = inbox(raw={STAGE_OF[key]: None}, errors=errs)
        got = page(out)
        tile = next((t for t in got['strip'] if t[0] == titles[key]), None)
        check(f'{key} {why}: the tile is "—", not 0', tile, (titles[key], '', 'could not be read', 'orange'))
        card = next((c for c in got['cards'] if c['subSecondaryText'] == labels[key]), None)
        check(f'{key} {why}: a card says it could not be read, with the error',
              card and (card['primaryText'], card['secondaryText'], card['description'], card['color']),
              (labels[key] + ' could not be read', 'Not read', msg, 'orange'))
        check(f'{key} {why}: the prompt asks about it first', got['ask'][1].startswith(
            f'Brief me on what needs me on my platform: {labels[key]} could not be read ({msg})'), True)
        check(f'{key} {why}: the facet keeps it, as one item', dict((o[1], o[2]) for o in got['options']).get(key), 1)
    # With nothing else to say, a failed read still never reads as all quiet.
    quiet_raw = {'incidents': {'items': []}, 'alerts': {'items': [alert('a', 'OK')]}, 'proposals': {'items': []},
                 'servers': {'items': []}, 'agents': {'items': [agent('autopilot')]}, 'allCompositions': {'items': []}}
    out = inbox(raw=dict(quiet_raw, incidents=None), errors={'incidentsError': ['forbidden']})
    got = page(out)
    check('only a failed read: Needs you holds it', got['titles'], ['Incidents could not be read'])
    check('only a failed read: the calm line does not say All quiet', got['quiet'].startswith('All quiet'), False)
    check('only a failed read: and does not mention incidents', 'incident' in got['quiet'], False)


# --- 5. the agent gateway, unchecked ------------------------------------------------------------------
@case('an unchecked gateway is said')
def _():
    out = inbox(raw={'routes': {'items': []}})
    got = page(out)
    check('no route visible: the tile says not checked', got['strip'][3], ('Agent gateway gaps', '', 'not checked', 'orange'))
    caveat = next(c for c in got['cards'] if c['primaryText'] == 'Gateway registration not checked')
    check('no route visible: the card says why', caveat['description'],
          'Whether the gateway can reach your agents could not be checked: no HTTPRoute is visible to you.')
    check('no route visible: nobody is flagged', [c['primaryText'] for c in got['cards'] if 'reachable' in c['primaryText']], [])
    check('no route visible: no "registered" claim', 'registered' in got['quiet'], False)
    out = inbox(raw={'policies': None}, errors={'policiesError': ['boom']})
    check('policies failed: the reason', out['agents']['gatewayUnknownWhy'], 'the AgentgatewayPolicy read failed — boom')


# --- 6. ?source= and ?projects -------------------------------------------------------------------------
@case('the filter params')
def _():
    out = inbox(extras={'source': 'incidents'})
    got = page(out)
    check('?source=incidents: the strip shows its tile only', [t[0] for t in got['strip']], ['Open incidents'])
    check('?source=incidents: its card only', got['titles'], ['5 open incidents'])
    check('?source=incidents: nothing else is called fine', got['quiet'], '')
    check('?source=incidents: no calm line in the body', 'dashboard-quiet' in got['body'], False)
    check('?source=incidents: the facet still counts every source', dict((o[1], o[2]) for o in got['options']),
          {'reviews': 5, 'incidents': 5, 'agents': 2, 'alerts': 1, 'compositions': 1})
    check('?source=incidents: one removable chip, then Clear all', got['chips'],
          [('Source: Incidents', '/dashboard', True), ('Clear all', '/dashboard', False)])
    check('?source=incidents: the chips are in the body', 'dashboard-active-filters' in got['body'], True)
    href, prompt, params = got['ask']
    check('?source=incidents: Ask keeps the filter', params, {'source': 'incidents'})
    check('?source=incidents: the prompt says what is in view', prompt.startswith(
        'Brief me on what needs me on my platform (only Incidents): 5 open incidents'), True)
    check('?source=incidents: and nothing else', 'proposal' in prompt, False)
    out = inbox(extras={'source': 'alerts,compositions', 'projects': 'krateo-system'})
    got = page(out)
    check('two sources: their tiles', [t[0] for t in got['strip']], ['Alerts firing', 'Compositions not Ready'])
    check('two sources: a chip each, removing only itself, keeping ?projects', got['chips'], [
        ('Source: Alerts', '/dashboard?projects=krateo-system&source=compositions', True),
        ('Source: Compositions', '/dashboard?projects=krateo-system&source=alerts', True),
        ('Clear all', '/dashboard?projects=krateo-system', False)])
    check('two sources: the card links keep ?projects', [c['navigateTo'] for c in got['cards']],
          ['/alerts?status=alert&projects=krateo-system', '/compositions?projects=krateo-system'])
    check('two sources: Ask keeps both params', got['ask'][2], {'projects': 'krateo-system', 'source': 'alerts,compositions'})
    out = inbox(extras={'projects': 'team-a'})
    check('?projects=team-a: the compositions narrow to it', (out['compositions']['total'], out['compositions']['blueprints']), (0, 0))
    check('?projects=team-a: the incidents do not (they live in the platform namespace)', out['incidents']['open'], 5)
    out = inbox(extras={'source': 'nonsense'})
    got = page(out)
    check('?source=nonsense: nothing in view, said', (got['body'], got['quiet']),
          (['dashboard-filter-bar', 'dashboard-active-filters', 'dashboard-quiet'], 'No source in this filter is in your inbox.'))
    check('?source=nonsense: the value is offered at 0 so it can be removed', ('nonsense', 'nonsense', 0) in got['options'], True)


# --- 7. facet counts and hiding --------------------------------------------------------------------------
QUIET_RAW = {'incidents': {'items': []}, 'alerts': {'items': [alert('a', 'OK'), alert('b', 'OK')]},
             'proposals': {'items': [proposal('m', 'Merged')]}, 'servers': {'items': [server('docs')]},
             'agents': {'items': AGENTS[:2]}, 'allCompositions': {'items': [i for i in COMPS057 if i['metadata']['name'] != 'acme-feedback-controller']}}


@case('facet counts and hiding')
def _():
    out = inbox(raw=dict(QUIET_RAW, proposals={'items': [proposal('w1'), proposal('w2')]}))
    got = page(out)
    check('one source with items: the facet hides', ('dashboard-filter-bar' in got['body'], out['facets'][0]['show']), (False, False))
    check('one source with items: distinct over the base set', out['facets'][0]['distinct'], 1)
    out = inbox(raw=dict(QUIET_RAW, proposals={'items': [proposal('w1'), proposal('w2')]}), extras={'source': 'reviews'})
    check('one source, picked: the facet shows while active', page(out)['body'][0], 'dashboard-filter-bar')
    out = inbox(raw=dict(QUIET_RAW, proposals={'items': [proposal('w1')]}, incidents={'items': [incident('i', 'Open', 'a', '2026-11-01T00:00:00Z')]}))
    got = page(out)
    check('two sources with items: the facet shows', got['body'][0], 'dashboard-filter-bar')
    check('two sources with items: one option each, with its count', got['options'], [('Incidents', 'incidents', 1), ('Platform review', 'reviews', 1)])
    sel = resolved('dashboard-source', 'Select', out)
    check('the Select is a URL-bound multi facet', (sel['queryParam'], sel['mode'], sel['prefix']), ('source', 'multiple', 'Source'))


# --- 8. the cap --------------------------------------------------------------------------------------------
@case('the cap')
def _():
    many = [agent(f'agent-{i}') for i in range(6)]
    out = inbox(raw={'agents': {'items': many}, 'servers': {'items': []}})
    card = next(c for c in page(out)['cards'] if c['subSecondaryText'] == 'Agents')
    check('six unreachable agents: three names, then the rest counted', (card['primaryText'], card['subPrimaryText']),
          ('6 agents not reachable through the gateway', 'agent-0, agent-1, agent-2 and 3 more'))
    check('six unreachable agents: the body names three gaps', card['description'],
          'Gateway: agent-0 not routed · no Allow rule; agent-1 not routed · no Allow rule; agent-2 not routed · no Allow rule.')
    out = inbox(raw={'alerts': {'items': [alert(f'a{i}', 'ALERT') for i in range(4)]}})
    card = next(c for c in page(out)['cards'] if c['subSecondaryText'] == 'Alerts')
    check('four firing alerts: three names and 1 more', card['subPrimaryText'], 'a0, a1, a2 and 1 more')


# --- 9. all quiet -----------------------------------------------------------------------------------------
@case('all quiet')
def _():
    out = inbox(raw=QUIET_RAW)
    got = page(out)
    check('all quiet: no Needs you, no facet', got['body'], ['dashboard-strip', 'dashboard-quiet'])
    check('all quiet: no panel', got['panel'], None)
    check('all quiet: the strip is neutral', [t[3] for t in got['strip']], [None] * 5)
    check('all quiet: the calm line', got['quiet'],
          'All quiet: 79 compositions Ready · 2 alerts OK · 2 blueprints Ready · 2 of 2 agents registered at the gateway · '
          'no open incident · no proposal waiting for a decision.')
    check('all quiet: the prompt says so per source', 'no open incident; no alert firing (2 of 2 OK)' in got['ask'][1], True)


# --- 10. Recent changes -------------------------------------------------------------------------------------
def recent_changes(dict_):
    spec = named(DASH, 'RESTAction', 'dashboard-recent-changes')['spec']
    st = next(s for s in spec['api'] if s['name'] == 'recent')
    iterations = jq(st['dependsOn']['iterator'], dict_)
    out = jq(spec['filter'], dict_)
    return iterations, out, resolved('dashboard-recent-changes', 'Collapse', out)


def ch_rows(n, total):
    return {'meta': [], 'rows': n, 'data': [
        {'ts_ms': str(1762990000000 + i * 60000), 'scope': 'github.com/krateo-platformops/snowplow/audit', 'usr': 'admin',
         'act': 'call', 'method': 'PATCH', 'res': 'compositions', 'kind': '', 'grp': 'composition.krateo.io',
         'name': f'c{i}', 'ns': NS, 'outcome': 'success', 'server': '', 'matched': total} for i in range(n)]}


@case('Recent changes')
def _():
    it, out, w = recent_changes({'namespaces': [], 'allNamespaces': False})
    check('no audit access: no ClickHouse call', it, [])
    check('no audit access: one row saying so', [r['what'] for r in w['items'][0]['dataSource']],
          ['The audit trail of your namespaces is not shared with you.'])
    it, out, w = recent_changes({'namespaces': [NS], 'allNamespaces': False, 'recent': ch_rows(5, 12)})
    check('namespace access: one call, narrowed', [(i['all'], i['acc'], i['r']) for i in it], [(False, [NS], '7d')])
    item = w['items'][0]
    check('twelve this week: five shown, counted, See all to /audit', (item['count'], len(item['dataSource']), item.get('footer'), item.get('footerNavigateTo')),
          (12, 5, 'See all 12 →', '/audit?range=7d'))
    check('a row reads action, target, who and when', item['dataSource'][0],
          {'what': f'Update compositions/c0 in {NS}', 'who': 'admin · 11-12 23:26 UTC'})
    it, out, w = recent_changes({'namespaces': [], 'allNamespaces': True, 'recent': ch_rows(3, 3)})
    check('cluster-wide: no namespace predicate', [i['all'] for i in it], [True])
    check('three this week: no See all', 'footer' in w['items'][0], False)
    it, out, w = recent_changes({'namespaces': [NS], 'allNamespaces': False, 'recentError': ['connection refused']})
    check('ClickHouse failed: said, not an empty week', (w['items'][0].get('status'), w['items'][0]['extra']), ('warning', 'could not be read'))
    it, out, w = recent_changes({'namespaces': [NS], 'allNamespaces': False, 'recent': ch_rows(0, 0)})
    check('an empty week: said', w['items'][0]['dataSource'], [{'what': 'No changes in the last 7 days.', 'who': ''}])


# --- 10b. the ClickHouse request, as snowplow dials it ---------------------------------------------
# The SQL, written out independently of _audit.tpl's defines (test-audit-page.py's POP and DRY).
POP = "ScopeName IN ('github.com/krateo-platformops/snowplow/audit', 'rest-dynamic-controller/audit')"
DRY = "(LogAttributes['krateo.audit.dry_run'] = 'true' OR endsWith(LogAttributes['krateo.action'], '.dryRun'))"
SELECT = ("SELECT toUnixTimestamp64Milli(Timestamp) AS ts_ms, ScopeName AS scope, LogAttributes['user.name'] AS usr, "
          "LogAttributes['krateo.action'] AS act, LogAttributes['http.request.method'] AS method, "
          "LogAttributes['k8s.resource.resource'] AS res, LogAttributes['k8s.resource.kind'] AS kind, "
          "LogAttributes['k8s.resource.group'] AS grp, LogAttributes['k8s.resource.name'] AS name, "
          "LogAttributes['k8s.namespace.name'] AS ns, LogAttributes['outcome'] AS outcome, "
          "LogAttributes['server.address'] AS server, count() OVER () AS matched FROM krateo_audit WHERE ")


@case('Recent changes: the request snowplow dials')
def _():
    spec = importlib.util.spec_from_file_location('spath', os.path.join(HERE, 'snowplow-path.py'))
    spath = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spath)
    st = next(s for s in named(DASH, 'RESTAction', 'dashboard-recent-changes')['spec']['api'] if s['name'] == 'recent')
    tail = " ORDER BY Timestamp DESC LIMIT 5 FORMAT JSON"
    for label, item, access in [
            ('two namespaces', {'namespaces': ['team-a', NS], 'allNamespaces': False}, f" AND LogAttributes['k8s.namespace.name'] IN ('{NS}', 'team-a')"),
            ('cluster-wide', {'namespaces': [], 'allNamespaces': True}, '')]:
        elems = jq(st['dependsOn']['iterator'], item)
        check(f'{label}: one iteration', len(elems), 1)
        path = spath.render(st['path'], elems[0])
        check(f'{label}: a ClickHouse query', path.startswith('/?query='), True)
        once = urllib.parse.unquote(path[len('/?query='):])
        check(f'{label}: no escape survives one decode (not encoded twice)', re.search(r'%[0-9A-Fa-f]{2}', once), None)
        check(f'{label}: the SQL', once, SELECT + POP + ' AND Timestamp > now() - INTERVAL 7 DAY AND NOT ' + DRY + access + tail)


# --- 11. the widgets against the CRDs ------------------------------------------------------------------------
def strict(schema):
    if isinstance(schema, dict):
        schema = {k: strict(v) for k, v in schema.items()}
        if schema.get('type') == 'object' and 'properties' in schema \
                and not schema.get('x-kubernetes-preserve-unknown-fields') and 'additionalProperties' not in schema:
            schema['additionalProperties'] = False
        if schema.get('x-kubernetes-int-or-string'):
            schema.pop('type', None)
            schema['anyOf'] = [{'type': 'integer'}, {'type': 'string'}]
        return schema
    if isinstance(schema, list):
        return [strict(x) for x in schema]
    return schema


def crd_case(crds_dir):
    import jsonschema
    crds = {}
    for path in glob.glob(os.path.join(crds_dir, '**', '*.yaml'), recursive=True):
        for doc in yaml.safe_load_all(open(path, encoding='utf-8')):
            if isinstance(doc, dict) and doc.get('kind') == 'CustomResourceDefinition':
                crds[doc['spec']['names']['kind']] = doc
    counted = [0]

    def validate(label, doc):
        counted[0] += 1
        crd = crds.get(doc['kind'])
        if crd is None:
            failures.append(f'{label}: no CRD for kind {doc["kind"]} in {crds_dir}')
            return
        served = [v for v in crd['spec']['versions'] if v['name'] == doc['apiVersion'].split('/')[1]]
        schema = strict(copy.deepcopy(served[0]['schema']['openAPIV3Schema']))
        schema.setdefault('properties', {}).update({'metadata': {'type': 'object'}, 'apiVersion': {'type': 'string'}, 'kind': {'type': 'string'}})
        for err in sorted(jsonschema.Draft7Validator(schema).iter_errors(doc), key=lambda e: list(e.path)):
            failures.append(f'{label}: {"/".join(map(str, err.path))}: {err.message[:200]}')

    widgets = [d for d in DASH if d.get('apiVersion', '').startswith('widgets.templates.krateo.io')]
    for d in widgets:
        validate(f'authored {d["kind"]} {d["metadata"]["name"]}', d)
    outs = {'present': inbox(), 'quiet': inbox(raw=QUIET_RAW), 'filtered': inbox(extras={'source': 'incidents,nonsense'}),
            'failed': inbox(raw={'incidents': None}, errors={'incidentsError': ['forbidden']}),
            'no gateway': inbox(raw={'routes': {'items': []}}), 'no pages': inbox(gates={g: False for g in GATES})}
    for case_name, out in outs.items():
        for d in widgets:
            if d['spec'].get('apiRef', {}).get('name') != 'dashboard-inbox':
                continue
            doc = copy.deepcopy(d)
            doc['spec']['widgetData'] = resolved(d['metadata']['name'], d['kind'], out)
            validate(f'resolved[{case_name}] {d["kind"]} {d["metadata"]["name"]}', doc)
    for label, dict_ in {'none': {'namespaces': [], 'allNamespaces': False},
                         'rows': {'namespaces': [NS], 'allNamespaces': False, 'recent': ch_rows(5, 12)},
                         'failed': {'namespaces': [NS], 'allNamespaces': False, 'recentError': ['x']}}.items():
        _, _, data = recent_changes(dict_)
        doc = copy.deepcopy(named(DASH, 'Collapse', 'dashboard-recent-changes'))
        doc['spec']['widgetData'] = data
        validate(f'resolved[{label}] Collapse dashboard-recent-changes', doc)
    return counted[0]


parser = argparse.ArgumentParser()
parser.add_argument('--crds')
args = parser.parse_args()

run('render the chart', load)
for label, fn in CASES.items():
    run(label, fn)
crd_checks = 0
if args.crds:
    def _crds():
        global crd_checks
        crd_checks = crd_case(args.crds)
    run('widgets against the CRDs', _crds)

for line in failures:
    print(f'FAIL  {line}')
crd_note = f', {crd_checks} widget(s) validated against the CRDs' if args.crds else ', CRDs not checked (no --crds)'
print(f'{"FAILED" if failures else "ok"}: dashboard inbox ({len(failures)} failure(s){crd_note}, JQ={JQ})')
raise SystemExit(1 if failures else 0)
