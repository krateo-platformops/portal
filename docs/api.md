---
type: API
title: portal — api
description: The contract the portal chart emits — the derived Portal composition API, the AuditRecord CRD it defines, the CR kinds it instantiates whose CRDs live elsewhere, and the observability APIs the incident pages read and write.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, composition, crd, api]
timestamp: 2026-09-25T00:00:00Z
---

# API

This chart runs no server and exposes no HTTP endpoint. Its API surface is what the
platform **derives from it** and the one CRD it **defines**.

## The derived composition API (the primary contract)

Registering the published chart via a `CompositionDefinition`
([`compositiondefinition.yaml`](../compositiondefinition.yaml)) makes core-provider
generate, from `values.schema.json`:

- **Kind** `Portal`, group `composition.krateo.io`, version **`v1-6-0`** (the chart
  version with dots→dashes — the API version *is* the chart version).
- A namespaced CRD whose `spec` is exactly the values surface documented in
  [configuration](./configuration.md).
- A composition-dynamic-controller that reconciles each `Portal` CR as one Helm
  release of this chart.

```yaml
apiVersion: composition.krateo.io/v1-6-0
kind: Portal
metadata:
  name: portal
  namespace: krateo-system
spec:
  tenant: krateo-enterprise
  enableDemoSystemNamespace: true
```

A chart-version bump therefore changes the served API version; the platform (cdc
≥ 1.3.3) converts existing instances in place on definition upgrade.

## The CRD this chart defines: `AuditRecord`

[`crd.auditrecords.yaml`](../helm/portal/templates/crd.auditrecords.yaml) ships
`auditrecords.audit.krateo.io` (`v1alpha1`, namespaced, shortName `ar`) — the
data-plane audit trail for **gated portal mutations**: who acted (human or agent,
with agent identity + session), what was asked (the prompt), the exact Kubernetes
write (`spec.action`: verb + GVR + target), the blast-radius shown at the gate, and
the outcome (`spec.outcome.ok`). Strongly typed end to end (no
`x-kubernetes-preserve-unknown-fields`). Printer columns: Actor, Verb, OK, Age.
Producers write these records; no portal page consumes them yet.

## CR kinds instantiated (contracts owned elsewhere)

The other ~530 rendered objects are *instances* of APIs owned by peer components —
this chart consumes those contracts, it does not define them:

| API group | Kinds used | CRD owner |
|---|---|---|
| `widgets.templates.krateo.io/v1beta1` | Layout, Menu, Theme, Flex, Card, Table, Listy, Button, Form, Paragraph, Row, Col, Tabs, Steps, Statistic, Tag, Markdown, Descriptions, Select, Input, LineChart, YamlViewer, RangePicker, Image, Divider, Alert | [frontend](https://github.com/krateo-platformops/frontend) |
| `templates.krateo.io/v1` | RESTAction (×45) | [snowplow](https://github.com/krateo-platformops/snowplow) |
| `basic.authn.krateo.io/v1alpha1` | User (×2) | [authn](https://github.com/krateo-platformops/authn) |
| `rbac.authorization.k8s.io/v1`, `v1` | Roles/Bindings, Namespace, Secrets | Kubernetes |

The RESTActions additionally *call* APIs at resolve time: the cluster's own
apiserver (compositions, compositiondefinitions, events, pods…), the ClickHouse HTTP
interface (observability metrics), the helm-render-service (`/render`, the
blueprint-preview seam) and the marketplace catalog index — each through an
`Endpoint` Secret or an in-cluster path, always under the calling user's RBAC.

## Observability APIs the incident and alert pages read

| API | Owner | Read by | Written by the portal |
|---|---|---|---|
| `incidents.observability.krateo.io/v1alpha1` | [incident-controller](https://github.com/krateo-platformops/incident-controller) | `/incidents`, `/incidents/{namespace}/{name}`, the composition page's Incidents strip, `/alerts/{namespace}/{name}`, the alerts summary band, global search | `spec.applied: true` ("I applied it"), `spec.closed: true` (Close), and DELETE (Discard), each as the clicking user |
| `alerts.observability.krateo.io/v1alpha1` | [alert-troubleshooter](https://github.com/krateo-platformops/alert-troubleshooter) | `/alerts`, `/alerts/{namespace}/{name}` (for an apiRef alert, `spec.apiRef` and `status.value` against the threshold, in place of the HyperDX walk), the incident page's alert chip (`status.state`, `status.okSince`) | the alert create/edit/delete forms |

An Incident lives in its Alert's namespace and carries the label
`observability.krateo.io/alert: <alert name>`; the pages join incidents to alerts on that
label (and `spec.alertRef`), never on display names. The incident page reads the apply
state from `status.checks` rather than `spec.applied`, which the controller resets once it
records the `apply` check. Users need `get`/`list` on both resources, and `patch`/`delete`
on `incidents` for the incident page's actions.
