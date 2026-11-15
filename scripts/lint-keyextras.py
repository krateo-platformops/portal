#!/usr/bin/env python3
"""
lint-keyextras — F6 cache-key declaration lint (issues #26 / #27).

THE RULE (docs: README "Authoring rule: spec.keyExtras"):
  Every widget rendered on a parameterized route MUST declare, in its chart
  template, the request-extras keys that route injects (`/x/{namespace}/{name}`
  -> `keyExtras: [name, namespace]`). A widget that receives extras it does not
  declare is refused by snowplow's post-F6 self-quarantine Put-guard: it serves
  200 but is NEVER cached (cold resolve on every visit). Fails safe, caching
  permanently defeated — the exact shape of incidents PR#21 (chrome) and #26
  (21 structural composition-detail widgets).

WHAT THIS SCRIPT DOES:
  a. Renders the chart with `helm template` (the repo's CHART_VERSION release
     placeholder breaks helm, so the chart is copied to a tempdir and the
     placeholder substituted with 0.0.0-dev there — the working tree is never
     mutated).
  b. Builds the route table from the nav Menu widget (`sidebar-nav`): each
     `items[]` entry with a `path` is a route; `{param}` path segments are the
     extras keys the route injects; the routed page widget is resolved via
     `resourceRefId` (through the Menu's own resourcesRefs) or the
     `page: <slug>` -> `page-<slug>` convention.
  c. Walks each route's widget tree transitively: every `resourcesRefs.items[]`
     entry whose apiVersion is widgets.templates.krateo.io and whose
     (namespace, name) exists in this chart is an edge. (resourcesRefsTemplate
     entries are runtime-computed and target data resources, not the render
     tree — not walked.)
  d. FAIL: a widget reachable from a parameterized route whose spec.keyExtras
     is missing one of that route's param keys.
  e. WARN (chrome rule): widgets reachable from the app-shell Layout or the
     frontend-mounted header widget (`header-context`) that do not declare
     `projects` — the shell scope selector writes ?projects= which rides every
     navigation (the PR#21 dashboard first-nav regression). The nav Menu is a
     walk BOUNDARY here: its resourcesRefs are route bindings (per-route page
     content), not chrome render children.
  f. FAIL (filter-keys rule, root-pages UX build plan §2.4): a page root that
     carries the annotation `krateo.io/filter-keys: "projects,source"` names
     the URL params its filter bar writes. Every URL query param reaches every
     widget on the page as an extra, so every widget reachable from that root
     must declare all of them in spec.keyExtras, or snowplow's F6 guard
     declines every cache Put for it. This rule runs over EVERY chart under
     helm/ whose templates use the annotation (page roots live in the page
     charts, not in the portal chart the route rule reads), and the walk is
     the same same-chart resourcesRefs walk as (c). A widget another chart owns
     (the shell's new-composition Button) is outside the walk and is not
     checked here.

  Exit code: 1 if any FAIL, 0 otherwise (warnings never gate).

USAGE:
  python3 scripts/lint-keyextras.py            # from the repo root (or anywhere)
  CHART_DIR=helm/portal ROUTES_WIDGET=sidebar-nav CHROME_HEADER_WIDGET=header-context \
      python3 scripts/lint-keyextras.py

Requires: helm on PATH, PyYAML.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
HELM_DIR = os.environ.get("HELM_DIR", os.path.join(HERE, "..", "helm"))
CHART_DIR = os.environ.get("CHART_DIR", os.path.join(HELM_DIR, "portal"))
FILTER_KEYS_ANNOTATION = "krateo.io/filter-keys"
ROUTES_WIDGET = os.environ.get("ROUTES_WIDGET", "sidebar-nav")
CHROME_HEADER_WIDGET = os.environ.get("CHROME_HEADER_WIDGET", "header-context")
WIDGET_API_GROUP = "widgets.templates.krateo.io"
PARAM_RE = re.compile(r"\{([A-Za-z0-9_-]+)\}")


def render_chart(chart_dir=None):
    """helm template the chart; substitute the CHART_VERSION placeholder in a
    tempdir copy so the working tree is untouched (restore-after by design)."""
    with tempfile.TemporaryDirectory(prefix="lint-keyextras-") as tmp:
        chart_copy = os.path.join(tmp, "chart")
        shutil.copytree(chart_dir or CHART_DIR, chart_copy)
        chart_yaml = os.path.join(chart_copy, "Chart.yaml")
        with open(chart_yaml) as f:
            text = f.read()
        with open(chart_yaml, "w") as f:
            f.write(text.replace("CHART_VERSION", "0.0.0-dev"))
        out = subprocess.run(
            ["helm", "template", "lint-keyextras", chart_copy,
             "--namespace", "krateo-system"],
            capture_output=True, text=True)
        if out.returncode != 0:
            sys.stderr.write(out.stderr)
            sys.exit(f"FATAL: helm template failed ({out.returncode})")
        return out.stdout


# Learned from the chart's own references at startup (see learn_plurals); the suffix rules below
# are only the fallback for a kind nothing references.
_LEARNED_PLURALS = {}


def learn_plurals(docs):
    """{Kind: plural}, learned from the chart's OWN `resourcesRefs` entries.

    Every ref carries both the plural (`resource`) and the target `name`; where that name belongs
    to exactly one widget kind, the pair is an observation. This replaces guessing as the PRIMARY
    source, because guessing was wrong for two shipped kinds and wrong SILENTLY:

        Switch   -> guessed `switchs`, real plural `switches`
        Progress -> guessed `progress`, real plural `progresses`

    The index is keyed on the derived plural while every lookup comes from a CR's declared
    `resource:`, so a wrong guess does not raise — it just never matches, and the widget drops out
    of every route walk with no diagnostic at all."""
    by_name = {}
    for d in docs:
        if not str(d.get("apiVersion", "")).startswith(WIDGET_API_GROUP):
            continue
        name = (d.get("metadata") or {}).get("name")
        if name:
            by_name.setdefault(name, set()).add(d.get("kind"))
    unique = {n: next(iter(k)) for n, k in by_name.items() if len(k) == 1}

    seen = {}
    for d in docs:
        refs = (spec(d).get("resourcesRefs") or {})
        refs = refs.get("items") if isinstance(refs, dict) else refs
        for r in (refs or []):
            if not isinstance(r, dict):
                continue
            kind = unique.get(r.get("name"))
            if kind and r.get("resource"):
                seen.setdefault(kind, set()).add(r["resource"])
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def plural(kind):
    """Kind -> CRD plural. The chart's own references first; suffix rules only as a fallback."""
    if kind in _LEARNED_PLURALS:
        return _LEARNED_PLURALS[kind]
    k = kind.lower()
    if k.endswith("s"):
        return k
    if k.endswith("x"):
        return k + "es"
    if k.endswith("y"):
        return k[:-1] + "ies"
    return k + "s"


def spec(doc):
    return doc.get("spec") or {}


def key_extras(doc):
    return spec(doc).get("keyExtras") or []


def build_widget_index(docs):
    global _LEARNED_PLURALS
    _LEARNED_PLURALS = learn_plurals(docs)
    """(namespace, name, plural) -> doc — plural-qualified because different
    kinds may share a metadata.name (e.g. Form + Button 'register-cluster')."""
    widgets = {}
    for d in docs:
        if str(d.get("apiVersion", "")).startswith(WIDGET_API_GROUP):
            meta = d.get("metadata") or {}
            key = (meta.get("namespace"), meta.get("name"), plural(d["kind"]))
            if key in widgets:
                print(f"WARN  duplicate widget {key}")
            widgets[key] = d
    return widgets


def build_route_table(menu, widgets):
    """(path, [params], (ns, name)-or-None) for every nav item with a path."""
    mns = menu["metadata"]["namespace"]
    refs = {r.get("id"): r
            for r in (spec(menu).get("resourcesRefs") or {}).get("items", [])}
    routes = []
    for item in (spec(menu).get("widgetData") or {}).get("items", []):
        path = item.get("path")
        if not path:
            continue
        params = PARAM_RE.findall(path)
        target = None
        if item.get("resourceRefId"):
            r = refs.get(item["resourceRefId"])
            if r:
                target = (r.get("namespace"), r.get("name"), r.get("resource"))
        elif item.get("page"):
            target = (mns, f"page-{item['page']}", "flexes")
        else:  # path-derived slug convention (page widgets are Flexes)
            slug = [s for s in path.split("/") if s and not s.startswith("{")]
            if slug:
                target = (mns, f"page-{slug[-1]}", "flexes")
        if target is not None and target not in widgets:
            print(f"WARN  route {path}: target widget {target[1]} not in this "
                  "chart (frontend-rendered or missing) — not walked")
            target = None
        routes.append((path, params, target))
    return routes


def walk(root, widgets, boundaries=frozenset()):
    """Transitive same-chart widget tree via resourcesRefs (render edges).
    Nodes in `boundaries` are visited but their refs are not expanded."""
    seen, stack = set(), [root]
    while stack:
        cur = stack.pop()
        if cur in seen or cur not in widgets:
            continue
        seen.add(cur)
        if cur in boundaries:
            continue
        for r in (spec(widgets[cur]).get("resourcesRefs") or {}).get("items") or []:
            if str(r.get("apiVersion", "")).startswith(WIDGET_API_GROUP):
                stack.append((r.get("namespace"), r.get("name"), r.get("resource")))
    return seen


def filter_key_charts():
    """Every chart under HELM_DIR whose templates use the filter-keys annotation."""
    found = []
    for chart in sorted(os.listdir(HELM_DIR)):
        tdir = os.path.join(HELM_DIR, chart, "templates")
        if not os.path.isfile(os.path.join(HELM_DIR, chart, "Chart.yaml")) or not os.path.isdir(tdir):
            continue
        for root, _, files in os.walk(tdir):
            if any(FILTER_KEYS_ANNOTATION in open(os.path.join(root, f), encoding="utf-8").read()
                   for f in files if f.endswith((".yaml", ".tpl"))):
                found.append(os.path.join(HELM_DIR, chart))
                break
    return found


def filter_key_failures(docs, chart_label):
    """(f): every widget reachable from an annotated page root declares the root's filter keys."""
    widgets = build_widget_index(docs)
    failures = 0
    roots = [(k, d) for k, d in widgets.items()
             if FILTER_KEYS_ANNOTATION in ((d.get("metadata") or {}).get("annotations") or {})]
    for key, doc in sorted(roots, key=lambda kd: kd[0][1]):
        raw = doc["metadata"]["annotations"][FILTER_KEYS_ANNOTATION]
        keys = [k.strip() for k in str(raw).split(",") if k.strip()]
        if not keys:
            failures += 1
            print(f"  FAIL  {chart_label}: {doc['kind']} {key[1]}: {FILTER_KEYS_ANNOTATION} "
                  f"is {raw!r}, which names no key")
            continue
        tree = walk(key, widgets)
        bad = []
        for w in sorted(tree, key=lambda k: k[1]):
            missing = [k for k in keys if k not in key_extras(widgets[w])]
            if missing:
                bad.append((widgets[w]["kind"], w[1], missing, key_extras(widgets[w])))
        print(f"filter-keys {chart_label} page={key[1]} keys={keys} "
              f"widgets={len(tree)} violations={len(bad)}")
        for kind, name, missing, declared in bad:
            failures += 1
            print(f"  FAIL  {kind} {name}: keyExtras={declared} is missing filter "
                  f"key(s) {missing} from {key[1]}'s {FILTER_KEYS_ANNOTATION} - every "
                  "URL param reaches every widget on the page, so snowplow's F6 guard "
                  "will decline every cache Put for it")
    return failures, len(roots)


def main():
    docs = [d for d in yaml.safe_load_all(render_chart()) if d]
    widgets = build_widget_index(docs)

    menus = [(k, d) for k, d in widgets.items()
             if d.get("kind") == "Menu" and k[1] == ROUTES_WIDGET]
    if not menus:
        sys.exit(f"FATAL: nav Menu widget '{ROUTES_WIDGET}' not found in render")
    menu_key, menu_doc = menus[0]
    routes = build_route_table(menu_doc, widgets)

    failures = warnings = 0

    # (d) parameterized-route rule — FAIL
    for path, params, target in routes:
        if not params or target is None:
            continue
        tree = walk(target, widgets)
        bad = []
        for key in sorted(tree, key=lambda k: k[1]):
            missing = [p for p in params if p not in key_extras(widgets[key])]
            if missing:
                bad.append((widgets[key]["kind"], key[1], missing,
                            key_extras(widgets[key])))
        print(f"route {path}  params={params}  page={target[1]}  "
              f"widgets={len(tree)}  violations={len(bad)}")
        for kind, name, missing, declared in bad:
            failures += 1
            print(f"  FAIL  {kind} {name}: keyExtras={declared} is missing "
                  f"route param(s) {missing} — snowplow's F6 guard will decline "
                  "every cache Put for it (uncacheable on every visit)")

    # (e) chrome rule — WARN. The nav Menu is a boundary: its refs bind routes
    # (page content), they are not chrome render children.
    chrome_roots = [k for k, d in widgets.items() if d.get("kind") == "Layout"]
    chrome_roots += [k for k in widgets if k[1] == CHROME_HEADER_WIDGET]
    chrome = set()
    for root in chrome_roots:
        chrome |= walk(root, widgets, boundaries=frozenset([menu_key]))
    for key in sorted(chrome, key=lambda k: k[1]):
        if "projects" not in key_extras(widgets[key]):
            warnings += 1
            print(f"  WARN  {widgets[key]['kind']} {key[1]}: app-shell/header "
                  f"chrome without 'projects' in keyExtras={key_extras(widgets[key])}"
                  " — will guard-decline if the shell scope selector's ?projects= "
                  "reaches it (the PR#21 regression shape)")

    # (f) filter-keys rule — FAIL, over every chart that uses the annotation.
    filtered_pages = 0
    for chart_dir in filter_key_charts():
        label = os.path.basename(os.path.normpath(chart_dir))
        chart_docs = docs if os.path.realpath(chart_dir) == os.path.realpath(CHART_DIR) else \
            [d for d in yaml.safe_load_all(render_chart(chart_dir)) if d]
        f, n = filter_key_failures(chart_docs, label)
        failures += f
        filtered_pages += n

    print(f"\nlint-keyextras: {failures} failure(s), {warnings} warning(s) "
          f"across {sum(1 for _, p, t in routes if p and t)} parameterized "
          f"route(s), {len(widgets)} widgets, {filtered_pages} filtered page(s)")
    if failures:
        print("FAILED — declare the missing keys in the widget's CHART template "
              "(live CR edits are reverted by the composition controller). "
              "See README 'Authoring rule: spec.keyExtras'.")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
