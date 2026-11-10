#!/usr/bin/env python3
"""
test-audit-page — the /audit page (AUDIT-9, installer#415), resolved as a team member, as an admin
and as someone with no namespace, against an audit trail held in a stand-in ClickHouse.

WHAT IT GUARDS. Two RESTActions (audit-records, audit-facets) put request text AND the caller's
namespaces into ClickHouse SQL, and the rows they return are an audit trail: a row in the wrong
reader's table is a disclosure, not a cosmetic bug. `helm template` evaluates none of that jq, and a
check that only compares the SQL to a string cannot tell whether the predicate restricts anything.
So this renders the chart and resolves each RESTAction the way snowplow does:
  - every stage path is rendered by snowplow's own renderer (snowplow-path.py, the tag the
    installer pins), so the request is the one snowplow dials;
  - every userAccessFilter stage is narrowed by a port of snowplow's refilter
    (resolvers/restactions/api/refilter.go applyUserAccessFilterOnPig: resourcesFrom against the
    dict, namespaceFrom per item, a single object for a GET by name, #1124's served-resource rule,
    everything fail-closed) over an RBAC model with snowplow's evaluation rule (rbac/evaluate.go:
    a ClusterRoleBinding applies everywhere, a RoleBinding only when the check names its
    namespace);
  - every ClickHouse request is DECODED ONCE and EXECUTED against fixture rows in sqlite (a few
    ClickHouse functions translated, see FakeClickHouse), so what a reader sees is what the query
    selects, not what a fixture was told to return.
Then the widgets' own jq runs over the result, and with --crds/--pinned every widget, as rendered and
as resolved, is validated against the frontend-crds chart the installer pins.

THE ACCEPTANCE TEST (installer#415): a non-admin granted `list` on auditrecords.audit.krateo.io in
team-a only, over rows from team-a, team-b and krateo-system, sees ZERO team-b and krateo-system
rows; the admin sees all three; a caller with no grant sees zero rows and makes no ClickHouse request
at all — including one who may list every composition in team-a, which was the rule this replaced.

THE DEMO PERSONA: the portal chart's `devs` (rbac.cyberjoker-rbac.yaml) may `list` auditrecords in
demo-system and nowhere else, read off the rendered RoleBindings and run through the same stages.

Uses the `jq` binary (JQ=gojq runs the same checks on it). Needs `go` and `git` for snowplow's path
renderer (SNOWPLOW_SRC=<checkout> skips the clone of the pinned tag).
Usage: test-audit-page.py [--crds DIR | --pinned] [--print-sql]. Exit code = failed checks.
"""
import copy
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from urllib.parse import quote, unquote

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CHART = os.path.join(HERE, '..', 'helm', 'audit-page')
PORTAL_CHART = os.path.join(HERE, '..', 'helm', 'portal')   # where the demo persona's grants live


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gates = _load('gates', 'test-ra-input-gates.py')        # dependsOn order, iterator, store
tbi = gates.tbi                                           # render helpers + CRD validation
spath = gates.spath                                       # snowplow's own path renderer
rownav = _load('rownav', 'test-rownavigateto-f8.py')      # Table.tsx buildRowPath, ported

NS = 'krateo-audit'                  # the chart's default page namespace
RELEASE_NS = 'krateo-system'
TRACE_NS = 'krateo-observability'
KEY_EXTRAS = ['projects', 'range', 'auditNamespace', 'auditUser', 'auditOutcome']


def render(sets=(), values=None, chart=CHART, release='audit-page'):
    with tempfile.TemporaryDirectory() as tmp:
        staged = os.path.join(tmp, 'chart')
        shutil.copytree(chart, staged)
        meta = os.path.join(staged, 'Chart.yaml')
        text = open(meta, encoding='utf-8').read()
        open(meta, 'w', encoding='utf-8').write(text.replace('CHART_VERSION', '0.0.0-dev').replace('APP_VERSION', '0.0.0-dev'))
        cmd = ['helm', 'template', release, staged, '--namespace', RELEASE_NS]
        for value in sets:
            cmd += ['--set-string', value]
        if values is not None:
            vf = os.path.join(tmp, 'values.yaml')
            open(vf, 'w', encoding='utf-8').write(yaml.safe_dump(values))
            cmd += ['-f', vf]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode:
            raise SystemExit(f'helm template failed:\n{proc.stderr.strip()}')
        return tbi.Chart([d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)])


# ---------------------------------------------------------------------------------------------
# RBAC, and snowplow's userAccessFilter refilter over it
# ---------------------------------------------------------------------------------------------

class Caller:
    """A requester and the rules bound to it: (verb, group, resource, namespace), namespace '' for a
    ClusterRoleBinding. '*' matches anything, as in a Role rule."""

    def __init__(self, name, rules):
        self.name, self.rules = name, rules

    def can(self, verb, group, resource, namespace):
        for v, g, r, ns in self.rules:
            if v not in ('*', verb) or g not in ('*', group) or r not in ('*', resource):
                continue
            # rbac/evaluate.go:458-462: ClusterRoleBindings apply cluster-wide; RoleBindings are
            # consulted only when the check names a namespace, and only in that namespace.
            if ns == '' or (namespace != '' and ns == namespace):
                return True
        return False


ADMIN = Caller('admin', [('*', '*', '*', '')])                       # cluster-admin, via CRB
# The grant this page reads: `list` on auditrecords.audit.krateo.io, from a RoleBinding in team-a.
TEAM_A = Caller('j.doe', [('list', 'audit.krateo.io', 'auditrecords', 'team-a')])
NOBODY = Caller('nobody', [])
# The same grant in two namespaces, and none on the observability page set, so no trace link
# resolves for it.
TEAM_AB = Caller('m.ross', [('list', 'audit.krateo.io', 'auditrecords', 'team-a'),
                            ('list', 'audit.krateo.io', 'auditrecords', 'team-b')])
# Everything the old rule asked for in team-a — the CompositionDefinitions and every composition
# kind — and broad reads besides, but no audit grant. Sees nothing.
COMPOSITIONS_ONLY = Caller('c.lister', [('list', 'core.krateo.io', 'compositiondefinitions', 'team-a'),
                                         ('list', 'composition.krateo.io', '*', 'team-a'),
                                         ('*', 'apps', '*', 'team-a'), ('*', '', '*', 'team-a')])


def _jq_value(expr, item):
    try:
        out = tbi.jq(expr, item)
    except (RuntimeError, ValueError):
        return None
    return '' if out is None else out


def refilter(step, raw, dict_, caller):
    """snowplow's applyUserAccessFilterOnPig for one stage response (refilter.go:951)."""
    uaf = step.get('userAccessFilter')
    if not uaf:
        return raw
    if uaf.get('resourcesFrom'):
        try:
            res = tbi.jq(uaf['resourcesFrom'], dict_)
        except (RuntimeError, ValueError):
            res = None
        if not isinstance(res, list) or any(x is not None and not isinstance(x, str) for x in res):
            return {'items': []}                                     # unresolved: fail closed
        resources = list(dict.fromkeys(x for x in res if x))
    else:
        resources = [uaf.get('resource', '')]
    parsed = gates.apiserver_path(step.get('path'))
    if parsed:                                                       # #1124 uafItemResources
        prefix, _, served, _ = parsed
        group = '' if prefix.startswith('/api/') else prefix.split('/')[2]
        if group == uaf.get('group') and served in resources:
            resources = [served]
    ns_expr = uaf.get('namespaceFrom') or '.metadata.namespace'
    name_specific = uaf.get('verb') in ('get', 'update', 'patch', 'delete')

    def allowed(item):
        ns = _jq_value(ns_expr, item)
        if ns is None or not isinstance(ns, str):
            return False
        if name_specific and _jq_value(uaf.get('nameFrom') or '.metadata.name', item) is None:
            return False
        return any(caller.can(uaf['verb'], uaf.get('group', ''), r, ns) for r in resources)

    if isinstance(raw, dict) and 'items' not in raw:                 # a GET by name
        return raw if allowed(raw) else {}
    if isinstance(raw, dict):
        return dict(raw, items=[i for i in raw.get('items') or [] if allowed(i)])
    if isinstance(raw, list):
        return [i for i in raw if allowed(i)]
    return raw


def resolve(ra, extras, responses, caller=ADMIN):
    """(requests, dict, output) — test-ra-input-gates' resolver, with the refilter above and with a
    response that may be computed from the request: `responses[step]` is a value, or a function of
    the path returning one (None = the call failed)."""
    reserved = {s['name'] for s in ra['spec']['api']} | {s.get('errorKey', 'error') for s in ra['spec']['api']} | {'slice'}
    if reserved & set(extras):
        # snowplow #1116 (reserved_extras.go) refuses the request with a 400: nothing is resolved.
        raise PermissionError(f'reserved extras {sorted(reserved & set(extras))}')
    d, requests = copy.deepcopy(extras), []
    for step in gates.ordered(ra['spec']['api']):
        name, it = step['name'], (step.get('dependsOn') or {}).get('iterator')
        if it:
            items = tbi.jq(it, d)
            items = items if isinstance(items, list) else []
        else:
            items = [d]
        for item in items:
            try:
                path = spath.render(step.get('path'), item)
            except spath.Refused:
                continue
            requests.append((name, path))
            resp = responses.get(name)
            resp = resp(path) if callable(resp) else copy.deepcopy(resp)
            if resp is None:
                if not step.get('continueOnError'):
                    return requests, d, None
                d.setdefault(step.get('errorKey', 'error'), []).append(f'{path}: failed')
                continue
            pig = {name: refilter(step, resp, d, caller)}
            if extras:
                pig['extras'] = extras
            gates.store(d, name, tbi.jq(step['filter'], pig) if step.get('filter') else pig[name],
                        bool(step.get('filter')))
    return requests, d, tbi.jq(ra['spec']['filter'], d)


# ---------------------------------------------------------------------------------------------
# A stand-in ClickHouse: the decoded SQL, executed in sqlite
# ---------------------------------------------------------------------------------------------

NOW_MS = 1794218652000                    # 2026-11-09 10:04:12 UTC
H = 3600 * 1000
SNOW = 'github.com/krateo-platformops/snowplow/audit'
RDC = 'rest-dynamic-controller/audit'
TRACE_KEPT_A = '4f1c9e2a7b3d4c5e8f9a0b1c2d3ea07e'
TRACE_KEPT_SYS = '9b30aa55c1d24e6f8a7b6c5d4e3f11c2'
TRACE_GONE = 'e7d24c1b9a8f4e3d2c1b0a9f8e7d5a19'      # sampled out: not in otel_traces
TRACE_OLD = '71b5e2d3c4b5a69788776655443309de'       # kept, but 9 days ago


def rec(ago_ms, ns, user, method, resource, name, outcome, code, group='', kind='', action='call',
        trace='', scope=SNOW, extra=None):
    attrs = {'event.name': 'audit', 'user.name': user, 'k8s.namespace.name': ns, 'k8s.resource.group': group,
             'k8s.resource.resource': resource, 'k8s.resource.kind': kind, 'k8s.resource.name': name,
             'krateo.action': action, 'http.request.method': method, 'outcome': outcome,
             'http.response.status_code': str(code) if code else ''}
    attrs = {k: v for k, v in attrs.items() if v != ''}      # the library omits an unset field
    if not trace:
        attrs['krateo.audit.no_trace_context'] = 'true'
    attrs.update(extra or {})
    return {'ScopeName': scope, 'Timestamp': NOW_MS - ago_ms, 'TraceId': trace, 'LogAttributes': attrs}


# The mockup's rows, plus every population this page must keep out. Newest first.
AUDIT = [
    rec(0, 'krateo-system', 'admin', 'PATCH', 'alerts', 'acmp-new-alert-check', 'success', 200,
        group='observability.krateo.io', trace=TRACE_KEPT_SYS),
    rec(22 * 60 * 1000 + 17000, 'team-a', 'j.doe', 'POST', 'githubrepos', 'storefront-web', 'success', 201,
        group='github.krateo.io', trace=TRACE_KEPT_A),
    rec(24 * 60 * 1000 + 10000, 'team-a', 'j.doe', 'POST', 'githubrepos', 'storefront-web', 'failure', 409,
        group='github.krateo.io', trace=TRACE_GONE),
    # rest-dynamic-controller, as it writes on krateo-057: Kind only, an external host, NO user.name and
    # no trace context.
    rec(2 * H, 'team-a', '', 'POST', '', 'storefront-web',
        'success', 201, group='github.krateo.io', kind='Repo', action='rest.write', scope=RDC,
        extra={'server.address': 'api.github.com'}),
    rec(3 * H, 'team-b', 'ci-bot', 'PATCH', 'labels', 'release-tag', 'failure', 403),
    rec(4 * H, 'demo-system', 'admin', 'DELETE', 'compositions', 'old-demo', 'success', 200,
        group='composition.krateo.io', trace=TRACE_GONE),
    rec(5 * H, '', 'admin', 'POST', 'clusterroles', 'audit-reader', 'success', 201,
        group='rbac.authorization.k8s.io', trace=TRACE_GONE),            # cluster-scoped: no namespace
    rec(6 * H, 'old-team', 'admin', 'DELETE', 'githubrepos', 'gone', 'success', 200,
        group='github.krateo.io'),                                       # a namespace deleted since
    rec(7 * H, 'team-a', 'j.doe', 'GET', 'tables', 'audit-records', 'success', 200,
        group='widgets.templates.krateo.io', action='export'),           # an export: a read
    rec(8 * H, 'team-a', 'j.doe', '', 'githubrepos', 'storefront-web', 'denied', 0,
        group='github.krateo.io'),                                       # no method recorded
    # Previews: never shown, counted in the note.
    rec(9 * H, 'team-a', 'j.doe', 'POST', 'githubrepos', 'preview-repo', 'success', 201,
        group='github.krateo.io', action='call.dryRun'),
    rec(10 * H, 'team-b', 'ci-bot', 'POST', 'labels', 'preview-tag', 'success', 201,
        extra={'krateo.audit.dry_run': 'true'}),
    # Not audit records: the gateway access log and acmp's c8 test rows. Never shown.
    {'ScopeName': 'request', 'Timestamp': NOW_MS - 11 * H, 'TraceId': TRACE_KEPT_A,
     'LogAttributes': {'user': 'admin', 'k8s.namespace.name': 'team-a', 'http.request.method': 'POST'}},
    rec(12 * H, 'team-a', 'acmp', 'POST', 'githubrepos', 'c8-probe', 'success', 201, scope='krateo-acmp/c8'),
    # Outside every window this page offers, and one only the 7d and 30d windows reach.
    rec(40 * 24 * H, 'team-a', 'j.doe', 'POST', 'githubrepos', 'ancient', 'success', 201, group='github.krateo.io'),
    rec(9 * 24 * H, 'team-a', 'j.doe', 'PUT', 'githubrepos', 'last-week', 'success', 200,
        group='github.krateo.io', trace=TRACE_OLD),
    # A record missing user.name: shown, with "—", and counted as incomplete.
    rec(13 * H, 'team-b', '', 'PATCH', 'labels', 'nameless', 'success', 200),
]
TRACES = [TRACE_KEPT_A, TRACE_KEPT_SYS, TRACE_OLD]
TRACES_AGE = {TRACE_OLD: 9 * 24 * H}

NO_GRANT_NOTE = ("No namespace's audit trail has been shared with you, so there are no records to show. "
                 "Ask an administrator for access to a namespace.")
INT64 = {'ts_ms', 'matched', 'n', 'total', 'previews', 'unread', 'linked', 'untraced'}


def _balanced(s, start):
    """The index of the parenthesis closing the one at `start`, skipping string literals."""
    depth, i = 0, start
    while i < len(s):
        if s[i] == "'":
            i = s.index("'", i + 1)
        elif s[i] == '(':
            depth += 1
        elif s[i] == ')':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f'unbalanced: {s[start:]}')


def to_sqlite(sql):
    """The handful of ClickHouse spellings these queries use, in sqlite. Anything else is sent as
    written, so a query this cannot translate fails here rather than passing on a guess."""
    assert sql.endswith(' FORMAT JSON'), sql
    s = sql[:-len(' FORMAT JSON')]
    s = s.replace('toUnixTimestamp64Milli(Timestamp)', 'Timestamp')
    s = re.sub(r"LogAttributes\['([^']*)'\]", r"attr(LogAttributes, '\1')", s)
    s = re.sub(r'now\(\) - INTERVAL (\d+) (HOUR|DAY)',
               lambda m: f'({NOW_MS} - {int(m.group(1)) * (H if m.group(2) == "HOUR" else 24 * H)})', s)
    s = s.replace('count() OVER ()', 'count(*) OVER ()').replace('count()', 'count(*)')
    while 'countIf(' in s:
        i = s.index('countIf(')
        j = _balanced(s, i + len('countIf'))
        s = s[:i] + f'sum(CASE WHEN {s[i + len("countIf("):j]} THEN 1 ELSE 0 END)' + s[j + 1:]
    return s


class FakeClickHouse:
    def __init__(self, rows=AUDIT, traces=TRACES, quoted=True):
        self.db = sqlite3.connect(':memory:')
        self.db.create_function('attr', 2, lambda js, k: json.loads(js).get(k, ''))
        self.db.create_function('endsWith', 2, lambda a, b: int((a or '').endswith(b)))
        self.db.execute('CREATE TABLE krateo_audit (ScopeName TEXT, Timestamp INTEGER, TraceId TEXT, LogAttributes TEXT)')
        self.db.execute('CREATE TABLE otel_traces (TraceId TEXT, Timestamp INTEGER)')
        for r in rows:
            self.db.execute('INSERT INTO krateo_audit VALUES (?, ?, ?, ?)',
                            (r['ScopeName'], r['Timestamp'], r['TraceId'], json.dumps(r['LogAttributes'])))
        for t in traces:
            self.db.execute('INSERT INTO otel_traces VALUES (?, ?)', (t, NOW_MS - TRACES_AGE.get(t, H)))
        self.quoted, self.queries = quoted, []

    def __call__(self, path):
        assert path.startswith('/?query='), path
        sql = unquote(path[len('/?query='):])
        self.queries.append(sql)
        cur = self.db.execute(to_sqlite(sql))
        cols = [c[0] for c in cur.description]
        data = []
        for row in cur.fetchall():
            data.append({c: (str(v) if (self.quoted and c in INT64 and isinstance(v, int)) else v)
                         for c, v in zip(cols, row)})
        return {'meta': [{'name': c} for c in cols], 'data': data, 'rows': len(data)}


# ---------------------------------------------------------------------------------------------
# apiserver fixtures, as snowplow's ServiceAccount reads them (unnarrowed)
# ---------------------------------------------------------------------------------------------

APISERVER = {
    'namespaces': {'items': [{'metadata': {'name': n}} for n in
                             ['team-a', 'team-b', 'krateo-system', 'demo-system', NS, TRACE_NS]]},
    'allNamespaces': {'metadata': {'name': NS}},
    'tracePage': {'metadata': {'name': 'page-trace-detail', 'namespace': TRACE_NS}},
}


def served(ch=None):
    out = dict(APISERVER)
    ch = ch or FakeClickHouse()
    out.update({'rows': ch, 'summary': ch, 'facets': ch})
    return out, ch


def records(c, extras, caller=ADMIN, ch=None):
    responses, ch = served(ch)
    reqs, _, out = resolve(c.get('RESTAction', 'audit-records'), extras, responses, caller)
    return [r for r in reqs if r[0] in ('rows', 'summary')], out, ch


def facets(c, extras, caller=ADMIN, ch=None):
    responses, ch = served(ch)
    reqs, _, out = resolve(c.get('RESTAction', 'audit-facets'), extras, responses, caller)
    return [r for r in reqs if r[0] == 'facets'], out


def table(c, out, extras, label):
    return tbi.resolved_widget(c, 'Table', 'audit-records', out, extras, label)['spec']['widgetData']['dataSource']


def cells(row):
    return {x['valueKey']: x for x in row}


def col(rows, key):
    return [cells(r)[key]['stringValue'] for r in rows]


def expect(problems, label, got, want):
    if got != want:
        problems.append(f'{label}: got {got!r}, want {want!r}')


def sql_of(path):
    assert path.startswith('/?query='), path
    return unquote(path[len('/?query='):])


# The SQL this page sends, written out independently of the chart's defines.
POP = "ScopeName IN ('github.com/krateo-platformops/snowplow/audit', 'rest-dynamic-controller/audit')"
DRY = "(LogAttributes['krateo.audit.dry_run'] = 'true' OR endsWith(LogAttributes['krateo.action'], '.dryRun'))"
KEPT = ("(TraceId != '' AND TraceId IN (SELECT TraceId FROM otel_traces WHERE Timestamp > now() - INTERVAL 7 DAY "
        "AND TraceId IN (SELECT TraceId FROM krateo_audit WHERE " + POP +
        " AND Timestamp > now() - INTERVAL 7 DAY AND TraceId != '')))")
UNREAD = ("((LogAttributes['user.name'] = '' AND ScopeName != 'rest-dynamic-controller/audit') OR "
          "LogAttributes['k8s.namespace.name'] = '' OR "
          "(LogAttributes['k8s.resource.resource'] = '' AND LogAttributes['k8s.resource.kind'] = '' AND "
          "LogAttributes['server.address'] = '') OR LogAttributes['outcome'] NOT IN ('success', 'failure', 'denied'))")
ROWS_SQL = ("SELECT toUnixTimestamp64Milli(Timestamp) AS ts_ms, ScopeName AS scope, LogAttributes['user.name'] AS usr, "
            "LogAttributes['krateo.action'] AS act, LogAttributes['http.request.method'] AS method, "
            "LogAttributes['k8s.resource.resource'] AS res, LogAttributes['k8s.resource.kind'] AS kind, "
            "LogAttributes['k8s.resource.group'] AS grp, LogAttributes['k8s.resource.name'] AS name, "
            "LogAttributes['k8s.namespace.name'] AS ns, LogAttributes['outcome'] AS outcome, "
            "LogAttributes['http.response.status_code'] AS code, LogAttributes['server.address'] AS server, "
            "LogAttributes['krateo.audit.no_trace_context'] AS no_trace, TraceId AS trace_id, "
            + KEPT + " AS kept, count() OVER () AS matched FROM krateo_audit WHERE " + POP +
            " AND Timestamp > now() - INTERVAL {window} AND NOT " + DRY + "{access}{cond} "
            "ORDER BY Timestamp DESC LIMIT 200 FORMAT JSON")
SUMMARY_SQL = ("SELECT count() AS total, countIf(" + DRY + ") AS previews, countIf(NOT " + DRY + " AND " + UNREAD +
               ") AS unread, countIf(NOT " + DRY + " AND " + KEPT + ") AS linked, countIf(NOT " + DRY +
               " AND LogAttributes['krateo.audit.no_trace_context'] = 'true') AS untraced FROM krateo_audit WHERE " +
               POP + " AND Timestamp > now() - INTERVAL {window}{access}{cond} FORMAT JSON")


def facet_branch(i, dim, key, window, access):
    return (('' if i == 0 else ' UNION ALL ') + f"SELECT * FROM (SELECT '{dim}' AS dim, LogAttributes['{key}'] AS val, "
            f"count() AS n FROM krateo_audit WHERE {POP} AND Timestamp > now() - INTERVAL {window} AND NOT {DRY}{access} "
            "GROUP BY val ORDER BY n DESC, val LIMIT 12)")


def facets_sql(window='24 HOUR', access=''):
    return ''.join(facet_branch(i, d, k, window, access) for i, (d, k) in
                   enumerate([('ns', 'k8s.namespace.name'), ('user', 'user.name'), ('outcome', 'outcome')])) + ' FORMAT JSON'


def acc(*names):
    return " AND LogAttributes['k8s.namespace.name'] IN (" + ', '.join(f"'{n}'" for n in names) + ')'


# ---------------------------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------------------------

def check_negative_test(c):
    """THE ACCEPTANCE TEST. Rows from team-a, team-b and krateo-system (and more) are in the trail;
    each caller's table holds exactly the namespaces whose audit trail its own RBAC grants it."""
    p = []
    _, out, _ = records(c, {}, TEAM_A)
    member = col(table(c, out, {}, 'audit-records (team-a member)'), 'namespace')
    expect(p, 'team-a member: namespaces in the table', sorted(set(member)), ['team-a'])
    expect(p, 'team-a member: team-b rows', member.count('team-b'), 0)
    expect(p, 'team-a member: krateo-system rows', member.count('krateo-system'), 0)
    if not member:
        p.append('team-a member: no rows at all — a pass on an empty table measures nothing')
    expect(p, 'team-a member: scope', out['scope'], 'namespaces')
    _, out, _ = records(c, {}, ADMIN)
    admin = col(table(c, out, {}, 'audit-records (admin)'), 'namespace')
    for ns in ('team-a', 'team-b', 'krateo-system'):
        if ns not in admin:
            p.append(f'admin: no {ns} row')
    expect(p, 'admin: a cluster-scoped record and one of a deleted namespace are theirs too',
           ('—' in admin, 'old-team' in admin), (True, True))
    expect(p, 'admin: scope', out['scope'], 'all')
    reqs, out, ch = records(c, {}, NOBODY)
    expect(p, 'no namespace: ClickHouse requests', reqs, [])
    expect(p, 'no namespace: rows', table(c, out, {}, 'audit-records (no namespace)'), [])
    expect(p, 'no namespace: the note says why', out['note'],
           NO_GRANT_NOTE)
    # Choosing another namespace in the URL narrows further; it never widens.
    _, out, _ = records(c, {'auditNamespace': 'team-b'}, TEAM_A)
    expect(p, 'team-a member asking for team-b: rows', out['rows'], [])
    _, out, _ = records(c, {'auditNamespace': 'team-a'}, TEAM_A)
    expect(p, 'team-a member asking for team-a', sorted({r['namespace'] for r in out['rows']}), ['team-a'])
    # The chips can say nothing about namespaces the member cannot read.
    _, fac = facets(c, {}, TEAM_A)
    expect(p, 'team-a member: namespace chips counted', [x['name'] for x in fac['facets']['namespaces']], ['team-a'])
    if 'ci-bot' in [x['name'] for x in fac['facets']['users']]:
        p.append('team-a member: the user chips name a team-b user')
    _, fac = facets(c, {}, NOBODY)
    expect(p, 'no namespace: facets', (fac['scope'], fac['facets']['namespaces']), ('none', []))
    return p


def check_access_predicate_is_the_callers_own(c):
    p = []
    reqs, _, _ = records(c, {}, TEAM_A)
    expect(p, 'team-a member: rows SQL', sql_of(reqs[0][1]),
           ROWS_SQL.format(window='24 HOUR', access=acc('team-a'), cond=''))
    expect(p, 'team-a member: summary SQL', sql_of(reqs[1][1]),
           SUMMARY_SQL.format(window='24 HOUR', access=acc('team-a'), cond=''))
    reqs, _, _ = records(c, {}, TEAM_AB)
    expect(p, 'two namespaces: one IN list', sql_of(reqs[0][1]),
           ROWS_SQL.format(window='24 HOUR', access=acc('team-a', 'team-b'), cond=''))
    reqs, _, _ = records(c, {}, ADMIN)
    expect(p, 'admin: no namespace predicate', sql_of(reqs[0][1]), ROWS_SQL.format(window='24 HOUR', access='', cond=''))
    reqs, _ = facets(c, {}, TEAM_A)
    expect(p, 'team-a member: facets SQL', [sql_of(r[1]) for r in reqs], [facets_sql(access=acc('team-a'))])
    # Every access stage fails closed: a failing namespaces stage leaves a member with nothing to
    # read and no request made.
    for broken in ('namespaces',):
        responses, _ = served()
        responses[broken] = lambda path: None
        r, _, out = resolve(c.get('RESTAction', 'audit-records'), {}, responses, TEAM_A)
        expect(p, f'{broken} failing: ClickHouse requests', [x for x in r if x[0] in ('rows', 'summary')], [])
        expect(p, f'{broken} failing: scope', out['scope'], 'none')
    responses, _ = served()
    responses['allNamespaces'] = lambda path: None
    r, _, out = resolve(c.get('RESTAction', 'audit-records'), {}, responses, ADMIN)
    expect(p, 'allNamespaces failing for an admin: narrowed to the namespaces it holds the grant in, not widened',
           sql_of([x for x in r if x[0] == 'rows'][0][1]),
           ROWS_SQL.format(window='24 HOUR', access=acc('demo-system', 'krateo-audit', 'krateo-observability',
                                                        'krateo-system', 'team-a', 'team-b'), cond=''))
    # A request cannot stand in for a stage: snowplow refuses extras that name one (#1116).
    for key in ('namespaces', 'allNamespaces', 'tracePage', 'rows', 'summary'):
        try:
            records(c, {key: True}, TEAM_A)
            p.append(f'extras {key}: resolved')
        except PermissionError:
            pass
    # A namespace name is checked again before it goes into SQL.
    responses, _ = served()
    responses['namespaces'] = {'items': [{'metadata': {'name': "team-a') OR ('1'='1"}}, {'metadata': {'name': 'team-a'}}]}
    r, _, _ = resolve(c.get('RESTAction', 'audit-records'), {}, responses, Caller('x', [('*', '*', '*', "team-a') OR ('1'='1"),
                                                                                       ('*', '*', '*', 'team-a')]))
    r = [x for x in r if x[0] == 'rows']
    expect(p, 'a namespace name that is not one is dropped before SQL', sql_of(r[0][1]) if r else None,
           ROWS_SQL.format(window='24 HOUR', access=acc('team-a'), cond=''))
    return p


def check_scope_is_the_explicit_audit_grant(c):
    """Diego's rule (installer#415, 2026-11-09): a namespace is in the caller's list iff the caller
    may `list` auditrecords.audit.krateo.io there, and "all" iff it may do so cluster-wide. Nothing
    else counts — not compositions, not broad reads."""
    p = []
    reqs, out, _ = records(c, {}, COMPOSITIONS_ONLY)
    expect(p, 'compositions but no audit grant in team-a: ClickHouse requests', reqs, [])
    expect(p, 'compositions but no audit grant in team-a: rows', out['rows'], [])
    expect(p, 'compositions but no audit grant in team-a: the note', out['note'], NO_GRANT_NOTE)
    freqs, fac = facets(c, {}, COMPOSITIONS_ONLY)
    expect(p, 'compositions but no audit grant: facets', (freqs, fac['scope']), ([], 'none'))
    hdr = tbi.resolved_widget(c, 'PageHeader', 'audit-page-header', fac, {}, 'header (compositions, no grant)')['spec']['widgetData']
    expect(p, 'what someone with no grant reads', hdr['subtitle'],
           'You see records for namespaces where you have been granted access to the audit trail, and you have '
           'none yet. Ask an administrator for access to a namespace.')
    lister = Caller('composition-lister', [('list', 'core.krateo.io', 'compositiondefinitions', ''),
                                           ('list', 'composition.krateo.io', '*', '')])
    reqs, out, _ = records(c, {}, lister)
    expect(p, 'every composition kind cluster-wide, no audit grant', (out['scope'], reqs), ('none', []))
    auditor = Caller('auditor', [('list', 'audit.krateo.io', 'auditrecords', '')])
    reqs, out, _ = records(c, {}, auditor)
    expect(p, 'auditrecords cluster-wide: "all", no namespace predicate', (out['scope'], sql_of(reqs[0][1]) if reqs else None),
           ('all', ROWS_SQL.format(window='24 HOUR', access='', cond='')))
    ns_auditor = Caller('ns-auditor', [('list', 'audit.krateo.io', 'auditrecords', 'team-b')])
    reqs, out, _ = records(c, {}, ns_auditor)
    expect(p, 'auditrecords in team-b only: team-b, not "all"', (out['scope'], sql_of(reqs[0][1]) if reqs else None),
           ('namespaces', ROWS_SQL.format(window='24 HOUR', access=acc('team-b'), cond='')))
    other_verb = Caller('getter', [('get', 'audit.krateo.io', 'auditrecords', 'team-a'),
                                   ('list', 'audit.krateo.io', 'otherrecords', 'team-a')])
    reqs, out, _ = records(c, {}, other_verb)
    expect(p, 'get (not list) on auditrecords, or list on another kind: nothing', (out['scope'], reqs), ('none', []))
    # The admin needs no composition and no namespaced grant: cluster-admin covers the cluster-scope check.
    responses, _ = served()
    reqs, _, out = resolve(c.get('RESTAction', 'audit-records'), {}, responses, ADMIN)
    expect(p, 'admin: every record', len(out['rows']), 11)
    return p


def group_rules(docs, group):
    """Every rule bound to `group` in a rendered chart, as Caller rules (verb, apiGroup, resource,
    namespace) with namespace '' for a ClusterRoleBinding — the same shape snowplow's RBAC model
    evaluates. A RoleBinding may reference a ClusterRole; it still applies only in its namespace."""
    roles = {(d['kind'], (d['metadata'].get('namespace') or ''), d['metadata']['name']): d
             for d in docs if d.get('kind') in ('Role', 'ClusterRole')}
    out = []
    for b in docs:
        if b.get('kind') not in ('RoleBinding', 'ClusterRoleBinding'):
            continue
        if not any(s.get('kind') == 'Group' and s.get('name') == group for s in b.get('subjects') or []):
            continue
        ns = (b['metadata'].get('namespace') or '') if b['kind'] == 'RoleBinding' else ''
        ref = b['roleRef']
        role = roles.get((ref['kind'], ns if ref['kind'] == 'Role' else '', ref['name']))
        if role is None:
            raise LookupError(f'{b["kind"]} {b["metadata"]["name"]} references {ref["kind"]} {ref["name"]}, which the chart does not render')
        for rule in role.get('rules') or []:
            for v in rule.get('verbs') or []:
                for g in rule.get('apiGroups') or []:
                    for r in rule.get('resources') or []:
                        out.append((v, g, r, ns))
    return out


def check_portal_grants_devs_the_demo_audit_trail(c):
    """The portal chart's demo persona (group `devs`, rbac.cyberjoker-rbac.yaml) may `list`
    auditrecords.audit.krateo.io in demo-system and nowhere else, so /audit shows a dev demo-system's
    records and only those. Read off the RENDERED RoleBindings and the Roles they reference, any rule
    that could reach auditrecords counted (a '*' verb, group or resource included), then run through
    the page's own access stages as that persona."""
    p = []
    on = render(values={'enableCyberjokerUser': True}, chart=PORTAL_CHART, release='portal')
    rules = group_rules(on.docs, 'devs')
    reach = sorted(r for r in rules if r[1] in ('*', 'audit.krateo.io') and r[2] in ('*', 'auditrecords'))
    expect(p, 'what devs may do on auditrecords: list, in demo-system only',
           reach, [('list', 'audit.krateo.io', 'auditrecords', 'demo-system')])
    expect(p, 'devs bound cluster-wide by the portal chart', sorted(r for r in rules if r[3] == ''), [])
    role = on.get('Role', 'devs-list-auditrecords-in-demo-namespace')
    expect(p, 'the Role, exactly', (role['metadata']['namespace'], role['rules']),
           ('demo-system', [{'apiGroups': ['audit.krateo.io'], 'resources': ['auditrecords'], 'verbs': ['list']}]))
    rb = on.get('RoleBinding', 'devs-list-auditrecords-in-demo-namespace')
    expect(p, 'the RoleBinding, exactly', (rb['metadata']['namespace'], rb['roleRef'], rb['subjects']),
           ('demo-system', {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': 'devs-list-auditrecords-in-demo-namespace'},
            [{'kind': 'Group', 'name': 'devs', 'apiGroup': 'rbac.authorization.k8s.io'}]))
    dev = Caller('cyberjoker', rules)
    reqs, out, _ = records(c, {}, dev)
    expect(p, 'a dev on /audit: demo-system\'s records, by the namespace predicate',
           (out['scope'], [sql_of(r[1]) for r in reqs if r[0] == 'rows']),
           ('namespaces', [ROWS_SQL.format(window='24 HOUR', access=acc('demo-system'), cond='')]))
    # Driven by the same switches as the other demo-system grants: off with either of them.
    for label, values in (('enableCyberjokerUser off (the chart default)', {}),
                          ('enableDemoSystemNamespace off', {'enableCyberjokerUser': True, 'enableDemoSystemNamespace': False})):
        off = render(values=values, chart=PORTAL_CHART, release='portal')
        expect(p, f'{label}: devs on auditrecords', sorted(r for r in group_rules(off.docs, 'devs')
                                                           if r[1] in ('*', 'audit.krateo.io') and r[2] in ('*', 'auditrecords')), [])
    return p


def check_population_and_previews(c):
    p = []
    _, out, ch = records(c, {}, ADMIN)
    names = [r['name'] for r in out['rows']]
    for gone, why in [('c8-probe', 'krateo-acmp/c8'), ('preview-repo', 'a call.dryRun preview'),
                      ('preview-tag', 'a krateo.audit.dry_run preview'), ('ancient', 'a 40-day-old record'),
                      ('last-week', 'a 9-day-old record in the 24h window')]:
        if gone in names:
            p.append(f'{why} is in the table')
    if any(r['user'] == '' and r['namespace'] == 'team-a' and r['method'] == 'POST' and r['action'] == '' for r in out['rows']):
        p.append('the gateway access log (ScopeName request) is in the table')
    expect(p, 'rows: the two audit scopes', len(out['rows']), 11)
    expect(p, 'previews counted', out['counts']['previews'], 2)
    if '2 previews (dry runs) are not shown.' not in out['note']:
        p.append(f'the note does not count the previews: {out["note"]!r}')
    _, out7, _ = records(c, {'range': '7d'}, ADMIN)
    _, out30, _ = records(c, {'range': '30d'}, ADMIN)
    expect(p, '7d does not reach 9 days', 'last-week' in [r['name'] for r in out7['rows']], False)
    expect(p, '30d reaches 9 days, not 40', ('last-week' in [r['name'] for r in out30['rows']],
                                             'ancient' in [r['name'] for r in out30['rows']]), (True, False))
    return p


def check_columns_map_to_the_record(c):
    p = []
    _, out, _ = records(c, {}, ADMIN)
    tbl = c.get('Table', 'audit-records')['spec']['widgetData']
    expect(p, 'columns', [x['title'] for x in tbl['columns']],
           ['Time (UTC)', 'User', 'Action', 'Resource', 'Name', 'Namespace', 'Outcome', 'Trace'])
    expect(p, 'pages of 25', tbl['pagination'], {'pageSize': 25, 'hideOnSinglePage': True})
    rows = table(c, out, {}, 'audit-records (mapping)')
    by = {cells(r)['name']['stringValue'] + '/' + cells(r)['outcome']['stringValue']: cells(r) for r in rows}
    first = cells(rows[0])
    expect(p, 'newest first, UTC to the second', col(rows, 'time')[:2], ['2026-11-09 10:04:12', '2026-11-09 09:41:55'])
    expect(p, 'times descend', col(rows, 'time'), sorted(col(rows, 'time'), reverse=True))
    expect(p, 'PATCH is Update; plural and group', (first['user']['stringValue'], first['action']['stringValue'],
                                                    first['resource']['stringValue'], first['name']['stringValue'],
                                                    first['namespace']['stringValue']),
           ('admin', 'Update', 'alerts · observability.krateo.io', 'acmp-new-alert-check', 'krateo-system'))
    expect(p, 'outcome pill', (first['outcome']['kind'], first['outcome']['stringValue'], first['outcome']['color']),
           ('tag', 'success · 200', 'green'))
    expect(p, 'POST is Create; failure is red', (by['storefront-web/failure · 409']['action']['stringValue'],
                                                 by['storefront-web/failure · 409']['outcome']['color']), ('Create', 'red'))
    rdc = [cells(r) for r in rows if cells(r)['resource']['stringValue'].startswith('Repo ')][0]
    expect(p, 'rest-dynamic-controller: no user.name shows "—"', rdc['user']['stringValue'], '—')
    expect(p, 'rest-dynamic-controller: the Kind, its group and the host it wrote to', rdc['resource']['stringValue'],
           'Repo · github.krateo.io, on api.github.com')
    expect(p, 'a core resource names its group "core"', by['release-tag/failure · 403']['resource']['stringValue'], 'labels · core')
    expect(p, 'denied is orange; no method is "—", never a guess',
           (by['storefront-web/denied']['outcome']['color'], by['storefront-web/denied']['action']['stringValue'],
            by['storefront-web/denied']['outcome']['stringValue']), ('orange', '—', 'denied'))
    expect(p, 'DELETE is Delete', by['old-demo/success · 200']['action']['stringValue'], 'Delete')
    expect(p, 'an export is named for what it is, not by its method', by['audit-records/success · 200']['action']['stringValue'], 'Export')
    expect(p, 'a cluster-scoped record shows "—" for its namespace', by['audit-reader/success · 201']['namespace']['stringValue'], '—')
    expect(p, 'a record with no user.name shows "—"', by['nameless/success · 200']['user']['stringValue'], '—')
    return p


def check_trace_link_only_when_kept(c):
    p = []
    _, out, _ = records(c, {}, ADMIN)
    rows = table(c, out, {}, 'audit-records (traces)')
    tbl = c.get('Table', 'audit-records')['spec']['widgetData']
    trace = {cells(r)['name']['stringValue'] + '/' + cells(r)['outcome']['stringValue']: (cells(r)['trace']['stringValue'],
             rownav.build_row_path(tbl['rowNavigateTo'], r)) for r in rows}
    expect(p, 'kept trace: "open ↗" opens it', trace['acmp-new-alert-check/success · 200'],
           ('open ↗', f'/observability/traces/{TRACE_KEPT_SYS}'))
    expect(p, 'a TraceId not in otel_traces: "—", no link', trace['storefront-web/failure · 409'], ('—', None))
    expect(p, 'written with no trace context', trace['release-tag/failure · 403'], ('no trace (producer)', None))
    rdc = [cells(r)['trace']['stringValue'] for r in rows if cells(r)['resource']['stringValue'].startswith('Repo ')]
    expect(p, 'the rest-dynamic-controller write', rdc, ['no trace (producer)'])
    expect(p, 'linked count', out['counts']['linked'], 2)
    for sentence in ['"open ↗" appears only when the request\'s trace was kept. In this range, 2 of 11 records link to a trace.',
                     'Traces are kept for 7 days, so older records have no link.',
                     'External-system writes (rest-dynamic-controller) usually have no trace yet.']:
        if sentence not in out['note']:
            p.append(f'the note lacks {sentence!r}: {out["note"]!r}')
    # A reader who cannot open the trace page gets no link that would answer 403.
    _, out, _ = records(c, {}, TEAM_AB)
    rows = table(c, out, {}, 'audit-records (no trace page)')
    got = {cells(r)['name']['stringValue']: (cells(r)['trace']['stringValue'], rownav.build_row_path(tbl['rowNavigateTo'], r))
           for r in rows if cells(r)['outcome']['stringValue'] == 'success · 201' and cells(r)['user']['stringValue'] == 'j.doe'}
    expect(p, 'kept, but the trace page is not theirs', got.get('storefront-web'), ('kept', None))
    if 'It opens on the trace page, which your groups cannot read.' not in out['note']:
        p.append(f'no-trace-page note: {out["note"]!r}')
    # A trace kept 9 days ago is past otel_traces' 7 days: the 30d view does not link it.
    _, out, _ = records(c, {'range': '30d'}, ADMIN)
    old = [r for r in out['rows'] if r['name'] == 'last-week'][0]
    expect(p, 'a 9-day-old trace is not linked', (old['kept'], old['link']), (False, ''))
    return p


def check_row_count_check(c):
    p = []
    _, out, _ = records(c, {}, ADMIN)
    expect(p, 'consistent: the check passes', out['check'], True)
    expect(p, 'incomplete records (no namespace; a snowplow record with no user), classified and counted alike',
           (out['counts']['unread'], out['counts']['classifiedUnread']), (2, 2))
    if 'Row-count check' in out['note']:
        p.append(f'a passing check is in the note: {out["note"]!r}')
    if '2 records lack a field this page reads' not in out['note']:
        p.append(f'the incomplete records are not named: {out["note"]!r}')
    # The two queries disagree: a record the table query did not match. Surface it, never hide it.
    responses, ch = served()
    real = responses['summary']

    def summary_plus_one(path):
        body = real(path)
        if 'count() AS total' in sql_of(path):
            body['data'][0]['total'] = str(int(body['data'][0]['total']) + 1)
        return body
    responses['summary'] = summary_plus_one
    _, _, out = resolve(c.get('RESTAction', 'audit-records'), {}, responses, ADMIN)
    expect(p, 'a discrepancy fails the check', out['check'], False)
    if 'Row-count check: the window holds 12 records, the table query matched 11' not in out['note']:
        p.append(f'the discrepancy is not in the note: {out["note"]!r}')
    # ClickHouse unreachable: said, not shown as an empty trail.
    responses, _ = served()
    responses['rows'] = responses['summary'] = lambda path: None
    _, _, out = resolve(c.get('RESTAction', 'audit-records'), {}, responses, ADMIN)
    expect(p, 'ClickHouse down', (out['failed'], out['note'], out['check']),
           (True, 'The audit trail could not be read from ClickHouse.', None))
    table(c, out, {}, 'audit-records (ClickHouse down)')
    tbi.resolved_widget(c, 'Paragraph', 'audit-note', out, {}, 'audit-note (ClickHouse down)')
    return p


def check_requests_are_encoded_exactly_once(c):
    p = []
    oidc = 'CgVhZG1pbhIF+bG9j/YWw='
    cases = [
        ('default view (admin)', {}, ADMIN, ROWS_SQL.format(window='24 HOUR', access='', cond='')),
        ('namespace filter', {'auditNamespace': 'team-a', 'range': '7d'}, ADMIN,
         ROWS_SQL.format(window='7 DAY', access='', cond=" AND LogAttributes['k8s.namespace.name'] = 'team-a'")),
        ('user filter', {'auditUser': oidc}, ADMIN,
         ROWS_SQL.format(window='24 HOUR', access='', cond=f" AND LogAttributes['user.name'] = '{oidc}'")),
        ('outcome filter, 1h', {'auditOutcome': 'failure', 'range': '1h'}, ADMIN,
         ROWS_SQL.format(window='1 HOUR', access='', cond=" AND LogAttributes['outcome'] = 'failure'")),
        ('non-admin [team-a]', {}, TEAM_A, ROWS_SQL.format(window='24 HOUR', access=acc('team-a'), cond='')),
        ('all three filters, non-admin, 30d', {'auditNamespace': 'team-a', 'auditUser': 'system:serviceaccount:krateo-system:x',
                                               'auditOutcome': 'denied', 'range': '30d'}, TEAM_A,
         ROWS_SQL.format(window='30 DAY', access=acc('team-a'),
                         cond=" AND LogAttributes['k8s.namespace.name'] = 'team-a' AND "
                              "LogAttributes['user.name'] = 'system:serviceaccount:krateo-system:x' AND LogAttributes['outcome'] = 'denied'")),
    ]
    for label, extras, caller, want in cases:
        reqs, _, _ = records(c, extras, caller)
        if len(reqs) != 2:
            p.append(f'{label}: {len(reqs)} requests, want 2 (rows, summary)')
            continue
        for _, raw in reqs:
            once = sql_of(raw)
            if unquote(once) != once:
                p.append(f'{label}: an escape survived one decode (encoded twice): {once!r}')
            for bad, why in [('%25', 'an escaped %'), (' ', 'a space'), ('+', 'a bare +')]:
                if bad in raw:
                    p.append(f'{label}: the request carries {why}')
        expect(p, f'{label}: decoded once', sql_of(reqs[0][1]), want)
    for label, extras, caller in [('facets (admin)', {}, ADMIN), ('facets (team-a, 7d)', {'range': '7d'}, TEAM_A)]:
        reqs, _ = facets(c, extras, caller)
        raw = reqs[0][1]
        expect(p, f'{label}: decoded once', sql_of(raw),
               facets_sql('7 DAY' if extras else '24 HOUR', acc('team-a') if caller is TEAM_A else ''))
        if unquote(sql_of(raw)) != sql_of(raw) or '%25' in raw or ' ' in raw or '+' in raw:
            p.append(f'{label}: not encoded exactly once')
    return p


INJECTIONS = ["x' OR '1'='1", "a'b", "team a", "admin'--", "admin%27", "1; DROP TABLE krateo_audit",
              "team-a') OR (1=1", "’quote", "Team-A", "a" * 64]


def check_injection_is_refused(c):
    p = []
    for bad in INJECTIONS:
        for key, name in [('auditNamespace', 'namespace'), ('auditUser', 'user'), ('auditOutcome', 'outcome')]:
            if key == 'auditUser' and re.fullmatch(r'[A-Za-z0-9._@:+=/-]{1,256}', bad):
                continue                                   # a legitimate user name
            reqs, out, _ = records(c, {key: bad}, ADMIN)
            expect(p, f'{key}={bad!r}: requests', reqs, [])
            expect(p, f'{key}={bad!r}: rows', out['rows'], [])
            expect(p, f'{key}={bad!r}: rejected', out['rejected'], [name])
            freqs, fout = facets(c, {key: bad}, ADMIN)
            expect(p, f'{key}={bad!r}: facets requests', freqs, [])
    # The range is a four-way match: request text never reaches the SQL.
    for rng in ["24h' OR 1=1", '365d', '', 'all']:
        reqs, _, _ = records(c, {'range': rng}, ADMIN)
        expect(p, f'range={rng!r}: falls back to 24 HOUR', sql_of(reqs[0][1]), ROWS_SQL.format(window='24 HOUR', access='', cond=''))
    return p


def check_filter_chips(c):
    p = []
    ex = {'range': '7d', 'auditUser': 'j.doe', 'projects': 'team-a'}
    _, fac = facets(c, ex, ADMIN)
    ns = tbi.resolved_widget(c, 'Listy', 'audit-namespace-chips', fac, ex, 'namespace chips')['spec']['widgetData']['dataSource']
    expect(p, 'namespace chips with counts', [(x['label'], x['active'], x.get('count')) for x in ns],
           [('All', True, None), ('team-a', False, 5), ('team-b', False, 2), ('demo-system', False, 1),
            ('krateo-system', False, 1), ('old-team', False, 1)])
    expect(p, 'a namespace chip keeps the other filters', ns[1]['route'], '/audit?range=7d&auditNamespace=team-a&auditUser=j.doe&projects=team-a')
    expect(p, '"All" clears only its own filter', ns[0]['route'], '/audit?range=7d&auditUser=j.doe&projects=team-a')
    users = tbi.resolved_widget(c, 'Listy', 'audit-user-chips', fac, ex, 'user chips')['spec']['widgetData']['dataSource']
    expect(p, 'user chips', [(x['label'], x['active']) for x in users][:3], [('All', False), ('admin', False), ('j.doe', True)])
    oc = tbi.resolved_widget(c, 'Listy', 'audit-outcome-chips', fac, {'auditOutcome': 'failure'}, 'outcome chips')['spec']['widgetData']['dataSource']
    expect(p, 'outcome chips, in the library\'s order', [(x['label'], x['active'], x.get('count')) for x in oc],
           [('All', False, None), ('Success', False, 8), ('Failure', True, 2), ('Denied', False, 1)])
    expect(p, 'an outcome chip route', oc[3]['route'], '/audit?range=24h&auditOutcome=denied')
    plus = tbi.widget(c, 'Listy', 'audit-user-chips', 'dataSource', fac, {'auditUser': 'a+b/c='})[-1]
    expect(p, 'a user value with + / = is @uri-encoded in the route', (plus['label'], plus['route']),
           ('a+b/c=', '/audit?range=24h&auditUser=a%2Bb%2Fc%3D'))
    bad = tbi.widget(c, 'Listy', 'audit-namespace-chips', 'dataSource', fac, {'auditNamespace': "x' OR 1=1"})
    expect(p, 'a refused value gets no chip', "x' OR 1=1" in [x['label'] for x in bad], False)
    rng = tbi.resolved_widget(c, 'Listy', 'audit-range-chips', {}, ex, 'range chips')['spec']['widgetData']['dataSource']
    expect(p, 'range chips', [(x['label'], x['active']) for x in rng], [('1h', False), ('24h', False), ('7d', True), ('30d', False)])
    expect(p, 'a range chip route', rng[3]['route'], '/audit?range=30d&auditUser=j.doe&projects=team-a')
    dflt = tbi.widget(c, 'Listy', 'audit-range-chips', 'dataSource', {}, {'range': '365d'})
    expect(p, 'an unknown range shows 24h active', [x['label'] for x in dflt if x['active']], ['24h'])
    hdr = tbi.resolved_widget(c, 'PageHeader', 'audit-page-header', fac, ex, 'header (admin)')['spec']['widgetData']
    expect(p, 'admin header', hdr['subtitle'], 'Every change made through the portal and the platform API: who made it, '
                                               'to which object, and the outcome. You see all namespaces.')
    _, fac_ab = facets(c, {}, TEAM_AB)
    hdr = tbi.widget(c, 'PageHeader', 'audit-page-header', 'subtitle', fac_ab, {})
    expect(p, 'member header names their namespaces', hdr,
           'Records for the namespaces where you have been granted access to the audit trail: team-a, team-b.')
    _, fac_none = facets(c, {}, NOBODY)
    tbi.resolved_widget(c, 'PageHeader', 'audit-page-header', fac_none, {}, 'header (no namespace)')
    for name in ('audit-namespace-chips', 'audit-user-chips', 'audit-outcome-chips'):
        tbi.resolved_widget(c, 'Listy', name, fac_none, {}, f'{name} (no namespace)')
    return p


def check_records_with_no_user(c):
    """rest-dynamic-controller writes no user.name (2 rows on krateo-057). Such a record reads "—"
    in the User column, its chip reads "—" and filters to exactly those records, and no chip is ever
    empty."""
    p = []
    _, fac = facets(c, {}, ADMIN)
    expect(p, 'the user facet counts them under (none)', [(x['name'], x['count']) for x in fac['facets']['users']],
           [('admin', 4), ('j.doe', 4), ('(none)', 2), ('ci-bot', 1)])
    users = tbi.resolved_widget(c, 'Listy', 'audit-user-chips', fac, {}, 'user chips (no user)')['spec']['widgetData']['dataSource']
    expect(p, 'user chips', [(x['label'], x.get('count')) for x in users],
           [('All', None), ('admin', 4), ('j.doe', 4), ('—', 2), ('ci-bot', 1)])
    expect(p, 'the "—" chip route', users[3]['route'], '/audit?range=24h&auditUser=%28none%29')
    for name in ('audit-namespace-chips', 'audit-user-chips', 'audit-outcome-chips'):
        labels = [x['label'] for x in tbi.widget(c, 'Listy', name, 'dataSource', fac, {})]
        if any(lbl.strip() == '' for lbl in labels):
            p.append(f'{name}: an empty chip {labels}')
    reqs, out, _ = records(c, {'auditUser': '(none)'}, ADMIN)
    expect(p, '?auditUser=(none): accepted', out['rejected'], [])
    expect(p, '?auditUser=(none): decoded once', sql_of(reqs[0][1]) if reqs else None,
           ROWS_SQL.format(window='24 HOUR', access='', cond=" AND LogAttributes['user.name'] = ''"))
    if reqs and (unquote(sql_of(reqs[0][1])) != sql_of(reqs[0][1]) or '%25' in reqs[0][1]):
        p.append('?auditUser=(none): not encoded exactly once')
    rows = table(c, out, {'auditUser': '(none)'}, 'audit-records (no user)')
    expect(p, '?auditUser=(none): exactly the records with no user', sorted(cells(r)['name']['stringValue'] for r in rows),
           ['nameless', 'storefront-web'])
    expect(p, 'their User cells', col(rows, 'user'), ['—', '—'])
    rdc = [cells(r) for r in rows if cells(r)['resource']['stringValue'].startswith('Repo ')]
    expect(p, 'the rest-dynamic-controller row: no trace (producer)', [x['trace']['stringValue'] for x in rdc],
           ['no trace (producer)'])
    # A controller writing to an external system acts for no person: its blank user is expected
    # (installer#415). On krateo-057's shape — 2 complete snowplow rows, 2 rest-dynamic-controller
    # rows with no user — the note says nothing about missing fields, and the check passes.
    shape057 = [
        rec(10 * 60 * 1000, 'team-a', 'admin', 'PATCH', 'alerts', 'a1', 'success', 200, group='observability.krateo.io',
            trace=TRACE_KEPT_SYS),
        rec(20 * 60 * 1000, 'team-a', 'admin', 'POST', 'githubrepos', 'r1', 'success', 201, group='github.krateo.io'),
        rec(30 * 60 * 1000, 'team-a', '', 'POST', '', 'r1', 'success', 201, group='github.krateo.io', kind='Repo',
            action='rest.write', scope=RDC, extra={'server.address': 'api.github.com'}),
        rec(40 * 60 * 1000, 'team-a', '', 'PATCH', '', 'r1', 'success', 200, group='github.krateo.io', kind='Repo',
            action='rest.write', scope=RDC, extra={'server.address': 'api.github.com'}),
    ]
    _, out057, _ = records(c, {}, ADMIN, FakeClickHouse(rows=shape057))
    expect(p, "057's shape: nothing counted incomplete", (out057['counts']['unread'], out057['counts']['classifiedUnread'],
                                                         out057['check']), (0, 0, True))
    if 'lack' in out057['note'] or 'Row-count check' in out057['note']:
        p.append(f"057's shape: the note speaks of the rest-dynamic-controller rows: {out057['note']!r}")
    expect(p, "057's shape: their User cells", col(table(c, out057, {}, 'audit-records (057 shape)'), 'user'),
           ['admin', 'admin', '—', '—'])
    # A blank user on a SNOWPLOW record is a real gap: a caller always exists there.
    gap = shape057 + [rec(50 * 60 * 1000, 'team-a', '', 'PATCH', 'alerts', 'a2', 'success', 200,
                          group='observability.krateo.io')]
    _, outgap, _ = records(c, {}, ADMIN, FakeClickHouse(rows=gap))
    expect(p, 'a snowplow record with no user: counted', (outgap['counts']['unread'], outgap['counts']['classifiedUnread'],
                                                          outgap['check']), (1, 1, True))
    if '1 record lacks a field this page reads' not in outgap['note']:
        p.append(f'a snowplow record with no user is not named in the note: {outgap["note"]!r}')
    act = tbi.widget(c, 'Listy', 'audit-user-chips', 'dataSource', fac, {'auditUser': '(none)'})
    expect(p, 'the "—" chip is active while it filters', [x['label'] for x in act if x['active']], ['—'])
    reqs, out, _ = records(c, {'auditUser': '(none)'}, TEAM_A)
    expect(p, 'a member filtering to no user stays narrowed', sql_of(reqs[0][1]) if reqs else None,
           ROWS_SQL.format(window='24 HOUR', access=acc('team-a'), cond=" AND LogAttributes['user.name'] = ''"))
    return p


def check_64bit_integers_quoted_or_not(c):
    p = []
    views = []
    for quoted in (True, False):
        ch = FakeClickHouse(quoted=quoted)
        _, out, _ = records(c, {}, ADMIN, ch)
        _, fac = facets(c, {}, ADMIN, FakeClickHouse(quoted=quoted))
        views.append((table(c, out, {}, f'audit-records (quoted={quoted})'), out['note'], out['check'],
                      tbi.widget(c, 'Listy', 'audit-namespace-chips', 'dataSource', fac, {})))
    expect(p, 'unquoted == quoted', views[1], views[0])
    # The fixtures really are the two shapes, for rows, summary and facets: ClickHouse's default quotes
    # 64-bit integers, krateo-057's 26.8 sends bare numbers.
    for quoted, kind in ((True, str), (False, int)):
        ch = FakeClickHouse(quoted=quoted)
        _, out, _ = records(c, {}, ADMIN, ch)
        raw_rows = ch('/?query=' + quote(ch.queries[0], safe=''))['data'] if ch.queries else []
        raw_sum = ch('/?query=' + quote(ch.queries[1], safe=''))['data'] if len(ch.queries) > 1 else []
        shapes = {type(raw_rows[0]['ts_ms']), type(raw_rows[0]['matched']), type(raw_sum[0]['total'])} if raw_rows and raw_sum else set()
        expect(p, f'quoted={quoted}: the fixture sends ts_ms, matched and total as {kind.__name__}', shapes, {kind})
        expect(p, f'quoted={quoted}: read as numbers', (type(out['rows'][0]['tsMs']), type(out['counts']['total'])), (int, int))
        fch = FakeClickHouse(quoted=quoted)
        _, fac = facets(c, {}, ADMIN, fch)
        raw_fac = fch('/?query=' + quote(fch.queries[0], safe=''))['data'] if fch.queries else []
        expect(p, f'quoted={quoted}: the facets fixture sends n as {kind.__name__}', {type(r['n']) for r in raw_fac}, {kind})
        expect(p, f'quoted={quoted}: facet counts read as numbers', {type(x['count']) for d in fac['facets'].values() for x in d}, {int})
    return p


def check_chart_conventions(c):
    p = []
    widgets = [d for d in c.docs if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/')]
    ras = [d for d in c.docs if d.get('kind') == 'RESTAction']
    for d in widgets + ras:
        expect(p, f'{d["kind"]} {d["metadata"]["name"]} namespace', d['metadata']['namespace'], NS)
    for d in widgets:
        if sorted(d['spec'].get('keyExtras') or []) != sorted(KEY_EXTRAS):
            p.append(f'{d["kind"]} {d["metadata"]["name"]}: keyExtras {d["spec"].get("keyExtras")}')
        if 'krateo.io/external-cache-ttl-seconds' in (d['metadata'].get('annotations') or {}):
            p.append(f'{d["kind"]} {d["metadata"]["name"]} opts into the external-TTL cache')
    for ra in ras:
        for step in ra['spec']['api']:
            if step.get('endpointRef'):
                expect(p, f'{ra["metadata"]["name"]}/{step["name"]} endpoint', step['endpointRef'],
                       {'name': 'clickhouse-endpoint', 'namespace': RELEASE_NS})
                expect(p, f'{ra["metadata"]["name"]}/{step["name"]} verb', step.get('verb'), 'GET')
            elif not step.get('userAccessFilter'):
                p.append(f'{ra["metadata"]["name"]}/{step["name"]}: an apiserver stage with no userAccessFilter')
    ann = c.get('Flex', 'page-audit')['metadata']['annotations']
    expect(p, 'nav', {k: v for k, v in ann.items() if k.startswith('krateo.io/nav-')},
           {'krateo.io/nav-path': '/audit', 'krateo.io/nav-label': 'Audit', 'krateo.io/nav-icon': 'fa-clipboard-list',
            'krateo.io/nav-group': 'Observability', 'krateo.io/nav-order': '15'})
    kinds = sorted((d['kind'], d['metadata'].get('namespace', ''), d['metadata']['name'])
                   for d in c.docs if not d.get('apiVersion', '').startswith(('widgets.', 'templates.')))
    expect(p, 'every non-page object the chart creates', kinds, [
        ('Namespace', '', NS),
        ('Role', NS, 'admins-read-audit-pages'), ('Role', NS, 'devs-read-audit-pages'),
        ('RoleBinding', NS, 'admins-read-audit-pages'), ('RoleBinding', NS, 'devs-read-audit-pages')])
    # Diego, 2026-11-10: the table caption is a sentence in plain text, like the Traces tab's; the eyebrow
    # (uppercase since frontend#654) is for the four short chip-group labels only.
    expect(p, 'paragraph variants', {n: c.get('Paragraph', n)['spec']['widgetData'].get('variant') for n in
                                     ['audit-table-caption', 'audit-range-label', 'audit-namespace-label',
                                      'audit-outcome-label', 'audit-user-label']},
           {'audit-table-caption': None, 'audit-range-label': 'eyebrow', 'audit-namespace-label': 'eyebrow',
            'audit-outcome-label': 'eyebrow', 'audit-user-label': 'eyebrow'})
    expect(p, 'the caption is muted, like the Traces caption (type: secondary)',
           c.get('Paragraph', 'audit-table-caption')['spec']['widgetData'].get('type'), 'secondary')
    role = c.get('Role', 'devs-read-audit-pages')
    expect(p, 'what a reader group may do there', role['rules'], [
        {'apiGroups': ['widgets.templates.krateo.io'], 'resources': ['*'], 'verbs': ['get', 'list']},
        {'apiGroups': ['templates.krateo.io'], 'resources': ['restactions'], 'verbs': ['get']}])
    other = render(values={'readerGroups': ['admins', 'devs', 'tenants'], 'namespace': 'audit-x', 'traceNamespace': 'obs-x'})
    expect(p, 'readerGroups replaces the default', sorted(s['name'] for d in other.docs if d['kind'] == 'RoleBinding'
                                                         for s in d['subjects']), ['admins', 'devs', 'tenants'])
    expect(p, 'traceNamespace reaches the trace page check',
           [s['path'] for s in other.get('RESTAction', 'audit-records')['spec']['api'] if s['name'] == 'tracePage'],
           ['/apis/widgets.templates.krateo.io/v1beta1/namespaces/obs-x/flexes/page-trace-detail'])
    expect(p, 'namespace reaches the cluster-scope check',
           [s['path'] for s in other.get('RESTAction', 'audit-facets')['spec']['api'] if s['name'] == 'allNamespaces'],
           ['/api/v1/namespaces/audit-x'])
    for bad in ["x/../y", 'Bad_NS', '']:
        try:
            render(values={'traceNamespace': bad})
            p.append(f'traceNamespace={bad!r} rendered')
        except SystemExit as exc:
            if 'traceNamespace' not in str(exc):
                p.append(f'traceNamespace={bad!r} failed for another reason: {str(exc)[-200:]}')
    return p


def check_authored_widgets_validate(c):
    for d in c.docs:
        if d.get('apiVersion', '').startswith('widgets.templates.krateo.io/'):
            tbi.RESOLVED.append((f'{d["kind"]} {d["metadata"]["name"]} as rendered', copy.deepcopy(d)))
    return []


def print_sql(c):
    """The decoded SQL of the PR body's four cases, as snowplow dials them."""
    for label, extras, caller in [('Default view (admin, 24h)', {}, ADMIN),
                                  ('Namespace filter (admin, ?auditNamespace=team-a)', {'auditNamespace': 'team-a'}, ADMIN),
                                  ('User filter (admin, ?auditUser=j.doe)', {'auditUser': 'j.doe'}, ADMIN),
                                  ('Non-admin whose namespaces are [team-a]', {}, TEAM_A)]:
        reqs, _, _ = records(c, extras, caller)
        print(f'### {label}')
        for name, raw in reqs:
            print(f'-- {name}\n{sql_of(raw)}\n')
    reqs, _ = facets(c, {}, TEAM_A)
    print('### Facets, non-admin [team-a]\n' + sql_of(reqs[0][1]) + '\n')


def main():
    args = sys.argv[1:]
    crds_dir, source = None, ''
    if '--crds' in args:
        crds_dir = args[args.index('--crds') + 1]
        source = crds_dir
    elif '--pinned' in args:
        pinned = _load('pinned', 'lint-pinned-crds.py')
        version, why = pinned.pinned_version()
        if not version:
            print(f'cannot determine the pinned frontend-crds version: {why}')
            return 1
        crds_dir = pinned.unpack(version)
        source = f'frontend-crds {version} ({why})'
    c = render()
    if '--print-sql' in args:
        print_sql(c)
        return 0
    checks = [
        ('NEGATIVE TEST: a team-a member sees no team-b or krateo-system row; the admin sees all; '
         'no namespace sees nothing', lambda: check_negative_test(c)),
        ('the namespace predicate is the caller\'s own, and every access stage fails closed',
         lambda: check_access_predicate_is_the_callers_own(c)),
        ('the scope is the explicit audit grant: compositions or other reads count for nothing',
         lambda: check_scope_is_the_explicit_audit_grant(c)),
        ('the portal chart grants devs list auditrecords in demo-system, and nothing broader',
         lambda: check_portal_grants_devs_the_demo_audit_trail(c)),
        ('only the two audit scopes, no previews, bounded windows', lambda: check_population_and_previews(c)),
        ('columns map to the record as approved', lambda: check_columns_map_to_the_record(c)),
        ('a trace links only when it is kept and openable', lambda: check_trace_link_only_when_kept(c)),
        ('the row-count check passes when it should and speaks when it does not', lambda: check_row_count_check(c)),
        ('each request is encoded exactly once, as snowplow dials it', lambda: check_requests_are_encoded_exactly_once(c)),
        ('a quote or SQL fragment in any input makes no request', lambda: check_injection_is_refused(c)),
        ('filter chips, counts and the header', lambda: check_filter_chips(c)),
        ('a record with no user reads "—", and its chip filters to exactly those', lambda: check_records_with_no_user(c)),
        ('64-bit integers read the same quoted or not', lambda: check_64bit_integers_quoted_or_not(c)),
        ('chart conventions: namespace, RBAC, endpoint, keyExtras, nav, values', lambda: check_chart_conventions(c)),
    ]
    if crds_dir:
        checks.append(('(authored widgets queued for validation)', lambda: check_authored_widgets_validate(c)))
    failed = 0
    for label, run in checks:
        try:
            problems = run()
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError, ValueError,
                sqlite3.Error, PermissionError) as exc:
            problems = [f'{type(exc).__name__}: {exc}']
        print(f'[{"FAIL" if problems else "PASS"}] {label}')
        for prob in dict.fromkeys(problems):
            print(f'        {prob}')
        failed += bool(problems)
    total = len(checks)
    if crds_dir:
        total += 1
        problems = tbi.validate_resolved(crds_dir)
        print(f'[{"FAIL" if problems else "PASS"}] {len(tbi.RESOLVED)} widgets (rendered and resolved) validate against {source}')
        for prob in dict.fromkeys(problems):
            print(f'        {prob}')
        failed += bool(problems)
    print(f'\n{total - failed} of {total} checks passed ({tbi.JQ}; paths rendered by snowplow '
          f'{spath.renderer().source}'
          f'{"" if crds_dir else "; widgets NOT validated (no --crds / --pinned)"})')
    return failed


if __name__ == '__main__':
    sys.exit(main())
