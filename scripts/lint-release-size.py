#!/usr/bin/env python3
"""lint-release-size — model the Helm release record and fail before it stops being writable.

THE FAILURE THIS EXISTS TO PREVENT, which already happened once and was invisible for two days:

Helm's secret driver stores one release record as base64(gzip(release JSON)) in a Secret's
`data.release`. Kubernetes rejects a Secret whose decoded data exceeds `MaxSecretSize` — a
compiled-in 1 MiB constant, not etcd's limit and not tunable anywhere. On 2026-09-11 chart 1.8.4
pushed the portal's record past it and every upgrade since failed at the record write with:

    Secret "sh.helm.release.v1.portal.v36" is invalid: data: Too long: may not be more than
    1048576 bytes

Nothing looked wrong. The composition-dynamic-controller applies the content FIRST (its
three-way merge is "the only mutation") and writes the release record SECOND, so the cluster
converged correctly while `helm history` silently froze three releases back. The error is one-shot
per chart change — the next reconcile finds nothing changed, never retries the write, and the
composition goes on reporting Ready/Synced.

WHAT TO MEASURE, and what NOT to. Raw manifest bytes and CR count both move OPPOSITE to the record
at times: 1.8.4 -> 1.8.14 shed 100 documents and ~97 KB of JSON while the record GREW, because
per-document controller labels dominate a small CR and the record is compressed. So this models the
real encoding rather than counting anything.

CALIBRATION. The first version of this gate modelled only manifest + templates, rendered with
chart DEFAULTS, and read 87.8% while the live portal.v37 record was 91.4% — under-reporting by
3.6 points on a gate whose whole job is to warn early. Two real omissions, both now modelled:
the install's value flags (which add documents), and CDC's per-document label block (~226 KB).
Re-derive against the live record with:

    kubectl --kubeconfig <kc> --context <ctx> get secret -n krateo-system \
      -l owner=helm,name=portal,status=deployed -o jsonpath='{.data.release}' \
      | base64 -d | base64 -d | gunzip | wc -c
"""
import base64
import glob
import gzip
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

MAX_SECRET = 1 * 1024 * 1024      # k8s.io/api/core/v1 MaxSecretSize — compiled in, not tunable
FAIL_AT = 0.95
WARN_AT = 0.85

# The install turns these on; chart defaults do not. Rendering with the defaults under-reports,
# because the flags bring in extra documents (the cyberjoker RBAC file alone is 20).
INSTALL_VALUES = {
    'enableAdminUser': True,
    'enableCyberjokerUser': True,
    'enableDemoSystemNamespace': True,
}

# CDC's post-renderer stamps this label block onto EVERY document on the way to the cluster, so it
# is in the stored manifest and in nothing helm template produces. Measured at 405 B per document
# against the live portal.v37 record; at ~550 documents that is ~226 KB — more than a fifth of the
# entire budget, and invisible to any model that stops at `helm template`.
#
# This is why the first version of this gate read 87.8% while the live record was 91.4%: it is not
# a fudge factor, it is a real part of the object that was simply not being counted.
#
# It is INTERLEAVED into each document, not appended in a run, because the number that matters is
# the COMPRESSED one and placement decides it. Two earlier attempts both failed the same way: N
# bytes of a repeated character gzips to nothing, and 554 identical blocks appended consecutively
# gzip to nearly nothing too. In the real manifest each block sits between varying documents, so
# every occurrence costs a back-reference — ~200 KB raw becomes ~29 KB compressed, and only
# interleaving reproduces that.
# The residual after everything above is modelled. The model still sits ~2.9% under the live
# record because the install supplies values this script cannot see (CDC injects composition
# identity, and the Installer carries its own overrides), so some documents render differently
# here than on the cluster. Rather than keep guessing at those values, the gap is MEASURED and
# applied — 931,780 modelled against 958,700 live for portal.v37 on krateo-057, 2026-09-14.
#
# Re-derive it whenever the install's values change, or whenever the live number is to hand:
#   live / modelled, where live is the wc -c of the decoded record (see the command above).
# A stated, reproducible correction is honest; a model that silently reads 2.5 points light on a
# gate whose entire job is to warn early is not.
LIVE_CALIBRATION = 1.029

CDC_LABEL_BLOCK = '''
  labels:
    krateo.io/composition-group: composition.krateo.io
    krateo.io/composition-id: 573b884a-ca8c-4f96-9c17-d1b563d504fc
    krateo.io/composition-installed-version: v1-8-17
    krateo.io/composition-kind: Portal
    krateo.io/composition-name: portal
    krateo.io/composition-namespace: krateo-system
    krateo.io/krateo-namespace: krateo-system'''
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Every chart in this repo that becomes a composition gets its own release record and therefore its
# own 1 MiB budget — which is the entire point of having split portal-agents out of portal.
CHARTS = [os.path.join(REPO, 'helm', name) for name in ('portal', 'portal-agents')]


def render(chart_dir):
    """helm template, with the chart's CHART_VERSION placeholder swapped for something helm accepts."""
    tmp = tempfile.mkdtemp()
    try:
        staged = os.path.join(tmp, 'portal')
        shutil.copytree(chart_dir, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = io.open(meta, encoding='utf-8').read()
        io.open(meta, 'w', encoding='utf-8').write(text.replace('version: CHART_VERSION', 'version: 0.0.0'))
        args = ['helm', 'template', 'portal', staged, '--namespace', 'krateo-system']
        for key, value in INSTALL_VALUES.items():
            args += ['--set', f'{key}={str(value).lower()}']
        proc = subprocess.run(args, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f'helm template failed:\n{proc.stderr.strip()}')
        return proc.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def modelled_size(chart_dir):
    """Bytes Kubernetes would validate for the release Secret's `data`."""
    manifest = render(chart_dir)
    chunks = manifest.split('\n---\n')
    manifest = '\n---\n'.join(chunk + CDC_LABEL_BLOCK for chunk in chunks)   # see CDC_LABEL_BLOCK

    templates = []
    for path in sorted(glob.glob(os.path.join(chart_dir, 'templates', '*.yaml'))):
        templates.append({'name': 'templates/' + os.path.basename(path),
                          'data': base64.b64encode(io.open(path, 'rb').read()).decode()})
    files = []
    for path in sorted(glob.glob(os.path.join(chart_dir, 'files', '**', '*'), recursive=True)):
        if os.path.isfile(path):
            files.append({'name': os.path.relpath(path, chart_dir),
                          'data': base64.b64encode(io.open(path, 'rb').read()).decode()})

    def read(name):
        path = os.path.join(chart_dir, name)
        return io.open(path, encoding='utf-8').read() if os.path.isfile(path) else ''

    release = {
        'name': 'portal', 'version': 1, 'namespace': 'krateo-system', 'manifest': manifest,
        'chart': {
            'metadata': {'name': 'portal', 'version': '0.0.0'},
            'templates': templates, 'files': files,
            'schema': base64.b64encode(read('values.schema.json').encode()).decode(),
            'values': read('values.yaml'),
        },
        'config': INSTALL_VALUES,
        'info': {'status': 'deployed'},
    }
    modelled = len(base64.b64encode(gzip.compress(json.dumps(release).encode(), 9)))
    return int(modelled * LIVE_CALIBRATION), len(templates)


def main():
    worst = 0
    for chart in CHARTS:
        if not os.path.isdir(chart):
            continue
        size, files = modelled_size(chart)
        pct = size / MAX_SECRET
        worst = max(worst, pct)
        print(f'lint-release-size: {os.path.basename(chart)} — modelled release record {size:,} B '
              f'across {files} templates = {pct * 100:.1f}% of the {MAX_SECRET:,} B Secret-data cap '
              f'(incl. the {LIVE_CALIBRATION}x measured live correction)')
    pct = worst

    if pct >= FAIL_AT:
        print(f'  FAILED: at or above {FAIL_AT * 100:.0f}%. The next feature section will push this '
              f'past the cap, and when it does the symptom is NOT an error — the content still '
              f'applies and helm history silently stops advancing. Shrink the chart or split a '
              f'subsystem out before merging. See design/ for the options that were costed.', file=sys.stderr)
        return 1
    if pct >= WARN_AT:
        print(f'  WARNING: above {WARN_AT * 100:.0f}%. Headroom is {MAX_SECRET - size:,} B; a feature '
              f'section has historically cost ~18% of the cap, so this is roughly one away.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
