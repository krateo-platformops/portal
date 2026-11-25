#!/usr/bin/env python3
"""
test-builders-overview — /portal-builder, /blueprint-builder and /controller-builder as dashboards (the
/incidents layout, mockup A4), over the charts' own jq.

WHAT IT GUARDS:
  - <builder>-builder-overview joins the caller's drafts and what the builder published, and gives
    each row one lifecycle state: failed, action (a person's step), ready, draft, review, merged,
    live, closed. The source's own status stays the label; the drafts are the caller's only, and
    no username means no drafts, never an error;
  - the Portal Builder lists its own page publishes and the pre-claim change requests that did not
    go to a blueprints repository, never a blueprint's or a controller's, and the Blueprint Builder
    the pre-claim ones that did: each on exactly one page;
  - merged is the provider's word: a change request closed with no merge reported keeps its step,
    worded "if merged"; what the platform does after a merge (reconciling, registering,
    installing) is merged, not "Change request open";
  - a failed row says why, cleaned of any response body, links to the object that says so, and a
    controller's credentials note never stands in for it;
  - a failed read says "could not be read", never zeros;
  - one colour per state everywhere (cards, table, figures, donut); a zero is never red, orange or
    gold, and only a non-zero figure of a state that needs attention stands out;
  - Needs attention (failed, action, ready, draft) is the default chip, then each state something is
    in, in triage order, then All; every route keeps the range, the view and the search;
  - the five figures count their states, link to the chip of a single state, and caption what the
    builder config names (oldest open, merged in the range, drafts ready);
  - the Summary cards come in triage order, split head / drafts / tail, and only the drafts carry
    Discard; the Full list carries every row with its link;
  - ?q= narrows everything before it is counted, the charts included; ?range= sets the trend's
    buckets; Discard deletes the card's own record, after a confirmation;
  - every widget of the three pages declares the page's filter keys and, where it reads the
    overview, the caller's identity.

Usage: test-builders-overview.py [--crds DIR]. JQ=gojq runs the same on snowplow's engine.
"""
import datetime
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)
_spec = importlib.util.spec_from_file_location('ctl', os.path.join(HERE, 'test-controller-builder.py'))
ctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctl)

NOW = datetime.datetime.now(datetime.timezone.utc)
KEYS = ['state', 'range', 'view', 'q']
SANDBOX = 'krateo-preview'


def ago(days):
    return (NOW - datetime.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')


def record(owner, kind, name, previewed=False, state='open', days=1, version='0.2.0'):
    labels = {'krateo.io/purpose': 'draft-record', 'krateo.io/draft-owner': owner, 'krateo.io/draft-kind': kind,
              'krateo.io/draft-state': state}
    if previewed:
        labels['krateo.io/draft-previewed'] = 'true'
    body = {'kind': kind, 'name': name, 'files': {'Chart.yaml': f'version: {version}\n', 'values.yaml': 'x: 1\n'}}
    return {'metadata': {'name': f'draft-{kind}-{owner}-{name}', 'namespace': SANDBOX, 'labels': labels,
                         'annotations': {'krateo.io/draft-name': name, 'krateo.io/draft-updated-at': ago(days)},
                         'creationTimestamp': ago(days)},
            'data': {'draft.json': json.dumps(body)}}


RECORDS = {'items': [
    record('admin', 'page', 'team-costs', previewed=True, days=1),
    record('admin', 'page', 'onboarding-hub', days=0.1, version='CHART_VERSION'),
    record('admin', 'page', 'shipped-page', state='published'),
    record('admin', 'blueprint', 'postgres-ha', days=0.2),
    record('admin', 'controller', 'github-teams', previewed=True, days=0.3),
    record('someone-else', 'page', 'not-mine'),
]}


def local_resource(publish, builder, failed=False, days=3, message='the branch builder/x already exists', reg=False):
    if reg:
        lr = tbi.local_resource(publish, builder, 'compositiondefinition.yaml', '/',
                                tbi.registration_file(publish[8:], f'oci://ghcr.io/krateo-blueprints/{publish[8:]}', '0.1.0'))
    else:
        lr = tbi.local_resource(publish, builder, 'Chart.yaml')
    lr['metadata']['creationTimestamp'] = ago(days)
    if failed:
        lr['status']['conditions'] = [{'type': 'Synced', 'status': 'False', 'reason': 'ReconcileError', 'message': message}]
    return lr


def pr(publish, number, state, merged=tbi.ABSENT, merged_at=tbi.ABSENT, repo=None, head=None, days=3):
    p = tbi.pull_request(publish, number, state, merged, repo=repo, head=head, merged_at=merged_at)
    p['metadata']['creationTimestamp'] = ago(days)
    return p


# Pages: one failed publish, one open, one merged (no registration file, so nothing to install),
# one closed, and a pre-claim PR to the portal repo. A blueprint and a controller publish, which the
# Portal Builder must not list.
PUBLISHES = [
    local_resource('publish-vendor-contracts', 'page', failed=True, days=0.2),
    local_resource('publish-release-calendar', 'page', days=9),
    local_resource('publish-finops-weekly', 'page', days=6),
    local_resource('publish-old-landing', 'page', days=31),
    local_resource('publish-redis-cache', 'blueprint', days=4),
    local_resource('publish-kafka-topic', 'blueprint', days=20),
    local_resource('publish-pet', 'controller', days=2),
]
PRS = {'items': [
    pr('publish-release-calendar', 209, 'open', days=9),
    pr('publish-finops-weekly', 201, 'closed', merged=True, merged_at=ago(2), days=6),
    pr('publish-old-landing', 160, 'closed', merged=False, days=31),
    pr('publish-redis-cache', 91, 'closed', merged=True, merged_at=ago(1), days=4),
    pr('publish-kafka-topic', 80, 'closed', merged=True, merged_at=ago(15), days=20),
    pr('publish-pet', 44, 'open', days=2),
    pr('', 150, 'open', repo='portal', head='builder/incident-wall', days=12),
    pr('', 70, 'open', repo='krateo-blueprints', head='builder/legacy-bp', days=28),
    pr('', 71, 'open', repo='acme-blueprints', head='builder/acme-bp', days=27),
]}
CDS = {'items': [{'metadata': {'name': 'kafka-topic', 'namespace': tbi.NS}, 'spec': {'chart': {'version': '1.1.0'}},
                  'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}]}


def responses():
    return {'records': RECORDS, 'prs': PRS, 'publishes': {'items': PUBLISHES}, 'cds': CDS,
            'catalog': tbi.catalog_configmap(), 'installs': {'items': []}, 'restdefs': {'items': []}}


def overview(key, extras=None, username='admin', **override):
    """The overview as snowplow serves it; `override` replaces whole step responses ('ERROR' fails one)."""
    ex = dict(extras or {})
    if username is not None:
        ex['username'] = username
    return tbi.resolve(CH, f'{key}-builder-overview', {**responses(), **override}, ex)


def rows(out):
    return out['cardsHead'] + out['cardsDrafts'] + out['cardsTail']


def by_name(out):
    return {r['name']: r for r in out['table']}


def expect(f, label, got, want):
    if got != want:
        f.append(f'{label}: got {got!r}, want {want!r}')


def check_states_and_sources():
    f = []
    out = overview('portal', {'state': 'all'})
    t = by_name(out)
    expect(f, 'portal: its own pages, its drafts, the pre-claim portal PR — nothing else',
           sorted(t), sorted(['vendor-contracts', 'release-calendar', 'finops-weekly', 'old-landing', 'feat: incident-wall',
                              'team-costs', 'onboarding-hub']))
    expect(f, 'portal: states', {n: r['color'] for n, r in t.items()},
           {'vendor-contracts': 'red', 'release-calendar': 'blue', 'finops-weekly': 'gray', 'old-landing': 'gray',
            'feat: incident-wall': 'blue', 'team-costs': 'gold', 'onboarding-hub': 'gray'})
    expect(f, 'portal: an open change request says so, never PR', t['release-calendar']['label'], 'Change request open')
    expect(f, 'portal: the source keeps its word', t['vendor-contracts']['label'], 'Publish failed')
    expect(f, 'portal counts', {k: out['counts'][k] for k in ('failed', 'ready', 'draft', 'review', 'merged', 'closed', 'attention', 'all')},
           {'failed': 1, 'ready': 1, 'draft': 1, 'review': 2, 'merged': 1, 'closed': 1, 'attention': 3, 'all': 7})
    bp = by_name(overview('blueprint', {'state': 'all'}))
    expect(f, 'blueprint: merged without a CD and with no registration file is merged; registered is live',
           {n: (r['label'], r['color']) for n, r in bp.items() if n in ('redis-cache', 'kafka-topic', 'postgres-ha')},
           {'redis-cache': ('Merged', 'gray'), 'kafka-topic': ('Registered', 'green'), 'postgres-ha': ('Draft', 'gray')})
    expect(f, 'blueprint: the pre-claim change request on the blueprints repository is the Blueprint Builder\'s',
           'legacy-bp' in bp, True)
    ctl = by_name(overview('controller', {'state': 'all'}))
    expect(f, 'controller: an open change request is blue, a previewed draft is gold',
           {n: r['color'] for n, r in ctl.items()}, {'pet': 'blue', 'github-teams': 'gold'})
    return f


def check_drafts_are_the_callers():
    f = []
    names = {r['name'] for r in overview('portal', {'state': 'all'})['table']}
    expect(f, 'another person\'s draft is not listed', 'not-mine' in names, False)
    expect(f, 'a published draft is not listed twice', 'shipped-page' in names, False)
    metas = {r['name']: r['meta'] for r in overview('portal', {'state': 'all'})['table']}
    expect(f, 'a released version is shown', metas['team-costs'].startswith('v0.2.0'), True)
    expect(f, 'the CHART_VERSION placeholder is not a version', 'CHART_VERSION' in metas['onboarding-hub'], False)
    anon = overview('portal', {'state': 'all'}, username=None)
    expect(f, 'no username: no drafts, and no error', [r['name'] for r in anon['cardsDrafts']], [])
    return f


def check_chips_and_routes():
    f = []
    out = overview('portal', {'range': '7d', 'q': ''})
    chips = [(c['label'], c['count'], c['active']) for c in out['stateChips']]
    expect(f, 'chips: Needs attention by default, then each state in triage order, then All', chips,
           [('Needs attention', 3, True), ('Failed', 1, False), ('Ready to publish', 1, False), ('Draft', 1, False),
            ('Change request open', 2, False), ('Merged', 1, False), ('Closed', 1, False), ('All', 7, False)])
    expect(f, 'a chip keeps the range', out['stateChips'][1]['route'], '/portal-builder?state=failed&range=7d')
    expect(f, 'the default range is not written', overview('portal')['stateChips'][-1]['route'], '/portal-builder?state=all')
    table = overview('blueprint', {'view': 'table', 'q': 'redis'})
    expect(f, 'view chips keep the state and the search',
           [(c['label'], c['active'], c['route']) for c in table['viewChips']],
           [('Summary', False, '/blueprint-builder?q=redis'), ('Full list', True, '/blueprint-builder?view=table&q=redis')])
    expect(f, 'range chips', [(c['label'], c['active']) for c in overview('controller', {'range': '90d'})['rangeChips']],
           [('7d', False), ('30d', False), ('90d', True)])
    expect(f, 'an unknown state lists nothing and says so', overview('portal', {'state': 'bogus'})['emptyText'],
           'That is not a state a page can be in. Choose a chip above.')
    return f


def check_figures():
    f = []
    out = overview('portal')
    stats = {s['title']: (s['value'], s['tone']) for s in out['stats']}
    expect(f, 'portal figures', stats, {'Publish failed': ('1', 'failed'), 'Needs a step': ('0', 'action'), 'Change request open': ('2', 'review'),
                                        'Drafts': ('2', 'draft'), 'Merged': ('1', 'merged')})
    look = {s['title']: (s['color'], s['highlight']) for s in out['stats']}
    expect(f, 'colour and highlight are the data\'s: a zero is grey, only a non-zero alarm stands out', look,
           {'Publish failed': ('red', True), 'Needs a step': ('gray', False), 'Change request open': ('blue', False),
            'Drafts': ('gray', False), 'Merged': ('gray', False)})
    cap = {s['title']: s['caption'] for s in out['stats']}
    expect(f, 'oldest open', cap['Change request open'], 'oldest open 12 days')
    expect(f, 'merged in the range', cap['Merged'], '1 merged in the last 30 days')
    expect(f, 'a one-state figure opens its chip', out['stats'][0]['route'], '/portal-builder?state=failed')
    bp = {s['title']: s for s in overview('blueprint')['stats']}
    expect(f, 'blueprint: Drafts counts drafts and ready, captioned with the ready', (bp['Drafts']['value'], bp['Drafts']['caption']),
           ('1', '0 ready to publish'))
    expect(f, 'blueprint: Registered', (bp['Registered']['value'], bp['Registered']['caption']), ('1', '0 in the marketplace'))
    # A caption with a comma in it is one caption (a YAML flow mapping would split it into two keys).
    expect(f, 'blueprint: To install keeps its whole caption', bp['To install']['caption'], 'merged, not installed yet')
    expect(f, 'a figure of several states that all need attention opens Needs attention', bp['Drafts']['route'], '/blueprint-builder')
    return f


def check_cards():
    f = []
    out = overview('portal', {'state': 'all'})
    expect(f, 'cards in triage order, newest first within a state', [r['name'] for r in rows(out)],
           ['vendor-contracts', 'team-costs', 'onboarding-hub', 'release-calendar', 'feat: incident-wall', 'finops-weekly', 'old-landing'])
    expect(f, 'only drafts in the drafts list', [r['name'] for r in out['cardsDrafts']], ['team-costs', 'onboarding-hub'])
    d = out['cardsDrafts'][0]
    expect(f, 'a draft card resumes its record', (d['href'], d['recordName'], d['cta']),
           ('/portal-builder/compose?resume=draft-page-admin-team-costs', 'draft-page-admin-team-costs', 'Publish'))
    head = out['cardsHead'][0]
    expect(f, 'a failed publish says why', (head['label'], head['icon'], head['description']),
           ('Publish failed', 'fa-triangle-exclamation', 'The branch builder/x already exists.'))
    rest = CH.get('Listy', 'portal-builder-cards-drafts')['spec']['widgetData']['actions']['rest'][0]
    target = next(r for r in CH.get('Listy', 'portal-builder-cards-drafts')['spec']['resourcesRefs']['items'] if r['id'] == rest['resourceRefId'])
    name = tbi.jq(rest['payloadToOverride'][0]['value'][2:-1].strip(), {'json': d})
    expect(f, 'Discard asks first, then deletes the card\'s own record in the sandbox',
           (rest['requireConfirmation'], target['verb'], target['resource'], name, rest['payloadToOverride'][1]['value']),
           (True, 'DELETE', 'configmaps', 'draft-page-admin-team-costs', SANDBOX))
    drafts = CH.get('Listy', 'portal-builder-cards-drafts')['spec']['widgetData']
    expect(f, 'drafts can be resumed and discarded', [a['actionId'] for a in drafts['itemTemplate']['rowActions']], ['resume', 'discard'])
    # A navigate path that starts with ${ is jq over the widget, not the row: Resume must fill a
    # placeholder from the row, and land where the card's own href does.
    for key in ('portal', 'blueprint', 'controller'):
        resume = CH.get('Listy', f'{key}-builder-cards-drafts')['spec']['widgetData']['actions']['navigate'][0]['path']
        expect(f, f'{key}: Resume fills the row\'s record into the path', resume,
               f"/{key}-builder/compose?resume=${{recordName}}")
    expect(f, 'Resume lands where the card does', '/portal-builder/compose?resume=${recordName}'.replace('${recordName}', d['recordName']), d['href'])
    for part in ('head', 'tail'):
        w = CH.get('Listy', f'portal-builder-cards-{part}')['spec']['widgetData']
        expect(f, f'{part}: no row action, a call to action', ('rowActions' in w['itemTemplate'], w['itemTemplate'].get('cardCta')), (False, '{cta}'))
    card = CH.get('Card', 'portal-builder-list-card')
    items = tbi.widget(CH, 'Card', 'portal-builder-list-card', 'items', out, {})
    expect(f, 'the list card mounts the three lists', [i['resourceRefId'] for i in items],
           ['portal-builder-state-chips', 'portal-builder-cards-head', 'portal-builder-cards-drafts', 'portal-builder-cards-tail',
            'portal-builder-published-label', 'my-drafts-page-published'])
    table = tbi.widget(CH, 'Card', 'portal-builder-list-card', 'items', overview('portal', {'view': 'table'}), {})
    expect(f, 'Full list', [i['resourceRefId'] for i in table], ['portal-builder-state-chips', 'portal-builder-table'])
    empty = tbi.widget(CH, 'Card', 'portal-builder-list-card', 'items', overview('portal', {'state': 'live'}), {})
    expect(f, 'empty', [i['resourceRefId'] for i in empty][:2], ['portal-builder-state-chips', 'portal-builder-empty'])
    expect(f, 'the list card names its pieces', card['spec']['widgetData']['extraRefId'], 'portal-builder-view-chips')
    return f


def check_search_and_charts():
    f = []
    out = overview('portal', {'state': 'all', 'q': 'CALENDAR'})
    expect(f, 'the search narrows before counting', (out['counts']['all'], [r['name'] for r in out['table']]), (1, ['release-calendar']))
    expect(f, 'the search is a substring of the name', overview('portal', {'state': 'all', 'q': 'zzz'})['emptyText'],
           'Nothing matches "zzz". Clear the search to see everything.')
    for rng, n in (('7d', 7), ('30d', 30), ('90d', 13)):
        trend = overview('portal', {'range': rng})['trend']
        expect(f, f'{rng}: two series of {n} buckets', len(trend), 2 * n)
    trend = overview('portal')['trend']
    expect(f, 'published and merged in 30 days', (sum(p['v'] for p in trend if p['series'] == 'Published'),
                                                  sum(p['v'] for p in trend if p['series'] == 'Merged')), (4, 1))
    out = overview('portal')
    expect(f, 'time to merge: finops-weekly, 4 days', ([p['v'] for p in out['ttm']], out['ttmText']), ([4.0], 'median 4 days in the last 30 days'))
    expect(f, 'waiting longest, oldest first', [w['name'] for w in out['waiting']],
           ['feat: incident-wall', 'release-calendar', 'team-costs', 'vendor-contracts'])
    expect(f, 'by state', out['byState'], [{'state': s, 'count': c} for s, c in
                                          (('Failed', 1), ('Ready to publish', 1), ('Draft', 1), ('Change request open', 2), ('Merged', 1), ('Closed', 1))])
    expect(f, 'the donut\'s legend holds only the states it draws, each in the cards\' colour', out['byStateColors'],
           {'Failed': 'red', 'Ready to publish': 'gold', 'Draft': 'gray', 'Change request open': 'blue', 'Merged': 'gray', 'Closed': 'gray'})
    cards = {r['name']: r['color'] for r in rows(overview('portal', {'state': 'all'}))}
    expect(f, 'one state, one colour: card and slice', (cards['team-costs'], cards['finops-weekly']), ('gold', 'gray'))
    narrowed = overview('portal', {'q': 'calendar'})
    expect(f, 'the search narrows the trend and time to merge',
           (sum(p['v'] for p in narrowed['trend']), narrowed['ttm'], narrowed['ttmText']), (1, [], 'no merge in the last 30 days'))
    return f


def check_widgets_declare_the_page():
    """Every widget reachable from the three page roots declares the filter keys; every one that
    reads an overview asks as the caller. The guides exist, outside the pages."""
    f = []
    for root, key in (('page-portal-builder', 'portal'), ('page-blueprint-builder', 'blueprint'), ('page-kog-builder', 'controller')):
        page = CH.get('Flex', root)
        expect(f, f'{root}: filter keys', page['metadata']['annotations'].get('krateo.io/filter-keys'), ','.join(KEYS))
        seen, todo = set(), [(page['kind'], root)]
        while todo:
            kind, name = todo.pop()
            if (kind, name) in seen:
                continue
            seen.add((kind, name))
            doc = next((d for d in CH.docs if d.get('kind') == kind and d['metadata']['name'] == name), None)
            if doc is None:
                continue
            spec = doc['spec']
            # Resolved as snowplow serves it, for the CRD check (--crds): over the overview of every
            # row, or as authored when it templates nothing.
            src = (spec.get('apiRef') or {}).get('name', '')
            if src.endswith('-builder-overview'):
                tbi.resolved_widget(CH, kind, name, overview(key, {'state': 'all'}), {}, f'{kind} {name}')
            elif not spec.get('widgetDataTemplate'):
                tbi.RESOLVED.append((f'{kind} {name} (as authored)', doc))
            if spec.get('keyExtras') != KEYS:
                f.append(f'{kind} {name}: keyExtras {spec.get("keyExtras")!r}, want {KEYS!r}')
            if (spec.get('apiRef') or {}).get('name', '').endswith('-builder-overview') and spec.get('identityContext') != ['username']:
                f.append(f'{kind} {name} reads the overview without the caller\'s identity')
            for ref in (spec.get('resourcesRefs') or {}).get('items') or []:
                if ref.get('apiVersion', '').startswith('widgets.templates.krateo.io'):
                    kinds = {d['kind'] for d in CH.docs if d['metadata']['name'] == ref['name'] and d.get('kind') != 'RESTAction'}
                    todo += [(k, ref['name']) for k in kinds]
        if not any(n == f'{key}-builder-table' for _, n in seen):
            f.append(f'{root}: the Full list is not reachable')
    expect(f, 'the guides are the Builders\' own (spec.guide), not widgets of this chart',
           [d['metadata']['name'] for d in CH.docs if 'guide' in d['metadata']['name']], [])
    return f


def check_what_the_old_pages_had():
    """The three things the builder pages showed before the dashboards, still there."""
    f = []
    items = lambda key, out: [i['resourceRefId'] for i in tbi.widget(CH, 'Card', f'{key}-builder-list-card', 'items', out, {})]
    # 1. The caller's published drafts, under the Summary, each with Remove from list.
    out = overview('portal', {'state': 'all'})
    expect(f, 'the caller\'s published drafts are counted', out['published'], 1)
    expect(f, 'under the Summary: the published drafts', items('portal', out)[-2:],
           ['portal-builder-published-label', 'my-drafts-page-published'])
    expect(f, 'even when the state has nothing', items('portal', overview('portal', {'state': 'live'}))[-2:],
           ['portal-builder-published-label', 'my-drafts-page-published'])
    expect(f, 'not in the Full list', 'my-drafts-page-published' in items('portal', overview('portal', {'view': 'table'})), False)
    expect(f, 'none published: no section', items('blueprint', overview('blueprint', {'state': 'all'}))[-1], 'blueprint-builder-cards-tail')
    pub = CH.get('Listy', 'my-drafts-page-published')['spec']
    expect(f, 'a published draft can be removed', [a['label'] for a in pub['widgetData']['itemTemplate']['rowActions']], ['Remove from list'])
    expect(f, 'the published list declares the page', pub.get('keyExtras'), ['state', 'range', 'view', 'q'])
    # 2. Every RestDefinition, with why it is not Ready: the Controller Builder's own view.
    rd = overview('controller', {'view': 'restdefs', 'state': 'failed', 'range': '7d'})
    expect(f, 'RestDefinitions is a view of the Controller Builder', [(c['label'], c['active'], c['route']) for c in rd['viewChips']],
           [('Summary', False, '/controller-builder?state=failed&range=7d'), ('Full list', False, '/controller-builder?state=failed&range=7d&view=table'),
            ('RestDefinitions', True, '/controller-builder?state=failed&range=7d&view=restdefs')])
    expect(f, 'the view shows the table alone', items('controller', rd), ['kog-restdefinitions'])
    expect(f, 'a figure leads back to the list', rd['stats'][0]['route'], '/controller-builder?state=failed&range=7d')
    expect(f, 'a range keeps the view', rd['rangeChips'][0]['route'], '/controller-builder?state=failed&range=7d&view=restdefs')
    expect(f, 'only the Controller Builder has it', (overview('portal', {'view': 'restdefs'})['view'], len(overview('portal')['viewChips'])), ('', 2))
    expect(f, 'the search narrows the RestDefinitions too', [r['name'] for r in tbi.resolve(CH, 'kog-restdefinitions', {'restdefs': {'items': [
        {'metadata': {'name': 'pets-pet', 'namespace': 'team-a'}, 'spec': {'resourceGroup': 'petstore.example.io'}},
        {'metadata': {'name': 'teams', 'namespace': 'team-b'}, 'spec': {'resourceGroup': 'github.example.io'}}]}}, {'q': 'PETSTORE'})['rows']],
           ['pets-pet'])
    reasons = [c['title'] for c in CH.get('Table', 'kog-restdefinitions')['spec']['widgetData']['columns']]
    expect(f, 'the table says why one is not Ready', 'Reason' in reasons, True)
    bars = CH.get('Listy', 'controller-builder-groups')['spec']['widgetData']['itemTemplate']
    expect(f, 'a bar opens the view', bars.get('navigateTo'), '/controller-builder?view=restdefs')
    # 3. Why a publish failed, in the Full list too.
    why = {r['name']: r['why'] for r in overview('portal', {'state': 'all', 'view': 'table'})['table']}
    expect(f, 'a failed row says why', why['vendor-contracts'], 'The branch builder/x already exists.')
    expect(f, 'any other row is blank', why['release-calendar'], '')
    cols = [c['valueKey'] for c in CH.get('Table', 'portal-builder-table')['spec']['widgetData']['columns']]
    expect(f, 'the Full list has a Why column', cols[:3], ['name', 'status', 'why'])
    return f


def check_what_the_review_asked():
    """portal#510's review: each case the first version got wrong."""
    f = []
    t = lambda out: by_name(out)
    # A failed blueprint publish: why, without the response body, and the refused file to open.
    body = ('POST https://api.github.com/repos/krateo-blueprints/broken-bp/git/refs: 422 Unprocessable Entity '
            '{"message":"Reference already exists","documentation_url":"https://docs.github.com"}')
    pubs = {'items': PUBLISHES + [local_resource('publish-broken-bp', 'blueprint', failed=True, days=0.5, message=body)]}
    row = t(overview('blueprint', {'state': 'all'}, publishes=pubs))['broken-bp']
    expect(f, 'a failed blueprint says why, without the body, and opens the refused file', (row['label'], row['why'], row['href']),
           ('Publish failed', 'POST https://api.github.com/repos/krateo-blueprints/broken-bp/git/refs: 422 Unprocessable Entity.',
            f'/resources/{tbi.NS}/git.krateo.io/v1alpha1/localresources/publish-broken-bp-Chart.yaml-root'))
    # A failed controller whose Ready kind needs credentials: the failure is its Why, not the note.
    broken = ctl.restdef('pets-store', 'pets', ready='False', gen='Store', synced='False')
    broken['status']['conditions'][-1]['message'] = 'cannot reach https://petstore.example.io: 401 Unauthorized <html><body>denied</body></html>'
    def controller(*restdefs):
        return overview('controller', {'state': 'all'}, cds=ctl.items(ctl.cd('pet', ready='True')),
                        installs=ctl.items(ctl.claim('pets', 'pet')), restdefs=ctl.items(*restdefs), configs=ctl.items())
    out = controller(ctl.restdef('pets-pet', 'pets', auth=True), broken)
    row = t(out)['pet']
    expect(f, 'a failed controller says what failed, never the credentials note, and opens the failing kind',
           (row['label'], row['why'], row['href']),
           ('Failed', 'Cannot reach https://petstore.example.io: 401 Unauthorized.',
            '/resources/team-a/ogen.krateo.io/v1alpha1/restdefinitions/pets-store'))
    # After the merge, the platform's work is merged (grey), not "Change request open".
    out = controller(ctl.restdef('pets-pet', 'pets', ready='False', reason='Unavailable'))
    expect(f, 'an Installed controller is merged, not open', (t(out)['pet']['label'], t(out)['pet']['color'], out['counts']['review']),
           ('Installed', 'gray', 0))
    out = controller(ctl.restdef('pets-pet', 'pets'))
    expect(f, 'a Ready controller is live, the only green', (t(out)['pet']['label'], t(out)['pet']['color']), ('Ready', 'green'))
    reconciling = {'metadata': {'name': 'redis-cache', 'namespace': tbi.NS}, 'spec': {'chart': {'version': '0.3.0'}},
                   'status': {'conditions': [{'type': 'Ready', 'status': 'False'}]}}
    out = overview('blueprint', {'state': 'all'}, cds={'items': CDS['items'] + [reconciling]})
    expect(f, 'a Reconciling blueprint is merged, not open', (t(out)['redis-cache']['label'], t(out)['redis-cache']['color']),
           ('Reconciling', 'gray'))
    # A step due, and a close the provider does not call merged: the step stays, never "Merged".
    pubs = {'items': PUBLISHES + [local_resource('publish-new-bp', 'blueprint', reg=True, days=1),
                                  local_resource('publish-maybe-bp', 'blueprint', reg=True, days=1),
                                  local_resource('publish-maybe-page', 'page', reg=True, days=1)]}
    prs = {'items': PRS['items'] + [pr('publish-new-bp', 95, 'closed', merged=True, merged_at=ago(0.5), days=1),
                                    pr('publish-maybe-bp', 96, 'closed', days=1), pr('publish-maybe-page', 210, 'closed', days=1)]}
    out = overview('blueprint', {'state': 'all'}, publishes=pubs, prs=prs)
    rows_ = t(out)
    expect(f, 'needs a step: To install, orange', (rows_['new-bp']['label'], rows_['new-bp']['color'], rows_['new-bp']['next']),
           ('To install', 'orange', 'Install'))
    expect(f, 'closed, merge not reported: the step if merged, no merge date',
           (rows_['maybe-bp']['label'], rows_['maybe-bp']['next'], rows_['maybe-bp']['mergedAt']),
           ('Closed · install if merged', 'Install if merged', ''))
    expect(f, '... and its card does not say Merged',
           next(c for c in rows(out) if c['name'] == 'maybe-bp')['description'].startswith('Closed, and the provider does not report'), True)
    tile = next(s for s in out['stats'] if s['title'] == 'To install')
    expect(f, 'a non-zero step stands out', (tile['value'], tile['color'], tile['highlight']), ('2', 'orange', True))
    page = overview('portal', {'state': 'all'}, publishes=pubs, prs=prs)
    expect(f, 'a page closed with no merge reported is not counted merged',
           (t(page)['maybe-page']['label'], next(s for s in page['stats'] if s['title'] == 'Merged')['value']),
           ('Closed · merge not reported', '1'))
    # Each pre-claim change request on exactly one page.
    expect(f, 'a pre-claim change request on any blueprints repository is a blueprint, not a page',
           ('acme-bp' in t(overview('blueprint', {'state': 'all'})), 'feat: acme-bp' in t(overview('portal', {'state': 'all'}))), (True, False))
    # A failed read: never zeros.
    out = overview('portal', {'state': 'all'}, publishes='ERROR')
    expect(f, 'a failed read: every figure says so, and opens nothing',
           {(s['value'], s['caption'], s['color'], s['route']) for s in out['stats']}, {('', 'could not be read', 'orange', '')})
    expect(f, '... and readError says why', out['readError'], 'simulated 403 forbidden')
    empty = overview('portal', {'state': 'all'}, username=None, publishes='ERROR', prs='ERROR')
    expect(f, 'nothing read: the list says it could not be read, not "nothing built"', empty['emptyText'],
           'What the Portal Builder built could not be read (simulated 403 forbidden). Reload the page, or ask an administrator for access.')
    missing = tbi.resolve(CH, 'portal-builder-overview', {k: v for k, v in responses().items() if k != 'publishes'},
                          {'username': 'admin', 'publishesError': {'message': 'the server could not find the requested resource (404)'}})
    expect(f, 'a 404 (no git-provider) is none, not a failed read', (missing['readError'], missing['stats'][0]['caption']),
           ('', 'no change request opened'))
    return f


CHECKS = [check_states_and_sources, check_what_the_review_asked, check_what_the_old_pages_had, check_drafts_are_the_callers, check_chips_and_routes, check_figures, check_cards,
          check_search_and_charts, check_widgets_declare_the_page]
CH = None


def main():
    global CH
    args = sys.argv[1:]
    crds_dir = args[args.index('--crds') + 1] if '--crds' in args else None
    CH = tbi.Chart(tbi.render(os.path.join(HERE, '..', 'helm', 'builders-page')))
    failed = 0
    for check in CHECKS:
        try:
            problems = check()
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError, StopIteration) as exc:
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
