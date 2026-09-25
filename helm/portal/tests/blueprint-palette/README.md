# blueprint-palette jq fixtures

Self-contained jq fixtures for the `blueprint-palette` RESTAction: the kinds a person may
place on the Blueprint Composer's canvas, read as that person. The composer fetches the RA
through snowplow `/call` with the caller's Bearer. Every step is a plain apiserver call with
no `endpointRef` and no `userAccessFilter`, so snowplow runs it under the caller's own
credential.

Every jq program in the RA is extracted here verbatim from the helm-rendered
`restaction.blueprint-palette.yaml`:

- **`step.crds.jq`** projects the CRD list, which is about 20 MB on krateo-057, down to
  `{group, kind, plural, version, scope, owner, statusFields, conditions}`. It drops Krateo's
  own groups (`composition.krateo.io`, `widgets.templates.krateo.io`, `templates.krateo.io`,
  `core.krateo.io`). It picks the version that is served and stored, and otherwise the first
  served one.
- **`step.compdefs.jq`** keeps every CompositionDefinition whose CRD has been generated, as
  `{name, namespace, kind, apiVersion (served), plural, chartVersion, projects}`.
- **`step.running.jq`** reduces one kind's instance list to its
  `krateo.io/composition-definition-{name,namespace}` labels. The RA runs it once per
  definition, and snowplow splices the arrays together.
- **`step.mayListCrds.jq` / `step.mayListCompdefs.jq`** shape a SelfSubjectAccessReview
  answer into `{allowed, evaluated}`.
- **`filter.jq`** is the RA `spec.filter`. It folds everything into
  `{custom, compositions, runningPartial}`, where each class is either its list or
  `{error: {code, reason, message}}`.

The `step-input.*.json` files hold what snowplow puts under the step's own key before the
step filter runs. The `input.*.json` files hold the dict the top-level filter sees: each
step's output under its name, plus any `errorKey` lists. The `*.057` step inputs and
`input.admin.json` are slices of krateo-057, taken on 2026-09-26 and reduced to the fields
the filters read. The `*.synthetic` inputs cover shapes 057 does not have, and say so
below.

## Run

```sh
for c in admin crds-403 crds-string-error compdefs-403 compdefs-refused probe-failed running-partial empty; do
  diff <(jq -S -f filter.jq "input.$c.json") <(jq -S . "expected.$c.json") \
    && echo "$c OK" || echo "$c FAIL"
done

for c in crds.057 crds.synthetic compdefs.057 compdefs.synthetic running.057 \
         mayListCrds.allowed mayListCompdefs.allowed mayListCompdefs.denied; do
  diff <(jq -S -f "step.${c%%.*}.jq" "step-input.$c.json") <(jq -S . "step-expected.$c.json") \
    && echo "step $c OK" || echo "step $c FAIL"
done
```

## What each case proves

| case | proves |
|------|--------|
| `step crds.057` | Krateo's four groups are dropped. `agents.kagent.dev` resolves to its served-and-stored `v1alpha2`. `capacityrequests` skips an unserved, unstored version. `owner` comes from the KOG annotation (`krateo-system/github-provider-kog-repository`), else from the `krateo.io/composition-name` label (`kagent-crds`, `portal`), else `null`. A kind with no status schema gets `statusFields: []`. A Cluster-scoped kind stays in the list, marked with its `scope`. |
| `step crds.synthetic` | *(synthetic)* A kind that stores an unserved version gets its served one (`vacuum` is skipped for `v1-2-0`). A kind that serves nothing is dropped. |
| `step compdefs.057` | `apiVersion` is the version the definition **serves** (`v1-8-40`), not the CRD's storage version. `chartVersion` and `projects` are included. |
| `step compdefs.synthetic` | *(synthetic)* A definition whose CRD is not generated yet is dropped. `projects` lists `statusDataTemplate[].forPath`, which none of 057's 46 definitions set. |
| `step running.057` | 23 real BuilderPublish instances become 23 `{cd, cdNs}` label pairs. |
| `step mayList*` | A SelfSubjectAccessReview answer becomes `{allowed, evaluated: true}`. |
| `admin` | Both classes are lists. Counts are joined by label: `builder-publish` has 23 running, `mongodb` 2, `portal` 1. |
| `crds-403` | A real apiserver refusal on the CRD list makes `custom` `{error}` carrying the Status's own `code`, `reason` and `message`. |
| `crds-string-error` | An error that snowplow records as a bare string (an in-process dispatch) still yields `{code: 0, reason: "", message}` and never breaks the filter. |
| `compdefs-403` | The cyberjoker shape when the apiserver answers: `compositions` is the list's own 403. |
| `compdefs-refused` | The cyberjoker shape on a snowplow with its cache on. The list comes back narrowed, here to empty, because cyberjoker may list definitions only in demo-system. The probe says `allowed: false`, so `compositions` is a 403 and never "none installed". |
| `probe-failed` | Both probes failed (a 500), so they claim nothing, and each class is whatever its list returned. |
| `running-partial` | One kind's instance list failed. Its count reads 0 and `runningPartial` is `true`, so the composer shows no count rather than a false 0. |
| `empty` | Nothing is installed and both probes allow. The result is empty lists, not errors. |

## Contract notes

- **A denial is content.** With `CACHE_ENABLED`, snowplow serves a collection GET from its
  informer and narrows it to what the caller may list (`filterListByRBAC`). A caller with no
  cluster-wide list right therefore gets an empty list where the apiserver would have
  answered 403. The two `mayList*` steps ask the apiserver the same question as a
  SelfSubjectAccessReview, and a `no` makes that class a 403.
- **The error record** comes first from the list's own `errorKey` (the first entry of
  snowplow's accumulated list), and then from the probe. A real apiserver Status is always
  preferred over the synthesized one.
- **The counts** are what the caller may list of each kind. They are joined by label, never
  by `.kind`.
