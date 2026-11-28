#!/usr/bin/env python3
"""The namespace switcher (approved mockup, 2026-11-24): one word per thing.

- A namespace labelled krateo.io/tenant belongs to a tenant (a customer). It is listed by the tenant's
  name with the namespace muted beside it, under "Tenants"; the rest under "Other namespaces". Where
  no tenant exists there are no headings. The value is always the namespace (?projects= unchanged).
- The panel says Namespaces / All namespaces, never "project", and several picked read as one line.
- The chip is the PLATFORM's name (platformName); with none set there is no chip at all.

The `projects` RESTAction's own filter is run over namespace lists. Controls: the old filter puts no
tenant first and names none; an old-default chip renders with no platformName.
JQ=gojq runs the same checks on gojq. Exit code = failed checks.
"""
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)
_spec = importlib.util.spec_from_file_location('pinned', os.path.join(HERE, 'lint-pinned-crds.py'))
pinned = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pinned)
JQ = os.environ.get('JQ', 'jq')
CHART = os.path.join(HERE, '..', 'helm', 'portal')
failures = 0


def check(name, got, want):
    global failures
    ok = got == want
    failures += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{'' if ok else f': got {got!r}, want {want!r}'}")


def ns(name, tenant=None):
    """A namespace; `tenant` makes it a tenant's, as the onboarding policy labels one: the marker
    krateo.io/tenant "true" and the name in krateo.io/tenant-user ('' leaves the name out)."""
    if tenant is None:
        return {'metadata': {'name': name}}
    labels = {'krateo.io/tenant': 'true', **({'krateo.io/tenant-user': tenant} if tenant else {})}
    return {'metadata': {'name': name, 'labels': labels}}


def options(expr, namespaces):
    proc = subprocess.run([JQ, '-c', expr], input=json.dumps({'namespaces': {'items': namespaces}}),
                          capture_output=True, text=True, check=False)
    if proc.returncode:
        raise SystemExit(f'{JQ}: {proc.stderr}')
    return json.loads(proc.stdout)['options']


def docs_with(value):
    """The chart rendered with one --set value, as tbi.render renders it otherwise."""
    import shutil
    import tempfile
    import yaml
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(CHART, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        open(meta, 'w', encoding='utf-8').write(text.replace('CHART_VERSION', '0.0.0-dev').replace('APP_VERSION', '0.0.0-dev'))
        proc = subprocess.run(['helm', 'template', 'portal', staged, '--namespace', tbi.NS, '--set', value], capture_output=True, text=True, check=False)
        if proc.returncode:
            raise SystemExit(f'helm template failed:\n{proc.stderr.strip()}')
        out = proc.stdout
    return [d for d in yaml.safe_load_all(out) if d]


docs = tbi.render(CHART)
named = {(d.get('kind'), d['metadata']['name']): d for d in docs if d}
expr = named[('RESTAction', 'projects')]['spec']['filter']

# 057 today: no tenant, so no headings, alphabetical.
check('no tenants: plain namespaces, no headings', options(expr, [ns('krateo-system'), ns('cert-manager')]),
      [{'label': 'cert-manager', 'value': 'cert-manager'}, {'label': 'krateo-system', 'value': 'krateo-system'}])
# Where tenants exist: tenants first, by their name, the namespace as the hint; the rest under their heading.
got = options(expr, [ns('krateo-system'), ns('tenant-zeta', 'zeta'), ns('tenant-acme', 'acme'), ns('cert-manager')])
check('tenants first, by name, the namespace muted; the rest under Other namespaces', got, [
    {'label': 'acme', 'hint': 'tenant-acme', 'value': 'tenant-acme', 'group': 'Tenants'},
    {'label': 'zeta', 'hint': 'tenant-zeta', 'value': 'tenant-zeta', 'group': 'Tenants'},
    {'label': 'cert-manager', 'value': 'cert-manager', 'group': 'Other namespaces'},
    {'label': 'krateo-system', 'value': 'krateo-system', 'group': 'Other namespaces'}])
check('a tenant with no name label shows the namespace alone, still a tenant',
      options(expr, [ns('tenant-x', ''), ns('a')])[0], {'label': 'tenant-x', 'value': 'tenant-x', 'group': 'Tenants'})
old = "{ options: ([ .namespaces.items[]? | { label: .metadata.name, value: .metadata.name } ] | sort_by(.value)) }"
check('control: the old filter names no tenant', 'Tenants' in json.dumps(options(old, [ns('tenant-acme', 'acme')])), False)

select = named[('Select', 'project-select')]['spec']['widgetData']
check('the panel says namespaces, never project',
      (select['label'], select['placeholder'], select['summary'], select['panelFooter']),
      ('Namespaces', 'All namespaces', '{count} namespaces', 'kept from page to page'))
check('no word "project" on screen (name and queryParam are the URL\'s, not words)',
      'roject' in json.dumps({k: v for k, v in select.items() if k not in ('name', 'queryParam')}), False)

flex = named[('Flex', 'header-context')]['spec']
check('no platformName: no chip, no ref', (('Tag', 'tenant-chip') in named, [i['resourceRefId'] for i in flex['widgetData']['items']],
      [r['id'] for r in flex['resourcesRefs']['items']]), (False, ['project-select'], ['project-select']))
named2 = {(d.get('kind'), d['metadata']['name']): d for d in docs_with('platformName=krateo-057') if d}
check('platformName: the chip says it, before the switcher',
      (named2[('Tag', 'tenant-chip')]['spec']['widgetData']['label'], [i['resourceRefId'] for i in named2[('Flex', 'header-context')]['spec']['widgetData']['items']]),
      ('⬡ krateo-057', ['tenant-chip', 'project-select']))

# The options are computed, so lint-pinned-crds cannot see their fields: hold them, with the widget's own
# fields, against the Select CRD at the chart's declared floor. Control: 1.8.91, before frontend#790.
sample = dict(select, options=got)
def refused(version):
    spec = pinned.schemas(pinned.unpack(version))['Select'][named[('Select', 'project-select')]['apiVersion'].split('/', 1)[1]]
    return sorted({p.rsplit('.', 1)[-1] for p in pinned.undeclared(sample, spec['properties']['widgetData'])})
floor, floor_error = pinned.floor_of('portal')
check(f'the Select CRD at the floor ({floor}) declares every field', (floor_error, refused(floor)), (None, []) if floor_error is None else ('', []))
check('control: 1.8.91 refuses the new fields', refused('1.8.91'), ['group', 'hint', 'summary'])
print(f'namespace switcher: {failures} failure(s)')
sys.exit(failures)
