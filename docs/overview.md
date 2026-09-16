---
type: Architecture
title: portal — overview
description: What the portal blueprint chart deploys and how it works — the widget/RESTAction content plane, the composition model, routes, RBAC tiers and demo-system provisioning.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, blueprint, composition, widgets, restaction]
timestamp: 2026-08-07T00:00:00Z
---

# Overview

The portal chart is **pure content**: it deploys no workload, no image, no Service. A
default render (`helm template`, all toggles on) emits ~530 namespaced Kubernetes
objects that together *are* the Krateo Composable Portal:

| Group | Kinds (count at 1.6.0) | Role |
|---|---|---|
| `widgets.templates.krateo.io/v1beta1` | Flex ×104, Paragraph ×88, Card ×60, Listy ×39, Button ×35, Row ×23, Table ×22, Statistic ×16, Tag ×14, Markdown ×11, Descriptions ×9, Form ×7, + Tabs/Steps/Select/LineChart/Input/Col/YamlViewer/Theme/RangePicker/Menu/Layout/Image/Divider/Alert | The widget trees of every page, plus the app shell |
| `templates.krateo.io/v1` | RESTAction ×45 | The declarative data layer behind every page (resolved by snowplow) |
| `basic.authn.krateo.io/v1alpha1` + `v1` Secrets | User ×2, basic-auth Secret ×2 | The `admin` and `cyberjoker` demo personas |
| `rbac.authorization.k8s.io/v1` | Role ×7, RoleBinding ×7, ClusterRole ×2, ClusterRoleBinding ×3 | Persona grants + demo-system grants |
| `v1` | Namespace `demo-system`, 2 endpoint Secrets | Demo target namespace; external-API endpoints for RESTActions |
| `apiextensions.k8s.io/v1` | CustomResourceDefinition ×1 | `auditrecords.audit.krateo.io` (see [api](./api.md)) |

The **frontend** SPA boots from `config.INIT` →
`/call?resource=layouts&name=app-shell` (the chart's `layout.app-shell`), and
**snowplow** resolves every widget and RESTAction on demand, enforcing the caller's
RBAC — so *what this chart places in which namespace* directly decides *who sees what*.

## The composition model

The chart is never operated as a bare Helm release on a live platform. It is a
**blueprint**:

1. A `CompositionDefinition` (`core.krateo.io/v1alpha1`) points at
   `oci://ghcr.io/krateo-platformops/charts/portal` at a pinned version
   ([`compositiondefinition.yaml`](../compositiondefinition.yaml)).
2. **core-provider** pulls the chart, derives a CRD from its
   `values.schema.json` — kind `Portal`, group `composition.krateo.io`, version
   `v1-6-0` (the chart version, dots→dashes) — and starts a
   composition-dynamic-controller for it.
3. Each **`Portal` CR** is reconciled as one Helm release of this chart; the CR's
   `spec` *is* the chart values. Drift in any of the ~530 children is detected and
   re-applied on the next reconcile.

The **Krateo installer** automates all of this: its umbrella pins `portal` at
`1.6.0` (feature `portal`, dependency: `frontend`) and emits the
CompositionDefinition + the `Portal` instance for you.

Because the derived API *version* embeds the chart version, a chart-version bump is
an API-identity change for the composition — the handover is managed by the platform
(cdc ≥ 1.3.3 upgrades in place), not by this chart.

## Routes: one Menu is the router

`menu.sidebar-nav.yaml` is the **single route source** (no RoutesLoader): every
`items[]` entry with a `path` is a route; entries with a `label` are visible sidebar
items, label-less entries are hidden routes (detail/create pages, `/search`, deep-link
aliases). Templated `{param}` segments become route params and reach the page's
widgets as request *extras* — which is why every widget on a parameterized route must
declare them ([authoring-keyextras](./authoring-keyextras.md)).

Content resolution per route: an explicit `resourceRefId` (RBAC-aware, via
`resourcesRefs`) or the `page:<slug>` convention (resolves `flexes/page-<slug>`).
Visible nav at 1.6.0: Dashboard, Compositions, Blueprints, Marketplace ·
Observability, Incidents, Alerts · Portal Builder, Blueprint Builder, API Builder ·
Settings, Clusters.

**Extension without editing the Menu**: the Menu carries no hand-written items at all. Its entries
are computed by `restaction.sidebar-nav` from the `krateo.io/nav-*` annotations on the page roots
the cluster actually has, so a page declares its own sidebar entry and installing the chart that
ships it is what puts the entry there. A page set in a SEPARATE chart therefore reaches the sidebar
without this chart knowing it exists — which a build-time glob over this chart's own files could
never do.

## RBAC tiers: page visibility by namespace placement

The `portal.tierNamespace` helper (`templates/_tiers.tpl`) resolves
`.Values.tiers.<tier>` (empty → `.Release.Namespace`). Ten page-root Flexes and the
Menu's `resourcesRefs` use it, so snowplow resolves each page's root **as the user**
and the frontend hides pages the user's Role cannot GET:

- **common** (always the release namespace): Dashboard, Compositions and all
  detail/hidden pages — `flex.page-compositions` deliberately lives in the common
  tier (#68) so a namespace-scoped tenant sees its own compositions.
- **tenant**: Blueprints.
- **admin**: Marketplace, Observability, Incidents, Alerts, the three Builders,
  Settings, Clusters.

All tiers default to `""` — the single-namespace layout; tiering is opt-in
([configuration](./configuration.md)).

## Demo-system provisioning

With `enableDemoSystemNamespace: true` (the default) the chart creates the
**`demo-system` namespace** — the canonical target where demo compositions are
instantiated — and, when the `cyberjoker` persona is also enabled, grants the `devs`
group inside it: `get/list` on `compositiondefinitions.core.krateo.io`,
`create/get/list` on every `composition.krateo.io` kind, `get/list` on every
widget kind and on RESTActions, and `get` on ConfigMaps. Combined with
cyberjoker's release-namespace read grants (widgets +
RESTActions) and cluster-wide `list namespaces` / `get,list` CRDs, this makes
`cyberjoker` a working namespace-scoped persona: it can browse the portal and
instantiate blueprints **only** in `demo-system`. The `admin` persona is the
counterpart: group `admins` bound to `cluster-admin`.

Both persona passwords are generated once (`randAlphaNum 12`) and then preserved
across reconciles by a `lookup` guard — an unguarded generate would churn a Helm
revision every reconcile cycle.

## External endpoints (RESTAction inputs)

Two `Endpoint` Secrets ship with the chart:

- `blueprints-endpoint` → the marketplace catalog source
  (`restaction.blueprints-catalog` GETs `/charts/blueprints/index.yaml` +
  `/charts/operators/index.yaml` and merges them). **Note:** its `server-url` still
  points at the legacy pre-migration Pages helm-repo index — this source is
  unmigrated; the OCI-registry catalog is the planned successor.
- `clickhouse-endpoint` → the clickstack ClickHouse HTTP interface (`:8123`),
  consumed by the observability reconcile-metrics RESTActions.

## Platform peers

| Peer | Relationship |
|---|---|
| [frontend](https://github.com/krateo-platformops/frontend) | Owns the widget CRDs; renders what this chart declares (INIT → `app-shell`). |
| [snowplow](https://github.com/krateo-platformops/snowplow) | Owns the `RESTAction` CRD; resolves widgets + RAs with the caller's RBAC. Catalog RAs require snowplow ≥ 1.4.0 (external fetch + YAML→JSON); caching requires ≥ 1.7.11 (`keyExtras`). |
| [authn](https://github.com/krateo-platformops/authn) | Owns the `User` CRD the personas instantiate. |
| [core-provider](https://github.com/krateo-platformops/core-provider) | Turns the published chart into the `Portal` composition API and reconciles instances. |
| [installer](https://github.com/krateo-platformops/installer) | Pins the chart version and provisions the CompositionDefinition + `Portal` CR. |
