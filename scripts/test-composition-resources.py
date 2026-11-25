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

A child's state is core-provider's verdict for it (classifyChildEval, childhealth.go): a group rule,
not a list of kinds. In a native group only the kinds that carry readiness are judged (apps workloads,
Job, CronJob, Pod, PVC, CRD) and every other native kind is healthy by existence; a custom resource is
read only through a Ready condition (or a documented kind's own), else it has no readiness. So the
page's "N of M healthy" is the count the composition's own Ready verdict was made from.

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
def payload(conditions, getpath='/api/v1/namespaces/ns/configmaps/c', kind='configmaps', grp=None, phase='', **live):
    grp, rns = ('apiextensions.k8s.io', '') if kind == 'customresourcedefinitions' else (grp or 'core', 'ns')
    return {
        'found': [{'_cn': 'comp', '_cns': 'ns', '_rns': rns, '_name': 'c', '_kind': kind,
                   '_grp': grp, '_ver': 'v1', '_cluster': rns == '', '_getpath': getpath,
                   '_wh': True, '_whMsg': ''}],
        # `live` is the rest of what the resources step projects (gen, ogen, counts).
        'resources': [{'rname': 'c', 'rns': rns, 'rkind': 'ConfigMap', 'conditions': conditions, 'phase': phase, **live}],
        'name': 'comp', 'namespace': 'ns',
    }


# The condition cases below are read off a KRATEO custom resource (a rest-dynamic-controller kind),
# because core-provider reads conditions only there and on a third-party CR: a ConfigMap carrying a
# Ready=False is healthy by existence (native group), so these cases used to test a reading that the
# controller never makes.
KRATEO_KIND, KRATEO_GRP = 'repoes', 'github.ogen.krateo.io'
KRATEO_PATH = f'/apis/{KRATEO_GRP}/v1alpha1/namespaces/ns/{KRATEO_KIND}/c'


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
    # A Ready=False reason core-provider's krateoEval does not list as a failure is CONVERGING there
    # (its `default` arm, childhealth.go:1403), so it is here, labelled with the reason verbatim. It
    # used to be "failed", which put a Ready=False/Creating child Composition in the problems list
    # while its parent's own verdict said Creating.
    ('Ready=False/SomeNewReason',
     conds(('Ready', 'False', 'SomeNewReason2028'),), 'converging', 'SomeNewReason2028'),
    ('Ready=False/Creating (a child Composition)',
     conds(('Ready', 'False', 'Creating'),), 'converging', 'Creating'),
    # A timer-bounded helm operation reads Pending (portal.readyReasonDefs, Diego 2026-11-15).
    ('Ready=False/HelmOperationInProgress',
     conds(('Ready', 'False', 'HelmOperationInProgress'),), 'converging', 'Pending'),
    ('Ready=False/Unavailable', conds(('Ready', 'False', 'Unavailable'),), 'failed', 'NotReady'),
    ('Ready=Unknown/Unavailable', conds(('Ready', 'Unknown', 'Unavailable'),), 'failed', 'NotReady'),
    ('Ready=False/CreateWithheldByPolicy', conds(('Ready', 'False', 'CreateWithheldByPolicy'),), 'failed', 'NotReady'),
    ('Ready=False/InstallFailing', conds(('Ready', 'False', 'InstallFailing'),), 'failed', 'NotReady'),
    # Ready=True/AssumedUpToDate is ASSERTED, not observed (oasgen-provider#709): unevaluated there,
    # so no readiness here — not healthy.
    ('Ready=True/AssumedUpToDate', conds(('Ready', 'True', 'AssumedUpToDate'),), 'noReadiness', 'AssumedUpToDate'),
    # Resting at Ready=False is the child's declared design (#498): healthy there, healthy here.
    ('Ready=False/TerminalByDesign', conds(('Ready', 'False', 'TerminalByDesign'),), 'healthy', 'TerminalByDesign'),
    # A child Composition that could judge none of its own children: unevaluated (noEvidenceBelow).
    ('Ready=Unknown/ZeroEvidence', conds(('Ready', 'Unknown', 'ZeroEvidence'),), 'noReadiness', 'ZeroEvidence'),
    # Conditions, but none that says ready for a kind with no readiness model: honest, not pending.
    ('no Ready/Available among conditions',
     conds(('Complete', 'True', ''),), 'noReadiness', 'NoReadyCondition'),
    # Only Ready is read on a custom resource. Available is not core-provider's reading of a CR.
    ('Available=True, no Ready', conds(('Available', 'True', ''),), 'noReadiness', 'NoReadyCondition'),
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
    # core-provider never reads a Namespace: native and not judged, so healthy by existence,
    # Terminating included (it used to be "converging" here).
    ('a Terminating Namespace', 'namespaces', 'core', 'Terminating', 'healthy', 'Healthy', 100),
    ('a Pending PersistentVolumeClaim', 'persistentvolumeclaims', 'core', 'Pending', 'converging', 'Pending', 50),
    ('a Lost PersistentVolumeClaim', 'persistentvolumeclaims', 'core', 'Lost', 'failed', 'Lost', 20),
    # pvcReady: a phase not yet written is healthy, fail-safe (it used to be "converging" here).
    ('a PersistentVolumeClaim with no phase yet', 'persistentvolumeclaims', 'core', '', 'healthy', 'Healthy', 100),
    # THE GROUP RULE (isNativeGroup): a native kind no list here names is healthy by existence. Each of
    # these read "noReadiness" before, because it was not on the presence list.
    ('an HPA (autoscaling)', 'horizontalpodautoscalers', 'autoscaling', '', 'healthy', 'Healthy', 100),
    ('a PDB (policy)', 'poddisruptionbudgets', 'policy', '', 'healthy', 'Healthy', 100),
    ('a Lease (coordination.k8s.io)', 'leases', 'coordination.k8s.io', '', 'healthy', 'Healthy', 100),
    ('a ControllerRevision (apps, unjudged)', 'controllerrevisions', 'apps', '', 'healthy', 'Healthy', 100),
    ('a CronJob (batch)', 'cronjobs', 'batch', '', 'healthy', 'Healthy', 100),
    ('a legacy extensions kind', 'ingresses', 'extensions', '', 'healthy', 'Healthy', 100),
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

ALL = [(l, c, s, lb, KRATEO_KIND, KRATEO_GRP, KRATEO_PATH) for l, c, s, lb in CASES] + \
      [(l, c, s, lb, 'customresourcedefinitions', None, CRD) for l, c, s, lb in CRD_CASES]

for label, conditions, want_state, want_label, kind, grp, getpath in ALL:
    out = jq(prog, payload(conditions, getpath=getpath, kind=kind, grp=grp))
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
               'terminalCount', 'convergingCount', 'noReadinessCount']
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

# ---------------------------------------------------------------------------------------------
# core-provider's classifyChildEval, kind by kind (childhealth.go:832-879). Each case is the child
# as it is fetched — conditions, phase and the counts the resources step projects — and the state
# the controller's own rollup gives it. (label, kind, group, conditions, phase, live, state, label)
BUSY = conds(('AbleToScale', 'True', 'ReadyForNewScale'), ('ScalingActive', 'False', 'FailedGetResourceMetric'))
GROUP_CASES = [
    # A native kind's conditions are NOT read: healthy by existence whatever they say.
    ('an HPA whose metrics fail', 'horizontalpodautoscalers', 'autoscaling', BUSY, '', {}, 'healthy', 'Healthy'),
    ('a PDB with no disruption allowed', 'poddisruptionbudgets', 'policy',
     conds(('DisruptionAllowed', 'False', 'InsufficientPods'),), '', {}, 'healthy', 'Healthy'),
    ('an APIService Available=False', 'apiservices', 'apiregistration.k8s.io',
     conds(('Available', 'False', 'MissingEndpoints'),), '', {}, 'healthy', 'Healthy'),
    ('a Namespace Terminating with conditions', 'namespaces', 'core',
     conds(('NamespaceDeletionContentFailure', 'False', 'ContentDeleted'),), 'Terminating', {}, 'healthy', 'Healthy'),
    ('a ConfigMap carrying a Ready=False', 'configmaps', 'core', conds(('Ready', 'False', 'Unavailable'),), '', {}, 'healthy', 'Healthy'),
    ('a ConfigMap, plain', 'configmaps', 'core', [], '', {}, 'healthy', 'Healthy'),
    # workloadReady (:1438-1456): replica counts, never failed; spec.replicas defaults to 1.
    ('a Deployment 2 of 2 ready', 'deployments', 'apps', conds(('Available', 'True', 'MinimumReplicasAvailable'),), '',
     {'counts': {'replicas': 2, 'readyReplicas': 2}}, 'healthy', 'Healthy'),
    ('a Deployment 0 of 1 ready, Available=False', 'deployments', 'apps', conds(('Available', 'False', 'MinimumReplicasUnavailable'),), '',
     {'counts': {'replicas': 1, 'readyReplicas': None, 'unavailableReplicas': 1}}, 'converging', 'NotReady'),
    ('a Deployment with replicas unset and none ready', 'deployments', 'apps', [], '', {'counts': {}}, 'converging', 'NotReady'),
    ('a Deployment scaled to 0', 'deployments', 'apps', [], '', {'counts': {'replicas': 0}}, 'healthy', 'Healthy'),
    ('a StatefulSet 3 of 3, 1 unavailable', 'statefulsets', 'apps', [], '',
     {'counts': {'replicas': 3, 'readyReplicas': 3, 'unavailableReplicas': 1}}, 'converging', 'NotReady'),
    ('a DaemonSet on every node', 'daemonsets', 'apps', [], '',
     {'counts': {'desiredNumberScheduled': 3, 'numberReady': 3, 'numberUnavailable': 0}}, 'healthy', 'Healthy'),
    ('a DaemonSet short of a node', 'daemonsets', 'apps', [], '',
     {'counts': {'desiredNumberScheduled': 3, 'numberReady': 2, 'numberUnavailable': 1}}, 'converging', 'NotReady'),
    # jobReady (:1498-1507).
    ('a Job that succeeded', 'jobs', 'batch', conds(('Complete', 'True', 'CompletionsReached'),), '',
     {'counts': {'succeeded': 1, 'backoffLimit': 6}}, 'healthy', 'Healthy'),
    ('a Job past its backoffLimit', 'jobs', 'batch', conds(('Failed', 'True', 'BackoffLimitExceeded'),), '',
     {'counts': {'failed': 7, 'backoffLimit': 6}}, 'failed', 'BackoffLimitExceeded'),
    ('a Job still running', 'jobs', 'batch', [], '', {'counts': {'failed': 1, 'backoffLimit': 6}}, 'converging', 'Running'),
    # podReady (:999-1016).
    ('a Pod that Succeeded', 'pods', 'core', conds(('Ready', 'False', 'PodCompleted'),), 'Succeeded', {}, 'healthy', 'Healthy'),
    ('a Pod that Failed', 'pods', 'core', conds(('Ready', 'False', 'PodFailed'),), 'Failed', {}, 'failed', 'Failed'),
    ('a Running Pod, Ready', 'pods', 'core', conds(('Ready', 'True', ''),), 'Running', {}, 'healthy', 'Healthy'),
    ('a Running Pod failing its probe', 'pods', 'core', conds(('Ready', 'False', 'ContainersNotReady'),), 'Running', {}, 'converging', 'NotReady'),
    ('a Pending Pod', 'pods', 'core', conds(('PodScheduled', 'False', 'Unschedulable'),), 'Pending', {}, 'converging', 'Pending'),
    # pvcReady reads the phase even when the claim carries conditions (a resize).
    ('a Bound PVC mid-resize', 'persistentvolumeclaims', 'core', conds(('Resizing', 'True', ''),), 'Bound', {}, 'healthy', 'Bound'),
    # thirdPartyEval (:904-949): a current Ready, or a documented kind's own condition; nothing else.
    ('a CR in an undocumented shape (Synced only)', 'widgets', 'example.io', conds(('Synced', 'True', 'ReconcileSuccess'),), '', {}, 'noReadiness', 'NoReadyCondition'),
    ('a CR with only Available', 'widgets', 'example.io', conds(('Available', 'True', ''),), '', {}, 'noReadiness', 'NoReadyCondition'),
    ('a CR Ready=True', 'clusters', 'postgresql.cnpg.io', conds(('Ready', 'True', 'ClusterIsReady'),), '', {}, 'healthy', 'Healthy'),
    # Not failed: a third party's reasons are its own vocabulary.
    ('a CR Ready=False', 'clusters', 'postgresql.cnpg.io', conds(('Ready', 'False', 'ClusterIsNotReady'),), '', {}, 'converging', 'ClusterIsNotReady'),
    ('a CR Ready=True for an older generation', 'clusters', 'postgresql.cnpg.io',
     [{'type': 'Ready', 'status': 'True', 'reason': 'ClusterIsReady', 'observedGeneration': 3}], '', {'gen': 4}, 'converging', 'Updating'),
    ('a CR Ready=True, status.observedGeneration behind', 'keycloaks', 'k8s.keycloak.org',
     conds(('Ready', 'True', '')), '', {'gen': 2, 'ogen': 1}, 'converging', 'Updating'),
    ('a CR Ready=True, current', 'keycloaks', 'k8s.keycloak.org', conds(('Ready', 'True', '')), '', {'gen': 2, 'ogen': 2}, 'healthy', 'Healthy'),
    ('a kagent ModelConfig Accepted', 'modelconfigs', 'kagent.dev', conds(('Accepted', 'True', 'ModelConfigReconciled'),), '', {}, 'healthy', 'Healthy'),
    ('a kagent ModelConfig, refs unresolved', 'modelconfigs', 'kagent.dev',
     conds(('Accepted', 'True', 'ModelConfigReconciled'), ('ResolvedRefs', 'False', 'SecretNotFound')), '', {}, 'converging', 'SecretNotFound'),
    ('a kagent ModelConfig not Accepted', 'modelconfigs', 'kagent.dev',
     conds(('Accepted', 'False', 'ModelConfigReconcileFailed'),), '', {}, 'converging', 'ModelConfigReconcileFailed'),
    # `Accepted` is never read generically: in Gateway API, Accepted is not Programmed.
    ('a Gateway API HTTPRoute Accepted', 'httproutes', 'gateway.networking.k8s.io',
     conds(('Accepted', 'True', 'Accepted'),), '', {}, 'healthy', 'Healthy'),  # .k8s.io: native, by existence
    ('another CR with only Accepted', 'agents', 'kagent.dev', conds(('Accepted', 'True', ''),), '', {}, 'noReadiness', 'NoReadyCondition'),
]
group_emitted = set()
for label, kind, grp, c, phase, live, want_state, want_label in GROUP_CASES:
    path = f'/apis/{grp}/v1/namespaces/ns/{kind}/c' if grp != 'core' else f'/api/v1/namespaces/ns/{kind}/c'
    out = jq(prog, payload(c, getpath=path, kind=kind, grp=grp, phase=phase, **live))
    row = out['resources'][0]
    check(f'{label}: state, label', (row['state'], row['stateLabel']), (want_state, want_label))
    group_emitted.add(row['state'])

# core-provider's own table of a composition's Ready reasons and the verdict a PARENT's rollup gives
# each (ready-reasons.yaml: the snapshot test-ready-reasons.py holds equal to core-provider's
# docs/ready-reasons.md). A child Composition carrying each one lands in that verdict's bucket here.
# The portal's finer states sit inside a bucket: `terminal` is a failure, `unknown` a Ready=Unknown
# core-provider counts converging, notObserved/readyNotSet its unevaluated `notObserved` cause.
VERDICT_STATES = {'healthy': {'healthy'}, 'failed': {'failed', 'terminal'}, 'converging': {'converging', 'unknown'},
                  'unevaluated (rolls up as healthy)': {'noReadiness', 'notObserved', 'readyNotSet'}}
with open(os.path.join(REPO, 'ready-reasons.yaml'), encoding='utf-8') as fh:
    _table = yaml.safe_load(fh)['table']
check('the ready-reasons snapshot is read', len(_table) > 0, True)
for _row in _table:
    _out = jq(prog, payload(conds(('Ready', _row['status'], _row['reason'])),
                            getpath='/apis/composition.krateo.io/v1-2-0/namespaces/ns/portals/c',
                            kind='portals', grp='composition.krateo.io'))
    _state = _out['resources'][0]['state']
    check(f'child Composition Ready={_row["status"]}/{_row["reason"]}: {_state} is core-provider\'s {_row["verdict"]}',
          _state in VERDICT_STATES.get(_row['verdict'], set()), True)

# THE HEADLINE. One composition with the children the issue names: the page's "N of M healthy" is
# core-provider's count of healthy children, and its no-readiness count is the unevaluated one.
MIXED = [
    ('cm', 'configmaps', 'core', [], {}),
    ('hpa', 'horizontalpodautoscalers', 'autoscaling', BUSY, {}),
    ('pdb', 'poddisruptionbudgets', 'policy', conds(('DisruptionAllowed', 'True', 'SufficientPods'),), {}),
    ('lease', 'leases', 'coordination.k8s.io', [], {}),
    ('cr', 'widgets', 'example.io', conds(('Synced', 'True', 'ReconcileSuccess'),), {}),
    ('crd', 'customresourcedefinitions', 'apiextensions.k8s.io',
     conds(('NamesAccepted', 'True', 'NoConflicts'), ('Established', 'False', 'Installing')), {}),
]
mixed = {'found': [], 'resources': [], 'name': 'comp', 'namespace': 'ns'}
for nm, kind, grp, c, live in MIXED:
    rns = '' if kind == 'customresourcedefinitions' else 'ns'
    path = (f'/apis/{grp}/v1/{kind}/{nm}' if rns == '' else
            f'/api/v1/namespaces/ns/{kind}/{nm}' if grp == 'core' else f'/apis/{grp}/v1/namespaces/ns/{kind}/{nm}')
    mixed['found'].append({'_cn': 'comp', '_cns': 'ns', '_rns': rns, '_name': nm, '_kind': kind, '_grp': grp, '_ver': 'v1',
                           '_cluster': rns == '', '_getpath': path, '_wh': True, '_whMsg': ''})
    mixed['resources'].append({'rname': nm, 'rns': rns, 'rkind': '', 'conditions': c, 'phase': '', **live})
out = jq(prog, mixed)
check('the mixed composition: healthy / no readiness / converging of total',
      (out['healthyCount'], out['noReadinessCount'], out['convergingCount'], out['total']), (4, 1, 1, 6))

# The resources step projects what those predicates read, off the object as fetched: the counts and
# generations, and nothing that errors on a kind whose spec is not an object.
res_prog = named(docs, 'composition-resources')['spec']['api'][2]['filter']
_deploy = {'metadata': {'name': 'd', 'namespace': 'ns', 'generation': 4},
           'spec': {'replicas': 2, 'backoffLimit': None},
           'status': {'readyReplicas': 1, 'unavailableReplicas': 1, 'observedGeneration': 3, 'conditions': []}}
_odd = {'metadata': {'name': 'o', 'namespace': 'ns'}, 'spec': 'not-an-object', 'status': {}}
_proj = jq(res_prog, {'resources': _deploy}) + jq(res_prog, {'resources': _odd})
check('resources step: a Deployment counts and generations',
      [(_p['rname'], _p.get('gen'), _p.get('ogen'), *[(_p.get('counts') or {}).get(k)
        for k in ('replicas', 'readyReplicas', 'unavailableReplicas')]) for _p in _proj],
      [('d', 4, 3, 2, 1, 1), ('o', 0, None, None, None, None)])

# The RA names core-provider's function as the source of truth, beside the chain that ports it.
with open(os.path.join(CHART, 'templates', 'restaction.composition-resources.yaml'), encoding='utf-8') as fh:
    _src = fh.read()
check('the RA names classifyChildEval as its source of truth', 'core-provider\'s classifyChildEval' in _src, True)
check('the presence list is gone (the rule is by group)', '$presenceKind' in _src, False)

# A state the chain can emit but nothing counts would pass every case above and still be wrong, so
# the set of emitted states is compared against the colour map rather than enumerated by hand.
emitted = set()
for _, conditions, _, _, kind, grp, getpath in ALL:
    emitted.add(jq(prog, payload(conditions, getpath=getpath, kind=kind, grp=grp))['resources'][0]['state'])
# `unread` (a Secret, refused by fetchablePath) and `pending` (managed but not fetched) are reachable
# through inputs these fixtures do not build; they are in the map and in the chain, so they are
# listed here rather than silently excluded.
emitted |= {'unread', 'pending'}
emitted |= group_emitted

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
# A joined Role/ClusterRole is healthy by existence whatever its conditions say (native group); an
# unjoined one is "pending", so a joined row is still told from an unjoined one.
WANT_JOIN = [
    # (name, row ns, state, navHref)
    ('admins-read', 'krateo-alerts', 'healthy', '/resources/krateo-alerts/rbac.authorization.k8s.io/v1/roles/admins-read'),
    ('krateo-alerts', '', 'healthy', '/resources/cluster/core/v1/namespaces/krateo-alerts'),
    ('obs-alerts', 'krateo-alerts', 'healthy', '/resources/krateo-alerts/templates.krateo.io/v1/restactions/obs-alerts'),
    ('legacy', 'krateo-system', 'healthy', '/resources/krateo-system/core/v1/configmaps/legacy'),
    ('shared', '', 'healthy', '/resources/cluster/rbac.authorization.k8s.io/v1/clusterroles/shared'),
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
