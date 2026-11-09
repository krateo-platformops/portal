---
type: Configuration
title: portal — configuration
description: The whole values surface of the portal blueprint chart — personas, demo-system, tenant branding, observability console link, spoke read-back — i.e. a Portal CR's spec.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, values, configuration]
timestamp: 2026-08-07T00:00:00Z
---

# Configuration

The chart's [`values.yaml`](../helm/portal/values.yaml) is fully typed by
[`values.schema.json`](../helm/portal/values.schema.json) — and that schema is also
the **composition API**: core-provider derives the `Portal` CRD's `spec` from it, so
everything below is settable both as Helm values and as fields of a `Portal` CR.
Under the installer, overrides ride in through the Installer CR's
`componentValues.portal`.

## Personas

| Value | Type | Default | Effect |
|---|---|---|---|
| `enableAdminUser` | bool | `true` | `admin` User (basic-auth) + `admin-password` Secret in the release namespace; group `admins` bound to the `cluster-admin` ClusterRole (binding name `cluster-admin-binding-<ns>`). |
| `enableCyberjokerUser` | bool | `true` | `cyberjoker` User + `cyberjoker-password` Secret; group `devs` granted: `get/list` widgets and `get` restactions in the release namespace, `list` namespaces and `get/list` CRDs cluster-wide (+ the demo-system grants below when the demo namespace is enabled). |

Both password Secrets are `lookup`-guarded: the `randAlphaNum 12` password is
generated **once** and re-read from the live Secret on every subsequent render, so
composition reconciles do not churn credentials or Helm revisions
([`rbac.admin-rbac.yaml`](../helm/portal/templates/rbac.admin-rbac.yaml)). Read a
password with:

```sh
kubectl get secret admin-password -n krateo-system -o jsonpath='{.data.password}' | base64 -d
```

## Demo system

| Value | Type | Default | Effect |
|---|---|---|---|
| `enableDemoSystemNamespace` | bool | `true` | Creates the `demo-system` Namespace (the demo target for instantiating blueprints) and, if `enableCyberjokerUser` is also true, grants `devs` inside it: `get/list` compositiondefinitions, `create/get/list` every `composition.krateo.io` kind, `get/list` every widget kind and restactions, `get` configmaps. |

> The chart no longer deploys demo `Route` resources into `demo-system` (older docs
> and the `values.yaml` comment still say so) — provisioning is the namespace + the
> persona grants; demo *content* is whatever compositions you instantiate there.

## Branding & links

| Value | Type | Default | Effect |
|---|---|---|---|
| `tenant` | string | `krateo-enterprise` | Display name of the platform tenant — the sider tenant chip (`tag.tenant-chip`) and the page eyebrows (dashboard greeting, compositions, observability, incidents, alerts, builders). A deployer-set label, not derived data. |
| `observabilityConsoleUrl` | string | `""` | URL of an external observability console (e.g. the clickstack/HyperDX UI). When set, "View in console" links appear on `/observability` and the alert-detail pages (`button.obs-console-link`, `button.alert-detail-hyperdx-*`); empty hides them. No default target is invented. |

## Spoke read-back (W3-3)

The `/clusters/{name}` detail page can show **live** compositions read directly from
one registered spoke cluster's apiserver (`restaction.cluster-compositions`). snowplow
`endpointRef` references are static, so the read-back is bound per-install to ONE
spoke:

| Value | Type | Default | Effect |
|---|---|---|---|
| `spokeReadbackCluster` | string | `krateo-spoke-demo` | Name of the one registered spoke whose detail page gets the live read-back. |
| `spokeReadbackEndpointSecret` | string | `krateo-spoke-demo-endpoint` | Name of the **derived** endpoint Secret (snowplow shape: `server-url`/`token`/`insecure`/`certificate-authority-data`, with `server-url` carrying the `/apis` suffix) — derived from the spoke's kubeconfig Secret, *not* the kubeconfig Secret itself. |
| `spokeReadbackEndpointNamespace` | string | `krateo-system` | Namespace of that Secret (must be readable by snowplow's ServiceAccount). |

## Page visibility (no value to set)

There is nothing to configure. Every page root this chart ships is installed into the
release namespace, and page visibility follows the namespace RBAC the cluster already
has: `restaction.sidebar-nav` discovers the namespaces where **the caller** may `list`
page roots and lists the roots in each as that caller, so a user sees exactly the pages
their Role lets them read. Install a page chart into another namespace and its entries
appear for whoever can read them there — see [overview](./overview.md#page-visibility-is-discovered-not-configured).

This replaced a `tiers.{common,tenant,admin}` values trio and a `portal.tierNamespace`
helper (removed in #261). They were not an RBAC mechanism: no Role or RoleBinding ever
referenced them. They were three deployer-set namespace strings that the sidebar could
see and nothing else could, so a page chart installed into any fourth namespace was
invisible to the nav no matter what the caller could read.

One hard limit survives, and it constrains any per-namespace split: a page root reached
by the `page: <slug>` convention is resolved in the namespace the **frontend** is
configured with (`config.params.FRONTEND_NAMESPACE`), never in the nav entry's
namespace. Only a root addressed by `resourceRefId` can be installed elsewhere; a
`page:`-addressed root installed outside the frontend's namespace is a silent 404.

## Fixed (non-value) configuration baked into templates

- `blueprints-endpoint` Secret — the marketplace catalog source URL (legacy Pages
  helm-repo index; unmigrated — see [overview](./overview.md)).
- `clickhouse-endpoint` Secret — `http://krateo-clickstack-clickhouse-clickhouse-headless.<ns>.svc:8123`,
  the observability metrics source.
- `theme.app-theme` — tenant brand tokens (default: font only; `colorPrimary` and the
  sidebar rail deliberately left to the frontend's per-mode derivation).
