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
| `incidents.observability.krateo.io/v1alpha1` | [incident-controller](https://github.com/krateo-platformops/incident-controller) | `/incidents`, `/incidents/{namespace}/{name}`, the composition page's Incidents strip, `/alerts/{namespace}/{name}`, the alerts summary band, global search | `spec.applied: true` (Apply or "I applied it"), `spec.closed: true` (Close), and DELETE (Discard), each as the clicking user |
| `incidentapplies.observability.krateo.io/v1alpha1` | [incident-controller](https://github.com/krateo-platformops/incident-controller) | `/incidents/{namespace}/{name}` (the Apply runs tab, and whether Run apply shows) | POST of `{spec: {incidentRef: {name}}}` (Run apply), as the clicking user |
| `alerts.observability.krateo.io/v1alpha1` | [alert-provider](https://github.com/krateo-platformops/alert-troubleshooter) | `/alerts`, `/alerts/{namespace}/{name}`, the incident page's alert chip (`status.state`, `status.okSince`) | the alert create/edit/delete forms |

An Incident lives in its Alert's namespace and carries the label
`observability.krateo.io/alert: <alert name>`; the pages join incidents to alerts on that
label (and `spec.alertRef`), never on display names. The incident page reads the apply
state from `status.checks` rather than `spec.applied`, which the controller resets once it
records the `apply` check. Users need `get`/`list` on both resources, and `patch`/`delete`
on `incidents` for the incident page's actions.

**Apply.** When `status.howToFix.applyAction` is one actionable write (verb `patch`, `create` or
`delete`, with apiVersion, resource and name, and a payload object unless it deletes), the
"Review & apply" step offers **Apply** instead of "I applied it". Apply sends that write exactly,
through snowplow `/call` as the clicking user (`patch` as a JSON merge patch, `create` as a POST,
`delete` as a DELETE), then sets `spec.applied: true` on the incident, as ONE confirmed set: the
confirm lists both writes with their target and body, and the incident is marked only if the fix
succeeded. The user needs that verb on the target (for a composition, `patch` on its
`composition.krateo.io` resource in its namespace).

**Run apply.** For a fix that is only a script, the step offers **Run apply** beside "I applied
it", when the user can list `incidentapplies` in the incident's namespace and no run of the
incident is unfinished. It POSTs an IncidentApply named `<incident>-apply-<unix seconds>` (snowplow
`/call` writes the ref's name into `metadata.name`, so `generateName` cannot be used) that names
only the incident; the API server stamps `spec.requestedBy`, and incident-controller runs the
apply script with that user's permissions and records the result in the IncidentApply's status.
The button waits up to 3 minutes for the run's `ApplyFinished` Event and shows its message. The
**Apply runs** tab lists the incident's IncidentApplies (matched on `spec.incidentRef.name`),
newest first, with each run's phase, exit code, message and output. Each run is also an `apply`
check with its exit, which Check history shows; exit 0 moves the incident to Verifying. The user needs `create` on
`incidentapplies` (incident-controller's `krateo-incident-responder`).

## The nightly review's CronJob

`/reviews` reads `batch/v1` CronJob `nightly-review` in the portal namespace (as the caller) and,
when it can, offers **Run review now**: a POST of the Job `kubectl create job
--from=cronjob/nightly-review` creates (the jobTemplate's spec, labels and annotations,
`cronjob.kubernetes.io/instantiate: manual`, the CronJob as controller owner), named
`nightly-review-manual-<unix seconds>`. It needs `get` on cronjobs and `create` on jobs in that
namespace; the nightly-review chart grants both to `admins`.
