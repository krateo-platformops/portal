---
type: Example
title: Register and instantiate the portal composition
description: The full composition flow — a CompositionDefinition registering the published portal chart, then a Portal CR instantiating it with custom values.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, composition, compositiondefinition]
timestamp: 2026-08-07T00:00:00Z
---

# Register and instantiate the portal composition

Two manifests, applied in order:

1. [`compositiondefinition.yaml`](./compositiondefinition.yaml) — registers the
   published chart (`1.6.0`). core-provider derives the `Portal` CRD
   (`composition.krateo.io/v1-6-0`) from the chart's `values.schema.json` and starts
   its controller.
2. [`portal.yaml`](./portal.yaml) — one `Portal` CR = one reconciled portal-content
   release (~530 CRs): custom tenant name, demo-system enabled.

## Preconditions

- A cluster running the Krateo engine — core-provider + composition-dynamic-controller
  (a stock Krateo installer deploy has them; note the installer *already* provisions
  its own portal instance there, so run this example against an engine-only cluster
  or pick a different CR name/namespace).
- The peer CRDs installed: frontend widget CRDs, snowplow `RESTAction` CRD
  (snowplow ≥ 1.4.0), authn `User` CRD — see [usage](../../docs/usage.md).

## Apply

```sh
kubectl apply -f ./compositiondefinition.yaml
kubectl wait compositiondefinition/portal -n krateo-system \
  --for=condition=Ready --timeout=300s
kubectl apply -f ./portal.yaml
```

## Verify

```sh
kubectl get portals.composition.krateo.io -n krateo-system
kubectl get restactions,flexes -n krateo-system | head
kubectl get namespace demo-system
```

Log in to the portal frontend as `admin` — the password was generated once and is
preserved across reconciles:

```sh
kubectl get secret admin-password -n krateo-system \
  -o jsonpath='{.data.password}' | base64 -d
```
