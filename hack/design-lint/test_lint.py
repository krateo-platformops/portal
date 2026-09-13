#!/usr/bin/env python3
"""Self-test: every rule must fire on fixtures/violations.yaml and stay silent on clean.yaml.

Both halves matter. A check that never fires is worse than no check — it reports "clean" for a
defect it cannot see — and a check that fires on correct authoring gets deleted, taking its signal
with it. The clean fixture encodes the specific cases that made earlier drafts noisy.
"""
import importlib.util
import os
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
    return proc.returncode, proc.stdout


def main():
    global RULES
    RULES = _rules()
    failures = []
    for rule in RULES:
        code, out = run('violations.yaml', rule)
        if code < 1:
            failures.append(f'{rule}: did not fire on a real violation')
        code, out = run('clean.yaml', rule)
        if code != 0:
            failures.append(f'{rule}: false positive on correct authoring\n{out}')

    for line in failures:
        print(f'FAIL {line}')
    print(f'{len(RULES) - len({f.split(":")[0] for f in failures})}/{len(RULES)} rules pass both halves')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
