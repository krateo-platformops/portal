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
| `dangling-ref` | X4 | An `items[].resourceRefId` with no matching `resourcesRefs` entry. Renders three different ways depending on container — silent drop, a dash, or a visible error — and only `Tabs` tells you. |
| `row-nav-placeholder` | P10 | A `rowNavigateTo` placeholder that resolves to neither a column nor a `dataSource` cell. The row stops being clickable with no cursor, no warning and no visual difference. |
| `back-link` | P1 | A `← Back to X` label. Filed four times on four pages with an identical fix each time. |
| `second-breadcrumb` | P27 | A `Breadcrumb` widget, or an eyebrow Paragraph spelling a path (`A / B`): the shell already renders the page's breadcrumb, so either is a second one. A context eyebrow (`Platform · tenant x`) is not a trail and passes. A portal with no shell breadcrumb opts a Breadcrumb CR out with `krateo.io/own-breadcrumb` |
| `autopilot-button` | A4 | An Autopilot entry point (a Button navigating to `?ask=`, computing an `askHref`, or labelled Ask Autopilot) that is not a filled `type: primary` button labelled exactly `Ask Autopilot →` with the `fa-wand-magic-sparkles` icon. Recognised by what the Button does as well as by its label: the first A4 sweep missed a CTA because it matched a shape, not the capability. Where it sits is P26's, as for any header action |
| `button-role` | C26 | A Button labelled as a dismissal (`Cancel`, `Close`, `Dismiss`, `Back`, `Keep editing`) without `intent: dismiss`, or labelled as a deletion (`Delete`, `Remove`, `Discard`, `Destroy`, `Uninstall`) without `danger: true`. Closing is amber, deleting is red, and a CR says which only through its label |
| `emoji` | P15 | Emoji in a title, label or status text. |
| `tag-colour-no-label` | C13 | A `Tag` with a colour and no label — meaning carried by colour alone. |
| `dead-kind` | X11 | A widget kind the frontend no longer resolves — `Panel`, `DataGrid`, `Column`, `TabList`, `NavMenu`, or a removed routing kind. Renders nothing. |
| `legacy-envelope` | X12 | `resourcesRefs` as a bare list instead of `{items: […]}`. The CR does not apply at all. |
| `missing-target` | X13 | A `resourcesRefs` entry naming a widget CR that does not exist in the chart — the deletion hazard. Indexes widget CRs only, so a same-named RESTAction cannot vouch for a deleted Table. |
| `containment` | X5 | A child whose kind is not in its container's declared `allowedResources`. The only enforcement there is: nothing checks the field at runtime. |
| `page-header` | P25 | A page the nav declares that does not open on a `PageHeader`. Reads through a templated `items` rather than exempting it, and reports a page it cannot judge instead of passing it. |
| `section-rhythm` | P9 | a nav-declared page root whose section `gap` is not the one shared step (`middle`, **8px** — both themes apply antd's `compactAlgorithm`, so the label's px is half what antd documents). Judged on the ROOT only: rhythm BETWEEN sections is the page's business, within a section is that section's. A root declaring no `gap` is reported too — inheriting a default is not a decision. Shares `page_roots()` with P25, so the two cannot disagree about what a page is. Opt out with `krateo.io/no-section-rhythm` |
| `root-coverage` | P9+P25 | a `page-*` CR the nav does not reach, so **neither page rule judged it**. Both start from the nav — correct, but it means a page the nav cannot reach is skipped in silence. Rendered with default values the agents pages are gated off, so the nav declares 26 roots while the chart ships 31 and both rules passed over 26 of them without saying so. A hit means the render omitted a values flag (under-covering) or the page is genuinely unreachable |
| `page-discovery-alive` | P0 | page discovery found **zero** page roots, so every rule built on it (P9, P25, root-coverage) is vacuously passing. `page_roots` reads the Menu CR's `widgetData` statically, or a page root's `krateo.io/nav-label` / `krateo.io/nav-path` annotations. When the sidebar's items move to a `widgetDataTemplate` — computed server-side from a cluster listing — the static walk sees nothing, nothing errors, and the suite goes green while checking no pages at all. A lint that silently stops checking is worse than one that fails |
| `colour-vocabulary` | T8 | a widget CR naming a colour outside the palette. `getColorCode` resolves a CR's colour NAME against `tokens.ts` and, on a miss, returns `palette.dark` — near-black — **with no error**, so `color: blu` or a renamed key renders as almost-black text that reads like a styling choice. Accepts both live authoring forms (`color: red` and the legacy `var(--red-color)` alias, emitted per key by `cssVariables`) and ignores `{…}` placeholders, which are the widget's own itemTemplate substitutions. The key list is embedded so the rule works from a chart repo, and `test_lint` asserts it against the real `tokens.ts` |

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
