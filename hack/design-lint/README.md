# design-lint (vendored)

Static checks for the design system's composition rules, run against this chart's widget CRs in CI.

**Canonical source:** [`krateo-platformops/frontend`](https://github.com/krateo-platformops/frontend)
`design/lint/`. The rules themselves are documented there, in `design/03-composition.md` and
`design/04-silent-failures.md`.

## Why the rules live there and the check runs here

That repo defines the widget vocabulary — tokens, components, schemas, CRDs — and **28 charts
consume `widgets.templates.krateo.io`**. A design system living in any one consuming chart would
govern one of twenty-eight.

But CR authors work *here*, and will not read a document in another repo. The check closes that
gap: the rules arrive as a failing CI check with a link, at the moment they matter.

## Why vendored rather than fetched

This repo already vendors its scripts (`hack/preflight-refs.py`). A cross-repo fetch would mean CI
here breaks whenever the frontend lands a new rule — hostile to a different team — and a lint that
needs the network is a lint that fails on a bad morning.

**The cost is drift.** If the rules change upstream, re-copy the script, its `fixtures/` and its
`test_lint.py` **together** — the self-test is what proves the copy still works.

## What it checks

| Rule | ID | Catches |
|---|---|---|
| `dead-kind` | X11 | a widget kind the frontend no longer resolves (`Panel`, `DataGrid`, `TabList`, …) |
| `legacy-envelope` | X12 | `resourcesRefs` as a bare list — the CR will not apply |
| `dangling-ref` | X4 | an `items[].resourceRefId` with no matching `resourcesRefs` entry |
| `row-nav-placeholder` | P10 | a `rowNavigateTo` placeholder that resolves to nothing — the row goes silently inert |
| `back-link` | P1 | a `← Back to X` label; the breadcrumb is the one way back |
| `emoji` | P15 | emoji in a title, label or status text |
| `tag-colour-no-label` | C13 | a `Tag` with a colour and no label — meaning carried by colour alone |
| `missing-target` | X13 | a `resourcesRefs` entry naming a CR that does not exist — the parent renders without it |

Current state: **0 violations**. These are regression guards, not a backlog.

`missing-target` is the **deletion hazard**, and it matters most right now: the PageHeader
migration removes 3–6 CRs per page across a dozen pages. It found a real defect on its first run —
#149 had shipped the alert-detail pipeline walk with its RESTAction and page reference but without
the Card and Markdown that render it, and nothing complained, because a dangling reference is not a
render error.

It discovers kind→plural from real CRDs rather than a hardcoded table (a table would have gone
stale the day `PageHeader` was added), and its primary check is plural-independent, so it still
works here where no CRDs are reachable.

```bash
python3 hack/design-lint/lint-portal-consistency.py helm/portal
python3 hack/design-lint/test_lint.py     # every rule must still fire, and still stay quiet
```
