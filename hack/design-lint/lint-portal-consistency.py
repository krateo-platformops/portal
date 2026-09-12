#!/usr/bin/env python3
"""
lint-portal-consistency — static checks for the design system's composition rules.

VENDORED from krateo-platformops/frontend `design/lint/`, which is the canonical source and where
the rules themselves live (`design/03-composition.md`, `design/04-silent-failures.md`).

Why a copy rather than a fetch: this repo already vendors its scripts (`hack/preflight-refs.py`),
CI here should not break because another repo landed a new rule, and a lint that needs the network
is a lint that fails on a bad morning. The cost is that this file can drift from upstream — if the
rules change there, re-copy the script, its fixtures and its self-test together.

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

def rule_dangling_ref(crs):
    """X4 — an items[].resourceRefId with no matching resourcesRefs entry.

    The same authoring mistake renders three different ways depending on the container: Row, Col,
    Flex and Card drop the child silently (console only), Table renders a dash, and Tabs is the
    only one that shows a visible error. Purely local and fully decidable."""
    out = []
    for fname, doc in crs:
        spec = doc.get('spec') or {}
        declared, _legacy = declared_refs(doc)
        # A resourcesRefsTemplate mints refs at resolve time, so its ids are not knowable here.
        if spec.get('resourcesRefsTemplate'):
            continue
        # An `items` list that is itself templated is not knowable either.
        if 'items' in templated_paths(doc):
            continue
        for path, value in walk_strings(widget_data(doc)):
            if path.endswith('resourceRefId') and value and value not in declared:
                out.append((fname, f'{path} -> "{value}" has no matching resourcesRefs entry'))
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
    """C13 — a Tag must never render a colour swatch with no label.

    Meaning carried by colour alone is invisible to a screen reader and to a colourblind reader.
    A templated `label` is computed, not missing, so those are skipped."""
    out = []
    for fname, doc in crs:
        if doc.get('kind') != 'Tag':
            continue
        wd = widget_data(doc)
        if wd.get('color') and not wd.get('label') and 'label' not in templated_paths(doc):
            out.append((fname, 'Tag sets `color` with no `label` — colour alone carries the meaning'))
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
    plurals = discover_plurals()
    # Every widget CR name in the chart, plus the plural each is ADDRESSED by where that is known.
    names, by_plural = set(), {}
    for fname, doc in crs:
        kind = doc.get('kind') or ''
        name = ((doc.get('metadata') or {}).get('name') or '')
        if not (kind and name):
            continue
        names.add(name)
        plural = plurals.get(kind)
        if plural:
            by_plural.setdefault(plural, set()).add(name)

    out = []
    for fname, doc in crs:
        for ref in (((doc.get('spec') or {}).get('resourcesRefs') or {}).get('items') or []):
            if not isinstance(ref, dict):
                continue
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


RULES = {
    'dead-kind': (rule_dead_kind, 'X11'),
    'missing-target': (rule_missing_target, 'X13'),
    'legacy-envelope': (rule_legacy_envelope, 'X12'),
    'dangling-ref': (rule_dangling_ref, 'X4'),
    'row-nav-placeholder': (rule_row_nav_placeholder, 'P10'),
    'back-link': (rule_back_link, 'P1'),
    'emoji': (rule_emoji, 'P15'),
    'tag-colour-no-label': (rule_tag_colour_without_label, 'C13'),
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
    return total


if __name__ == '__main__':
    sys.exit(main())
