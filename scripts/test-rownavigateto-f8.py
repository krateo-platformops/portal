#!/usr/bin/env python3
"""
test-rownavigateto-f8 — CTA audit #89 F8 route-derivation fixtures.

WHAT F8 IS:
  Pod/resource rows deep-link to their logs. table.component-detail-pods and
  table.obs-resource-table rows carry `rowNavigateTo: /observability?svc={<pod
  valueKey>}`, so clicking a pod row opens the observability log stream scoped
  (server-side, via obs-log-stream's ?svc bidirectional case-insensitive
  CONTAINS) to that pod's owning OTel ServiceName. Verified live on
  krateo-installer-release: a raw pod name resolves to exactly its service (the
  service name is a substring of the pod name) and, unlike the bare deployment
  name, does not over-match sibling *-controller/*-agent services.

WHAT THIS SCRIPT DOES:
  Models the frontend Table's buildRowPath (src/widgets/Table/Table.tsx): fill
  each {valueKey} in rowNavigateTo from the clicked row's matching stringValue;
  if ANY placeholder is undefined/empty the whole path is dropped and the row is
  INERT (no broken link — the guard pattern). Asserts, for BOTH shipped tables:
    - present pod name  -> a concrete /observability?svc=<pod> route
    - empty pod cell    -> inert
    - absent pod cell   -> inert
  Pure, no cluster/helm needed; exit non-zero on any mismatch.
"""
import re
import sys
from urllib.parse import quote


def build_row_path(row_navigate_to, row):
    """Port of Table.tsx buildRowPath. `row` is a list of {valueKey, stringValue}
    cells. Returns the concrete path, or None when a placeholder is missing/empty
    (row inert)."""
    if not row_navigate_to:
        return None
    by_key = {c["valueKey"]: c.get("stringValue") for c in row}
    missing = False
    # WHEN THE WHOLE TEMPLATE IS ONE `{key}`, the cell already holds a complete pre-built route
    # (the server precomputed a per-row branch — composition rows -> /compositions/.., everything
    # else -> /resources/..), so it is used VERBATIM rather than percent-encoded. This port quoted
    # unconditionally and so modelled such a row as `%2Fcompositions%2Fdemo%2Fapp`, a route that
    # goes nowhere. The chart's own prose already recorded the upstream change
    # (table.builder-prs.yaml cites "krateo-frontend@aee40c3, Table whole-placeholder
    # rowNavigateTo, UX #21"); the model never followed, and nothing noticed because no workflow
    # ran this file.
    whole_is_placeholder = re.fullmatch(r"\{[^}]+\}", row_navigate_to) is not None

    def sub(m):
        nonlocal missing
        val = by_key.get(m.group(1))
        if val is None or val == "":
            missing = True
            return ""
        return val if whole_is_placeholder else quote(val, safe="")

    path = re.sub(r"\{([^}]+)\}", sub, row_navigate_to)
    return None if missing else path


CASES = [
    # (label, rowNavigateTo, row, expected)
    (
        "component-detail-pods: present pod name -> route",
        "/observability?svc={name}",
        [{"valueKey": "name", "stringValue": "snowplow-6dd6dbd5fc-mgmqh"},
         {"valueKey": "ready", "stringValue": "1/1"}],
        "/observability?svc=snowplow-6dd6dbd5fc-mgmqh",
    ),
    (
        "component-detail-pods: empty name -> inert",
        "/observability?svc={name}",
        [{"valueKey": "name", "stringValue": ""},
         {"valueKey": "ready", "stringValue": "1/1"}],
        None,
    ),
    (
        "component-detail-pods: absent name cell -> inert",
        "/observability?svc={name}",
        [{"valueKey": "ready", "stringValue": "1/1"}],
        None,
    ),
    (
        "obs-resource-table: present pod name -> route",
        "/observability?svc={pod}",
        [{"valueKey": "pod", "stringValue": "frontend-krateo-frontend-76f99b47bc-dp9n5"},
         {"valueKey": "ns", "stringValue": "krateo-system"}],
        "/observability?svc=frontend-krateo-frontend-76f99b47bc-dp9n5",
    ),
    (
        "obs-resource-table: empty pod -> inert",
        "/observability?svc={pod}",
        [{"valueKey": "pod", "stringValue": ""},
         {"valueKey": "ns", "stringValue": "krateo-system"}],
        None,
    ),
    # The whole-placeholder shape this port used to get wrong. table.builder-prs carries
    # `rowNavigateTo: "{href}"`, whose cell is already a complete route — its slashes must
    # survive. Quoting them yields %2F and a dead row.
    (
        "builder-prs: whole placeholder used VERBATIM, slashes intact",
        "{href}",
        [{"valueKey": "href", "stringValue": "/compositions/demo/my-app"}],
        "/compositions/demo/my-app",
    ),
    (
        "builder-prs: whole placeholder, empty cell -> inert",
        "{href}",
        [{"valueKey": "href", "stringValue": ""}],
        None,
    ),
    # A multi-segment template still encodes, so a value containing a slash cannot forge a path.
    (
        "multi-segment template still percent-encodes an interpolated slash",
        "/compositions/{ns}/{name}",
        [{"valueKey": "ns", "stringValue": "demo"},
         {"valueKey": "name", "stringValue": "a/b"}],
        "/compositions/demo/a%2Fb",
    ),
]


def main():
    failures = 0
    for label, tmpl, row, expected in CASES:
        got = build_row_path(tmpl, row)
        ok = got == expected
        verdict = "route -> " + got if got is not None else "inert"
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {verdict}")
        if not ok:
            print(f"        expected: {expected!r}  got: {got!r}")
            failures += 1
    if failures:
        print(f"\n{failures} fixture(s) FAILED", file=sys.stderr)
        return 1
    print(f"\nall {len(CASES)} F8 route-derivation fixtures passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
