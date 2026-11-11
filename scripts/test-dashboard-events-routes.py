#!/usr/bin/env python3
"""The Dashboard's activity feed links each row from the event alone, with no composition lists.

WHY. The `events` RESTAction used to list every CompositionDefinition, then every composition of
every kind, only to map an event's `compositionId` back to the composition's namespace and name for
the row's link. That fan-out was slow, and any composition change re-ran it. sse-proxy 1.5.0 returns
the composition on the event itself (`composition: {id, namespace, name}`; namespace and name are
omitted on rows ingested before the otel-collector stamped them), so the RA now has one stage,
`getEvents`, and the link comes from that field.

HOW. dashboard-page is rendered with `helm template`; the RA's OWN filter, then the
list-activity-events widget's OWN dataSource expression, run under jq over sse-proxy 1.5.0-shaped
events. A test holding its own copy of the jq would pass while the chart drifts away from it.

Checks:
  - the RA has no stage but getEvents, and none lists compositions or CompositionDefinitions;
  - an event naming its composition links to /compositions/<namespace>/<name>;
  - an event carrying the composition id only (older rows) is shown, unlinked;
  - a CompositionDefinition event keeps its /blueprints/<ns>/<name> link;
  - an event with no composition is shown, unlinked;
  - a malformed `composition` does not error the filter (an error would EMPTY the whole feed).

Run: python3 -I scripts/test-dashboard-events-routes.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART = os.path.join(REPO, 'helm', 'dashboard-page')
JQ = os.environ.get('JQ', 'jq')

failures = []


def check(cond, msg):
    print(('  [PASS] ' if cond else '  [FAIL] ') + msg)
    if not cond:
        failures.append(msg)


def render():
    with tempfile.TemporaryDirectory() as tmp:
        copy = os.path.join(tmp, 'chart')
        subprocess.run(['cp', '-r', CHART, copy], check=True)
        meta = os.path.join(copy, 'Chart.yaml')
        with open(meta, encoding='utf-8') as fh:
            text = fh.read()
        for ph in ('CHART_VERSION', 'SOURCE_REF', 'APP_VERSION'):
            text = text.replace(ph, '0.0.0-test')
        with open(meta, 'w', encoding='utf-8') as fh:
            fh.write(text)
        proc = subprocess.run(['helm', 'template', 'c', copy], capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template dashboard-page failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def named(docs, kind, name):
    hits = [d for d in docs if d.get('kind') == kind and d.get('metadata', {}).get('name') == name]
    if len(hits) != 1:
        raise RuntimeError(f'expected one {kind}/{name}, found {len(hits)}')
    return hits[0]


def jq(program, doc):
    with tempfile.TemporaryDirectory() as tmp:
        fp = os.path.join(tmp, 'f.jq')
        with open(fp, 'w', encoding='utf-8') as fh:
            fh.write(program)
        proc = subprocess.run([JQ, '-c', '-f', fp], input=json.dumps(doc), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return json.loads(proc.stdout)


def event(uid, kind, name, ns, ts, composition=None, reason='Synced', message=None, cid=''):
    """An event as sse-proxy 1.5.0 encodes SSEK8sEvent (main.go): `composition` is omitempty, and
    inside it namespace and name are omitempty too."""
    e = {'metadata': {'name': name + '.ev', 'namespace': ns, 'uid': 'ev-' + uid, 'creationTimestamp': ts},
         'involvedObject': {'apiVersion': 'v1', 'kind': kind, 'name': name, 'namespace': ns, 'uid': uid},
         'reason': reason, 'message': message or f'{kind} {name}', 'type': 'Normal',
         'firstTimestamp': ts, 'lastTimestamp': ts, 'eventTime': ts, 'source': {'component': 'c'},
         'compositionId': cid}
    if composition is not None:
        e['composition'] = composition
    return e


CID = '3f1c2a9e-0000-4000-8000-000000000001'
CID_OLD = '3f1c2a9e-0000-4000-8000-000000000002'

# (label, event, want route) — timestamps descend so the RA's newest-first order equals this order.
CASES = [
    ('a named composition event links to its composition',
     event('u1', 'Deployment', 'web', 'team-a', '2026-11-11T10:00:09Z', cid=CID,
           composition={'id': CID, 'namespace': 'team-a', 'name': 'spring-summit'}),
     '/compositions/team-a/spring-summit'),
    # The composition lives in a namespace other than its child's: the link uses the COMPOSITION's.
    ('the link uses the composition\'s namespace, not the involved object\'s',
     event('u2', 'Service', 'web', 'team-a-runtime', '2026-11-11T10:00:08Z', cid=CID,
           composition={'id': CID, 'namespace': 'team-a', 'name': 'spring-summit'}),
     '/compositions/team-a/spring-summit'),
    ('an id-only event (ingested before names were stamped) is shown, unlinked',
     event('u3', 'Deployment', 'api', 'team-b', '2026-11-11T10:00:07Z', cid=CID_OLD,
           composition={'id': CID_OLD}),
     ''),
    ('an event with a namespace but no name is unlinked',
     event('u4', 'Deployment', 'api2', 'team-b', '2026-11-11T10:00:06Z', cid=CID_OLD,
           composition={'id': CID_OLD, 'namespace': 'team-b'}),
     ''),
    ('a CompositionDefinition event keeps its blueprint link',
     event('u5', 'CompositionDefinition', 'keystone', 'krateo-system', '2026-11-11T10:00:05Z', cid=CID,
           composition={'id': CID, 'namespace': 'team-a', 'name': 'spring-summit'}),
     '/blueprints/krateo-system/keystone'),
    ('a CompositionDefinition event naming no composition keeps its blueprint link',
     event('u6', 'CompositionDefinition', 'portal', 'krateo-system', '2026-11-11T10:00:04Z'),
     '/blueprints/krateo-system/portal'),
    ('a non-composition event is shown, unlinked',
     event('u7', 'Node', 'gke-node-1', '', '2026-11-11T10:00:03Z'),
     ''),
    ('a malformed composition does not error the filter',
     event('u8', 'Pod', 'p', 'team-c', '2026-11-11T10:00:02Z', composition='spring-summit'),
     ''),
    ('a null composition does not error the filter',
     event('u9', 'Pod', 'q', 'team-c', '2026-11-11T10:00:01Z', composition=None) | {'composition': None},
     ''),
]


def main():
    if not shutil.which('helm') or not shutil.which(JQ):
        print('FAIL: helm and jq are both required')
        return 1
    docs = render()
    ra = named(docs, 'RESTAction', 'events')
    widget = named(docs, 'Listy', 'list-activity-events')

    print('events RESTAction stages:')
    stages = ra['spec'].get('api') or []
    check([s.get('name') for s in stages] == ['getEvents'],
          f"the only stage is getEvents (got {[s.get('name') for s in stages]})")
    lists = [s.get('name') for s in stages
             if 'composition.krateo.io' in str(s.get('path', '')) or 'compositiondefinitions' in str(s.get('path', ''))
             or s.get('dependsOn') or s.get('userAccessFilter')]
    check(not lists, f'no stage lists compositions or CompositionDefinitions (got {lists})')
    check('byCid' not in ra['spec']['filter'] and '.comps' not in ra['spec']['filter'] and '.crds' not in ra['spec']['filter'],
          'the filter reads no composition-list stage')
    ge = next((s for s in stages if s.get('name') == 'getEvents'), {})
    check(ge.get('path') == '/events' and ge.get('exportJwt') is True and ge.get('continueOnError') is True
          and (ge.get('endpointRef') or {}).get('name') == 'sse-proxy-internal-endpoint',
          'getEvents is unchanged: /events via sse-proxy-internal-endpoint, exportJwt, continueOnError')

    # snowplow hands the final filter every stage's output under its name. A stale `comps` key is
    # included on purpose: the filter must not read it, so its links must not change.
    stale = {'comps': [{'name': 'WRONG', 'namespace': 'WRONG', 'compositionId': CID_OLD}]}
    events = [c[1] for c in CASES]
    out = jq(ra['spec']['filter'], dict({'getEvents': list(reversed(events))}, **stale))

    print('routes, from the RA\'s own filter:')
    rows = out.get('list') or []
    check(len(rows) == len(CASES), f'every event is shown ({len(rows)} of {len(CASES)})')
    by_uid = {r['involvedObject']['uid']: r for r in rows}
    for label, ev, want in CASES:
        got = by_uid.get(ev['involvedObject']['uid'], {}).get('route')
        check(got == want, f'{label}: route {got!r} (want {want!r})')
    check([r['involvedObject']['uid'] for r in rows] == [c[1]['involvedObject']['uid'] for c in CASES],
          'rows are newest first')

    print('dedup and cap kept:')
    dup = [event('d', 'Pod', 'dup', 'team-c', f'2026-11-11T09:00:0{i}Z', reason='BackOff', message='same')
           for i in range(3)]
    many = [event(f'm{i}', 'Pod', f'm{i}', 'team-c', f'2026-11-10T{i // 60:02d}:{i % 60:02d}:00Z') for i in range(70)]
    out2 = jq(ra['spec']['filter'], {'getEvents': dup + many})
    rows2 = out2['list']
    check(len(rows2) == 50, f'the list is capped at 50 (got {len(rows2)})')
    d = [r for r in rows2 if r['involvedObject']['uid'] == 'd']
    check(len(d) == 1 and d[0]['lastTimestamp'] == '2026-11-11T09:00:02Z',
          'same object+reason+message collapses to its newest event')
    check(jq(ra['spec']['filter'], {'getEvents': None}) == {'list': []}, 'a failed getEvents (null) is an empty list, not an error')
    check(jq(ra['spec']['filter'], {}) == {'list': []}, 'no getEvents at all is an empty list, not an error')

    print('the widget reads what the RA writes:')
    tpl = [t for t in widget['spec']['widgetDataTemplate'] if t.get('forPath') == 'dataSource']
    expr = tpl[0]['expression'].strip()
    check(expr.startswith('${') and expr.endswith('}'), 'dataSource is a ${ } expression')
    items = jq(expr[2:-1], out)
    check([i['route'] for i in items] == [c[2] for c in CASES][:8],
          f"the first 8 rows' navigateTo routes reach the widget ({[i['route'] for i in items]})")
    check(items[0]['ref'] == 'Deployment/web' and items[0]['message'] and items[0]['ts'],
          'the widget still finds message, ref and ts on each row')

    if failures:
        print(f'\n{len(failures)} check(s) failed')
        return 1
    print('\nall checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
