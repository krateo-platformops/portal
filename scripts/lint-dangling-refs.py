#!/usr/bin/env python3
"""lint-dangling-refs — every widget resourcesRefs item must resolve to a widget that EXISTS,
in the namespace the ref names, across the whole fleet, with every feature flag both OFF and ON.

Why this is separate from lint-ref-namespace: that gate checks whether a ref names the RIGHT KIND
of namespace (a widget plural is a location, so it must be the page's namespace; anything else is
data and must be the shell). It cannot tell whether the object is actually THERE. A ref can satisfy
the rule and still dangle, and after #261 split every page into its own chart, four of them did --
live on krateo-057 for the whole 1.8.78-1.8.81 window:

  obs-stat-card-healthy      -> stat-healthy @ krateo-system      (it had moved to krateo-dashboard)
  obs-stat-card-failed       -> stat-failed  @ krateo-system       (likewise)
  compositions-page-header   -> new-composition @ krateo-compositions  (shell-owned, stayed put)
  dashboard-page-header      -> new-composition @ krateo-dashboard     (likewise)

WHY NO GATE SAW THEM. Two reasons, and both are the point of this script:

  1. NOBODY RENDERED THE FLEET TOGETHER. Each chart validated alone, so a ref crossing a chart
     boundary -- which did not exist before the split -- had no checker. `composition-rules` loads
     the union now, but it reports a ref whose TARGET is missing, not one whose NAMESPACE is wrong
     while a same-named object exists elsewhere.
  2. NOBODY RENDERED WITH THE FEATURE FLAGS ON. 15 observability widgets sat in the shell behind
     `observability.reconcileTracing`, which defaults false, while the page that refs them is gated
     on the same flag. With the flag off neither side renders and everything looks consistent; the
     breakage only exists in a state no gate ever built. A default-only gate is blind to every
     gated feature in the repo by construction.

So: render every chart, in every single-flag-on state, and resolve every widget ref against the
objects the whole fleet actually produced. Exit 1 on any that does not resolve.

Run: python3 scripts/lint-dangling-refs.py
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL_NS = 'krateo-system'
WIDGET_GROUP = 'widgets.templates.krateo.io'
# builder-publish is a BLUEPRINT chart CDC renders with a composition's own values, not a portal
# chart -- rendering it with portal values is meaningless. Same exclusion, same reason, as the
# other gates.
CHARTS = [d for d in sorted(glob.glob(os.path.join(REPO, 'helm', '*')))
          if os.path.isfile(os.path.join(d, 'Chart.yaml'))
          and os.path.basename(d) != 'builder-publish']


# A gate is anything a template tests for truthiness, and helm's truthiness covers more than
# `bool`. An EMPTY STRING is just as much an off-switch as `false`: `{{- if .Values.
# observabilityConsoleUrl }}` wraps a Button that exists only on installs configuring a console.
# Sweeping booleans alone missed exactly that -- obs-console-link sat in the shell while the
# observability page referenced it at its own namespace, a dangling ref no default render and no
# boolean sweep could reach. The page's own comment had already predicted the blind spot: "X13
# cannot catch this one: both sides are behind the same helm conditional."
SENTINEL = 'https://zz-sentinel.invalid'


def gate_flags(chart):
    """Every values leaf that acts as an on/off gate, as (path, on_value) pairs: booleans flipped
    to true, and empty strings set to a sentinel. A gate that only renders the defaults is blind to
    every gated feature in the repo by construction."""
    path = os.path.join(chart, 'values.yaml')
    if not os.path.isfile(path):
        return []
    values = yaml.safe_load(open(path, encoding='utf-8').read()) or {}
    out = []

    def walk(node, prefix=''):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f'{prefix}.{k}' if prefix else k)
        elif isinstance(node, bool):
            out.append((prefix, 'true'))
        elif isinstance(node, str) and node == '':
            out.append((prefix, SENTINEL))
    walk(values)
    return out


def render(chart, sets=()):
    """CHART_VERSION is a placeholder stamped at release, so a bare `helm template` of any chart
    here FAILS -- and a parser reading its stdout then reports a confident zero. Stamp a version
    into a copy, and assert the exit status rather than trusting the output."""
    with tempfile.TemporaryDirectory() as tmp:
        dst = os.path.join(tmp, os.path.basename(chart))
        subprocess.run(['cp', '-R', chart, dst], check=True)
        cf = os.path.join(dst, 'Chart.yaml')
        text = open(cf, encoding='utf-8').read()
        open(cf, 'w', encoding='utf-8').write(
            re.sub(r'(?m)^version: CHART_VERSION$', 'version: 0.0.0-lint', text))
        argv = ['helm', 'template', os.path.basename(chart), dst, '--namespace', SHELL_NS]
        for s in sets:
            argv += ['--set', s]
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode != 0:
            print(f'  HELM FAILED for {os.path.basename(chart)} {list(sets)}: '
                  f'{proc.stderr.strip().splitlines()[0] if proc.stderr.strip() else "?"}',
                  file=sys.stderr)
            return None
        return [d for d in yaml.safe_load_all(proc.stdout)
                if isinstance(d, dict) and d.get('kind')]


def collect(sets_for):
    """(defined, refs) across the fleet for one flag state. `sets_for(chart)` gives that chart's
    --set list, so a flag is applied only to the chart that declares it."""
    defined, refs = set(), []
    for chart in CHARTS:
        docs = render(chart, sets_for(chart))
        if docs is None:
            return None, None
        for d in docs:
            if WIDGET_GROUP not in str(d.get('apiVersion', '')):
                continue
            meta = d.get('metadata') or {}
            defined.add((meta.get('name'), meta.get('namespace')))
        for d in docs:
            items = ((d.get('spec') or {}).get('resourcesRefs') or {}).get('items') or []
            for it in items:
                if not isinstance(it, dict) or WIDGET_GROUP not in str(it.get('apiVersion', '')):
                    continue
                if it.get('name'):
                    refs.append((it['name'], it.get('namespace'),
                                 os.path.basename(chart),
                                 ((d.get('metadata') or {}).get('name'))))
    return defined, refs


def main():
    if not CHARTS:
        print('lint-dangling-refs: FAILED — discovered ZERO charts. A run that checked nothing '
              'is not a clean run.', file=sys.stderr)
        return 1

    # One state per gate, plus the defaults. Not the power set: a single-flag sweep catches
    # a feature whose widgets and refs disagree, which is the failure this exists for, and stays
    # linear instead of exponential in the number of gates.
    states = [('defaults', {})]
    for chart in CHARTS:
        for flag, on in gate_flags(chart):
            states.append((f'{os.path.basename(chart)}:{flag}={on}',
                           {chart: [f'{flag}={on}']}))

    total = 0
    for label, overrides in states:
        defined, refs = collect(lambda c: overrides.get(c, ()))
        if defined is None:
            print(f'lint-dangling-refs: FAILED — a chart did not render in state {label}',
                  file=sys.stderr)
            return 1
        bad = sorted({r for r in refs if (r[0], r[1]) not in defined})
        if bad:
            print(f'  state {label}: {len(bad)} dangling ref(s)')
            for name, ns, chart, owner in bad:
                where = sorted({dns for dn, dns in defined if dn == name})
                hint = f' — a {name} exists in {where}' if where else ' — defined nowhere'
                print(f'    {chart}: {owner} refs {name} @ {ns}{hint}')
            total += len(bad)

    print(f'lint-dangling-refs: {len(CHARTS)} chart(s), {len(states)} flag state(s), '
          f'{total} dangling ref(s)')
    return 1 if total else 0


if __name__ == '__main__':
    sys.exit(main())
