#!/usr/bin/env python3
"""
test-incidents-list — /incidents' "Ask Agentiko →", over the chart's own jq.

WHAT IT GUARDS. incidents-list builds the header button's `askHref`: /incidents with the ?status= chip
and the ?projects= scope kept, and a prompt about the view on screen. The rules a reader would notice:
  - unfiltered, the prompt gives the per-state counts, the alerts firing with no incident, and the
    open incidents (title, root cause, the DAY each was triggered), at most eight;
  - with a chip, it is about that chip's incidents only, and the href keeps the chip; a done chip
    (Resolved, Closed) asks for a review, not a triage; a chip with no incidents says so;
  - a ?status= that is no state shows no rows and no chip, so the prompt is about every incident and
    the href drops it;
  - A FAILED READ IS NOT A QUIET PLATFORM: an Incident list that could not be read asks what the error
    means, and an alerts read that failed is never "no alert is firing". snowplow accumulates an
    errorKey as a LIST whose elements are strings or the apiserver's Status object
    (resolve.go accumulateErrorKey); both shapes are fed here;
  - the existing per-alert `awaiting[].askHref` keeps its path;
  - the "Firing alerts with no incident" section is a second table laid out like the incident table:
    each alert says why it has none, off alert-provider's OpensIncidents condition (a metric or value
    alert opens no incidents, a counting alert has not opened one yet, listed first), and an
    "Ask Agentiko →" pill says what a row click does; the prompt says when the alert never opens one;
    a paused alert is not listed; the page shows the section only while it has rows.
The button and the header are resolved over the same output, and with --crds every resolved widget is
validated against its CRD.

Uses the `jq` binary; JQ=gojq runs the same checks on it.
Usage: test-incidents-list.py [--crds DIR]. Exit code = failed checks.
"""
import importlib.util
import os
import sys
from urllib.parse import parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location('tbi', os.path.join(HERE, 'test-builder-install.py'))
tbi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tbi)

CHART = os.path.join(HERE, '..', 'helm', 'incidents-page')
TAIL = ' Tell me which to work on first, whether any share a cause, and the next step for each — grounded in the evidence on this page.'


def incident(name, state, triggered, alert=None, cause=None, error=None):
    st = {} if state is None else {'state': state}
    if cause:
        st['rootCause'] = {'statement': cause}
    if error:
        st['error'] = error
    meta = {'name': name, 'creationTimestamp': triggered}
    if alert:
        meta['labels'] = {'observability.krateo.io/alert': alert}
    return {'metadata': meta, 'spec': {'triggeredAt': triggered}, 'status': st}


def alert(name, display, state='ALERT', opens=None, typ=None, paused=False):
    st = {'state': state}
    if typ:
        st['type'] = typ
    if opens:  # (status, reason, message): alert-provider's OpensIncidents condition
        st['conditions'] = [{'type': 'Ready', 'status': 'True', 'reason': 'Available'},
                            {'type': 'OpensIncidents', 'status': opens[0], 'reason': opens[1], 'message': opens[2]}]
    meta = {'name': name}
    if paused:
        meta['annotations'] = {'krateo.io/paused': 'true'}
    return {'metadata': meta, 'spec': {'displayName': display}, 'status': st}


AGG = 'It compares an aggregated value; only an alert that counts log lines or spans opens incidents.'


INCIDENTS = [
    incident('inc-lat', 'Open', '2026-11-08T10:00:00Z', alert='api-latency', cause='connection pool exhausted on orders-db'),
    incident('inc-disk', 'Open', '2026-11-06T09:00:00Z', alert='disk', cause='PVC data-pg-0 at 97%'),
    incident('inc-new', None, '2026-11-09T11:00:00Z', alert='crashloop'),                 # Analyzing
    incident('inc-ver', 'Verifying', '2026-11-07T08:00:00Z', alert='api-latency', cause='rolled back orders-api'),
    incident('inc-res', 'Resolved', '2026-11-05T07:00:00Z', alert='disk', cause='log volume filled /var'),
]
ALERTS = [alert('api-latency', 'API latency p95'), alert('disk', 'Disk pressure', 'OK'), alert('crashloop', 'Pod crashloop'),
          alert('restarts', 'Pod restarts')]                                              # firing, no incident


def responses(**over):
    r = {'incidents': {'items': INCIDENTS}, 'alerts': {'items': ALERTS}}
    r.update(over)
    return r


def resolve(resp, extras):
    """incidents-list over `resp`: a step's response, or a list of errors (as snowplow accumulates
    them under the errorKey) for a failed call."""
    ra = CH.get('RESTAction', 'incidents-list')
    data = dict(extras)
    for step in ra['spec']['api']:
        raw = resp.get(step['name'])
        if isinstance(raw, list):
            data[step['errorKey']] = raw
        elif raw is not None:
            data[step['name']] = raw
    return tbi.jq(ra['spec']['filter'], data)


def ask(href):
    path, _, query = href.partition('?')
    params = parse_qs(query, keep_blank_values=True)
    prompt = params.pop('ask', [''])
    kept = '&'.join(f'{k}={v[0]}' for k, v in params.items())
    return (path + ('?' + kept if kept else ''), prompt[0])


def expect(f, what, got, want):
    if got != want:
        f.append(f'{what}: got {got!r}, want {want!r}')


def check_unfiltered():
    f = []
    out = resolve(responses(), {'projects': 'team-a'})
    href, prompt = ask(out['askHref'])
    expect(f, 'self page, scope kept, no chip', href, '/incidents?projects=team-a')
    expect(f, 'counts, awaiting, the open ones', prompt,
           'Triage my incidents: 2 open, 1 verifying, 1 still being analyzed, and 1 alert firing with no incident '
           '(Pod restarts). The open ones are: 1. "API latency p95" — connection pool exhausted on orders-db '
           '(triggered 2026-11-08) 2. "Disk pressure" — PVC data-pg-0 at 97% (triggered 2026-11-06).' + TAIL)
    expect(f, 'the per-alert links are untouched', [a['askHref'].split('?ask=')[0] for a in out['awaiting']], ['/incidents'])
    many = [incident(f'i{i}', 'Open', f'2026-11-{i + 10:02d}T00:00:00Z', cause='x' * 200) for i in range(10)]
    _, prompt = ask(resolve(responses(incidents={'items': many}), {})['askHref'])
    expect(f, 'eight at most', ('8. "' in prompt, '9. "' in prompt), (True, False))
    # With no alert, the title IS the root cause: it is cut at 110 and not repeated as the cause.
    expect(f, 'a cause equal to the title is not repeated', '"' + 'x' * 109 + '…" (triggered ' in prompt, True)
    long_cause = [incident('i-long', 'Open', '2026-11-10T00:00:00Z', alert='api-latency', cause='y' * 200)]
    _, prompt = ask(resolve(responses(incidents={'items': long_cause}), {})['askHref'])
    expect(f, 'a root cause is cut at 160', '"API latency p95" — ' + 'y' * 159 + '… (triggered 2026-11-10)' in prompt, True)
    return f


def check_filtered():
    f = []
    href, prompt = ask(resolve(responses(), {'status': 'open', 'projects': 'team-a'})['askHref'])
    expect(f, 'open chip: kept with the scope', href, '/incidents?status=open&projects=team-a')
    expect(f, 'open chip: only the open ones', prompt,
           'Triage my open incidents: 2 of 5 incidents. They are: 1. "API latency p95" — connection pool exhausted on '
           'orders-db (triggered 2026-11-08) 2. "Disk pressure" — PVC data-pg-0 at 97% (triggered 2026-11-06).' + TAIL)
    href, prompt = ask(resolve(responses(), {'status': 'Resolved'})['askHref'])
    expect(f, 'resolved chip, any case: kept', href, '/incidents?status=resolved')
    expect(f, 'resolved chip: a review', prompt,
           'Review my resolved incidents: 1 of 5 incidents. It is: 1. "Disk pressure" — log volume filled /var '
           '(triggered 2026-11-05). Tell me whether any looks resolved without its cause being fixed, and whether any '
           'is likely to come back — grounded in the evidence on this page.')
    href, prompt = ask(resolve(responses(), {'status': 'closed'})['askHref'])
    expect(f, 'empty chip: kept', href, '/incidents?status=closed')
    expect(f, 'empty chip: says so', prompt,
           'I have no closed incidents; my 5 incidents are in other states, with 1 alert firing with no incident '
           '(Pod restarts). Tell me whether anything here needs attention — grounded in the evidence on this page.')
    out = resolve(responses(), {'status': 'foo', 'projects': 'team-a'})
    href, prompt = ask(out['askHref'])
    expect(f, 'not a state: no rows, as before', out['rows'], [])
    expect(f, 'not a state: the param is dropped', href, '/incidents?projects=team-a')
    expect(f, 'not a state: the whole set', prompt, ask(resolve(responses(), {'projects': 'team-a'})['askHref'])[1])
    return f


def check_empty_and_failed():
    f = []
    _, prompt = ask(resolve(responses(incidents={'items': []}), {})['askHref'])
    expect(f, 'no incidents, an alert firing', prompt,
           'There are no incidents on this platform, and 3 alerts firing with no incident (API latency p95, Pod '
           'crashloop, Pod restarts). Tell me which alert to investigate first and why — grounded in the evidence on this page.')
    _, prompt = ask(resolve(responses(incidents={'items': []}, alerts={'items': []}), {})['askHref'])
    expect(f, 'nothing at all', prompt,
           'There are no incidents on this platform, and no alert firing without an incident. Tell me whether anything '
           'on this page needs attention.')
    status = {'kind': 'Status', 'status': 'Failure', 'code': 403, 'reason': 'Forbidden',
              'message': 'incidents.observability.krateo.io is forbidden: User "erin" cannot list resource'}
    for label, errs, said in (('a Status object', [status], status['message']),
                              ('a string', ['dial tcp: i/o timeout'], 'dial tcp: i/o timeout')):
        out = resolve(responses(incidents=errs), {'status': 'open'})
        href, prompt = ask(out['askHref'])
        expect(f, f'failed read ({label}): flagged', (out['failed'], out['readError']), (True, said))
        expect(f, f'failed read ({label}): asks about the error', prompt,
               'The incidents on this page could not be read (' + said + '). Explain what that error means and what to check.')
        expect(f, f'failed read ({label}): keeps the chip', href, '/incidents?status=open')
    _, prompt = ask(resolve(responses(alerts=['dial tcp: i/o timeout']), {})['askHref'])
    expect(f, 'alerts unread: never "no alert firing"', 'no alert firing' in prompt, False)
    expect(f, 'alerts unread: said', 'an unknown number of alerts firing with no incident (the alerts could not be read)' in prompt, True)
    out = resolve(responses(), {})
    expect(f, 'healthy read: not failed', (out['failed'], out['readError']), (False, ''))
    return f


def check_button_and_header():
    f = []
    ex = {'status': 'open', 'projects': 'team-a'}
    out = resolve(responses(), ex)
    btn = tbi.resolved_widget(CH, 'Button', 'incidents-ask-agentiko', out, ex, 'ask button')['spec']
    expect(f, 'button: the RA\'s link', btn['widgetData']['actions']['navigate'][0]['path'], out['askHref'])
    expect(f, 'button: label and intent', (btn['widgetData']['label'], btn['widgetData']['intent']), ('Ask Agentiko →', 'agentiko'))
    expect(f, 'button: keyExtras match the table and chips', btn['keyExtras'],
           CH.get('Table', 'incidents-table')['spec']['keyExtras'])
    expect(f, 'button, no data: the list', tbi.widget(CH, 'Button', 'incidents-ask-agentiko', 'actions', {}, {})['navigate'][0]['path'], '/incidents')
    hdr = tbi.resolved_widget(CH, 'PageHeader', 'incidents-page-header', {}, ex, 'header')['spec']['widgetData']
    expect(f, 'header: the button, alone', ([i['resourceRefId'] for i in hdr['items']], hdr['allowedResources']),
           (['incidents-ask-agentiko'], ['buttons']))
    tbi.resolved_widget(CH, 'Listy', 'incidents-status-chips', out, ex, 'chips')
    tbi.resolved_widget(CH, 'Table', 'incidents-table', out, ex, 'table')
    return f


def check_awaiting_section():
    f = []
    alerts = [alert('disk-free', 'ClickHouse disk below 20% free', opens=('False', 'AggregatedValue', AGG), typ='Metric'),
              alert('cost', 'LLM spend over budget', opens=('False', 'AggregatedValue', AGG), typ='LogValue'),
              alert('pull', 'Image pull failing', opens=('True', 'CountsRecords', 'A firing opens an incident.'), typ='Logs'),
              alert('muted', 'Muted alert', paused=True, opens=('False', 'Paused', 'Paused.'), typ='Logs'),
              alert('older', 'Older provider')]  # no condition: an alert-provider before OpensIncidents
    out = resolve(responses(incidents={'items': []}, alerts={'items': alerts}), {})
    expect(f, 'why each has no incident, the not-yet ones first, paused left out',
           [(a['name'], a['why']) for a in out['awaiting']],
           [('Image pull failing', 'Incident not opened yet'), ('Older provider', ''),
            ('ClickHouse disk below 20% free', 'Metric · opens no incidents'), ('LLM spend over budget', 'Value · opens no incidents')])
    prompts = {a['name']: ask(a['askHref'])[1] for a in out['awaiting']}
    expect(f, 'a metric alert\'s prompt says it never opens one', prompts['ClickHouse disk below 20% free'],
           'Investigate the "ClickHouse disk below 20% free" observability alert — it is firing but has no open incident. '
           'It never opens one: ' + AGG + ' Run a root-cause analysis and give me the concrete next steps.')
    expect(f, 'a counting alert\'s prompt is unchanged', prompts['Image pull failing'],
           'Investigate the "Image pull failing" observability alert — it is firing but has no open incident. '
           'Run a root-cause analysis and give me the concrete next steps.')
    table = tbi.resolved_widget(CH, 'Table', 'incidents-awaiting', out, {}, 'awaiting table')['spec']['widgetData']
    incidents = CH.get('Table', 'incidents-table')['spec']['widgetData']
    expect(f, 'laid out like the incident table', [(c['valueKey'], c['title']) for c in table['columns']],
           [('sev', ' '), ('name', 'Alert'), ('why', 'Why no incident'), ('ask', ' ')])
    expect(f, 'same table style', {k: table[k] for k in ('fitContent', 'bordered', 'size')},
           {k: incidents[k] for k in ('fitContent', 'bordered', 'size')})
    rows = [{c['valueKey']: (c.get('stringValue'), c.get('kind'), c.get('color')) for c in r} for r in table['dataSource']]
    expect(f, 'rows: alert, why (— when unknown)', [(r['name'][0], r['why'][0]) for r in rows],
           [(a['name'], a['why'] or '—') for a in out['awaiting']])
    expect(f, 'every row carries the Ask Agentiko pill', {r['ask'] for r in rows}, {('Ask Agentiko →', 'tag', 'primary')})
    expect(f, 'row click opens Agentiko on that alert', (table['rowNavigateTo'], [r['askHref'][0] for r in rows]),
           ('{askHref}', [a['askHref'] for a in out['awaiting']]))
    title = tbi.resolved_widget(CH, 'Paragraph', 'incidents-awaiting-title', {}, {}, 'section title')['spec']['widgetData']
    expect(f, 'section title, no em dash', (title['text'], title.get('level')), ('Firing alerts with no incident', 2))
    tbi.resolved_widget(CH, 'Flex', 'incidents-awaiting-section', {}, {}, 'section')
    page = lambda o: [i['resourceRefId'] for i in tbi.resolved_widget(CH, 'Flex', 'page-incidents', o, {}, 'page')['spec']['widgetData']['items']]
    expect(f, 'the page shows the section while it has rows', page(out),
           ['incidents-page-header', 'incidents-status-chips', 'incidents-table', 'incidents-awaiting-section'])
    quiet = resolve(responses(alerts={'items': [alert('disk', 'Disk pressure', 'OK')]}), {})
    expect(f, 'and leaves it out when none', page(quiet), ['incidents-page-header', 'incidents-status-chips', 'incidents-table'])
    hdr = tbi.resolved_widget(CH, 'PageHeader', 'incidents-page-header', {}, {}, 'header')['spec']['widgetData']
    expect(f, 'subtitle', hdr['subtitle'],
           'Each incident tracks one problem an alert caught, from finding its cause until it is fixed or someone closes it.')
    return f


CHECKS = [check_unfiltered, check_filtered, check_empty_and_failed, check_button_and_header, check_awaiting_section]


def main():
    global CH
    args = sys.argv[1:]
    crds_dir = args[args.index('--crds') + 1] if '--crds' in args else None
    CH = tbi.Chart(tbi.render(CHART))
    failed = 0
    for check in CHECKS:
        try:
            problems = check()
        except (AssertionError, LookupError, RuntimeError, KeyError, TypeError, IndexError) as exc:
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
