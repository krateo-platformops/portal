---
type: Architecture
title: "CR-TRACE — the 2026-06-23 CR reachability sweep"
description: "Point-in-time trace report: 13-agent reachability sweep over the chart's then-180 CRs ahead of the consolidated fresh-GKE install (the chart renders ~530 CRs at 1.6.0 — historical record, not current inventory)."
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [archive]
timestamp: 2026-06-23T00:00:00Z
---

# TRACE REPORT — krateo-platformops/portal CR inventory

> Generated 2026-06-23 by a 13-agent reachability sweep (orphan-trace workflow) over
> the chart's CRs, ahead of the consolidated fresh-GKE install. Method: build the root
> set (app-shell Layout + nav Menu + `page-*` roots), walk every `resourceRefId` /
> `resourcesRefs.items` / `apiRef` / `endpointRef` edge, then adjudicate each
> unreachable CR as **superseded** (dead, prune) or **dynamic** (alive at runtime via
> Helm flags, `{{ .k }}` fan-out, shared-prefix coordination, or state ConfigMaps).

**Scope:** consolidation / fresh-install decision. 180 total CRs · 155 reachable from roots (app-shell + nav Menu + page-*) · 22 orphan candidates.

## 1. Summary
Of 180 CRs, **172 truly ship** and **8 should be pruned**. The 22 orphan candidates split into **8 genuinely-dead (superseded)** widgets — all retired migration leftovers (DataGrid→Table, EventList→List, donut/ring→conditions List, bare table→card grid) — and **14 false orphans** that are alive at runtime (Helm-flag fixtures, endpoint Secrets, state ConfigMaps, `{{ .k }}` fan-out chips, and shared-prefix filter coordinators) and must be kept. No reachable page CR lacks a nav route.

## 2. Prune before install (superseded / dead — 8)
| CR | Kind | Why prune |
|---|---|---|
| `compositions-panels` | RESTAction | Feeds only the dormant `compositions-page-datagrid`; live /compositions uses `compositions-table` + `compositions-list`. |
| `compositions-page-datagrid` | DataGrid | Replaced by `compositions-table` (Table over `compositions-list`); referenced by no `resourceRefId`, only stale comments. |
| `compositions-page-button-drawer-filters` | Button + Filters | Filter-drawer of the retired DataGrid path; replaced by inline `compositions-toolbar` status+range chips. |
| `blueprints-table` | Table | Nav /blueprints now renders `blueprints-grid` (List card-grid); table referenced by nothing. |
| `activity-events` | EventList | Retired non-antd widget; dashboard uses `list-activity-events` (antd List) off the same `events` RA. |
| `eventlist-composition-detail-events` | EventList | Retired non-antd widget; detail uses `list-composition-detail-events` (antd List) off `composition-events`. |
| `status-donut` | Chart | Zero inbound refs; replaced by `dashboard-conditions` List (real `status.conditions`) in `status-card`. |
| `progress-composition-detail-health` | Progress | Zero inbound refs; replaced by `list-composition-detail-conditions` List in `card-composition-detail-health`. |

## 3. Keep (dynamic — do NOT prune, 14)
Looked orphan to a static inbound-edge scan, but referenced at runtime:
- **Fan-out chips (`{{ .k }}` templated, listed in parent `resourcesRefs.items`):** `bp-cat-btn-*` (blueprints category), `comp-range-btn-*` / `comp-status-btn-*` (compositions toolbar), `mp-cat-btn-*` (marketplace category), `range-btn-*` (dashboard range).
- **Shared-prefix coordinators:** `marketplace-filters` + `marketplace-category-filter` (couple to `marketplace-grid` by prefix; options derived from catalog tags).
- **Runtime state ConfigMaps:** `blueprint-drafts` (form save/resume via `/call` + `blueprint-formdef`), `dashboard-range` (read by `dashboard-data` RA).
- **Endpoint Secret:** `blueprints-endpoint` (endpointRef for `blueprints-catalog` / `blueprint-detail` / `blueprint-install-formdef` / `global-search`).
- **Helm-flag fixtures (non-widget):** `admin-password`, `cyberjoker-password` (authn User passwordRefs), `demo-system` namespace (gated by `enableDemoSystemNamespace`, paired with cyberjoker RBAC).

## 4. Feature map (reachable CRs by area + backing RESTActions)
| Area | Root | Backing RESTAction(s) (`apiRef`) |
|---|---|---|
| **Shell / nav** | `app-shell` (Layout), `sidebar-nav` (Menu) | `projects` (via `project-select`); `brand-logo`, `tenant-chip` |
| **Dashboard** | `dashboard-flex` | `dashboard-data` (greeting, throughput, deltas), `compositions-list` (stat/delta cards, `dashboard-conditions`, `rail-list`), `events` (`list-activity-events`) |
| **Compositions (list)** | `page-compositions` | `compositions-list` (table + status/range chip counts) |
| **Composition detail** | `page-composition-detail` | `composition-detail` (header/metadata/spec/conditions/manifest + sync/pause/delete), `composition-events` (events List), `composition-resources` (rail + relations), `composition-editdef` (edit Form) |
| **Blueprints (list)** | `page-blueprints` | `blueprints-cards` (`blueprints-grid`/`blueprints-list`) |
| **Blueprint detail / create / install** | `page-blueprint-detail`, `page-blueprint-create`, `page-blueprint-install` | `blueprint-detail` (info/metadata/status/update + create/delete), `blueprint-formdef` (create Form), `blueprint-install-formdef` (install Form), `blueprint-install-origin` (the change-request note above the install Form, builder charts only; also decides whether the page composes it) |
| **Marketplace** | `marketplace-flex` | `blueprints-catalog` (`marketplace-grid` + category facet) |
| **Create** | `create-header` / `create-wizard` (Steps) | (no RA; hosts blueprint create flow) |
| **Settings** | `page-settings` (`settings-admin` Tabs) | `settings-krateo-status`, `settings-users` |
| **Search** | (nav /search) | `global-search` (`search-results`) |

## 5. Page CRs with no matching nav route
**None.** All seven `page-*` roots map to a defined nav route: `page-compositions`→`/compositions`, `page-composition-detail`→`/compositions/{namespace}/{name}`, `page-blueprints`→`/blueprints`, `page-blueprint-detail`→`/blueprints/{namespace}/{name}`, `page-blueprint-create`→`/blueprints/{namespace}/{name}/new`, `page-blueprint-install`→`/marketplace/{name}/install`, `page-settings`→`/settings`. (Dashboard and marketplace roots are `dashboard-flex` / `marketplace-flex`, intentionally not `page-*`; `/dashboard`, `/marketplace`, `/search` and the `/compositions/{namespace}` collapse-to-`compositions` route are all covered.)
