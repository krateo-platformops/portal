#!/usr/bin/env python3
"""
lint-crd-retention — a chart that OWNS a CRD must not destroy its data when it is uninstalled.

WHAT HAPPENED. service-desk-page ships `tickets.servicedesk.krateo.io` and the namespace the
tickets live in, and shipped BOTH with no `helm.sh/resource-policy` at all — 0 of 30 rendered
objects carried one. Deleting a CRD cascade-deletes every CR of that kind in the cluster, so a
`helm uninstall` of that chart would have destroyed every ticket anyone had submitted.

WHY THAT IS NOT A RARE ADMINISTRATIVE ACT. Uninstall is on the path of an ordinary component
version bump: the CompositionDefinition prunes the release and recreates it. Nobody has to run
`helm uninstall` by hand for this to fire — a pin bump is enough. The defect was found by the
installer session reading the rendered chart, not by any gate here.

THE TWO CLAUSES, because the first alone is not sufficient.

  1. Every CRD a chart renders carries `helm.sh/resource-policy: keep`.

  2. Every namespace DECLARED below as holding a CRD's objects carries `keep` too.
     Deleting a Namespace deletes everything inside it regardless of the CRD's own policy, so a
     `keep` on the CRD while its objects' namespace is pruned protects nothing — the CRD survives
     empty.

     Which namespace holds a kind's objects is NOT derivable from the manifest, so it is declared
     in DATA_NAMESPACES rather than guessed. The first version of this gate guessed: it required
     a CRD-owning chart to keep one of the namespaces it creates, and immediately produced a false
     positive on `portal`, whose AuditRecords live in the release namespace while the only
     namespace it creates (`demo-system`) is an unrelated RBAC sandbox. A gate that reports a
     defect where there is none costs more than the one it catches, because the next real finding
     reads like noise. An explicit table also forces a decision for every NEW CRD-owning chart,
     which a heuristic does not.

WHAT IT DELIBERATELY DOES NOT CHECK. A chart that renders no CRD is not examined: widgets, pages
and RBAC are rebuilt from the chart on reinstall, so pruning them loses nothing. Retention follows
the DATA, and the only data these charts own is in their own CRDs.

Usage: lint-crd-retention.py. Exit code = findings.
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY = 'helm.sh/resource-policy'
# Discovered, not listed, for the reason lint-pinned-crds.py records: a hardcoded chart list went
# stale three times as the page-per-namespace split added charts. builder-publish is excluded on
# the same grounds there — it is a blueprint chart CDC renders with a composition's own values.
CHARTS = tuple(sorted(os.path.basename(os.path.dirname(p))
                      for p in glob.glob(os.path.join(REPO, 'helm', '*', 'Chart.yaml'))
                      if os.path.basename(os.path.dirname(p)) != 'builder-publish'))

# chart -> the namespaces IT CREATES that hold its own CRs, each of which must also be kept.
# An empty tuple is a positive statement, not a blank: the objects live somewhere this chart does
# not create (typically the release namespace), so no namespace of ours can defeat the CRD's keep.
# A CRD-owning chart missing from this table is a finding — the question has to be answered once
# per chart, and the answer is not in the manifest.
DATA_NAMESPACES = {
    # service-desk-page is no longer here: it stopped rendering the Ticket CRD when the service desk
    # moved to Zendesk, so this gate no longer examines it. Its namespace still carries `keep` by the
    # chart's own choice (namespace.yaml), because the Tickets filed before the move are still in it;
    # test-service-desk.py asserts that, since this table can only speak for CRD-owning charts.
    # AuditRecords are written into the release namespace (settings-page's audit-log RESTAction
    # reads /namespaces/{{ .Release.Namespace }}/auditrecords). `demo-system`, the only namespace
    # this chart creates, is an RBAC sandbox for the demo application kinds.
    'portal': (),
}


def render(chart):
    """Render a chart at its defaults, with the release-time placeholders substituted."""
    work = tempfile.mkdtemp()
    try:
        dst = os.path.join(work, chart)
        shutil.copytree(os.path.join(REPO, 'helm', chart), dst)
        cy = os.path.join(dst, 'Chart.yaml')
        with open(cy) as fh:
            text = fh.read()
        with open(cy, 'w') as fh:
            fh.write(re.sub(r'CHART_VERSION|SOURCE_REF|APP_VERSION', '0.0.0', text))
        out = subprocess.run(['helm', 'template', chart, dst],
                             capture_output=True, text=True)
        if out.returncode:
            return None, out.stderr.strip().splitlines()[-1:] or ['render failed']
        return [d for d in yaml.safe_load_all(out.stdout) if isinstance(d, dict)], None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def policy_of(doc):
    return ((doc.get('metadata') or {}).get('annotations') or {}).get(POLICY)


def main():
    findings = 0
    crds_seen = 0
    charts_with_crds = 0

    for chart in CHARTS:
        docs, err = render(chart)
        if docs is None:
            # A chart that does not render is a chart whose CRDs were never looked at. Reporting
            # that as a pass is how a gate goes green having checked nothing.
            print(f'FAIL {chart}: did not render, so its CRDs could NOT be checked: {err[0]}')
            findings += 1
            continue

        crds = [d for d in docs if d.get('kind') == 'CustomResourceDefinition']
        if not crds:
            continue
        charts_with_crds += 1
        crds_seen += len(crds)

        for crd in crds:
            name = (crd.get('metadata') or {}).get('name', '<unnamed>')
            if policy_of(crd) != 'keep':
                print(f'FAIL {chart}: CRD {name} has {POLICY}={policy_of(crd)!r}, not "keep". '
                      f'Deleting it cascade-deletes every {name.split(".")[0]} in the cluster, '
                      f'and an uninstall happens on every component version bump.')
                findings += 1

        if chart not in DATA_NAMESPACES:
            print(f'FAIL {chart}: owns a CRD but is not in DATA_NAMESPACES. Name the namespaces '
                  f'this chart creates that hold its CRs — or an empty tuple if they live '
                  f'somewhere it does not create. The manifest does not say, so someone has to.')
            findings += 1
            continue

        rendered_ns = {(d.get('metadata') or {}).get('name') for d in docs
                       if d.get('kind') == 'Namespace'}
        for ns_name in DATA_NAMESPACES[chart]:
            if ns_name not in rendered_ns:
                print(f'FAIL {chart}: DATA_NAMESPACES names {ns_name!r}, which this chart does '
                      f'not render (it renders {sorted(rendered_ns) or "none"}). The table is '
                      f'stale — a renamed or removed namespace leaves this clause checking '
                      f'nothing.')
                findings += 1
                continue
            ns = next(d for d in docs if d.get('kind') == 'Namespace'
                      and (d.get('metadata') or {}).get('name') == ns_name)
            if policy_of(ns) != 'keep':
                print(f'FAIL {chart}: namespace {ns_name} holds this chart\'s CRs but has '
                      f'{POLICY}={policy_of(ns)!r}, not "keep". Deleting a namespace deletes its '
                      f'contents whatever the CRD says, so the CRD would survive empty.')
                findings += 1

    print(f'checked {len(CHARTS)} chart(s); {charts_with_crds} own a CRD; '
          f'{crds_seen} CRD(s) examined; {findings} finding(s)')
    if crds_seen == 0:
        # The population, printed because a zero is otherwise indistinguishable from a gate that
        # found nothing to look at. If no chart here owns a CRD any more, this gate is dead code
        # and should be deleted rather than left passing vacuously.
        print('FAIL: no chart under helm/ renders a CRD — this gate checked nothing')
        return 1
    return findings


if __name__ == '__main__':
    sys.exit(main())
