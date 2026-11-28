#!/usr/bin/env python3
"""The blueprint detail page's data is split into three RESTActions so its skeleton clears early.

snowplow runs a RESTAction's stages serially, so when every widget read one seven-stage
`blueprint-detail` (compdef, jsonschema, catalog, catalogLive, comps, mayDelete, mayPatch, mayCreate)
the whole page waited for the cluster-wide compositions LIST and the three access reviews. Now
(restaction.blueprint-detail.yaml renders all three from one source):

  blueprint-detail               compdef, jsonschema, catalog, catalogLive           the cheap reads
  blueprint-detail-compositions  the same + comps                                    what reads compositions
  blueprint-detail-may           compdef, catalog, catalogLive, 3 access reviews     what reads the caller's access

Checked, over the chart as rendered:
  1. THE FAST WIDGETS DO NOT WAIT. The root Flex, the header, the left column, the Details card, Metadata
     and the "What it creates" card and its parts read neither the compositions RA nor the may RA.
  2. EVERY WIDGET'S DATA IS IN ITS RA. Each widget's widgetDataTemplate / resourcesRefsTemplate is parsed
     for the fields it reads from the RA's output, and each must be emitted by the RA it names, run over
     a fixture fed ONLY the stages that RA has (a field cannot come from a stage the RA does not run).
  3. A FIELD IS NEVER EMITTED FROM AN RA THAT DID NOT READ WHAT IT IS MADE OF: `upgrades`, `askHref` and
     `compositions` (they count compositions) only from the compositions RA; `may` only from the may RA.
  4. SAME PAGE. With OLD_BLUEPRINTS_CHART=<origin/main's helm/blueprints-page> (the single seven-stage
     RA) every widget's expressions are evaluated over the old RA's output and over the output of the RA
     it reads now, and must give the same result. Without it, the union of the three outputs must cover
     every field the old RA emitted.

BLUEPRINTS_CHART=<origin/main's chart> is the control: checks 1-3 fail there.
Uses `helm` and `jq`. Exit code = number of failures.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.environ.get('BLUEPRINTS_CHART') or os.path.join(os.path.dirname(HERE), 'helm', 'blueprints-page')
OLD = os.environ.get('OLD_BLUEPRINTS_CHART')
JQ = os.environ.get('JQ', 'jq')
BASE, COMPS, MAY = 'blueprint-detail', 'blueprint-detail-compositions', 'blueprint-detail-may'

# Every top-level field the single RA emitted (the union the three must still cover).
OLD_FIELDS = {'askHref', 'contents', 'migratedValues', 'inputs', 'inputsHref', 'upgrades', 'may', 'detail',
              'compositions', 'compositionsCount', 'name', 'namespace', 'marketplaceHref', 'inCatalog', 'chart',
              'chartUrl', 'currentVersion', 'availableVersions', 'latestVersion', 'latestLabel', 'updateAvailable',
              'ready', 'synced', 'created', 'versionSchema', 'versionInitial', 'gvr'}

# Widgets that must NOT wait for the slow RAs: kind -> names.
FAST = {('Flex', 'page-blueprint-detail'), ('PageHeader', 'blueprint-detail-page-header'),
        ('Flex', 'blueprint-detail-col-left'), ('Card', 'card-blueprint-detail-info'),
        ('Card', 'card-blueprint-detail-metadata'), ('Descriptions', 'descriptions-blueprint-detail-metadata'),
        ('Card', 'card-blueprint-detail-contents'), ('List', 'list-blueprint-detail-contents')}
FAST_PREFIX = ('paragraph-blueprint-detail-contents-',)
# The one Details table that reads both: its Upgrades row counts the compositions behind the served version.
NEEDS_COMPOSITIONS = {('Descriptions', 'descriptions-blueprint-detail-info'): 'the Upgrades row counts compositions'}


def render(chart):
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', chart, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read().replace('version: CHART_VERSION', 'version: 0.0.0-test')
        open(meta, 'w', encoding='utf-8').write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy, '--namespace', 'krateo-blueprints-page',
                               '--set', 'namespace=krateo-blueprints'], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip())
        return {(d['kind'], d['metadata']['name']): d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)}


def jq(program, data):
    proc = subprocess.run([JQ, '-c', program], input=json.dumps(data), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'jq failed: {proc.stderr.strip()[:300]}')
    return json.loads(proc.stdout)


def template(expr):
    expr = expr.strip()
    assert expr.startswith('${') and expr.endswith('}'), expr
    return expr[2:-1]


def expressions(widget):
    """Every jq template a widget evaluates over its RA's output."""
    spec = widget['spec']
    out = [e['expression'] for e in spec.get('widgetDataTemplate') or []]

    def walk(v):
        if isinstance(v, str) and v.strip().startswith('${'):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(spec.get('resourcesRefsTemplate') or [])
    return out


def reads(expr, fields):
    """The RA output fields an expression reads: `.field` at the head of a path, string literals ignored."""
    body = re.sub(r'"(?:[^"\\]|\\.)*"', '""', template(expr))
    return {m for m in re.findall(r'(?<![\w\]\)"\.])\.([A-Za-z_]\w*)', body) if m in fields}


def api_ref(widget):
    return ((widget.get('spec') or {}).get('apiRef') or {}).get('name')


# --- the fixture: a reconciled blueprint with compositions, a catalog, an authored-schema ConfigMap, access ---
CD = {'metadata': {'name': 'web-app', 'namespace': 'krateo-system', 'uid': 'u-1', 'labels': {'a': 'b'},
                   'creationTimestamp': '2026-11-01T00:00:00Z'},
      'spec': {'chart': {'repo': 'web-app', 'version': '1.4.2'}, 'upgradePolicy': 'Manual'},
      'status': {'apiVersion': 'composition.krateo.io/v1-4-2', 'resource': 'webapps', 'kind': 'WebApp',
                 'conditions': [{'type': 'Ready', 'status': 'True'}, {'type': 'Synced', 'status': 'True'}],
                 'contents': {'chartVersion': '1.4.2', 'values': 'default',
                              'kinds': [{'apiVersion': 'v1', 'kind': 'ConfigMap', 'count': 2}]}}}
COMP = lambda n, v: {'metadata': {'name': n, 'namespace': 'team-a', 'creationTimestamp': '2026-11-02T00:00:00Z',
                                  'labels': {'krateo.io/composition-version': v}},
                     'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}}
STAGES = {
    'compdef': CD,
    'jsonschema': {'data': {'values.schema.json': json.dumps({'type': 'object', 'properties': {}})}},
    'catalog': {'entries': {'web-app': [{'version': '1.5.0'}, {'version': '1.4.2'}]}},
    'comps': {'items': [COMP('a', 'v1-4-2'), COMP('b', 'v1-3-0')]},
    'mayDelete': {'allowed': True, 'evaluated': True},
    'mayPatch': {'allowed': False, 'evaluated': True},
    'mayCreate': {'allowed': True, 'evaluated': True},
}


def run(ra, extras=None):
    """The RA's final filter over exactly the stages it declares (extras at top level, as snowplow gives them)."""
    names = {s['name'] for s in ra['spec']['api']}
    data = {'name': 'web-app', 'namespace': 'krateo-system', **(extras or {})}
    data.update({k: v for k, v in STAGES.items() if k in names})
    return jq(ra['spec']['filter'], data)


def main():
    docs = render(CHART)
    f = []

    def bad(msg):
        f.append(msg)
        print(f'FAIL  {msg}')

    ras = {n: docs.get(('RESTAction', n)) for n in (BASE, COMPS, MAY)}
    for n, ra in ras.items():
        if ra is None:
            bad(f'RESTAction {n} is not rendered')
    if f:
        return min(len(f), 125)
    out = {n: run(ra) for n, ra in ras.items()}
    stages = {n: [s['name'] for s in ra['spec']['api']] for n, ra in ras.items()}

    # 1. the fast widgets do not wait for the slow RAs
    widgets = [(k, d) for k, d in docs.items() if k[0] != 'RESTAction' and api_ref(d) in (BASE, COMPS, MAY)]
    for key, w in widgets:
        fast = key in FAST or key[1].startswith(FAST_PREFIX)
        if fast and api_ref(w) != BASE:
            bad(f'{key[0]} {key[1]} reads {api_ref(w)}: it must clear with the cheap RA {BASE}')
    for key, why in NEEDS_COMPOSITIONS.items():
        if api_ref(docs[key]) != COMPS:
            bad(f'{key[1]} must read {COMPS} ({why})')

    # 2. every widget's data is in its RA
    for key, w in widgets:
        ra = api_ref(w)
        for e in expressions(w):
            missing = sorted(r for r in reads(e, OLD_FIELDS) if r not in out[ra])
            if missing:
                bad(f'{key[0]} {key[1]} reads {missing} from {ra}, which does not emit it')

    # 3. a field is only emitted by an RA that ran its stages
    counts = {'upgrades', 'askHref', 'compositions', 'compositionsCount'}
    for n in (BASE, MAY):
        for fld in sorted(counts & set(out[n])):
            bad(f'{n} emits {fld}, which counts compositions, without the comps stage')
    if 'comps' in stages[BASE] + stages[MAY]:
        bad('only the compositions RA lists compositions')
    for n in (BASE, COMPS):
        if 'may' in out[n]:
            bad(f'{n} emits may without the access reviews')
        if {'mayDelete', 'mayPatch', 'mayCreate'} & set(stages[n]):
            bad(f'{n} runs an access review')
    if set(stages[MAY]) - {'compdef', 'catalog', 'catalogLive', 'mayDelete', 'mayPatch', 'mayCreate'}:
        bad(f'{MAY} runs stages it has no reader for: {stages[MAY]}')

    union = set().union(*(set(o) for o in out.values()))
    if OLD_FIELDS - union:
        bad(f'the three RAs no longer emit {sorted(OLD_FIELDS - union)}')

    # the values the split RAs share are the same values
    for a, b in ((BASE, COMPS), (BASE, MAY), (COMPS, MAY)):
        for fld in sorted((set(out[a]) & set(out[b])) - {'may'}):
            if out[a][fld] != out[b][fld]:
                bad(f'{fld} differs between {a} and {b}')

    # 4. same page as the single RA
    if OLD:
        old_docs = render(OLD)
        old_out = run(old_docs[('RESTAction', BASE)])
        for key, w in widgets:
            new = out[api_ref(w)]
            for e in expressions(w):
                got, want = jq(template(e), new), jq(template(e), old_out)
                if got != want:
                    bad(f'{key[0]} {key[1]}: {json.dumps(got)[:120]} != old {json.dumps(want)[:120]}')
        merged = {}
        for o in out.values():
            merged.update(o)
        for fld in sorted(OLD_FIELDS):
            if merged.get(fld) != old_out.get(fld):
                bad(f'field {fld}: the union of the three RAs differs from the old single RA')

    print(f'{"PASS" if not f else "FAIL"}: {len(widgets)} widgets over {BASE} ({len(stages[BASE])} stages), '
          f'{COMPS} ({len(stages[COMPS])}), {BASE}-may ({len(stages[MAY])})'
          f'{"; same output as the old single RA" if OLD else ""} ({len(f)} failure(s))')
    return min(len(f), 125)


if __name__ == '__main__':
    sys.exit(main())
