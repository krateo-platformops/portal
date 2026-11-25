#!/usr/bin/env python3
"""A child's readiness, over the chart's OWN jq.

A Ready condition is True, False or Unknown. `Unknown` used to fall off the end of the state chain
into "pending", so a child whose provider never reported readiness rendered as CONVERGING — it
looked like it was still working. provider-runtime's framing, and the reason this matters more than
a wrong label: an operator watching a stuck resource waits instead of looking.

The two Unknown cases are split by reason because their causes and fixes differ. `NotObserved` with
Synced=False is "we cannot observe it"; the same reason with Synced=TRUE is a contract breach — the
provider finished Observe and never set Ready — for which the runtime records a Warning event whose
reason is verbatim `ReadyNotSetAfterObserve`.

THE ASSERTION THAT CANNOT BE REPLACED BY A COMMENT: every state the RA can emit must appear in the
relations list's colour map. A state missing from it falls through to the raw state string as a
palette colour NAME, and getColorCode returns near-black for a name it does not know — so the row
renders as dark ink and nothing reports it.
"""
import json
import sys
import os
import subprocess
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHART = os.path.join(REPO, 'helm', 'compositions-page')
JQ = os.environ.get('JQ', 'jq')


def render():
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', CHART, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy, '--set', 'namespace=krateo-compositions'],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def named(docs, name):
    for d in docs:
        if d.get('metadata', {}).get('name') == name:
            return d
    raise AssertionError(f'{name} not in the rendered chart')


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


def conds(*triples):
    return [{'type': t, 'status': s, 'reason': r} for t, s, r in triples]


# One managed child, fetched, with these conditions. The shapes mirror what the upstream stages
# project: `found[]` carries the managed entry, `resources` the fetched object.
def payload(conditions, getpath='/api/v1/namespaces/ns/configmaps/c', kind='configmaps', grp=None, phase=''):
    grp, rns = ('apiextensions.k8s.io', '') if kind == 'customresourcedefinitions' else (grp or 'core', 'ns')
    return {
        'found': [{'_cn': 'comp', '_cns': 'ns', '_rns': rns, '_name': 'c', '_kind': kind,
                   '_grp': grp, '_ver': 'v1', '_cluster': rns == '', '_getpath': getpath,
                   '_wh': True, '_whMsg': ''}],
        'resources': [{'rname': 'c', 'rns': rns, 'rkind': 'ConfigMap', 'conditions': conditions, 'phase': phase}],
        'name': 'comp', 'namespace': 'ns',
    }


CASES = [
    # (label, conditions, want state, want stateLabel)
    ('Ready=True, Synced=True',   conds(('Ready', 'True', 'Available'), ('Synced', 'True', 'ReconcileSuccess')), 'healthy', 'Healthy'),
    ('Ready=True, Synced=False',  conds(('Ready', 'True', 'Available'), ('Synced', 'False', 'ReconcileError')),  'drift',   'Drift'),
    ('Ready=False',               conds(('Ready', 'False', 'Unavailable'),),                                     'failed',  'NotReady'),
    # THE REGRESSION. Each of these three was "pending" — i.e. rendered as progress.
    ('Unknown/NotObserved + Synced=False',
     conds(('Ready', 'Unknown', 'NotObserved'), ('Synced', 'False', 'ReconcileError')), 'notObserved', 'NotObserved'),
    ('Unknown/NotObserved + Synced=True',
     conds(('Ready', 'Unknown', 'NotObserved'), ('Synced', 'True', 'ReconcileSuccess')), 'readyNotSet', 'ReadyNotSetAfterObserve'),
    ('Unknown, empty reason',
     conds(('Ready', 'Unknown', ''),), 'unknown', 'Unknown'),
    # ObserveFailed is the SAME FACT as NotObserved, from unstructured-runtime rather than
    # provider-runtime. Same state and same rank; the label stays verbatim per reason, because
    # greppability is the point of the rule and an operator reading `ObserveFailed` can find it.
    ('Unknown/ObserveFailed + Synced=False',
     conds(('Ready', 'Unknown', 'ObserveFailed'), ('Synced', 'False', 'ReconcileError')), 'notObserved', 'ObserveFailed'),
    ('Unknown/ObserveFailed + Synced=True',
     conds(('Ready', 'Unknown', 'ObserveFailed'), ('Synced', 'True', 'ReconcileSuccess')), 'readyNotSet', 'ReadyNotSetAfterObserve'),
    # Ready=False is not one thing either, and this was the MIRROR of the Unknown defect: these two
    # are genuinely in progress and were rendering red.
    ('Ready=False/Pending',  conds(('Ready', 'False', 'Pending'),),  'converging', 'Pending'),
    ('Ready=False/Updating', conds(('Ready', 'False', 'Updating'),), 'converging', 'Updating'),
    # And these two are TERMINAL — the controller has stopped and a human must act — where they
    # previously rendered identically to a transient failure.
    ('Ready=False/DriftNotConverging',
     conds(('Ready', 'False', 'DriftNotConverging'),), 'terminal', 'DriftNotConverging'),
    ('Ready=False/AuthorizationDenied',
     conds(('Ready', 'False', 'AuthorizationDenied'),), 'terminal', 'AuthorizationDenied'),
    # The set is OPEN: a Ready=False reason nothing here lists must stay a failure rather than
    # become progress. That direction is the safe one and it is asserted, not assumed.
    ('Ready=False/SomeNewReason',
     conds(('Ready', 'False', 'SomeNewReason2028'),), 'failed', 'NotReady'),
    # Conditions, but none that says ready for a kind with no readiness model: honest, not pending.
    ('no Ready/Available among conditions',
     conds(('Complete', 'True', ''),), 'noReadiness', 'NoReadyCondition'),
]

# EXISTING IS NOT BEING HEALTHY (portal#230): with no conditions, only a kind whose existence is
# its state is healthy by presence. (label, kind, group, phase, state, stateLabel, healthPercent)
PRESENCE_CASES = [
    ('a ConfigMap with no conditions', 'configmaps', 'core', '', 'healthy', 'Healthy', 100),
    ('a RoleBinding with no conditions', 'rolebindings', 'rbac.authorization.k8s.io', '', 'healthy', 'Healthy', 100),
    ('a Failed MongoDBCommunity (no conditions, status.phase)', 'mongodbcommunity', 'mongodbcommunity.mongodb.com', 'Failed', 'noReadiness', 'Failed', 50),
    ('a custom resource with no conditions and no phase', 'widgets', 'example.io', '', 'noReadiness', 'NoReadyCondition', 50),
    ('a Bound PersistentVolumeClaim', 'persistentvolumeclaims', 'core', 'Bound', 'healthy', 'Bound', 100),
    ('an Active Namespace', 'namespaces', 'core', 'Active', 'healthy', 'Healthy', 100),
    ('a Terminating Namespace', 'namespaces', 'core', 'Terminating', 'converging', 'Terminating', 50),
    ('a Pending PersistentVolumeClaim', 'persistentvolumeclaims', 'core', 'Pending', 'converging', 'Pending', 50),
    ('a Lost PersistentVolumeClaim', 'persistentvolumeclaims', 'core', 'Lost', 'failed', 'Lost', 20),
]

# A CRD child: readiness is Established/NamesAccepted, never Ready (krateo-057 authn-crd showed
# "pending" forever). The arms are core-provider#473's crdReady, so portal and composition agree.
CRD = '/apis/apiextensions.k8s.io/v1/customresourcedefinitions/c'
CRD_CASES = [
    ('CRD Established=True', conds(('NamesAccepted', 'True', 'NoConflicts'), ('Established', 'True', 'InitialNamesAccepted')), 'healthy', 'Healthy'),
    ('CRD NamesAccepted=False', conds(('NamesAccepted', 'False', 'KindConflict'), ('Established', 'False', 'NotAccepted')), 'failed', 'KindConflict'),
    # A fresh CRD is briefly Established=False: progress, never a failure flash on install.
    ('CRD Established=False', conds(('NamesAccepted', 'True', 'NoConflicts'), ('Established', 'False', 'Installing')), 'converging', 'Creating'),
    ('CRD Established=Unknown', conds(('Established', 'Unknown', ''),), 'converging', 'Creating'),
    ('CRD with no conditions yet', [], 'converging', 'Creating'),
    # crdReady's order: NamesAccepted=False first (even while Established), and Established counts
    # only while not Terminating.
    ('CRD established but terminating', conds(('Established', 'True', 'InitialNamesAccepted'), ('Terminating', 'True', 'InstanceDeletionInProgress')), 'converging', 'Terminating'),
    ('CRD name conflict while Established', conds(('NamesAccepted', 'False', 'ListKindConflict'), ('Established', 'True', 'InitialNamesAccepted')), 'failed', 'ListKindConflict'),
]

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f'{label}: {got!r} != {want!r}')


docs = render()
prog = named(docs, 'composition-resources')['spec']['filter']

ALL = [(l, c, s, lb, 'configmaps', '/api/v1/namespaces/ns/configmaps/c') for l, c, s, lb in CASES] + \
      [(l, c, s, lb, 'customresourcedefinitions', CRD) for l, c, s, lb in CRD_CASES]

for label, conditions, want_state, want_label, kind, getpath in ALL:
    out = jq(prog, payload(conditions, getpath=getpath, kind=kind))
    rows = out['resources']
    check(f'{label}: one row', len(rows), 1)
    if not rows:
        continue
    check(f'{label}: state', rows[0]['state'], want_state)
    check(f'{label}: stateLabel', rows[0]['stateLabel'], want_label)
    # THE PARTITION. Every checked row belongs to exactly one counted bucket; a state added later
    # must not land in an existing one silently, which is how Ready=Unknown became "pending".
    buckets = ['healthyCount', 'driftCount', 'failedCount', 'pendingCount',
               'notObservedCount', 'readyNotSetCount', 'unknownCount',
               'terminalCount', 'convergingCount', 'noReadinessCount', 'notFoundCount']
    summed = sum(out.get(b, 0) for b in buckets)
    check(f'{label}: counts sum to total', summed, out['total'])

for label, kind, grp, phase, want_state, want_label, want_pct in PRESENCE_CASES:
    out = jq(prog, payload([], getpath=f'/apis/{grp}/v1/namespaces/ns/{kind}/c', kind=kind, grp=grp, phase=phase))
    row = out['resources'][0]
    check(f'{label}: state, label, percent', (row['state'], row['stateLabel'], row['healthPercent']), (want_state, want_label, want_pct))
# The composition whose child failed this way no longer reads 100% match.
out = jq(prog, payload([], getpath='/apis/mongodbcommunity.mongodb.com/v1/namespaces/ns/mongodbcommunity/c',
                       kind='mongodbcommunity', grp='mongodbcommunity.mongodb.com', phase='Failed'))
check('a failed conditionless custom resource: not healthy, not converged', (out['healthyCount'], out['converged']), (0, False))

# A state the chain can emit but nothing counts would pass every case above and still be wrong, so
# the set of emitted states is compared against the colour map rather than enumerated by hand.
emitted = set()
for _, conditions, _, _, kind, getpath in ALL:
    emitted.add(jq(prog, payload(conditions, getpath=getpath, kind=kind))['resources'][0]['state'])
# `unread` (a Secret, refused by fetchablePath) and `pending` (managed but not fetched) are reachable
# through inputs these fixtures do not build; they are in the map and in the chain, so they are
# listed here rather than silently excluded.
emitted |= {'unread', 'pending'}
# `notFound` (managed, not fetched, and the apiserver said NotFound) is built by the NOT THERE cases
# below; it is listed here so the maps are checked against it.
emitted |= {'notFound'}

relations = named(docs, 'list-composition-detail-relations')
colour_map = relations['spec']['widgetData']['itemTemplate']['color']['map']
missing = sorted(emitted - set(colour_map))
extra = sorted(set(colour_map) - emitted)
check('every emitted state has a colour', missing, [])
check('no colour map entry is dead', extra, [])
# And every colour is a real palette name: getColorCode returns near-black for one it cannot resolve,
# which is a wrong colour rather than an error.
PALETTE = {'green', 'violet', 'orange', 'red', 'gray', 'slate', 'blue'}
check('every mapped colour is a palette name', sorted(set(colour_map.values()) - PALETTE), [])
# The label must come from the RA verbatim, not be re-cased in the widget: `ReadyNotSetAfterObserve`
# cannot be produced by capitalising a state string.
expr = [e for e in relations['spec']['widgetDataTemplate'] if e['forPath'] == 'dataSource'][0]['expression']
check('stateLabel is passed through, not re-cased', 'ascii_upcase' in expr, False)

# ---------------------------------------------------------------------------------------------
# THE JOIN: a fetched object belongs to a row by name and the CHILD's namespace.
#
# Not by group or kind: an informer-served object has no apiVersion/kind (snowplow
# dispatchViaInformer), so a fixture object here carries neither, as such an object would.
#
# It used to join on the COMPOSITION's namespace. A page chart's composition lives in krateo-system
# and its widgets in the page's own namespace, so on krateo-057 alerts-page read 44 of its 45
# children "Pending" although every one was fetched; portal's 17 demo-system Roles/RoleBindings the
# same. The cases above could not see it: their one child sits in the composition's namespace.
# So these children do NOT, and each carries a condition that makes a joined row distinguishable
# from an unjoined one ("pending").
#
# _rns comes from the found step, so that step's own jq is run over status.managed paths first.
found_prog = named(docs, 'composition-resources')['spec']['api'][1]['filter']
managed = [
    {'path': '/apis/rbac.authorization.k8s.io/v1/namespaces/krateo-alerts/roles/admins-read'},   # another namespace
    {'path': '/api/v1/namespaces/krateo-alerts'},                                                 # cluster-scoped Namespace
    {'path': '/apis/templates.krateo.io/v1/namespaces/krateo-alerts/restactions/obs-alerts'},
    {'path': '/api/v1/configmaps/legacy'},                                                        # old shape: no /namespaces/
    {'path': '/apis/rbac.authorization.k8s.io/v1/clusterroles/shared'},
]
found_in = {'found': {'items': [{'metadata': {'name': 'alerts-page', 'namespace': 'krateo-system'},
                                 'status': {'managed': managed, 'workloadHealthy': True}}]},
            'extras': {'name': 'alerts-page', 'namespace': 'krateo-system'}}
found_rows = jq(found_prog, found_in)
check('found: child namespaces come from the path, not the composition',
      [(r['_name'], r['_rns'], r['_grp']) for r in found_rows],
      [('admins-read', 'krateo-alerts', 'rbac.authorization.k8s.io'),
       ('krateo-alerts', '', 'core'),
       ('obs-alerts', 'krateo-alerts', 'templates.krateo.io'),
       ('legacy', 'krateo-system', 'core'),      # injected namespace = the composition's (#152 shape)
       ('shared', '', 'rbac.authorization.k8s.io')])

READY_FALSE = conds(('Ready', 'False', 'Unavailable'))
READY_TRUE = conds(('Ready', 'True', 'Available'))


def fetched(name, ns, c):
    return {'rname': name, 'rns': ns, 'rkind': '', 'conditions': c}


join_in = {
    'found': found_rows + [
        # A Role named like a cluster-scoped ClusterRole: only the ClusterRole is fetched below.
        {**found_rows[0], '_name': 'shared', '_getpath': '/apis/rbac.authorization.k8s.io/v1/namespaces/krateo-alerts/roles/shared'},
    ],
    'resources': [
        fetched('admins-read', 'krateo-alerts', READY_FALSE),
        fetched('krateo-alerts', '', []),
        fetched('obs-alerts', 'krateo-alerts', READY_TRUE),
        fetched('legacy', 'krateo-system', READY_TRUE),
        fetched('shared', '', READY_FALSE),
    ],
    'name': 'alerts-page', 'namespace': 'krateo-system',
}
WANT_JOIN = [
    # (name, row ns, state, navHref)
    ('admins-read', 'krateo-alerts', 'failed', '/resources/krateo-alerts/rbac.authorization.k8s.io/v1/roles/admins-read'),
    ('krateo-alerts', '', 'healthy', '/resources/cluster/core/v1/namespaces/krateo-alerts'),
    ('obs-alerts', 'krateo-alerts', 'healthy', '/resources/krateo-alerts/templates.krateo.io/v1/restactions/obs-alerts'),
    ('legacy', 'krateo-system', 'healthy', '/resources/krateo-system/core/v1/configmaps/legacy'),
    ('shared', '', 'failed', '/resources/cluster/rbac.authorization.k8s.io/v1/clusterroles/shared'),
    # Not fetched: the same-named ClusterRole must not stand in for it.
    ('shared', 'krateo-alerts', 'pending', '/resources/krateo-alerts/rbac.authorization.k8s.io/v1/roles/shared'),
]


def join_failures(program):
    rows = jq(program, join_in)['resources']
    got = [(r['name'], r['ns'], r['state'], r['navHref']) for r in rows]
    return [f'join row {i}: {g!r} != {w!r}' for i, (g, w) in enumerate(zip(got, WANT_JOIN)) if g != w] + \
           ([f'join: {len(got)} rows, want {len(WANT_JOIN)}'] if len(got) != len(WANT_JOIN) else [])


NEW_JOIN = 'select(.rname == $m._name and .rns == $m._rns)'
OLD_JOIN = 'select(.rname == $m._name and ((.rns == $m._cns) or (.rns == "")))'
check('the join is the name+child-namespace rule exactly once', prog.count(NEW_JOIN), 1)
# CONTROL: the old join, swapped into the same program, must fail these cases, or they do not test it.
old_prog = prog.replace(NEW_JOIN, OLD_JOIN).replace('ns: $m._rns,', 'ns: $m._cns,')
check('control: the old composition-namespace join fails the join cases', bool(join_failures(old_prog)), True)
failures.extend(join_failures(prog))

# ---------------------------------------------------------------------------------------------
# THE ROW'S STATED HEALTH (frontend Listy itemTemplate.health). The page header's worst-child pill
# used to infer each child's health from the row COLOUR, where `unread` is the same grey as a
# genuinely unknown child: a healthy installer read "└─ ReadyUnknown · 3 of 137 children not Ready"
# over three Secrets the portal never reads. Now each row states it. The map must cover every state
# the RA emits; `unread` is `excluded` (not counted); every other state keeps the verdict its colour
# gave, so this changes nothing for any child but the unread ones.
import re as _re
COLOUR_VERDICT = {'green': 'ready', 'violet': 'notSynced', 'orange': 'pending', 'red': 'notReady', 'gray': 'unknown'}
HEALTH_TOKENS = {'ready', 'pending', 'unknown', 'notSynced', 'notReady', 'excluded'}
m = _re.search(r'\{([^{}]*)\} as \$health', expr)
health_map = dict(_re.findall(r'(\w+): "(\w+)"', m.group(1))) if m else {}
check('the relations list states each row\'s health', relations['spec']['widgetData']['itemTemplate'].get('health'), '{health}')
check('every emitted state has a health', sorted(emitted - set(health_map)), [])
check('no health map entry is dead', sorted(set(health_map) - emitted), [])
check('every health is a token the header knows', sorted(set(health_map.values()) - HEALTH_TOKENS), [])
check('unread is excluded', health_map.get('unread'), 'excluded')
check('every other state keeps its colour\'s verdict',
      {k: v for k, v in health_map.items() if k != 'unread'},
      {k: COLOUR_VERDICT[colour_map[k]] for k in health_map if k != 'unread' and k in colour_map})
rows_out = jq(expr.strip().removeprefix('${').removesuffix('}'), {'resources': [
    {'kind': 'Secret', 'name': 'hyperdx-admin-creds', 'ns': 'krateo-system', 'state': 'unread', 'stateLabel': 'Unread', 'navHref': ''},
    {'kind': 'Deployment', 'name': 'snowplow', 'ns': 'krateo-system', 'state': 'healthy', 'stateLabel': 'Healthy', 'navHref': '/x'}]})
check('each resolved row carries its health', [(r['name'], r['health']) for r in rows_out],
      [('hyperdx-admin-creds', 'excluded'), ('snowplow', 'ready')])

# ---------------------------------------------------------------------------------------------
# NOT THERE (visual audit CMP-2). A paused composition's two children had been deleted; the page read
# them "Pending" and the verdict "the rest still converging". A child is NotFound exactly when the
# resources stage's errors (resErr, a list) hold the apiserver's NotFound for it: snowplow records a
# definite NotFound as a 404 Status whose message is `<plural>[.<group>] "<name>" not found`, any
# other failure as text. Anything else unfetched stays Pending, as before.
def unfetched(kind, grp, name='c', getpath=None, res_err=None, apiresources=None, conds_=None):
    path = getpath or (f'/api/v1/namespaces/ns/{kind}/{name}' if grp == 'core' else f'/apis/{grp}/v1alpha1/namespaces/ns/{kind}/{name}')
    d = {'found': [{'_cn': 'comp', '_cns': 'ns', '_rns': 'ns', '_name': name, '_kind': kind, '_grp': grp,
                    '_ver': 'v1' if grp == 'core' else 'v1alpha1', '_getpath': path, '_wh': None, '_whMsg': '',
                    '_conds': conds_ or []}],
         'resources': [], 'name': 'comp', 'namespace': 'ns'}
    if res_err is not None:
        d['resErr'] = res_err
    if apiresources is not None:
        d['apiresources'] = apiresources
    return d


def one(d):
    out = jq(prog, d)
    r = out['resources'][0]
    return out, (r['kind'], r['state'], r['stateLabel'], r['healthPercent'])


NF = {'kind': 'Status', 'apiVersion': 'v1', 'status': 'Failure', 'reason': 'NotFound', 'code': 404}
out, got = one(unfetched('configmaps', 'core', 'acme-feedback-oas',
                         res_err=[dict(NF, message='configmaps "acme-feedback-oas" not found')]))
check('NotFound (a definite 404 Status): the row says so, no health', got, ('ConfigMap', 'notFound', 'NotFound', 0))
check('NotFound: counted apart, not pending, and the counts still partition',
      (out.get('notFoundCount'), out['pendingCount'], out['healthyCount'], out['total']), (1, 0, 0, 1))
_, got = one(unfetched('restdefinitions', 'ogen.krateo.io', 'acme-feedback-form',
                       res_err=['restdefinitions.ogen.krateo.io "acme-feedback-form" not found']))
check('NotFound as text (a client-go StatusError): the same', got[1:3], ('notFound', 'NotFound'))
_, got = one(unfetched('configmaps', 'core', 'c', res_err=[{'kind': 'Status', 'code': 403, 'reason': 'Forbidden',
                                                         'message': 'configmaps "c" is forbidden'}]))
check('refused, not missing: still Pending', got[1:3], ('pending', 'Pending'))
_, got = one(unfetched('configmaps', 'core', 'c'))
check('no error recorded at all: still Pending', got[1:3], ('pending', 'Pending'))
_, got = one(unfetched('roles', 'rbac.authorization.k8s.io', 'shared',
                       res_err=['clusterroles.rbac.authorization.k8s.io "shared" not found']))
check('another kind\'s NotFound (clusterroles ends with roles) does not match', got[1:3], ('pending', 'Pending'))
_, got = one(unfetched('configmaps', 'core', 'c', res_err=[dict(NF, message='configmaps "c2" not found')]))
check('another name\'s NotFound does not match', got[1:3], ('pending', 'Pending'))

# THE KIND AS SERVED (visual audit REL-2): a child that was not fetched read "Restdefinition", made up
# from its plural; the served kind is RestDefinition, which discovery (the apiresources stage) names.
DISC = [[{'group': 'ogen.krateo.io', 'kind': 'RestDefinition', 'plural': 'restdefinitions'}]]
_, got = one(unfetched('restdefinitions', 'ogen.krateo.io', apiresources=DISC))
check('an unfetched child: the kind discovery serves', got[0], 'RestDefinition')
_, got = one(unfetched('restdefinitions', 'ogen.krateo.io'))
check('discovery unread: the plural as recorded, never a made-up kind', got[0], 'restdefinitions')
stage = next((st for st in named(docs, 'composition-resources')['spec']['api'] if st['name'] == 'apiresources'),
             {'dependsOn': {'iterator': '[]'}, 'filter': '[]'})
it = jq(stage['dependsOn']['iterator'], {'name': 'comp', 'namespace': 'ns', 'found': [
    {'_cn': 'comp', '_cns': 'ns', '_grp': 'ogen.krateo.io', '_ver': 'v1alpha1', '_gvpath': '/apis/ogen.krateo.io/v1alpha1'},
    {'_cn': 'comp', '_cns': 'ns', '_grp': 'ogen.krateo.io', '_ver': 'v1alpha1', '_gvpath': '/apis/ogen.krateo.io/v1alpha1'},
    {'_cn': 'comp', '_cns': 'ns', '_grp': 'core', '_ver': 'v1', '_gvpath': ''},
    {'_cn': 'other', '_cns': 'ns', '_grp': 'apps', '_ver': 'v1', '_gvpath': '/apis/apps/v1'}]})
check('discovery: one call per group/version of THIS composition\'s children, core left to the map',
      it, [{'g': 'ogen.krateo.io', 'v': 'v1alpha1', 'ns': '', 'pl': '', 'nm': ''}])
check('discovery: nothing to look up without the composition', jq(stage['dependsOn']['iterator'], {'name': 'comp', 'namespace': 'ns', 'found': []}), [])
check('discovery filter: plural -> kind, subresources dropped',
      jq(stage['filter'], {'apiresources': {'groupVersion': 'ogen.krateo.io/v1alpha1', 'resources': [
          {'name': 'restdefinitions', 'kind': 'RestDefinition'}, {'name': 'restdefinitions/status', 'kind': 'RestDefinition'}]}}),
      [{'group': 'ogen.krateo.io', 'kind': 'RestDefinition', 'plural': 'restdefinitions'}])

# A CLUSTER-SCOPED CHILD the static list does not name (visual audit CMP-7: internal-ca-bundle's
# trust-manager Bundle). core-provider records namespace "" for it and a path without /namespaces/;
# the composition's namespace used to be spliced in, so the GET 404'd and the row read Pending.
found_new = jq(found_prog, {'found': {'items': [{'metadata': {'name': 'ca', 'namespace': 'krateo-system'}, 'status': {'managed': [
    {'apiVersion': 'trust.cert-manager.io/v1alpha1', 'resource': 'bundles', 'name': 'krateo-internal-ca', 'namespace': '',
     'path': '/apis/trust.cert-manager.io/v1alpha1/bundles/krateo-internal-ca'},
    {'apiVersion': 'v1', 'resource': 'configmaps', 'name': 'cm', 'namespace': 'team-a', 'path': '/api/v1/configmaps/cm'}]}}]},
    'extras': {'name': 'ca', 'namespace': 'krateo-system'}})
check('found: a cluster-scoped child is fetched where core-provider recorded it; a namespaced one in its own namespace',
      [(r['_name'], r['_getpath'], r['_rns'], r.get('_gvpath')) for r in found_new],
      [('krateo-internal-ca', '/apis/trust.cert-manager.io/v1alpha1/bundles/krateo-internal-ca', '', '/apis/trust.cert-manager.io/v1alpha1'),
       ('cm', '/api/v1/namespaces/team-a/configmaps/cm', 'team-a', '')])

# THE COMPOSITION'S OWN STATE rides on the rows (its Ready and Synced), for the verdict.
PAUSED = [{'type': 'Ready', 'status': 'True', 'reason': 'ReconcilePaused', 'lastTransitionTime': '2026-11-04T10:10:00Z'},
          {'type': 'Synced', 'status': 'False', 'reason': 'ReconcilePaused', 'lastTransitionTime': '2026-11-04T10:10:05Z'}]
out, _ = one(unfetched('configmaps', 'core', conds_=PAUSED))
check('the composition: Paused, since its pause', out.get('composition'), {'state': 'paused', 'stateLabel': 'Paused',
      'pausedSince': '4 Nov 10:10 UTC', 'readyReason': 'ReconcilePaused', 'readyMessage': ''})
out, _ = one(unfetched('configmaps', 'core', conds_=[{'type': 'Ready', 'status': 'Unknown', 'reason': 'ZeroEvidence',
                                                       'message': 'Composition health is unknown: no child reports readiness'}]))
check('the composition: Unknown (ZeroEvidence), its message', (out.get('composition', {}).get('state'), out.get('composition', {}).get('readyMessage')),
      ('unknown', 'no child reports readiness'))
found_paused = jq(found_prog, {'found': {'items': [{'metadata': {'name': 'p', 'namespace': 'ns'}, 'status': {
    'conditions': PAUSED + [{'type': 'Healthy', 'status': 'True'}], 'managed': [{'path': '/api/v1/namespaces/ns/configmaps/x'}]}}]},
    'extras': {'name': 'p', 'namespace': 'ns'}})
check('found: the composition\'s Ready and Synced ride on its rows, nothing else', [c['type'] for c in found_paused[0].get('_conds', [])], ['Ready', 'Synced'])

# --pinned: the relations widget must validate against the frontend-crds the installer PINS. The
# `health` field exists from frontend 1.8.46; a portal that emits it before that CRD is live has its
# widget refused by snowplow (400), so this keeps the order: frontend line first, then portal.
if '--pinned' in sys.argv[1:]:
    import importlib.util as _ilu
    def _load(name, file):
        spec = _ilu.spec_from_file_location(name, os.path.join(HERE, file))
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    _pinned = _load('pinned', 'lint-pinned-crds.py')
    _version, _why = _pinned.pinned_version()
    if not _version:
        failures.append(f'cannot determine the pinned frontend-crds version: {_why}')
    else:
        _tbi = _load('gates', 'test-ra-input-gates.py').tbi
        _tbi.RESOLVED.append(('authored list-composition-detail-relations', relations))
        failures.extend(f'pinned frontend-crds {_version}: {p}' for p in _tbi.validate_resolved(_pinned.unpack(_version)))
        print(f'relations widget checked against frontend-crds {_version} ({_why})')

for f in failures:
    print(f'FAIL  {f}')
print(f'{"FAILED" if failures else "ok"}: composition resources, {len(ALL)} condition shapes,'
      f' {len(WANT_JOIN)} join cases ({len(failures)} failure(s))')
raise SystemExit(1 if failures else 0)
