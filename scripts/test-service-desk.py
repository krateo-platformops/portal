#!/usr/bin/env python3
"""The service desk on Zendesk, over the chart's OWN jq.

The page files ZendeskRequests (zendesk-kog 0.2.1, zendesk.ogen.krateo.io/v2) and tracks them. It
renders the chart and runs the real programs (the three RESTActions and every widgetDataTemplate)
rather than keeping copies here: a test holding its own copy of the jq passes while the chart drifts.

WHAT IT PINS, because each is a way the page could lie:
  - A request's state comes from its Ready/Synced condition STATUS AND REASON, never the message.
    An unknown reason is shown verbatim and is never drawn as "Filing…" (progress).
  - The queue's chips: Active by default (hides Solved and Closed), then All, the six Zendesk
    statuses verbatim, and Mine (the raised-by label against the caller, slugged the same way the
    form slugs it).
  - ONE warning above the queue names each organisation whose requests can't be read from Zendesk;
    no row is marked, and those rows keep their last known status.
  - A cluster that does not serve the kind, a denied read and a failed read each say so in place of
    the queue, distinctly, and never render as an empty list.
  - The raise form: one configuration used silently, several as an Organisation select first, none
    as a sentence; the name `zr-<user>-<seconds>` equals krateo_reference; the body the frontend
    would POST lands in the configuration's namespace with configurationRef naming it; a 403 says
    the person may not raise requests for that organisation.
  - A request's page: the facts in the decided order, the line saying replies come later, and NO
    link to Zendesk: status.url and status.html_url are never read (the requester Zendesk records is
    the organisation's support account, so nobody could open it).
  - Raised from an incident: the form reads the Incident AS THE CALLER, pre-fills from it and labels
    the request `krateo.io/incident-uid`; an unreadable incident leaves the form empty with one line.
  - Nothing of the Ticket desk is left in the chart, and its namespace stays kept.

WHAT THIS CANNOT TEST: the userAccessFilter itself. Whether snowplow drops the requests a caller may
not list is snowplow's refilter against a live apiserver. This file covers the chart's half — what
the page SAYS about what it got back. The fixtures follow the field list of zendesk-kog 0.2.1; the
CRD was not served on krateo-057 when they were written.

With --crds DIR or --pinned, every widget resolved here is validated against its CRD.
Usage: test-service-desk.py [--crds DIR | --pinned]. SERVICE_DESK_CHART=<dir> renders another copy
of the chart (the control run against main's templates). Exit code = failed checks.
"""
import copy
import importlib.util
import json
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def _load(mod, file):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tbi = _load('tbi', 'test-builder-install.py')
CHART_DIR = os.environ.get('SERVICE_DESK_CHART') or os.path.join(REPO, 'helm', 'service-desk-page')
GROUP = 'zendesk.ogen.krateo.io'


class Err:
    """A failed step: snowplow appends it to the step's errorKey list (a Status object or a string)."""
    def __init__(self, value):
        self.value = value


def status(code, reason, message):
    return {'kind': 'Status', 'apiVersion': 'v1', 'status': 'Failure', 'code': code, 'reason': reason,
            'message': message}


NOT_SERVED = status(404, 'NotFound', 'the server could not find the requested resource')
DENIED = status(403, 'Forbidden', 'zendeskrequests.zendesk.ogen.krateo.io is forbidden: User "erin" cannot '
                'list resource "zendeskrequests" in API group "zendesk.ogen.krateo.io" at the cluster scope')
BROKEN = status(500, 'InternalError', 'etcdserver: request timed out')


def expr(s):
    s = (s or '').strip()
    return s[2:-1] if s.startswith('${') and s.endswith('}') else None


def resolve(chart, name, responses, extras):
    """snowplow's resolution of RESTAction `name`, with dependsOn iterators and errorKey lists.
    Returns (output, the paths it dialled)."""
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
            dialled.append(path)
            if sname not in responses:
                continue
            raw = responses[sname]
            if isinstance(raw, Err):
                assert step.get('continueOnError'), f'{sname} fails without continueOnError'
                data.setdefault(key, []).append(raw.value)
                continue
            data[sname] = raw
    return tbi.jq(ra['spec']['filter'], data), dialled


def widget(chart, kind, name, output, extras, label):
    return tbi.resolved_widget(chart, kind, name, output, extras, label)['spec']['widgetData']


def ids(items):
    return [i['resourceRefId'] for i in items]


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def cond(t, s, r, message=''):
    c = {'type': t, 'status': s, 'reason': r, 'lastTransitionTime': '2026-11-13T06:00:00Z'}
    if message:
        c['message'] = message
    return c


def zr(name, ns='acme-retail', conditions=None, zid=None, zstatus=None, subject=None, raised_by=None,
       created='2026-11-13T09:00:00Z', updated=None, cfg='zendesk-acme', html_url=None, priority='normal',
       kind='Bug', description='The authn-crd composition reports Ready=False.'):
    md = {'name': name, 'namespace': ns, 'creationTimestamp': created}
    if raised_by:
        md['labels'] = {'krateo.io/raised-by': raised_by[0]}
        md['annotations'] = {'krateo.io/raised-by': raised_by[1]}
    st = {}
    if conditions is not None:
        st['conditions'] = conditions
    if zid is not None:
        st['id'] = zid
        st['url'] = f'https://acme-retail.zendesk.com/api/v2/requests/{zid}.json'
        st['created_at'] = '2026-11-13T09:32:00Z'
    if zstatus is not None:
        st['status'] = zstatus
    if updated:
        st['updated_at'] = updated
    if html_url:
        st['html_url'] = html_url
    return {'apiVersion': f'{GROUP}/v2', 'kind': 'ZendeskRequest', 'metadata': md,
            'spec': {'configurationRef': {'name': cfg, 'namespace': ns}, 'subject': subject or f'Subject of {name}',
                     'description': description, 'priority': priority, 'kind': kind, 'krateo_reference': name},
            'status': st}


FILED = [cond('Ready', 'True', 'Available'), cond('Synced', 'True', 'ReconcileSuccess')]
UNREACHABLE = [cond('Ready', 'Unknown', 'ObserveFailed', 'get requests: 401 Unauthorized'),
               cond('Synced', 'False', 'ReconcileError', 'observe failed')]

# THE DOCUMENTED REASON TABLE, one case per row: krateo-blueprints/krateo-zendesk-kog docs/api.md,
# "Conditions a reader can key on" (PR #11, measured in the zendesk-kog 0.2.0 end-to-end run). The
# reasons are not zendesk-kog's own: Available, Creating, ObserveFailed and ReconcileError are
# provider-runtime's, set by rest-dynamic-controller; CorrelationTokenMissing is oasgen-provider's
# `dispatch` verb. A reason that changes with a rest-dynamic-controller or oasgen-provider release is
# changed HERE first, and the page (_zendesk.tpl, in both charts) follows.
#   (doc row, owner of the reasons, conditions, status.id, want state, pill label, pill colour, alert)
DOCUMENTED_REASONS = [
    ('filed and observed', 'rest-dynamic-controller',
     [cond('Ready', 'True', 'Available'), cond('Synced', 'True', 'ReconcileSuccess')], 48213,
     'filed', 'Open', 'slate', None),
    ('token expired or revoked (401), after filing', 'rest-dynamic-controller',
     [cond('Ready', 'Unknown', 'ObserveFailed', 'get requests: 401 Unauthorized'), cond('Synced', 'False', 'ReconcileError', 'observe failed')], 48190,
     'unreachable', 'Open', 'slate', 'warning'),
    ('token expired or revoked (401), before filing', 'rest-dynamic-controller',
     [cond('Ready', 'Unknown', 'ObserveFailed', 'get requests: 401 Unauthorized'), cond('Synced', 'False', 'ReconcileError', 'observe failed')], None,
     'unreachable', 'Not filed', 'gray', 'warning'),
    ('Zendesk refuses the filing (e.g. 422)', 'rest-dynamic-controller',
     [cond('Ready', 'False', 'Creating'), cond('Synced', 'False', 'ReconcileError', 'create request: 422 Unprocessable Entity: subject is too long')], None,
     'refused', 'Refused by Zendesk', 'red', 'error'),
    ('held: empty krateo_reference', 'oasgen-provider dispatch',
     [cond('Ready', 'False', 'CorrelationTokenMissing', 'krateo_reference is empty'), cond('Synced', 'True', 'ReconcileSuccess')], None,
     'held', 'Held', 'orange', 'warning'),
]

# The PORTAL'S OWN cases, which the documented table does not list: a request the provider has not
# looked at yet, and a reason nobody documented. An unknown reason is shown verbatim and never as
# progress — the safe direction when a provider release adds one.
PORTAL_CASES = [
    ('no conditions yet', [], None, 'filing', 'Filing…', 'gold', 'info'),
    ('no status at all', None, None, 'filing', 'Filing…', 'gold', 'info'),
    ('Ready=False/Creating, Synced=True', [cond('Ready', 'False', 'Creating'), cond('Synced', 'True', 'ReconcileSuccess')],
     None, 'filing', 'Filing…', 'gold', 'info'),
    # THE MESSAGE IS NOT READ: a Creating condition whose message happens to say "ObserveFailed" or
    # "refused" is still filing.
    ('Ready=False/Creating whose message says ObserveFailed',
     [cond('Ready', 'False', 'Creating', 'ObserveFailed: refused, token expired')], None, 'filing', 'Filing…', 'gold', 'info'),
    ('Ready=False/QuotaExceeded2028', [cond('Ready', 'False', 'QuotaExceeded2028', 'quota')], None, 'other', 'QuotaExceeded2028', 'gray', 'warning'),
    ('Ready=True/SomethingElse', [cond('Ready', 'True', 'SomethingElse')], 48001, 'other', 'SomethingElse', 'gray', 'warning'),
    ('Ready=Unknown/Unexpected', [cond('Ready', 'Unknown', 'Unexpected')], None, 'other', 'Unexpected', 'gray', 'warning'),
]

STATES = [(row, conds, zid, st, pill, colour, alert) for row, _owner, conds, zid, st, pill, colour, alert in DOCUMENTED_REASONS] + PORTAL_CASES


def state_fixture(conds, zid):
    """A request in one of the STATES, with a Zendesk status of `open` once it has a number."""
    obj = zr('zr-a', conditions=conds, zid=zid, zstatus='open' if zid else None,
             html_url=f'https://acme-retail.zendesk.com/hc/requests/{zid}' if zid else None)
    if conds is None:
        obj['status'] = {}
    return obj


ZD_WORDS = [('new', 'New', 'slate'), ('open', 'Open', 'slate'), ('pending', 'Pending', 'gold'),
            ('hold', 'On-hold', 'gold'), ('solved', 'Solved', 'faint'), ('closed', 'Closed', 'faint')]


def queue(chart, items=None, err=None, state=None, username='claude-user1@krateo.io'):
    extras = {'username': username}
    if state is not None:
        extras['state'] = state
    responses = {'requests': Err(err) if err else {'items': items or []}}
    out, _ = resolve(chart, 'zendesk-requests', responses, extras)
    return out, extras


def detail(chart, obj=None, err=None, name='zr-a', ns='acme-retail'):
    extras = {'name': name, 'namespace': ns}
    responses = {'request': Err(err) if err else obj}
    out, dialled = resolve(chart, 'zendesk-request', responses, extras)
    return out, extras, dialled


def check_state_mapping(chart):
    f = []
    for label, conds, zid, state, pill, colour, alert in STATES:
        obj = state_fixture(conds, zid)
        out, ex = queue(chart, [obj], state='all')
        rows = out.get('rows') or []
        expect(f, f'{label}: one row', len(rows), 1)
        if rows:
            expect(f, f'{label}: queue pill', (rows[0]['label'], rows[0]['color']), (pill, colour))
            if state != 'filing':
                expect(f, f'{label}: not drawn as progress', rows[0]['label'] == 'Filing…', False)
        cells = widget(chart, 'Table', 'service-desk-requests', out, ex, f'queue table ({label})')['dataSource']
        tag = [c for c in cells[0] if c['valueKey'] == 'status'][0] if cells else {}
        expect(f, f'{label}: the table draws the RA pill', (tag.get('stringValue'), tag.get('color')), (pill, colour))
        d, dex, _ = detail(chart, obj)
        expect(f, f'{label}: detail pill = queue pill', (d['pill']['label'], d['pill']['color']), (pill, colour))
        expect(f, f'{label}: detail alert', d['alert'].get('type') if d['alert'].get('show') else None, alert)
    # The six Zendesk statuses, verbatim, in their colours.
    for zs, word, colour in ZD_WORDS:
        out, _ = queue(chart, [zr('zr-a', conditions=FILED, zid=1, zstatus=zs)], state='all')
        expect(f, f'status {zs}', (out['rows'][0]['label'], out['rows'][0]['color']), (word, colour))
    out, _ = queue(chart, [zr('zr-a', conditions=FILED, zid=1, zstatus='escalated')], state='all')
    expect(f, 'a Zendesk status outside the six is shown as itself', (out['rows'][0]['label'], out['rows'][0]['color']), ('escalated', 'gray'))
    return f


def mixed():
    me = ('claude-user1-krateo.io', 'claude-user1@krateo.io')
    return [
        zr('zr-filing', conditions=[], raised_by=me, created='2026-11-13T09:59:00Z'),
        zr('zr-new', conditions=FILED, zid=48236, zstatus='new', updated='2026-11-13T09:50:00Z'),
        zr('zr-open', conditions=FILED, zid=48213, zstatus='open', updated='2026-11-13T09:40:00Z', raised_by=me),
        zr('zr-pending', ns='tern-logistics', cfg='zendesk-tern', conditions=UNREACHABLE, zid=48190, zstatus='pending',
           updated='2026-11-12T08:00:00Z'),
        zr('zr-hold', conditions=FILED, zid=48102, zstatus='hold', updated='2026-11-11T08:00:00Z'),
        zr('zr-solved', conditions=FILED, zid=47988, zstatus='solved', updated='2026-11-08T08:00:00Z', raised_by=me),
        zr('zr-closed', conditions=FILED, zid=47001, zstatus='closed', updated='2026-11-01T08:00:00Z'),
    ]


def check_chips(chart):
    f = []
    out, ex = queue(chart, mixed())
    expect(f, 'no ?state= selects Active', out['state'], 'active')
    expect(f, 'Active hides Solved and Closed, keeps Filing', [r['name'] for r in out['rows']],
           ['zr-filing', 'zr-new', 'zr-open', 'zr-pending', 'zr-hold'])
    expect(f, 'counts', out['counts'], {'active': 5, 'all': 7, 'new': 1, 'open': 1, 'pending': 1, 'hold': 1,
                                        'solved': 1, 'closed': 1, 'mine': 3})
    chips = widget(chart, 'Listy', 'service-desk-state-chips', out, ex, 'chips')['dataSource']
    expect(f, 'chip order and words', [c['label'] for c in chips],
           ['Active', 'All', 'New', 'Open', 'Pending', 'On-hold', 'Solved', 'Closed', 'Mine'])
    expect(f, 'Active is the selected chip', [c['label'] for c in chips if c['active']], ['Active'])
    expect(f, 'chip routes', chips[5]['route'], '/service-desk?state=hold')
    out, _ = queue(chart, mixed(), state='all')
    expect(f, 'All, newest change first', [r['name'] for r in out['rows']],
           ['zr-filing', 'zr-new', 'zr-open', 'zr-pending', 'zr-hold', 'zr-solved', 'zr-closed'])
    out, _ = queue(chart, mixed(), state='pending')
    expect(f, 'a status chip', [r['name'] for r in out['rows']], ['zr-pending'])
    out, _ = queue(chart, mixed(), state='mine')
    expect(f, 'Mine is every status the caller raised', [r['name'] for r in out['rows']], ['zr-filing', 'zr-open', 'zr-solved'])
    expect(f, 'Raised by shows the identity verbatim', out['rows'][0]['raisedBy'], 'claude-user1@krateo.io')
    out, _ = queue(chart, mixed(), state='mine', username='someone.else@krateo.io')
    expect(f, 'Mine for someone who raised nothing', (out['rows'], out['emptyTitle']), ([], 'You haven’t raised a request here.'))
    out, _ = queue(chart, mixed(), state='mine', username='')
    expect(f, 'Mine without an identity matches nothing (an unlabelled request is nobody\'s)', out['rows'], [])
    out, ex = queue(chart, mixed(), state='bogus')
    expect(f, 'a ?state= that is not one', (out['stateValid'], out['rows'], out['emptyTitle']), (False, [], 'That is not a request status'))
    root = widget(chart, 'Flex', 'page-service-desk', out, ex, 'root (bogus state)')
    expect(f, 'a bogus state still draws chips and says so', ids(root['items']),
           ['service-desk-header', 'service-desk-unreachable', 'service-desk-state-chips', 'service-desk-empty'])
    return f


def check_unreachable_warning(chart):
    f = []
    items = mixed() + [zr('zr-other-cfg', ns='tern-logistics', cfg='zendesk-tern-eu', conditions=UNREACHABLE)]
    out, ex = queue(chart, items, state='all')
    expect(f, 'one warning', out['unreachable']['show'], True)
    expect(f, 'it names each organisation, telling two configurations in one namespace apart',
           out['unreachable']['title'], 'Can’t reach Zendesk for tern-logistics (zendesk-tern) and tern-logistics (zendesk-tern-eu)')
    expect(f, 'it says who fixes it', 'Ask an administrator.' in out['unreachable']['description'], True)
    pending = [r for r in out['rows'] if r['name'] == 'zr-pending'][0]
    expect(f, 'no per-row mark: the row keeps its last known status', pending['label'], 'Pending')
    root = widget(chart, 'Flex', 'page-service-desk', out, ex, 'root (unreachable)')
    expect(f, 'the warning sits above the chips and the table', ids(root['items']),
           ['service-desk-header', 'service-desk-unreachable', 'service-desk-state-chips', 'service-desk-requests'])
    alert = widget(chart, 'Alert', 'service-desk-unreachable', out, ex, 'unreachable alert')
    expect(f, 'the alert is a warning', alert['type'], 'warning')
    out, ex = queue(chart, [zr('zr-a', conditions=UNREACHABLE, zid=48190, zstatus='pending')], state='all')
    expect(f, 'one organisation', out['unreachable']['title'], 'Can’t reach Zendesk for acme-retail')
    out, ex = queue(chart, [zr('zr-a', conditions=FILED, zid=1, zstatus='open')])
    root = widget(chart, 'Flex', 'page-service-desk', out, ex, 'root (healthy)')
    expect(f, 'nothing marks the healthy case', 'service-desk-unreachable' in ids(root['items']), False)
    return f


def check_failures_are_not_an_empty_queue(chart):
    f = []
    seen = set()
    for label, err, want_title, raise_kept in [
            ('kind not served', NOT_SERVED, 'Zendesk requests aren’t served on this cluster', False),
            ('kind not served, as a string', 'the server could not find the requested resource', 'Zendesk requests aren’t served on this cluster', False),
            ('denied', DENIED, 'Your access to Zendesk requests could not be confirmed', True),
            ('denied, as a string', '403 Forbidden', 'Your access to Zendesk requests could not be confirmed', True),
            ('failed', BROKEN, 'Zendesk requests could not be read', True)]:
        out, ex = queue(chart, err=err)
        root = widget(chart, 'Flex', 'page-service-desk', out, ex, f'root ({label})')
        expect(f, f'{label}: the sentence replaces chips and table', ids(root['items']), ['service-desk-header', 'service-desk-unavailable'])
        res = widget(chart, 'Result', 'service-desk-unavailable', out, ex, f'unavailable ({label})')
        expect(f, f'{label}: title', res['title'], want_title)
        seen.add(res['title'])
        head = widget(chart, 'PageHeader', 'service-desk-header', out, ex, f'header ({label})')
        expect(f, f'{label}: Raise a request', 'service-desk-raise' in ids(head['items']), raise_kept)
        expect(f, f'{label}: Ask Agentiko stays', 'service-desk-ask-agentiko' in ids(head['items']), True)
        expect(f, f'{label}: no empty-queue sentence', out['emptyTitle'], '')
        ask = widget(chart, 'Button', 'service-desk-ask-agentiko', out, ex, f'ask ({label})')
        prompt = urllib.parse.unquote(ask['actions']['navigate'][0]['path'].split('ask=')[1])
        expect(f, f'{label}: the prompt asks about the error', 'could not be read' in prompt, True)
    expect(f, 'the three failures are worded distinctly', len(seen), 3)
    out, _ = queue(chart, err=BROKEN)
    expect(f, 'a failed read carries its error', 'etcdserver: request timed out' in out['unavailable']['subTitle'], True)
    return f


def check_empty_queue(chart):
    f = []
    out, ex = queue(chart, [])
    root = widget(chart, 'Flex', 'page-service-desk', out, ex, 'root (empty)')
    expect(f, 'the chips stay, at 0, over the sentence', ids(root['items']),
           ['service-desk-header', 'service-desk-state-chips', 'service-desk-empty'])
    res = widget(chart, 'Result', 'service-desk-empty', out, ex, 'empty')
    expect(f, 'empty title', res['title'], 'No Zendesk requests yet.')
    expect(f, 'it says where a request goes', 'go to Zendesk' in res['subTitle'], True)
    head = widget(chart, 'PageHeader', 'service-desk-header', out, ex, 'header (empty)')
    expect(f, 'header actions', ids(head['items']), ['service-desk-ask-agentiko', 'service-desk-raise'])
    out, _ = queue(chart, [zr('zr-s', conditions=FILED, zid=1, zstatus='solved')])
    expect(f, 'Active empty but requests exist', (out['emptyTitle'], 'Choose All' in out['emptyText']), ('No active requests.', True))
    return f


def configs(*pairs):
    return {'items': [{'apiVersion': f'{GROUP}/v2', 'kind': 'ZendeskRequestConfiguration',
                       'metadata': {'name': n, 'namespace': ns}, 'spec': {'secretRef': {'name': 'zd-token', 'namespace': ns, 'key': 'token'}}}
                      for ns, n in pairs]}


def raise_ctx(chart, resp, username='claude-user1@krateo.io'):
    extras = {'username': username}
    out, _ = resolve(chart, 'request-raise-context', {'configs': resp}, extras)
    return out, extras


def check_raise_context(chart):
    f = []
    for label, resp, has_form, title in [
            ('none', configs(), False, 'No Zendesk configuration for your organisation — ask an administrator.'),
            ('kind not served', Err(NOT_SERVED), False, 'Zendesk requests aren’t served on this cluster'),
            ('read failed', Err(BROKEN), False, 'Your organisation’s Zendesk configuration could not be read'),
            ('one', configs(('acme-retail', 'zendesk-acme')), True, None),
            ('several', configs(('tern-logistics', 'zendesk-tern'), ('acme-retail', 'zendesk-acme')), True, None)]:
        out, ex = raise_ctx(chart, resp)
        root = widget(chart, 'Flex', 'page-request-new', out, {}, f'raise root ({label})')
        expect(f, f'{label}: page', ids(root['items']), ['request-new-header', 'request-new-form' if has_form else 'request-new-unavailable'])
        if not has_form:
            res = widget(chart, 'Result', 'request-new-unavailable', out, {}, f'raise unavailable ({label})')
            expect(f, f'{label}: says why', res['title'], title)
            continue
        schema = json.loads(out['stringSchema'])
        order = list(schema['properties'])
        want = (['organisation'] if label == 'several' else []) + ['subject', 'description', 'kind', 'priority']
        expect(f, f'{label}: field order', order, want)
        expect(f, f'{label}: every field up front', schema['required'], want)
        kind = schema['properties']['kind']
        expect(f, f'{label}: kind options', [(o['const'], o['title']) for o in kind['oneOf']],
               [('Bug', 'Something is broken'), ('FeatureRequest', 'A feature or change I’d like')])
        expect(f, f'{label}: kind enum beside oneOf, so today\'s Form submits a real value', kind['enum'], ['Bug', 'FeatureRequest'])
        prio = schema['properties']['priority']
        expect(f, f'{label}: priority', ([o['title'] for o in prio['oneOf']], prio['enum'], prio['default']),
               (['Low', 'Normal', 'High', 'Urgent'], ['low', 'normal', 'high', 'urgent'], 'normal'))
        expect(f, f'{label}: every priority has a hint', all(o.get('description') for o in prio['oneOf']), True)
        expect(f, f'{label}: no schema `format` on Description (a format is meaning, not a control)',
               'format' in schema['properties']['description'], False)
        # The presentation change refuses a field whose oneOf consts differ from its enum.
        for fld in [k for k in ('organisation', 'kind', 'priority') if k in schema['properties']]:
            node = schema['properties'][fld]
            expect(f, f'{label}: {fld} enum == the set of oneOf consts',
                   (sorted(node['enum']), len(node['enum']) == len(set(node['enum']))), (sorted(o['const'] for o in node['oneOf']), True))
        if label == 'several':
            org = schema['properties']['organisation']
            expect(f, 'several: options are namespace with the configuration under it, sorted',
                   [(o['const'], o['title'], o['description']) for o in org['oneOf']],
                   [('acme-retail/zendesk-acme', 'acme-retail', 'configuration zendesk-acme'),
                    ('tern-logistics/zendesk-tern', 'tern-logistics', 'configuration zendesk-tern')])
            expect(f, 'several: no default organisation', 'default' in org, False)
    return f


def frontend_body(form, values):
    """The frontend's buildPayload: action payload < served ref payload < overrides, then {} pruned.
    The served create-ref payload carries an EMPTY metadata.name and namespace (snowplow Reference,
    no omitempty) — which is why name and namespace must be overrides."""
    action = form['actions']['rest'][0]
    body = copy.deepcopy(action.get('payload') or {})
    served = {'apiVersion': f'{GROUP}/v2', 'kind': 'ZendeskRequest', 'metadata': {'name': '', 'namespace': ''}}

    def merge(a, b):
        for k, v in b.items():
            a[k] = merge(a.get(k) if isinstance(a.get(k), dict) else {}, v) if isinstance(v, dict) else v
        return a
    body = merge(body, served)
    for o in action.get('payloadToOverride') or []:
        e = expr(o['value'])
        v = tbi.jq(e, {'json': values}) if e is not None else o['value']
        tgt = body
        keys = o['name'].split('.')
        for k in keys[:-1]:
            tgt = tgt.setdefault(k, {})
        tgt[keys[-1]] = v

    def prune(n):
        if isinstance(n, dict):
            return {k: prune(v) for k, v in n.items() if not (isinstance(v, dict) and not prune(v))}
        return n
    return prune(body)


def check_form_submit(chart):
    f = []
    values = {'subject': 'Raise the CPU quota in payments-staging to 24 cores', 'description': 'The quota caps requests.cpu at 16.',
              'kind': 'FeatureRequest', 'priority': 'high'}
    out, ex = raise_ctx(chart, configs(('acme-retail', 'zendesk-acme')))
    form = widget(chart, 'Form', 'request-new', out, ex, 'form (one)')
    expect(f, 'the notice says how Zendesk records the requester',
           form.get('notice'), 'Zendesk records this request as coming from your organisation’s support account, not from you by name. Agents answer it in Zendesk.')
    expect(f, 'Description is multi-line through the textareas list', form.get('textareas'), [{'name': 'description', 'rows': 6}])
    expect(f, 'a plain visit: nothing pre-filled, no incident label, Cancel to the queue',
           (form.get('initialValues'), form['buttonConfig']['secondary']['navigateTo']), ({}, '/service-desk'))
    body = frontend_body(form, values)
    name = body['metadata'].get('name', '')
    expect(f, 'name scheme', bool(re.fullmatch(r'zr-claude-user1-krateo-io-[0-9]{9,}', name)), True)
    expect(f, 'krateo_reference is the same name', body['spec'].get('krateo_reference'), name)
    expect(f, 'created in the configuration\'s namespace', body['metadata'].get('namespace'), 'acme-retail')
    expect(f, 'configurationRef', body['spec'].get('configurationRef'), {'name': 'zendesk-acme', 'namespace': 'acme-retail'})
    expect(f, 'the fields the person typed', {k: body['spec'].get(k) for k in values}, values)
    expect(f, 'raised-by label (a slug) and annotation (verbatim)',
           (body['metadata'].get('labels'), body['metadata'].get('annotations')),
           ({'krateo.io/raised-by': 'claude-user1-krateo.io'}, {'krateo.io/raised-by': 'claude-user1@krateo.io'}))
    expect(f, 'the kind', (body.get('apiVersion'), body.get('kind')), (f'{GROUP}/v2', 'ZendeskRequest'))
    ref = chart.get('Form', 'request-new')['spec']['resourcesRefs']['items'][0]
    expect(f, 'the POST collection', (ref['apiVersion'], ref['resource'], ref['verb'], 'namespace' in ref), (f'{GROUP}/v2', 'zendeskrequests', 'POST', False))
    action = form['actions']['rest'][0]
    expect(f, 'on success, the new request\'s page',
           tbi.jq(expr(action['onSuccessNavigateTo']), {'json': body, 'response': {}}), f'/service-desk/acme-retail/{name}')
    msg403 = tbi.jq(expr(action['errorMessage']), {'json': body, 'response': status(403, 'Forbidden', 'zendeskrequests is forbidden')})
    expect(f, '403', msg403, 'You don’t have permission to raise requests for this organisation. Nothing was filed, and what you typed is still in the form.')
    msg409 = tbi.jq(expr(action['errorMessage']), {'json': body, 'response': status(409, 'AlreadyExists', 'exists')})
    expect(f, '409 is told apart', 'already exists' in msg409, True)
    # Several: the organisation the person chose decides namespace and configurationRef.
    out, ex = raise_ctx(chart, configs(('tern-logistics', 'zendesk-tern'), ('acme-retail', 'zendesk-acme')))
    form = widget(chart, 'Form', 'request-new', out, ex, 'form (several)')
    body = frontend_body(form, dict(values, organisation='tern-logistics/zendesk-tern'))
    expect(f, 'several: namespace and configurationRef from the choice',
           (body['metadata'].get('namespace'), body['spec'].get('configurationRef')),
           ('tern-logistics', {'name': 'zendesk-tern', 'namespace': 'tern-logistics'}))
    expect(f, 'several: the organisation is not sent as a spec field', 'organisation' in body['spec'], False)
    # The label the form writes is the one "Mine" compares against, for awkward names too.
    for user in ['Maria.Rossi@Acme-Retail.example', '-weird--user-', 'a' * 80 + '@x.io', 'oidc:https://idp/u/42']:
        out, ex = raise_ctx(chart, configs(('acme-retail', 'zendesk-acme')), username=user)
        form = widget(chart, 'Form', 'request-new', out, ex, f'form ({user[:20]})')
        label = (frontend_body(form, values)['metadata'].get('labels') or {}).get('krateo.io/raised-by', '')
        ok = bool(re.fullmatch(r'([a-z0-9]([-a-z0-9_.]{0,61}[a-z0-9])?)?', label))
        expect(f, f'{user[:24]}: a legal label value', ok, True)
        req = zr('zr-x', conditions=FILED, zid=1, zstatus='open', raised_by=(label, user))
        q, _ = queue(chart, [req], state='mine', username=user)
        expect(f, f'{user[:24]}: the form\'s label is Mine for the same person', [r['name'] for r in q['rows']], ['zr-x'])
    return f


def check_request_page(chart):
    f = []
    html = 'https://acme-retail.zendesk.com/hc/requests/48213'
    obj = zr('zr-a', conditions=FILED, zid=48213, zstatus='open', priority='high', subject='Composition authn-crd shows children pending',
             updated='2026-11-13T11:20:00Z', html_url=html, raised_by=('claude-user1-krateo.io', 'claude-user1@krateo.io'))
    d, ex, _ = detail(chart, obj)
    root = widget(chart, 'Flex', 'page-request-detail', d, ex, 'detail root (filed)')
    expect(f, 'filed: sections', ids(root['items']),
           ['request-detail-header', 'request-detail-pills', 'request-detail-facts', 'request-asked-card', 'request-replies-card'])
    head = widget(chart, 'PageHeader', 'request-detail-header', d, ex, 'detail header (filed)')
    expect(f, 'title is the subject', head['title'], 'Composition authn-crd shows children pending')
    expect(f, 'subtitle gives the Zendesk number', head['subtitle'], 'Zendesk request #48213, filed 2026-11-13 09:32 UTC.')
    facts = widget(chart, 'Descriptions', 'request-detail-facts', d, ex, 'detail facts (filed)')['items']
    expect(f, 'facts, in the decided order', [(i['label'], i['value']) for i in facts], [
        ('Status', 'Open'), ('Priority', 'High'), ('Kind', 'Bug'), ('Zendesk #', '#48213'),
        ('Created', '2026-11-13 09:32 UTC'), ('Updated', '2026-11-13 11:20 UTC'), ('Organisation', 'acme-retail'),
        ('Raised by', 'claude-user1@krateo.io'), ('Portal reference', 'zr-a')])
    expect(f, 'the header\'s only action is Ask Agentiko', ids(head['items']), ['request-ask-agentiko'])
    replies = chart.get('Paragraph', 'request-replies-text')['spec']['widgetData']['text']
    expect(f, 'replies are a later version, said once', replies, 'Replies from Zendesk will appear here in a later version.')
    txt = widget(chart, 'Paragraph', 'request-asked-text', d, ex, 'asked text')
    expect(f, 'the request text verbatim', txt['text'], obj['spec']['description'])
    expect(f, 'status.url and status.html_url are never read', ('api/v2/requests' in json.dumps(d), 'hc/requests' in json.dumps(d)), (False, False))
    expect(f, 'no Zendesk link anywhere in the chart', [x for x in ('html_url', 'Open in Zendesk')
                                                        if x in json.dumps([dd for dd in chart.docs if dd['kind'] != 'RESTAction'])], [])
    expect(f, 'the pill', widget(chart, 'Tag', 'request-status', d, ex, 'status tag')['label'], 'Open')
    # Just filed.
    d, ex, _ = detail(chart, zr('zr-a', conditions=[]))
    root = widget(chart, 'Flex', 'page-request-detail', d, ex, 'detail root (filing)')
    expect(f, 'filing: the alert, then the rest', ids(root['items']),
           ['request-detail-header', 'request-detail-pills', 'request-detail-alert', 'request-detail-facts', 'request-asked-card', 'request-replies-card'])
    al = widget(chart, 'Alert', 'request-detail-alert', d, ex, 'alert (filing)')
    expect(f, 'filing: says reload', (al['type'], al['title'], 'Reload in a moment.' in al['description']), ('info', 'Filing with Zendesk…', True))
    head = widget(chart, 'PageHeader', 'request-detail-header', d, ex, 'header (filing)')
    expect(f, 'filing: no number yet', head['subtitle'], 'Raised 2026-11-13 09:00 UTC. No Zendesk number yet.')
    facts = {i['label']: i['value'] for i in widget(chart, 'Descriptions', 'request-detail-facts', d, ex, 'facts (filing)')['items']}
    expect(f, 'filing: facts', (facts['Status'], facts['Zendesk #'], facts['Updated']), ('Not filed yet', '—', '—'))
    # Token expired, with a number: last known.
    d, ex, _ = detail(chart, zr('zr-a', conditions=UNREACHABLE, zid=48190, zstatus='pending', updated='2026-11-12T16:00:00Z',
                                html_url='https://acme-retail.zendesk.com/hc/requests/48190'))
    pills = widget(chart, 'Flex', 'request-detail-pills', d, ex, 'pills (expired)')
    expect(f, 'expired: Last known beside the pill', ids(pills['items']), ['request-status', 'request-last-known'])
    al = widget(chart, 'Alert', 'request-detail-alert', d, ex, 'alert (expired)')
    expect(f, 'expired: warning', (al['type'], al['title']), ('warning', 'Can’t reach Zendesk for this request'))
    expect(f, 'expired: who fixes it, and when it was last read',
           al['description'], 'Your organisation’s Zendesk token may have expired. Ask an administrator. The portal last read this request at 2026-11-13 06:00 UTC, so the status shown may be out of date.')
    facts = {i['label']: i['value'] for i in widget(chart, 'Descriptions', 'request-detail-facts', d, ex, 'facts (expired)')['items']}
    expect(f, 'expired: marked in the facts', (facts['Status'], facts['Updated']), ('Pending (last known)', '2026-11-12 16:00 UTC (last known)'))
    d, ex, _ = detail(chart, zr('zr-a', conditions=UNREACHABLE))
    expect(f, 'expired before filing', d['alert']['description'].endswith('Nothing has reached Zendesk yet.'), True)
    expect(f, 'expired before filing: no Last known', d['lastKnown'], False)
    # Refused: the provider's message verbatim.
    msg = 'create request: 422 Unprocessable Entity: subject is too long'
    d, ex, _ = detail(chart, zr('zr-a', conditions=[cond('Ready', 'False', 'Creating'), cond('Synced', 'False', 'ReconcileError', msg)]))
    al = widget(chart, 'Alert', 'request-detail-alert', d, ex, 'alert (refused)')
    expect(f, 'refused', (al['type'], al['title'], al['description']), ('error', 'Zendesk refused this request', msg + '. Ask an administrator.'))
    # An unknown reason: verbatim title.
    d, ex, _ = detail(chart, zr('zr-a', conditions=[cond('Ready', 'False', 'QuotaExceeded2028', 'quota')]))
    expect(f, 'unknown reason is the title', d['alert']['title'], 'Ready=False · QuotaExceeded2028')
    # Unreadable.
    for label, err, title in [('missing', status(404, 'NotFound', 'zendeskrequests.zendesk.ogen.krateo.io "zr-a" not found'), 'This request isn’t here'),
                              ('not served', NOT_SERVED, 'Zendesk requests aren’t served on this cluster'),
                              ('denied', status(403, 'Forbidden', 'zendeskrequests "zr-a" is forbidden'), 'You can’t read this request'),
                              ('failed', BROKEN, 'This request could not be read')]:
        d, ex, _ = detail(chart, err=err)
        root = widget(chart, 'Flex', 'page-request-detail', d, ex, f'detail root ({label})')
        expect(f, f'{label}: one sentence', ids(root['items']), ['request-detail-header', 'request-detail-unavailable'])
        expect(f, f'{label}: title', widget(chart, 'Result', 'request-detail-unavailable', d, ex, f'detail unavailable ({label})')['title'], title)
    return f


def check_request_read_is_gated_and_as_the_caller(chart):
    f = []
    _, _, dialled = detail(chart, obj=None, name='', ns='')
    expect(f, 'no route params, nothing dialled (prewarm)', dialled, [])
    _, _, dialled = detail(chart, obj=zr('zr-a'))
    expect(f, 'the one read', dialled, [f'/apis/{GROUP}/v2/namespaces/acme-retail/zendeskrequests/zr-a'])
    step = chart.get('RESTAction', 'zendesk-request')['spec']['api'][0]
    expect(f, 'a request is read with the caller\'s own credential', ('userAccessFilter' in step, 'endpointRef' in step), (False, False))
    for ra, res in [('zendesk-requests', 'zendeskrequests'), ('request-raise-context', 'zendeskrequestconfigurations')]:
        s = chart.get('RESTAction', ra)['spec']['api'][0]
        expect(f, f'{ra}: listed across namespaces, refiltered as the caller',
               (s['path'], s.get('userAccessFilter')), (f'/apis/{GROUP}/v2/{res}',
                                                       {'verb': 'list', 'group': GROUP, 'resource': res, 'namespaceFrom': '.metadata.namespace'}))
        expect(f, f'{ra}: no endpointRef', 'endpointRef' in s, False)
    return f


def check_ticket_desk_is_gone(chart):
    f = []
    text = json.dumps(chart.docs)
    for gone in ['servicedesk.krateo.io', 'raiseNamespace', 'deskActions', 'assign-to-me', 'keep-open', 'ticket-reply',
                 'ticket-activity', 'objectRef']:
        expect(f, f'no {gone} left', gone in text, False)
    expect(f, 'no CRD rendered', [d['metadata']['name'] for d in chart.docs if d['kind'] == 'CustomResourceDefinition'], [])
    nss = [d for d in chart.docs if d['kind'] == 'Namespace']
    expect(f, 'one namespace, still kept (it holds the orphaned Tickets)',
           [(d['metadata']['name'], d['metadata'].get('annotations', {}).get('helm.sh/resource-policy')) for d in nss],
           [('krateo-service-desk', 'keep')])
    rules = [r for d in chart.docs if d['kind'] == 'Role' for r in d['rules']]
    expect(f, 'the page grant reads widgets and RESTActions only', sorted({g for r in rules for g in r['apiGroups']}),
           ['templates.krateo.io', 'widgets.templates.krateo.io'])
    expect(f, 'no write verb', sorted({v for r in rules for v in r['verbs']}), ['get', 'list'])
    # Every queue widget reads zendesk-requests with the same identity and the same chip key, so they
    # cannot disagree about Mine.
    readers = [d for d in chart.docs if isinstance(d.get('spec'), dict)
               and (d['spec'].get('apiRef') or {}).get('name') == 'zendesk-requests']
    expect(f, 'the queue widgets reading zendesk-requests', len(readers), 8)
    for d in readers:
            expect(f, f'{d["kind"]}/{d["metadata"]["name"]}: identity and chip key',
                   (d['spec'].get('identityContext'), d['spec'].get('keyExtras')), (['username'], ['state']))
    return f


INC_NS, INC_NAME, INC_UID = 'krateo-system', 'krateo-composition-reconcile-error-20261002-194519', 'b20776b4-8c3c-4940-b981-7597e916f219'


def incident_obj(statement=True):
    """Shaped like krateo-057's krateo-composition-reconcile-error-20261002-194519, with the owner
    fields incident-provider 0.10.0 adds."""
    rc = {'category': 'config', 'confidence': '0.6', 'owner': 'customer',
          'ownerEvidence': [{'kind': 'component', 'ref': 'apps/Deployment/krateo-system/core-provider'}]}
    if statement:
        rc['statement'] = ('Alert krateo-composition-reconcile-error omits incident-controller from its ServiceName '
                           'NOT IN filter. So its own conflict logs count as composition failures.')
    return {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Incident',
            'metadata': {'name': INC_NAME, 'namespace': INC_NS, 'uid': INC_UID, 'creationTimestamp': '2026-10-02T19:45:19Z'},
            'spec': {'triggeredAt': '2026-10-02T19:45:19Z'}, 'status': {'state': 'Open', 'rootCause': rc}}


def check_raised_from_an_incident(chart):
    f = []
    ex = {'username': 'maria.rossi@acme.example', 'incidentName': INC_NAME, 'incidentNamespace': INC_NS}
    cfg = configs(('acme-retail', 'zendesk-acme'))
    out, dialled = resolve(chart, 'request-raise-context', {'configs': cfg, 'incident': incident_obj()}, ex)
    expect(f, 'reads exactly this incident', [p for p in dialled if 'incidents' in p],
           [f'/apis/observability.krateo.io/v1alpha1/namespaces/{INC_NS}/incidents/{INC_NAME}'])
    step = [st for st in chart.get('RESTAction', 'request-raise-context')['spec']['api'] if st['name'] == 'incident'][0]
    expect(f, 'as the caller', ('userAccessFilter' in step, 'endpointRef' in step), (False, False))
    form = widget(chart, 'Form', 'request-new', out, ex, 'form (from incident)')
    iv = form['initialValues']
    expect(f, 'Subject: the root cause\'s first sentence', iv.get('subject'),
           'Alert krateo-composition-reconcile-error omits incident-controller from its ServiceName NOT IN filter')
    expect(f, 'Description: the incident summary', iv.get('description', '').startswith(f'Incident {INC_NAME} in {INC_NS}, opened 2026-10-02 19:45 UTC. Root cause (Agentiko, confidence 0.6, category config): '), True)
    expect(f, 'Description: its page in the portal', iv.get('description', '').endswith(f' In the portal: /incidents/{INC_NS}/{INC_NAME}.'), True)
    expect(f, 'Kind: Bug', iv.get('kind'), 'Bug')
    expect(f, 'one line above the standing notice', form['notice'].startswith(f'From incident {INC_NAME} in {INC_NS}. '), True)
    expect(f, 'Cancel goes back to the incident', form['buttonConfig']['secondary']['navigateTo'], f'/incidents/{INC_NS}/{INC_NAME}')
    body = frontend_body(form, dict(iv, priority='high'))
    expect(f, 'the request carries the incident\'s uid', body['metadata']['labels'],
           {'krateo.io/raised-by': 'maria.rossi-acme.example', 'krateo.io/incident-uid': INC_UID})
    expect(f, 'filed through the organisation, as on the plain form', (body['metadata']['namespace'], body['spec']['configurationRef']),
           ('acme-retail', {'name': 'zendesk-acme', 'namespace': 'acme-retail'}))
    expect(f, 'every label value fits', all(len(v) <= 63 for v in body['metadata']['labels'].values()), True)
    keyed = chart.get('Form', 'request-new')['spec'].get('keyExtras')
    expect(f, 'each incident\'s form is its own cache cell', sorted(keyed or []), ['incidentName', 'incidentNamespace'])
    # Several configurations: the select, as on the plain form.
    out, _ = resolve(chart, 'request-raise-context', {'configs': configs(('a', 'za'), ('b', 'zb')), 'incident': incident_obj()}, ex)
    expect(f, 'several: the Organisation select comes first', list(json.loads(out['stringSchema'])['properties'])[0], 'organisation')
    # None: the ask-an-administrator page, even from an incident.
    out, _ = resolve(chart, 'request-raise-context', {'configs': configs(), 'incident': incident_obj()}, ex)
    expect(f, 'none: no form', out['hasForm'], False)
    # The incident cannot be read: the form opens empty, with one line.
    for label, inc in [('403', Err(status(403, 'Forbidden', 'incidents "x" is forbidden'))),
                       ('another incident came back', dict(incident_obj(), metadata=dict(incident_obj()['metadata'], name='other')))]:
        out, _ = resolve(chart, 'request-raise-context', {'configs': cfg, 'incident': inc}, ex)
        form = widget(chart, 'Form', 'request-new', out, ex, f'form (incident {label})')
        expect(f, f'{label}: empty form', form['initialValues'], {})
        expect(f, f'{label}: one line says why', form['notice'].startswith(f'Incident {INC_NAME} in {INC_NS} could not be read'), True)
        expect(f, f'{label}: no incident label', 'krateo.io/incident-uid' in (frontend_body(form, {'subject': 's', 'description': 'd', 'kind': 'Bug', 'priority': 'low'})['metadata'].get('labels') or {}), False)
        root = widget(chart, 'Flex', 'page-request-new', out, ex, f'raise root (incident {label})')
        expect(f, f'{label}: the form still opens', ids(root['items']), ['request-new-header', 'request-new-form'])
    # No statement yet: Subject names the incident.
    out, _ = resolve(chart, 'request-raise-context', {'configs': cfg, 'incident': incident_obj(statement=False)}, ex)
    expect(f, 'no statement: Subject', out['initialValues']['subject'], f'Incident {INC_NAME}')
    # No parameters (prewarm, a plain visit), or a name without a namespace: nothing dialled.
    _, dialled = resolve(chart, 'request-raise-context', {'configs': cfg}, {'username': 'x'})
    expect(f, 'no parameters: no incident read', [p for p in dialled if 'incidents' in p], [])
    _, dialled = resolve(chart, 'request-raise-context', {'configs': cfg}, {'username': 'x', 'incidentName': INC_NAME})
    expect(f, 'a name without a namespace: no incident read', [p for p in dialled if 'incidents' in p], [])
    # Carried over from test-ticket-from-incident.py, whose subject (pre-fill from an incident) is
    # still here: the owner sentence, one line, no agent note text, the person's edits win, a
    # returned incident with no uid is not used, and the bounds.
    def ctx_for(inc):
        return resolve(chart, 'request-raise-context', {'configs': cfg, 'incident': inc}, ex)[0]
    base = incident_obj()
    desc = ctx_for(base)['initialValues']['description']
    expect(f, 'customer owner', ' Owner: your organization.' in desc, True)
    expect(f, 'evidence, by kind and ref', ' Evidence: component: apps/Deployment/krateo-system/core-provider.' in desc, True)
    expect(f, 'one line until the textarea ships', '\n' in desc, False)
    noted = copy.deepcopy(base)
    noted['status']['rootCause']['ownerEvidence'][0]['note'] = 'conflict logs are the lines that matter'
    expect(f, 'no agent note text', 'conflict logs are the lines' in ctx_for(noted)['initialValues']['description'], False)
    for owner, want in (('unknown', ' Owner: not determined.'), ('vendor', None), (None, None)):
        inc = copy.deepcopy(base)
        if owner is None:
            del inc['status']['rootCause']['owner']
        else:
            inc['status']['rootCause']['owner'] = owner
        d2 = ctx_for(inc)['initialValues']['description']
        expect(f, f'owner {owner}', (want in d2) if want else ('Owner' in d2), bool(want))
    nouid = copy.deepcopy(base)
    del nouid['metadata']['uid']
    expect(f, 'no uid, no label to write: not from the incident', ctx_for(nouid)['fromIncident'], False)
    long_inc = copy.deepcopy(base)
    long_inc['status']['rootCause']['statement'] = 'x' * 400
    iv = ctx_for(long_inc)['initialValues']
    expect(f, 'within bounds', (len(iv['subject']) <= 150, len(iv['description']) <= 8000), (True, True))
    out, _ = resolve(chart, 'request-raise-context', {'configs': cfg, 'incident': base}, ex)
    form = widget(chart, 'Form', 'request-new', out, ex, 'form (edits win)')
    body = frontend_body(form, {'subject': 'Mine', 'description': 'My words', 'kind': 'FeatureRequest', 'priority': 'low'})
    expect(f, 'the person\'s edits win', (body['spec']['subject'], body['spec']['description'], body['spec']['kind']), ('Mine', 'My words', 'FeatureRequest'))
    return f


CHECKS = [check_state_mapping, check_chips, check_unreachable_warning, check_failures_are_not_an_empty_queue,
          check_empty_queue, check_raise_context, check_form_submit, check_raised_from_an_incident, check_request_page,
          check_request_read_is_gated_and_as_the_caller, check_ticket_desk_is_gone]


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
    chart = tbi.Chart(tbi.render(CHART_DIR))
    failed = 0
    for check in CHECKS:
        try:
            problems = check(chart)
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
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}{"" if crds_dir else "; resolved widgets NOT validated (no --crds / --pinned)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
