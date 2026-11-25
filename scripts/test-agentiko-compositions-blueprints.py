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

And the Marketplace tile's installed state and its action (UX audit MKT-2, Diego: "same name
counts"): a CD installed from oci://…/<name> is an install of catalog chart <name>, as is a
helm-repo CD from the card's repo; such a card offers Configure (its blueprint page), every other
Install, through the grid's {actionLabel} row action and its "/${actionPath}" route.
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
check('compositions: unfiltered -> the workloads needing attention (the default state)', prompt.split(' The ones that need attention')[0],
      'Review my compositions (State: Needs attention): 3 in view — 0 healthy, 1 failed, 1 degraded, 1 drifted, 0 paused, 0 pending, 0 with unreadable children.')
check('compositions: ?status=all -> every workload, a drifted one not counted healthy',
      ask(run_comps(status='all')['askHref'])[1].split(' The ones that need attention')[0],
      'Review my compositions: 4 in view — 1 healthy, 1 failed, 1 degraded, 1 drifted, 0 paused, 0 pending, 0 with unreadable children.')
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
check('compositions: ?scope=platform -> said', prompt.startswith('Review my compositions (State: Needs attention; platform components only): 1 in view — 0 healthy, 1 failed'), True)
check('compositions: ?scope=platform -> the platform component listed', '1. Portal/portal · krateo-system — failed.' in prompt, True)
_, prompt = ask(run_comps(q='site', status='all')['askHref'])
check('compositions: nothing unhealthy -> no empty list', 'The ones that need attention are' in prompt, False)
check('compositions: nothing unhealthy -> says so', 'None of them is failed, degraded, drifted, paused or unreadable.' in prompt, True)
check('compositions: ?status=all -> carried (Needs attention is the default)', ask(run_comps(status='all')['askHref'])[0], '/compositions?status=all')

href, prompt = ask(run_comps(status='pending')['askHref'])
check('compositions: a filter with no rows -> keeps it', href, '/compositions?status=pending')
check('compositions: a filter with no rows -> says so', prompt,
      'No composition is in view (State: Pending); 5 other compositions are outside it. '
      'Tell me whether that is expected, and whether anything in my other compositions needs attention.')
_, prompt = ask(run_comps(items=[])['askHref'])
check('compositions: none at all -> says so', prompt,
      'No composition is in view (State: Needs attention). Explain how a composition is created on this platform from an installed blueprint.')

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


def card(name, ns, state, reason='', category='blueprint'):
    color = {'ready': 'green', 'failed': 'red', 'installing': 'orange', 'paused': 'gray', 'unobserved': 'gray'}[state]
    return {'metadata': {'name': name, 'namespace': ns}, 'provisions': name.title(), 'readyState': state,
            'readyColor': color, 'readyReason': reason, 'category': category, 'version': '1.0.0'}


CARDS = [card('web-app', 'krateo-system', 'ready'), card('redis', 'team-alpha', 'failed', 'ReconcileError'),
         card('kafka', 'krateo-system', 'installing', category='platform')]
out = jq(cards_prog, {'compdefs': CARDS})
check('blueprints: cards unchanged', [c['metadata']['name'] for c in out['cards']], ['kafka', 'redis', 'web-app'])
href, prompt = ask(out['askHref'])
check('blueprints: unfiltered -> self page', href, '/blueprints')
check('blueprints: unfiltered -> the prompt', prompt,
      'Review the blueprints installed on this platform: 3 blueprints — 1 ready, 1 failing to reconcile '
      '(redis · team-alpha — ReconcileError), 1 still installing. Explain what the failing ones are stuck on and how to fix them.')
_, prompt = ask(jq(cards_prog, {'compdefs': CARDS + [card('vault', 'krateo-system', 'paused', 'Paused'),
                                                     card('nats', 'krateo-system', 'unobserved', 'NotObserved')]})['askHref'])
check('blueprints: paused and not-observed are counted apart, never as installing or failing', prompt,
      'Review the blueprints installed on this platform: 5 blueprints — 1 ready, 1 failing to reconcile '
      '(redis · team-alpha — ReconcileError), 1 still installing, 1 paused, 1 not observed by core-provider. '
      'Explain what the failing ones are stuck on and how to fix them.')
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
# UX review round 3: the card's Create is SECONDARY (the card click is the main path). Without
# `type`, the frontend makes a card's first row action primary (rowActionButtonType).
check('blueprints grid: Create is a secondary button',
      [(a['label'], a.get('type')) for a in grid['spec']['widgetData']['itemTemplate']['rowActions']], [('Create', 'default')])

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

# Running compositions (visual audit BP-1, STATE-1): each row prints the state the Compositions list
# prints for the same object, from the same conditions, and its colour is that state's. A paused
# composition reports the Ready=True it had before the pause; it read green "Ready" here.
def bp_comp(name, conds, wh=None):
    st = {'conditions': [{'type': t, 'status': v, 'reason': r} for t, v, r in conds]}
    if wh is not None:
        st['workloadHealthy'] = wh
    return {'kind': 'WebApp', 'apiVersion': 'composition.krateo.io/v1-4-2',
            'metadata': {'name': name, 'namespace': 'team-alpha', 'creationTimestamp': '2026-11-01T00:00:00Z', 'uid': name}, 'status': st}


STATES = [bp_comp('paused', [('Ready', 'True', 'ReconcilePaused'), ('Synced', 'False', 'ReconcilePaused')]),
          bp_comp('ready', [('Ready', 'True', 'Available'), ('Synced', 'True', 'ReconcileSuccess')]),
          bp_comp('drift', [('Ready', 'True', 'Available'), ('Synced', 'False', 'ReconcileError')]),
          bp_comp('down', [('Ready', 'False', 'Unavailable'), ('Synced', 'True', 'ReconcileSuccess')]),
          bp_comp('creating', [('Ready', 'False', 'Creating')]),
          bp_comp('silent', [('Ready', 'Unknown', 'ZeroEvidence'), ('Synced', 'True', 'ReconcileSuccess')]),
          bp_comp('locked', [('Ready', 'Unknown', 'ChildrenForbidden')]),
          bp_comp('sick', [('Ready', 'True', 'Available'), ('Synced', 'True', 'ReconcileSuccess')], wh=False)]
rows = {r['name']: r for r in jq(detail_prog, {'name': 'web-app', 'namespace': 'krateo-system', 'compdef': CD, 'catalog': CATALOG,
                                               'comps': {'items': STATES}})['compositions']}
listed = jq(named(portal, 'RESTAction', 'compositions-list')['spec']['filter'],
            {'status': 'all', 'allCompositions': jq(named(portal, 'RESTAction', 'compositions-list')['spec']['api'][1]['filter'],
                                                    {'allCompositions': {'items': STATES}})})
cards = {r['name']: r for r in listed['rows']}
check('blueprint detail: the fixture reaches both pages', (len(rows), sorted(cards) == sorted(rows)), (8, True))
check('blueprint detail: a running composition reads as on /compositions (label)',
      {n: rows[n]['readyLabel'] for n in rows}, {n: cards[n]['stateLabel'] for n in rows})
check('blueprint detail: the paused one is Paused and gray, not Ready',
      (rows['paused']['readyLabel'], rows['paused']['readyColor']), ('Paused', 'paused'))
bplist = named(bpage, 'Listy', 'blueprint-detail-compositions')['spec']['widgetData']['itemTemplate']['color']
cardmap = named(cpage, 'Listy', 'compositions-cards')['spec']['widgetData']['itemTemplate']['color']['map']
check('blueprint detail: every state has a colour, the cards\' one (pending keeps this page\'s orange)',
      {r['readyColor']: bplist['map'].get(r['readyColor']) for r in rows.values()},
      {r['readyColor']: (cardmap.get(r['readyColor']) if r['readyColor'] != 'pending' else 'orange') for r in rows.values()})

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
      ['Help me choose from the Marketplace: this view (blueprints only', ' category Database', ' matching "red is") shows 0 of the catalog\'s 4 charts (0 blueprints, 0 operators)'])
_, prompt = ask(run_cat(source='blueprint', category='database')['askHref'])
check('marketplace: filtered -> counts what is shown', prompt.split(' What should')[0],
      "Help me choose from the Marketplace: this view (blueprints only; category Database) shows 1 of the catalog's 4 charts "
      "(1 blueprints, 0 operators); 1 is installed here, and 1 of those is not at the catalog's latest version (redis 1.1.0 → 1.2.0).")
href, prompt = ask(run_cat(keyword='queue')['askHref'])
check('marketplace: a keyword -> kept, and said', (href, prompt.split(' shows')[0]),
      ('/marketplace?keyword=queue', 'Help me choose from the Marketplace: this view (keyword queue)'))
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

d = jq(detail_prog, {'name': 'redis', 'catalog': INDEX, 'compdefs': [{'name': 'redis', 'ns': 'team-alpha', 'version': '1.1.0', 'repo': 'redis', 'url': BASE}]})
href, prompt = ask(d['askHref'])
check('marketplace detail: -> self page', href, '/marketplace/redis')
check('marketplace detail: installed -> the prompt', prompt,
      'Tell me about the "redis" chart in the Marketplace (v1.2.0, Database, stable; installed here at v1.1.0): what it provisions, '
      'what it has created in my namespace, and whether it fits this platform.')
_, prompt = ask(jq(detail_prog, {'name': 'kafka', 'catalog': INDEX, 'compdefs': []})['askHref'])
check('marketplace detail: not installed -> the prompt', prompt,
      'Tell me about the "kafka" chart in the Marketplace (v3.0.0; not installed here): what it provisions, '
      'what installing it would create in my namespace, and whether it fits this platform.')
_, prompt = ask(jq(detail_prog, {'name': 'kafka', 'catalog': INDEX, 'compdefsError': ERR403})['askHref'])
check('marketplace detail: installed list refused -> says so', '; whether it is installed here could not be read)' in prompt, True)
missing = jq(detail_prog, {'name': 'nope', 'catalog': INDEX})
check('marketplace detail: not in the catalog -> no link', missing['askHref'], '')
dbtn = named(mpage, 'Button', 'marketplace-detail-ask-agentiko')
button_check('marketplace detail button', dbtn, d, 'askHref', '/marketplace')
check('marketplace detail actions: Ask, then Install', items_of(named(mpage, 'Flex', 'marketplace-detail-actions')),
      ['marketplace-detail-ask-agentiko', 'marketplace-detail-action'])

# --- the Marketplace tile: installed, and the action it offers (UX audit MKT-2) --------------------
OCI = [{'name': 'kafka-events', 'ns': 'krateo-system', 'version': '3.0.0', 'repo': '', 'url': 'oci://ghcr.io/krateo-blueprints/charts/kafka'},
       # Another chart, whose name only starts with "kafka": not an install of kafka.
       {'name': 'kafka-ui', 'ns': 'krateo-system', 'version': '1.0.0', 'repo': '', 'url': 'oci://ghcr.io/krateo-blueprints/charts/kafka-ui:1.0.0'}]
cards = {c['name']: c for c in run_cat(compdefs=INSTALLED + OCI)['cards']}
def tile(name):
    c = cards[name]
    return tuple(c.get(k) for k in ('installed', 'installedNs', 'installedName', 'actionLabel', 'actionIcon', 'actionPath'))
check('tile, helm-repo install -> Configure', tile('redis'), (True, 'team-alpha', 'redis', 'Configure', 'fa-sliders', 'blueprints/team-alpha/redis'))
check('tile, OCI install of the same chart name -> installed, Configure its own CD',
      tile('kafka'), (True, 'krateo-system', 'kafka-events', 'Configure', 'fa-sliders', 'blueprints/krateo-system/kafka-events'))
check('tile, OCI install of the same chart name -> the badge', (cards['kafka']['installedIcon'], cards['kafka']['installedTooltip']),
      ('fa-circle-check', 'Installed · v3.0.0'))
check('tile, not installed -> Install', tile('cert-manager'), (False, '', '', 'Install', 'fa-download', 'marketplace/cert-manager/install'))
tagged = {c['name']: c for c in run_cat(compdefs=[dict(OCI[0], url='oci://ghcr.io/krateo-blueprints/charts/kafka:3.0.0')])['cards']}
check('tile, OCI url with a tag -> its chart name', tagged['kafka']['installed'], True)
other = {c['name']: c for c in run_cat(compdefs=[OCI[1]])['cards']}
check('tile, an OCI chart of another name -> not installed', (other['kafka']['installed'], other['kafka'].get('actionLabel')), (False, 'Install'))
grid = named(mpage, 'Listy', 'marketplace-grid')['spec']
check('grid, the card action reads the row', [(a['actionId'], a['label'], a.get('icon')) for a in grid['widgetData']['itemTemplate']['rowActions']][0],
      ('install', '{actionLabel}', '{actionIcon}'))
check('grid, the route is the row\'s (a leading "${" would be jq over the widget)',
      [a['path'] for a in grid['widgetData']['actions']['navigate'] if a['id'] == 'install'], ['/${actionPath}'])
row = jq(expr_of(named(mpage, 'Listy', 'marketplace-grid'), 'dataSource'), {'cards': [cards['kafka']]})[0]
check('grid, the row carries the action fields', (row.get('actionLabel'), row.get('actionIcon'), row.get('actionPath')),
      ('Configure', 'fa-sliders', 'blueprints/krateo-system/kafka-events'))

# --- ONE RULE for the tile and the detail page (portal.marketplaceInstalledDefs) ------------------
# Chart identity, never the CD's own name: the two cases where the tile (repo + chart) and the page
# (CD name) used to disagree now agree, and so does every card over one set of CDs.
def detail_of(name, cds):
    return jq(detail_prog, {'name': name, 'catalog': INDEX, 'operators': OPS, 'compdefs': cds})
def tile_of(name, cds):
    return {c['name']: c for c in run_cat(compdefs=cds)['cards']}[name]
detail_action = expr_of(named(mpage, 'Button', 'marketplace-detail-action'), 'actions')
# 1. Installed through the Install form under another name: both installed, both open that CD.
custom = [{'name': 'my-redis', 'ns': 'team-alpha', 'version': '1.2.0', 'repo': 'redis', 'url': BASE}]
t, d = tile_of('redis', custom), detail_of('redis', custom)
check('one rule, custom name: tile installed, Configure my-redis', (t['installed'], t.get('actionPath')), (True, 'blueprints/team-alpha/my-redis'))
check('one rule, custom name: page installed', (d['installed'], d['installedNamespace'], d.get('installedName'), d['installedVersion']),
      (True, 'team-alpha', 'my-redis', '1.2.0'))
check('one rule, custom name: page Configure opens my-redis', jq(detail_action, d)['navigate'][0]['path'], '/blueprints/team-alpha/my-redis')
# 2. A CD named like the chart, installed from a repo that is not the catalog's: neither installed.
foreign = [{'name': 'kafka', 'ns': 'team-alpha', 'version': '3.0.0', 'repo': 'kafka', 'url': 'https://other.example/charts'}]
t, d = tile_of('kafka', foreign), detail_of('kafka', foreign)
check('one rule, same-named CD from a foreign repo: tile not installed', (t['installed'], t.get('actionLabel')), (False, 'Install'))
check('one rule, same-named CD from a foreign repo: page not installed', d['installed'], False)
check('one rule, same-named CD from a foreign repo: page offers Install', jq(detail_action, d)['navigate'][0]['path'], '/marketplace/kafka/install')
# 3. An OCI install of the chart's name (acme-feedback's case): both installed.
oci = [{'name': 'kafka-events', 'ns': 'krateo-system', 'version': '3.0.0', 'repo': '', 'url': 'oci://ghcr.io/krateo-blueprints/charts/kafka@sha256:abc'}]
check('one rule, OCI install: tile and page installed', (tile_of('kafka', oci)['installed'], detail_of('kafka', oci).get('installedName')), (True, 'kafka-events'))
# 4. Over one set of CDs, every card and its page agree.
mixed = INSTALLED + custom + foreign + oci + [{'name': 'cert-manager', 'ns': 'x', 'version': '1.0.0', 'repo': 'cert-manager', 'url': 'https://charts.example/operators'}]
tiles = {c['name']: c for c in run_cat(compdefs=mixed)['cards']}
check('one rule, every card agrees with its page',
      {n: (c['installed'], c['installedName']) for n, c in tiles.items()},
      {n: (detail_of(n, mixed)['installed'], detail_of(n, mixed).get('installedName') if detail_of(n, mixed)['installed'] else '') for n in tiles})
check('an operator\'s page finds it in the operators index', (detail_of('cert-manager', [])['found'], detail_of('cert-manager', [])['version']), (True, '1.0.0'))
check('the rule is one definition, included by both', all('def installedOf(' in p for p in (cat_prog, detail_prog)), True)

# --- ONE RULE for a chart's category (portal.marketplaceCategoryDefs, UX audit MKT-4/MKT-5) ------
# The krateo.io/category annotation when it is one of the eight, else the first of the chart's own
# keywords the fixed table lists (whole tokens), else none. The tile's tag, the chips and the detail
# page's Category row all read it (Diego, 2026-11-25: the derived category shows everywhere).
def entry(name, keywords=None, category=None, version='1.0.0'):
    e = {'name': name, 'version': version, 'urls': [f'{BASE}/{name}-{version}.tgz'], 'keywords': keywords or []}
    if category is not None:
        e['annotations'] = {'krateo.io/category': category}
    return [e]
CAT_INDEX = {'entries': {
    'aws-rds-stack': entry('aws-rds-stack', ['aws', 'rds'], 'Database'),           # annotation, any case
    'rds-chart': entry('rds-chart', ['aws', 'ack', 'kubernetes', 'rds']),        # keyword rds, after three unlisted
    'acm-chart': entry('acm-chart', ['acm', 'iam']),                             # the FIRST listed keyword wins
    'autoscaling-chart': entry('autoscaling-chart', ['kubernetes', 'autoscaling']),
    'agentgateway': entry('agentgateway', ['agentgateway', 'gateway']),          # whole token, not its own name
    'kube-thing': entry('kube-thing', ['kubernetes', 'ec2-like']),               # substrings only: none
    'odd-annotation': entry('odd-annotation', ['S3'], 'krateo'),                 # not one of the eight: keyword, any case
    'acme-feedback': entry('acme-feedback')}}                                     # nothing: none
CAT_OPS = {'entries': {'cert-manager': [{'name': 'cert-manager', 'version': '1.0.0', 'urls': ['https://charts.example/operators/cm.tgz'],
                                         'keywords': ['cert-manager', 'security']}]}}
WANT_CAT = {'aws-rds-stack': 'Database', 'rds-chart': 'Database', 'acm-chart': 'Security', 'autoscaling-chart': 'Compute',
            'agentgateway': 'Network', 'kube-thing': '', 'odd-annotation': 'Storage', 'acme-feedback': '', 'cert-manager': 'Security'}
cat_cards = {c['name']: c for c in jq(cat_prog, {'catalog': CAT_INDEX, 'operators': CAT_OPS, 'compdefs': []})['cards']}
check('category rule: each tile\'s tag', {n: c.get('category') for n, c in cat_cards.items()}, WANT_CAT)
check('category rule: the key beside the word', {n: c.get('categoryKey') for n, c in cat_cards.items()}, {n: v.lower() for n, v in WANT_CAT.items()})
cat_details = {n: jq(detail_prog, {'name': n, 'catalog': CAT_INDEX, 'operators': CAT_OPS, 'compdefs': []}) for n in WANT_CAT}
check('category rule: the detail page says what the tile says', {n: d['category'] for n, d in cat_details.items()}, WANT_CAT)
about = expr_of(named(mpage, 'Descriptions', 'descriptions-marketplace-detail-about'), 'items')
check('category rule: the Category row, derived or annotated, and no row for none',
      {n: [i['value'] for i in jq(about, d) if i['label'] == 'Category'] for n, d in cat_details.items()},
      {n: ([v] if v else []) for n, v in WANT_CAT.items()})
check('category rule: one definition, included by both', all('def marketplaceCategory:' in p for p in (cat_prog, detail_prog)), True)

# --- THE FILTERS (UX audit MKT-4, approved mockup 2026-11-25) --------------------------------------
# Type chips and category chips with counts, the keywords as one Select whose options merge the
# keywords that pick the same charts, the order as a Select; each count is what the choice shows
# under the OTHER filters; an old ?category=<keyword> link still filters by that keyword.
FX = {'entries': {
    'aws-rds': entry('aws-rds', ['aws', 'ack', 'rds']),
    'aws-vpc': entry('aws-vpc', ['aws', 'ack', 'vpc']),
    'aws-s3': entry('aws-s3', ['aws', 'ack', 's3']),
    'aws-ec2': entry('aws-ec2', ['aws', 'ack', 'ec2']),
    'aws-iam': entry('aws-iam', ['aws', 'ack', 'iam']),
    'aws-dns': entry('aws-dns', ['aws', 'dns']),
    'pg': entry('pg', ['postgresql'], 'database'),
    'plain': entry('plain', ['krateo'])}}
FX_OPS = {'entries': {'rds-op': [{'name': 'rds-op', 'version': '1.0.0', 'urls': ['https://charts.example/operators/rds-op.tgz'], 'keywords': ['rds']}]}}
def fx(**extras):
    return jq(cat_prog, dict({'catalog': FX, 'operators': FX_OPS, 'compdefs': []}, **extras))
def chips(out, key):
    return [(c['label'], c.get('count'), c.get('active')) for c in out.get(key, [])]
def qs_of(route):
    path, _, q = route.partition('?')
    return path, sorted(q.split('&')) if q else []
def filters_checks():
    global out
    out = fx()
    check('filters: type chips count every card', chips(out, 'sources'), [('All', 9, True), ('Blueprints', 8, False), ('Operators', 1, False)])
    check('filters: category chips by catalog count, "Any category" first, none for a chart with none',
          chips(out, 'categoryFacets'), [('Any category', None, True), ('Database', 3, False), ('Network', 2, False),
                                         ('Compute', 1, False), ('Security', 1, False), ('Storage', 1, False)])
    check('filters: aws (6) and ack (5) pick different charts, so two options',
          [(o['value'], o['label'], o['count']) for o in out.get('keywordOptions', [])], [('aws', 'aws', 6), ('ack', 'ack', 5)])
    merged = fx(catalog={'entries': {k: v for k, v in FX['entries'].items() if k != 'aws-dns'}})
    check('filters: keywords that pick the same charts are one option', [(o['value'], o['label'], o['count']) for o in merged.get('keywordOptions', [])],
          [('aws', 'aws · ack', 5)])
    out = fx(category='database', source='blueprint')
    check('filters: ?category=database&source=blueprint -> the blueprints of that category', sorted(c['name'] for c in out['cards']), ['aws-rds', 'pg'])
    check('filters: a type chip counts under the category, not under its own type', chips(out, 'sources'),
          [('All', 3, False), ('Blueprints', 2, True), ('Operators', 1, False)])
    check('filters: a category chip counts under the type, not under its own category',
          chips(out, 'categoryFacets')[:3], [('Any category', None, False), ('Database', 2, True), ('Network', 2, False)])
    check('filters: a category with no card in view is left out', [c[0] for c in chips(fx(source='operator'), 'categoryFacets')], ['Any category', 'Database'])
    check('filters: a category chip route keeps the other filters and sets its own',
          qs_of(next(c['route'] for c in out['categoryFacets'] if c['label'] == 'Network')), ('/marketplace', ['category=network', 'source=blueprint']))
    check('filters: "Any category" drops the category only', qs_of(out['categoryFacets'][0]['route']), ('/marketplace', ['source=blueprint']))
    check('filters: a type chip keeps the category', qs_of(out['sources'][2]['route']), ('/marketplace', ['category=database', 'source=operator']))
    out = fx(keyword='ack', q='ACK s3')
    check('filters: ?keyword= and ?q= together', [c['name'] for c in out['cards']], ['aws-s3'])
    check('filters: routes keep keyword and the @uri-encoded q', qs_of(out['sources'][1]['route']), ('/marketplace', ['keyword=ack', 'q=ACK%20s3', 'source=blueprint']))
    check('filters: a keyword option counts under the other filters, not its own', [(o['value'], o['count']) for o in out.get('keywordOptions', [])], [('aws', 1), ('ack', 1)])
    old = fx(category='ack')
    check('filters: an old ?category=<keyword> link filters by that keyword', (len(old['cards']), old['selectedKeyword'], old['selectedCategory']), (5, 'ack', 'all'))
    check('filters: and its links say keyword=', qs_of(old['sources'][1]['route']), ('/marketplace', ['keyword=ack', 'source=blueprint']))
    check('filters: ?category= in any case', fx(category='Database')['selectedCategory'], 'database')
    check('filters: ?keyword= wins over an old ?category=<keyword>', fx(category='rds', keyword='ack')['selectedKeyword'], 'ack')
    check('filters: an option with no card in view is left out, the selected one kept',
          [(o['value'], o['count']) for o in fx(keyword='ack', source='operator').get('keywordOptions', [])], [('ack', 0)])
    check('filters: no category at all -> no category row', fx(catalog={'entries': {'plain': FX['entries']['plain']}}, operators=None)['categoryFacets'], [])
    check('filters: the orders the index supports', fx()['sorts'], [{'value': 'name', 'label': 'Name (A–Z)'}, {'value': 'recent', 'label': 'Recently updated'}])
    check('filters: the old keyword chips are gone', 'categories' in fx(), False)
    # The widgets: the search line (search 18 + order 6), then one line of chips and the Keyword select.
    page_items = items_of(named(mpage, 'Flex', 'page-marketplace'))
    check('filters: page order', page_items, ['marketplace-page-header', 'marketplace-filters-row', 'marketplace-facets', 'marketplace-grid'])
    row = named(mpage, 'Row', 'marketplace-filters-row')['spec']['widgetData']['items']
    check('filters: the search line', [(i['resourceRefId'], i['size']) for i in row], [('marketplace-search-wrap', 18), ('marketplace-sort', 6)])
    check('filters: the filter line', items_of(named(mpage, 'Flex', 'marketplace-facets')), ['marketplace-source-toggle', 'marketplace-category-chips', 'marketplace-keyword'])
    kw_sel, sort_sel = named(mpage, 'Select', 'marketplace-keyword'), named(mpage, 'Select', 'marketplace-sort')
    check('filters: Keyword writes ?keyword=, Order ?sort=', (kw_sel['spec']['widgetData']['queryParam'], sort_sel['spec']['widgetData']['queryParam']), ('keyword', 'sort'))
    check('filters: Keyword options carry the count', jq(expr_of(kw_sel, 'options'), fx()), [{'value': 'aws', 'label': 'aws', 'count': 6}, {'value': 'ack', 'label': 'ack', 'count': 5}])
    check('filters: Order options', jq(expr_of(sort_sel, 'options'), fx()), fx()['sorts'])
    check('filters: the category chips read categoryFacets', jq(expr_of(named(mpage, 'Listy', 'marketplace-category-chips'), 'dataSource'), out), out['categoryFacets'])
    check('filters: every widget over the catalog partitions by every filter',
          {d['metadata']['name']: sorted(set(d['spec']['keyExtras']) - {'spotlight'}) for d in mpage
           if d.get('kind') != 'RESTAction' and (d.get('spec', {}).get('apiRef') or {}).get('name') == 'blueprints-catalog'},
          {n: ['category', 'keyword', 'q', 'sort', 'source'] for n in ('marketplace-grid', 'marketplace-category-chips', 'marketplace-source-toggle',
                                                                     'marketplace-keyword', 'marketplace-sort', 'marketplace-page-header', 'marketplace-ask-agentiko')})


# Run as one unit: on code without the filters a missing key is one failure, not a traceback.
try:
    filters_checks()
except (KeyError, TypeError, StopIteration, AssertionError) as exc:
    failures.append(f'filters: the catalog\'s output lacks what the filters read ({type(exc).__name__}: {exc})')

for f in failures:
    print(f'FAIL  {f}')
print(f'{"FAILED" if failures else "ok"}: Ask Agentiko on compositions, blueprints and marketplace'
      f' ({len(failures)} failure(s))')
raise SystemExit(1 if failures else 0)
