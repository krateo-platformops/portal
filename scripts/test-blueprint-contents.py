#!/usr/bin/env python3
"""A blueprint's "What it creates" card, over the chart's OWN jq.

core-provider records what a blueprint's chart creates at its default values in the
CompositionDefinition's status.contents (core-provider#599, merged as #605): chartVersion,
values: default, kinds[{apiVersion, kind, count}] (at most 50, count descending), `omitted` (kinds
past the cap, ABSENT when complete) and `unknown` (a string, set INSTEAD of kinds when the chart
cannot render at its defaults). Approved mockup: https://claude.ai/artifact/XTHrG6nao9fagYyEf7gL7q.
This runs restaction.blueprint-detail's filter, the Card's items and the right column's items, as
they ship, over:

  known     core-provider's own envtest sample (~/krateo/samples/cd-contents-sample.yaml, inlined)
  capped    kinds plus `omitted`: "and N more kinds not listed"
  stale     a recorded chartVersion older than the installed one: "read from chart …", orange
  unknown   the chart cannot render at its defaults: core-provider's words, never an empty list
  empty     rendered, creates nothing: said in words
  none      no status.contents (an older core-provider): "Contents not available."
A control: dropping the unknown branch must fail the unknown case.

And the blueprint page's LATEST VERSION (UX audit BP-2), over the same filter and the Details card:
the catalog as the Marketplace reads it (the ConfigMap catalog with the live index merged in), newest
by version number, and "not known" with the reason when the catalog holds no version of the chart
(an OCI or URL install, or an unreadable catalog), never the installed version and "Up to date".
Neither Details nor Metadata repeats the header's Created (BP-4). BLUEPRINTS_CHART=<origin/main's
chart> is the control: those checks fail there.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.environ.get('BLUEPRINTS_CHART') or os.path.join(os.path.dirname(HERE), 'helm', 'blueprints-page')
MARKETPLACE = os.environ.get('MARKETPLACE_CHART') or os.path.join(os.path.dirname(HERE), 'helm', 'marketplace-page')
JQ = os.environ.get('JQ', 'jq')

# core-provider#605's envtest output for its own chart at default values (version placeholder).
SAMPLE = {'chartVersion': '0.0.0-envtest', 'values': 'default', 'kinds': [
    {'apiVersion': 'v1', 'count': 7, 'kind': 'ConfigMap'},
    {'apiVersion': 'rbac.authorization.k8s.io/v1', 'count': 3, 'kind': 'ClusterRole'},
    {'apiVersion': 'rbac.authorization.k8s.io/v1', 'count': 3, 'kind': 'ClusterRoleBinding'},
    {'apiVersion': 'apps/v1', 'count': 2, 'kind': 'Deployment'},
    {'apiVersion': 'v1', 'count': 2, 'kind': 'ServiceAccount'},
    {'apiVersion': 'admissionregistration.k8s.io/v1', 'count': 1, 'kind': 'MutatingAdmissionPolicy'},
    {'apiVersion': 'admissionregistration.k8s.io/v1', 'count': 1, 'kind': 'MutatingAdmissionPolicyBinding'},
    {'apiVersion': 'admissionregistration.k8s.io/v1', 'count': 1, 'kind': 'ValidatingAdmissionPolicy'},
    {'apiVersion': 'admissionregistration.k8s.io/v1', 'count': 1, 'kind': 'ValidatingAdmissionPolicyBinding'},
    {'apiVersion': 'policy/v1', 'count': 1, 'kind': 'PodDisruptionBudget'},
    {'apiVersion': 'v1', 'count': 1, 'kind': 'Service'}]}


def render(chart=CHART):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', chart, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        out = subprocess.run(['helm', 'template', 't', copy], capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def jq(prog, data):
    proc = subprocess.run([JQ, '-c', prog], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr.strip())
    return json.loads(proc.stdout)


def expr(doc, for_path):
    e = next(t['expression'] for t in doc['spec']['widgetDataTemplate'] if t['forPath'] == for_path).strip()
    assert e.startswith('${') and e.endswith('}'), e[:40]
    return e[2:-1]


def detail(prog, contents, version='0.0.0-envtest'):
    status = {'conditions': [{'type': 'Ready', 'status': 'True', 'reason': 'Available'}]}
    if contents is not None:
        status['contents'] = contents
    compdef = {'apiVersion': 'core.krateo.io/v1alpha1', 'kind': 'CompositionDefinition',
               'metadata': {'name': 'core-provider', 'namespace': 'krateo-system', 'creationTimestamp': '2026-11-21T10:00:00Z'},
               'spec': {'chart': {'repo': 'core-provider', 'version': version}}, 'status': status}
    data = {'name': 'core-provider', 'namespace': 'krateo-system', 'compdef': compdef,
            'catalog': {'entries': {}}, 'comps': {'items': []}}
    return jq(prog, data)


def latest_failures(by, prog):
    """BP-2 / BP-4: the latest version the blueprint page states, and its Details and Metadata rows."""
    extra = expr(by[('Card', 'card-blueprint-detail-info')], 'extra')
    info = expr(by[('Descriptions', 'descriptions-blueprint-detail-info')], 'items')
    meta = expr(by[('Descriptions', 'descriptions-blueprint-detail-metadata')], 'items')
    bad = []

    def entry(name, version, owned=False):
        e = {'name': name, 'version': version, 'urls': [f'https://krateo-blueprints.github.io/charts/blueprints/{name}-{version}.tgz']}
        if owned:
            e['sources'] = [f'https://github.com/krateo-blueprints/{name}']
        return e

    def page(chart, catalog, live=None, **more):
        compdef = {'apiVersion': 'core.krateo.io/v1alpha1', 'kind': 'CompositionDefinition',
                   'metadata': {'name': 'acme-feedback', 'namespace': 'krateo-system', 'creationTimestamp': '2026-10-07T10:00:00Z', 'uid': 'u'},
                   'spec': {'chart': chart}, 'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        data = {'name': 'acme-feedback', 'namespace': 'krateo-system', 'compdef': compdef,
                'catalog': {'entries': catalog}, 'comps': {'items': []}, **more}
        if live is not None:
            data['catalogLive'] = {'entries': live}
        out = jq(prog, data)
        rows = {r['label']: r['value'] for r in jq(info, out)}
        return out, rows, jq(extra, out)

    def check(label, got, want):
        if got != want:
            bad.append(label)
            print(f'FAIL latest, {label}: got {got!r}, want {want!r}')

    helm_repo = {'url': 'https://krateo-blueprints.github.io/charts/blueprints', 'repo': 'acme-feedback', 'version': '0.1.0'}
    # The live index holds 0.1.1, the ConfigMap catalog 0.1.0: the Marketplace says 0.1.1, so does this page.
    out, rows, tag = page(helm_repo, {'acme-feedback': [entry('acme-feedback', '0.1.0')]},
                          live={'acme-feedback': [entry('acme-feedback', '0.1.1', owned=True), entry('acme-feedback', '0.1.0', owned=True)]})
    check('live index read: latest', (out['latestVersion'], rows['Latest version'], out['updateAvailable'], tag),
          ('0.1.1', '0.1.1', True, 'Update available → 0.1.1'))
    check('live index read: the picker offers it', out['availableVersions'], ['0.1.0', '0.1.1'])
    # Newest by version number, not by string.
    out, rows, tag = page(dict(helm_repo, version='0.1.9'), {'acme-feedback': [entry('acme-feedback', '0.1.9'), entry('acme-feedback', '0.1.10')]})
    check('0.1.10 is newer than 0.1.9', (out['latestVersion'], tag), ('0.1.10', 'Update available → 0.1.10'))
    # Installed above the catalog: no "update" to an older version.
    out, rows, tag = page(dict(helm_repo, version='0.2.0'), {'acme-feedback': [entry('acme-feedback', '0.1.0')]})
    check('installed above the catalog', (out['updateAvailable'], tag), (False, 'Up to date'))
    # An OCI install: nothing here reads its registry, so the latest version is not known.
    oci = {'url': 'oci://ghcr.io/krateo-blueprints/charts/acme-feedback', 'version': '0.1.0'}
    out, rows, tag = page(oci, {'acme-feedback': [entry('acme-feedback', '0.1.1')]})
    check('OCI install: not known, no tag', (out['latestVersion'], out['updateAvailable'], tag), ('', False, ''))
    check('OCI install: the row says why, and what the Marketplace lists', rows['Latest version'],
          'not known: installed from an OCI registry, which is not read here (the Marketplace lists acme-feedback 0.1.1 from its own catalog)')
    out, rows, tag = page(dict(oci, url='oci://ghcr.io/krateo-platformops/charts/observability-page'), {})
    check('OCI install, nothing of that name in the catalog', rows['Latest version'], 'not known: installed from an OCI registry, which is not read here')
    # The catalog could not be read.
    out, rows, tag = page(helm_repo, {}, catalogError=[{'message': 'configmaps "blueprints-catalog-index" not found'}])
    check('catalog unread', (rows['Latest version'], tag), ('not known: the catalog could not be read', ''))
    out, rows, tag = page(helm_repo, {})
    check('not in the catalog', (rows['Latest version'], tag), ('not known: this chart is not in the catalog', ''))
    # Created is the header's, said once (BP-4).
    check('Details has no Created', 'Created' in rows, False)
    check('Metadata has no Created', [r['label'] for r in jq(meta, out)], ['Namespace', 'UID'])
    return bad


def main():
    docs = render()
    by = {(d['kind'], d['metadata']['name']): d for d in docs}
    prog = by[('RESTAction', 'blueprint-detail')]['spec']['filter']
    card = by[('Card', 'card-blueprint-detail-contents')]
    items_prog = expr(card, 'items')
    col_prog = expr(by[('Flex', 'blueprint-detail-col-right')], 'items')
    bad = []

    def check(label, got, want):
        if got != want:
            bad.append(label)
            print(f'FAIL {label}: got {got!r}, want {want!r}')

    def items(out):
        return [i['resourceRefId'] for i in jq(items_prog, out)]

    known = detail(prog, SAMPLE)['contents']
    check('known: state', known['state'], 'known')
    check('known: kinds in core-provider order', [k['kind'] for k in known['kinds']], [k['kind'] for k in SAMPLE['kinds']])
    check('known: a row', known['kinds'][0], {'kind': 'ConfigMap', 'group': 'v1', 'count': 7, 'countLabel': '×7'})
    check('known: header', known['extra'], '11 kinds')
    check('known: footer', known['foot'], 'At default values · chart 0.0.0-envtest · 23 resources of 11 kinds')
    check('known: card shows the list and the footer', items({'contents': known}),
          ['list-blueprint-detail-contents', 'paragraph-blueprint-detail-contents-foot'])

    capped = detail(prog, dict(SAMPLE, omitted=17))['contents']
    check('capped: header counts the omitted kinds', capped['extra'], '28 kinds')
    check('capped: footer says so', capped['foot'], 'At default values · chart 0.0.0-envtest · and 17 more kinds not listed')

    stale = detail(prog, dict(SAMPLE, chartVersion='0.2.0'), version='0.3.0')['contents']
    check('stale: flagged', stale['stale'], True)
    check('stale: header names the version read', stale['extra'], 'from 0.2.0')
    check('stale: footer', stale['foot'], 'At default values · read from chart 0.2.0; the installed chart is 0.3.0 and is being read')

    why = "the chart's default values are not valid for it: clusterName is required"
    unknown = detail(prog, {'chartVersion': '0.3.0', 'values': 'default', 'unknown': why}, version='0.3.0')['contents']
    check('unknown: state', unknown['state'], 'unknown')
    check('unknown: core-provider\'s words', unknown['body'], 'core-provider: ' + why)
    check('unknown: card says why, never a list', items({'contents': unknown}),
          ['paragraph-blueprint-detail-contents-title-unknown', 'paragraph-blueprint-detail-contents-body',
           'paragraph-blueprint-detail-contents-hint'])

    empty = detail(prog, {'chartVersion': '0.3.0', 'values': 'default', 'kinds': []}, version='0.3.0')['contents']
    check('empty: said in words', (empty['state'], empty['title']), ('empty', 'Creates nothing at default values.'))
    check('empty: card shows no list', items({'contents': empty}),
          ['paragraph-blueprint-detail-contents-title-none', 'paragraph-blueprint-detail-contents-body'])

    unread = jq(prog, {'name': 'x', 'namespace': 'krateo-system', 'compdef': None, 'catalog': {'entries': {}}, 'comps': {'items': []}})['contents']
    check('blueprint page, CD not read: not available, never "not installed"', unread['state'], 'none')
    none = detail(prog, None)['contents']
    check('none: older core-provider', (none['state'], none['title'], none['extra']), ('none', 'Contents not available.', '—'))

    check('column: the card leads, for a reader who may not update',
          [i['resourceRefId'] for i in jq(col_prog, {'may': {'update': False}})], ['card-blueprint-detail-contents'])
    check('column: then the update card',
          [i['resourceRefId'] for i in jq(col_prog, {'may': {'update': True}, 'availableVersions': ['0.3.0'], 'currentVersion': '0.3.0'})],
          ['card-blueprint-detail-contents', 'card-blueprint-detail-update'])

    # The marketplace page: the same states, "Known once installed" before it is, and an honest
    # "not known here" when the installed blueprints could not be read.
    mdocs = {(d['kind'], d['metadata']['name']): d for d in render(MARKETPLACE)}
    mprog = mdocs[('RESTAction', 'marketplace-detail')]['spec']['filter']
    base = {'name': 'core-provider', 'catalog': {'entries': {'core-provider': [{'version': '0.0.0-envtest', 'name': 'core-provider'}]}}}
    m_not = jq(mprog, dict(base, compdefs=[]))['contents']
    check('marketplace, not installed', (m_not['state'], m_not['title'], m_not['extra']), ('notInstalled', 'Known once installed.', 'not installed'))
    m_in = jq(mprog, dict(base, compdefs=[{'name': 'core-provider', 'version': '0.0.0-envtest', 'namespace': 'krateo-system',
                                           'status': {'contents': SAMPLE}}]))['contents']
    check('marketplace, installed: the blueprint page\'s list', (m_in['state'], m_in['extra'], m_in['foot']),
          ('known', '11 kinds', 'At default values · chart 0.0.0-envtest · 23 resources of 11 kinds'))
    m_err = jq(mprog, dict(base, compdefs=[], compdefsError=[{'message': 'forbidden'}]))['contents']
    check('marketplace, installed list unreadable: not "Known once installed"', (m_err['state'], m_err['title']), ('none', 'Not known here.'))
    m_col = [i['resourceRefId'] for i in mdocs[('Flex', 'marketplace-detail-col-left')]['spec']['widgetData']['items']]
    check('marketplace: the card follows About', m_col, ['card-marketplace-detail-about', 'card-marketplace-detail-contents'])

    bad.extend(latest_failures(by, prog))

    control = prog.replace('elif ((($ct.unknown // "") | tostring) != "") then', 'elif false then', 1)
    if control == prog or detail(control, {'chartVersion': '0.3.0', 'values': 'default', 'unknown': why}, version='0.3.0')['contents']['state'] == 'unknown':
        bad.append('control')
        print('FAIL control: dropping the unknown branch did not change the unknown case')

    print(f'blueprint contents: 6 states + card + column + marketplace, {len(bad)} failure(s)')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
