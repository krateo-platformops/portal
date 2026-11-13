---
type: Usage
title: portal — authoring rule, spec.keyExtras
description: Any widget rendered on a parameterized route (or consuming query context) must declare, in its chart template, exactly the request-extras keys it receives — CI-enforced by scripts/lint-keyextras.py.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, authoring, caching, snowplow]
timestamp: 2026-08-07T00:00:00Z
---

# Authoring rule: `spec.keyExtras`

**Any widget rendered on a parameterized route (or consuming query context) MUST
declare, in its chart template, exactly the request-extras keys it receives:**

```yaml
spec:
  keyExtras: [name, namespace]   # e.g. the /compositions/{namespace}/{name} route params
  # or, for chrome widgets receiving the shell's project scope:
  keyExtras: [projects]
```

## Why (snowplow ≥ 1.7.11, F6 self-quarantine Put-guard)

- A widget that receives request extras it does **not** declare is refused by the
  cache guard: it serves 200 but is **never cached** — cold resolve on *every*
  visit, including revisits. It fails safe (no wrong content), but caching is
  permanently defeated. This was the exact shape of the PR #21 chrome gap and the
  issue #26 structural gap (84 guard declines per browser walk).
- Declarations are only real if **chart-baked** — live widget-CR edits are
  reverted by the composition controller on the next portal reconcile.
- Identity keys (`username`, `groups`, `displayName`) as *caller identity* never
  need declaring — snowplow exempts them. A **route param** that happens to be
  named `username` (e.g. `/settings/access/{username}`, the *viewed* persona)
  still must be declared: it partitions the widget's content.

## Verifying a new/changed widget (snowplow ≥ 1.7.11)

Navigate its route and check there is no `"Widget request carried extras not
declared in spec.keyExtras; declining to cache"` WARN naming it (counter:
`snowplow_widget_skipped_undeclared_extras_put_total`), and that a revisit logs
an L1 HIT at an `extras_len>0` key.

## The CI gate

[`scripts/lint-keyextras.py`](../scripts/lint-keyextras.py) (the `keyextras` job in
[`.github/workflows/lint.yaml`](../.github/workflows/lint.yaml)) enforces the route
half of this rule: it renders the chart (tempdir copy, `CHART_VERSION` substituted),
derives the route table from the `sidebar-nav` Menu (`{param}` path segments → the
extras keys the route injects), walks each route's widget tree transitively via
same-chart `resourcesRefs`, and **fails** if any reachable widget's `keyExtras`
misses that route's params. It also **warns** (non-gating) for app-shell/header
chrome widgets that do not declare `projects`.

## Filtered pages: `krateo.io/filter-keys`

Every URL query param reaches **every** widget on a page as an extra, not only the
widget that reads it. A filtered overview page (root-pages UX: a filter bar writing
`?source=`, `?kind=`, …) therefore puts its filter params on every widget in its
tree, and each widget has to declare all of them. The page root names them once:

```yaml
metadata:
  annotations:
    "krateo.io/filter-keys": "projects,source"
spec:
  keyExtras: [projects, source]
```

`scripts/lint-keyextras.py` **fails** any widget reachable from an annotated root
(the same same-chart `resourcesRefs` walk) whose `keyExtras` misses one of those keys.
Unlike the route rule, it runs over every chart under `helm/` whose templates use the
annotation, because page roots live in the page charts. A widget another chart owns
(the shell's `new-composition` Button on /dashboard) is outside the walk and is not
checked.
