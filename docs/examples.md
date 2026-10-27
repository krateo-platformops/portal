---
type: ExampleIndex
title: portal — examples
description: Index of the runnable examples under examples/.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, examples]
timestamp: 2026-08-07T00:00:00Z
---

# Examples

- [portal-composition](../examples/portal-composition/README.md) — the full
  composition flow: register the blueprint with a `CompositionDefinition`, then
  instantiate a `Portal` CR with custom values (tenant name, demo-system on).
  Works against a stock Krateo engine (core-provider + cdc).

- [form-options](../examples/form-options/README.md) — a demo blueprint whose create form
  loads options from RESTActions (a region → zone cascade, the shared namespaces source),
  hides a field until it applies, and titles its list rows; with its options RESTActions.

Related fixtures (not standalone examples): the jq test corpora under
[`helm/portal/tests/`](../helm/portal/tests/) exercise the `blueprints-catalog`,
`blueprint-render`, `blueprint-palette` and `composition-architecture` RESTAction filters offline.
