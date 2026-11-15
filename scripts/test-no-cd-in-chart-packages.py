#!/usr/bin/env python3
"""No portal chart package ships a CompositionDefinition, unless installer has been told first.

Since krateo-platformops/.github#45, the shared release-oci workflow ships a chart's stamped
`compositiondefinition.yaml` INSIDE its package when one sits in the chart's own directory or,
failing that, in the chart directory's parent (ship-cd.sh `source`). Portal's CompositionDefinitions
are registered by the INSTALLER: its emission path assumes so. A file appearing in one of those two
places would silently change who registers the definition, at the next portal release.

So this enumerates the charts exactly as release-oci does (same pruned directories, vendored
subcharts skipped) and fails if any of them would pick a CompositionDefinition up. If shipping one
is ever intended, tell installer first, then change this test in the same PR.

Usage: test-no-cd-in-chart-packages.py [root]. Exit code = number of findings. Runs its controls
first: a CompositionDefinition in a chart directory, and one in a chart's parent, must be caught;
one at a directory release-oci prunes (examples/) must not.
"""
import os
import sys
import tempfile

# release-oci.yaml's `find ... -prune` list, verbatim (keep in step with it).
PRUNED = {'.git', 'node_modules', 'examples', 'example', '__fixtures__', 'fixtures', 'testdata', 'test', 'tests', 'docs'}
CD = 'compositiondefinition.yaml'


def charts(root):
    """First-class chart directories, as release-oci discovers them."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in PRUNED)
        if 'Chart.yaml' not in filenames:
            continue
        parent = os.path.dirname(dirpath)
        if os.path.basename(parent) == 'charts' and os.path.isfile(os.path.join(os.path.dirname(parent), 'Chart.yaml')):
            continue          # a vendored subchart
        found.append(dirpath)
    return sorted(found)


def findings(root):
    """Each chart that release-oci would now ship a CompositionDefinition into."""
    out = []
    for d in charts(root):
        for candidate in (os.path.join(d, CD), os.path.join(os.path.dirname(d), CD)):
            if os.path.isfile(candidate):
                out.append((os.path.relpath(d, root), os.path.relpath(candidate, root)))
                break         # ship-cd.sh takes the chart directory's own file first
    return out


def controls():
    problems = []
    with tempfile.TemporaryDirectory() as tmp:
        def chart(path):
            os.makedirs(os.path.join(tmp, path), exist_ok=True)
            open(os.path.join(tmp, path, 'Chart.yaml'), 'w').write('name: x\nversion: 0.0.0\n')
        def cd(path):
            os.makedirs(os.path.join(tmp, path), exist_ok=True)
            open(os.path.join(tmp, path, CD), 'w').write('kind: CompositionDefinition\n')
        chart('helm/a')
        chart('helm/b')
        chart('examples/c')
        cd('examples/c')                       # pruned by release-oci: not shipped
        if findings(tmp):
            problems.append('control: a CompositionDefinition under examples/ was flagged, but release-oci prunes it')
        cd('helm/a')                           # beside a chart's Chart.yaml
        if ('helm/a', 'helm/a/' + CD) not in findings(tmp):
            problems.append('control: a CompositionDefinition in a chart directory was NOT caught')
        os.remove(os.path.join(tmp, 'helm/a', CD))
        cd('helm')                             # in a chart directory's parent
        got = findings(tmp)
        if len(got) != 2 or any(c != 'helm/' + CD for _, c in got):
            problems.append(f'control: a CompositionDefinition in the charts\' parent was not caught for both charts: {got}')
    return problems


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    problems = controls()
    if problems:
        for p in problems:
            print(f'[FAIL] {p}')
        return len(problems)
    print('[PASS] controls: a CompositionDefinition in a chart directory or its parent is caught; one under a pruned directory is not')
    found = findings(root)
    for chart_dir, cd_path in found:
        print(f'[FAIL] {cd_path}: release-oci would ship it inside {chart_dir}\'s package (.github#45). '
              f'Portal\'s CompositionDefinitions are registered by the installer, whose emission path assumes so: '
              f'tell installer first, then change this test in the same PR.')
    if not found:
        print(f'[PASS] {len(charts(root))} chart(s), as release-oci enumerates them: none would ship a CompositionDefinition')
    return min(len(found), 125)


if __name__ == '__main__':
    sys.exit(main())
