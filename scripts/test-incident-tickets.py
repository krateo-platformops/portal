#!/usr/bin/env python3
"""
test-incident-tickets — the incident page's owner verdict, Agentiko's proposal to raise a Zendesk
request and the "Zendesk requests for this incident" card, evaluated end to end.

WHAT IT GUARDS:
  - status.rootCause.owner (incident-provider 0.10.0) is the Summary's "Who fixes it" row: "Krateo",
    "Your organization" or "Not determined". ABSENT — an older CRD — means no row and no proposal.
  - Agentiko's proposal shows when the owner is `customer` or `krateo`, the incident is Open or
    Verifying, the request list came back holding no ZendeskRequest for this incident, and it is not
    dismissed (`krateo.io/ticket-proposal: dismissed`, a name kept so past dismissals hold); each
    condition, flipped, hides it. A list that did not come back is never "no request".
  - "Review and raise" and "Raise a Zendesk request" open service-desk-page's raise form with the
    incident in the query string; FOLLOWED INTO THAT FORM, it reads the incident as the caller,
    pre-fills from it, files the ZendeskRequest through the organisation's configuration with the
    label `krateo.io/incident-uid`, and that request reads back on this page's card. Dismiss
    merge-patches the annotation onto this Incident, the target Close writes to.
  - the card lists the ZendeskRequests carrying `krateo.io/incident-uid: <uid>` across the namespaces
    the caller may list (a userAccessFilter), most recently updated first, with Status, Zendesk #,
    Subject and Updated, opening the service desk's page for each; says "No Zendesk request yet.";
    says a refused or failed read so, in both errorKey shapes; and does not render on a cluster that
    does not serve the kind (404).
  - THE STATE MAPPING IS ONE DEFINITION: incidents-page's _zendesk.tpl is byte-for-byte
    service-desk-page's, and every row of the documented reason table (test-service-desk.py
    DOCUMENTED_REASONS) comes out of this card exactly as out of the service-desk queue.
  - the page keeps one agent entry point, "Ask Agentiko →": the proposal is not a second one.

HOW. As test-incident-reanalyze: the chart is rendered, incident-detail is resolved over fixtures
shaped like krateo-057's Incidents by the model in test-platform-review.py — extended so a failed
step's errorKey entry can be a Status object as well as a string — and the widgets are evaluated over
its output. service-desk-page is rendered too, for the raise form the links open.

With --crds DIR or --pinned, every widget resolved here is validated against its CRD.
Usage: test-incident-tickets.py [--crds DIR | --pinned]. INCIDENTS_CHART / SERVICE_DESK_CHART=<dir>
render other copies (the control run against main). Exit code = failed checks.
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
tsd = _load('tsd', 'test-service-desk.py')
tbi = tpr.tbi
expect, ref_template, expr = tpr.expect, tpr.ref_template, tpr.expr

INCIDENTS_DIR = os.environ.get('INCIDENTS_CHART') or os.path.join(REPO, 'helm', 'incidents-page')
DESK_DIR = os.environ.get('SERVICE_DESK_CHART') or os.path.join(REPO, 'helm', 'service-desk-page')
NS = 'krateo-system'
ORG_NS = 'acme-retail'
NAME = 'krateo-composition-reconcile-error-20261002-194519'
UID = 'b20776b4-8c3c-4940-b981-7597e916f219'
EX = {'name': NAME, 'namespace': NS}
ZR_PATH = '/apis/zendesk.ogen.krateo.io/v2/zendeskrequests'


class Err:
    """A failed call: `value` is what snowplow appends under the step's errorKey."""
    def __init__(self, value):
        self.value = value


def status(code, reason, message):
    return {'kind': 'Status', 'apiVersion': 'v1', 'metadata': {}, 'status': 'Failure',
            'message': message, 'reason': reason, 'code': code}


DENIED = status(403, 'Forbidden', 'zendeskrequests.zendesk.ogen.krateo.io is forbidden: User "erin" cannot list resource '
                                  '"zendeskrequests" in API group "zendesk.ogen.krateo.io" at the cluster scope')
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


def zrequest(name, updated, uid=UID, ns=ORG_NS, zid=None, zstatus=None, conditions=None):
    """A ZendeskRequest raised from this incident (tsd.zr, labelled with the incident's uid)."""
    obj = tsd.zr(name, ns=ns, conditions=tsd.FILED if conditions is None else conditions, zid=zid, zstatus=zstatus,
                 updated=updated, created=updated)
    obj['metadata'].setdefault('labels', {})['krateo.io/incident-uid'] = uid
    return obj


def listing(*items):
    return {'kind': 'ZendeskRequestList', 'apiVersion': 'zendesk.ogen.krateo.io/v2', 'items': list(items)}


EMPTY = listing()

WIDGETS = (('Flex', 'page-incident-detail'), ('Card', 'incident-why-card'), ('Descriptions', 'incident-summary-desc'),
           ('Card', 'incident-ticket-proposal'),
           ('Button', 'incident-ticket-proposal-raise'), ('Button', 'incident-ticket-proposal-dismiss'),
           ('Card', 'incident-tickets-card'), ('Listy', 'incident-tickets-list'), ('Button', 'incident-raise-ticket'),
           ('Flex', 'incident-detail-actions'))


def page(chart, inc, tickets=EMPTY, label=''):
    responses = {'incident': inc, 'incidents': {'items': [inc] if isinstance(inc, dict) else []},
                 'incidentApplies': Err('the server could not find the requested resource'),
                 'alert': Err(f'alerts.observability.krateo.io "krateo-composition-reconcile-error" not found'),
                 'zendeskRequests': tickets}
    out, dialled = resolve(chart, responses)
    w = {n: tbi.resolved_widget(chart, k, n, out, EX, f'{label}: {k} {n}')['spec'].get('widgetData', {}) for k, n in WIDGETS}
    return out, dialled, w


def ids(items):
    return [i['resourceRefId'] for i in items]


NEW_IDS = {'incident-ticket-proposal', 'incident-tickets-card'}
OWNER_LABEL = 'Who fixes it'


def owner_row(w):
    """The Summary's "Who fixes it" value, or None when the row is absent."""
    rows = [i['value'] for i in w['incident-summary-desc']['items'] if i['label'] == OWNER_LABEL]
    return rows[0] if rows else None


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
    # krateo-057 today: incident-provider 0.9.0 (no owner), and ZendeskRequest not served (a 404).
    out, _, w = page(chart, incident(), Err(NO_CRD), 'absent')
    root = ids(w['page-incident-detail']['items'])
    expect(f, 'no owner, no desk: nothing new on the page', sorted(NEW_IDS & set(root)), [])
    expect(f, 'no owner: the why card holds only its tabs', ids(w['incident-why-card']['items']), ['incident-why-tabs'])
    expect(f, 'no owner: no Summary row, no proposal', (owner_row(w), out['sections']['proposal']), (None, False))
    # With the kind served, the only addition is the requests card, right after "Why did it happen".
    out, _, w = page(chart, incident(), EMPTY, 'absent, desk')
    with_desk = ids(w['page-incident-detail']['items'])
    expect(f, 'no owner, the kind served: the same root plus the requests card after Why',
           with_desk, root[:root.index('incident-why-card') + 1] + ['incident-tickets-card'] + root[root.index('incident-why-card') + 1:])
    expect(f, 'no owner: no proposal, even with no request', out['sections']['proposal'], False)
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
    for owner, line in (('krateo', 'Krateo: the fix belongs in a component Krateo ships'),
                        ('customer', 'Your organization: the fix belongs in your own configuration or services')):
        out, _, w = page(chart, incident(owner=owner, evidence=EVIDENCE), EMPTY, owner)
        expect(f, f'{owner}: the Summary rows', [i['label'] for i in w['incident-summary-desc']['items']], ['Why', 'Fix', OWNER_LABEL])
        expect(f, f'{owner}: who fixes it', owner_row(w), line)
        expect(f, f'{owner}: the why card holds only its tabs', ids(w['incident-why-card']['items']), ['incident-why-tabs'])
        expect(f, f'{owner}: no category, confidence or evidence notes on the page',
               [x for x in ('0.6', 'The CRD generator is the only writer') if x in str(w)], [])
    # A value outside the enum (a newer writer, a typo) is not an owner.
    out, _, w = page(chart, incident(owner='vendor', evidence=EVIDENCE), EMPTY, 'vendor')
    expect(f, 'out of enum: nothing', (owner_row(w), out['sections']['proposal']), (None, False))
    # The owner shows even when the analysis left nothing else structured.
    out, _, w = page(chart, incident(owner='krateo', evidence=EVIDENCE, statement=False, missing=[]), EMPTY, 'no statement')
    expect(f, 'no statement: the Summary still renders for the owner', 'incident-summary-card' in ids(w['page-incident-detail']['items']), True)
    expect(f, 'no statement, analysis done: Why says so', w['incident-summary-desc']['items'][0], {'label': 'Why', 'value': 'Not determined.'})
    return f


def check_owner_unknown(chart):
    f = []
    out, _, w = page(chart, incident(owner='unknown', missing=['gateway access logs for 11:14 to 11:20 UTC']), EMPTY, 'unknown')
    expect(f, 'unknown: Not determined', owner_row(w), 'Not determined')
    expect(f, 'unknown: the why card holds only its tabs', ids(w['incident-why-card']['items']), ['incident-why-tabs'])
    expect(f, 'unknown: never a proposal', out['sections']['proposal'], False)
    expect(f, 'unknown: the manual raise stays', ('incident-tickets-card' in ids(w['page-incident-detail']['items'])), True)
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
           ('Agentiko suggests raising a Zendesk request for this incident',
            'Owner: your organization. The request goes to your organisation’s Zendesk, from the service desk.'))
    expect(f, 'shown: marked as Agentiko\'s', (card['icon'], card['extra'], card['extraVariant']),
           ({'name': 'fa-wand-magic-sparkles', 'color': 'blue'}, 'Agentiko', 'tag'))
    out, _, w = page(chart, incident(state='Verifying', **base), EMPTY, 'verifying')
    expect(f, 'Verifying: shown', out['sections']['proposal'], True)
    # Krateo-owned: the same request, for now — the send to Krateo waits for the relay.
    out, _, w = page(chart, incident(owner='krateo', evidence=EVIDENCE), EMPTY, 'krateo')
    expect(f, 'krateo: shown, to the organisation\'s Zendesk', (out['sections']['proposal'], w['incident-ticket-proposal']['headerLeft']),
           (True, 'Owner: Krateo. The request goes to your organisation’s Zendesk, from the service desk.'))
    expect(f, 'krateo: nothing about sending to Krateo', 'Krateo support' in str(w['incident-ticket-proposal']), False)
    flips = (
        ('owner unknown', incident(owner='unknown'), EMPTY),
        ('owner absent', incident(), EMPTY),
        ('Analyzing', incident(state='Analyzing', **base), EMPTY),
        ('Resolved', incident(state='Resolved', **base), EMPTY),
        ('Closed', incident(state='Closed', **base), EMPTY),
        ('a request exists', incident(**base), listing(zrequest('zr-maria-rossi-1', '2026-10-02T20:00:00Z'))),
        ('a request exists, still filing', incident(**base), listing(zrequest('zr-maria-rossi-1', '2026-10-02T20:00:00Z', conditions=[]))),
        ('dismissed', incident(annotations={'krateo.io/ticket-proposal': 'dismissed'}, **base), EMPTY),
        ('requests denied [Status]', incident(**base), Err(DENIED)),
        ('requests denied [string]', incident(**base), Err(DENIED['message'])),
        ('requests failed', incident(**base), Err('dial tcp 10.0.0.1:443: i/o timeout')),
        ('kind not served', incident(**base), Err(NO_CRD)),
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
    expect(f, 'Raise a ticket opens the same form', w['incident-raise-ticket']['actions']['navigate'][0]['path'], href)
    expect(f, 'Raise a Zendesk request opens the same form', (w['incident-raise-ticket']['label'], w['incident-raise-ticket']['actions']['navigate'][0]['path']),
           ('Raise a Zendesk request', href))
    # THE ROUND TRIP: follow the link into service-desk-page's form, raise the request it builds, and
    # read it back through this page's own card. The label the form writes must be the one this page
    # selects on, or a raised request never shows here and the proposal never goes away.
    roots = [d for d in desk_chart.docs if d.get('kind') == 'Flex'
             and ((d.get('metadata') or {}).get('annotations') or {}).get('krateo.io/nav-path') == path]
    expect(f, 'the link lands on a route service-desk-page serves', len(roots), 1)
    form_doc = desk_chart.get('Form', 'request-new')
    expect(f, 'its form keys its cell on the link\'s parameters', sorted(form_doc['spec'].get('keyExtras') or []), sorted(params))
    raise_ex = dict({k: v[0] for k, v in params.items()}, username='maria.rossi@acme.example')
    inc = incident(owner='customer', evidence=EVIDENCE)
    ctx, dialled = tsd.resolve(desk_chart, 'request-raise-context',
                               {'configs': tsd.configs((ORG_NS, 'zendesk-acme')), 'incident': inc}, raise_ex)
    expect(f, 'the form reads exactly this incident', [d for d in dialled if '/incidents/' in d],
           [f'/apis/observability.krateo.io/v1alpha1/namespaces/{NS}/incidents/{NAME}'])
    form_w = tbi.resolved_widget(desk_chart, 'Form', 'request-new', ctx, raise_ex, 'round trip: Form request-new')['spec']['widgetData']
    body = tsd.frontend_body(form_w, dict(form_w['initialValues'], priority='high'))
    expect(f, 'the request carries this incident\'s uid', body['metadata']['labels'].get('krateo.io/incident-uid'), UID)
    expect(f, 'pre-filled from it', (body['spec']['kind'], body['spec']['description'].endswith(f'In the portal: /incidents/{NS}/{NAME}.')), ('Bug', True))
    expect(f, 'into the organisation\'s namespace, which this page lists across', body['metadata']['namespace'], ORG_NS)
    body['metadata']['creationTimestamp'] = '2026-10-02T20:00:00Z'
    body['status'] = {}
    out, _, w = page(chart, inc, listing(body), 'round trip')
    expect(f, 'read back: listed here, filing', [(r['name'], r['label']) for r in w['incident-tickets-list']['dataSource']],
           [(body['metadata']['name'], 'Filing…')])
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
        zrequest('zr-maria-rossi-1', '2026-10-02T20:00:00Z', zid=48213, zstatus='open'),
        zrequest('zr-luca-bianchi-2', '2026-10-03T08:30:00Z', ns='tern-logistics', conditions=[]),
        # The list holds every request the caller may see; one for another incident is not shown.
        zrequest('zr-other', '2026-10-04T00:00:00Z', uid='another-uid', zid=1, zstatus='new')), 'requests')
    expect(f, 'lists ZendeskRequests across namespaces', [d for d in dialled if d[0] == 'zendeskRequests'], [('zendeskRequests', ZR_PATH)])
    step = [st for st in chart.get('RESTAction', 'incident-detail')['spec']['api'] if st['name'] == 'zendeskRequests'][0]
    expect(f, 'refiltered as the caller', step.get('userAccessFilter'),
           {'verb': 'list', 'group': 'zendesk.ogen.krateo.io', 'resource': 'zendeskrequests', 'namespaceFrom': '.metadata.namespace'})
    rows = w['incident-tickets-list']['dataSource']
    expect(f, 'most recently updated first, this incident\'s only', [r['name'] for r in rows], ['zr-luca-bianchi-2', 'zr-maria-rossi-1'])
    expect(f, 'a filed row', rows[1], {'name': 'zr-maria-rossi-1', 'namespace': ORG_NS, 'subject': 'Subject of zr-maria-rossi-1',
                                       'label': 'Open', 'color': 'slate', 'zendesk': '#48213', 'updated': 'Updated 2026-10-02 20:00 UTC'})
    expect(f, 'a row still filing', (rows[0]['label'], rows[0]['color'], rows[0]['zendesk'], rows[0]['namespace']),
           ('Filing…', 'gold', 'No Zendesk # yet', 'tern-logistics'))
    tmpl = w['incident-tickets-list']['itemTemplate']
    expect(f, 'Status, Zendesk #, Subject, Updated', (tmpl['primaryText'], tmpl['subPrimaryText'], tmpl['secondaryText'], tmpl['subSecondaryText']),
           ('{subject}', '{zendesk}', '{label}', '{updated}'))
    expect(f, 'a row opens the service desk\'s page for it', tmpl['navigateTo'], '/service-desk/{namespace}/{name}')
    expect(f, 'the colour is the one _zendesk.tpl chose', (tmpl['color']['value'], all(k == v for k, v in tmpl['color']['map'].items())), ('{color}', True))
    card = w['incident-tickets-card']
    expect(f, 'title', card['title'], 'Zendesk requests for this incident')
    expect(f, 'rows: the list, no line', (ids(card['items']), card['headerLeft']), (['incident-tickets-list'], ''))
    expect(f, 'Raise a Zendesk request in the header', (card['extraRefId'], w['incident-raise-ticket']['label'], w['incident-raise-ticket']['type']),
           ('incident-raise-ticket', 'Raise a Zendesk request', 'default'))
    out, _, w = page(chart, incident(), EMPTY, 'none')
    expect(f, 'empty: No Zendesk request yet', (ids(w['incident-tickets-card']['items']), w['incident-tickets-card']['headerLeft']),
           ([], 'No Zendesk request yet.'))
    for label, err in (('[Status 403]', Err(DENIED)), ('[string 403]', Err(DENIED['message'])),
                       ('[Status 403, no word]', Err(status(403, 'Forbidden', 'access refused by policy')))):
        out, _, w = page(chart, incident(), err, label)
        expect(f, f'{label}: the refused line', (out['ticketsState'], w['incident-tickets-card']['headerLeft']),
               ('denied', 'Your access to Zendesk requests could not be confirmed.'))
        expect(f, f'{label}: still offers the raise', ('incident-tickets-card' in ids(w['page-incident-detail']['items'])), True)
    for label, err in (('[Status 404]', Err(NO_CRD)), ('[string 404]', Err(NO_CRD['message']))):
        out, _, w = page(chart, incident(), err, label)
        expect(f, f'{label}: kind not served, no card', (out['ticketsState'], 'incident-tickets-card' in ids(w['page-incident-detail']['items'])),
               ('absent', False))
    out, _, w = page(chart, incident(), Err('dial tcp 10.0.0.1:443: i/o timeout'), 'timeout')
    expect(f, 'another failure: says so, never "No Zendesk request yet"', w['incident-tickets-card']['headerLeft'],
           'The Zendesk requests raised for this incident could not be read: dial tcp 10.0.0.1:443: i/o timeout')
    # Two entries accumulate; both are read, in order, and a Status object is read by its message.
    resp = {'incident': incident(), 'incidents': {'items': []}, 'zendeskRequests': Err(status(500, 'InternalError', 'etcd timed out'))}
    ra_out, _ = resolve(chart, resp, EX)
    expect(f, 'one Status entry: its message', ra_out['ticketsNote'],
           'The Zendesk requests raised for this incident could not be read: etcd timed out')
    two = tbi.jq(chart.get('RESTAction', 'incident-detail')['spec']['filter'],
                 dict(EX, incident=incident(), incidents={'items': []},
                      zendeskRequestsError=['first failure', status(500, 'InternalError', 'etcd timed out')]))
    expect(f, 'two entries: both, in order', two['ticketsNote'],
           'The Zendesk requests raised for this incident could not be read: first failure; etcd timed out')
    # A missing incident shows no card and no proposal, whatever the list holds.
    out, _, w = page(chart, Err(status(404, 'NotFound', f'incidents.observability.krateo.io "{NAME}" not found')),
                     listing(zrequest('zr-x', '2026-10-02T20:00:00Z')), 'no incident')
    expect(f, 'no incident: no card, no proposal', (out['sections']['tickets'], out['sections']['proposal'], out['ticketRows']), (False, False, []))
    return f


def check_state_mapping_is_one_definition(chart, desk_chart):
    """The incident card and the service desk read a request through _zendesk.tpl. A `define` cannot
    cross charts and snowplow's shared jq modules cannot be shipped by a page chart (see the file),
    so incidents-page carries a copy — and this is what keeps it a copy."""
    f = []
    def read(chart_dir):
        path = os.path.join(chart_dir, 'templates', '_zendesk.tpl')
        return open(path, encoding='utf-8').read() if os.path.exists(path) else None
    a, b = read(INCIDENTS_DIR), read(DESK_DIR)
    expect(f, 'both charts carry _zendesk.tpl', (a is not None, b is not None), (True, True))
    expect(f, '_zendesk.tpl is byte-for-byte the same in both charts', a == b and a is not None, True)
    for row in tsd.DOCUMENTED_REASONS + [(c[0], 'portal') + c[1:] for c in tsd.PORTAL_CASES]:
        label, conds, zid = row[0], row[2], row[3]
        obj = tsd.state_fixture(conds, zid)
        obj['metadata'].setdefault('labels', {})['krateo.io/incident-uid'] = UID
        out, _, _ = page(chart, incident(), listing(obj), f'parity ({label})')
        desk, _ = tsd.queue(desk_chart, [obj], state='all')
        expect(f, f'{label}: the card says what the service desk says',
               [(r['label'], r['color']) for r in out['ticketRows']], [(r['label'], r['color']) for r in desk['rows']])
        expect(f, f'{label}: and what the documented table says', [(r['label'], r['color']) for r in out['ticketRows']], [(row[5], row[6])])
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
    chart = tbi.Chart(tbi.render(INCIDENTS_DIR))
    desk_chart = tbi.Chart(tbi.render(DESK_DIR))
    runs = [(check_owner_absent_is_todays_page, (chart, base_chart())), (check_owner_krateo_and_customer, (chart,)),
            (check_owner_unknown, (chart,)), (check_proposal_conditions, (chart,)),
            (check_proposal_actions, (chart, desk_chart)), (check_tickets_card, (chart,)),
            (check_state_mapping_is_one_definition, (chart, desk_chart))]
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
