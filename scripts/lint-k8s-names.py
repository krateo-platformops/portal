#!/usr/bin/env python3
"""Every object a chart renders, and every widget or RESTAction it refers to, has a valid Kubernetes name.

WHY THIS EXISTS. alerts-page 1.8.119 shipped a Select named `alerts-hasIncidents`. The apiserver
refuses it, because a name must be a lowercase RFC 1123 subdomain. On krateo-057 the page's
composition then hot-looped (observe -> helm -> fail, every ~3 s) and stayed on 1.8.118 while the
other 16 charts of its line moved to 1.8.119. Nothing in this repo could see it: `helm template`
does not validate names, the CRD schema tests validate `spec` and not `metadata`, and the
dangling-ref lint compares names with each other, so an invalid name that is referenced consistently
passes. Only the apiserver knew, at the roll.

THE RULE, per rendered document, in every chart and every single-flag state (the same sweep as
lint-dangling-refs.py, whose render and flag discovery this reuses, so the two cannot disagree on what
"every chart" means):
  - metadata.name is a lowercase RFC 1123 subdomain, at most 253 characters;
  - so is every resourcesRefs item's `name` and every apiRef's `name`: a ref to an invalid name can
    never resolve, and it is how a capitalised name would come back.
metadata.generateName is checked as a prefix (it may end in '-'). Names containing a Helm-rendered
value are checked as rendered, which is what the apiserver sees.

Usage: lint-k8s-names.py. Exit code = number of findings (capped at 125). Runs a control first: an
uppercase name, an underscore and a trailing dash must each be refused, and a valid name accepted.
"""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('dangling', os.path.join(HERE, 'lint-dangling-refs.py'))
dangling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dangling)

# The apiserver's own regex for a DNS-1123 subdomain (k8s.io/apimachinery validation.IsDNS1123Subdomain).
SUBDOMAIN = re.compile(r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$')
PREFIX = re.compile(r'^[a-z0-9]([-a-z0-9.]*[-a-z0-9.])?$')


def valid(name):
    return isinstance(name, str) and len(name) <= 253 and SUBDOMAIN.match(name) is not None


def valid_prefix(prefix):
    return isinstance(prefix, str) and len(prefix) <= 253 and PREFIX.match(prefix) is not None


def findings_in(doc, chart):
    out = []
    meta = doc.get('metadata') or {}
    owner = f'{doc.get("kind")}/{meta.get("name") or meta.get("generateName")}'
    if 'name' in meta and not valid(meta['name']):
        out.append(f'{chart}: {owner}: metadata.name {meta["name"]!r} is not a lowercase RFC 1123 subdomain')
    if 'generateName' in meta and not valid_prefix(meta['generateName']):
        out.append(f'{chart}: {owner}: metadata.generateName {meta["generateName"]!r} is not a valid name prefix')
    spec_ = doc.get('spec') or {}
    items = ((spec_.get('resourcesRefs') or {}).get('items') or []) if isinstance(spec_, dict) else []
    for it in items:
        if isinstance(it, dict) and 'name' in it and not valid(it['name']):
            out.append(f'{chart}: {owner}: resourcesRefs item {it.get("id")!r} names {it["name"]!r}, not a valid name')
    for ref in dangling.find_api_refs(spec_):
        if 'name' in ref and not valid(ref['name']):
            out.append(f'{chart}: {owner}: apiRef names {ref["name"]!r}, not a valid name')
    return out


def control():
    """The predicate must refuse what the apiserver refuses, and accept what it accepts."""
    bad = [{'kind': 'Select', 'metadata': {'name': 'alerts-hasIncidents'}},
           {'kind': 'Flex', 'metadata': {'name': 'snake_case'}},
           {'kind': 'Flex', 'metadata': {'name': 'trailing-'}},
           {'kind': 'Flex', 'metadata': {'name': 'ok'}, 'spec': {'resourcesRefs': {'items': [{'id': 'x', 'name': 'Bad'}]}}},
           {'kind': 'Table', 'metadata': {'name': 'ok'}, 'spec': {'apiRef': {'name': 'Bad'}}}]
    good = {'kind': 'Select', 'metadata': {'name': 'alerts-has-incidents'},
            'spec': {'resourcesRefs': {'items': [{'id': 'a', 'name': 'alerts-signal'}]}, 'apiRef': {'name': 'obs-alerts'}}}
    problems = [f'control: {d["metadata"]["name"]} / {d.get("spec")} was NOT refused' for d in bad if not findings_in(d, 'control')]
    if findings_in(good, 'control'):
        problems.append('control: a valid name was refused')
    return problems


def main():
    problems = control()
    if problems:
        for p in problems:
            print(f'[FAIL] {p}')
        return len(problems)
    print('[PASS] control: an uppercase name, an underscore, a trailing dash, a bad ref and a bad apiRef are refused; a valid name passes')
    if not dangling.CHARTS:
        print('lint-k8s-names: FAILED: discovered ZERO charts. A run that checked nothing is not a clean run.')
        return 1
    states = [('defaults', {})]
    for chart in dangling.CHARTS:
        for flag, on in dangling.gate_flags(chart):
            states.append((f'{os.path.basename(chart)}:{flag}={on}', {chart: [f'{flag}={on}']}))
    found, docs_seen = set(), 0
    for label, overrides in states:
        for chart in dangling.CHARTS:
            docs = dangling.render(chart, overrides.get(chart, ()))
            if docs is None:
                print(f'lint-k8s-names: FAILED: {os.path.basename(chart)} did not render in state {label}')
                return 1
            for d in docs:
                docs_seen += 1
                for f in findings_in(d, os.path.basename(chart)):
                    found.add(f)
    for f in sorted(found):
        print(f'[FAIL] {f}')
    print(f'lint-k8s-names: {len(dangling.CHARTS)} chart(s), {len(states)} flag state(s), '
          f'{docs_seen} rendered document(s), {len(found)} invalid name(s)')
    return min(len(found), 125)


if __name__ == '__main__':
    sys.exit(main())
