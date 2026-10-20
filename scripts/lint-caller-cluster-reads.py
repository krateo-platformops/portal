#!/usr/bin/env python3
"""
lint-caller-cluster-reads — nothing `devs` reaches may require one of the two ClusterRoles #261
deleted, and no chart template may bind the `devs` group cluster-wide.

WHAT THIS GUARDS (#261). `devs` carries no ClusterRole and no ClusterRoleBinding. That is a
property of the CHART, not of the cluster: the moment a RESTAction step dials a cluster-scoped
path as the caller, every persona that loads the page hosting it needs a cluster-wide grant, and
the only way to give one is the thing we just deleted. The two ClusterRoles this chart used to
ship existed for exactly that reason and for no other.

WHICH STEPS NEED ONE. snowplow resolves a step's endpoint per `resolveStageEndpoint`
(resolvers/restactions/api/resolve.go:386): "UAF stages use the snowplow-SA endpoint (cluster-wide
read); non-UAF stages go through the per-user clientconfig (or the named EndpointRef)." So:

  userAccessFilter  -> snowplow's SA dials, the UAF narrows per item   -> caller needs NO grant
  endpointRef       -> that endpoint's own credential                  -> caller needs NO grant
  neither           -> the CALLER's own <user>-clientconfig            -> caller needs the grant

and a path with no `/namespaces/<ns>/` segment is a cluster-scoped request, which RBAC can only
satisfy from a ClusterRole. `resourceNames` does not narrow a `list`, so there is no smaller
version of such a grant for the LIST cases — it is all or nothing.

THE FIX IS USUALLY TO CHANGE THE QUESTION, not to add the grant. Four steps listed CRDs to learn
which composition kinds exist; CompositionDefinitions carry the same GVR in `.status`, are
namespaced, and can be read cluster-scope under a UAF — which is how compositions-list always did
it. `projects` asked "may I list namespaces", which no Role can answer, and now asks where the
caller may list compositions.

SCOPE, DELIBERATELY NARROW. This checks the two resources whose ClusterRoles were deleted —
`namespaces` and `customresourcedefinitions` — and nothing else. The chart has ~35 caller-dialled
cluster-scoped steps in total (rolebindings, users, SubjectAccessReviews, `/apis`, cluster-scope
composition LISTs). Those are NOT regressions of #261: `devs` never held a grant for any of them,
so they already degraded through `continueOnError` long before this change, and widening the lint
to cover them would encode ~28 "this is an admin page" claims nobody has verified. They are worth
their own pass; this gate exists to stop the two that were fixed from coming back, and to stop a
new cluster-wide grant being attached to `devs` to paper over a question that should change
instead.

Usage: lint-caller-cluster-reads.py. Exit code = findings.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NS = 'krateo-system'
EXTRA_VALUES = {'portal': ['observability.snowplowSli=true']}

# (RESTAction, step) -> why a caller-dialled cluster-scoped read is acceptable here.
# Only ever an ADMIN-ONLY page: `admins` is bound to cluster-admin, so the grant already exists and
# is not a tenant persona's. A dev-reachable page must never appear in this list — change the
# question instead.
ALLOWED = {
    ('alert-formdef', 'crd'): 'Alerts is an admin page; the Alert CRD by name feeds the create form schema',
    ('alert-editdef', 'crd'): 'Alerts is an admin page (edit form, same CRD)',
    ('agent-formdef', 'crd'): 'Agents is an admin page; the kagent Agent CRD by name feeds the create form',
    ('agents-policy-formdef', 'crd'): 'Agents policies is an admin page; the AgentgatewayPolicy CRD by name',
    ('controller-configure-formdef', 'crd'): 'Controller Builder is an admin page; the RestDefinition CRD by name',
    ('blueprint-install-formdef', 'crd'): 'Marketplace install is an admin page; the CompositionDefinition CRD by name',
    ('blueprint-palette', 'crds'): 'Blueprint Builder is an admin page; the palette is a LIST of every non-krateo '
                                   'CRD, which has no namespaced equivalent — there is no CR that enumerates '
                                   'third-party kinds',
    ('blueprint-formdef', 'crd'): 'DEV-REACHABLE (/blueprints/{namespace}/{name}/new) but no longer REQUIRED: '
                                  'continueOnError, and the not-ready gate accepts the namespaced jsonschema '
                                  'ConfigMap on its own, so a caller who cannot read CRDs still gets the real '
                                  'form. Verified: the no-CRD path yields one value with stringSchema populated '
                                  'and no "still being registered"',
}


def render(chart):
    d = tempfile.mkdtemp()
    dst = os.path.join(d, 'c')
    shutil.copytree(os.path.join(REPO, 'helm', chart), dst)
    cy = open(os.path.join(dst, 'Chart.yaml')).read().replace('CHART_VERSION', '0.0.0').replace('APP_VERSION', '0.0.0')
    open(os.path.join(dst, 'Chart.yaml'), 'w').write(cy)
    cmd = ['helm', 'template', 'x', dst, '--namespace', NS]
    for v in EXTRA_VALUES.get(chart, []):
        cmd += ['--set', v]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode:
        raise SystemExit(f'helm template {chart} failed:\n{out.stderr[-2000:]}')
    return [d for d in yaml.safe_load_all(out.stdout) if d]


def cluster_scoped(path):
    """True when the request names no namespace, so RBAC can only satisfy it cluster-wide."""
    p = (path or '').split('?')[0]
    if not p.startswith('/'):          # a ${ … } expression: judge the literal segments it concatenates
        p = ''.join(re.findall(r'"([^"]*)"', path or ''))
    if not p.startswith('/'):
        return False
    return '/namespaces/' not in p


def main():
    findings, checked, allowed_hit = [], 0, set()
    for chart in ('portal', 'portal-agents'):
        for doc in render(chart):
            if doc.get('kind') == 'RESTAction':
                ra = doc['metadata']['name']
                for st in (doc.get('spec') or {}).get('api') or []:
                    if st.get('userAccessFilter') or st.get('endpointRef'):
                        continue
                    if not cluster_scoped(st.get('path')):
                        continue
                    if not re.search(r'/(namespaces|customresourcedefinitions)(/|$|\?)',
                                     (st.get('path') or '').split('?')[0] + '/'):
                        continue   # see SCOPE in the docstring
                    checked += 1
                    key = (ra, st.get('name'))
                    if key in ALLOWED:
                        allowed_hit.add(key)
                        continue
                    findings.append(f'{ra}/{st.get("name")}: caller-dialled cluster-scoped read of '
                                    f'{st.get("path")} — needs a ClusterRole on every persona that '
                                    f'loads this page. Give it a userAccessFilter, read a namespaced '
                                    f'equivalent, or add it to ALLOWED with the admin-only page it '
                                    f'belongs to.')
            # The requirement itself, structurally.
            if doc.get('kind') in ('ClusterRole', 'ClusterRoleBinding'):
                subjects = [s.get('name') for s in (doc.get('subjects') or [])]
                if 'devs' in subjects:
                    findings.append(f'{doc["kind"]}/{doc["metadata"]["name"]} binds the `devs` group. '
                                    f'devs carries no cluster-scoped RBAC (#261); use a Role in a namespace.')

    for key in sorted(set(ALLOWED) - allowed_hit):
        findings.append(f'{key[0]}/{key[1]} is in ALLOWED but no longer exists — drop the entry.')

    for f in findings:
        print(f'  {f}')
    print(f'\nlint-caller-cluster-reads: {checked} caller-dialled cluster-scoped step(s), '
          f'{len(allowed_hit)} allowed, {len(findings)} finding(s)')
    return len(findings)


if __name__ == '__main__':
    sys.exit(main())
