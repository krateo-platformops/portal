#!/usr/bin/env python3
"""The Dashboard's "not assessable" compositions: who is in the bucket, and what the page then says.

WHY. core-provider reports Ready=Unknown with reason `ZeroEvidence` when none of a composition's
managed children has readiness semantics it can read (unstructured-runtime v1.12.0 condition.go).
That is permanent and healthy, but the Dashboard counted it as Pending forever. compositions-list
now marks it `assessable: false`; the stat cards, the condition coverage and the reconciliation rail
set it aside and say how many they set aside. Hovering a rail row's "No signal" names the reason
(`rail.reason`, "Ready=Unknown · ZeroEvidence: <the controller's message>"); no other row has one.

`ChildrenForbidden` (core-provider 2.13.82, cdc childhealth.go) is the other Unknown that cannot be
judged: the controller was REFUSED reading every child it could not otherwise assess, an RBAC
defect. Since Diego's decision of 2026-11-10 it is set aside too (not Pending: nothing converges),
but labelled as the defect it is: its rail row says "Children unreadable" in the attention colour
(`warning`), with the controller's reason on hover, where ZeroEvidence says a neutral "No signal".

THE RULE, and the one that is easy to get wrong: the bucket is matched on the REASON, exactly as
core-provider spells it. Unknown also comes with `InventoryNotRecorded` (transient by design), and
the set of reasons is open. So an Unknown with any other reason must stay Pending. A match on
`status == "Unknown"` alone passes the 057 case and fails the defect case below.

HOW. The fixture is krateo-057's 82 compositions as read on 2026-11-08 (65 Available, 14
AvailablePartialCoverage, 1 ReconcilePaused, 2 ZeroEvidence), trimmed to the fields the RA reads.
The RA's own jq (both stages) runs over it, then each dashboard widget's own widgetDataTemplate runs
over the RA's output, and the Listy rows are resolved the way the frontend's itemTemplate.ts does,
so the checks are on what a person sees. With --crds DIR (a frontend-crds chart, as
`helm pull --untar` leaves it) every widget, as authored and as resolved, is validated against its
CRD, closed the way the apiserver's strict field validation is.

Usage: test-dashboard-assessable.py [--crds DIR]
"""
import argparse
import copy
import glob
import json
import os
import re
import subprocess
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
JQ = os.environ.get('JQ', 'jq')
FIXTURE = os.path.join(HERE, 'fixtures', 'dashboard-compositions-057.json')

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f'{label}: {got!r} != {want!r}')


def render(chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy_dir = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', os.path.join(REPO, 'helm', chart), copy_dir], check=True)
        meta = os.path.join(copy_dir, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy_dir], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template {chart} failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def named(docs, name):
    for d in docs:
        if d.get('metadata', {}).get('name') == name:
            return d
    raise AssertionError(f'{name} not in the rendered chart')


def jq(program, data):
    with tempfile.NamedTemporaryFile('w', suffix='.jq', delete=False) as fh:
        fh.write(program)
        path = fh.name
    try:
        proc = subprocess.run([JQ, '-c', '-f', path], input=json.dumps(data),
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise AssertionError(f'jq failed: {proc.stderr.strip()}')
        return json.loads(proc.stdout)
    finally:
        os.unlink(path)


def query(text):
    """plumbing jqutil.MaybeQuery: the jq between `${` and its matching `}`, or None for a literal."""
    start = text.find('${')
    if start < 0:
        return None
    depth, i = 1, start + 2
    while i < len(text):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[start + 2:i].strip()
        i += 1
    return None


# --- the frontend's row resolution (ui/src/widgets/List/itemTemplate.ts), so checks read the page --
def resolve_path(item, path):
    for key in path.split('.'):
        item = item.get(key) if isinstance(item, dict) else None
    return item


def interpolate(template, item):
    if not template:
        return ''

    def one(match):
        for path in match.group(1).split('|'):
            value = resolve_path(item, path.strip())
            if value is not None and value != '':
                if isinstance(value, bool):
                    return 'true' if value else 'false'
                return value if isinstance(value, str) else json.dumps(value)
        return ''
    return re.sub(r'\{([^}]+)\}', one, template)


def resolve_color(spec, item):
    if not spec:
        return 'gray'
    got = interpolate(spec.get('value'), item)
    if 'map' in spec:
        return spec['map'].get(got, spec.get('default', 'gray'))
    return got or spec.get('default') or 'gray'


def rows(listy, data_source):
    tpl = listy['spec']['widgetData']['itemTemplate']
    out = []
    for item in data_source:
        bar = tpl['bar']
        try:
            percent = max(0, min(100, float(interpolate(bar.get('percent'), item)) or 0))
        except ValueError:
            percent = 0
        out.append({'primary': interpolate(tpl.get('primaryText'), item),
                    'sub': interpolate(tpl.get('subPrimaryText'), item),
                    'percent': percent, 'color': resolve_color(bar.get('color'), item),
                    'label': interpolate(bar.get('label'), item),
                    'tooltip': interpolate(bar.get('tooltip'), item)})
    return out


# --- the RA, both stages, then the widgets over its output -------------------------------------
PORTAL = render('portal')
DASH = render('dashboard-page')
RA = named(PORTAL, 'compositions-list')['spec']
STAGE = next(a for a in RA['api'] if a['name'] == 'allCompositions')['filter']


def compositions_list(items):
    projected = jq(STAGE, {'allCompositions': {'items': items}})
    return jq(RA['filter'], {'allCompositions': projected})


def resolved(name, out):
    """A widget's widgetData with every widgetDataTemplate entry resolved over the RA output."""
    doc = named(DASH, name)
    data = copy.deepcopy(doc['spec']['widgetData'])
    for entry in doc['spec'].get('widgetDataTemplate') or []:
        program = query(entry['expression'])
        data[entry['forPath']] = jq(program, out) if program else entry['expression']
    return data


def page(out):
    """What the four cards, the coverage and the rail say for one compositions-list output."""
    subline = [i['resourceRefId'] for i in resolved('delta-compositions', out)['items']]
    rail = {r['primary']: r for r in rows(named(DASH, 'rail-list'), resolved('rail-list', out)['dataSource'])}
    return {
        'compositions': resolved('stat-compositions', out)['value'],
        'subline': resolved('delta-cap-not-assessable', out)['text']
        if subline == ['delta-cap-not-assessable'] else subline,
        'healthy': (resolved('stat-healthy', out)['value'], resolved('delta-tag-healthy', out)['label'],
                    resolved('delta-cap-healthy', out)['text']),
        'pending': (resolved('stat-reconciles', out)['value'], resolved('delta-tag-reconciles', out)['label'],
                    resolved('delta-cap-reconciles', out)['text']),
        'failed': (resolved('stat-failed', out)['value'], resolved('delta-tag-failed', out)['label'],
                   resolved('delta-cap-failed', out)['text']),
        'coverage': {r['primary']: (r['label'], r['percent'], r['sub'])
                     for r in rows(named(DASH, 'dashboard-conditions'), resolved('dashboard-conditions', out)['dataSource'])},
        'rail': rail,
    }


with open(FIXTURE, encoding='utf-8') as fh:
    LIVE = json.load(fh)


def unknown(name, reason):
    return {'apiVersion': 'composition.krateo.io/v0-1-0', 'kind': 'Synthetic',
            'metadata': {'name': name, 'namespace': 'krateo-system', 'uid': f'uid-{name}',
                         'creationTimestamp': '2026-11-01T00:00:00Z'},
            'status': {'conditions': [{'type': 'Ready', 'status': 'Unknown', 'reason': reason, 'message': ''},
                                      {'type': 'Synced', 'status': 'True', 'reason': 'ReconcileSuccess'}]}}


def ready_reason(item):
    return next((c['reason'] for c in item['status']['conditions'] if c['type'] == 'Ready'), None)


# core-provider 2.13.82's message for this reason (childrenForbiddenMessage + withUnreadable).
FORBIDDEN_MSG = ('Composition health is unknown: none of its 3 managed children could be health-evaluated '
                 '(3 of 3 managed children could NOT BE READ — this controller\'s ServiceAccount was refused '
                 'access, which is a gap in the generated RBAC rather than a problem with the children: '
                 'Deployment/web, Service/web, ConfigMap/web-config)')
UNREADABLE = unknown('children-forbidden', 'ChildrenForbidden')
UNREADABLE['status']['conditions'][0]['message'] = FORBIDDEN_MSG
# Unknowns that are NOT this bucket, and a composition still coming up: all Pending, all assessable.
DEFECTS = [unknown('inventory-not-recorded', 'InventoryNotRecorded'),
           unknown('some-new-reason', 'SomeNewReason')]
CREATING = unknown('creating', 'Creating')
CREATING['status']['conditions'][0]['status'] = 'False'

check('the fixture is the 057 mix', sorted({r: [ready_reason(i) for i in LIVE].count(r)
                                            for r in map(ready_reason, LIVE)}.items()),
      [('Available', 65), ('AvailablePartialCoverage', 14), ('ReconcilePaused', 1), ('ZeroEvidence', 2)])
SILENT = {'event-health', 'model-configs'}
check('the fixture\'s ZeroEvidence pair', sorted(i['metadata']['name'] for i in LIVE
                                                 if ready_reason(i) == 'ZeroEvidence'), sorted(SILENT))

# --- 1. krateo-057 as it is -----------------------------------------------------------------------
out = compositions_list(LIVE)
got = page(out)
check('057: Compositions', got['compositions'], 82)
check('057: Compositions subline names the not assessable', got['subline'], '2 not assessable')
check('057: Healthy over the assessable', got['healthy'], (80, '100%', 'of 80 assessable'))
check('057: Pending leaves the silent pair out', got['pending'], (0, '0%', 'of assessable'))
check('057: Failed is unchanged', got['failed'], (0, '0%', 'needs attention'))
check('057: coverage Ready counts the assessable, and says how many it left out',
      got['coverage'].get('Ready'), ('80/80', 100, '+ 2 not assessable (no readiness signal)'))
check('057: no other coverage row carries a note',
      sorted(t for t, (_, _, sub) in got['coverage'].items() if t != 'Ready' and sub), [])
for name in sorted(SILENT):
    row = got['rail'].get(name)
    check(f'057: rail {name} is a fully hatched neutral bar',
          row and (row['percent'], row['color'], row['label']), (0, 'gray', 'No signal'))
check('057: no other rail row says No signal',
      sorted(n for n, r in got['rail'].items() if r['label'] == 'No signal' and n not in SILENT), [])
# The tooltip on "No signal". The message is core-provider's own, less its opening "Composition health
# is unknown: " (Ready=Unknown already says it). No other row carries a reason, in the RA's whole list
# (the rail shows only 12 rows) or on the rail, so no other label gets a tooltip.
REASONS = {n: f'Ready=Unknown · ZeroEvidence: none of its {k} managed children could be health-evaluated '
              '(no readiness semantics this controller knows how to read)'
           for n, k in [('event-health', 5), ('model-configs', 3)]}
for name in sorted(SILENT):
    check(f'057: rail {name} names its reason on hover', (got['rail'].get(name) or {}).get('tooltip'), REASONS[name])
check('057: only the not assessable rows carry rail.reason',
      sorted(i['name'] for i in out['list'] if i['rail'].get('reason')), sorted(SILENT))
check('057: no other rail row has a tooltip',
      sorted(n for n, r in got['rail'].items() if r['tooltip'] and n not in SILENT), [])
check('057: the RA keeps .state and .railState for the pages that read them',
      sorted({(i['state'], i['railState']) for i in out['list'] if i['name'] in SILENT}), [('pending', 'pending')])

# --- 2. ChildrenForbidden is set aside too, and says it is a defect; other Unknowns stay Pending --
out = compositions_list(LIVE + [UNREADABLE] + DEFECTS)
got = page(out)
SET_ASIDE = SILENT | {'children-forbidden'}
check('defects: Compositions', got['compositions'], 85)
check('defects: the ZeroEvidence pair and ChildrenForbidden are set aside', got['subline'], '3 not assessable')
check('defects: Healthy over 82 assessable', got['healthy'], (80, '97%', 'of 82 assessable'))
check('defects: the two other Unknowns are Pending', got['pending'], (2, '2%', 'of assessable'))
check('defects: coverage Ready', got['coverage'].get('Ready'),
      ('80/82', 97, '+ 3 not assessable (no readiness signal)'))
item = next(i for i in out['list'] if i['name'] == 'children-forbidden')
check('defects: children-forbidden is not assessable', item['assessable'], False)
check('defects: children-forbidden keeps .state and .railState for the pages that read them',
      (item['state'], item['railState']), ('pending', 'pending'))
row = got['rail'].get('children-forbidden')
check('defects: rail children-forbidden is a fully hatched bar in the attention colour, saying why',
      row and (row['percent'], row['color'], row['label']), (0, 'warning', 'Children unreadable'))
check('defects: rail children-forbidden names its reason on hover', row and row['tooltip'],
      'Ready=Unknown · ChildrenForbidden: ' + FORBIDDEN_MSG.removeprefix('Composition health is unknown: '))
for name in sorted(SILENT):
    r = got['rail'].get(name)
    check(f'defects: rail {name} stays the neutral No signal', r and (r['percent'], r['color'], r['label']),
          (0, 'gray', 'No signal'))
check('defects: only children-forbidden says Children unreadable',
      sorted(n for n, r in got['rail'].items() if r['label'] == 'Children unreadable'), ['children-forbidden'])
for d in DEFECTS:
    name = d['metadata']['name']
    item = next(i for i in out['list'] if i['name'] == name)
    check(f'defects: {name} is assessable', item['assessable'], True)
    check(f'defects: {name} is pending', (item['state'], item['railState']), ('pending', 'pending'))
    row = got['rail'].get(name)
    check(f'defects: rail {name} keeps its pending bar', row and (row['percent'], row['color'], row['label']),
          (50, 'orange', '50%'))
    check(f'defects: {name} carries no rail.reason', 'reason' in item['rail'], False)
    check(f'defects: rail {name} has no tooltip', row and row['tooltip'], '')
check('defects: only the not assessable rows carry rail.reason',
      sorted(i['name'] for i in out['list'] if i['rail'].get('reason')), sorted(SET_ASIDE))
# Ready=False/Creating is converging: Pending and assessable, not set aside.
item = compositions_list([CREATING])['list'][0]
check('creating: assessable and pending', (item['assessable'], item['state'], item['railState']), (True, 'pending', 'pending'))
check('creating: its pending bar, no reason', item['rail'], {'percent': 50, 'state': 'pending', 'label': '50%'})
# ...and so the Pending card counts it, in its figure AND in its tag. The tag's pendingPercent used
# to count only rows whose Ready was neither True nor False, which left Creating out: here the card
# said 4 beside a 2%. Both now come from the same set, so they agree.
CREATING2 = copy.deepcopy(CREATING)
CREATING2['metadata'].update(name='creating-2', uid='uid-creating-2')
out = compositions_list(LIVE + [UNREADABLE] + DEFECTS + [CREATING, CREATING2])
got = page(out)
check('creating: the card counts both Creating with the two other Unknowns, and its tag says the same set',
      got['pending'], (4, '4%', 'of assessable'))
check('creating: Healthy over 84 assessable', got['healthy'], (80, '95%', 'of 84 assessable'))
check('creating: pendingPercent is pending over assessable',
      out['assessment']['pendingPercent'], out['assessment']['pending'] * 100 // out['assessment']['assessable'])
only_creating = compositions_list([CREATING])['assessment']
check('only Creating: one pending, and that is all of the assessable',
      (only_creating['pending'], only_creating['pendingPercent']), (1, 100))

# --- 3. nothing to set aside: the page reads as it did before ------------------------------------
# These are the values the widgets' previous expressions give over the same data (diffed against
# main when this landed): the "N new · past 24h" pair, "of total", and every rail row's own
# healthPercent and railState colour.
NONE = [i for i in LIVE if ready_reason(i) != 'ZeroEvidence'] + DEFECTS
out = compositions_list(NONE)
got = page(out)
check('none: Compositions', got['compositions'], 82)
check('none: the subline is the new-in-range pair', got['subline'],
      ['delta-tag-compositions', 'delta-cap-compositions'])
check('none: Healthy of total', got['healthy'], (80, '97%', 'of total'))
check('none: Pending of total', got['pending'], (2, '2%', 'of total'))
check('none: coverage Ready has no note', got['coverage'].get('Ready'), ('80/82', 97, ''))
rail_map = named(DASH, 'rail-list')['spec']['widgetData']['itemTemplate']['bar']['color']['map']
by_name = {i['name']: i for i in out['list']}
check('none: every rail row is its healthPercent and railState colour',
      sorted(n for n, r in got['rail'].items()
             if (r['percent'], r['color'], r['label'])
             != (by_name[n]['healthPercent'], rail_map[by_name[n]['railState']], f'{by_name[n]["healthPercent"]}%')), [])
check('none: no row carries rail.reason', sorted(i['name'] for i in out['list'] if 'reason' in i['rail']), [])
check('none: no rail row has a tooltip', sorted(n for n, r in got['rail'].items() if r['tooltip']), [])
check('none: the delta-compositions Flex resolves to its authored items',
      resolved('delta-compositions', out)['items'], named(DASH, 'delta-compositions')['spec']['widgetData']['items'])

# --- 4. edges -------------------------------------------------------------------------------------
out = compositions_list([])
check('empty: no division by zero', (out['assessment'], page(out)['healthy'], page(out)['pending']),
      ({'total': 0, 'assessable': 0, 'notAssessable': 0, 'pending': 0, 'healthyPercent': 0, 'pendingPercent': 0},
       (0, '0%', 'of total'), (0, '0%', 'of total')))
only_silent = [i for i in LIVE if ready_reason(i) == 'ZeroEvidence']
got = page(compositions_list(only_silent))
check('all silent: nothing assessable, nothing pending', (got['subline'], got['healthy'], got['pending']),
      ('2 not assessable', (0, '0%', 'of 0 assessable'), (0, '0%', 'of assessable')))
# The reason decides, not the message and not the status alone: ZeroEvidence on a status that is not
# Unknown is not this bucket.
odd = copy.deepcopy(only_silent[0])
odd['status']['conditions'][0]['status'] = 'False'
check('ZeroEvidence on Ready=False is not set aside', compositions_list([odd])['list'][0]['assessable'], True)
check('ZeroEvidence on Ready=False carries no rail.reason', 'reason' in compositions_list([odd])['list'][0]['rail'], False)
odd = copy.deepcopy(UNREADABLE)
odd['status']['conditions'][0]['status'] = 'False'
check('ChildrenForbidden on Ready=False is not set aside', compositions_list([odd])['list'][0]['assessable'], True)
check('ChildrenForbidden on Ready=False is failed, not unreadable',
      compositions_list([odd])['list'][0]['rail']['state'], 'failed')
# A near-miss spelling is not the reason: core-provider pins the exact string, and so does this.
odd = unknown('forbidden', 'Forbidden')
check('Unknown/Forbidden is not ChildrenForbidden', compositions_list([odd])['list'][0]['assessable'], True)
only_unreadable = compositions_list([UNREADABLE])
check('only ChildrenForbidden: nothing assessable, nothing pending',
      (only_unreadable['assessment']['notAssessable'], only_unreadable['assessment']['pending']), (1, 0))
# The reason text when the message is not the usual one: empty says the condition alone, and one
# without the usual opening is kept whole.
bare = copy.deepcopy(only_silent[0])
bare['status']['conditions'][0]['message'] = ''
check('ZeroEvidence with no message: the reason is the condition alone',
      compositions_list([bare])['list'][0]['rail']['reason'], 'Ready=Unknown · ZeroEvidence')
bare['status']['conditions'][0].pop('message')
check('ZeroEvidence with the message absent: the reason is the condition alone',
      compositions_list([bare])['list'][0]['rail']['reason'], 'Ready=Unknown · ZeroEvidence')
bare['status']['conditions'][0]['message'] = 'no child reports readiness'
check('ZeroEvidence with another message: kept whole',
      compositions_list([bare])['list'][0]['rail']['reason'], 'Ready=Unknown · ZeroEvidence: no child reports readiness')

# --- every rail state the RA emits is coloured (a missing one renders near-black) ----------------
check('the dashboard rail colours every rail.state',
      sorted({'healthy', 'drift', 'pending', 'degraded', 'failed', 'paused', 'notAssessable', 'unreadable'}
             - set(rail_map)), [])


# --- widgets as authored and as resolved, against the frontend CRDs -------------------------------
def strict(schema):
    """Close an openAPIV3Schema the way the apiserver's strict field validation does."""
    if isinstance(schema, dict):
        schema = {k: strict(v) for k, v in schema.items()}
        if schema.get('type') == 'object' and 'properties' in schema \
                and not schema.get('x-kubernetes-preserve-unknown-fields') and 'additionalProperties' not in schema:
            schema['additionalProperties'] = False
        if schema.get('x-kubernetes-int-or-string'):
            schema.pop('type', None)
            schema['anyOf'] = [{'type': 'integer'}, {'type': 'string'}]
        return schema
    if isinstance(schema, list):
        return [strict(x) for x in schema]
    return schema


WIDGETS = ['delta-compositions', 'delta-cap-not-assessable', 'delta-cap-healthy', 'delta-cap-reconciles',
           'delta-tag-healthy', 'delta-tag-reconciles', 'stat-reconciles', 'dashboard-conditions', 'rail-list']

parser = argparse.ArgumentParser()
parser.add_argument('--crds')
args = parser.parse_args()
crd_checks = 0
if args.crds:
    import jsonschema
    crds = {}
    for path in glob.glob(os.path.join(args.crds, '**', '*.yaml'), recursive=True):
        for doc in yaml.safe_load_all(open(path, encoding='utf-8')):
            if isinstance(doc, dict) and doc.get('kind') == 'CustomResourceDefinition':
                crds[doc['spec']['names']['kind']] = doc

    def validate(label, doc):
        global crd_checks
        crd_checks += 1
        crd = crds.get(doc['kind'])
        if crd is None:
            failures.append(f'{label}: no CRD for kind {doc["kind"]} in {args.crds}')
            return
        served = [v for v in crd['spec']['versions'] if v['name'] == doc['apiVersion'].split('/')[1]]
        if not served:
            failures.append(f'{label}: the {doc["kind"]} CRD serves no {doc["apiVersion"]}')
            return
        schema = strict(copy.deepcopy(served[0]['schema']['openAPIV3Schema']))
        schema.setdefault('properties', {}).update({'metadata': {'type': 'object'},
                                                    'apiVersion': {'type': 'string'}, 'kind': {'type': 'string'}})
        for err in sorted(jsonschema.Draft7Validator(schema).iter_errors(doc), key=lambda e: list(e.path)):
            failures.append(f'{label}: {"/".join(map(str, err.path))}: {err.message[:200]}')

    for case, items in [('057', LIVE), ('defects', LIVE + [UNREADABLE] + DEFECTS), ('none', NONE)]:
        out = compositions_list(items)
        for name in WIDGETS:
            doc = copy.deepcopy(named(DASH, name))
            if case == '057':
                validate(f'authored {doc["kind"]} {name}', doc)
            doc['spec']['widgetData'] = resolved(name, out)
            validate(f'resolved[{case}] {doc["kind"]} {name}', doc)

for line in failures:
    print(f'FAIL  {line}')
crd_note = f', {crd_checks} widget(s) validated against the CRDs' if args.crds else ', CRDs not checked (no --crds)'
print(f'{"FAILED" if failures else "ok"}: dashboard not-assessable bucket ({len(failures)} failure(s){crd_note})')
raise SystemExit(1 if failures else 0)
