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
  that turn the cluster into what a particular caller meets. A case may also patch the composition's
  `spec` and list objects its chart has not rendered for those values (`unmanaged`); the ConfigMap is
  then rendered from the patched spec. `proves` says what the case pins. A `deny` names the shape
  snowplow records the denial in: `dispatch` (internal dispatch's Forbidden string, in cluster, #256)
  or `httpcall` (plumbing's `response.Status`, the httpcall fall-through, out of cluster).
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
| `demo` | A finished composition: S4 `change-request-open`, all ready, graph alone (no "Where it is"), and no step active. The ConfigMap and every other managed object are served without TypeMeta, as snowplow's informer serves them, so the join is by name. Node names drop the composition's prefix (`…-repo`, `…-000 … 009`). |
| `v1b` | The live v1b: S2 `seeding`. Repository done (`default_branch · main`). Repo waiting (`targetCommitId · 0 of 1`) with a NotSynced dot. The ten files and the PR are withheld and not fetched. Where it is, under the graph: Since the Repo's creation, Waiting on Repo, Next LocalResource. |
| `v1b-cdc-extras` | CDC's extras (`compositionName`, `compositionNamespace`, `compositionId`) give exactly the `v1b` output. |
| `v1b-denied-repository` | The Repository is denied: the in-cluster shape with #256 (internal dispatch's Forbidden string). The rendered Repo proves the Repository was ready at the last render (the chart gates the Repo on it), so the Repository is done, inferred, "not readable with your access". |
| `v1b-denied-current` | The Repo is denied as plumbing's `response.Status` (snowplow's httpcall path, out of cluster), which has no `details`, so the object is named from the message. The Repo is unreadable, the level is still known, and Waiting on reads "Repo (not readable with your access)". |
| `v1b-current-deleted` | The Repo is gone (404) while `status.managed` still lists it: waiting, not unreadable. |
| `v1b-current-dropped` | The Repo is served with no error and nothing in it (an informer drop): unavailable, "could not be read". |
| `v1b-current-failed` | The Repo's read fails with a 500 that names no object: unavailable, never "not readable with your access". |
| `demo-denied-two-files` | Two of the ten files are denied (as httpcall's `response.Status`). The rendered PullRequest proves all ten were ready, so the files are done, inferred, the composition is still all ready, and the node reads "2 of 10 not readable with your access". |
| `demo-denied-all-files` | All ten files are denied (the in-cluster shape with #256). Still done, inferred; a node denied in full keeps the plain "not readable with your access". |
| `composition-forbidden` | The composition is denied (as httpcall's `response.Status`): `readable: false`, every present node `unknown`, no object fetched. The body says the topology is not readable and names the composition's kind as what to grant, not its ConfigMaps. |
| `composition-failed` | The composition's read fails with a 500: `access: error`. The body says only that the topology could not be read. |
| `composition-version-moved` | The composition's CRD now serves a newer version, and the ConfigMap names the old one until CDC renders it again: a 404, `access: error`, the neutral sentence. |
| `configmap-forbidden` | The ConfigMap is denied as plumbing's `response.Status` (snowplow's httpcall path, out of cluster): `architecture: false, access: forbidden`. The body says it is not readable. |
| `configmap-denied-by-snowplow` | The same denial, in the in-cluster shape with #256 (internal dispatch's Forbidden string). |
| `configmap-failed` | The ConfigMap's read fails with a 500: `access: error`, the neutral sentence. |
| `no-configmap` | No ConfigMap (404) while `status.managed` lists it: `architecture: false, access: null`. No claim about access: the neutral sentence. |
| `label-mismatch` | A ConfigMap of that name that another composition rendered is not this one's architecture, and not a denial. |
| `configmap-no-graph` | A ConfigMap with no graph (the frontend <= 1.6.59 composer wrote `<release>-architecture` without one): no architecture, not a denial. |
| `graph-v2` | A graph shape this portal does not know (`v: 2`): no architecture, and no one, admins included, is told their access is the problem. |
| `no-extras` | No extras: `architecture: false`, not a denial. |
| `empty-descriptor` | A chart that declares no resources: `nodes: []`, all ready, level null. The body says so in one sentence rather than leaving an empty titled card. |
| `demo-no-pull-request` | `pullRequest.create=false` (stop at the pushed branch): the PullRequest is absent, the composition is all ready at S3 `committing`, and the strip draws S1–S3 only, never a change request that does not exist. |
| `v1b-no-source` | No source to seed from: the Repo is absent, so the strip draws S1, S3, S4, each with its level's own number, and `current` points at S3 among the steps drawn. |

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
- **Where the denial comes from.** An in-cluster /call carries snowplow's ServiceAccount REST
  config, and on snowplow 1.12.13 the internal dispatch serves every apiserver GET with it, whatever
  the caller may read. So on 1.12.13 no denial row holds. A caller denied the ConfigMap or the
  composition gets `architecture: true`, the full graph and every node's real state. #256 (merged,
  not in a tag) re-gates those reads with the caller's RBAC and returns snowplow's Forbidden string.
  Every denial row here needs a snowplow with #256. A `response.Status` arrives only when snowplow
  falls through to plumbing's httpcall (out of cluster).
- **"Not readable with your access" only for `access: forbidden`.** The gate reads
  composition-detail, not this RESTAction: a 404 in a RESTAction stage is a stage error, which
  snowplow serves but declines to cache, with a WARN, on every view of a composition without an
  architecture. The gate proves the ConfigMap exists, but not that a failed read was a denial. So
  the body says "not readable with your access" only when the RESTAction reports `access: forbidden`,
  and names what was denied (the ConfigMap, or the composition). A failed read, a missing or foreign
  ConfigMap, or a graph shape this RESTAction does not know says only that the topology could not be
  read.
- **Names, not kinds.** Paths come from `status.managed`, never from a pluralised kind (`Repo` is
  `repoes`). Objects join by name, because informer-served objects have no TypeMeta. The composer's
  lint L5 keeps names unique across nodes.
