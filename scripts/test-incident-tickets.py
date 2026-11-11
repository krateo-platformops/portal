#!/usr/bin/env python3
"""
test-incident-tickets — the incident page's owner verdict, Agentiko's ticket proposal and the Tickets
card, evaluated end to end.

WHAT IT GUARDS (the approved mockup's frames A, C, E and F):
  - status.rootCause.owner and ownerEvidence (incident-provider 0.10.0) render under "Why did it
    happen": Category, Confidence, Owner ("Krateo" / "Your organization" / "Not determined"), the
    evidence (kind, ref, note), and for `unknown` one quiet line with status.missingContext. ABSENT —
    an older CRD, krateo-057 today — renders the page exactly as before.
  - Agentiko's proposal shows when the owner is `customer` or `krateo`, the incident is Open or
    Verifying, the ticket list came back holding no ticket for this incident, and it is not dismissed
    (`krateo.io/ticket-proposal: dismissed`); each condition, flipped, hides it. A ticket list that did
    not come back is never "no ticket".
  - "Review and raise" and "Raise a ticket" open service-desk-page's raise form with the incident in
    the query string, under the parameter names that form keys its cell on; Dismiss merge-patches the
    annotation onto this Incident, the target Close writes to.
  - the Tickets card lists the tickets labelled `krateo.io/incident-uid: <uid>`, newest first; says
    "No ticket yet."; tells a reader who is not the desk (403) who can see them; says a failed read
    failed; and does not render on an install with no service desk (404). errorKey is a LIST, each
    entry a message string or the apiserver's Status object (snowplow resolve.go accumulateErrorKey),
    and is read in both shapes — by code, by reason and by words.
  - the page keeps one agent entry point, "Ask Agentiko →": the proposal is not a second one.

HOW. As test-incident-reanalyze: the chart is rendered, incident-detail is resolved over fixtures
shaped like krateo-057's Incidents by the model in test-platform-review.py — extended so a failed
step's errorKey entry can be a Status object as well as a string — and the widgets are evaluated over
its output. service-desk-page is rendered too, for the raise link's contract with its form.

With --crds DIR or --pinned, every widget resolved here is validated against its CRD.
Usage: test-incident-tickets.py [--crds DIR | --pinned]. Exit code = failed checks.
"""
import copy
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(mod, file):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tpr = _load('tpr', 'test-platform-review.py')
tbi = tpr.tbi
expect, ref_template, expr = tpr.expect, tpr.ref_template, tpr.expr

NS = 'krateo-system'
DESK_NS = 'krateo-service-desk'
NAME = 'krateo-composition-reconcile-error-20261002-194519'
UID = 'b20776b4-8c3c-4940-b981-7597e916f219'
EX = {'name': NAME, 'namespace': NS}
TICKETS_PATH = (f'/apis/servicedesk.krateo.io/v1alpha1/namespaces/{DESK_NS}/tickets'
                f'?labelSelector=krateo.io%2Fincident-uid%3D{UID}')


class Err:
    """A failed call: `value` is what snowplow appends under the step's errorKey."""
    def __init__(self, value):
        self.value = value


def status(code, reason, message):
    return {'kind': 'Status', 'apiVersion': 'v1', 'metadata': {}, 'status': 'Failure',
            'message': message, 'reason': reason, 'code': code}


DENIED = status(403, 'Forbidden', f'tickets.servicedesk.krateo.io is forbidden: User "erin" cannot list resource '
                                  f'"tickets" in API group "servicedesk.krateo.io" in the namespace "{DESK_NS}"')
NO_CRD = status(404, 'NotFound', 'the server could not find the requested resource')


def resolve(chart, responses, extras=EX):
    """tpr.resolve's model of snowplow, plus Err() for a Status-object errorKey entry."""
    ra = chart.get('RESTAction', 'incident-detail')
    data = copy.deepcopy(extras)
    dialled = []
    for step in ra['spec']['api']:
        sname, key = step['name'], step.get('errorKey', 'error')
        it = (step.get('dependsOn') or {}).get('iterator')
        items = [data]
        if it:
            items = tbi.jq(it, data)
            items = items if isinstance(items, list) else []
        for item in items:
            path = tbi.jq(expr(step['path']), item) if expr(step['path']) else step['path']
            dialled.append((sname, path))
            if sname not in responses:
                continue
            raw = responses[sname]
            if isinstance(raw, Err):
                assert step.get('continueOnError'), f'{sname} fails without continueOnError'
                data.setdefault(key, []).append(raw.value)
                continue
            data[sname] = raw
    return tbi.jq(ra['spec']['filter'], data), dialled


def incident(state='Open', owner=None, evidence=None, annotations=None, missing=None, statement=True):
    """Shaped like krateo-057's krateo-composition-reconcile-error-20261002-194519, plus the owner
    fields incident-provider 0.10.0 adds (docs/api.md, examples/incident)."""
    rc = {'category': 'config', 'confidence': '0.6'}
    if statement:
        rc['statement'] = ('Alert krateo-composition-reconcile-error omits incident-controller from its ServiceName '
                           'NOT IN filter, so its own conflict logs count as composition failures.')
    if owner is not None:
        rc['owner'] = owner
    if evidence is not None:
        rc['ownerEvidence'] = evidence
    meta = {'name': NAME, 'namespace': NS, 'uid': UID, 'creationTimestamp': '2026-10-02T19:45:19Z',
            'labels': {'observability.krateo.io/alert': 'krateo-composition-reconcile-error'}}
    if annotations:
        meta['annotations'] = annotations
    return {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Incident', 'metadata': meta,
            'spec': {'alertRef': {'name': 'krateo-composition-reconcile-error', 'namespace': NS}, 'trigger': 'alert',
                     'triggeredAt': '2026-10-02T19:45:19Z'},
            'status': {'state': state, 'rootCause': rc, 'completedAt': '2026-10-02T19:55:00Z',
                       'missingContext': missing if missing is not None else
                       ['Some reads failed; log queries returned no data']}}


EVIDENCE = [{'kind': 'component', 'ref': 'apps/Deployment/krateo-system/core-provider',
             'note': 'The CRD generator is the only writer of the rejected field.'},
            {'kind': 'log', 'ref': '/Pod/krateo-system/core-provider-6c9d'}]


def ticket(name, created, uid=UID, state='Open', requester='maria.rossi'):
    return {'metadata': {'name': name, 'namespace': DESK_NS, 'creationTimestamp': created,
                         'labels': {'krateo.io/ticket-state': state.lower().replace(' ', '-'), 'krateo.io/incident-uid': uid}},
            'spec': {'kind': 'broken', 'state': state, 'summary': f'Summary of {name}', 'requester': requester}}


def listing(*items):
    return {'kind': 'TicketList', 'apiVersion': 'servicedesk.krateo.io/v1alpha1', 'items': list(items)}


EMPTY = listing()

WIDGETS = (('Flex', 'page-incident-detail'), ('Card', 'incident-why-card'), ('Descriptions', 'incident-owner-desc'),
           ('Paragraph', 'incident-owner-unknown'), ('Paragraph', 'incident-owner-evidence-title'),
           ('Listy', 'incident-owner-evidence'), ('Card', 'incident-ticket-proposal'),
           ('Button', 'incident-ticket-proposal-raise'), ('Button', 'incident-ticket-proposal-dismiss'),
           ('Card', 'incident-tickets-card'), ('Listy', 'incident-tickets-list'), ('Button', 'incident-raise-ticket'),
           ('Flex', 'incident-detail-actions'))


def page(chart, inc, tickets=EMPTY, label=''):
    responses = {'incident': inc, 'incidents': {'items': [inc] if isinstance(inc, dict) else []},
                 'incidentApplies': Err('the server could not find the requested resource'),
                 'alert': Err(f'alerts.observability.krateo.io "krateo-composition-reconcile-error" not found'),
                 'tickets': tickets}
    out, dialled = resolve(chart, responses)
    w = {n: tbi.resolved_widget(chart, k, n, out, EX, f'{label}: {k} {n}')['spec'].get('widgetData', {}) for k, n in WIDGETS}
    return out, dialled, w


def ids(items):
    return [i['resourceRefId'] for i in items]


NEW_IDS = {'incident-ticket-proposal', 'incident-tickets-card', 'incident-owner-desc', 'incident-owner-unknown',
           'incident-owner-evidence-title', 'incident-owner-evidence'}


def base_chart():
    """incidents-page at BASE_REF (e.g. origin/main before this feature), read with `git archive` so
    the working tree is never touched, or None when BASE_REF is not set. A one-off comparison for a
    reviewer, not a standing gate: every later change to the page would differ from a pinned base."""
    ref = os.environ.get('BASE_REF')
    if not ref:
        return None
    tmp = tempfile.mkdtemp(prefix='incidents-page-base-')
    try:
        archive = subprocess.run(['git', '-C', REPO, 'archive', ref, 'helm/incidents-page'], capture_output=True, check=True)
        subprocess.run(['tar', '-x', '-C', tmp], input=archive.stdout, check=True)
        return tbi.Chart(tbi.render(os.path.join(tmp, 'helm', 'incidents-page')))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_owner_absent_is_todays_page(chart, base):
    f = []
    # krateo-057 today: incident-provider 0.9.0 (no owner) and no service desk (Ticket CRD absent).
    out, _, w = page(chart, incident(), Err(NO_CRD), 'absent')
    root = ids(w['page-incident-detail']['items'])
    expect(f, 'no owner, no desk: nothing new on the page', sorted(NEW_IDS & set(root)), [])
    expect(f, 'no owner: the why card holds only its tabs', ids(w['incident-why-card']['items']), ['incident-why-tabs'])
    expect(f, 'no owner: nothing to show', (out['ownerItems'], out['ownerEvidenceRows'], out['ownerUnknownText'], out['sections']['proposal']),
           ([], [], '', False))
    # With a service desk, the only addition is the Tickets card, right after "Why did it happen".
    out, _, w = page(chart, incident(), EMPTY, 'absent, desk')
    with_desk = ids(w['page-incident-detail']['items'])
    expect(f, 'no owner, a desk: the same root plus Tickets after Why',
           with_desk, root[:root.index('incident-why-card') + 1] + ['incident-tickets-card'] + root[root.index('incident-why-card') + 1:])
    expect(f, 'no owner: no proposal, even with no ticket', out['sections']['proposal'], False)
    if base is not None:
        was, _ = resolve(base, {'incident': incident(), 'incidents': {'items': [incident()]},
                                'incidentApplies': Err('the server could not find the requested resource'),
                                'alert': Err('alerts.observability.krateo.io "krateo-composition-reconcile-error" not found')})
        changed = sorted(k for k in was if k != 'sections' and out.get(k) != was[k])
        changed += sorted(f'sections.{k}' for k in was['sections'] if out['sections'].get(k) != was['sections'][k])
        expect(f, f'BASE_REF: every field {os.environ["BASE_REF"]} emits is unchanged', changed, [])
        expect(f, f'BASE_REF: the root is {os.environ["BASE_REF"]}\'s',
               root, ids(tbi.jq(base.expression('Flex', 'page-incident-detail', 'items'), dict(EX, **was))))
        expect(f, f'BASE_REF: the why card is {os.environ["BASE_REF"]}\'s',
               ids(w['incident-why-card']['items']), ids(tbi.jq(base.expression('Card', 'incident-why-card', 'items'), dict(EX, **was))))
    return f


def check_owner_krateo_and_customer(chart):
    f = []
    for owner, line, noun in (('krateo', 'Krateo — the fix is in a component Krateo ships', 'Krateo'),
                              ('customer', 'Your organization — the fix is in your own configuration or services', 'your organization')):
        out, _, w = page(chart, incident(owner=owner, evidence=EVIDENCE), EMPTY, owner)
        expect(f, f'{owner}: category, confidence, owner', w['incident-owner-desc']['items'],
               [{'label': 'Category', 'value': 'config'}, {'label': 'Confidence', 'value': '0.6'}, {'label': 'Owner', 'value': line}])
        expect(f, f'{owner}: the why card', ids(w['incident-why-card']['items']),
               ['incident-owner-desc', 'incident-owner-evidence-title', 'incident-owner-evidence', 'incident-why-tabs'])
        expect(f, f'{owner}: evidence caption', w['incident-owner-evidence-title']['text'], f'Why {noun} · 2 pieces of evidence')
        expect(f, f'{owner}: evidence rows', w['incident-owner-evidence']['dataSource'],
               [{'kind': 'component', 'title': 'The CRD generator is the only writer of the rejected field.',
                 'ref': 'apps/Deployment/krateo-system/core-provider'},
                {'kind': 'log', 'title': '/Pod/krateo-system/core-provider-6c9d', 'ref': ''}])
        expect(f, f'{owner}: no unknown line', 'incident-owner-unknown' in ids(w['incident-why-card']['items']), False)
    out, _, w = page(chart, incident(owner='krateo', evidence=EVIDENCE[:1]), EMPTY, 'one')
    expect(f, 'one piece: singular', w['incident-owner-evidence-title']['text'], 'Why Krateo · 1 piece of evidence')
    out, _, w = page(chart, incident(owner='krateo'), EMPTY, 'no evidence')
    expect(f, 'owner without evidence: no caption, no list', ids(w['incident-why-card']['items']), ['incident-owner-desc', 'incident-why-tabs'])
    # A value outside the enum (a newer writer, a typo) is not an owner.
    out, _, w = page(chart, incident(owner='vendor', evidence=EVIDENCE), EMPTY, 'vendor')
    expect(f, 'out of enum: nothing', (out['sections']['owner'], out['sections']['proposal']), (False, False))
    # The Why card renders for an owner even when the statement is missing.
    out, _, w = page(chart, incident(owner='krateo', evidence=EVIDENCE, statement=False, missing=[]), EMPTY, 'no statement')
    expect(f, 'no statement: the why card still renders for the owner', 'incident-why-card' in ids(w['page-incident-detail']['items']), True)
    return f


def check_owner_unknown(chart):
    f = []
    out, _, w = page(chart, incident(owner='unknown', missing=['gateway access logs for 11:14 to 11:20 UTC']), EMPTY, 'unknown')
    expect(f, 'unknown: Not determined', w['incident-owner-desc']['items'][-1], {'label': 'Owner', 'value': 'Not determined'})
    expect(f, 'unknown: the quiet line, with what was missing', w['incident-owner-unknown']['text'],
           'Agentiko could not tell whose fix this is, so it does not suggest a ticket. Missing: gateway access logs for 11:14 to 11:20 UTC.')
    expect(f, 'unknown: muted', w['incident-owner-unknown']['type'], 'secondary')
    expect(f, 'unknown: the why card', ids(w['incident-why-card']['items']), ['incident-owner-desc', 'incident-owner-unknown', 'incident-why-tabs'])
    expect(f, 'unknown: never a proposal', out['sections']['proposal'], False)
    expect(f, 'unknown: the manual raise stays', ('incident-tickets-card' in ids(w['page-incident-detail']['items'])), True)
    out, _, w = page(chart, incident(owner='unknown', missing=[]), EMPTY, 'unknown, nothing missing')
    expect(f, 'unknown, nothing listed missing', w['incident-owner-unknown']['text'],
           'Agentiko could not tell whose fix this is, so it does not suggest a ticket.')
    # Evidence given with unknown is not presented as evidence FOR an owner.
    out, _, w = page(chart, incident(owner='unknown', evidence=EVIDENCE), EMPTY, 'unknown with evidence')
    expect(f, 'unknown: no "Why …" list', 'incident-owner-evidence' in ids(w['incident-why-card']['items']), False)
    return f


def check_proposal_conditions(chart):
    f = []
    base = dict(owner='customer', evidence=EVIDENCE)
    out, _, w = page(chart, incident(**base), EMPTY, 'proposal')
    root = ids(w['page-incident-detail']['items'])
    expect(f, 'shown: under the header block, before everything else', root[:3],
           ['incident-detail-page-header', 'incident-detail-header-block', 'incident-ticket-proposal'])
    card = w['incident-ticket-proposal']
    expect(f, 'shown: title and text', (card['title'], card['headerLeft']),
           ('Agentiko suggests opening a ticket for your platform team',
            'Owner: your organization, from 2 pieces of evidence. The ticket goes to this portal\'s service desk.'))
    expect(f, 'shown: marked as Agentiko\'s', (card['icon'], card['extra'], card['extraVariant']),
           ({'name': 'fa-wand-magic-sparkles', 'color': 'blue'}, 'Agentiko', 'tag'))
    out, _, w = page(chart, incident(state='Verifying', **base), EMPTY, 'verifying')
    expect(f, 'Verifying: shown', out['sections']['proposal'], True)
    # Krateo-owned: the same internal ticket, for now — the send to Krateo waits for the relay.
    out, _, w = page(chart, incident(owner='krateo', evidence=EVIDENCE), EMPTY, 'krateo')
    expect(f, 'krateo: shown, to the portal\'s own desk', (out['sections']['proposal'], w['incident-ticket-proposal']['headerLeft']),
           (True, 'Owner: Krateo, from 2 pieces of evidence. The ticket goes to this portal\'s service desk.'))
    expect(f, 'krateo: nothing about sending to Krateo', 'Krateo support' in str(w['incident-ticket-proposal']), False)
    flips = (
        ('owner unknown', incident(owner='unknown'), EMPTY),
        ('owner absent', incident(), EMPTY),
        ('Analyzing', incident(state='Analyzing', **base), EMPTY),
        ('Resolved', incident(state='Resolved', **base), EMPTY),
        ('Closed', incident(state='Closed', **base), EMPTY),
        ('a ticket exists', incident(**base), listing(ticket('ticket-maria-rossi-1', '2026-10-02T20:00:00Z'))),
        ('dismissed', incident(annotations={'krateo.io/ticket-proposal': 'dismissed'}, **base), EMPTY),
        ('tickets denied [Status]', incident(**base), Err(DENIED)),
        ('tickets denied [string]', incident(**base), Err(DENIED['message'])),
        ('tickets failed', incident(**base), Err('dial tcp 10.0.0.1:443: i/o timeout')),
        ('no service desk', incident(**base), Err(NO_CRD)),
    )
    for label, inc, tickets in flips:
        out, _, w = page(chart, inc, tickets, label)
        expect(f, f'{label}: no proposal', (out['sections']['proposal'], 'incident-ticket-proposal' in ids(w['page-incident-detail']['items'])),
               (False, False))
    # Another annotation value is not a dismissal.
    out, _, _ = page(chart, incident(annotations={'krateo.io/ticket-proposal': 'later'}, **base), EMPTY, 'other value')
    expect(f, 'an annotation that is not "dismissed": still shown', out['sections']['proposal'], True)
    return f


def check_proposal_actions(chart, desk_chart):
    f = []
    out, _, w = page(chart, incident(owner='customer', evidence=EVIDENCE), EMPTY, 'actions')
    flex = chart.get('Flex', 'incident-ticket-proposal-actions')['spec']['widgetData']
    expect(f, 'Review and raise, then Dismiss', ids(flex['items']), ['incident-ticket-proposal-raise', 'incident-ticket-proposal-dismiss'])
    raise_ = w['incident-ticket-proposal-raise']
    expect(f, 'Review and raise is the callout\'s primary', (raise_['label'], raise_['type']), ('Review and raise', 'primary'))
    href = raise_['actions']['navigate'][0]['path']
    path, _, query = href.partition('?')
    params = urllib.parse.parse_qs(query)
    expect(f, 'opens the service desk\'s raise form', path, '/service-desk/new')
    expect(f, 'with this incident', params, {'incidentName': [NAME], 'incidentNamespace': [NS]})
    expect(f, 'never a ?ask= (not an Agentiko entry point)', 'ask=' in href, False)
    # THE CONTRACT: the raise form must read these parameters, and key its cell on them. It lives in
    # service-desk-page, which has to ship first (the incident page links into it).
    try:
        desk_chart.get('RESTAction', 'ticket-raise-context')
    except LookupError:
        f.append('service-desk-page has no ticket-raise-context: its raise form does not read the incident yet '
                 '(the service-desk half of this feature ships first)')
        return f
    form = desk_chart.get('Form', 'ticket-new')
    expect(f, 'service-desk-page\'s form keys on the raise link\'s parameters',
           sorted(form['spec'].get('keyExtras') or []), sorted(params))
    ctx = desk_chart.get('RESTAction', 'ticket-raise-context')
    expect(f, 'and its context RESTAction reads them', all(f'.{p}' in ctx['spec']['api'][0]['dependsOn']['iterator'] for p in params), True)
    expect(f, 'Raise a ticket opens the same form', w['incident-raise-ticket']['actions']['navigate'][0]['path'], href)
    # THE ROUND TRIP: follow the link into the form, raise the ticket it builds, and read it back
    # through this page's own ticket list. The label the form writes must be the one this page
    # selects on, or a raised ticket never shows here and the proposal never goes away.
    raise_ex = {k: v[0] for k, v in params.items()}
    inc = incident(owner='customer', evidence=EVIDENCE)
    ra = desk_chart.get('RESTAction', 'ticket-raise-context')
    it = tbi.jq(ra['spec']['api'][0]['dependsOn']['iterator'], raise_ex)
    expect(f, 'the form reads exactly this incident', [tbi.jq(expr(ra['spec']['api'][0]['path']), i) for i in it],
           [f'/apis/observability.krateo.io/v1alpha1/namespaces/{NS}/incidents/{NAME}'])
    ctx = tbi.jq(ra['spec']['filter'], dict(raise_ex, incident=inc))
    form_w = tbi.resolved_widget(desk_chart, 'Form', 'ticket-new', ctx, dict(raise_ex, username='maria.rossi'),
                                 'round trip: Form ticket-new')['spec']['widgetData']
    act = [a for a in form_w['actions']['rest'] if a['id'] == form_w['submitActionId']][0]
    body = tpr.apply_overrides(act['payload'], act.get('payloadToOverride'), form_w['initialValues'])
    body['metadata']['creationTimestamp'] = '2026-10-02T20:00:00Z'
    expect(f, 'the raised ticket points at this incident', body['spec']['objectRef'],
           {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Incident', 'name': NAME, 'namespace': NS})
    expect(f, 'into the namespace this page lists', body['metadata']['namespace'], DESK_NS)
    out, _, w = page(chart, inc, listing(body), 'round trip')
    expect(f, 'read back: listed here', [r['name'] for r in w['incident-tickets-list']['dataSource']], [body['metadata']['name']])
    expect(f, 'read back: the proposal is gone', out['sections']['proposal'], False)
    dismiss = w['incident-ticket-proposal-dismiss']
    act = dismiss['actions']['rest'][0]
    expect(f, 'Dismiss is amber, not destructive', (dismiss['label'], dismiss.get('intent'), dismiss.get('danger')), ('Dismiss', 'dismiss', None))
    expect(f, 'Dismiss merge-patches one annotation', (act['headers'], act['payload']),
           (['Content-Type: application/merge-patch+json'], {'metadata': {'annotations': {'krateo.io/ticket-proposal': 'dismissed'}}}))
    ref = ref_template(chart, 'Button', 'incident-ticket-proposal-dismiss', 'dismiss-proposal', out)
    close = ref_template(chart, 'Button', 'incident-detail-close', 'close-incident', out)
    expect(f, 'Dismiss writes where Close writes', ref, dict(close, id='dismiss-proposal'))
    # Dismissed, read back: the proposal goes.
    out, _, _ = page(chart, incident(owner='customer', evidence=EVIDENCE, annotations=act['payload']['metadata']['annotations']), EMPTY, 'after dismiss')
    expect(f, 'the patch, applied, hides it', out['sections']['proposal'], False)
    # ONE agent entry point on the page: the header's "Ask Agentiko →".
    asks = [n for k, n in WIDGETS if k == 'Button' and str(w[n].get('label', '')).lower().startswith('ask ')]
    expect(f, 'the only Ask button is the header\'s', asks, [])
    diag = chart.get('Button', 'incident-detail-diagnose')['spec']['widgetData']
    expect(f, 'the header keeps "Ask Agentiko →"', (diag['label'], diag['intent']), ('Ask Agentiko →', 'agentiko'))
    return f


def check_tickets_card(chart):
    f = []
    out, dialled, w = page(chart, incident(), listing(
        ticket('ticket-maria-rossi-1', '2026-10-02T20:00:00Z', requester='maria.rossi'),
        ticket('ticket-luca-bianchi-2', '2026-10-03T08:30:00Z', state='In progress', requester='luca.bianchi'),
        # The label selector is the apiserver's; a row that does not carry this uid is not shown anyway.
        ticket('ticket-other', '2026-10-04T00:00:00Z', uid='another-uid')), 'tickets')
    expect(f, 'lists the tickets by this incident\'s uid, as the caller', [d for d in dialled if d[0] == 'tickets'], [('tickets', TICKETS_PATH)])
    expect(f, 'every label value it selects on fits', len(UID) <= 63, True)
    rows = w['incident-tickets-list']['dataSource']
    expect(f, 'newest first, this incident\'s only', [r['name'] for r in rows], ['ticket-luca-bianchi-2', 'ticket-maria-rossi-1'])
    expect(f, 'a row', rows[0], {'name': 'ticket-luca-bianchi-2', 'namespace': DESK_NS, 'summary': 'Summary of ticket-luca-bianchi-2',
                                  'state': 'In progress', 'raised': 'luca.bianchi · 2026-10-03 08:30 UTC'})
    tmpl = w['incident-tickets-list']['itemTemplate']
    expect(f, 'a row opens the ticket', tmpl['navigateTo'], '/service-desk/{namespace}/{name}')
    expect(f, 'states as the queue colours them', (tmpl['color']['map'], tmpl['color']['default']),
           ({'Open': 'slate', 'In progress': 'gold', 'Waiting on requester': 'gold', 'Resolved': 'faint', 'Closed': 'faint'}, 'slate'))
    card = w['incident-tickets-card']
    expect(f, 'rows: the list, no line', (ids(card['items']), card['headerLeft']), (['incident-tickets-list'], ''))
    expect(f, 'Raise a ticket in the header', (card['extraRefId'], w['incident-raise-ticket']['label'], w['incident-raise-ticket']['type']),
           ('incident-raise-ticket', 'Raise a ticket', 'default'))
    out, _, w = page(chart, incident(), EMPTY, 'none')
    expect(f, 'empty: No ticket yet', (ids(w['incident-tickets-card']['items']), w['incident-tickets-card']['headerLeft']), ([], 'No ticket yet.'))
    for label, err in (('[Status 403]', Err(DENIED)), ('[string 403]', Err(DENIED['message'])),
                       ('[Status 403, no word]', Err(status(403, 'Forbidden', 'access refused by policy')))):
        out, _, w = page(chart, incident(), err, label)
        expect(f, f'{label}: the desk-only line', (out['ticketsState'], w['incident-tickets-card']['headerLeft']),
               ('denied', 'The service desk can see tickets raised for this incident.'))
        expect(f, f'{label}: still offers the raise', ('incident-tickets-card' in ids(w['page-incident-detail']['items'])), True)
    for label, err in (('[Status 404]', Err(NO_CRD)), ('[string 404]', Err(NO_CRD['message']))):
        out, _, w = page(chart, incident(), err, label)
        expect(f, f'{label}: no service desk, no card', (out['ticketsState'], 'incident-tickets-card' in ids(w['page-incident-detail']['items'])),
               ('absent', False))
    out, _, w = page(chart, incident(), Err('dial tcp 10.0.0.1:443: i/o timeout'), 'timeout')
    expect(f, 'another failure: says so, never "No ticket yet"', w['incident-tickets-card']['headerLeft'],
           'The tickets raised for this incident could not be read: dial tcp 10.0.0.1:443: i/o timeout')
    # Two entries accumulate; both are read, in order, and a Status object is read by its message.
    resp = {'incident': incident(), 'incidents': {'items': []}, 'tickets': Err(status(500, 'InternalError', 'etcd timed out'))}
    ra_out, _ = resolve(chart, resp, EX)
    data = {'ticketsError': ['first failure', status(500, 'InternalError', 'etcd timed out')]}
    expect(f, 'one Status entry: its message', ra_out['ticketsNote'],
           'The tickets raised for this incident could not be read: etcd timed out')
    two = tbi.jq(chart.get('RESTAction', 'incident-detail')['spec']['filter'],
                 dict(EX, incident=incident(), incidents={'items': []}, **data))
    expect(f, 'two entries: both, in order', two['ticketsNote'],
           'The tickets raised for this incident could not be read: first failure; etcd timed out')
    # A missing incident dials no ticket list and shows no card.
    out, dialled, w = page(chart, Err(status(404, 'NotFound', f'incidents.observability.krateo.io "{NAME}" not found')), EMPTY, 'no incident')
    expect(f, 'no incident: no ticket list dialled', [d for d in dialled if d[0] == 'tickets'], [])
    expect(f, 'no incident: no card, no proposal', (out['sections']['tickets'], out['sections']['proposal']), (False, False))
    # Prewarm: no extras at all.
    out, dialled = resolve(chart, {}, {})
    expect(f, 'prewarm: no ticket list dialled', [d for d in dialled if d[0] == 'tickets'], [])
    return f


def main():
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        crds_dir = args[args.index('--crds') + 1]
    elif '--pinned' in args:
        pinned = _load('pinned', 'lint-pinned-crds.py')
        version, why = pinned.pinned_version()
        if not version:
            print(f'cannot determine the pinned frontend-crds version: {why}')
            return 1
        crds_dir = pinned.unpack(version)
        print(f'validating against the pinned frontend-crds {version}')
    chart = tbi.Chart(tbi.render(os.path.join(REPO, 'helm', 'incidents-page')))
    desk_chart = tbi.Chart(tbi.render(os.path.join(REPO, 'helm', 'service-desk-page')))
    runs = [(check_owner_absent_is_todays_page, (chart, base_chart())), (check_owner_krateo_and_customer, (chart,)),
            (check_owner_unknown, (chart,)), (check_proposal_conditions, (chart,)),
            (check_proposal_actions, (chart, desk_chart)), (check_tickets_card, (chart,))]
    failed = 0
    for check, a in runs:
        try:
            problems = check(*a)
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {check.__name__.replace("check_", "").replace("_", " ")}')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    total = len(runs)
    if crds_dir:
        total += 1
        problems = tbi.validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} resolved widgets validate against their CRDs')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}{"" if crds_dir else "; resolved widgets NOT validated (no --crds / --pinned)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
