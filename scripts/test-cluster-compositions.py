#!/usr/bin/env python3
"""The clusters page's readiness summary — and its "Ask Agentiko →" links — over the chart's OWN jq.

portal#333: `notReady` was `$total - $ready`, the complement of Ready=="True", so it absorbed
Ready=Unknown as well as Ready=="False". The row LABEL already had three states, so the summary
contradicted the rows it summarised — and core-provider 2.13.36 makes that reachable by reporting
Ready=Unknown for a composition whose child-health rollup could not assess it ("not measured", not
"unhealthy"). Unknown now carries three reasons and NONE of them means "the composition has no
children" — one that manages nothing reports `Available`. See the chart's own comment for the split
(ZeroEvidence / ChildrenForbidden / InventoryNotRecorded).

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
import urllib.parse

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
    `Unavailable`, `ZeroEvidence`, `ChildrenForbidden`, `InventoryNotRecorded`,
    `ReconcileGracefullyPaused`) — never the message, which is prose
    they change freely. Note that `ReconcileGracefullyPaused` AND `AvailablePartialCoverage` both
    carry Status=TRUE, not False.
    """
    def one(r):
        # A bare status, a (status, reason) pair, or a (status, reason, synced) triple. Synced
        # defaults to True so every existing case reads as "the Ready condition alone decides the
        # Ready bucket" — which is the property most of them are about. The triple exists for the
        # states that only OCCUR alongside a Synced failure, where a fixture pinning the Ready half
        # in isolation would be asserting a state that never happens.
        ready, reason, synced = (tuple(r) + ('True',))[:3] if isinstance(r, tuple) else (r, '', 'True')
        return {'ready': ready, 'reason': reason, 'synced': synced}

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

    # core-provider#324 / #328: two more Unknown reasons, both core-provider's OWN defects rather
    # than a permanent fact about the children. They must be counted as `unknown` like ZeroEvidence
    # — not as failures, because nothing is claimed broken, and not as ready.
    #
    # The point of asserting them BY NAME when the bucket is chosen by STATUS: it records that the
    # vocabulary grew and that the growth was absorbed, which is the property the `SomeNewTrueReason`
    # case proves generically. A named case is how the next reader learns the real set.
    ([('Unknown', 'ChildrenForbidden')],
                                  {'total': 1, 'ready': 0, 'notReady': 0, 'converging': 0, 'paused': 0, 'unknown': 1}, 'gray'),
    ([('Unknown', 'InventoryNotRecorded')],
                                  {'total': 1, 'ready': 0, 'notReady': 0, 'converging': 0, 'paused': 0, 'unknown': 1}, 'gray'),
    # BOTH Synced values occur with InventoryNotRecorded, and the case ABOVE — synced=True — is the
    # COMMON one. That is the opposite of what I first wrote here, and the correction is worth
    # keeping because the wrong version is the intuitive one: "reports drift" sounds like
    # "Synced=False".
    #
    # It is not. unstructured-runtime's drift branch (pkg/controller/worker.go:773) writes the
    # handler's conditions — Ready — and NO Synced condition at all, then queues an Update. So
    # Synced keeps whatever it already held, which for a composition that has been reconciling
    # successfully is Synced=True/ReconcileSuccess. The sequence:
    #
    #   Observe finds the inventory absent -> Ready=Unknown/InventoryNotRecorded persisted
    #                                      -> Synced UNTOUCHED, still True
    #                                      -> an Update is queued, writes status.managed
    #                                      -> the next Observe reports something else entirely
    #
    # Synced=False alongside it is the SECOND-ORDER case: it occurs only when that queued Update
    # then fails for its own reasons. This row covers that half; the row above covers the half a
    # dashboard will actually catch.
    #
    # And the reason is TRANSIENT BY DESIGN either way, unlike ZeroEvidence which is a steady state
    # nobody can clear. A composition STUCK on InventoryNotRecorded is a second defect rather than
    # the expected case — worth knowing before someone reads a persistent gray pill as normal.
    ([('Unknown', 'InventoryNotRecorded', 'False')],
                                  {'total': 1, 'ready': 0, 'notReady': 0, 'converging': 0, 'paused': 0, 'unknown': 1}, 'gray'),
    # A real failure still outranks withheld evidence, whichever reason withheld it.
    ([('Unknown', 'ChildrenForbidden'), ('False', 'Unavailable')],
                                  {'total': 2, 'ready': 0, 'notReady': 1, 'converging': 0, 'paused': 0, 'unknown': 1}, 'orange'),

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


def ask(href):
    """(the path the link opens, the decoded ?ask prompt)."""
    path, _, query = href.partition('?')
    return path, urllib.parse.parse_qs(query).get('ask', [''])[0]


def target(name, conn='', version='', probe=''):
    return {'metadata': {'name': name}, 'spec': {'kubeconfigRef': {'name': name + '-kc'}},
            'status': {'connectionStatus': conn, 'version': version, 'lastProbeTime': probe}}


def compdef(name, tgt, conn='', ns='krateo-system'):
    return {'metadata': {'name': name, 'namespace': ns},
            'spec': {'deploy': {'targetRef': {'name': tgt}}, 'chart': {'version': '1.4.2'}},
            'status': {'target': {'connectionStatus': conn, 'version': ''}}}


def ask_checks(docs, failures):
    """"Ask Agentiko →" on /clusters and /clusters/{name}: the prompt names what the table shows,
    and a failed read asks about the error rather than describing a registry nobody read."""
    def check(label, got, want):
        if got != want:
            failures.append(f'ask: {label}: {got!r} != {want!r}')

    for ra in ('clusters-list', 'cluster-detail'):
        doc = [d for d in docs if d.get('kind') == 'RESTAction' and d['metadata']['name'] == ra][0]
        steps = {s['name']: s['filter'] for s in doc['spec']['api']}
        final = doc['spec']['filter']

        def run(targets=None, compdefs=None, extra=None):
            data = dict(extra or {})
            if targets is not None:
                data['targets'] = jq(steps['targets'], {'targets': {'items': targets}})
            if compdefs is not None:
                data['compdefs'] = jq(steps['compdefs'], {'compdefs': {'items': compdefs}})
            return jq(final, data)

        fleet = [target('hub', 'Healthy', 'v1.31.2', '2026-11-11T09:00:00Z'),
                 target('spoke-eu-west', 'Down', 'v1.30.4', '2026-11-11T08:56:00Z'),
                 target('spoke-new')]
        cds = [compdef('web-app', 'spoke-eu-west', 'Down'), compdef('db', 'spoke-eu-west', 'Down'),
               compdef('cache', 'spoke-eu-west', 'Healthy')]
        if ra == 'clusters-list':
            out = run(fleet, cds)
            path, prompt = ask(out['askHref'])
            check('list -> self page', path, '/clusters')
            check('list -> not failed', out['failed'], False)
            check('list -> prompt', prompt,
                  'Check my registered clusters: 3 registered — 1 healthy, 1 down (1. spoke-eu-west — last probe '
                  '2026-11-11T08:56:00Z), 1 unknown (1. spoke-new). For each one that is not healthy, explain what '
                  'its connection probe says and how to restore it.')
            _, prompt = ask(run([target('hub', 'Healthy')], [])['askHref'])
            check('list, all healthy -> no "not healthy" list', prompt,
                  'Check my registered clusters: 1 registered, all healthy. Tell me whether anything about them still needs attention.')
            _, prompt = ask(run([], [])['askHref'])
            check('list, none registered -> says so', prompt.startswith('No cluster is registered on this platform yet.'), True)
            # A FAILED READ IS NOT AN EMPTY REGISTRY.
            failed = run(None, cds, {'targetsError': [{'code': 403, 'message': 'kubernetestargets is forbidden'}]})
            check('list, failed read -> failed', (failed['failed'], failed['readError']), (True, 'kubernetestargets is forbidden'))
            _, prompt = ask(failed['askHref'])
            check('list, failed read -> asks about the error', prompt,
                  'The cluster registry could not be read (kubernetestargets is forbidden). Explain what that error '
                  'means and what an administrator has to fix.')
            # The roll-up half failing is stated, not hidden: health then comes from the probe alone.
            _, prompt = ask(run(fleet, None, {'compdefsError': 'timeout'})['askHref'])
            check('list, compdefs failed -> stated', prompt.endswith(
                'The blueprints that target them could not be read (timeout), so each cluster\'s health here comes '
                'from its own probe alone.'), True)
            many = [target(f'c{i}', 'Down', probe='2026-11-11T00:00:00Z') for i in range(10)]
            _, prompt = ask(run(many, [])['askHref'])
            check('list -> eight at most', ('8. c7' in prompt, '9. c8' in prompt), (True, False))
        else:
            out = run(fleet, cds, {'name': 'spoke-eu-west'})
            path, prompt = ask(out['askHref'])
            check('detail -> self page', path, '/clusters/spoke-eu-west')
            check('detail -> prompt', prompt,
                  'Diagnose cluster "spoke-eu-west": its connection is Down (last probe 2026-11-11T08:56:00Z), '
                  'Kubernetes v1.30.4, and 3 blueprints target it (2 report it Down). Is it reachable and healthy? '
                  'If not, explain why and how to restore it.')
            _, prompt = ask(run(fleet, [], {'name': 'spoke-new'})['askHref'])
            check('detail, no probe, no version -> said', prompt,
                  'Diagnose cluster "spoke-new": its connection is Unknown (no probe recorded), its Kubernetes version '
                  'is unknown, and 0 blueprints target it (0 report it Down). Is it reachable and healthy? If not, '
                  'explain why and how to restore it.')
            check('detail, not found -> no link', run(fleet, cds, {'name': 'nope'})['askHref'], '')
            check('detail, failed read -> no link', run(None, cds, {'name': 'hub', 'targetsError': 'x'})['askHref'], '')

    for name, ra, fb, ke in (('clusters-ask-agentiko', 'clusters-list', '/clusters', None),
                             ('cluster-detail-ask-agentiko', 'cluster-detail', '/clusters', ['name'])):
        b = [d for d in docs if d.get('kind') == 'Button' and d['metadata']['name'] == name][0]
        expr = [e for e in b['spec']['widgetDataTemplate'] if e['forPath'] == 'actions'][0]['expression'].strip()[2:-1]
        check(f'{name} -> askHref', jq(expr, {'askHref': '/x?ask=y'})['navigate'][0]['path'], '/x?ask=y')
        check(f'{name} -> fallback', jq(expr, {'askHref': ''})['navigate'][0]['path'], fb)
        check(f'{name} -> reads', b['spec']['apiRef']['name'], ra)
        check(f'{name} -> keyExtras', b['spec'].get('keyExtras'), ke)
        check(f'{name} -> label, intent', (b['spec']['widgetData']['label'], b['spec']['widgetData']['intent']),
              ('Ask Agentiko →', 'agentiko'))
    header = [d for d in docs if d.get('kind') == 'PageHeader' and d['metadata']['name'] == 'clusters-page-header'][0]
    check('header -> Ask, then Register', [i['resourceRefId'] for i in header['spec']['widgetData']['items']],
          ['clusters-ask-agentiko', 'register-cluster'])


def main():
    docs = render(CHART)
    program = restaction_filter(docs, 'cluster-compositions')
    failures = []
    ask_checks(docs, failures)
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
    asks = [f for f in failures if f.startswith('ask: ')]
    print(f'{len(CASES) - len({f.split(":")[0] for f in failures if f not in asks})}/{len(CASES)} cases ok; '
          f'Ask Agentiko: {len(asks)} failure(s)')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
