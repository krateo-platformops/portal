---
type: Example
title: A blueprint whose create form loads options, cascades and hides fields
description: A demo blueprint using every x-krateo-* schema extension — a region → zone cascade from a RESTAction, the shared namespaces source, a field shown only for NodePort, titled host rows, key/value image pull secrets — with its options RESTActions.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, blueprints, forms, restactions]
timestamp: 2026-10-27T00:00:00Z
---

# A blueprint whose create form loads options, cascades and hides fields

The keywords are documented in the frontend's
[form-schema-extensions](https://github.com/krateo-platformops/frontend/blob/main/ui/docs/form-schema-extensions.md);
this is one blueprint that uses all of them, and the RESTActions it needs.

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
- **Node port** appears only when **Service type** is NodePort. The same rule is written as
  an `if`/`then` beside it, so Helm enforces it when the composition is deployed.
- **Hosts** rows are titled by their host; at least one, at most five.
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
