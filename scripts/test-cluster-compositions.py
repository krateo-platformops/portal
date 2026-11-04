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
    contract core-provider maintains (`Available`, `AvailablePartialCoverage`, `Creating`,
    `Unavailable`, `ZeroEvidence`, `ReconcileGracefullyPaused`) — never the message, which is prose
    they change freely. Note that `ReconcileGracefullyPaused` AND `AvailablePartialCoverage` both
    carry Status=TRUE, not False.
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


# Every row pill, label -> colour. The colour is checked as well as the label because they are
# derived from the same two fields by two separate if-chains, which is the same shape as a label
# disagreeing with a count: one of them can be wrong on its own. And a wrong colour NAME does not
# fail anywhere — the frontend's getColorCode returns palette.dark for a name it does not know, so
# a typo renders as near-black ink rather than raising. These five names are all in ui/src/theme
# tokens.ts, in both the light and dark tables.
LABEL_COLOR = {'Creating': 'blue', 'NotReady': 'red', 'Paused': 'slate', 'Ready': 'green', 'Unknown': 'gray'}

C0 = {'converging': 0, 'paused': 0}
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
    ([('False', 'Creating')],     {'total': 1, 'ready': 0, 'notReady': 0, 'converging': 1, 'paused': 0, 'unknown': 0}, 'blue'),
    # Converging must NOT fall through to green: the row never reported True.
    ([('True', 'Available'), ('False', 'Creating')],
                                  {'total': 2, 'ready': 1, 'notReady': 0, 'converging': 1, 'paused': 0, 'unknown': 0}, 'blue'),
    # A real failure still outranks progress.
    ([('False', 'Unavailable'), ('False', 'Creating')],
                                  {'total': 2, 'ready': 0, 'notReady': 1, 'converging': 1, 'paused': 0, 'unknown': 0}, 'orange'),
    # Progress outranks withheld evidence.
    ([('False', 'Creating'), ('Unknown', 'ZeroEvidence')],
                                  {'total': 2, 'ready': 0, 'notReady': 0, 'converging': 1, 'paused': 0, 'unknown': 1}, 'blue'),
    # An UNRECOGNISED False reason stays not-ready, deliberately: `notReady` is False-and-not-
    # Creating rather than False-and-Unavailable, so a reason added to the vocabulary later reads
    # as needing attention instead of vanishing into `unknown`.
    ([('False', 'SomeNewReason')], {'total': 1, 'ready': 0, 'notReady': 1, 'converging': 0, 'paused': 0, 'unknown': 0}, 'orange'),

    # core-provider#238: `ReconcileGracefullyPaused` is Status=TRUE. A paused composition is not a
    # failure and must never show red — but "Ready" claims it is being maintained, which is the one
    # thing it is not, so it is labelled and counted apart from Available.
    ([('True', 'ReconcileGracefullyPaused')],
                                  {'total': 1, 'ready': 0, 'notReady': 0, 'converging': 0, 'paused': 1, 'unknown': 0}, 'green'),
    ([('True', 'Available'), ('True', 'ReconcileGracefullyPaused')],
                                  {'total': 2, 'ready': 1, 'notReady': 0, 'converging': 0, 'paused': 1, 'unknown': 0}, 'green'),
    # A real failure still outranks a pause.
    ([('True', 'ReconcileGracefullyPaused'), ('False', 'Unavailable')],
                                  {'total': 2, 'ready': 0, 'notReady': 1, 'converging': 0, 'paused': 1, 'unknown': 0}, 'orange'),
    # Withheld evidence still turns the dot; a pause on its own does not.
    ([('True', 'ReconcileGracefullyPaused'), ('Unknown', 'ZeroEvidence')],
                                  {'total': 2, 'ready': 0, 'notReady': 0, 'converging': 0, 'paused': 1, 'unknown': 1}, 'gray'),
    # The mirror of the unrecognised-False case: an unrecognised TRUE reason stays READY. `paused`
    # is a named-reason predicate, NOT True-and-not-Available, so a reason core-provider adds on
    # the True side cannot silently become a pause.
    ([('True', 'SomeNewTrueReason')],
                                  {'total': 1, 'ready': 1, 'notReady': 0, 'converging': 0, 'paused': 0, 'unknown': 0}, 'green'),

    # core-provider#297: `AvailablePartialCoverage` is Status=TRUE — Available's condition with a
    # different reason, stamped when MOST CHILDREN COULD NOT BE HEALTH-EVALUATED.
    #
    # The case above already pins the property that keeps this right: an unrecognised True reason
    # counts as ready. So this adds no new behaviour, and that is the point of writing it by name
    # anyway — the generic case proves the predicate is a complement rather than an enumeration,
    # while a NAMED case records what core-provider actually ships, so the next reader of this
    # table sees the real vocabulary instead of inferring it. When a value is populous, "it would
    # have worked anyway" is not the same as "we know it works".
    #
    # Populous it is: on 057, 30 of 85 compositions had a majority of children unevaluated, and
    # portal-agents was 118 of 121.
    #
    # WHAT THIS DOES NOT ASSERT, so nobody reads it as reassurance: the row is green and counted
    # ready, which is CORRECT — the composition is available. It is not honest, because the pill is
    # identical to one backed by every child, and the number of children behind the claim is not
    # shown anywhere. Fixing that needs the coverage count on the status; it is not a counting bug
    # and no change to this table can address it. See core-provider#318 for the worse sibling: a
    # child the controller cannot READ is counted healthy and surfaced nowhere at all.
    ([('True', 'AvailablePartialCoverage')],
                                  {'total': 1, 'ready': 1, 'notReady': 0, 'converging': 0, 'paused': 0, 'unknown': 0}, 'green'),
    # It is READY, not PAUSED: both are Status=True, and only the named reason separates them.
    ([('True', 'AvailablePartialCoverage'), ('True', 'ReconcileGracefullyPaused')],
                                  {'total': 2, 'ready': 1, 'notReady': 0, 'converging': 0, 'paused': 1, 'unknown': 0}, 'green'),
    # And a real failure still outranks it, so partial coverage cannot mask a broken sibling.
    ([('True', 'AvailablePartialCoverage'), ('False', 'Unavailable')],
                                  {'total': 2, 'ready': 1, 'notReady': 1, 'converging': 0, 'paused': 0, 'unknown': 0}, 'orange'),

    # An unrecognised STATUS, as opposed to an unrecognised reason. `Unknown` and `""` are both
    # covered above, but a Ready condition is only ever True/False/Unknown by the Kubernetes
    # convention — so a third token here stands for the case where that convention is broken
    # upstream, or where a projection bug puts something else in the field. It must land in the
    # named `unknown` bucket and show the gray pill, not vanish: the partition invariant in main()
    # is what makes "vanish" a failure rather than a silently smaller total.
    ([('Degraded', 'SomeReason')],
                                  {'total': 1, 'ready': 0, 'notReady': 0, 'converging': 0, 'paused': 0, 'unknown': 1}, 'gray'),
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
        # Both True-side states, which is where an if-chain in the wrong order shows up: test
        # `.ready == "True"` before the paused arm and a paused row is LABELLED Ready while being
        # COUNTED paused — the mismatch these assertions exist to make impossible.
        if labels.count('Paused') != got.get('counts', {}).get('paused'):
            failures.append(f'{label}: Paused pills={labels.count("Paused")} but '
                            f'paused={got.get("counts", {}).get("paused")}')
        if labels.count('Ready') != got.get('counts', {}).get('ready'):
            failures.append(f'{label}: Ready pills={labels.count("Ready")} but '
                            f'ready={got.get("counts", {}).get("ready")}')
        # The FIFTH label, which was the only one not cross-checked — and `unknown` is exactly the
        # bucket core-provider's ZeroEvidence landed in wrongly once, when `notReady` was the
        # complement of True and absorbed it. Four of five checked is the shape where the unchecked
        # one is the one that breaks.
        if labels.count('Unknown') != got.get('counts', {}).get('unknown'):
            failures.append(f'{label}: Unknown pills={labels.count("Unknown")} but '
                            f'unknown={got.get("counts", {}).get("unknown")}')

        # THE PARTITION INVARIANT, asserted on every case rather than written as another case.
        #
        # The five counts must sum to `total`: each row belongs to exactly one bucket. This is what
        # catches a (status, reason) pair the vocabulary has not seen landing in NO bucket or in
        # TWO, which no single fixture row can catch — a new value that falls through every
        # predicate still satisfies each individual label-vs-count check, because the label chain
        # has an `else` and the row simply stops being counted anywhere.
        #
        # This is the general form of the regression this file was written for. core-provider's
        # ZeroEvidence was absorbed by a bucket defined by subtraction; the counts were rewritten as
        # five positive predicates so that could not recur, and THIS is the assertion that proves
        # the five still cover the space rather than merely being five.
        counts = got.get('counts', {})
        parts = ['ready', 'paused', 'converging', 'notReady', 'unknown']
        summed = sum(counts.get(k, 0) for k in parts)
        if summed != counts.get('total'):
            failures.append(f'{label}: the five states sum to {summed} but total={counts.get("total")} — '
                            f'a row is counted twice or not at all ({ {k: counts.get(k) for k in parts} })')
        for row in got.get('rows', []):
            want = LABEL_COLOR.get(row.get('readyLabel'))
            if want is None:
                failures.append(f'{label}: row label {row.get("readyLabel")!r} is not one of {sorted(LABEL_COLOR)}')
            elif row.get('readyColor') != want:
                failures.append(f'{label}: a {row.get("readyLabel")} pill is {row.get("readyColor")!r}, want {want!r}')
    for f in failures:
        print(f'FAIL  {f}')
    print(f'{len(CASES) - len({f.split(":")[0] for f in failures})}/{len(CASES)} cases ok')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
