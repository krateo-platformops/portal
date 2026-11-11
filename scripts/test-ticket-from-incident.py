#!/usr/bin/env python3
"""
test-ticket-from-incident — the service desk's half of a ticket raised from an incident, evaluated.

WHAT IT GUARDS. The incident page sends a person to /service-desk/new?incidentName=…&incidentNamespace=…
(incidents-page, restaction.incident-detail `raiseHref`). From there:
  - restaction.ticket-raise-context reads that Incident as the caller and the raise form is pre-filled
    from it: a summary, and a one-line detail curated from the incident's own fields (root cause,
    confidence, category, owner, each piece of evidence as `kind: ref`) and nothing else — no log
    line, no excerpt. It hides the two questions the incident answers and submits `submit-incident`,
    whose body is a Ticket with `kind: broken`, `objectRef` -> the Incident and the label
    `krateo.io/incident-uid: <uid>` the incident page selects on. A plain visit is the plain form,
    byte for byte where it matters: `submit`, no notice, nothing hidden.
  - an incident that cannot be read (403, 404 — errorKey is a LIST of message strings or apiserver
    Status objects, snowplow resolve.go accumulateErrorKey) pre-fills nothing and says so;
  - the ticket page reads the Incident through objectRef, as the caller, and shows an Incident card
    (state and how it ended, owner, root cause) in place of the generic object card;
  - it DERIVES Agentiko's "Incident resolved. Its checks passed at HH:MM UTC. Resolve this ticket?"
    entry when the incident's status.resolution.by is `verify`, in the slot for entries that are
    not in spec ($allActs), never in what the reply form patches back; not for a Closed incident,
    a Resolved or Closed ticket, or one the desk kept open (`krateo.io/resolve-proposal: kept`);
  - Resolve and Keep open follow the entry where the desk works tickets (deskActions), and do not
    exist where it does not: the entry is text only there.

HOW. As test-incident-reanalyze: the chart is rendered (both deskActions modes), each RESTAction is
resolved over 057-shaped fixtures by the model in test-platform-review.py — extended here so a failed
step's errorKey entry can be the apiserver Status OBJECT as well as a string, which is what snowplow
stores for an HTTP failure — and the widgets are evaluated over its output.

With --crds DIR or --pinned, every widget resolved here is validated against its CRD.
Usage: test-ticket-from-incident.py [--crds DIR | --pinned]. Exit code = failed checks.
"""
import copy
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHART = os.path.join(REPO, 'helm', 'service-desk-page')


def _load(mod, file):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tpr = _load('tpr', 'test-platform-review.py')
tbi = tpr.tbi
expect, apply_overrides, ref_template, expr = tpr.expect, tpr.apply_overrides, tpr.ref_template, tpr.expr

DESK_NS = 'krateo-service-desk'
RAISE_NS = 'krateo-service-desk-raise'
INC_NS = 'krateo-system'
INC_NAME = 'krateo-composition-reconcile-error-20261002-194519'
INC_UID = 'b20776b4-8c3c-4940-b981-7597e916f219'
RAISE_EX = {'incidentName': INC_NAME, 'incidentNamespace': INC_NS}


class Err:
    """A failed call: `value` is what snowplow appends under the step's errorKey — a message
    string, or the apiserver's Status decoded to a map (resolve.go: the asMap when AsMap succeeds)."""
    def __init__(self, value):
        self.value = value


def status(code, reason, message):
    return {'kind': 'Status', 'apiVersion': 'v1', 'metadata': {}, 'status': 'Failure',
            'message': message, 'reason': reason, 'code': code}


FORBIDDEN = status(403, 'Forbidden', f'incidents.observability.krateo.io "{INC_NAME}" is forbidden: '
                                     f'User "erin" cannot get resource "incidents" in API group '
                                     f'"observability.krateo.io" in the namespace "{INC_NS}"')
NOT_FOUND = status(404, 'NotFound', f'incidents.observability.krateo.io "{INC_NAME}" not found')


def render(desk_actions):
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(CHART, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        open(meta, 'w', encoding='utf-8').write(text.replace('CHART_VERSION', '0.0.0-dev').replace('APP_VERSION', '0.0.0-dev'))
        proc = subprocess.run(['helm', 'template', 'service-desk-page', staged, '--namespace', 'krateo-system',
                               '--set', f'deskActions={"true" if desk_actions else "false"}'],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise SystemExit(f'helm template failed:\n{proc.stderr.strip()}')
        return tbi.Chart([d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)])


def resolve(chart, name, responses, extras):
    """tpr.resolve's model of snowplow, plus Err(): a failed call whose errorKey entry is given as is.
    A step with no response is not served; an iterator yields one call per element and none for a
    non-array (setup.go createRequestOptions)."""
    ra = chart.get('RESTAction', name)
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
                assert step.get('continueOnError'), f'{name}: {sname} fails without continueOnError'
                data.setdefault(key, []).append(raw.value)
                continue
            data[sname] = raw
    return tbi.jq(ra['spec']['filter'], data), dialled


def widget(chart, kind, name, out, extras, label):
    return tbi.resolved_widget(chart, kind, name, out, extras, label)['spec'].get('widgetData', {})


def incident(state='Open', owner=None, evidence=None, resolution=None, statement=None, confidence='0.6',
             category='config', name=INC_NAME, uid=INC_UID):
    """Shaped like krateo-057's krateo-composition-reconcile-error-20261002-194519, with the owner
    fields incident-provider 0.10.0 adds (docs/api.md, examples/incident)."""
    rc = {'category': category, 'confidence': confidence,
          'statement': statement if statement is not None else (
              'Alert krateo-composition-reconcile-error in namespace krateo-system omits incident-controller from its '
              'ServiceName NOT IN filter in spec.where. Its own conflict logs are counted as composition failures.')}
    if owner is not None:
        rc['owner'] = owner
    if evidence is not None:
        rc['ownerEvidence'] = evidence
    st = {'state': state, 'rootCause': rc,
          'missingContext': ['Some reads failed; log queries returned no data']}
    if resolution:
        st['resolution'] = resolution
    return {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Incident',
            'metadata': {'name': name, 'namespace': INC_NS, 'uid': uid, 'creationTimestamp': '2026-10-02T19:45:19Z',
                         'labels': {'observability.krateo.io/alert': 'krateo-composition-reconcile-error'}},
            'spec': {'alertRef': {'name': 'krateo-composition-reconcile-error', 'namespace': INC_NS},
                     'trigger': 'alert', 'triggeredAt': '2026-10-02T19:45:19Z'},
            'status': st}


EVIDENCE = [{'kind': 'config', 'ref': 'observability.krateo.io/Alert/krateo-system/krateo-composition-reconcile-error',
             'note': 'The filter is a value of the customer\'s Alert.'},
            {'kind': 'log', 'ref': '/Pod/krateo-system/incident-controller-7d9f',
             'note': 'Its conflict logs are the lines the alert counts.'}]


# ---------------------------------------------------------------------------------------------
# The raise form, from an incident
# ---------------------------------------------------------------------------------------------

def form(chart, out, extras, label):
    return widget(chart, 'Form', 'ticket-new', out, dict(extras, username='maria.rossi@acme.example'), label)


def submit(w, values):
    act = [a for a in w['actions']['rest'] if a['id'] == w['submitActionId']][0]
    return act, apply_overrides(act['payload'], act.get('payloadToOverride'), values)


def check_plain_visit_is_the_plain_form(chart):
    f = []
    out, dialled = resolve(chart, 'ticket-raise-context', {}, {})
    expect(f, 'no parameters: nothing dialled', dialled, [])
    expect(f, 'no parameters: not from an incident', (out['fromIncident'], out['notice'], out['hide']), (False, '', []))
    w = form(chart, out, {}, 'plain')
    expect(f, 'plain: submit', w['submitActionId'], 'submit')
    expect(f, 'plain: nothing hidden, no notice, no prefill', (w['propertiesToHide'], w['notice'], w['initialValues']), ([], '', {}))
    expect(f, 'plain: Cancel goes to the queue', w['buttonConfig']['secondary']['navigateTo'], '/service-desk')
    act, body = submit(w, {'kind': 'I need access', 'summary': 'Read on team-alpha', 'detail': 'For the audit.',
                           'urgency': 'No particular date'})
    expect(f, 'plain: the body is the plain ticket', {k: body[k] for k in ('apiVersion', 'kind')},
           {'apiVersion': 'servicedesk.krateo.io/v1alpha1', 'kind': 'Ticket'})
    expect(f, 'plain: no incident label', body['metadata']['labels'], {'krateo.io/ticket-state': 'open'})
    expect(f, 'plain: kind from the form', (body['spec']['kind'], body['spec']['requester']), ('access', 'maria.rossi@acme.example'))
    # Only one parameter is not an incident: the iterator needs both.
    out, dialled = resolve(chart, 'ticket-raise-context', {}, {'incidentName': INC_NAME})
    expect(f, 'name without namespace: nothing dialled', dialled, [])
    return f


def check_raised_from_an_incident(chart):
    f = []
    inc = incident(owner='customer', evidence=EVIDENCE)
    out, dialled = resolve(chart, 'ticket-raise-context', {'incident': inc}, RAISE_EX)
    path = f'/apis/observability.krateo.io/v1alpha1/namespaces/{INC_NS}/incidents/{INC_NAME}'
    expect(f, 'reads exactly that incident', dialled, [('incident', path)])
    expect(f, 'one segment per value', path.count('/'), 7)
    expect(f, 'from the incident', (out['fromIncident'], out['submitActionId']), (True, 'submit-incident'))
    expect(f, 'summary: the statement\'s first sentence',
           out['initialValues']['summary'],
           'Alert krateo-composition-reconcile-error in namespace krateo-system omits incident-controller from its '
           'ServiceName NOT IN filter in spec.where')
    expect(f, 'detail: curated, one line, as frame E words it', out['initialValues']['detail'],
           f'Incident {INC_NAME} in {INC_NS}, opened 2026-10-02 19:45 UTC. Root cause (Agentiko, confidence 0.6, '
           'category config): Alert krateo-composition-reconcile-error in namespace krateo-system omits incident-controller '
           'from its ServiceName NOT IN filter in spec.where. Its own conflict logs are counted as composition failures. '
           'Owner: your organization. Evidence: config: observability.krateo.io/Alert/krateo-system/krateo-composition-reconcile-error; '
           'log: /Pod/krateo-system/incident-controller-7d9f.')
    expect(f, 'detail: no line breaks', '\n' in out['initialValues']['detail'], False)
    expect(f, 'detail: no note text (agent prose stays on the incident)', 'conflict logs are the lines' in out['initialValues']['detail'], False)
    w = form(chart, out, RAISE_EX, 'from incident')
    expect(f, 'form: submit-incident', w['submitActionId'], 'submit-incident')
    expect(f, 'form: the incident answers kind and object', w['propertiesToHide'], ['kind', 'objectName'])
    expect(f, 'form: the notice says where it goes', w['notice'],
           f'From incident {INC_NAME} in {INC_NS}. The ticket goes to this portal\'s service desk ({DESK_NS}); nothing leaves this cluster.')
    expect(f, 'form: Cancel goes back to the incident', w['buttonConfig']['secondary']['navigateTo'], f'/incidents/{INC_NS}/{INC_NAME}')
    act, body = submit(w, {'summary': out['initialValues']['summary'], 'detail': out['initialValues']['detail'],
                           'urgency': 'Needed this week'})
    expect(f, 'POSTs into the queue', (act['resourceRefId'], body['metadata']['namespace']), ('create-ticket', DESK_NS))
    expect(f, 'labels: state and the incident uid', body['metadata']['labels'],
           {'krateo.io/ticket-state': 'open', 'krateo.io/incident-uid': INC_UID})
    expect(f, 'every label value fits', all(len(v) <= 63 for v in body['metadata']['labels'].values()), True)
    expect(f, 'spec', {k: body['spec'][k] for k in ('kind', 'state', 'requester', 'objectRef', 'urgency')},
           {'kind': 'broken', 'state': 'Open', 'requester': 'maria.rossi@acme.example', 'urgency': 'Needed this week',
            'objectRef': {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Incident', 'name': INC_NAME, 'namespace': INC_NS}})
    expect(f, 'name is the per-user scheme', body['metadata']['name'].startswith('ticket-maria-rossi-acme-example-'), True)
    expect(f, 'back to the incident page', act['onSuccessNavigateTo'], f'/incidents/{INC_NS}/{INC_NAME}')
    expect(f, 'the person\'s edits win', submit(w, {'summary': 'Mine', 'detail': 'My words'})[1]['spec'].get('summary'), 'Mine')
    expect(f, 'within the CRD bounds', (len(body['spec']['summary']) <= 200, len(body['spec']['detail']) <= 8000), (True, True))
    # The form keys its cell on the incident, or one incident's text would pre-fill another's.
    for kind, name in (('Form', 'ticket-new'), ('Flex', 'page-ticket-new'), ('PageHeader', 'ticket-new-header')):
        expect(f, f'{name} keyExtras', chart.get(kind, name)['spec'].get('keyExtras'), ['incidentName', 'incidentNamespace'])
    return f


def check_owner_present_absent_unknown(chart):
    f = []
    want = {'customer': ' Owner: your organization. Evidence: ', 'krateo': ' Owner: Krateo. Evidence: '}
    for owner, tail in want.items():
        out, _ = resolve(chart, 'ticket-raise-context', {'incident': incident(owner=owner, evidence=EVIDENCE)}, RAISE_EX)
        expect(f, f'{owner}: the owner sentence', tail in out['initialValues']['detail'], True)
    out, _ = resolve(chart, 'ticket-raise-context', {'incident': incident(owner='unknown')}, RAISE_EX)
    expect(f, 'unknown: says not determined, no evidence', out['initialValues']['detail'].endswith(
        'Owner: not determined.'), True)
    # An older CRD (incident-provider 0.9.0, krateo-057 today): no owner at all, so nothing is said.
    out, _ = resolve(chart, 'ticket-raise-context', {'incident': incident()}, RAISE_EX)
    expect(f, 'absent: no owner sentence', 'Owner' in out['initialValues']['detail'], False)
    expect(f, 'absent: still raised from the incident', out['fromIncident'], True)
    # A value outside the enum is not an owner.
    out, _ = resolve(chart, 'ticket-raise-context', {'incident': incident(owner='vendor')}, RAISE_EX)
    expect(f, 'out-of-enum owner ignored', 'Owner' in out['initialValues']['detail'], False)
    # No analysis yet.
    out, _ = resolve(chart, 'ticket-raise-context', {'incident': incident(state='Analyzing', statement='')}, RAISE_EX)
    expect(f, 'no statement: the summary names the incident', out['initialValues']['summary'], f'Incident {INC_NAME}')
    expect(f, 'no statement: says so', 'Root cause: not determined yet.' in out['initialValues']['detail'], True)
    return f


def check_unreadable_incident_prefills_nothing(chart):
    f = []
    cases = (('[Status 403]', Err(FORBIDDEN)), ('[Status 404]', Err(NOT_FOUND)),
             ('[string]', Err(FORBIDDEN['message'])))
    for label, err in cases:
        out, _ = resolve(chart, 'ticket-raise-context', {'incident': err}, RAISE_EX)
        expect(f, f'{label}: not from the incident', (out['fromIncident'], out['submitActionId'], out['initialValues']), (False, 'submit', {}))
        expect(f, f'{label}: says it could not read it', out['notice'],
               f'Incident {INC_NAME} in {INC_NS} could not be read, so this form is not filled in from it. '
               'It may be gone, or you may not have access to it.')
        w = form(chart, out, RAISE_EX, f'unread {label}')
        expect(f, f'{label}: the plain form', (w['submitActionId'], w['propertiesToHide']), ('submit', []))
    # A response that is not the incident asked for (a stale cell, a renamed object) is not used.
    other = incident(name='some-other-incident')
    out, _ = resolve(chart, 'ticket-raise-context', {'incident': other}, RAISE_EX)
    expect(f, 'a different incident is not used', out['fromIncident'], False)
    nouid = incident()
    del nouid['metadata']['uid']
    out, _ = resolve(chart, 'ticket-raise-context', {'incident': nouid}, RAISE_EX)
    expect(f, 'no uid, no label to write: not from the incident', out['fromIncident'], False)
    return f


# ---------------------------------------------------------------------------------------------
# The ticket page
# ---------------------------------------------------------------------------------------------

def ticket(state='Open', labels=None, activity=None, ref=True):
    spec = {'kind': 'broken', 'state': state, 'summary': 'Alert omits incident-controller', 'requester': 'maria.rossi',
            'activity': activity if activity is not None else [
                {'at': '2026-10-05T12:00:00Z', 'who': 'luca.bianchi', 'kind': 'comment', 'text': 'Patched the filter.'}]}
    if ref:
        spec['objectRef'] = {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Incident', 'name': INC_NAME, 'namespace': INC_NS}
    return {'apiVersion': 'servicedesk.krateo.io/v1alpha1', 'kind': 'Ticket',
            'metadata': {'name': 'ticket-maria-rossi-1759600000', 'namespace': DESK_NS,
                         'labels': dict({'krateo.io/ticket-state': state.lower().replace(' ', '-'),
                                         'krateo.io/incident-uid': INC_UID}, **(labels or {}))},
            'spec': spec}


TICKET_EX = {'name': 'ticket-maria-rossi-1759600000', 'namespace': DESK_NS}
RESOLVED = {'at': '2026-10-05T12:21:33Z', 'by': 'verify'}


def page(chart, tkt, obj, label, desk):
    responses = {'ticket': tkt}
    if obj is not None:
        responses['object'] = obj
    out, dialled = resolve(chart, 'ticket-detail', responses, TICKET_EX)
    w = {}
    for kind, name in (('Flex', 'page-ticket-detail'), ('Card', 'ticket-activity-card'), ('Listy', 'ticket-activity'),
                       ('Card', 'ticket-incident-card'), ('Descriptions', 'ticket-incident-meta'),
                       ('Paragraph', 'ticket-incident-cause'), ('Button', 'ticket-incident-open')) + (
                       (('Button', 'ticket-keep-open'), ('Button', 'ticket-resolve')) if desk else ()):
        w[name] = widget(chart, kind, name, out, TICKET_EX, f'{label}: {kind} {name}')
    return out, dialled, w


def ids(items):
    return [i['resourceRefId'] for i in items]


def check_linked_incident_card(chart, desk):
    f = []
    inc = incident(state='Resolved', owner='krateo', evidence=EVIDENCE, resolution=RESOLVED)
    out, dialled, w = page(chart, ticket(), inc, 'linked', desk)
    expect(f, 'reads the incident through objectRef, as the caller',
           dialled[-1], ('object', f'/apis/observability.krateo.io/v1alpha1/namespaces/{INC_NS}/incidents/{INC_NAME}'))
    root = ids(w['page-ticket-detail']['items'])
    expect(f, 'the incident card, not the object card', ('ticket-incident-card' in root, 'ticket-object-card' in root), (True, False))
    expect(f, 'card rows', w['ticket-incident-meta']['items'],
           [{'label': 'Incident', 'value': INC_NAME},
            {'label': 'State', 'value': 'Resolved, by its checks at 2026-10-05 12:21 UTC'},
            {'label': 'Owner', 'value': 'Krateo'}])
    expect(f, 'card: the root cause, muted', (ids(w['ticket-incident-card']['items']), w['ticket-incident-cause']['type']),
           (['ticket-incident-meta', 'ticket-incident-cause'], 'secondary'))
    expect(f, 'card: no note when read', w['ticket-incident-card'].get('headerLeft'), '')
    expect(f, 'Open incident -> the incident page', w['ticket-incident-open']['actions']['navigate'][0]['path'], f'/incidents/{INC_NS}/{INC_NAME}')
    # Older CRD: no owner row, everything else as before.
    out, _, w = page(chart, ticket(), incident(state='Open'), 'no owner', desk)
    expect(f, 'no owner: no owner row, state only', w['ticket-incident-meta']['items'],
           [{'label': 'Incident', 'value': INC_NAME}, {'label': 'State', 'value': 'Open'}])
    out, _, w = page(chart, ticket(), incident(state='Open', owner='vendor'), 'out-of-enum owner', desk)
    expect(f, 'an owner outside the enum: no owner row', [i['label'] for i in w['ticket-incident-meta']['items']], ['Incident', 'State'])
    out, _, w = page(chart, ticket(), incident(state='Open', owner='unknown'), 'unknown owner', desk)
    expect(f, 'unknown owner: Not determined', w['ticket-incident-meta']['items'][-1], {'label': 'Owner', 'value': 'Not determined'})
    out, _, w = page(chart, ticket(), incident(state='Closed', resolution={'at': '2026-10-06T08:00:00Z', 'by': 'user'}), 'closed', desk)
    expect(f, 'closed by a person', w['ticket-incident-meta']['items'][1]['value'], 'Closed, by a person at 2026-10-06 08:00 UTC')
    out, _, w = page(chart, ticket(), incident(state='Closed', resolution={'at': '2026-10-06T08:00:00Z', 'by': 'new-reason'}), 'unknown by', desk)
    expect(f, 'a resolution.by it does not know reads as itself', w['ticket-incident-meta']['items'][1]['value'],
           'Closed, (new-reason) at 2026-10-06 08:00 UTC')
    # FAILED READS: errorKey is a list, each entry a Status object or a string.
    for label, err in (('[Status 403]', Err(FORBIDDEN)), ('[Status 404]', Err(NOT_FOUND)), ('[string]', Err('dial tcp: i/o timeout'))):
        out, _, w = page(chart, ticket(), err, f'unread {label}', desk)
        expect(f, f'{label}: still the incident card', ('ticket-incident-card' in ids(w['page-ticket-detail']['items'])), True)
        expect(f, f'{label}: name only', w['ticket-incident-meta']['items'], [{'label': 'Incident', 'value': INC_NAME}])
        expect(f, f'{label}: says why', w['ticket-incident-card']['headerLeft'],
               'This incident could not be read: it may be gone, or you may not have access to it.')
        expect(f, f'{label}: no cause, no proposal', (ids(w['ticket-incident-card']['items']), out['sections']['resolveProposal']),
               (['ticket-incident-meta'], False))
    # A ticket about something else keeps the generic card.
    other = ticket(ref=False)
    other['spec']['objectRef'] = {'apiVersion': 'composition.krateo.io/v1-2-0', 'kind': 'FireworksApp', 'name': 'fw', 'namespace': 'demo'}
    out, _, w = page(chart, other, {'metadata': {'name': 'fw'}, 'status': {'conditions': []}}, 'other ref', desk)
    root = ids(w['page-ticket-detail']['items'])
    expect(f, 'another kind: the object card', ('ticket-object-card' in root, 'ticket-incident-card' in root), (True, False))
    expect(f, 'another kind: no proposal', out['sections']['resolveProposal'], False)
    return f


PROPOSAL = 'Incident resolved. Its checks passed at 12:21 UTC. Resolve this ticket?'


def agentiko_rows(w):
    return [r for r in w['ticket-activity']['dataSource'] if r['tag'] == 'Agentiko']


def check_derived_resolve_entry(chart, desk):
    f = []
    resolved = incident(state='Resolved', owner='krateo', resolution=RESOLVED)
    out, _, w = page(chart, ticket(), resolved, 'shown', desk)
    expect(f, 'shown: the entry', [(r['text'], r['who'], r['at'], r['kindKey']) for r in agentiko_rows(w)],
           [(PROPOSAL, 'Agentiko', RESOLVED['at'], 'agentiko')])
    expect(f, 'shown: in time order, after the reply at 12:00',
           [r['who'] for r in w['ticket-activity']['dataSource']], ['luca.bianchi', 'Agentiko'])
    expect(f, 'shown: never in what the reply form patches back', [a['who'] for a in out['activityExisting']], ['luca.bianchi'])
    expect(f, 'shown: the activity card renders', out['sections']['activity'], True)
    card = ids(w['ticket-activity-card']['items'])
    if desk:
        expect(f, 'desk: Resolve and Keep open follow the conversation', card, ['ticket-activity', 'ticket-resolve-proposal-actions'])
        flex = chart.get('Flex', 'ticket-resolve-proposal-actions')['spec']['widgetData']
        expect(f, 'desk: Resolve then Keep open', ids(flex['items']), ['ticket-resolve', 'ticket-keep-open'])
        expect(f, 'desk: Resolve is the existing patch', w['ticket-resolve']['actions']['rest'][0]['payload'], out['resolvePatch'])
        keep = w['ticket-keep-open']['actions']['rest'][0]
        expect(f, 'desk: Keep open is a merge-patch of one label', (keep['headers'], keep['payload']),
               (['Content-Type: application/merge-patch+json'], {'metadata': {'labels': {'krateo.io/resolve-proposal': 'kept'}}}))
        ref = ref_template(chart, 'Button', 'ticket-keep-open', 'patch-ticket', out)
        expect(f, 'desk: Keep open patches this ticket', (ref['resource'], ref['name'], ref['namespace'], ref['verb']),
               ('tickets', TICKET_EX['name'], DESK_NS, 'PATCH'))
    else:
        expect(f, 'no desk: text only, no buttons', card, ['ticket-activity'])
        expect(f, 'no desk: the buttons do not exist', [d['metadata']['name'] for d in chart.docs
               if d['metadata']['name'] in ('ticket-keep-open', 'ticket-resolve-proposal-actions', 'ticket-resolve')], [])
    # Each condition, flipped, hides it.
    hidden = (
        ('incident still Open', ticket(), incident(state='Open', owner='krateo')),
        ('incident Verifying', ticket(), incident(state='Verifying', owner='krateo')),
        ('incident Closed by a person', ticket(), incident(state='Closed', resolution={'at': RESOLVED['at'], 'by': 'user'})),
        ('incident Closed, alert gone', ticket(), incident(state='Closed', resolution={'at': RESOLVED['at'], 'by': 'alert-gone'})),
        ('ticket Resolved', ticket(state='Resolved'), resolved),
        ('ticket Closed', ticket(state='Closed'), resolved),
        ('kept open', ticket(labels={'krateo.io/resolve-proposal': 'kept'}), resolved),
        ('incident unreadable', ticket(), Err(FORBIDDEN)),
    )
    for label, tkt, obj in hidden:
        out, _, w = page(chart, tkt, obj, label, desk)
        expect(f, f'{label}: no entry', (agentiko_rows(w), out['sections']['resolveProposal']), ([], False))
        expect(f, f'{label}: no buttons', ids(w['ticket-activity-card']['items']), ['ticket-activity'])
    # A ticket with no conversation still shows the proposal (the card is gated on $allActs).
    out, _, w = page(chart, ticket(activity=[]), resolved, 'no replies', desk)
    expect(f, 'no replies: the card renders for the entry alone',
           (out['sections']['activity'], [r['text'] for r in w['ticket-activity']['dataSource']]), (True, [PROPOSAL]))
    expect(f, 'no replies: the reply form still patches back nothing', out['activityExisting'], [])
    return f


CHECKS = (check_plain_visit_is_the_plain_form, check_raised_from_an_incident, check_owner_present_absent_unknown,
          check_unreadable_incident_prefills_nothing)
PAGE_CHECKS = (check_linked_incident_card, check_derived_resolve_entry)


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
    runs = []
    for desk in (True, False):
        chart = render(desk)
        mode = 'deskActions' if desk else 'no deskActions'
        runs += [(f'{mode}: {c.__name__}', (lambda c=c, chart=chart: c(chart))) for c in CHECKS]
        runs += [(f'{mode}: {c.__name__}', (lambda c=c, chart=chart, desk=desk: c(chart, desk))) for c in PAGE_CHECKS]
    failed = 0
    for name, run in runs:
        try:
            problems = run()
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {name.replace("check_", "").replace("_", " ")}')
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
