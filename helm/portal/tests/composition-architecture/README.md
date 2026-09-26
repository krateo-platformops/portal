# composition-architecture fixtures

Fixtures for a composition's topology on its detail page (`/compositions/<namespace>/<name>`).
A chart that ships `templates/architecture.yaml` (builder-publish is the first) renders a ConfigMap
per composition. The ConfigMap holds the chart's descriptor and a compiled `graph`, with every
node's object names already resolved. The `composition-architecture` RESTAction reads that ConfigMap,
the composition and each object a node names. Every read runs under the caller's own credential:
no `endpointRef`, no `userAccessFilter`. The RESTAction then says which state the composition is in.
Five widgets draw the result: the gate Row, the Topology card's body Row, the Steps strip, the
FlowChart (`variant: architecture`) and "Where it is".

`scripts/test-composition-architecture.py` runs everything here; CI runs it in the
`composition-architecture` job of `.github/workflows/lint.yaml`.

## Files

- **The RESTAction's jq, verbatim from the rendered chart.** The runner fails when a copy drifts
  from the chart.
  - `arch.path.jq`, `arch.filter.jq`: the ConfigMap `<compositionName>-architecture`, kept only
    when CDC's `krateo.io/composition-name` label names this composition and the graph is `v: 1`.
  - `comp.iterator.jq`, `comp.path.jq`, `comp.filter.jq`: the composition itself (zero calls when
    there is no graph), projected to `{uid, managed, conditions}`.
  - `objs.iterator.jq`, `objs.path.jq`, `objs.filter.jq`: every rendered object a present node
    names, at its `status.managed` path. A withheld node is not fetched.
  - `filter.jq`: the final filter.
- **krateo-057 objects, read-only, 2026-09-26.** managedFields are dropped, and so is the objects'
  `spec`, which no program reads.
  - `composition.demo.json` and `objects.demo.json`: `publish-pod-sizing-demo`, finished, 13
    managed objects.
  - `composition.v1b.json` and `objects.v1b.json`: `publish-pod-sizing-v1b`, stuck in S2 on a Repo
    that is NotSynced, 2 managed objects.
  - `composition.frontend-agent.json`: a composition that manages a ConfigMap that is not an
    architecture, for the gate.
- **`cases.json`.** Each case names a composition, the extras it is asked with, and the mutations
  that turn the cluster into what a particular caller meets. `proves` says what the case pins.
- **`expected.<case>.json`**: the RESTAction's output. **`widgets.<case>.json`**: what each widget
  resolves to over it.

The ConfigMap is not a fixture. The runner renders `helm/builder-publish` for each composition the
way CDC renders it: the composition's `spec` as values, plus the `global` block CDC injects, under
the composition's release name. It then stamps the ConfigMap with CDC's post-renderer labels. So the
chart's own output is what the RESTAction reads, and a change to the chart that breaks the page
fails here.

## Run

```sh
python3 scripts/test-composition-architecture.py                          # jq
JQ=/path/to/gojq python3 scripts/test-composition-architecture.py         # snowplow's engine
python3 scripts/test-composition-architecture.py --crds <frontend-crds>   # + widget CR validation
python3 scripts/test-composition-architecture.py --update                 # rewrite expected files
```

`<frontend-crds>` is a chart directory, such as the one
`helm pull oci://ghcr.io/krateo-platformops/charts/frontend-crds --untar` leaves.

For `JQ=gojq`, use a gojq built against `krateo-platformops/gojq v0.13.0`, the fork snowplow 1.12.13
compiles with. A module with `replace github.com/itchyny/gojq => github.com/krateo-platformops/gojq
v0.13.0` and a `main` that calls `cli.Run()` builds it. Upstream gojq is a different engine.

## What each case proves

| case | proves |
|------|--------|
| `demo` | A finished composition: S4 `change-request-open`, all ready, graph alone (no "Where it is"). The ConfigMap and every other managed object are served without TypeMeta, as snowplow's informer serves them, so the join is by name. |
| `v1b` | The live v1b: S2 `seeding`. Repository done (`default_branch · main`). Repo waiting (`targetCommitId · 0 of 1`) with a NotSynced dot. The ten files and the PR are withheld and not fetched. Where it is: Since the Repo's creation, Waiting on Repo, Next LocalResource. |
| `v1b-cdc-extras` | CDC's extras (`compositionName`, `compositionNamespace`, `compositionId`) give exactly the `v1b` output. |
| `v1b-denied-repository` | The Repository is denied by snowplow's own RBAC re-gate, which is a Forbidden string. The rendered Repo proves the Repository was ready at the last render (the chart gates the Repo on it), so the Repository is done, inferred, "not readable with your access". |
| `v1b-denied-current` | The Repo is denied by the apiserver (a 403). snowplow keeps it as plumbing's `response.Status`, which has no `details`, so the object is named from the message. The Repo is unreadable, the level is still known, and Waiting on reads "Repo (not readable with your access)". |
| `v1b-current-deleted` | The Repo is gone (404) while `status.managed` still lists it: waiting, not unreadable. |
| `v1b-current-dropped` | The Repo is served with no error and nothing in it (an informer drop): unavailable, "could not be read". |
| `v1b-current-failed` | The Repo's read fails with a 500 that names no object: unavailable, never "not readable with your access". |
| `demo-denied-two-files` | Two of the ten files are denied. The rendered PullRequest proves all ten were ready, so the files are done, inferred, and the composition is still all ready. |
| `composition-forbidden` | The composition is denied: `readable: false`, every present node `unknown`, no object fetched. The body shows the "not readable" sentence. |
| `configmap-forbidden` | The ConfigMap is denied by the apiserver: `architecture: false, access: forbidden`. The body shows the sentence. |
| `configmap-denied-by-snowplow` | The same denial as snowplow's Forbidden string. That is the shape an informer-served kind gets once snowplow re-gates internal reads (#256, after 1.12.13). |
| `no-configmap` | No ConfigMap (404): `architecture: false, access: null`. No claim about access. |
| `label-mismatch` | A ConfigMap of that name that another composition rendered is not this one's architecture. |
| `no-extras` | No extras: `architecture: false`. |
| `empty-descriptor` | A chart that declares no resources: `nodes: []`, all ready, level null. The body draws nothing. |

The gate cases (in `cases.json`, evaluated over composition-detail's own output):

| gate | items |
|------|-------|
| `v1b-rendered`, `demo-rendered` | The card. CDC records the ConfigMap in `status.managed` once the chart renders it. |
| `v1b-today` | `[]`: 057 before this release, when composition-architecture is never called. |
| `no-architecture` | `[]`: frontend-agent manages a ConfigMap, but not `<name>-architecture`. |
| `not-found` | `[]`: composition-detail cannot resolve the composition for this caller. |

Every case is resolved twice, in both iterator orders, and must give one answer. snowplow runs
iterator items concurrently, so the order in which objects and errors accumulate is not fixed.

## Contract notes

- **A denial is content.** A denied object reads `unreadable`, never "not ready", and is never
  re-read under another identity.
  - A denied node that a rendered dependent proves ready is `done`, `inferred`. This is gate parity:
    the chart rendered the dependent only because the dependency was ready.
  - A node whose readiness is existence, meaning it has no `readyWhen`, counts as ready when
    `status.managed` lists it, unless a read says it is gone (404).
- **Where the denial comes from.** snowplow 1.12.13 can serve a denied GET under its
  ServiceAccount through its internal REST config. #256 closes that, and is merged but not in a tag.
  Until a snowplow with #256 is deployed, a caller may see a node's real state where these fixtures
  expect `unreadable`. The two ConfigMap rows are plain 403s either way.
- **The gate reads composition-detail, not this RESTAction.** A 404 in a RESTAction stage is a stage
  error. snowplow serves the result, declines to cache it and WARNs, and the gate avoids that on
  every view of a composition without an architecture. Existence is known before the ConfigMap is
  read, so "not readable with your access" is said only when it is true.
- **Names, not kinds.** Paths come from `status.managed`, never from a pluralised kind (`Repo` is
  `repoes`). Objects join by name, because informer-served objects have no TypeMeta. The composer's
  lint L5 keeps names unique across nodes.
