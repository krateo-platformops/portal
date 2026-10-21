#!/usr/bin/env python3
"""
lint-ref-namespace — in a page chart, a resourcesRefs item pointing at a WIDGET is a location; one
pointing at anything else is DATA, and the two must not share a namespace value.

THE RULE, and why it needs a machine. When the portal's pages were split into per-namespace charts
(#261) every `namespace:` key had to be classified: does it say where this object LIVES, or where
the data it touches lives? The first becomes `.Values.namespace` (the page namespace, which is the
RBAC boundary); the second becomes `.Values.dataNamespace`, because the CRs and ConfigMaps the
pages read and write did not move.

I got that distinction right for RESTAction paths — which is why `dataNamespace` exists at all —
and wrong for resourcesRefs. A ref pointing at a sibling widget is a location. A ref pointing at
`compositiondefinitions`, `secrets`, `proposals`, `deployments`, `kubernetestargets` is a WRITE OR
READ TARGET, and repointing it at the page namespace sends the write to the wrong place.

WHAT IT COST. marketplace-page 1.8.78 shipped and rolled to krateo-057 with:

    {{/* POST target — create a CompositionDefinition in the release namespace. */}}
    - id: create-compdef
      namespace: {{ $.Values.namespace }}        # krateo-marketplace. The comment says otherwise.
      resource: compositiondefinitions
      verb: POST

The Marketplace Install flow would have created a CompositionDefinition in krateo-marketplace
instead of krateo-system. It never fired — zero compositiondefinitions in that namespace — so
nothing was damaged, but it was live.

Eleven such refs existed across the eight page charts. **One** was caught by a test
(test-platform-review noticed a Reject PATCH targeting the wrong namespace). The other ten were
silent, including both shipped ones, because test-builder-install asserts that the flow WORKS and
not WHERE IT WRITES — a suite that exercises a POST without asserting its namespace cannot see this
class at all. Hence a lint rather than another fixture.

HOW. Each page chart is rendered with sentinel namespaces (`namespace=NSPAGE`,
`dataNamespace=NSDATA`) so the two are distinguishable in the output no matter what the defaults
are. Then every resourcesRefs item is classified by its `resource`:

    resource is a widget plural      -> expect NSPAGE (or a deliberate cross-chart value)
    resource is anything else        -> expect NSDATA; NSPAGE is a finding

Widget plurals come from the released frontend-crds chart, so the lint tracks the real widget set
rather than a hand-kept list. Charts without a `namespace`/`dataNamespace` pair (portal,
portal-agents) are skipped: they have one namespace and the distinction does not arise.

Usage: lint-ref-namespace.py. Exit code = findings.
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NSPAGE, NSDATA = 'zz-sentinel-page', 'zz-sentinel-data'
WIDGET_GROUP = 'widgets.templates.krateo.io'

# A ref whose namespace is deliberately neither sentinel: an object another chart owns. Keyed by
# (chart, ref id) -> the value expected and why.
CROSS_CHART = {
    ('observability-page', 'stat-healthy'): 'the Dashboard Statistic that stays with the portal',
    ('observability-page', 'stat-failed'): 'the Dashboard Statistic that stays with the portal',
}


def widget_plurals():
    out = set()
    for d in ('/tmp/crdslatest/frontend-crds', os.environ.get('FRONTEND_CRDS_DIR') or ''):
        if not d or not os.path.isdir(d):
            continue
        for p in glob.glob(os.path.join(d, 'templates', '*.crd.yaml')):
            doc = yaml.safe_load(open(p, encoding='utf-8'))
            if doc and (doc.get('spec') or {}).get('group') == WIDGET_GROUP:
                out.add(doc['spec']['names']['plural'])
        if out:
            return out
    # no local copy: pull the released chart
    tmp = tempfile.mkdtemp()
    r = subprocess.run(['helm', 'pull', 'oci://ghcr.io/krateo-platformops/charts/frontend-crds',
                        '--untar', '-d', tmp], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'could not obtain frontend-crds to learn the widget plurals:\n{r.stderr[-500:]}')
    for p in glob.glob(os.path.join(tmp, 'frontend-crds', 'templates', '*.crd.yaml')):
        doc = yaml.safe_load(open(p, encoding='utf-8'))
        if doc and (doc.get('spec') or {}).get('group') == WIDGET_GROUP:
            out.add(doc['spec']['names']['plural'])
    return out


def page_charts():
    """Charts that declare BOTH namespace and dataNamespace — i.e. the split page charts."""
    out = []
    for cy in sorted(glob.glob(os.path.join(REPO, 'helm', '*', 'Chart.yaml'))):
        d = os.path.dirname(cy)
        vf = os.path.join(d, 'values.yaml')
        if not os.path.exists(vf):
            continue
        v = yaml.safe_load(open(vf, encoding='utf-8')) or {}
        if 'namespace' in v and 'dataNamespace' in v:
            out.append(d)
    return out


def render(chart_dir):
    tmp = tempfile.mkdtemp()
    dst = os.path.join(tmp, 'c')
    shutil.copytree(chart_dir, dst)
    meta = os.path.join(dst, 'Chart.yaml')
    t = open(meta, encoding='utf-8').read()
    open(meta, 'w', encoding='utf-8').write(
        t.replace('CHART_VERSION', '0.0.0').replace('APP_VERSION', '0.0.0'))
    cmd = ['helm', 'template', 'x', dst, '--namespace', 'krateo-system',
           '--set', f'namespace={NSPAGE}', '--set', f'dataNamespace={NSDATA}',
           '--set', 'observability.snowplowSli=true']
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'helm template {chart_dir} failed:\n{r.stderr[-800:]}')
    return [d for d in yaml.safe_load_all(r.stdout) if d]


def main():
    plurals = widget_plurals()
    if not plurals:
        print('  lint-ref-namespace: learned NO widget plurals — refusing to report a pass')
        return 1
    findings, checked = [], 0
    charts = page_charts()
    if not charts:
        print('  lint-ref-namespace: found no page chart (one declaring namespace AND '
              'dataNamespace) — refusing to report a pass on an empty set')
        return 1
    for cd in charts:
        chart = os.path.basename(cd)
        for doc in render(cd):
            if not str(doc.get('apiVersion', '')).startswith(WIDGET_GROUP + '/'):
                continue
            owner = f"{doc.get('kind')}/{(doc.get('metadata') or {}).get('name')}"
            for item in (((doc.get('spec') or {}).get('resourcesRefs') or {}).get('items') or []):
                res, ns, rid = item.get('resource'), item.get('namespace'), item.get('id')
                if not res or ns is None:
                    continue
                checked += 1
                is_widget = res in plurals
                if (chart, rid) in CROSS_CHART:
                    if ns in (NSPAGE, NSDATA):
                        findings.append(f'{chart}: {owner} ref {rid} ({res}) is declared a '
                                        f'cross-chart ref ({CROSS_CHART[(chart, rid)]}) but resolves '
                                        f'to {ns}')
                    continue
                if is_widget and ns == NSDATA:
                    findings.append(f'{chart}: {owner} ref {rid} points at the WIDGET {res} but in '
                                    f'dataNamespace. A widget this chart ships lives in '
                                    f'.Values.namespace.')
                if not is_widget and ns == NSPAGE:
                    findings.append(f'{chart}: {owner} ref {rid} points at {res} (verb '
                                    f'{item.get("verb", "GET")}), which is NOT a widget — so this '
                                    f'names where the DATA is, not where the page lives. Use '
                                    f'.Values.dataNamespace. Pointing it at the page namespace '
                                    f'sends the read or write to a namespace that holds no such '
                                    f'object (portal#319 shipped exactly this for the Marketplace '
                                    f'Install POST).')
    for f in findings:
        print(f'  {f}')
    print(f'\nlint-ref-namespace: {len(charts)} page chart(s), {checked} resourcesRefs item(s), '
          f'{len(plurals)} widget plural(s), {len(findings)} finding(s)')
    return len(findings)


if __name__ == '__main__':
    sys.exit(main())
