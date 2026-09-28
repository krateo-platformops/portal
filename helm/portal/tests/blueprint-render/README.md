# blueprint-render jq fixtures

Self-contained jq fixtures for the `blueprint-render` RESTAction — the server-side
helm-render seam behind the Autopilot `previewBlueprint` verb. The RA POSTs the caller's
chart source + values to the cluster-internal helm-render-service (`/render`, via
`endpointRef` — the service is ClusterIP-only and never browser-exposed) and shapes the
result into the drawer contract the frontend already consumes.

Two jq programs ship in the RA, extracted here verbatim:

- **`payload.jq`** — the api-step **request-body builder** (RA `spec.api[0].payload`, minus
  its trailing `| tojson`). Turns `?extras` into the render service's exactly-one-of body:
  `{chart{url[,repo][,version]}}` XOR `{rawTemplates}`, plus `values` and the optional
  `releaseName`/`namespace`. Empty `repo`/`version`/`releaseName`/`namespace` are omitted
  (an empty `repo` would break the render service's repo-base-URL path).
- **`filter.jq`** — the RA `spec.filter`. Shapes the raw `/render` result (the api-step
  output under `.render`, plus `.renderError` from `continueOnError`) into:
  `{ objects:[{apiVersion,kind,name,namespace,yaml}], valuesSchema?, error? }`.

The `input.*.json` files simulate snowplow's RA input for the FILTER: the `render` api-step
output (or the `renderError` transport-failure key) at the top level, plus the request
extras (`chart` / `rawTemplates` / `values`). The `payload-input.*.json` files simulate the
extras for the PAYLOAD builder.

## Run

```sh
# filter cases
for c in chart-success rawtemplates-success service-error transport-error both-sources no-source stubs-success; do
  diff <(jq -S -f filter.jq "input.$c.json") <(jq -S . "expected.$c.json") \
    && echo "$c OK" || echo "$c FAIL"
done

# payload builder cases (compact, the exact bytes the RA POSTs sans | tojson)
for c in chart rawtemplates stubs stubs-empty; do
  diff <(jq -c -f payload.jq "payload-input.$c.json") "expected.payload-$c.json" \
    && echo "payload-$c OK" || echo "payload-$c FAIL"
done
```

## What each case proves

| case | proves |
|------|--------|
| `chart-success` | a remote-chart render → the normalized `objects` list + the chart's `valuesSchema` passed through verbatim; no `error` key |
| `rawtemplates-success` | an inline-draft render → objects list; NO `valuesSchema` key when the response omits one |
| `service-error` | a 200-with-`{error}` body (a bad chart is DATA) → surfaced as `error`, `objects:[]` |
| `transport-error` | a `continueOnError` `renderError` (service unreachable) → surfaced as `error`, `objects:[]` |
| `both-sources` | both `chart` AND `rawTemplates` present → an exactly-one-of `error` (defensive; the client guard denies this first), NO ambiguous render call |
| `no-source` | neither source present → a "no chart source" `error`, `objects:[]` |
| `payload-chart` | remote body: `{chart{url,repo,version}, values, releaseName, namespace}` — empty optionals omitted |
| `payload-rawtemplates` | inline body: `{rawTemplates, values}` — `chart` never emitted, defaulted `values:{}` |
| `payload-stubs` | a preview's `lookupStubs` ride to the render service verbatim, so gated resources render |
| `payload-stubs-empty` | an empty `lookupStubs` array is not sent — the body is the plain `helm template` request |
| `stubs-success` | the service's `lookups` report (which gates a stub answered) passes through beside `objects` |

## Contract notes

- `error` is CONTENT, not a step failure: a bad chart, an ambiguous request, and an
  unreachable service all resolve to `{objects:[], error:"..."}` so the drawer shows the
  string instead of the preview silently vanishing (mirrors the frontend `HelmRenderResult`).
- `valuesSchema` rides ONLY on a successful render that carried one (the chart's
  `values.schema.json`), so the drawer's create-form-preview section is data-driven.
- Exactly-one-of is enforced client-side (`parseBlueprintPreviewArgs`) AND here, so a
  malformed proposal never produces an ambiguous render-service body.
