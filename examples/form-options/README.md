---
type: Example
title: A blueprint whose create form loads options and cascades
description: A demo blueprint using `x-krateo-options` — a region → zone cascade from a RESTAction, the shared namespaces source, key/value image pull secrets — with its options RESTActions.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, blueprints, forms, restactions]
timestamp: 2026-10-27T00:00:00Z
---

# A blueprint whose create form loads options and cascades

`x-krateo-options` is documented in [form-options](../../docs/form-options.md); this is one
blueprint that uses it, and the RESTActions it needs. It is the keyword the portal translates into
the Form's lists. The other retired keywords do nothing in a blueprint's schema, and
`scripts/lint-form-keywords.py` fails on them here.

| File | What it is |
| --- | --- |
| [`chart/values.schema.json`](./chart/values.schema.json) | the schema the create form is built from |
| [`options.yaml`](./options.yaml) | the catalogue ConfigMap and the two options RESTActions |
| [`compositiondefinition.yaml`](./compositiondefinition.yaml) | registers the chart |

What the create form does with it:

- **Region** lists the regions `form-demo-regions` returns; **Zone** waits for a region, then
  lists that region's zones (`form-demo-zones`, called with `extras: {region}`), and is
  cleared when the region changes.
- **Target namespace** lists the namespaces the caller can list, from the portal's shared
  `krateo-system/form-options-namespaces`.
- **Node port** is always shown. An `if`/`then` makes it required for NodePort, and Helm enforces
  that when the composition is deployed, not the form.
- **Hosts** takes at least one row and at most five.
- **Image pull secrets** declare no item fields, so each is a key/value row.

## How the refs are wired

The portal's `blueprint-formdef` reads the schema from core-provider's jsonschema ConfigMap,
collects every `x-krateo-options.ref`, and the create form turns each into a GET ref:
`form-demo-regions` and `form-demo-zones` (bare names) resolve in the blueprint's namespace,
`krateo-system/form-options-namespaces` where it says. snowplow resolves each path and whether
the caller may call it, so the RESTActions need the usual RBAC: whoever creates this blueprint
must be able to `get` them.

## Apply

```sh
helm package chart && helm push form-options-demo-0.1.0.tgz oci://<your-registry>/charts
# set spec.chart.url in compositiondefinition.yaml, then:
kubectl apply -f options.yaml
kubectl apply -f compositiondefinition.yaml
kubectl wait compositiondefinition/form-options-demo -n demo-system --for=condition=Ready --timeout=300s
```

Then open Blueprints → form-options-demo → Create.

## The rules an options RESTAction follows

`options.yaml` is annotated `krateo.io/form-options: "true"` and follows the rules
`scripts/lint-form-options.py` checks on the portal's own sources: a typed value never reaches
a path unescaped (`form-demo-zones` keeps the region out of its path entirely, and gates the
call on it), no Secrets are read, and the answer is a list of `{label, value}`.
