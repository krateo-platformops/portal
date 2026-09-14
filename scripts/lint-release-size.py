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

The model is calibrated against a real record: portal v35 on krateo-057 stored 993,412 bytes, and
this pipeline reproduces it to within ~1.2%.
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
CHART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'helm', 'portal')


def render(chart_dir):
    """helm template, with the chart's CHART_VERSION placeholder swapped for something helm accepts."""
    tmp = tempfile.mkdtemp()
    try:
        staged = os.path.join(tmp, 'portal')
        shutil.copytree(chart_dir, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = io.open(meta, encoding='utf-8').read()
        io.open(meta, 'w', encoding='utf-8').write(text.replace('version: CHART_VERSION', 'version: 0.0.0'))
        proc = subprocess.run(['helm', 'template', 'portal', staged, '--namespace', 'krateo-system'],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f'helm template failed:\n{proc.stderr.strip()}')
        return proc.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def modelled_size(chart_dir):
    """Bytes Kubernetes would validate for the release Secret's `data`."""
    manifest = render(chart_dir)
    templates = []
    for path in sorted(glob.glob(os.path.join(chart_dir, 'templates', '*.yaml'))):
        templates.append({'name': 'templates/' + os.path.basename(path),
                          'data': base64.b64encode(io.open(path, 'rb').read()).decode()})
    release = {
        'name': 'portal', 'version': 1, 'manifest': manifest,
        'chart': {'metadata': {'name': 'portal', 'version': '0.0.0'}, 'templates': templates},
        'info': {'status': 'deployed'},
    }
    return len(base64.b64encode(gzip.compress(json.dumps(release).encode(), 9))), len(templates)


def main():
    size, files = modelled_size(CHART)
    pct = size / MAX_SECRET
    print(f'lint-release-size: modelled release record {size:,} B across {files} templates '
          f'= {pct * 100:.1f}% of the {MAX_SECRET:,} B Secret-data cap')

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
