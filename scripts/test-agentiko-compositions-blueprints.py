#!/usr/bin/env python3
""""Ask Agentiko →" on /compositions, /blueprints, /marketplace and the two detail pages,
over the charts' OWN jq.

Every entry point is a Button that navigates to an `…Href` its RESTAction builds, so what the
person gets is decided entirely by that jq: which set the prompt describes, which filter params the
link keeps, and what it says when the read behind it failed. This renders the charts and runs the
real programs on fixtures — a copy of the jq kept here would pass while the chart drifted.

THE RULES IT HOLDS THE CHARTS TO:
  * The prompt is about the view on screen. On a filtered list (a status chip, a search, a
    category, a namespace) it describes that filtered set, and the link keeps the same params.
  * A failed read is never described as an empty one. continueOnError turns a refused list into an
    empty array; the prompt then asks what the error means instead of saying there is nothing.
  * The Marketplace never says "newer" or "behind": nothing compares versions, so an installed chart
    is "not at the catalog's latest version" (a string inequality).
  * A detail page's link is "" when the object was not read, and its Button then falls back to the
    list — never a 404.
"""
import json
import os
import subprocess
import tempfile
import urllib.parse

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
JQ = os.environ.get('JQ', 'jq')

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f'{label}: {got!r} != {want!r}')


def render(chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', os.path.join(REPO, 'helm', chart), copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy], capture_output=True, text=True, check=False)
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
            raise AssertionError(f'jq failed: {proc.stderr.strip()}')
        return json.loads(proc.stdout)
    finally:
        os.unlink(path)


def expr_of(doc, for_path):
    for e in doc['spec'].get('widgetDataTemplate') or []:
        if e['forPath'] == for_path:
            raw = e['expression'].strip()
            assert raw.startswith('${') and raw.endswith('}'), raw
            return raw[2:-1]
    raise AssertionError(f'no widgetDataTemplate for {for_path}')


def ask(href):
    """(the path and query the link opens with ?ask removed, the decoded prompt)."""
    path, _, query = href.partition('?')
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    prompt = [v for k, v in pairs if k == 'ask']
    kept = '&'.join(f'{k}={v}' for k, v in pairs if k != 'ask')
    return (path + ('?' + kept if kept else ''), prompt[0] if prompt else None)


def button_check(label, button, payload, field, fallback):
    """The Button navigates to the RA's link, and to its list when there is none."""
    prog = expr_of(button, 'actions')
    data = button['spec']['widgetData']
    check(f'{label}: label and intent', (data['label'], data['intent']), ('Ask Agentiko →', 'agentiko'))
    check(f'{label}: -> {field}', jq(prog, payload)['navigate'][0]['path'], payload[field])
    check(f'{label}: no link -> {fallback}', jq(prog, {})['navigate'][0]['path'], fallback)


def items_of(doc):
    return [i['resourceRefId'] for i in doc['spec']['widgetData']['items']]


ERR403 = [{'apiVersion': 'v1', 'kind': 'Status', 'code': 403, 'reason': 'Forbidden', 'status': 'Failure',
           'message': 'compositiondefinitions.core.krateo.io is forbidden: User "erin" cannot list resource'}]

# --- /compositions and /dashboard: the shell's compositions-list -----------------------------------
portal = render('portal')
comps_prog = named(portal, 'RESTAction', 'compositions-list')['spec']['filter']


def comp(name, ns, kind='WebApp', ready='True', reason='Available', synced='True', wh=None, platform=False):
    conds = [{'type': 'Ready', 'status': ready, 'reason': reason}, {'type': 'Synced', 'status': synced}]
    out = {'uid': name, 'name': name, 'ns': ns, 'ts': '2026-11-01T00:00:00Z', 'kind': kind,
           'av': 'composition.krateo.io/v1', 'conditions': conds, 'platform': platform}
    if wh is not None:
        out['wh'] = wh
    return out


COMPS = [comp('orders-api', 'team-alpha', ready='False', reason='ReconcileError'),
         comp('web', 'team-alpha', wh=False),
         comp('cache', 'team-beta', kind='Redis', synced='False'),
         comp('site', 'team-beta'),
         # A FAILING platform component: outside the default workloads view, inside the dashboard's
         # `.list`. It is what tells the two prompts' sets apart.
         comp('portal', 'krateo-system', kind='Portal', ready='False', reason='ReconcileError', platform=True)]


def run_comps(**extras):
    data = {'allCompositions': extras.pop('items', COMPS)}
    data.update(extras)
    return jq(comps_prog, data)


normal = run_comps()
check('compositions: a read with no error -> readError empty', normal['readError'], '')
href, prompt = ask(normal['askHref'])
check('compositions: unfiltered -> self page, no params', href, '/compositions')
check('compositions: unfiltered -> the workloads in view', prompt.split(' The ones that need attention')[0],
      'Review my compositions: 4 in view — 2 healthy, 1 failed, 1 degraded, 1 drifted, 0 paused, 0 pending, 0 with unreadable children.')
check('compositions: unfiltered -> the ones needing attention, failed first',
      prompt.split('The ones that need attention are: ')[1].split(' For each')[0],
      '1. WebApp/orders-api · team-alpha — failed 2. WebApp/web · team-alpha — degraded 3. Redis/cache · team-beta — drifted.')
check('compositions: unfiltered -> the ask', prompt.endswith(
    'For each, explain what is wrong from its conditions and managed resources, and what to fix first.'), True)

filt = run_comps(status='failed', q='ORD', projects='team-alpha')
href, prompt = ask(filt['askHref'])
check('compositions: filtered -> keeps status, q and projects (in the page\'s param order)', href, '/compositions?projects=team-alpha&status=failed&q=ORD')
check('compositions: filtered -> the filtered view', prompt.split(' The ones that need attention')[0],
      'Review my compositions (State: NotReady; namespace team-alpha; name or type matching "ORD"): '
      '1 in view — 0 healthy, 1 failed, 0 degraded, 0 drifted, 0 paused, 0 pending, 0 with unreadable children.')
check('compositions: filtered -> only the filtered rows listed', 'web' in prompt.split('The ones that need attention are: ')[1], False)
check('compositions: filtered -> the prompt counts what the table shows', len(filt['filtered']), 1)
href, prompt = ask(run_comps(scope='platform')['askHref'])
check('compositions: ?scope=platform -> kept', href, '/compositions?scope=platform')
check('compositions: ?scope=platform -> said', prompt.startswith('Review my compositions (platform components only): 1 in view — 0 healthy, 1 failed'), True)
check('compositions: ?scope=platform -> the platform component listed', '1. Portal/portal · krateo-system — failed.' in prompt, True)
_, prompt = ask(run_comps(q='site')['askHref'])
check('compositions: nothing unhealthy -> no empty list', 'The ones that need attention are' in prompt, False)
check('compositions: nothing unhealthy -> says so', 'None of them is failed, degraded, drifted, paused or unreadable.' in prompt, True)
check('compositions: ?status=all -> not carried', ask(run_comps(status='all')['askHref'])[0], '/compositions')

href, prompt = ask(run_comps(status='pending')['askHref'])
check('compositions: a filter with no rows -> keeps it', href, '/compositions?status=pending')
check('compositions: a filter with no rows -> says so', prompt,
      'No composition is in view (State: Pending); 5 other compositions are outside it. '
      'Tell me whether that is expected, and whether anything in my other compositions needs attention.')
_, prompt = ask(run_comps(items=[])['askHref'])
check('compositions: none at all -> says so', prompt,
      'No composition is in view. Explain how a composition is created on this platform from an installed blueprint.')

# A FAILED READ IS NOT AN EMPTY PLATFORM.
refused = run_comps(items=[], crdsError=ERR403)
check('compositions: refused -> readError', refused['readError'], ERR403[0]['message'])
href, prompt = ask(refused['askHref'])
check('compositions: refused -> asks about the error', prompt,
      f'My compositions could not be read ({ERR403[0]["message"]}). Explain what that error means and what to check.')
check('compositions: refused, filtered -> still keeps the filter',
      ask(run_comps(items=[], crdsError=ERR403, status='failed')['askHref'])[0], '/compositions?status=failed')
partial = run_comps(allCompositionsError=[{'message': 'the server could not find the requested resource'}])
_, prompt = ask(partial['askHref'])
check('compositions: part failed -> says the list may be incomplete', prompt.endswith(
    ' Part of the list could not be read (the server could not find the requested resource), so this may not be all of them.'), True)
check('compositions: the existing keys are unchanged by a read error',
      {k: partial[k] for k in ('list', 'filtered', 'counts')}, {k: normal[k] for k in ('list', 'filtered', 'counts')})
many = run_comps(items=[comp(f'c{i}', 'ns', ready='False', reason='X') for i in range(10)])
_, prompt = ask(many['askHref'])
check('compositions: eight at most', ('8. WebApp/c7' in prompt, '9. ' in prompt), (True, False))

# The Dashboard's Ask Agentiko no longer reads compositions-list: it is the platform inbox now, and
# scripts/test-dashboard-inbox.py holds its prompt (dashboard-inbox's askHref).
check('compositions-list no longer builds the Dashboard\'s link', 'dashboardAskHref' in normal, False)

cpage = render('compositions-page')
cbutton = named(cpage, 'Button', 'compositions-ask-agentiko')
button_check('compositions button', cbutton, normal, 'askHref', '/compositions')
check('compositions button: keyExtras = the table\'s', cbutton['spec']['keyExtras'],
      named(cpage, 'Table', 'compositions-table')['spec']['keyExtras'])
check('compositions header: Ask, then Create', items_of(named(cpage, 'PageHeader', 'compositions-page-header')),
      ['compositions-ask-agentiko', 'new-composition'])

# --- /blueprints: the shell's blueprints-cards ------------------------------------------------------
cards_prog = named(portal, 'RESTAction', 'blueprints-cards')['spec']['filter']


def card(name, ns, color, reason='', category='blueprint'):
    return {'metadata': {'name': name, 'namespace': ns}, 'provisions': name.title(), 'readyColor': color,
            'readyReason': reason, 'category': category, 'version': '1.0.0'}


CARDS = [card('web-app', 'krateo-system', 'green'), card('redis', 'team-alpha', 'red', 'ReconcileError'),
         card('kafka', 'krateo-system', 'orange', category='platform')]
out = jq(cards_prog, {'compdefs': CARDS})
check('blueprints: cards unchanged', [c['metadata']['name'] for c in out['cards']], ['kafka', 'redis', 'web-app'])
href, prompt = ask(out['askHref'])
check('blueprints: unfiltered -> self page', href, '/blueprints')
check('blueprints: unfiltered -> the prompt', prompt,
      'Review the blueprints installed on this platform: 3 blueprints — 1 ready, 1 failing to reconcile '
      '(redis · team-alpha — ReconcileError), 1 still installing. Explain what the failing ones are stuck on and how to fix them.')
href, prompt = ask(jq(cards_prog, {'compdefs': CARDS, 'category': 'platform', 'q': 'kaf'})['askHref'])
check('blueprints: filtered -> keeps category and q', href, '/blueprints?category=platform&q=kaf')
check('blueprints: filtered -> the filtered view', prompt,
      'Review the blueprints installed on this platform (category platform; name or kind matching "kaf"): 1 blueprint — '
      '0 ready, 0 failing to reconcile, 1 still installing. None is failing to reconcile. Tell me whether any still '
      'installing looks stuck, and what each blueprint lets me create.')
_, prompt = ask(jq(cards_prog, {'compdefs': CARDS, 'projects': 'nowhere'})['askHref'])
check('blueprints: a filter with no cards -> says so', prompt,
      'No installed blueprint is in view (namespace nowhere); 3 are installed outside it. Tell me whether that is expected.')
_, prompt = ask(jq(cards_prog, {'compdefs': []})['askHref'])
check('blueprints: none installed -> says so', prompt.startswith('No blueprint is installed on this platform yet.'), True)
refused = jq(cards_prog, {'compdefsError': ERR403})
_, prompt = ask(refused['askHref'])
check('blueprints: refused -> asks about the error, not "none installed"', prompt,
      f'The blueprints on this platform could not be read ({ERR403[0]["message"]}). Explain what that error means and what to check.')
bpage = render('blueprints-page')
button_check('blueprints button', named(bpage, 'Button', 'blueprints-ask-agentiko'), out, 'askHref', '/blueprints')
grid = [d for d in bpage if d.get('metadata', {}).get('name') == 'blueprints-grid' and d.get('kind') != 'RESTAction'][0]
check('blueprints button: keyExtras = the grid\'s', named(bpage, 'Button', 'blueprints-ask-agentiko')['spec']['keyExtras'],
      grid['spec']['keyExtras'])
check('blueprints header: the button alone', items_of(named(bpage, 'PageHeader', 'blueprints-page-header')), ['blueprints-ask-agentiko'])

# --- /blueprints/{namespace}/{name} ----------------------------------------------------------------
detail_prog = named(bpage, 'RESTAction', 'blueprint-detail')['spec']['filter']
CD = {'metadata': {'name': 'web-app', 'namespace': 'krateo-system'},
      'spec': {'chart': {'repo': 'web-app', 'version': '1.4.2'}},
      'status': {'conditions': [{'type': 'Ready', 'status': 'True'}, {'type': 'Synced', 'status': 'True'}]}}
CATALOG = {'entries': {'web-app': [{'version': '1.5.0'}, {'version': '1.4.2'}]}}
ITEMS = [{'metadata': {'name': f'c{i}', 'namespace': 'team-alpha', 'creationTimestamp': f'2026-11-0{i + 1}T00:00:00Z'},
          'status': {'conditions': [{'type': 'Ready', 'status': 'False' if i == 0 else 'True'}]}} for i in range(3)]
d = jq(detail_prog, {'name': 'web-app', 'namespace': 'krateo-system', 'compdef': CD, 'catalog': CATALOG, 'comps': {'items': ITEMS}})
href, prompt = ask(d['askHref'])
check('blueprint detail: -> self page', href, '/blueprints/krateo-system/web-app')
check('blueprint detail: -> the prompt', prompt,
      'Explain blueprint "web-app" in namespace "krateo-system": it is at version 1.4.2 (latest 1.5.0), Ready=True, Synced=True, '
      'and 3 compositions run from it (1 not ready). Is it healthy, should I upgrade it, and what would an upgrade change for those compositions?')
_, prompt = ask(jq(detail_prog, {'name': 'web-app', 'namespace': 'krateo-system', 'compdef': CD,
                                 'catalog': {'entries': {'web-app': [{'version': '1.4.2'}]}}, 'compsError': ERR403})['askHref'])
check('blueprint detail: at latest, compositions refused -> says both', prompt.split(': it is at ')[1].split('. Is it')[0],
      f'version 1.4.2 (the catalog\'s latest), Ready=True, Synced=True, and the compositions running from it could not be listed ({ERR403[0]["message"]})')
missing = jq(detail_prog, {'name': 'gone', 'namespace': 'krateo-system'})
check('blueprint detail: not found -> no link', missing['askHref'], '')
dbtn = named(bpage, 'Button', 'blueprint-detail-ask-agentiko')
button_check('blueprint detail button', dbtn, d, 'askHref', '/blueprints')
check('blueprint detail button: not found -> the list', jq(expr_of(dbtn, 'actions'), missing)['navigate'][0]['path'], '/blueprints')
acts = items_of(named(bpage, 'Flex', 'blueprint-detail-actions'))
check('blueprint detail actions: Ask immediately left of Create', acts[acts.index('blueprint-detail-create') - 1], 'blueprint-detail-ask-agentiko')

# --- /marketplace and /marketplace/{name} ----------------------------------------------------------
mpage = render('marketplace-page')
cat_prog = named(mpage, 'RESTAction', 'blueprints-catalog')['spec']['filter']
detail_prog = named(mpage, 'RESTAction', 'marketplace-detail')['spec']['filter']
BASE = 'https://charts.example/blueprints'
INDEX = {'entries': {
    'redis': [{'name': 'redis', 'version': '1.2.0', 'urls': [f'{BASE}/redis-1.2.0.tgz'], 'keywords': ['database'],
               'annotations': {'krateo.io/category': 'database', 'krateo.io/maturity': 'stable'}}],
    'web-app': [{'name': 'web-app', 'version': '2.0.0', 'urls': [f'{BASE}/web-app-2.0.0.tgz'], 'keywords': ['web']}],
    'kafka': [{'name': 'kafka', 'version': '3.0.0', 'urls': [f'{BASE}/kafka-3.0.0.tgz'], 'keywords': ['queue']}]}}
OPS = {'entries': {'cert-manager': [{'name': 'cert-manager', 'version': '1.0.0', 'urls': ['https://charts.example/operators/cm.tgz']}]}}
INSTALLED = [{'name': 'redis', 'ns': 'team-alpha', 'version': '1.1.0', 'repo': 'redis', 'url': BASE},
             # Installed at a version the catalog does not list as its latest, and above it as a
             # string. Nothing compares versions, so it is "not at the catalog's latest" too.
             {'name': 'web-app', 'ns': 'team-alpha', 'version': '2.1.0', 'repo': 'web-app', 'url': BASE}]


def run_cat(**extras):
    data = {'catalog': INDEX, 'operators': OPS, 'compdefs': INSTALLED}
    data.update(extras)
    return jq(cat_prog, data)


out = run_cat()
href, prompt = ask(out['askHref'])
check('marketplace: unfiltered -> self page', href, '/marketplace')
check('marketplace: unfiltered -> the prompt', prompt,
      "Help me choose from the Marketplace: the catalog offers 4 charts (3 blueprints, 1 operators); 2 are installed here, "
      "and 2 of those are not at the catalog's latest version (redis 1.1.0 → 1.2.0, web-app 2.1.0 → 2.0.0). "
      "What should I install or upgrade for this platform, and why?")
check('marketplace: never "newer" or "behind"', any(w in prompt for w in ('newer', 'behind')), False)
href, prompt = ask(run_cat(source='blueprint', category='database', q='red is', sort='recent')['askHref'])
# qstr builds the query from an object, and jq and gojq order an object's keys differently, so the
# params are compared as a set: their order means nothing to the page.
check('marketplace: filtered -> keeps every facet', (href.split('?')[0], sorted(href.split('?')[1].split('&'))),
      ('/marketplace', ['category=database', 'q=red is', 'sort=recent', 'source=blueprint']))
check('marketplace: filtered -> the view', prompt.split(';')[0:3],
      ['Help me choose from the Marketplace: this view (blueprints only', ' keyword database', ' matching "red is") shows 0 of the catalog\'s 4 charts (0 blueprints, 0 operators)'])
_, prompt = ask(run_cat(source='blueprint', category='database')['askHref'])
check('marketplace: filtered -> counts what is shown', prompt.split(' What should')[0],
      "Help me choose from the Marketplace: this view (blueprints only; keyword database) shows 1 of the catalog's 4 charts "
      "(1 blueprints, 0 operators); 1 is installed here, and 1 of those is not at the catalog's latest version (redis 1.1.0 → 1.2.0).")
_, prompt = ask(run_cat(catalog=None, operators=None, catalogError=[{'message': 'dial tcp: i/o timeout'}],
                        operatorsError='503 Service Unavailable')['askHref'])
check('marketplace: index unreadable -> asks about it, never "offers 0 charts"', prompt,
      'The Marketplace catalog could not be read: the blueprints index could not be read (dial tcp: i/o timeout); '
      'the operators index could not be read (503 Service Unavailable). Explain what that means and what to check.')
_, prompt = ask(run_cat(operators=None, operatorsError='503 Service Unavailable')['askHref'])
check('marketplace: one index unreadable -> incomplete', 'The catalog may be incomplete: the operators index could not be read (503 Service Unavailable).' in prompt, True)
_, prompt = ask(run_cat(compdefs=None, compdefsError=ERR403)['askHref'])
check('marketplace: installed list refused -> not "0 installed"', ('0 are installed' in prompt, 'which of them are installed here could not be read' in prompt), (False, True))
mbtn = named(mpage, 'Button', 'marketplace-ask-agentiko')
button_check('marketplace button', mbtn, out, 'askHref', '/marketplace')
check('marketplace button: keyExtras = the header\'s', mbtn['spec']['keyExtras'], named(mpage, 'PageHeader', 'marketplace-page-header')['spec']['keyExtras'])
check('marketplace header: the button alone', items_of(named(mpage, 'PageHeader', 'marketplace-page-header')), ['marketplace-ask-agentiko'])

d = jq(detail_prog, {'name': 'redis', 'catalog': INDEX, 'compdefs': [{'name': 'redis', 'version': '1.1.0', 'namespace': 'team-alpha'}]})
href, prompt = ask(d['askHref'])
check('marketplace detail: -> self page', href, '/marketplace/redis')
check('marketplace detail: installed -> the prompt', prompt,
      'Tell me about the "redis" chart in the Marketplace (v1.2.0, database, stable; installed here at v1.1.0): what it provisions, '
      'what it has created in my namespace, and whether it fits this platform.')
_, prompt = ask(jq(detail_prog, {'name': 'kafka', 'catalog': INDEX, 'compdefs': []})['askHref'])
check('marketplace detail: not installed -> the prompt', prompt,
      'Tell me about the "kafka" chart in the Marketplace (v3.0.0, queue; not installed here): what it provisions, '
      'what installing it would create in my namespace, and whether it fits this platform.')
_, prompt = ask(jq(detail_prog, {'name': 'kafka', 'catalog': INDEX, 'compdefsError': ERR403})['askHref'])
check('marketplace detail: installed list refused -> says so', '; whether it is installed here could not be read)' in prompt, True)
missing = jq(detail_prog, {'name': 'nope', 'catalog': INDEX})
check('marketplace detail: not in the catalog -> no link', missing['askHref'], '')
dbtn = named(mpage, 'Button', 'marketplace-detail-ask-agentiko')
button_check('marketplace detail button', dbtn, d, 'askHref', '/marketplace')
check('marketplace detail actions: Ask, then Install', items_of(named(mpage, 'Flex', 'marketplace-detail-actions')),
      ['marketplace-detail-ask-agentiko', 'marketplace-detail-action'])

for f in failures:
    print(f'FAIL  {f}')
print(f'{"FAILED" if failures else "ok"}: Ask Agentiko on compositions, blueprints and marketplace'
      f' ({len(failures)} failure(s))')
raise SystemExit(1 if failures else 0)
