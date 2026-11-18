#!/usr/bin/env python3
"""No chart renders an unquoted YAML 1.1 boolean word: y, n, yes, no, on, off (any case).

WHY THIS EXISTS. observability-page 1.8.131 shipped a Table column `valueKey: n`. helm and the
apiserver read YAML with a YAML 1.1 resolver (go-yaml v2, via sigs.k8s.io/yaml), where a bare `n` is
the boolean false. The Table CRD wants a string, so on krateo-057 the 1.8.135 roll failed with
`spec.widgetData.columns[3].valueKey: Invalid value: "boolean"` and the page's release went FAILED.
Nothing in this repo could see it: `helm template` does not validate widget CRDs, and every schema
test here parses the render with PyYAML, which reads `n` and `y` as strings. The two parsers
disagree on exactly these words, so the test suite and the cluster saw different documents.

THE RULE, per rendered chart, in every chart and every single-flag state (the same sweep as
lint-dangling-refs.py, whose render and flag discovery this reuses): no PLAIN (unquoted) scalar, key
or value, is one of y Y yes Yes YES n N no No NO on On ON off Off OFF. Quote the word when it is a
string ("n"), write true/false when it is a boolean, or pick another word. A plain `true`/`false`
is fine: both parsers agree on those.

Usage: lint-yaml11-booleans.py. Exit code = number of findings (capped at 125). Runs a control first:
each of the sixteen words, as a key and as a value, must be found; quoted forms and true/false must
not be.
"""
import importlib.util
import os
import re
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('dangling', os.path.join(HERE, 'lint-dangling-refs.py'))
dangling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dangling)

# go-yaml v2's YAML 1.1 boolean set, minus true/false (on which every parser agrees).
WORDS = ['y', 'Y', 'yes', 'Yes', 'YES', 'n', 'N', 'no', 'No', 'NO', 'on', 'On', 'ON', 'off', 'Off', 'OFF']
AMBIGUOUS = re.compile('^(' + '|'.join(WORDS) + ')$')


def source_of(lines, n):
    """The template a rendered line came from: the nearest `# Source:` comment above it (helm writes
    one per document), so a finding names the file to fix and stays the same in every flag state."""
    for i in range(n, -1, -1):
        if lines[i].startswith('# Source: '):
            return lines[i][len('# Source: '):].strip()
    return '?'


def findings_in(text, label):
    lines = text.splitlines()
    out = []
    for tok in yaml.scan(text):
        if isinstance(tok, yaml.ScalarToken) and tok.plain and AMBIGUOUS.match(tok.value):
            n = tok.start_mark.line
            out.append(f'{label}: {source_of(lines, n)}: unquoted {tok.value!r} is a boolean to helm and the '
                       f'apiserver: {lines[n].strip()[:120]}')
    return out


def control():
    problems = []
    for w in WORDS:
        for doc in (f'a: {w}\n', f'{w}: 1\n', f'- {w}\n'):
            if not findings_in(doc, 'control'):
                problems.append(f'control: unquoted {w!r} in {doc.strip()!r} was NOT found')
    clean = 'a: "n"\nb: \'y\'\n"on": 1\nc: true\nd: false\ne: samples\nf: none\n'
    if findings_in(clean, 'control'):
        problems.append(f'control: a quoted word, true/false or an ordinary word was flagged: {findings_in(clean, "control")}')
    return problems


def main():
    problems = control()
    if problems:
        for p in problems:
            print(f'[FAIL] {p}')
        return len(problems)
    print(f'[PASS] control: all {len(WORDS)} words are found as a key, a value and a list item; quoted forms, true/false and ordinary words pass')
    if not dangling.CHARTS:
        print('lint-yaml11-booleans: FAILED: discovered ZERO charts. A run that checked nothing is not a clean run.')
        return 1
    states = [('defaults', {})]
    for chart in dangling.CHARTS:
        for flag, on in dangling.gate_flags(chart):
            states.append((f'{os.path.basename(chart)}:{flag}={on}', {chart: [f'{flag}={on}']}))
    found, renders = set(), 0
    for label, overrides in states:
        for chart in dangling.CHARTS:
            text = dangling.render_text(chart, overrides.get(chart, ()))
            if text is None:
                print(f'lint-yaml11-booleans: FAILED: {os.path.basename(chart)} did not render in state {label}')
                return 1
            renders += 1
            for f in findings_in(text, os.path.basename(chart)):
                found.add(f)
    for f in sorted(found):
        print(f'[FAIL] {f}')
    print(f'lint-yaml11-booleans: {len(dangling.CHARTS)} chart(s), {len(states)} flag state(s), '
          f'{renders} render(s), {len(found)} unquoted YAML 1.1 boolean word(s)')
    return min(len(found), 125)


if __name__ == '__main__':
    sys.exit(main())
