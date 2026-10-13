#!/usr/bin/env python3
"""Self-test: every rule must fire on fixtures/violations.yaml and stay silent on clean.yaml.

Both halves matter. A check that never fires is worse than no check — it reports "clean" for a
defect it cannot see — and a check that fires on correct authoring gets deleted, taking its signal
with it. The clean fixture encodes the specific cases that made earlier drafts noisy.
"""
import importlib.util
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LINT = os.path.join(HERE, 'lint-portal-consistency.py')
def _rules():
    """Every rule the lint registers — DERIVED, never listed here.

    This was a hardcoded list of six, and the lint had grown to nine: `dead-kind`,
    `legacy-envelope` and `containment` had no self-test at all, so "6/6 rules pass" was true and
    meaningless. A test whose subject list is maintained by hand drifts from its subject exactly
    the way the widget `allowedResources` enums drifted from the registry.

    Deriving it means adding a rule without a fixture now FAILS, which is the point."""
    spec = importlib.util.spec_from_file_location('lintmod', LINT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return sorted(mod.RULES)


RULES = None  # populated in main() — the lint module is imported there


def run(target, rule):
    proc = subprocess.run(
        [sys.executable, LINT, os.path.join(HERE, 'fixtures', target), '--rule', rule, '--quiet'],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr



def readme_drift():
    """The README's rule table must list exactly the rules the registry registers.

    The table had drifted to seven of ten — missing `missing-target`, `containment` and
    `page-header`, two of which the design docs describe as "enforced". A reader checking that claim
    against the lint's own documentation found it absent, which is the worst possible answer: not a
    wrong rule, an apparently missing one. Documentation that can silently fall behind the code is
    the thing this whole design system exists to complain about.

    Checked in ONE direction only — registered-but-undocumented. The reverse would false-positive
    here, because this README documents the CSS lint's rules in a second table and they are not in
    this registry. An undocumented rule is the failure that actually happened; a documented rule
    that no longer exists is rarer and louder."""
    readme = os.path.join(HERE, 'README.md')
    if not os.path.isfile(readme):
        return []
    documented = set(re.findall(r'^\|\s*`([a-z-]+)`\s*\|', io.open(readme, encoding='utf-8').read(), re.M))
    return [f'{name}: registered in RULES but absent from README.md\'s rule table'
            for name in sorted(set(RULES) - documented)]



def enforced_count_drift():
    """Every number the design README states about enforcement must match the tools.

    This paragraph has now drifted three times: it claimed the lints were not wired into CI when
    they were, quoted a CSS baseline of 314 when the file held seven, then said "ten composition
    rules" after three were added and "seven" baseline violations after a rule landed that imported
    nineteen. Each time it was the section specifically about what is verified — the one place a
    reader goes to find out what they can trust.

    A number a human retypes is a number that goes stale, so this stops asking. Every figure below
    is derived from the thing it describes: the lint registries, the baseline file, and the rule
    headings in the six documents. If someone adds a rule and not a sentence, this fails and names
    both numbers."""
    readme = os.path.join(HERE, os.pardir, 'README.md')
    if not os.path.isfile(readme):
        return []
    text = io.open(readme, encoding='utf-8').read()
    out = []

    def claim(pattern, actual, label):
        found = re.search(pattern, text)
        if not found:
            out.append(f'{label}: the README no longer states this figure (pattern {pattern!r}) — '
                       f'it should say {actual}')
        elif int(found.group(1)) != actual:
            out.append(f'{label}: README says {found.group(1)}, tools say {actual}')

    # Composition rules: this file's own registry.
    composition = len(RULES)
    # Token rules: the sibling lint's REGISTRY, imported the same way _rules() imports this one's.
    # Counting `def rule_` would count an unregistered helper and miss a rule registered under an
    # alias — the hand-maintained-list failure in a new costume, which is what _rules() exists to
    # refuse.
    css_spec = importlib.util.spec_from_file_location('csslintmod', os.path.join(HERE, 'lint-css-tokens.py'))
    css_mod = importlib.util.module_from_spec(css_spec)
    css_spec.loader.exec_module(css_mod)
    token = len(css_mod.RULES)
    # Accessibility rules: the C-rule IDs the a11y gate actually cites — `// C8:` / `// C9:` beside
    # the jsx-a11y rules in ui/eslint.config.js, and the `C10 —` heading of the trigger test. Read
    # from the gates themselves, so a rule enforced without saying which one does not count.
    ui = os.path.join(HERE, os.pardir, os.pardir, 'ui')
    a11y_ids = set()
    for rel in ('eslint.config.js', os.path.join('src', 'test', 'a11yTriggers.test.ts')):
        path = os.path.join(ui, rel)
        if os.path.isfile(path):
            a11y_ids.update(re.findall(r'^\s*(?://|\*)\s*(C\d+)\s*(?::|—)', io.open(path, encoding='utf-8').read(), re.M))
    a11y = len(a11y_ids)
    # App-side layout rules: the P-IDs the in-app checks cite in their headings (`P26 — …`), less the
    # ones the composition lint already counts (P27 is checked on both sides; it is one rule).
    lint_spec = importlib.util.spec_from_file_location('lintmod_ids', LINT)
    lint_mod = importlib.util.module_from_spec(lint_spec)
    lint_spec.loader.exec_module(lint_mod)
    portal_ids = {part for _, rule_id in lint_mod.RULES.values() for part in rule_id.split('+')}
    app_ids = set()
    for rel in (os.path.join('src', 'test', 'screenHeaders.test.ts'), os.path.join('visual', 'layout.spec.ts')):
        path = os.path.join(ui, rel)
        if os.path.isfile(path):
            app_ids.update(re.findall(r'^\s*(?://|\*)\s*(P\d+)\s*(?::|—|,)', io.open(path, encoding='utf-8').read(), re.M))
    app = len(app_ids - portal_ids)
    held = composition + token + a11y + app
    # Total rules: the headings across the six design documents.
    total = 0
    for name in sorted(os.listdir(os.path.join(HERE, os.pardir))):
        if re.match(r'^0\d-.*\.md$', name):
            doc = io.open(os.path.join(HERE, os.pardir, name), encoding='utf-8').read()
            total += len(re.findall(r'^\s*#{2,4}\s*[A-Z]\d+\b', doc, re.M))

    claim(r'they hold \*\*(\d+) of the \d+ rules\*\*', held, 'machine-held total')
    claim(r'they hold \*\*\d+ of the (\d+) rules\*\*', total, 'total rule count')
    claim(r'(\d+) composition rules and \d+ token rules', composition, 'composition rule count')
    claim(r'\d+ composition rules and (\d+) token rules', token, 'token rule count')
    claim(r'across all (\d+) rules against the portal chart', composition, 'portal-chart rule count')
    claim(r'all (\d+) machine-held rules hold as of that commit', held, 'machine-held total (CI sentence)')
    claim(r'(\d+) accessibility rules', a11y, 'accessibility rule count')
    claim(r'(\d+) app-side layout rule', app, 'app-side layout rule count')
    claim(r'they cover the (\d+) rules no', total - held, 'human-held remainder')

    # The CSS baseline: total violations and the number of files they span.
    baseline_path = os.path.join(HERE, 'css-baseline.json')
    if os.path.isfile(baseline_path):
        baseline = json.load(io.open(baseline_path, encoding='utf-8'))
        files = {path for entries in baseline.values() for path in entries}
        violations = sum(count for entries in baseline.values() for count in entries.values())
        if violations == 0:
            # Paid off. "0 of those 0 are <rule>" would name an arbitrary rule; say what is true.
            if not re.search(r'baseline is \*\*empty\*\*', text):
                out.append('CSS baseline: it is empty, and the README should say "baseline is **empty**"')
        else:
            claim(r'\*\*baseline\*\* of (\d+)\s*\n?\s*pre-existing violations', violations, 'CSS baseline violations')
            claim(r'pre-existing violations across (\d+) files', len(files), 'CSS baseline file count')
            worst = max(baseline.items(), key=lambda kv: sum(kv[1].values()))
            claim(r'(\d+) of those \d+ are', sum(worst[1].values()), f'largest baseline rule ({worst[0]})')
    return out


def status_body_drift():
    """Rules whose Status line reads OPEN while their body claims the work is done.

    Three real instances in one day — X3 (status doubled to `gap -> fixed -> fixed`), X4 (body
    described the fix in full while the status still said `gap`), and T2 (body said "Now enforced"
    and named the lint; status still said "the rule is not holding"). All three were written by the
    person who wrote the rule about citations drifting, within hours of writing it.

    Nobody READING those rules would have caught it: the body is long and persuasive and only a
    one-line status contradicted it. That is precisely the class of error a human review misses and
    a diff does not, so it belongs here rather than in anyone's attention.

    A status carrying an arrow (`gap -> fixed`) has already been reconciled and is skipped.
    """
    import glob
    # Tightened after a false positive: the first draft matched the bare word "shipped", which fired
    # on A1's "the shipped verbs" — ordinary prose describing what EXISTS, not a claim of completion.
    # A lint that cries wolf gets switched off, so the markers here are deliberately strong: a bold
    # status word, a DATED resolution, or the explicit "now enforced" that T2 used.
    resolved = re.compile(
        r'\*\*(RESOLVED|FIXED|SHIPPED|DONE)\*\*'
        r'|\b(now enforced|is now enforced)\b'
        r'|\b(RESOLVED|FIXED|SHIPPED)\s+20\d\d'
        r'|\bhas shipped\b',
        re.I)
    openish = re.compile(r'^(gap|open|severe|missing|partial|unenforced|breached|defect|risk|inconsistent|ungoverned)', re.I)
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, '..', '0*.md'))):
        txt = open(path, encoding='utf-8').read()
        for m in re.finditer(r'^### ([TCPXAG]\d+) —.*?(?=^### |\Z)', txt, re.M | re.S):
            body, rule = m.group(0), m.group(1)
            st = re.search(r'^\*\*Status:\*\*(.*)$', body, re.M)
            if not st:
                continue
            status = re.sub(r'\*\*', '', st.group(1)).strip()
            if '\u2192' in status or '->' in status or not openish.match(status):
                continue
            if resolved.search(body):
                out.append(f'{rule}: status reads {status[:40]!r} but the body claims the work is done')
    return out


# Rules whose violation is a property of the CORPUS, not of any one document, so the
# one-fixture-fires / one-fixture-silent harness cannot express them. Each must instead have a
# dedicated check below — an exemption with no test is how a rule stops being tested at all.
CORPUS_RULES = {
    # P0 fires when page discovery finds NOTHING, so it is silent on any fixture that contains a
    # page and fires on any that does not — the exact inverse of every other rule. Covered by
    # check_page_discovery_guard().
    'page-discovery-alive',
}


def check_page_discovery_guard():
    """P0 must fire when the Menu stops being statically readable, and stop when annotations return.

    Both directions, because this rule exists to prevent a SILENT pass: if it only fired, a future
    change that made it fire always would be indistinguishable from working. The two fixtures are
    built inline rather than committed, so they cannot drift from the rule they describe.
    """
    out = []
    page = (
        'kind: Flex\n'
        'apiVersion: widgets.templates.krateo.io/v1beta1\n'
        'metadata:\n  name: page-thing\n{ann}'
        'spec:\n  widgetData:\n    allowedResources: []\n    items: []\n'
        '  resourcesRefs:\n    items: []\n'
    )
    menu_static = (
        '---\nkind: Menu\napiVersion: widgets.templates.krateo.io/v1beta1\n'
        'metadata:\n  name: sidebar-nav\n'
        'spec:\n  widgetData:\n    allowedResources: [flexes]\n'
        '    items:\n    - {label: Thing, path: /thing, page: thing}\n'
        '  resourcesRefs:\n    items: []\n'
    )
    menu_templated = (
        '---\nkind: Menu\napiVersion: widgets.templates.krateo.io/v1beta1\n'
        'metadata:\n  name: sidebar-nav\n'
        'spec:\n  widgetData:\n    allowedResources: [flexes]\n    items: []\n'
        '  widgetDataTemplate:\n  - forPath: items\n    expression: \'${ .pages }\'\n'
        '  resourcesRefs:\n    items: []\n'
    )
    annotation = '  annotations:\n    krateo.io/nav-label: "Thing"\n'

    cases = [
        ('static menu, no annotations', page.format(ann='') + menu_static, False),
        ('TEMPLATED menu, no annotations', page.format(ann='') + menu_templated, True),
        ('templated menu, ANNOTATED root', page.format(ann=annotation) + menu_templated, False),
    ]
    for label, body, should_fire in cases:
        # `run` resolves a target under fixtures/, so the temp file must live there.
        tmp = os.path.join(HERE, 'fixtures', '.p0-fixture.yaml')
        with open(tmp, 'w') as handle:
            handle.write(body)
        try:
            code, _stdout, err = run('.p0-fixture.yaml', 'page-discovery-alive')
        finally:
            os.unlink(tmp)
        if 'Traceback' in err:
            out.append(f'page-discovery-alive: CRASHED on {label}')
        elif bool(code >= 1) != should_fire:
            verb = 'did not fire' if should_fire else 'fired'
            out.append(f'page-discovery-alive: {verb} on "{label}"')
    return out


def main():
    global RULES
    RULES = _rules()
    failures = []
    failures += check_page_discovery_guard()
    for rule in RULES:
        if rule in CORPUS_RULES:
            continue
        code, out, err = run('violations.yaml', rule)
        # A CRASH IS NOT A PASS. This used to read `if code < 1`, so a rule that raised — exiting 1
        # with a traceback — was indistinguishable from one that reported a violation. X13 had been
        # crashing on this very fixture (a bare-list `resourcesRefs`, which is what X12 exists to
        # catch) and scoring green for as long as the self-test has existed.
        if 'Traceback' in err:
            failures.append(f'{rule}: CRASHED on the violations fixture\n{err.strip().splitlines()[-1]}')
        elif code < 1:
            failures.append(f'{rule}: did not fire on a real violation')
        elif not out.strip():
            failures.append(f'{rule}: exited non-zero but reported no violation line')

        code, out, err = run('clean.yaml', rule)
        if 'Traceback' in err:
            failures.append(f'{rule}: CRASHED on the clean fixture\n{err.strip().splitlines()[-1]}')
        elif code != 0:
            failures.append(f'{rule}: false positive on correct authoring\n{out}')

    # T8's key list is embedded for chart-repo runs; from THIS repo the real tokens.ts is present,
    # so assert they agree. A key added or renamed upstream without updating the lint would otherwise
    # make `colour-vocabulary` reject valid CRs — a false positive is how a rule gets switched off.
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location('lintmod', LINT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        real = mod.discover_palette()
        if real and real != mod.PALETTE_KEYS:
            missing = sorted(real - mod.PALETTE_KEYS)
            extra = sorted(mod.PALETTE_KEYS - real)
            failures.append(f'palette drift: tokens.ts has {missing} not in PALETTE_KEYS; '
                            f'PALETTE_KEYS has {extra} not in tokens.ts')
    except Exception as exc:
        failures.append(f'palette drift check could not run: {exc}')

    failures.extend(f'status/body drift: {d}' for d in status_body_drift())
    failures.extend(f'design README count drift: {d}' for d in enforced_count_drift())

    for line in failures:
        print(f'FAIL {line}')
    print(f'{len(RULES) - len({f.split(":")[0] for f in failures})}/{len(RULES)} rules pass both halves')

    drift = readme_drift()
    for line in drift:
        print(f'FAIL {line}')
    if not drift:
        print(f'README documents all {len(RULES)} registered rules')
    return 1 if (failures or drift) else 0


if __name__ == '__main__':
    sys.exit(main())
