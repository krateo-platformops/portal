# portal

The **portal blueprint chart** of Krateo PlatformOps — the content of the Composable
Portal (pages, widgets, RESTActions, personas, demo-system), shipped as a Krateo
composition.

## What is this

A chart repo, not a service: `helm/portal/` renders ~530 Krateo custom resources —
the app-shell Layout, the sidebar Menu (the single route source), every page's widget
tree (`widgets.templates.krateo.io/v1beta1`), the 45 `RESTAction`s that feed them,
two demo personas with their RBAC, and the `demo-system` namespace. The frontend SPA
([krateo-platformops/frontend](https://github.com/krateo-platformops/frontend)) renders
what this chart declares; [snowplow](https://github.com/krateo-platformops/snowplow)
resolves it. Full picture: [docs/index.md](docs/index.md).

## Install

Normally provisioned by the **Krateo installer** (feature `portal`, pinned at `1.6.0`).
Standalone, via the composition model (never a direct `helm install` on a live platform):

```sh
# 1. Register the blueprint — core-provider generates the Portal CRD + controller:
kubectl apply -f compositiondefinition.yaml

# 2. Instantiate it — one Portal CR = one reconciled portal-content release:
kubectl apply -f examples/portal-composition/portal.yaml
```

Details and the local-render loop: [docs/usage.md](docs/usage.md).

## Configure

See [docs/configuration.md](docs/configuration.md). Most used:

| Value | Default | Effect |
|---|---|---|
| `tenant` | `krateo-enterprise` | Display name in the sider tenant chip + page eyebrows. |
| `enableDemoSystemNamespace` | `true` | Creates the `demo-system` namespace + the demo persona's grants in it. |
| `tiers.{common,tenant,admin}` | `""` (release ns) | Opt-in RBAC namespace tiering of page visibility. |

## Examples

- [examples/portal-composition](examples/portal-composition) — register the blueprint
  (`CompositionDefinition`) and instantiate a `Portal` composition with custom values.

## Docs

- [docs/index.md](docs/index.md) — the map (bundle + design records + chart-adjacent corpora)
- [docs/overview.md](docs/overview.md) — what the chart deploys and how it works
- [docs/usage.md](docs/usage.md) — installer path, composition registration, local render
- [docs/configuration.md](docs/configuration.md) — the whole values surface
- [docs/api.md](docs/api.md) — the emitted contract: Portal composition API + AuditRecord CRD
- [docs/examples.md](docs/examples.md) — examples index
- [docs/release.md](docs/release.md) — how a release ships
- [docs/log.md](docs/log.md) — curated history

Authoring rules for chart contributors: [docs/authoring-keyextras.md](docs/authoring-keyextras.md)
(`spec.keyExtras`, CI-enforced), and a RESTAction step that needs a route param is gated on it
rather than given a `//` default — the idiom is in [scripts/lint-ra-paths.py](scripts/lint-ra-paths.py),
which CI runs.

## Develop & release

```sh
# Chart.yaml carries the CHART_VERSION release placeholder — lint/render a substituted copy:
cp -r helm/portal /tmp/portal-render && sed -i 's/CHART_VERSION/0.0.0-dev/g' /tmp/portal-render/Chart.yaml
helm lint /tmp/portal-render && helm template smoke /tmp/portal-render
python3 scripts/lint-keyextras.py   # F6 cache-key declaration gate (does its own tempdir render)
python3 scripts/lint-ra-paths.py    # no RESTAction step dials a half-built path (needs jq)
```

Tag `X.Y.Z` (no `v` prefix) — CI packages `helm/portal/` and publishes to
`oci://ghcr.io/krateo-platformops/charts/portal`. Runbook: [docs/release.md](docs/release.md).
