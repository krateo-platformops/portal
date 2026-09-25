---
type: Log
title: portal — log
description: Curated chronological history of the portal blueprint chart — notable arcs, decisions and incidents; release notes stay in GitHub Releases.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [history]
timestamp: 2026-08-07T00:00:00Z
---

# Log

Curated history, newest first. Point-in-time records live as archives (the
[CR-TRACE sweep](./CR-TRACE.md)); design records carry a `status` field
([marketplace-registry-discovery](./marketplace-registry-discovery.md),
[snowplow-yaml-api-step-enabler](./snowplow-yaml-api-step-enabler.md)).

## 2026-09-25 — incident pages read Incidents

The incident pages read `incidents.observability.krateo.io` (incident-controller), one object per
occurrence of an alert's problem, instead of the one-per-alert `TroubleshootingReport`. An incident
joins its alert on the `observability.krateo.io/alert` label and `spec.alertRef`, not on a
normalized display name. **How to fix** shows the three scripts in `status.howToFix`
(precondition · apply · verify), each with its last run from `status.checks`; its one button is
"I applied it" (`spec.applied`). **Close** (`spec.closed`) replaces Resolve, cannot be undone and is not offered on a Resolved incident.
Check history (from `status.checks`) replaces the Remediation audit, and Involved resources,
Check history and Similar incidents (same alert first) share one tab strip.

## 2026-09-09 — builder-publish creates the destination repository

Publishing required a repository someone had already made by hand, which is backwards for a
brand-new blueprint: `toRepo.cloneFromBranch` presupposes a repo that already has that branch.
`helm/builder-publish` now renders the repository itself, as a CR, from the same claim.

**SCM dispatch is install-level.** `git.scm` sits beside `git.host`/`scheme` and selects WHICH
repository kind is rendered; the file commits stay scm-blind through git-provider either way. Only
`github` is implemented, because github-provider-kog is the only per-SCM provider that exists. An
unsupported value **fails the render** — twice over, at the values schema (`enum: [github]`) and
again at a template `fail` for consumers that skip schema validation. It deliberately does not skip
creation: skipping would produce a publish that reports success and then dies inside git-provider
with a clone error the author has to decode.

**Ordering is solved with `lookup`, because there is no ordering primitive.** `LocalResource` has no
`dependsOn` or `waitFor` field. The supported Krateo answer is Helm's `lookup`, which reads live
cluster state, plus the fact that composition-dynamic-controller re-renders every reconcile: the
first pass emits only the `Repository`, and a later pass — once the repo is real — emits the files.
No manual sequencing. Same pattern as the `nutanix-chain-lookup` blueprint, which proved it.

The gate keys on **`status.default_branch`**, not on the CR merely existing. That field comes from
GitHub's GET response, so it proves the repository exists, is initialised, AND has a branch — which
is exactly what `cloneFromBranch` needs. It also makes `repository.autoInit` load-bearing: a repo
created without an initial commit never publishes a `default_branch`, so the files correctly never
render against an empty repo.

**Adoption is free.** `Repository` is level-based — Observe does `GET /repos/{org}/{name}` first and
only creates when that reports absent. Declaring it unconditionally is therefore safe: an existing
repository is adopted, not re-created and not 422'd. There is no "does it exist?" check anywhere.

Two credential models now sit in one composition — `LocalResource` takes an inline
`credentials.secretRef`, `Repository` takes a `configurationRef`. Both are rendered here from the
SAME secret, so one token drives both halves and no operator wiring is added.

`repository.create=false` restores the previous behaviour exactly, for installs that provision
repositories out of band.

**New install dependency:** unless `repository.create=false`, this chart now needs
github-provider-kog, which owns `github.krateo.io/Repository`. That was a deliberate choice over
degrading gracefully when the CRD is absent, for the same reason the SCM check fails loudly.

Not verified end to end: no cluster available to this change has github-provider-kog installed, so
the rendered `Repository` could not be server-validated. Its fields were cross-checked against the
kind's OpenAPI document instead — every one resolves to a create-body property or a path parameter.

## 2026-08-07 — adopted the Krateo Documentation Standard

This bundle: root `docs/` + `examples/` + thin README. The old README prescribed a
`chart/` layout two migrations stale (the chart lives at `helm/portal/`), pinned the
sample CompositionDefinition at `1.2.2`, and claimed the demo toggle "deploys two
Route resources" (the chart ships no Route kind — provisioning is the namespace +
persona grants). The keyExtras authoring rule moved to
[authoring-keyextras](./authoring-keyextras.md); the two design docs got verified
`status` frontmatter (one `implemented`, one `diverged`).

## 2026-08-03 — 1.6.0: the org migration release

Repo restructured to `helm/portal/` and re-pointed to `krateo-platformops` (Go-wave
full-independence migration); CI moved to the canonical org workflows (shape-agnostic
`release-oci`, shared `security`). `1.6.0` is the version the installer pins.

## 2026-07 — the 1.5.x enterprise-UX arc (…→ 1.5.119)

A ~120-release polish-and-features run driven by mockup parity and UX review rounds:
the marketplace rework (#82–#86: merged blueprint+operator catalog off two helm-repo
indexes, data-driven category facets, installed-state LEFT-JOIN onto
CompositionDefinitions, detail page, facet-band layout); the IA split of
Observability / Incidents / Alerts (alert rules = configuration, incidents = triage,
per-alert detail pages); the three Builder pages (Portal / Blueprint / API) with the
right-aligned Ask-Autopilot CTA; the nav-fragments drop-in mechanism (#106) so
Autopilot-published pages ship their own sidebar entry; W3 cluster registry +
one-spoke live read-back; the generic `/resources/...` drill-down route (F5);
notifications Listy (SSE-driven); brand theming (Brand v2).

## 2026-06/07 — caching correctness: the keyExtras gate

snowplow's F6 self-quarantine Put-guard made undeclared request-extras a permanent
cache defeat (PR #21 chrome gap; #26: 84 guard declines per browser walk). The rule
— every widget on a parameterized route declares its extras keys in-chart — became
[an authoring invariant](./authoring-keyextras.md) enforced by
`scripts/lint-keyextras.py` in PR CI.

## 2026-06-23 — the CR reachability sweep

A 13-agent orphan-trace over the then-180 CRs ahead of the consolidated fresh-GKE
install: 8 superseded widgets pruned, 14 false orphans adjudicated alive (Helm-flag
fixtures, `{{ .k }}` fan-out chips, state ConfigMaps, endpoint Secrets). Preserved
as [CR-TRACE](./CR-TRACE.md) (archive).

## Earlier — from starter to blueprint

The repo began as the `composable-portal-starter` (basic auth users, demo-system,
a handful of pages) and grew into the platform's full portal content plane, consumed
exclusively through the composition model (CompositionDefinition → derived `Portal`
API → cdc-reconciled release).
