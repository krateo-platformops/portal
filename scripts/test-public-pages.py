#!/usr/bin/env python3
"""
test-public-pages — the public pages publish exactly what they show, to the public identity only.

WHAT IT GUARDS. helm/public-pages serves /public/request-access and
/public/request-access-confirm to visitors who are not signed in. snowplow serves a token-less
request as the user `krateo:public` (snowplow#1334) and decides what it may read by RBAC alone, so
the chart's one Role IS the definition of what is public. Rendered from the chart:

  1. The Role's resourceNames equal, EXACTLY, the set of objects reachable from the two page roots
     (page-request-access, page-request-access-confirm) through resourcesRefs and apiRef: a name
     missing is a widget that 403s for every visitor, an extra one is an object published for no
     reason. Every rule is `get` with resourceNames; nothing is listable, watchable or writable.
     Every ref resolves to an object the chart renders, in the chart's namespace. A templated ref
     (resourcesRefsTemplate) or a RESTAction cannot be enumerated from the chart, so either one is
     a finding until this test learns to enumerate it.
  2. ITS OWN CONTROL, run every time: the same comparison over the Role with one name removed, and
     with one name added, must each report a finding. A comparison that cannot fail proves nothing.
  3. The Role is bound to the USER krateo:public and to nothing else — never the group
     system:unauthenticated (also what an anonymous apiserver caller is) — and the chart renders no
     ClusterRole or ClusterRoleBinding.
  4. The roots: page-<slug> names, `krateo.io/fragment-keys: "s,t"` on the confirm root only, and
     NO `krateo.io/nav-*` annotation on any object: these are public routes, not sidebar entries.
  5. The namespace: created by the chart, NOT kept (it holds no data), and every object lives in it
     — also when `namespace` is overridden.
  6. The forms post only to identity-lifecycle's three intake paths, with no snowplow write
     (`actions`/`submitActionId`), and send only fragment keys the root declares.
  7. Every intake answer is mapped (README @66b93b8), `retry` falling to `otherwise` by design, and
     the Diego-approved copy added to mockup v3 is present verbatim.
  8. The country select: the complete ISO 3166-1 alpha-2 set (249 codes, Norway included — a bare
     NO is YAML false), `enum` and `oneOf` naming the same codes; cross-checked against pycountry
     wherever it is installed.
  9. The per-installation legal copy comes from values.
  With --crds DIR (an unpacked frontend-crds chart) every rendered widget is validated against its
  CRD, after checking that the validator refuses an undeclared key.

Usage: test-public-pages.py [--crds DIR]. Exit code = failed checks.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART_DIR = os.path.join(REPO, 'helm', 'public-pages')
WIDGET_GROUP = 'widgets.templates.krateo.io'
PUBLIC_USER = 'krateo:public'
ROOTS = ('page-request-access', 'page-request-access-confirm')
INTAKE = '/request-access/api/v1/'
DEFAULT_NS = 'krateo-public'


def render(*sets):
    """The chart's rendered objects, with the release placeholders substituted."""
    work = tempfile.mkdtemp()
    try:
        dst = os.path.join(work, 'public-pages')
        shutil.copytree(CHART_DIR, dst)
        meta = os.path.join(dst, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        open(meta, 'w', encoding='utf-8').write(text.replace('CHART_VERSION', '0.0.0').replace('APP_VERSION', '0.0.0'))
        cmd = ['helm', 'template', 'x', dst, '--namespace', 'krateo-system']
        for s in sets:
            cmd += ['--set', s]
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode:
            raise RuntimeError(f'helm template failed: {out.stderr.strip()[-800:]}')
        return [d for d in yaml.safe_load_all(out.stdout) if d]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def group_of(api_version):
    return api_version.split('/')[0] if '/' in api_version else ''


def plural_of(doc):
    """The plural a ref names this object by: lower-case kind + the CRD's plural rule. Widget kinds
    are checked against their CRD's real plural with --crds; here every kind the chart renders is
    listed, so a new kind fails loudly instead of being guessed."""
    return {'Flex': 'flexes', 'Card': 'cards', 'Form': 'forms', 'Markdown': 'markdowns',
            'RESTAction': 'restactions'}[doc['kind']]


class Chart:
    def __init__(self, docs):
        self.docs = docs
        self.by_key = {}
        for d in docs:
            if d['kind'] in ('Namespace', 'Role', 'RoleBinding', 'ClusterRole', 'ClusterRoleBinding'):
                continue
            key = (group_of(d['apiVersion']), plural_of(d), d['metadata'].get('namespace'), d['metadata']['name'])
            self.by_key[key] = d

    def kind(self, kind):
        return [d for d in self.docs if d['kind'] == kind]

    def get(self, kind, name):
        hits = [d for d in self.docs if d['kind'] == kind and d['metadata']['name'] == name]
        if len(hits) != 1:
            raise LookupError(f'{kind}/{name}: rendered {len(hits)} times')
        return hits[0]


def reachable(chart, ns):
    """(group, resource, name) of every object the roots reach, and the problems met on the way."""
    seen, problems = set(), []
    stack = []
    for root in ROOTS:
        stack.append((WIDGET_GROUP, 'flexes', ns, root, f'root {root}'))
    while stack:
        grp, res, rns, name, via = stack.pop()
        if rns != ns:
            problems.append(f'{via} -> {res}/{name} is in namespace {rns!r}, not the chart\'s {ns!r}')
            continue
        key = (grp, res, rns, name)
        if (grp, res, name) in seen:
            continue
        doc = chart.by_key.get(key)
        if doc is None:
            problems.append(f'{via} -> {res}/{name}: no such object is rendered (a dangling ref)')
            continue
        seen.add((grp, res, name))
        spec = doc.get('spec') or {}
        if spec.get('resourcesRefsTemplate'):
            problems.append(f'{res}/{name} has a resourcesRefsTemplate: its refs are made at request time, '
                            f'so the grant cannot be checked from the chart')
        if doc['kind'] == 'RESTAction':
            problems.append(f'restactions/{name} is reached: what its steps read must be granted too, and '
                            f'this test does not enumerate RESTAction reads yet — extend it')
        for ref in (spec.get('resourcesRefs') or {}).get('items') or []:
            stack.append((group_of(ref.get('apiVersion', '')), ref.get('resource'), ref.get('namespace'),
                          ref.get('name'), f'{res}/{name}'))
        api_ref = spec.get('apiRef')
        if api_ref:
            stack.append(('templates.krateo.io', 'restactions', api_ref.get('namespace'), api_ref.get('name'),
                          f'{res}/{name} apiRef'))
    return seen, problems


def granted(role):
    """(group, resource, name) the Role grants, and the rules that grant more than `get` by name."""
    names, problems = set(), []
    for i, rule in enumerate(role.get('rules') or []):
        if rule.get('verbs') != ['get']:
            problems.append(f'rule {i}: verbs {rule.get("verbs")}, want exactly [get]')
        if not rule.get('resourceNames'):
            problems.append(f'rule {i}: no resourceNames — it grants every object of {rule.get("resources")}')
        if '*' in (rule.get('resources') or []) or '*' in (rule.get('apiGroups') or []):
            problems.append(f'rule {i}: a wildcard')
        for grp in rule.get('apiGroups') or []:
            for res in rule.get('resources') or []:
                for name in rule.get('resourceNames') or []:
                    names.add((grp, res, name))
    return names, problems


def compare(grant, reach):
    missing = sorted(reach - grant)
    extra = sorted(grant - reach)
    return ([f'reached but NOT granted (403 for every visitor): {"/".join(m[1:])}' for m in missing]
            + [f'granted but NOT reached (published for nothing): {"/".join(e[1:])}' for e in extra])


def the_role(chart):
    roles = chart.kind('Role')
    if len(roles) != 1:
        raise LookupError(f'{len(roles)} Roles rendered, want exactly one: the public identity\'s')
    return roles[0]


# ---------------------------------------------------------------------------------------------- #

def check_rbac_is_exactly_what_the_roots_reach(chart):
    f = []
    reach, problems = reachable(chart, DEFAULT_NS)
    f += problems
    grant, rule_problems = granted(the_role(chart))
    f += rule_problems
    f += compare(grant, reach)
    # Every object the chart renders is reached: a widget nothing shows is dead weight, and one the
    # Role grants is a publication nobody reviewed.
    for (grp, res, _, name) in chart.by_key:
        if (grp, res, name) not in reach:
            f.append(f'{res}/{name} is rendered but no root reaches it')
    print(f'        reached from the roots: {sorted("/".join(x[1:]) for x in reach)}')
    print(f'        granted by the Role:    {sorted("/".join(x[1:]) for x in grant)}')
    return f


def check_the_comparison_fails_on_a_mutation(chart):
    """The control: one name removed, one name added, each must be reported."""
    f = []
    reach, _ = reachable(chart, DEFAULT_NS)
    grant, _ = granted(the_role(chart))
    if compare(grant, reach):
        return ['the unmutated Role already disagrees; the control is meaningless until check 1 passes']
    for removed in sorted(grant):
        out = compare(grant - {removed}, reach)
        if out != [f'reached but NOT granted (403 for every visitor): {"/".join(removed[1:])}']:
            f.append(f'removing {removed} was not reported as exactly one missing name: {out}')
    bogus = (WIDGET_GROUP, 'forms', 'not-a-page-widget')
    out = compare(grant | {bogus}, reach)
    if out != ['granted but NOT reached (published for nothing): forms/not-a-page-widget']:
        f.append(f'adding {bogus} was not reported as exactly one extra name: {out}')
    # The same through the Role object, so the rule parsing is in the loop too.
    role = json.loads(json.dumps(the_role(chart)))
    role['rules'][0]['resourceNames'] = role['rules'][0]['resourceNames'][1:]
    if not compare(granted(role)[0], reach):
        f.append('a Role with its first resourceName dropped compared equal')
    role = json.loads(json.dumps(the_role(chart)))
    role['rules'][0]['resourceNames'].append('page-not-public')
    if not compare(granted(role)[0], reach):
        f.append('a Role with an extra resourceName compared equal')
    role = json.loads(json.dumps(the_role(chart)))
    role['rules'][0]['verbs'] = ['get', 'list']
    if not granted(role)[1]:
        f.append('a rule granting list was not reported')
    return f


def check_bound_to_the_public_user_only(chart):
    f = []
    role = the_role(chart)
    bindings = chart.kind('RoleBinding')
    if len(bindings) != 1:
        return [f'{len(bindings)} RoleBindings rendered, want exactly one']
    b = bindings[0]
    want_subjects = [{'kind': 'User', 'name': PUBLIC_USER, 'apiGroup': 'rbac.authorization.k8s.io'}]
    if b.get('subjects') != want_subjects:
        f.append(f'subjects {b.get("subjects")}, want exactly {want_subjects}')
    if b.get('roleRef') != {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': role['metadata']['name']}:
        f.append(f'roleRef {b.get("roleRef")} is not the Role {role["metadata"]["name"]}')
    for d in chart.docs:
        for s in d.get('subjects') or []:
            if s.get('kind') == 'Group':
                f.append(f'{d["kind"]}/{d["metadata"]["name"]} binds the group {s.get("name")}')
    for kind in ('ClusterRole', 'ClusterRoleBinding'):
        if chart.kind(kind):
            f.append(f'a {kind} is rendered; public pages are granted in their namespace only')
    return f


def check_roots_are_public_routes_not_nav_entries(chart):
    f = []
    fragment = {}
    for root in ROOTS:
        ann = chart.get('Flex', root)['metadata'].get('annotations') or {}
        fragment[root] = ann.get('krateo.io/fragment-keys')
    if fragment != {'page-request-access': None, 'page-request-access-confirm': 's,t'}:
        f.append(f'fragment-keys {fragment}, want only the confirm root, with "s,t"')
    for d in chart.docs:
        nav = [k for k in (d['metadata'].get('annotations') or {}) if k.startswith('krateo.io/nav-')]
        if nav:
            f.append(f'{d["kind"]}/{d["metadata"]["name"]} carries {nav}: it would enter the signed-in sidebar')
    return f


def check_namespace_created_not_kept_and_holds_everything(_chart):
    f = []
    for ns, sets in ((DEFAULT_NS, ()), ('public-elsewhere', ('namespace=public-elsewhere',))):
        docs = render(*sets)
        nss = [d for d in docs if d['kind'] == 'Namespace']
        if [d['metadata']['name'] for d in nss] != [ns]:
            f.append(f'namespace={ns}: the chart creates {[d["metadata"]["name"] for d in nss]}, want [{ns}]')
        for d in nss:
            if 'helm.sh/resource-policy' in (d['metadata'].get('annotations') or {}):
                f.append(f'{ns}: carries helm.sh/resource-policy, but it holds no data')
        for d in docs:
            if d['kind'] != 'Namespace' and d['metadata'].get('namespace') != ns:
                f.append(f'namespace={ns}: {d["kind"]}/{d["metadata"]["name"]} renders into {d["metadata"].get("namespace")!r}')
        moved = Chart(docs)
        reach, problems = reachable(moved, ns)
        f += [f'namespace={ns}: {p}' for p in problems]
        f += [f'namespace={ns}: {p}' for p in compare(granted(the_role(moved))[0], reach)]
    return f


def forms(chart):
    return {d['metadata']['name']: d['spec']['widgetData'] for d in chart.kind('Form')}


def check_forms_post_only_to_the_intake(chart):
    f = []
    want = {'request-access': {'submit': INTAKE + 'requests', 'checks': [], 'extras': None, 'root': 'page-request-access'},
            'request-access-confirm': {'submit': INTAKE + 'confirmations', 'checks': [INTAKE + 'username-checks'],
                                       'extras': {'submission': 's', 'token': 't'}, 'root': 'page-request-access-confirm'}}
    got = forms(chart)
    if sorted(got) != sorted(want):
        return [f'forms {sorted(got)}, want {sorted(want)}']
    for name, w in want.items():
        wd = got[name]
        for key in ('actions', 'submitActionId', 'submitActionSelector', 'draftActionId'):
            if key in wd:
                f.append(f'{name}: has `{key}` — a snowplow write; these forms post to the intake only')
        if (wd.get('submitPost') or {}).get('path') != w['submit']:
            f.append(f'{name}: submitPost.path {(wd.get("submitPost") or {}).get("path")!r}, want {w["submit"]!r}')
        checks = [c.get('path') for c in wd.get('fieldChecks') or []]
        if checks != w['checks']:
            f.append(f'{name}: fieldChecks paths {checks}, want {w["checks"]}')
        declared = set(((chart.get('Flex', w['root'])['metadata'].get('annotations') or {})
                        .get('krateo.io/fragment-keys') or '').split(',')) - {''}
        for where, extras in [('submitPost', (wd.get('submitPost') or {}).get('bodyExtras'))] + \
                             [(f'fieldChecks[{c.get("name")}]', c.get('bodyExtras')) for c in wd.get('fieldChecks') or []]:
            if extras != w['extras']:
                f.append(f'{name} {where}: bodyExtras {extras}, want {w["extras"]}')
            undeclared = set((extras or {}).values()) - declared
            if undeclared:
                f.append(f'{name} {where}: sends fragment keys {sorted(undeclared)} its root does not declare')
    return f


def whens(entries):
    out = []
    for e in entries:
        w = e.get('when') or {}
        if w.get('bodyField'):
            out += [f'{s}:{v}' for s in w.get('status') or ['*'] for v in w.get('equals') or []]
        else:
            out += [str(s) for s in w.get('status') or ['*']]
    return out


SUBMIT_LIMIT = {
    'status': 'warning',
    'title': 'This link has used up its tries',
    'subtitle': 'You tried 3 usernames that were already taken. You can request access again tomorrow, once this link has expired.',
    'extra': [{'kind': 'link', 'text': 'Already have an account?', 'label': 'Sign in', 'href': '/login'}],
}
CHECK_LIMIT = "You’ve used up this link’s availability checks. You can still submit; if the name is taken we’ll tell you."
SCREEN1_429 = ("We’ve had too many requests in a short time. Wait a while, then try again.",
               "We’ve had too many requests in a short time. Try again in {retryAfter}.")


def check_every_intake_answer_is_mapped(chart):
    f = []
    got = forms(chart)
    req, conf = got['request-access'], got['request-access-confirm']
    for label, entries, want in (
            ('screen 1 submit', req['submitPost']['responses'], ['202', '400', '429']),
            ('screen 2 check', conf['fieldChecks'][0]['answers'],
             ['200:available', '200:unavailable', '200:limit', '200:expired', '400', '429']),
            ('screen 2 submit', conf['submitPost']['responses'],
             ['200:filed', '200:unavailable', '200:expired', '200:limit', '400', '429'])):
        if whens(entries) != want:
            f.append(f'{label}: answers mapped {whens(entries)}, want {want} (retry, 5xx and no answer are `otherwise`)')
    if not req['submitPost'].get('otherwise') or not conf['submitPost'].get('otherwise') or not conf['fieldChecks'][0].get('otherwise'):
        f.append('an `otherwise` is missing')
    by = {whens([e])[0]: e for e in conf['submitPost']['responses']}
    if by.get('200:limit', {}).get('result') != SUBMIT_LIMIT:
        f.append(f'submit limit result {by.get("200:limit", {}).get("result")}, want {SUBMIT_LIMIT}')
    by = {whens([e])[0]: e for e in conf['fieldChecks'][0]['answers']}
    lim = by.get('200:limit', {})
    if (lim.get('validateStatus'), lim.get('message')) != ('warning', CHECK_LIMIT):
        f.append(f'check limit {(lim.get("validateStatus"), lim.get("message"))}, want warning / {CHECK_LIMIT!r}')
    if conf.get('validationMessages') != [{'name': 'username', 'requiredMessage': 'Choose a username.'}]:
        f.append(f'empty-username copy {conf.get("validationMessages")}, want "Choose a username."')
    by = {whens([e])[0]: e for e in req['submitPost']['responses']}
    alert = by.get('429', {}).get('alert') or {}
    if (alert.get('description'), alert.get('retryAfterDescription')) != SCREEN1_429:
        f.append(f'screen 1 429 copy {(alert.get("description"), alert.get("retryAfterDescription"))}, want {SCREEN1_429}')
    # The two expired Results are one message for three cases (2.5), on both routes.
    exp = [e['result'] for e in conf['submitPost']['responses'] + conf['fieldChecks'][0]['answers']
           if whens([e]) == ['200:expired']]
    if len(exp) != 2 or exp[0] != exp[1]:
        f.append(f'the two expired Results differ or are missing: {exp}')
    return f


def countries(chart):
    schema = json.loads(forms(chart)['request-access']['stringSchema'])
    return schema['properties']['country']


def check_country_is_the_complete_iso_list(chart):
    f = []
    c = countries(chart)
    enum = c.get('enum') or []
    consts = [o.get('const') for o in c.get('oneOf') or []]
    if enum != consts:
        f.append('enum and oneOf consts differ (the Form refuses to render then)')
    if len(enum) != 249 or len(set(enum)) != 249:
        f.append(f'{len(enum)} codes ({len(set(enum))} unique), want the 249 of ISO 3166-1')
    bad = [x for x in enum if not (isinstance(x, str) and re.fullmatch(r'[A-Z]{2}', x))]
    if bad:
        f.append(f'not alpha-2 codes: {bad[:10]}')
    for code in ('NO', 'IT', 'US', 'GB', 'DE'):
        if code not in enum:
            f.append(f'{code} is missing')
    untitled = [o.get('const') for o in c.get('oneOf') or [] if not o.get('title')]
    if untitled:
        f.append(f'options with no title: {untitled[:10]}')
    try:
        import pycountry
    except ImportError:
        print('        pycountry not installed: the cross-check against it is skipped (the shape is checked)')
    else:
        want = {x.alpha_2 for x in pycountry.countries}
        if set(enum) != want:
            f.append(f'differs from pycountry: missing {sorted(want - set(enum))}, extra {sorted(set(enum) - want)}')
    return f


def check_legal_copy_comes_from_values(_chart):
    f = []
    docs = render('requestAccess.processingConsent=Acme Ltd processes this. Read our notice.',
                  'requestAccess.privacyPolicy.text=our notice',
                  'requestAccess.privacyPolicy.url=https://acme.example/privacy',
                  'requestAccess.newsletterConsent=Acme news please.')
    wd = Chart(docs).get('Form', 'request-access')['spec']['widgetData']
    props = json.loads(wd['stringSchema'])['properties']
    if props['consentProcessing']['title'] != 'Acme Ltd processes this. Read our notice.':
        f.append(f'processing consent label {props["consentProcessing"]["title"]!r} is not the value')
    if props['consentNewsletter']['title'] != 'Acme news please.':
        f.append(f'newsletter label {props["consentNewsletter"]["title"]!r} is not the value')
    link = next((c.get('link') for c in wd['checkboxes'] if c['name'] == 'consentProcessing'), None)
    if link != {'text': 'our notice', 'href': 'https://acme.example/privacy'}:
        f.append(f'privacy link {link} is not the values')
    return f



def check_newsletter_shows_no_optional_mark(chart):
    """The approved mockup draws the newsletter checkbox with no "(optional)" after it (frontend
    1.8.44's checkboxes[].hideOptionalMark). It is optional, so the Form would add the marker;
    the required processing consent never shows one, so it carries no such key."""
    f = []
    boxes = {c['name']: c for c in chart.get('Form', 'request-access')['spec']['widgetData']['checkboxes']}
    if boxes.get('consentNewsletter', {}).get('hideOptionalMark') is not True:
        f.append(f'consentNewsletter has no hideOptionalMark: true ({boxes.get("consentNewsletter")})')
    if 'hideOptionalMark' in boxes.get('consentProcessing', {}):
        f.append('consentProcessing is required: hideOptionalMark has nothing to hide there')
    return f


CHECKS = [check_rbac_is_exactly_what_the_roots_reach, check_the_comparison_fails_on_a_mutation,
          check_bound_to_the_public_user_only, check_roots_are_public_routes_not_nav_entries,
          check_namespace_created_not_kept_and_holds_everything, check_forms_post_only_to_the_intake,
          check_every_intake_answer_is_mapped, check_country_is_the_complete_iso_list,
          check_legal_copy_comes_from_values, check_newsletter_shows_no_optional_mark]


def crd_schemas(crds_dir):
    out = {}
    for path in glob.glob(os.path.join(crds_dir, 'templates', '*.crd.yaml')):
        doc = yaml.safe_load(open(path, encoding='utf-8'))
        if not doc or doc.get('kind') != 'CustomResourceDefinition' or doc['spec'].get('group') != WIDGET_GROUP:
            continue
        for ver in doc['spec']['versions']:
            out[(doc['spec']['names']['kind'], ver['name'])] = (doc['spec']['names']['plural'],
                                                                ver['schema']['openAPIV3Schema'])
    return out


def undeclared(value, schema, path=''):
    """Paths in `value` the structural schema neither declares nor preserves. A CRD schema without
    additionalProperties does not REJECT an unknown field in JSON-Schema terms (the apiserver prunes
    it, or refuses it under strict field validation), so a plain jsonschema pass would miss exactly
    the failure that matters: a key the deployed CRD does not know. Same rule as lint-pinned-crds."""
    bad = []
    if not isinstance(schema, dict) or schema.get('x-kubernetes-preserve-unknown-fields'):
        return bad
    if isinstance(value, dict):
        props = schema.get('properties') or {}
        extra = schema.get('additionalProperties')
        for key, sub in value.items():
            here = f'{path}.{key}' if path else key
            if key in props:
                bad += undeclared(sub, props[key], here)
            elif isinstance(extra, dict):
                bad += undeclared(sub, extra, here)
            elif not props and extra is None:
                continue  # an opaque object: neither properties nor additionalProperties
            elif extra in (None, False):
                bad.append(here)
    elif isinstance(value, list) and isinstance(schema.get('items'), dict):
        for i, sub in enumerate(value):
            bad += undeclared(sub, schema['items'], f'{path}[{i}]')
    return bad


def validate(chart, crds_dir):
    import jsonschema
    schemas = crd_schemas(crds_dir)
    problems = []
    widgets = [d for d in chart.docs if group_of(d['apiVersion']) == WIDGET_GROUP]
    # The validator's own control: an undeclared key must be refused, or a pass means nothing.
    form = json.loads(json.dumps(chart.get('Form', 'request-access')))
    form['spec']['widgetData']['notAFormKey'] = True
    plural, schema = schemas.get(('Form', 'v1beta1'), (None, None))
    if schema is None or 'spec.widgetData.notAFormKey' not in undeclared(form, schema):
        problems.append('an undeclared widgetData key was not reported: validation is not live')
    for d in widgets:
        key = (d['kind'], d['apiVersion'].split('/')[1])
        if key not in schemas:
            problems.append(f'{d["kind"]}/{d["metadata"]["name"]}: no CRD for {key}')
            continue
        plural, schema = schemas[key]
        if plural != plural_of(d):
            problems.append(f'{d["kind"]}: the CRD plural is {plural}, the refs and the Role say {plural_of(d)}')
        for e in jsonschema.Draft7Validator(schema).iter_errors(d):
            problems.append(f'{d["kind"]}/{d["metadata"]["name"]} {".".join(map(str, e.path))}: {e.message[:200]}')
        for path in undeclared(d, schema):
            problems.append(f'{d["kind"]}/{d["metadata"]["name"]} {path}: not declared by the CRD (widget CRDs are strict)')
    return problems, len(widgets)


def main():
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        i = args.index('--crds')
        crds_dir = args[i + 1]
        del args[i:i + 2]
    chart = Chart(render())
    failed = 0
    for check in CHECKS:
        try:
            problems = check(chart)
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, ValueError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {check.__name__.replace("check_", "").replace("_", " ")}')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    total = len(CHECKS)
    if crds_dir:
        total += 1
        problems, n = validate(chart, crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {n} rendered widgets validate against the CRDs in {crds_dir}')
        for p in dict.fromkeys(problems):
            print(f'        {p}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed{"" if crds_dir else " (widgets NOT validated: no --crds)"}')
    return failed


if __name__ == '__main__':
    sys.exit(main())
