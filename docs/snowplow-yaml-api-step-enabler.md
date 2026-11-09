---
type: Decision
title: "Design — snowplow RESTAction YAML api-step (serve a fetched YAML body as JSON to jq)"
description: "The enabler design that let a RESTAction api step consume YAML endpoints (Helm index.yaml) — implemented in snowplow (external fetch + YAML→JSON, >= 1.4.0) and consumed by this chart's blueprints-catalog."
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [snowplow, restaction, design]
status: implemented
timestamp: 2026-08-07T00:00:00Z
---

# Design — snowplow RESTAction "YAML api-step" (serve a fetched YAML body as JSON to jq)

> **Implementation reality (verified):** shipped in snowplow — the external
> `endpointRef` fetch converts a YAML response body to JSON before the jq filter runs
> (snowplow's own record: ADR 0006 "snowplow-owned external fetch (YAML acceptance)",
> `external_fetch.go`; required version ≥ 1.4.0). This chart consumes it in
> [`restaction.blueprints-catalog`](../helm/marketplace-page/templates/restaction.blueprints-catalog.yaml),
> which relies on content-type-based acceptance (`Accept: application/x-yaml, text/yaml,
> application/json` headers) rather than a per-step `yaml: true` flag. The consumer
> design is [marketplace-registry-discovery](./marketplace-registry-discovery.md).

> Original status line: ready for implementation. Repo: **snowplow** (`internal/resolvers/restactions/api`).
> Companion: `docs/marketplace-registry-discovery.md` (the consumer; this is
> its enabler #1). This doc is self-contained — a snowplow session can implement from it alone.

## 1. Why

The portal's Marketplace is being reworked to **discover** installable blueprint charts from
external sources via a RESTAction. The Helm-repo path has the discovery RA GET a repo's
`index.yaml` and jq-filter the charts whose `keywords` contain `blueprint`, reading each chart's
`icon` from the same entry:

```yaml
spec:
  api:
    - name: index
      endpointRef: { name: charts-repo, namespace: krateo-system }   # auth (private repos) via the Endpoint
      path: /index.yaml
      verb: GET
  filter: '[ .index.entries | to_entries[] | .value[0]
             | select((.keywords // []) | index("blueprint"))
             | { name: .name, icon: .icon, version: .version } ]'
```

**Blocker:** `index.yaml` is **YAML**, but a RESTAction api step feeds its response to jq via
`json.Unmarshal`, so a YAML body can't be queried. This is the one snowplow change that unblocks
RA-driven discovery — and, generally, lets a RESTAction consume **any** YAML endpoint (Helm
`index.yaml`, a raw `Chart.yaml`, any `*.yaml`).

## 2. The change

Let an api step opt into **YAML→JSON conversion of its response body** before it is unmarshalled
for jq.

1. **Spec field.** Add `YAML bool` (`json:"yaml"` / `yaml:"yaml"`) to the api-step struct — the
   `spec.api[]` item where `path` / `verb` / `filter` / `dependsOn` / `endpointRef` are defined.
2. **Conversion hook.** In `internal/resolvers/restactions/api/apistage.go`, **immediately before**
   the response is unmarshalled for jq (`json.Unmarshal(raw, &v)` at **apistage.go:110**; the
   envelope/object unmarshals at **:146 / :290** apply the same way), convert when the step opts in
   (or when the response `Content-Type` is YAML):

   ```go
   // A step may declare `yaml: true` (or we sniff a YAML Content-Type): convert the body to JSON
   // before unmarshalling — reusing the same sigs.k8s.io/yaml that internal/handlers/convert.go uses.
   if step.YAML || isYAMLContentType(resp.Header.Get("Content-Type")) {
       if j, err := yaml.YAMLToJSON(raw); err == nil {
           raw = j
       }
       // (on error: leave raw as-is so the existing Unmarshal error path / continueOnError handles it)
   }
   if err := json.Unmarshal(raw, &v); err != nil { /* … existing … */ }
   ```

3. **Reuse, no new dep.** `yaml.YAMLToJSON` is already used by `internal/handlers/convert.go` (the
   `/convert` endpoint, line ~59) via `sigs.k8s.io/yaml`. Same import.
4. **Content-type sniff (recommended).** `isYAMLContentType(ct)` = `strings.Contains(ct,
   "application/x-yaml") || strings.Contains(ct, "text/yaml")` — mirrors convert.go. The explicit
   `yaml: true` flag still matters because **many Helm repos serve `index.yaml` as `text/plain`**,
   so sniffing alone is unreliable; the flag is authoritative.

## 3. Worked example (what the RA author sees vs. what jq receives)

**Response body (YAML the step fetches):**
```yaml
apiVersion: v1
entries:
  aws-eks-stack:
    - name: aws-eks-stack
      version: "0.3.0"
      icon: https://example/icon.svg
      keywords: [aws, eks, kubernetes, blueprint]
      urls: [oci://ghcr.io/example/charts/aws-eks-stack]
  prometheus:
    - { name: prometheus, version: "25.0.0", keywords: [monitoring] }
```
**After the hook** → jq sees the equivalent JSON, so the `select((.keywords // []) |
index("blueprint"))` filter yields:
```json
[{ "name": "aws-eks-stack", "version": "0.3.0", "icon": "https://example/icon.svg",
   "keywords": ["aws","eks","kubernetes","blueprint"] }]
```

## 4. Scope / non-goals

- Converts **only the api-step response body**. Request bodies, `/call` writes, and other paths are
  untouched.
- Does **not** pull OCI/tgz charts or untar archives (that is the chart-inspector's job — a separate
  enabler in the companion doc).
- `continueOnError` semantics are preserved: a malformed YAML → conversion left as-is → the existing
  `json.Unmarshal` error path fires → honored per `continueOnError`/`errorKey`.
- Private repos are already handled upstream: the Endpoint / `credentials` ride through `httpcall`;
  this change only touches body decoding.

## 5. Testing

- **Unit** (`apistage` test): a step with `yaml: true` whose response is a YAML doc → the resulting
  jq input dict equals the parsed structure; with `yaml:false` and a YAML body → unchanged
  (Unmarshal error) — proving opt-in.
- **Content-type sniff**: a `text/yaml` response with no flag → still converted.
- **Integration**: a RESTAction GET-ing a real Helm `index.yaml` with `yaml: true`, final filter
  selecting `keywords` containing `blueprint` → returns the expected entries.

## 6. Touch list

- `internal/resolvers/restactions/api/apistage.go` — the conversion before the response
  unmarshal(s) (`:110`, and `:146` / `:290` if those code paths also feed jq).
- the api-step spec struct (`spec.api[]` item type) — add `YAML bool`.
- `internal/handlers/convert.go` — reference for the existing `yaml.YAMLToJSON` usage (no change).
