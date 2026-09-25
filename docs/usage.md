---
type: Usage
title: portal — usage
description: How the portal blueprint is consumed — the installer path, direct CompositionDefinition registration, instantiating Portal CRs, and the local helm-template loop.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, blueprint, install, composition]
timestamp: 2026-08-07T00:00:00Z
---

# Usage

## The installer path (the normal way)

A stock Krateo installer deploy provisions the portal for you: the umbrella pins the
`portal` component at chart `1.6.0` (feature `portal`, kind `Portal`, dependency
`frontend`) and emits both the `CompositionDefinition` and the `Portal` instance in
`krateo-system`. Nothing to do; the portal renders once frontend + snowplow + authn
are up.

## Direct registration (composition model, no installer)

On a cluster that already runs the Krateo engine (core-provider + cdc):

```sh
# 1. Register the blueprint — core-provider derives the Portal CRD
#    (composition.krateo.io/v1-6-0, kind Portal) from the published chart's
#    values.schema.json and starts its controller:
kubectl apply -f compositiondefinition.yaml

# 2. Wait for the definition to become Ready:
kubectl get compositiondefinition portal -n krateo-system -w

# 3. Instantiate — one Portal CR = one reconciled portal-content release:
kubectl apply -f examples/portal-composition/portal.yaml
```

The CR's `spec` is the chart's values surface ([configuration](./configuration.md)).
Day-2 changes go **through the CR** — direct edits to the rendered widget CRs are
reverted by the composition controller on the next reconcile.

A direct `helm install oci://ghcr.io/krateo-platformops/charts/portal` renders the
same content but bypasses the composition controller (no drift healing, no
`Portal`-CR day-2 surface) — use it only for throwaway inspection, never on a live
platform where the installer already owns a portal instance.

## Preconditions

The chart instantiates CRs whose CRDs are owned by other components — they must be
installed first (the installer orders this for you):

- **frontend CRDs** — every `widgets.templates.krateo.io/v1beta1` kind.
- **snowplow CRDs** — `restactions.templates.krateo.io`; snowplow **≥ 1.4.0** for the
  marketplace catalog (external endpoint fetch + YAML→JSON), **≥ 1.7.11** for widget
  caching (`spec.keyExtras` guard).
- **authn CRDs** — `users.basic.authn.krateo.io` (the two personas).

## Local render loop (no cluster)

`Chart.yaml` carries the `CHART_VERSION` release placeholder, which breaks a raw
`helm template` on the working tree — substitute it in a copy:

```sh
cp -r helm/portal /tmp/portal-render
sed -i 's/CHART_VERSION/0.0.0-dev/g' /tmp/portal-render/Chart.yaml
helm lint /tmp/portal-render
helm template smoke /tmp/portal-render --namespace krateo-system
```

Then run the authoring gates:

```sh
python3 scripts/lint-keyextras.py   # F6 cache-key declaration gate (does its own tempdir render)
python3 scripts/test-builder-install.py   # builder Install step: the chart's own jq on fixtures (JQ=gojq for snowplow's engine)
node scripts/mockup-diff.mjs        # mockup coverage / journey / hardcoded-data audit
```

CI runs the same on every PR (`.github/workflows/lint.yaml`: helm lint +
`values.schema.json` validity + render smoke + the `keyextras` and `builder-install` jobs).
