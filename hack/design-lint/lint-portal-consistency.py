#!/usr/bin/env python3
"""
lint-portal-consistency — static checks for the design system's composition rules.

VENDORED from krateo-platformops/frontend `design/lint/`, which is the canonical source and where
the rules themselves live (`design/03-composition.md`, `design/04-silent-failures.md`).

Why a copy rather than a fetch: this repo already vendors its scripts (`hack/preflight-refs.py`),
CI here should not break because another repo landed a new rule, and a lint that needs the network
is a lint that fails on a bad morning. The cost is that this file can drift from upstream — if the
rules change there, re-copy the script, its fixtures and its self-test together.

VENDORED AT: krateo-platformops/frontend 1.7.0 (design/lint/). This stamp exists because the copy
HAD drifted and nothing noticed — it was missing rule_containment (X5) entirely, so that gate never
ran here after it landed upstream. The self-test now fails when a registered rule is undocumented,
which catches one shape of that drift; nothing catches the rest.

Ships from the frontend repo (which defines the widget vocabulary) and runs in a consuming
chart's CI, so the rules arrive where CRs are actually written: as a failing check with a link.

SCOPE DISCIPLINE. Every check here is decidable from the CR tree alone, with no guessing about
what a `widgetDataTemplate` will emit at resolve time. That boundary is not fussiness — a previous
attempt at a composition lint produced 23 false positives against 1 real defect and was deleted,
taking its signal with it. A check that cannot be made precise is left out rather than shipped
noisy; the measured false-positive rate for each rule below is recorded in the design system.

Usage:
    lint-portal-consistency.py <chart-dir|rendered-dir|file.yaml> [--rule R1,R2] [--quiet]

Exit code is the number of violations (0 = clean), so CI fails on any.
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

# A chart is RENDERED rather than text-substituted. Substituting `{{ … }}` inline looks simpler
# and silently loses every file using a Helm control block: measured against the portal chart it
# left 36 of 618 files unparseable — 6% of the tree, including the nav Menu that defines the route
# table. A lint with a silent 6% blind spot reports "clean" for a defect it never looked at.

# Emoji ranges (pictographs, symbols, dingbats, flags). Deliberately NOT matching every symbol —
# an arrow or a middot in a label is typography, not decoration.
EMOJI = re.compile(
    '[\U0001F300-\U0001FAFF\U0001F000-\U0001F0FF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]'
)

BACK_LINK = re.compile(r'^\s*[←<]?\s*back\s+to\b', re.I)


def render_chart(chart_dir):
    """`helm template` the chart into a list of documents.

    The chart carries a CHART_VERSION release placeholder that helm rejects as an invalid semver,
    so it is copied to a tempdir and the placeholder substituted there — the working tree is never
    mutated. (Same approach as lint-keyextras.py, for the same reason.)"""
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(chart_dir, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text.replace('CHART_VERSION', '0.0.0-dev'))
        proc = subprocess.run(
            ['helm', 'template', 'lint', staged],
            capture_output=True, text=True, check=False,
        )
        if proc.returncode != 0:
            raise SystemExit(f'helm template failed:\n{proc.stderr.strip()}')
        return proc.stdout


def load_crs(target):
    """Every widget CR, as (source, doc) pairs.

    `target` is a chart directory (rendered with helm) or a directory of already-rendered YAML.
    Unparseable documents are reported rather than skipped — a file the lint cannot read is a gap
    in coverage, not a pass, and staying silent about it is how a lint comes to report "clean"."""
    crs, unreadable = [], []

    if os.path.isfile(target):
        try:
            for doc in yaml.safe_load_all(open(target, encoding='utf-8').read()):
                if isinstance(doc, dict) and doc.get('kind'):
                    crs.append((os.path.basename(target), doc))
        except yaml.YAMLError as exc:
            unreadable.append((os.path.basename(target), str(exc).split('\n')[0]))
        return crs, unreadable

    if os.path.isfile(os.path.join(target, 'Chart.yaml')):
        stream = render_chart(target)
        # helm prefixes each document with `# Source: <chart>/templates/<file>`
        current = 'unknown'
        for chunk in stream.split('\n---\n'):
            match = re.search(r'#\s*Source:\s*\S*templates/(\S+)', chunk)
            if match:
                current = match.group(1)
            try:
                for doc in yaml.safe_load_all(chunk):
                    if isinstance(doc, dict) and doc.get('kind'):
                        crs.append((current, doc))
            except yaml.YAMLError as exc:
                unreadable.append((current, str(exc).split('\n')[0]))
        return crs, unreadable

    for path in sorted(glob.glob(os.path.join(target, '*.yaml'))):
        try:
            for doc in yaml.safe_load_all(open(path, encoding='utf-8').read()):
                if isinstance(doc, dict) and doc.get('kind'):
                    crs.append((os.path.basename(path), doc))
        except yaml.YAMLError as exc:
            unreadable.append((os.path.basename(path), str(exc).split('\n')[0]))
    return crs, unreadable


def widget_data(doc):
    return ((doc.get('spec') or {}).get('widgetData') or {})


def declared_refs(doc):
    """The ids in `resourcesRefs`, tolerating the legacy bare-list shape.

    The current CRD declares `resourcesRefs` as an object (`{items, slice}`), but pre-migration
    charts carry a bare list. Crashing on those was this script's own bug: a lint that dies on the
    charts most likely to be stale is a lint that never reports on them. The legacy shape is
    surfaced by `legacy-envelope` instead."""
    refs = (doc.get('spec') or {}).get('resourcesRefs')
    if isinstance(refs, dict):
        refs = refs.get('items') or []
    if not isinstance(refs, list):
        return set(), False
    return {r.get('id') for r in refs if isinstance(r, dict)}, isinstance((doc.get('spec') or {}).get('resourcesRefs'), list)


def templated_paths(doc):
    """The `forPath` values a widgetDataTemplate will fill at resolve time. A field listed here is
    NOT absent — it is computed — so no check may treat it as missing."""
    tpl = (doc.get('spec') or {}).get('widgetDataTemplate') or []
    return {entry.get('forPath') for entry in tpl if isinstance(entry, dict)}


def walk_strings(node, path=''):
    """Every string in a nested structure, with its dotted path."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk_strings(value, f'{path}.{key}' if path else key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk_strings(value, f'{path}[{i}]')
    elif isinstance(node, str):
        yield path, node


# ---------------------------------------------------------------------------------------------
# Rules. Each returns a list of (file, message).
# ---------------------------------------------------------------------------------------------

def template_ref_ids(doc):
    """`resourceRefId` LITERALS appearing inside jq template expressions.

    A CR whose `items` are assembled by jq still names most of its children as literals in the
    expression — `[ { resourceRefId: "x" } ] + (if .flag then [ { resourceRefId: "y" } ] else [] end)`.
    X4 and X5 skipped these CRs whole, on the grounds that a resolve-time list "is not knowable
    here". Only the COMPUTED part is unknowable; the literals are as decidable as a static list,
    and they are the real render path for every detail page in the chart.

    Measured when this was added: the skip cost X4 64 of 602 CRs and 64 of 462 `resourceRefId`
    occurrences, and cost X5 20 of the 134 CRs that declare `allowedResources`. P25 had the same
    hole and shipped claiming a migration complete while two pages had never been migrated."""
    found = []
    spec = doc.get('spec') or {}
    sources = [entry.get('expression') for entry in (spec.get('widgetDataTemplate') or [])
               if isinstance(entry, dict)]
    rrt = spec.get('resourcesRefsTemplate')
    if isinstance(rrt, list):
        sources += [e.get('expression') for e in rrt if isinstance(e, dict)]
    elif isinstance(rrt, dict):
        sources.append(rrt.get('expression'))
    for expr in sources:
        if not isinstance(expr, str):
            continue
        found += re.findall(r'resourceRefId"?\s*:\s*"([^"]+)"', expr)
    return found


def rule_dangling_ref(crs):
    """X4 — an items[].resourceRefId with no matching resourcesRefs entry.

    The same authoring mistake renders three different ways depending on the container: Row, Col,
    Flex and Card drop the child silently (console only), Table renders a dash, and Tabs is the
    only one that shows a visible error. Purely local and fully decidable."""
    out = []
    for fname, doc in crs:
        spec = doc.get('spec') or {}
        declared, _legacy = declared_refs(doc)
        # A resourcesRefsTemplate MINTS refs at resolve time, so the ids it computes are not
        # knowable here — but a CR carrying one still names literals elsewhere, and those are.
        minting = bool(spec.get('resourcesRefsTemplate'))
        templated_items = 'items' in templated_paths(doc)
        if not minting:
            for path, value in walk_strings(widget_data(doc)):
                if templated_items and path.endswith('resourceRefId'):
                    continue      # the static list is a pre-template default; judged below
                if path.endswith('resourceRefId') and value and value not in declared:
                    out.append((fname, f'{path} -> "{value}" has no matching resourcesRefs entry'))
            # The literals INSIDE the template expression — the real render path.
            for value in template_ref_ids(doc):
                if value not in declared:
                    out.append((fname, f'widgetDataTemplate names resourceRefId "{value}", which '
                                       f'has no matching resourcesRefs entry'))
    return out


def rule_row_nav_placeholder(crs):
    """P10 — every {placeholder} in rowNavigateTo must resolve to a cell on the row.

    An unresolvable placeholder makes buildRowPath return undefined and onRow return {} — no
    handler, no cursor, no warning. The row is pixel-identical to a working one.

    THE SUBTLETY THAT MAKES OR BREAKS THIS CHECK. `buildRowPath` resolves against the ROW'S CELLS,
    not against declared columns, and a table routinely carries cells that are never rendered as a
    column — `{routeNs}`, `{routeName}`, `{namespace}` exist purely to build the destination.
    Checking placeholders against `columns[].valueKey` alone flags every one of those: measured
    against the portal chart, that naive version reported 12 violations of which 12 were false.

    The cells come from a templated `dataSource`, whose jq is a string in the CR — so the keys ARE
    statically visible as `valueKey:"…"` literals, and the check reads both sources."""
    out = []
    for fname, doc in crs:
        if doc.get('kind') != 'Table':
            continue
        wd = widget_data(doc)
        nav = wd.get('rowNavigateTo')
        if not isinstance(nav, str):
            continue

        keys = {c.get('valueKey') for c in (wd.get('columns') or []) if isinstance(c, dict)}
        # Plus every valueKey literal the dataSource expression mints at resolve time.
        for entry in ((doc.get('spec') or {}).get('widgetDataTemplate') or []):
            if isinstance(entry, dict) and str(entry.get('forPath', '')).startswith('dataSource'):
                keys |= set(re.findall(r'valueKey\s*:\s*"([^"]+)"', str(entry.get('expression', ''))))
        # A static dataSource states its cells outright.
        for row in (wd.get('dataSource') or []):
            if isinstance(row, list):
                keys |= {c.get('valueKey') for c in row if isinstance(c, dict)}

        # A templated rowNavigateTo is computed, not authored — its placeholders are not knowable.
        if 'rowNavigateTo' in templated_paths(doc):
            continue

        for ph in re.findall(r'\{([^}]+)\}', nav):
            if ph not in keys:
                out.append((fname, f'rowNavigateTo "{nav}" uses {{{ph}}}, which is neither a column nor a dataSource cell — rows will be silently inert'))
    return out


def rule_back_link(crs):
    """P1 — one way back, and the breadcrumb is it.

    Filed four times on four pages with an identical fix each time. Matches a label that BEGINS
    with "back to" so a sentence merely containing the phrase is not caught."""
    out = []
    for fname, doc in crs:
        for path, value in walk_strings(widget_data(doc)):
            if path.split('.')[-1].split('[')[0] in ('label', 'text', 'title') and BACK_LINK.match(value):
                out.append((fname, f'{path} = "{value}" — the breadcrumb is the one way back'))
    return out


CRUMB_TRAIL = re.compile(r'\S\s+/\s+\S')


def rule_second_breadcrumb(crs):
    """P27 — one breadcrumb per page, and it is the shell's.

    The shell renders the page's breadcrumb above every page. A `Breadcrumb` widget in a chart, or an
    eyebrow Paragraph spelling a path ("Builders / Compose"), puts a second one under it — the
    composers did exactly that in-app until ScreenHeader removed the slot. A portal with no shell
    breadcrumb opts a Breadcrumb CR out with the `krateo.io/own-breadcrumb` annotation.

    A context eyebrow is not a trail: "Platform · tenant x" passes, "Platform / Compositions" does not."""
    out = []
    for fname, doc in crs:
        kind = doc.get('kind')
        annotations = (doc.get('metadata') or {}).get('annotations') or {}
        if kind == 'Breadcrumb' and not annotations.get('krateo.io/own-breadcrumb'):
            out.append((fname, "a Breadcrumb widget: the shell already renders this page's breadcrumb, so this is a second one"))
        data = widget_data(doc)
        if kind == 'Paragraph' and data.get('variant') == 'eyebrow' and CRUMB_TRAIL.search(str(data.get('text') or '')):
            out.append((fname, f'eyebrow text "{data.get("text")}" spells a path: that is the shell breadcrumb, said twice'))
    return out


def rule_emoji(crs):
    """P15 — no emoji in titles, headings or status text."""
    out = []
    for fname, doc in crs:
        for path, value in walk_strings(widget_data(doc)):
            leaf = path.split('.')[-1].split('[')[0]
            if leaf in ('title', 'label', 'text', 'stateLabel') and EMOJI.search(value):
                found = ''.join(EMOJI.findall(value))
                out.append((fname, f'{path} contains emoji ({found})'))
    return out


def rule_tag_colour_without_label(crs):
    """C13 — a coloured pill must never render without a label.

    Meaning carried by colour alone is invisible to a screen reader and to a colourblind reader.
    A templated `label` is computed, not missing, so those are skipped.

    THE PILL IS NOT A PROPERTY OF THE `Tag` KIND. This rule checked `kind == 'Tag'` and so
    examined 10 of 602 CRs. `Tag.tsx` and `PageHeader.tsx` render the SAME `<StatusPill>`
    component, and PageHeader draws one per entry of its own native `tags` array — 30 PageHeader
    CRs in the chart, every page header in the portal. The rule was written before PageHeader
    existed and was never widened when the migration moved the pills into it, so the check
    followed the kind while the component moved underneath it."""
    out = []
    for fname, doc in crs:
        kind = doc.get('kind')
        wd = widget_data(doc)
        templated = templated_paths(doc)

        if kind == 'Tag':
            if wd.get('color') and not wd.get('label') and 'label' not in templated:
                out.append((fname, 'Tag sets `color` with no `label` — colour alone carries the meaning'))
            continue

        # PageHeader draws a StatusPill per entry of `tags`. A templated `tags` is computed as a
        # whole, so it is skipped the same way a templated `label` is.
        if kind == 'PageHeader' and 'tags' not in templated:
            for i, tag in enumerate(wd.get('tags') or []):
                if not isinstance(tag, dict):
                    continue
                if tag.get('color') and not tag.get('label'):
                    out.append((fname, f'PageHeader tags[{i}] sets `color` with no `label` — '
                                       f'colour alone carries the meaning (same StatusPill a Tag draws)'))
    return out


def rule_dead_kind(crs):
    """A widget kind the frontend no longer resolves.

    The antd-fidelity migration was a HARD BREAK with no aliases — `Panel`→`Card`, `DataGrid`→
    `Listy`, `Column`→`Col`, `TabList`→`Tabs`, `NavMenu`→`Menu` — and the routing kinds (`Page`,
    `Route`, `RoutesLoader`, `NavMenuItem`) were removed outright when routing became data. A CR
    on one of these renders nothing: `getWidgetModule(kind)` returns undefined.

    This matters most in the charts nobody looks at. Measured across the four starter templates
    that new portals are CLONED from, 30-46% of CRs in each are on dead kinds — so a portal
    started from them is broken before anyone edits a line."""
    out = []
    for fname, doc in crs:
        kind = doc.get('kind')
        if kind in RENAMED_KINDS:
            out.append((fname, f'kind: {kind} no longer resolves — renamed to {RENAMED_KINDS[kind]} (hard break, no alias)'))
        elif kind in REMOVED_KINDS:
            out.append((fname, f'kind: {kind} was removed — routing is data now; the sidebar Menu\'s inline items are the route source'))
    return out


def rule_legacy_envelope(crs):
    """`resourcesRefs` as a bare list instead of `{items: [...]}`.

    The current CRD declares it an object. A chart still using the list form does not apply at
    all — it fails validation before a single widget renders."""
    out = []
    for fname, doc in crs:
        if isinstance((doc.get('spec') or {}).get('resourcesRefs'), list):
            out.append((fname, 'resourcesRefs is a bare list — the current CRD declares it an object ({items, slice}); this CR will not apply'))
    return out


# Source: ui/docs/cr-migration-map.json. Embedded so the script runs standalone in a chart's CI
# without needing this repo checked out beside it.
# Kind → plural, DISCOVERED from real CRDs rather than hardcoded here.
#
# Kubernetes is the authority: a table in this file goes stale the moment a widget is added, and
# deriving it (`Flex` → `flexs`, `Listy` → `listys`) got 125 references wrong on the first real run.
# Sources, in order — a CRD checkout reachable from the working tree, then the live cluster.
#
# When neither is available the rule still works, because the primary check in `rule_missing_target`
# is plural-INDEPENDENT. The mapping is only used for the secondary check, which stays silent
# unless the plural is genuinely known.
def discover_plurals():
    """{kind: plural} from real CRDs. An empty result is fine — the caller degrades gracefully."""
    found = {}
    for pattern in ('**/frontend-crds/templates/*.crd.yaml', '**/*.crd.yaml'):
        for path in glob.glob(pattern, recursive=True)[:300]:
            try:
                doc = yaml.safe_load(open(path, encoding='utf-8'))
            except Exception:
                continue
            names = (((doc or {}).get('spec') or {}).get('names') or {})
            if names.get('kind') and names.get('plural'):
                found[names['kind']] = names['plural']
        if found:
            return found

    # The cluster, if one is configured. Best-effort and time-boxed: a lint must never hang on a
    # missing kubeconfig or an unreachable API server.
    try:
        proc = subprocess.run(
            ['kubectl', 'get', 'crd', '-o',
             'jsonpath={range .items[?(@.spec.group=="widgets.templates.krateo.io")]}'
             '{.spec.names.kind}={.spec.names.plural}\n{end}'],
            capture_output=True, text=True, timeout=10, check=False,
        )
        for line in proc.stdout.splitlines():
            if '=' in line:
                kind, plural = line.split('=', 1)
                found[kind.strip()] = plural.strip()
    except Exception:
        pass
    return found


RENAMED_KINDS = {'Panel': 'Card', 'Column': 'Col', 'TabList': 'Tabs', 'NavMenu': 'Menu', 'DataGrid': 'Listy', 'List': 'Listy'}
REMOVED_KINDS = {'Page', 'Route', 'RoutesLoader', 'NavMenuItem', 'EventList', 'CompositionReference'}

def refs_of(doc):
    """`spec.resourcesRefs` as a list, whichever envelope it uses.

    The current CRD declares an object (`{items, slice}`); the legacy shape was a bare list, which
    X12 exists to report. A rule that assumes the object shape CRASHES on the legacy one — and a
    crash exits non-zero, which the self-test read as "the rule fired". X13 had been crashing on
    its own violations fixture for exactly that reason and scoring as a pass."""
    refs = (doc.get('spec') or {}).get('resourcesRefs')
    if isinstance(refs, dict):
        refs = refs.get('items')
    return [r for r in (refs or []) if isinstance(r, dict)]


WIDGET_API = 'widgets.templates.krateo.io'


def widget_crs(crs):
    """Only the widget CRs. RESTActions are `templates.krateo.io/v1` — a DIFFERENT group — as are
    Roles, Secrets and the CompositionDefinition, and none of them can be the target of a widget's
    `resourcesRefs`. Keeping them out of an existence index is what stops the chart's dominant
    naming convention (a RESTAction named after the widget it feeds — 17 such pairs) from vouching
    for a widget that has been deleted."""
    return [(f, d) for f, d in crs if WIDGET_API in str(d.get('apiVersion') or '')]


def learn_plurals(crs):
    """{kind: plural}, learned from the chart's OWN references — no CRD checkout, no inference.

    Every `resourcesRefs` entry carries both the plural (`resource`) and the target `name`. Where
    that name belongs to exactly one widget kind in the chart, the pair is an observation; where a
    name is shared across kinds it teaches nothing and is skipped. On the portal chart this learns
    24 of 27 kinds with ZERO conflicting observations.

    Why learn rather than pluralise: inferring `Flex` -> `flexs` produced 125 false positives once.
    The chart says `flexes`, and says `Listy` -> `listies`, which no naive rule gets right."""
    by_name = {}
    for _, doc in widget_crs(crs):
        name = ((doc.get('metadata') or {}).get('name') or '')
        if name:
            by_name.setdefault(name, set()).add(doc.get('kind'))
    unique = {n: next(iter(k)) for n, k in by_name.items() if len(k) == 1}

    seen = {}
    for _, doc in crs:
        for ref in refs_of(doc):
            name, plural = ref.get('name'), ref.get('resource')
            if not name or not plural or WIDGET_API not in str(ref.get('apiVersion') or ''):
                continue
            kind = unique.get(name)
            if kind:
                seen.setdefault(kind, set()).add(plural)
    # A kind observed with two different plurals teaches nothing reliable; drop it.
    return {k: next(iter(p)) for k, p in seen.items() if len(p) == 1}


def rule_missing_target(crs):
    """A resourcesRefs entry naming a CR that does not exist in the chart.

    `dangling-ref` checks the other direction — an items[] id with no resourcesRefs entry — and
    both are needed, because they fail differently. This one is the DELETION hazard: remove a CR
    and leave a reference to it somewhere else, and the parent silently renders without that child
    (Row/Col/Flex/Card drop it with only a console error). Nothing in the chart complains, and the
    page just says less than it used to.

    That is the top risk of the PageHeader migration, which deletes 3-6 CRs per page across a dozen
    pages — the exact shape this rule exists to catch.

    Only widget kinds are checked. A ref to a Secret, a ConfigMap or any non-widget resource is
    legitimately outside this chart's template set."""
    # The CRD-derived map when a checkout is reachable, otherwise the one learned from the chart.
    # discover_plurals() globs relative to the process CWD, and portal CI runs from a repo that
    # holds no *.crd.yaml at all — so there it returns nothing and every secondary check was
    # skipped. The learned map does not depend on where the process was started.
    plurals = dict(learn_plurals(crs))
    plurals.update(discover_plurals())
    # Every widget CR name in the chart, plus the plural each is ADDRESSED by where that is known.
    names, by_plural = set(), {}
    for fname, doc in crs:
        kind = doc.get('kind') or ''
        name = ((doc.get('metadata') or {}).get('name') or '')
        if not (kind and name):
            continue
        # WIDGETS ONLY. A RESTAction named `access-grants` must not vouch for a deleted Table
        # named `access-grants`; they are different API groups and a widget ref can only mean the
        # Table. Indexing every kind is how this rule went blind on 22 references — every one of
        # them a chart-wide table on Settings, Access, Agents, Observability or Incidents.
        if WIDGET_API not in str(doc.get('apiVersion') or ''):
            continue
        names.add(name)
        plural = plurals.get(kind)
        if plural:
            by_plural.setdefault(plural, set()).add(name)

    out = []
    for fname, doc in crs:
        for ref in refs_of(doc):
            plural, name = ref.get('resource'), ref.get('name')
            api = str(ref.get('apiVersion') or '')
            # Only widget CRs live in this chart's template set; anything else is out of scope.
            if not plural or not name or 'widgets.templates.krateo.io' not in api:
                continue
            # PRIMARY — plural-INDEPENDENT. Does a widget CR with this name exist at all? This is
            # the deletion hazard, and needing no plural knowledge means a stale or missing mapping
            # cannot silence it. An earlier version gated this on the plural and inverted the rule:
            # a reference to the LAST Card in a chart — the exact case where a deletion breaks
            # something — was the one case it skipped.
            if name not in names:
                out.append((fname, f'resourcesRefs -> {plural}/{name} does not exist in this chart — the parent will render without it'))
                continue

            # SECONDARY — the name exists but is addressed by the wrong plural. Reported ONLY when
            # the plural is genuinely known from a CRD, never inferred: inferring it (`Flex` →
            # `flexs`) is what produced 125 false positives.
            if plural in by_plural and name not in by_plural[plural]:
                out.append((fname, f'resourcesRefs -> {name} exists but is not a `{plural}` — wrong resource for its kind'))
    return out


def rule_containment(crs):
    """X5 — a child whose kind is not in its container's declared `allowedResources`.

    This is the ONLY enforcement there is. `allowedResources` is validated by nothing at runtime:
    not by OpenAPI (the CRD types it `string[]` with no enum, deliberately — the per-widget enums
    drifted and `Menu` carried two kinds removed in the routing refactor), not by a webhook, and
    not by the renderer, which resolves a `resourceRefId` without ever consulting it.

    So the field is a DECLARATION OF INTENT that nothing checks. A container that says it holds
    `paragraphs` and is handed a `Table` renders the Table quite happily. The declaration is still
    worth having — it is how an author says what a slot is for, and how the next reader knows
    whether a new child belongs — but only if something reads it back.

    Checked against the CHART, not against a frozen list: the child's real plural comes from its
    own `resourcesRefs` entry, so this stays correct as widget kinds are added and cannot go stale
    the way the enums did.

    Deliberately NOT reported: a container with no `allowedResources` at all. Absent means
    "unconstrained", which is a legitimate authoring choice; only a declaration that is CONTRADICTED
    is a defect. And templated `items` are skipped — a resolve-time item list is not knowable here,
    the same exclusion `dangling-ref` makes."""
    out = []
    for fname, doc in crs:
        spec = doc.get('spec') or {}
        wd = widget_data(doc)
        allowed = wd.get('allowedResources')
        if not isinstance(allowed, list) or not allowed:
            continue
        if spec.get('resourcesRefsTemplate'):
            continue          # refs minted at resolve time carry no knowable plural
        allowed_set = {a for a in allowed if isinstance(a, str)}
        # id -> the plural the CR itself declares for that child
        refs = spec.get('resourcesRefs')
        refs = refs.get('items') if isinstance(refs, dict) else refs
        by_id = {}
        if isinstance(refs, list):
            for r in refs:
                if isinstance(r, dict) and r.get('id') and r.get('resource'):
                    by_id[r['id']] = r['resource']
        static = [v for p, v in walk_strings(wd) if p.endswith('resourceRefId')]
        # A templated `items` supersedes the static list; judge the template's literals too.
        for value in (static + template_ref_ids(doc)):
            plural = by_id.get(value)
            if plural and plural not in allowed_set:
                out.append((
                    fname,
                    f'child `{value}` is a `{plural}`, which is not in this container\'s '
                    f'allowedResources ({", ".join(sorted(allowed_set))}) — it will still render, '
                    f'because nothing enforces the declaration at runtime',
                ))
    return out


def ref_resolver(crs):
    """(index, resolve) — how the RENDERER addresses a reference: by (plural, name).

    One implementation, because P25 and P9 both need it and a second copy is how "what is a page
    root" drifted four times.
    """
    index = {}
    for fname, doc in widget_crs(crs):
        name = ((doc.get('metadata') or {}).get('name') or '')
        if name:
            index[(doc.get('kind'), name)] = (fname, doc)
    plurals = dict(learn_plurals(crs))
    plurals.update(discover_plurals())
    kind_of = {p: k for k, p in plurals.items()}

    def resolve(name, plural):
        kind = kind_of.get(plural)
        if kind and (kind, name) in index:
            return index[(kind, name)]
        hits = [v for (k, n), v in index.items() if n == name]
        return hits[0] if len(hits) == 1 else None

    return index, resolve


# The one section step every nav-declared page root puts between its major sections (P9).
# The LABEL is antd's; the PX is not antd's documented value. Both themes apply
# `compactAlgorithm`, which halves the size ramp, so here small/middle/large = 4/8/16px, not
# 8/16/24. Anyone reasoning from antd's docs will pick the wrong one — hence the px in the name.
SECTION_GAP = 'middle'
SECTION_GAP_PX = 8


NAV_LABEL_ANNOTATION = 'krateo.io/nav-label'
NAV_PATH_ANNOTATION = 'krateo.io/nav-path'


def _annotated_page_roots(crs):
    """Yield (root_name, file, doc) for page roots that DECLARE their own nav entry.

    The second discovery source, for a menu assembled at RUNTIME from a cluster listing rather than
    written out in the Menu CR. A page that declares `krateo.io/nav-label` (a visible entry) or
    `krateo.io/nav-path` (a route-only one) is reachable from the nav by construction, so it is a
    page for every rule's purposes — exactly as a nav-declared root is.
    """
    for fname, doc in crs:
        meta = doc.get('metadata') or {}
        ann = meta.get('annotations') or {}
        if NAV_LABEL_ANNOTATION not in ann and NAV_PATH_ANNOTATION not in ann:
            continue
        name = meta.get('name')
        if name:
            yield name, fname, doc


def page_roots(crs):
    """Yield (root_name, page_file, page_doc) for every page the nav declares — by EITHER route.

    Extracted so P25 and P9 cannot disagree about what a page root is. Four hand surveys got that
    count wrong, each inheriting the last one's blind spot, because each looked for the SHAPE a page
    was expected to have rather than for what makes something a page — being reachable from the nav.
    A second rule re-deriving it independently would be the fifth.

    TWO SOURCES, DELIBERATELY. The original walk reads the Menu CR's `widgetData` for `page` /
    `resourceRefId` leaves. That is a STATIC read, and the sidebar is moving to a Menu whose items
    are computed server-side from a cluster listing — at which point the walk finds nothing and
    every rule built on it judges ZERO pages and passes. A lint that silently stops checking is
    worse than one that fails, so annotation-declared roots count too, and the union is what rules
    see. During the transition both sources are populated and agree; afterwards only the second is.
    """
    seen_roots = set()
    for name, fname, doc in _annotated_page_roots(crs):
        if name not in seen_roots:
            seen_roots.add(name)
            yield name, fname, doc

    _index, resolve = ref_resolver(crs)

    for fname, doc in crs:
        if doc.get('kind') != 'Menu':
            continue
        nav_refs = {r['id']: r for r in refs_of(doc) if r.get('id')}
        seen = set(seen_roots)
        for path, value in walk_strings(widget_data(doc)):
            leaf = path.rsplit('.', 1)[-1]
            if leaf == 'page':
                root, plural = f'page-{value}', None
            elif leaf == 'resourceRefId':
                ref = nav_refs.get(value)
                if not ref:
                    continue
                root, plural = ref.get('name'), ref.get('resource')
            else:
                continue
            if root in seen:
                continue
            seen.add(root)
            target = resolve(root, plural)
            if not target:
                continue          # P10's business
            seen_roots.add(root)
            yield root, target[0], target[1]


def rule_section_rhythm(crs):
    """P9 — a nav-declared page root whose section gap is not the one shared step.

    #54 §0.6 asked for a standard gap between major sections and a smaller one within a section.
    Without this rule the convention was re-decided per page: before it was first set, the 31 roots
    split middle 16 / large 13 / small 1 / unset 1.

    Judged only on the ROOT, which is what sets rhythm BETWEEN sections; the gap within a section is
    that section's own business. A root that declares no gap is reported too — inheriting a default
    is how the unset one got there, and an unstated value is not a decision.
    """
    out = []
    for root, page_file, page_doc in page_roots(crs):
        data = widget_data(page_doc)
        if (page_doc.get('metadata') or {}).get('annotations', {}).get('krateo.io/no-section-rhythm'):
            continue
        gap = data.get('gap')
        if gap == SECTION_GAP:
            continue
        if gap is None:
            out.append((page_file, f'page root `{root}` declares no `gap`, so its section rhythm is '
                                   f'whatever the renderer defaults to — set it to `{SECTION_GAP}` '
                                   f'({SECTION_GAP_PX}px) so the value is a decision, not an inheritance'))
        else:
            out.append((page_file, f'page root `{root}` uses gap `{gap}`, not the one section step '
                                   f'`{SECTION_GAP}` ({SECTION_GAP_PX}px) — annotate the root with '
                                   f'`krateo.io/no-section-rhythm` if this page genuinely differs'))
    return out


def rule_page_header(crs):
    """P25 — a page whose first child is not a `PageHeader`.

    Every page in the portal names itself, in the same place, in the same type ramp. That is the
    single most visible consistency rule the design system has, and until this rule landed the only
    thing enforcing it was someone running a survey and counting.

    Those surveys were wrong FOUR times, each in a way the next inherited, because each looked for
    the SHAPE a page header was expected to have instead of for the page:

      by name       `pageheader.*` / `*-header-block` missed two detail pages that spell their
                    parts `-titleline`.
      by first doc  `marketplace-detail.yaml` holds fifteen documents and opens with a RESTAction,
                    so a scanner reading one document per file never saw the header inside it.
      by container  a page opening on a bare `Paragraph` matched no container pattern.
      by templated  THIS RULE'S OWN FIRST DRAFT bailed out on any page whose `items` is assembled
                    by a jq template and counted the bail as a PASS. Every detail page in this
                    chart is authored that way, so the exemption landed precisely on the page class
                    the rule existed for — including the same two `-titleline` pages the first hand
                    survey missed. A rule that cannot see a case must SAY so, not pass it.

    So this rule starts from the NAV, which is what actually makes something a page, and resolves
    every route it declares. A page that exists but is unreachable is not this rule's business
    (P10 covers dangling routes); a page that is reachable and does not name itself is.

    A TEMPLATED `items` IS STILL JUDGED. The first child is recovered from whichever of these the
    page provides, and they must agree: the static `widgetData.items[0]`, which these pages carry
    as the pre-template default, and the first `resourceRefId` literal appearing in the template
    expression itself. If neither yields a child the page is reported as UNDETERMINED rather than
    passed — an unjudgeable page is a gap in the rule, and silence about it is how this rule
    shipped claiming a migration was complete when two pages had never been migrated.

    RESOLUTION IS BY (plural, name), never by name alone. 28 names in this chart are shared across
    kinds — a RESTAction and the Table it feeds conventionally share one — so a name-keyed index
    silently resolves to whichever document helm rendered last, which is decided by template
    filename order. The plural is already in hand: it is the `resource` on the `resourcesRefs`
    entry being followed.

    OPT-OUT, because one page legitimately has no single header: annotate the page root with
    `krateo.io/no-page-header: <reason>`. An exception that has to be written down and reviewed is
    the point; a silent exclusion list inside the lint is what let the hand surveys drift."""
    _index, resolve = ref_resolver(crs)

    def first_child(doc):
        """(kind, detail). kind is None when the page could not be judged — `detail` says why."""
        spec = doc.get('spec') or {}
        by_id = {r['id']: r for r in refs_of(doc) if r.get('id')}

        candidates = []
        items = widget_data(doc).get('items')
        if isinstance(items, list) and items and isinstance(items[0], dict):
            if items[0].get('resourceRefId'):
                candidates.append(items[0]['resourceRefId'])
        # The template's own first `resourceRefId` literal — these pages build `items` with jq,
        # and the head of that list is a literal in the expression.
        for entry in (spec.get('widgetDataTemplate') or []):
            if not isinstance(entry, dict) or not str(entry.get('forPath', '')).startswith('items'):
                continue
            found = re.search(r'resourceRefId"?\s*:\s*"([^"]+)"', str(entry.get('expression') or ''))
            if found:
                candidates.append(found.group(1))
                break

        if not candidates:
            if spec.get('resourcesRefsTemplate') or 'items' in templated_paths(doc):
                return None, 'its `items` are templated and the template names no literal first child'
            return None, 'it declares no items'
        if len(set(candidates)) > 1:
            return None, (f'its static first child ({candidates[0]}) and its templated first child '
                          f'({candidates[1]}) disagree')

        ref = by_id.get(candidates[0])
        if not ref:
            return None, f'its first child `{candidates[0]}` has no resourcesRefs entry'
        target = resolve(ref.get('name'), ref.get('resource'))
        if not target:
            return None, f'its first child `{ref.get("name")}` resolves to nothing in this chart'
        return target[1].get('kind'), ref.get('name')

    out = []
    for root, page_file, page_doc in page_roots(crs):
        if (page_doc.get('metadata') or {}).get('annotations', {}).get('krateo.io/no-page-header'):
            continue
        kind, detail = first_child(page_doc)
        if kind == 'PageHeader':
            continue
        if kind:
            out.append((page_file, f'page `{root}` opens on a `{kind}`, not a PageHeader — '
                                   f'every page names itself in the same place and type ramp; '
                                   f'annotate the root with `krateo.io/no-page-header` if this '
                                   f'page genuinely has none'))
        else:
            out.append((page_file, f'page `{root}` could not be judged: {detail} — a page this '
                                   f'rule cannot read is a gap in the rule, not a pass'))
    return out


ASK_PATH = re.compile(r'[?&]ask=')


ASK_LABEL = re.compile(r'^\s*ask autopilot\b', re.I)


def _is_autopilot_entry(button_doc):
    """A Button that opens the Autopilot rail seeded with a prompt.

    Three signals, because the chart spells the link three ways: A4's canonical label ("Ask
    Autopilot"), a static `?ask=` navigate path, or a widgetDataTemplate computing the navigate
    actions from an `askHref` (Alerts does that, so a path-only check missed it)."""
    data = widget_data(button_doc)
    if ASK_LABEL.match(str(data.get('label') or '')):
        return True
    for navigate in ((data.get('actions') or {}).get('navigate') or []):
        if isinstance(navigate, dict) and ASK_PATH.search(str(navigate.get('path') or '')):
            return True
    for entry in (button_doc.get('spec') or {}).get('widgetDataTemplate') or []:
        if isinstance(entry, dict) and str(entry.get('forPath', '')).startswith('actions') and 'askHref' in str(entry.get('expression') or ''):
            return True
    return False


AUTOPILOT_LABEL = 'Ask Autopilot →'
AUTOPILOT_ICON = 'fa-wand-magic-sparkles'


def rule_autopilot_button(crs):
    """A4 — every Autopilot entry point looks the same: a filled (`type: primary`) Button labelled
    "Ask Autopilot →" with the magic-wand icon. Never a link: the product's decision, recorded once
    here instead of re-litigated per page.

    Recognised by what the Button does, not what it says (a `?ask=` link or an `askHref` template),
    as well as by its label — the sweep that resolved A4 the first time missed a CTA precisely
    because it matched a shape instead of the capability. Where it sits is P26's business, as for any
    header action."""
    out = []
    for fname, doc in crs:
        if doc.get('kind') != 'Button' or not _is_autopilot_entry(doc):
            continue
        data = widget_data(doc)
        label, icon, kind = str(data.get('label') or ''), str(data.get('icon') or ''), data.get('type')
        if label != AUTOPILOT_LABEL:
            out.append((fname, f'Autopilot entry point labelled "{label}" — the canonical label is "{AUTOPILOT_LABEL}"'))
        if icon != AUTOPILOT_ICON:
            out.append((fname, f'Autopilot entry point with icon "{icon or "none"}" — it carries {AUTOPILOT_ICON}'))
        if kind != 'primary':
            out.append((fname, f'Autopilot entry point is a `type: {kind or "default"}` Button — it is a filled `type: primary` button, not a link'))
    return out


DISMISS_LABEL = re.compile(r'^\s*(cancel|close|close draft|dismiss|back|keep editing|keep it)\s*$', re.I)
DESTROY_LABEL = re.compile(r'^\s*(delete|remove|discard|destroy|uninstall)\b', re.I)


def rule_button_role(crs):
    """C26 — a Button says what it does to the work in front of you.

    DISMISS — it closes or backs out without deleting anything (Cancel, Close): `intent: dismiss`,
    which draws it amber and outlined. DESTROY — it deletes content (Delete, Remove, Discard):
    `danger: true`, red. Read from the label, the only place a CR says which it is; a label that is
    neither is not judged."""
    out = []
    for fname, doc in crs:
        if doc.get('kind') != 'Button':
            continue
        data = widget_data(doc)
        label = str(data.get('label') or '')
        if DISMISS_LABEL.match(label) and data.get('intent') != 'dismiss':
            out.append((fname, f'"{label}" dismisses — give it `intent: dismiss` (amber), not a {data.get("type") or "primary"} button'))
        if DESTROY_LABEL.match(label) and data.get('danger') is not True and data.get('color') != 'danger':
            out.append((fname, f'"{label}" deletes — give it `danger: true` (red)'))
    return out


def rule_root_coverage(crs):
    """P9+P25 coverage — a `page-*` CR the nav does not reach, so neither rule judged it.

    Both page rules start from the NAV, which is what makes something a page. That is right, and it
    has one failure mode: if the corpus contains a page the nav does not reach, both rules skip it
    in silence and report a clean run over an incomplete set.

    That is not hypothetical. The agents pages are gated behind `.Values.agents.enabled`, which
    defaults to false. Rendered with default values, the nav declares 26 roots while the chart ships
    31 pages — and P9 and P25 both passed, having judged 26 of 31 without saying so. A lint that
    quietly covers less than it claims is the thing this whole file exists to prevent.

    So: every CR named `page-*` must be reachable from the nav. In a correct render zero are not.
    A hit means either the render omitted a values flag (the lint is under-covering — fix the render)
    or the page is genuinely unreachable (a real defect — fix the nav). Both are worth a failure;
    neither is worth silence.
    """
    reachable = {name for name, _f, _d in page_roots(crs)}
    out = []
    for fname, doc in widget_crs(crs):
        name = ((doc.get('metadata') or {}).get('name') or '')
        # The `page-` prefix alone is too loose a proxy: it fired on `page-compose` (a Form) and
        # `page-compose-card` (a Card) — CRs that describe a page-composing FEATURE, not a page.
        # Every one of the 31 real page roots is a vertical container, so require the kind too.
        # A false positive is how a rule gets switched off, and this one found its own on first use.
        if doc.get('kind') not in ('Flex', 'Col'):
            continue
        if not name.startswith('page-') or name in reachable:
            continue
        out.append((fname, f'`{name}` looks like a page root but no nav entry reaches it, so P9 and '
                           f'P25 did NOT judge it — either this render omitted a values flag (e.g. '
                           f'`--set agents.enabled=true`) and this lint is under-covering, or the '
                           f'page is genuinely unreachable'))
    return out


# The widget-CR colour vocabulary: every key of `color` in ui/src/theme/tokens.ts. A CR names a
# colour by KEY — `color: red` — or through the legacy alias `var(--red-color)`, which
# `cssVariables` emits as `--${key}-color` for every key, so both forms reduce to the same check.
#
# Embedded so the rule works when it runs from a CHART repo, where ui/src is not present.
# discover_palette() prefers the real file when it is, and test_lint asserts the two agree — a key
# added or renamed in tokens.ts without updating this list fails the frontend's own CI.
PALETTE_KEYS = {
    'accent2', 'accentSoft', 'amber', 'background', 'blue', 'border',
    'cyan', 'dark', 'darkBlue', 'error', 'errorSoft', 'faint',
    'gold', 'gray', 'green', 'info', 'light', 'lightgray',
    'line', 'magenta', 'menubgend', 'menubgstart', 'olive', 'onmenubg',
    'orange', 'panelbg', 'primary', 'red', 'slate', 'success',
    'successSoft', 'teal', 'text', 'violet', 'warning', 'warningSoft',
}

CSS_VAR_COLOUR = re.compile(r'^var\(\s*--([A-Za-z0-9_]+)-color\s*\)$')


def discover_palette():
    """Palette keys from the real tokens.ts when it is reachable, else () so the caller falls back."""
    for pattern in ('ui/src/theme/tokens.ts', '**/ui/src/theme/tokens.ts'):
        for path in glob.glob(pattern, recursive=True)[:5]:
            try:
                src = open(path, encoding='utf-8').read()
            except OSError:
                continue
            m = re.search(r'export const color\s*:?[^=]*=\s*\{', src)
            if not m:
                continue
            depth, i = 0, m.end() - 1
            while i < len(src):
                if src[i] == '{':
                    depth += 1
                elif src[i] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            keys = set(re.findall(r"(\w+)\s*:\s*'#", src[m.end():i]))
            if keys:
                return keys
    return set()


def rule_colour_vocabulary(crs):
    """T8 — a widget CR naming a colour that is not in the palette.

    `getColorCode` resolves a CR's colour NAME against the palette and, on a miss, returns
    `palette.dark` — near-black — with no error. So `color: blu`, a key someone renamed, or a key
    someone deleted all render as almost-black text that reads as a styling choice. The palette is a
    public API consumed by chart authors, and nothing checked it.

    Both authoring forms are accepted because both are in live use and both resolve to a key:
    the bare name (`color: red`) and the legacy alias (`color: var(--red-color)`), which
    `cssVariables` emits for every key.

    A `var(--x)` that is not a `--*-color` alias is left alone — that is ordinary CSS custom-property
    use and none of this rule's business.
    """
    palette = discover_palette() or PALETTE_KEYS
    out = []
    for fname, doc in widget_crs(crs):
        for path, value in walk_strings(widget_data(doc)):
            if path.rsplit('.', 1)[-1] != 'color':
                continue
            value = value.strip()
            if not value:
                continue
            alias = CSS_VAR_COLOUR.match(value)
            if alias:
                key = alias.group(1)
                if key not in palette:
                    out.append((fname, f'{path} -> `{value}` names `--{key}-color`, and `{key}` is '
                                       f'not a palette key — cssVariables emits an alias per key, so '
                                       f'this variable is never defined and the colour falls back'))
                continue
            if value.startswith('var(') or value.startswith('#'):
                continue      # a non-colour custom property, or an explicit hex (that is T1's business)
            if '{' in value:
                # `{{ ... }}` is helm; `{readyColor}` is the widget's OWN itemTemplate placeholder,
                # substituted per row from the RA's data. Neither is knowable here, and judging a
                # placeholder as a literal is how a rule earns a false positive and gets switched off.
                continue
            if value not in palette:
                out.append((fname, f'{path} -> `{value}` is not a palette key, so getColorCode '
                                   f'returns palette.dark (near-black) with NO error'))
    return out


def rule_page_discovery_alive(crs):
    """P0 — the page-discovery walk found nothing, so every rule built on it is vacuous.

    THE FAILURE THIS EXISTS TO MAKE IMPOSSIBLE. `page_roots` is the shared definition of "a page",
    and P9, P25 and root-coverage are all built on it. It discovers roots by reading the Menu CR's
    widgetData (static) or a page root's nav annotations. If the Menu's items move to a
    `widgetDataTemplate` — computed server-side, which is where the sidebar is heading — the static
    walk sees nothing. Nothing ERRORS: the loops simply have no rows, every rule reports clean, and
    the suite goes green while checking exactly zero pages.

    A lint that silently stops checking is worse than one that fails, so this asserts the walk is
    still finding pages at all. It is deliberately dumb: no threshold to tune, no list to maintain.
    """
    if any(True for _ in page_roots(crs)):
        return []
    return [(
        'menu.sidebar-nav.yaml',
        'page discovery found ZERO page roots — every page rule below is vacuously passing. '
        f'Either the Menu\'s items are no longer statically readable (they moved to '
        f'widgetDataTemplate), or no page root carries {NAV_LABEL_ANNOTATION} / '
        f'{NAV_PATH_ANNOTATION}. Fix discovery before trusting a green run.',
    )]


RULES = {
    'page-discovery-alive': (rule_page_discovery_alive, 'P0'),
    'dead-kind': (rule_dead_kind, 'X11'),
    'missing-target': (rule_missing_target, 'X13'),
    'legacy-envelope': (rule_legacy_envelope, 'X12'),
    'dangling-ref': (rule_dangling_ref, 'X4'),
    'row-nav-placeholder': (rule_row_nav_placeholder, 'P10'),
    'back-link': (rule_back_link, 'P1'),
    'second-breadcrumb': (rule_second_breadcrumb, 'P27'),
    'autopilot-button': (rule_autopilot_button, 'A4'),
    'button-role': (rule_button_role, 'C26'),
    'emoji': (rule_emoji, 'P15'),
    'tag-colour-no-label': (rule_tag_colour_without_label, 'C13'),
    'containment': (rule_containment, 'X5'),
    'page-header': (rule_page_header, 'P25'),
    'section-rhythm': (rule_section_rhythm, 'P9'),
    'root-coverage': (rule_root_coverage, 'P9+P25'),
    'colour-vocabulary': (rule_colour_vocabulary, 'T8'),
}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('target', help='a chart directory (rendered with helm), a directory of rendered YAML, or a single YAML file')
    ap.add_argument('--rule', help='comma-separated subset of: ' + ', '.join(RULES))
    ap.add_argument('--quiet', action='store_true', help='only print violations')
    args = ap.parse_args()

    crs, unreadable = load_crs(args.target)
    if not args.quiet:
        print(f'lint-portal-consistency: {len(crs)} CRs from {args.target}')
    for fname, err in unreadable:
        print(f'  UNREADABLE {fname}: {err}', file=sys.stderr)

    selected = args.rule.split(',') if args.rule else list(RULES)
    total = 0
    for name in selected:
        if name not in RULES:
            print(f'unknown rule: {name}', file=sys.stderr)
            return 2
        fn, rule_id = RULES[name]
        hits = fn(crs)
        total += len(hits)
        if hits or not args.quiet:
            print(f'\n{rule_id} ({name}): {len(hits)} violation(s)')
        for fname, msg in hits:
            print(f'  {fname}: {msg}')

    if not args.quiet:
        print(f'\ntotal: {total} violation(s)')

    # A RUN THAT READ NOTHING IS NOT A CLEAN RUN. `unreadable` was printed to stderr and then
    # dropped on the floor: every rule trivially reported 0, `total` was 0, and the exit code said
    # success. That is how this lint was once pointed one path segment wrong — at
    # `helm/portal/templates`, the raw Helm templates, where 532 of 533 files fail to parse — and
    # reported "0 violations" over 1 CR while looking entirely healthy. Unreadable input is a
    # coverage failure and now fails the run.
    if unreadable:
        print(f'\nFAILED: {len(unreadable)} file(s) could not be parsed — this run inspected '
              f'{len(crs)} CR(s) and its 0s mean nothing. If you pointed this at a chart\'s '
              f'templates/ directory, point it at the chart directory instead; the lint renders '
              f'it with helm itself.', file=sys.stderr)
        return max(total, 1)
    return total


if __name__ == '__main__':
    sys.exit(main())
