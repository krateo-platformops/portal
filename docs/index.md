---
type: ChartRepo
title: portal — index
description: The map of the portal doc bundle — the blueprint chart that ships the Krateo Composable Portal's content as a composition.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, blueprint, chart-repo, composition]
timestamp: 2026-08-07T00:00:00Z
---

# portal

This repo ships **one chart** (`helm/portal/`, published as
`oci://ghcr.io/krateo-platformops/charts/portal`, deployed by the installer at
`1.6.0`): the **content** of the Krateo Composable Portal. It is a *blueprint* —
registered with the platform as a `CompositionDefinition`, instantiated as a `Portal`
composition CR, and reconciled by the composition-dynamic-controller as a Helm release
of ~530 Krateo custom resources: widget trees, RESTActions, personas, RBAC and the
`demo-system` namespace. The SPA that renders them is
[frontend](https://github.com/krateo-platformops/frontend); the API that resolves them
is [snowplow](https://github.com/krateo-platformops/snowplow).

## The bundle (start here)

- [overview](./overview.md) — what the chart deploys and how it works: the widget/RA
  content plane, the composition model, routes and tiers, demo-system provisioning.
- [usage](./usage.md) — how the installer consumes it, direct registration via
  `CompositionDefinition`, and the local `helm template` loop.
- [configuration](./configuration.md) — the whole values surface (a `Portal` CR's
  `spec`): personas, demo-system, tenant branding, spoke read-back, RBAC tiers.
- [api](./api.md) — the contract it emits: the derived `Portal` composition API, the
  `AuditRecord` CRD it defines, and the CR kinds it instantiates (owned elsewhere).
- [examples](./examples.md) — the runnable examples under `examples/`.
- [release](./release.md) — how a release ships (tag → OCI chart → installer pin).
- [log](./log.md) — curated history.
- [llms.txt](./llms.txt) — the version-pinned agent index of this bundle.

## Authoring rules (for chart contributors)

- [authoring-keyextras](./authoring-keyextras.md) — the `spec.keyExtras` cache-key
  declaration rule, CI-enforced by `scripts/lint-keyextras.py`.

## Design records

- [marketplace-registry-discovery](./marketplace-registry-discovery.md)
  (`status: diverged`) — the Marketplace-as-discovery design; the Helm-repo slice
  shipped (as `restaction.blueprints-catalog`), the source-config CRD and OCI/tgz
  discovery did not.
- [snowplow-yaml-api-step-enabler](./snowplow-yaml-api-step-enabler.md)
  (`status: implemented`) — the snowplow YAML→JSON api-step enabler this chart's
  catalog RESTAction depends on (shipped in snowplow; required `>= 1.4.0`).

## Archive (`tags: [archive]` — point-in-time, not current truth)

- [CR-TRACE](./CR-TRACE.md) — the 2026-06-23 13-agent CR-reachability sweep (180 CRs
  then; the chart renders ~530 today). Historical record of the consolidation pass.

## Chart-adjacent corpora (code-adjacent, authoritative for their scope)

  — the drop-in sidebar-entry mechanism for Autopilot-authored pages (#106).
- [`helm/portal/tests/blueprints-catalog/README.md`](../helm/portal/tests/blueprints-catalog/README.md)
  and [`helm/portal/tests/blueprint-render/README.md`](../helm/portal/tests/blueprint-render/README.md)
  — self-contained jq fixtures for the two heaviest RESTAction filters.
- `scripts/` — `lint-keyextras.py` (CI gate), `mockup-diff.mjs` (mockup coverage audit),
  `test-rownavigateto-f8.py` (route-derivation fixtures), `test-builder-install.py` (the builder
  publish chain's Install step: renders the chart and runs its RESTAction and widget jq on
  fixtures; CI gate).
