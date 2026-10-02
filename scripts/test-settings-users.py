#!/usr/bin/env python3
"""
test-settings-users — Settings > Users, resolved without a Secret.

WHAT IT GUARDS. restaction.settings-users lists the users the cluster knows — basic-strategy User
CRs and the User subjects of portal-managed access grants — instead of the per-user clientconfig
Secrets it used to LIST (lint-ra-secrets.py says why no RESTAction may). Resolved on fixtures shaped
like krateo-057 (admin and cyberjoker User CRs; grants as form.access-grant writes them):
  1. Who is listed: the union, once each, sorted; an OIDC identity once it holds a grant; groups
     from the User CR; the grant count joined on the slugified subject label; a Group subject is
     not a user. Source names the set that listed each user: "User CR", "Grant" (a raw User
     subject of a managed grant) or "User CR · Grant".
  2. Each read denied or absent (an OIDC-only cluster has no User CRD) degrades to what remains;
     the prewarm (nothing served) is an empty table, never an error.
  3. No step touches secrets, and the table has no column the Secret alone could fill.
  4. The one-line note on who is listed sits right above the table, as a secondary Paragraph.
  Every resolved Table is validated against its CRD with --crds.

Usage: test-settings-users.py [--crds DIR]. Exit code = failed checks.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tbi = _load('tbi', 'test-builder-install.py')
NS = tbi.NS
expect = tbi.expect
RA = 'settings-users'
CHART = None


def user(name, groups=None):
    obj = {'apiVersion': 'basic.authn.krateo.io/v1alpha1', 'kind': 'User',
           'metadata': {'name': name, 'namespace': NS}, 'spec': {'displayName': name}}
    if groups is not None:
        obj['spec']['groups'] = groups
    return obj


def grant(subject, ns, kind='User'):
    """A RoleBinding as form.access-grant writes it: slug label, RAW subject name."""
    slug = ''.join(c if (c.isascii() and (c.isalnum() or c == '-')) else '-' for c in subject.lower())
    return {'metadata': {'name': f'krateo-access-{slug}-{ns}', 'namespace': ns,
                         'labels': {'krateo.io/managed-by': 'portal-access', 'krateo.io/subject': slug}},
            'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': f'krateo-access-{slug}-{ns}'},
            'subjects': [{'apiGroup': 'rbac.authorization.k8s.io', 'kind': kind, 'name': subject}]}


USERS = {'items': [user('cyberjoker', ['devs']), user('admin', ['admins']), user('s6-harness')]}
GRANTS = {'items': [grant('cyberjoker', 'team-a'), grant('cyberjoker', 'team-b'),
                    grant('Jane.Doe@example.com', 'team-a'), grant('platform-ops', 'team-a', kind='Group')]}


def table(out, label):
    doc = tbi.resolved_widget(CHART, 'Table', RA, out, {}, f'{RA} ({label})')
    return [{c['valueKey']: c.get('stringValue') for c in row} for row in doc['spec']['widgetData']['dataSource']]


def check_who_is_listed():
    f = []
    out = tbi.resolve(CHART, RA, {'basicUsers': USERS, 'grantBindings': GRANTS}, {})
    expect(f, 'personas', out['personas'], [
        {'username': 'Jane.Doe@example.com', 'groups': [], 'grants': 1, 'source': 'Grant'},
        {'username': 'admin', 'groups': ['admins'], 'grants': 0, 'source': 'User CR'},
        {'username': 'cyberjoker', 'groups': ['devs'], 'grants': 2, 'source': 'User CR · Grant'},
        {'username': 's6-harness', 'groups': [], 'grants': 0, 'source': 'User CR'}])
    expect(f, 'table rows', table(out, 'both read'), [
        {'user': 'Jane.Doe@example.com', 'groups': '—', 'grants': '1', 'source': 'Grant'},
        {'user': 'admin', 'groups': 'admins', 'grants': '0', 'source': 'User CR'},
        {'user': 'cyberjoker', 'groups': 'devs', 'grants': '2', 'source': 'User CR · Grant'},
        {'user': 's6-harness', 'groups': '—', 'grants': '0', 'source': 'User CR'}])
    return f


def check_denied_reads_degrade():
    f = []
    cases = [('User CRs denied or absent (OIDC-only)', {'basicUsers': 'ERROR', 'grantBindings': GRANTS},
              [('Jane.Doe@example.com', 'Grant'), ('cyberjoker', 'Grant')]),
             ('grants denied', {'basicUsers': USERS, 'grantBindings': 'ERROR'},
              [('admin', 'User CR'), ('cyberjoker', 'User CR'), ('s6-harness', 'User CR')]),
             ('both denied', {'basicUsers': 'ERROR', 'grantBindings': 'ERROR'}, []),
             ('prewarm, nothing served', {}, [])]
    for label, resp, want in cases:
        out = tbi.resolve(CHART, RA, resp, {})
        expect(f, f'{label}: users and sources', [(p['username'], p['source']) for p in out['personas']], want)
        table(out, label)
    return f


def check_no_secret_is_read():
    f = []
    ra = CHART.get('RESTAction', RA)
    expect(f, 'no step path or access filter names secrets',
           [st['name'] for st in ra['spec']['api'] if 'secrets' in (st.get('path') or '')
            or (st.get('userAccessFilter') or {}).get('resource') == 'secrets'], [])
    cols = [c['valueKey'] for c in CHART.get('Table', RA)['spec']['widgetData']['columns']]
    expect(f, 'columns', cols, ['user', 'groups', 'grants', 'source'])
    return f


NOTE = 'People who sign in through OIDC, LDAP or OAuth appear here once they hold a grant.'


def check_note_above_the_table():
    f = []
    note = tbi.resolved_widget(CHART, 'Paragraph', 'settings-users-note', {}, {}, 'settings-users-note')
    expect(f, 'note', {k: note['spec']['widgetData'].get(k) for k in ('text', 'type')},
           {'text': NOTE, 'type': 'secondary'})
    page = CHART.get('Flex', 'page-settings')['spec']
    refs = {r['id']: r for r in page['resourcesRefs']['items']}
    items = [i['resourceRefId'] for i in page['widgetData']['items']]
    expect(f, 'note sits right above the table',
           items[items.index('settings-users-note') + 1:][:1] if 'settings-users-note' in items else [],
           ['settings-users'])
    expect(f, 'note ref', {k: (refs.get('settings-users-note') or {}).get(k) for k in ('name', 'resource')},
           {'name': 'settings-users-note', 'resource': 'paragraphs'})
    if 'paragraphs' not in page['widgetData']['allowedResources']:
        f.append('page-settings does not allow paragraphs')
    return f


CHECKS = [check_who_is_listed, check_denied_reads_degrade, check_no_secret_is_read, check_note_above_the_table]


def main():
    global CHART
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        i = args.index('--crds')
        crds_dir = args[i + 1]
        del args[i:i + 2]
    CHART = tbi.Chart(tbi.render(args[0] if args else os.path.join(HERE, '..', 'helm', 'portal')))
    failed = 0
    for check in CHECKS:
        try:
            problems = check()
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {check.__name__.replace("check_", "").replace("_", " ")}')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    total = len(CHECKS)
    if crds_dir:
        total += 1
        problems = tbi.validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} resolved widgets validate against their CRDs')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}{"" if crds_dir else "; resolved widgets NOT validated (no --crds)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
