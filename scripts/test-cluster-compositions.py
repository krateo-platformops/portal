#!/usr/bin/env python3
"""The clusters page's readiness summary, over the chart's OWN jq.

portal#333: `notReady` was `$total - $ready`, the complement of Ready=="True", so it absorbed
Ready=Unknown as well as Ready=="False". The row LABEL already had three states, so the summary
contradicted the rows it summarised — and core-provider 2.13.36 makes that reachable by reporting
Ready=Unknown/ZeroEvidence for a composition whose child-health rollup evaluated zero children
("not measured", not "unhealthy").

It renders helm/clusters-page and runs the RESTAction's final filter with jq, rather than keeping a
copy of the program here: a test holding its own copy of the jq passes while the chart drifts away
from it, which is the failure this file exists to prevent a repeat of.

jq is preinstalled on GitHub's ubuntu runners. JQ=<gojq built against krateo-platformops/gojq> runs
the same checks on the engine snowplow actually uses.
"""
import json
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHART = os.path.join(REPO, 'helm', 'clusters-page')
JQ = os.environ.get('JQ', 'jq')


def render(chart):
    """`helm template` the chart. Chart.yaml carries the CHART_VERSION placeholder the release
    substitutes, and helm refuses to render a non-semver version, so it is replaced in a copy."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', chart, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        text = text.replace('version: CHART_VERSION', 'version: 0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(
            ['helm', 'template', 'c', copy, '--set', 'namespace=krateo-clusters'],
            capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def restaction_filter(docs, name):
    for d in docs:
        if d.get('kind') == 'RESTAction' and d.get('metadata', {}).get('name') == name:
            return d['spec']['filter']
    raise AssertionError(f'RESTAction {name} not in the rendered chart')


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


def rows(*readys):
    """A spoke whose compositions carry these Ready condition values.

    `ready` is the condition's own `.status` string, as the upstream stage projects it
    (`[.status.conditions[] | select(.type=="Ready") | .status] | first // ""`), so a composition
    with no Ready condition at all arrives as "" — which must count as unknown, not as not-ready.
    Each item is either a Ready status string, or a (status, reason) pair. The REASON is the
    contract core-provider maintains (`Available`, `Creating`, `Unavailable`, `ZeroEvidence`) —
    never the message, which is prose they change freely.
    """
    def one(r):
        ready, reason = r if isinstance(r, tuple) else (r, '')
        return {'ready': ready, 'reason': reason, 'synced': 'True'}

    return {
        'name': 'spoke-a',
        'spokeCrds': {'items': []},
        'spokeCompositions': [one(r) for r in readys],
        'spokeCompositionDefinitions': [],
    }


C0 = {'converging': 0}
CASES = [
    # (ready values, expected counts, expected statusColor)
    (['True', 'True'],            {'total': 2, 'ready': 2, 'notReady': 0, 'unknown': 0, **C0}, 'green'),
    (['True', 'False'],           {'total': 2, 'ready': 1, 'notReady': 1, 'unknown': 0, **C0}, 'orange'),
    # THE REGRESSION: Unknown is not not-ready. Two healthy-but-unmeasured compositions on a
    # cluster where nothing is wrong must not be counted as problems.
    (['True', 'Unknown'],         {'total': 2, 'ready': 1, 'notReady': 0, 'unknown': 1, **C0}, 'gray'),
    # A composition with no Ready condition at all — same treatment, by the same predicate.
    (['True', ''],                {'total': 2, 'ready': 1, 'notReady': 0, 'unknown': 1, **C0}, 'gray'),
    # A real failure still outranks withheld evidence.
    (['False', 'Unknown'],        {'total': 2, 'ready': 0, 'notReady': 1, 'unknown': 1, **C0}, 'orange'),
    ([],                          {'total': 0, 'ready': 0, 'notReady': 0, 'unknown': 0, **C0}, 'gray'),

    # core-provider#234 / 2.13.40: Ready=False with reason `Creating` is CONVERGING, not broken.
    # It was landing in notReady — the same shape as the portal#333 regression above, one state
    # later: a bucket absorbing a state that is not a failure.
    ([('False', 'Creating')],     {'total': 1, 'ready': 0, 'notReady': 0, 'converging': 1, 'unknown': 0}, 'blue'),
    # Converging must NOT fall through to green: the row never reported True.
    ([('True', 'Available'), ('False', 'Creating')],
                                  {'total': 2, 'ready': 1, 'notReady': 0, 'converging': 1, 'unknown': 0}, 'blue'),
    # A real failure still outranks progress.
    ([('False', 'Unavailable'), ('False', 'Creating')],
                                  {'total': 2, 'ready': 0, 'notReady': 1, 'converging': 1, 'unknown': 0}, 'orange'),
    # Progress outranks withheld evidence.
    ([('False', 'Creating'), ('Unknown', 'ZeroEvidence')],
                                  {'total': 2, 'ready': 0, 'notReady': 0, 'converging': 1, 'unknown': 1}, 'blue'),
    # An UNRECOGNISED False reason stays not-ready, deliberately: `notReady` is False-and-not-
    # Creating rather than False-and-Unavailable, so a reason added to the vocabulary later reads
    # as needing attention instead of vanishing into `unknown`.
    ([('False', 'SomeNewReason')], {'total': 1, 'ready': 0, 'notReady': 1, 'converging': 0, 'unknown': 0}, 'orange'),
]


def main():
    program = restaction_filter(render(CHART), 'cluster-compositions')
    failures = []
    for readys, want_counts, want_color in CASES:
        got = jq(program, rows(*readys))
        label = f'ready={readys!r}'
        if got.get('counts') != want_counts:
            failures.append(f'{label}: counts {got.get("counts")} != {want_counts}')
        if got.get('statusColor') != want_color:
            failures.append(f'{label}: statusColor {got.get("statusColor")!r} != {want_color!r}')
        # The summary must never contradict the labels it summarises.
        labels = [r.get('readyLabel') for r in got.get('rows', [])]
        if labels.count('Creating') != got.get('counts', {}).get('converging'):
            failures.append(f'{label}: Creating pills={labels.count("Creating")} but '
                            f'converging={got.get("counts", {}).get("converging")}')
        if labels.count('NotReady') != got.get('counts', {}).get('notReady'):
            failures.append(f'{label}: {labels.count("NotReady")} NotReady rows but '
                            f'notReady={got.get("counts", {}).get("notReady")}')
    for f in failures:
        print(f'FAIL  {f}')
    print(f'{len(CASES) - len({f.split(":")[0] for f in failures})}/{len(CASES)} cases ok')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
