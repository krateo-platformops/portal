#!/usr/bin/env python3
"""
test-incident-reanalyze — the incident page's Re-run analysis, evaluated end to end.

WHAT IT GUARDS. A person asks alert-provider for a new root-cause analysis by setting the Incident's
spec.reanalysis (incident-controller CRD), optionally with feedback: what the analysis got wrong.
alert-provider consumes it: it clears the analysis and its check results, records the request in
status.reanalysis {at, feedback, count} and runs the analysis again. The rules, each pinned below by
what a reader would see:
  - Re-run analysis shows for an Open or Verifying incident while no request waits for alert-provider,
    and never for an Analyzing, Resolved or Closed one;
  - it opens a drawer holding only its form, one labelled field, which merge-PATCHes this incident with {spec: {reanalysis: {feedback}}},
    feedback "" when the person writes none (never an empty object), bounded like the CRD (2000);
  - a waiting request and a recorded reanalysis show in the incident's meta items;
  - a precondition or verify whose last run gave no verdict shows what the run printed
    (status.lastChecks.<script>.output); the apply step never does;
  - the Flagged note offers the re-run; a flagged Open or Verifying incident says "Precondition
    flagged" in its header, as a note with the reason on hover, never as a pill beside the state,
    and a Resolved or Closed one says nothing of it (audit INC-7);
  - an incident no model analyses (its alert is analysed by a person, or no analysis agent is
    installed) is information, never a failed analysis: no Re-run and no "Analyzed";
  - a Resolved or Closed incident names what ended it (status.resolution.by): the verify script, a
    person, or an automated close (alert-gone: its Alert was deleted; analysis-failed: the analysis
    failed), and a value the page does not know yet reads as itself, never as "a person". A Resolved
    incident whose header already says who resolved it and when has no Resolved item in its meta row
    (audit INC-7: the time once); Closed keeps its item, as the header has no line for Closed.

HOW. As test-incident-apply: helm/portal is rendered, incident-detail is resolved over fixtures shaped
like krateo-057's Incidents, and the widgets are evaluated over its output.

With --crds DIR (a frontend-crds chart), every widget resolved here is validated against its CRD.
Usage: test-incident-reanalyze.py [--crds DIR]. Exit code = failed checks.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(mod, file):
    spec = importlib.util.spec_from_file_location(mod, os.path.join(HERE, file))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tpr = _load('tpr', 'test-platform-review.py')
tbi = tpr.tbi
NS = tbi.NS
expect, apply_overrides, ref_template = tpr.expect, tpr.apply_overrides, tpr.ref_template
WIDGETS = (('Flex', 'incident-detail-actions'), ('Button', 'incident-detail-reanalyze'),
           ('Form', 'incident-reanalyze-form'),
           ('Markdown', 'incident-rem-step-md-0'), ('Markdown', 'incident-rem-step-md-1'),
           ('Markdown', 'incident-rem-step-md-2'), ('Alert', 'incident-no-analysis'),
           ('PageHeader', 'incident-detail-page-header'))

# As krateo-057 carries it (sre-probe-failing-20261011-224948): verify exits 2 on a jq parse error.
JQ_ERROR = ('jq: error: Invalid escape at line 1, column 4 (while parsing \'"\\x"\') at <top-level>, line 1:\n'
            'jq: 4 compile errors')


def incident(name, state='Open', flagged=False, last_checks=None, spec_reanalysis=None, reanalysis=None):
    spec = {'alertRef': {'name': 'sre-probe-failing', 'namespace': NS}, 'trigger': 'alert',
            'triggeredAt': '2026-10-11T22:49:48Z'}
    if spec_reanalysis is not None:
        spec['reanalysis'] = spec_reanalysis
    status = {'state': state,
              'howToFix': {'precondition': '#!/usr/bin/env bash\n# Holds while the filter is missing.\nexit 1\n',
                           'apply': '#!/usr/bin/env bash\n# Add the filter.\nkubectl patch ...\n',
                           'verify': '#!/usr/bin/env bash\n# Fixed once the filter is there.\nexit 0\n'},
              'rootCause': {'category': 'config', 'confidence': '0.85', 'statement': 'The alert counts DELETED events.'},
              'completedAt': '2026-10-11T22:59:00Z',
              'lastChecks': last_checks or {'precondition': {'exit': 1, 'since': '2026-10-11T23:00:00Z'}},
              'conditions': [{'type': 'Reproduced', 'status': 'False' if flagged else 'True'}]}
    if reanalysis is not None:
        status['reanalysis'] = reanalysis
    return {'apiVersion': 'observability.krateo.io/v1alpha1', 'kind': 'Incident',
            'metadata': {'name': name, 'namespace': NS, 'creationTimestamp': '2026-10-11T22:49:48Z',
                         'labels': {'observability.krateo.io/alert': 'sre-probe-failing'}},
            'spec': spec, 'status': status}


def resolve(chart, inc, label):
    ex = {'name': inc['metadata']['name'], 'namespace': NS}
    out = tpr.resolved(chart, 'incident-detail', {'incident': inc, 'incidents': {'items': [inc]},
                                                   'incidentApplies': 'ERROR:the server could not find the requested resource',
                                                   'alert': 'ERROR:alerts "sre-probe-failing" not found'}, ex)
    w = {n: tbi.resolved_widget(chart, k, n, out, ex, f'{label}: {k} {n}')['spec'].get('widgetData', {})
         for k, n in WIDGETS}
    return out, w


def actions(w):
    return [i['resourceRefId'] for i in w['incident-detail-actions']['items']]


def check_shown_for_open_and_verifying_only(chart):
    f = []
    for state, want in (('Open', True), ('Verifying', True), ('Analyzing', False), ('Resolved', False), ('Closed', False)):
        out, w = resolve(chart, incident('st-1', state=state), state)
        expect(f, f'{state}: canReanalyze', out['canReanalyze'], want)
        expect(f, f'{state}: button', 'incident-detail-reanalyze' in actions(w), want)
    out, w = resolve(chart, incident('pend-1', spec_reanalysis={'feedback': ''}), 'pending')
    expect(f, 'a waiting request hides it', 'incident-detail-reanalyze' in actions(w), False)
    out, w = resolve(chart, incident('ord-1'), 'order')
    expect(f, 'beside Diagnose, before Close', actions(w)[:3],
           ['incident-detail-diagnose', 'incident-detail-reanalyze', 'incident-detail-close'])
    return f


def check_no_analysis_agent(chart):
    """alert-provider >= 0.2.88: AnalysisUnavailable=True/NoAnalysisAgent on an open incident. Nothing
    failed and nothing would answer a Re-run, so the page says so as information and offers none."""
    f = []
    msg = 'No analysis agent is installed here: a person looks into this incident.'

    def no_agent(name, state='Open', status='True', error=None):
        inc = incident(name, state=state)
        inc['status'] = {'state': state, 'completedAt': '2026-10-11T22:59:00Z',
                         'conditions': [{'type': 'AnalysisUnavailable', 'status': status,
                                         'reason': 'NoAnalysisAgent' if status == 'True' else 'AnalysisAgentAvailable', 'message': msg},
                                        {'type': 'Ready', 'status': 'True'}]}
        if error:
            inc['status']['error'] = error
        return inc

    out, w = resolve(chart, no_agent('na-1'), 'no agent')
    expect(f, 'no Re-run', (out['canReanalyze'], 'incident-detail-reanalyze' in actions(w)), (False, False))
    expect(f, 'its own notice, in alert-provider\'s words; no failure and no empty-report sentence',
           (out['sections']['noAnalysis'], out['noAnalysisText'], out['sections']['error'], out['sections']['prose']), (True, msg, False, False))
    expect(f, 'the notice is information, titled as such', (w['incident-no-analysis']['type'], w['incident-no-analysis']['title']),
           ('info', 'No analysis on this platform'))
    expect(f, 'completedAt is not an analysis: no "Analyzed"', [i['label'] for i in out['metaItems']], ['Started'])
    out, _ = resolve(chart, no_agent('na-2', error='old failure'), 'no agent, error')
    expect(f, 'the condition wins over a leftover status.error', (out['sections']['noAnalysis'], out['sections']['error']), (True, False))
    out, _ = resolve(chart, no_agent('na-3', status='False'), 'agent arrived')
    expect(f, 'False: an agent is there, Re-run is offered', (out['sections']['noAnalysis'], out['canReanalyze']), (False, True))
    out, _ = resolve(chart, no_agent('na-4', state='Closed'), 'closed')
    expect(f, 'a closed incident keeps what it had: not read there', out['sections']['noAnalysis'], False)
    return f


# alert-provider's HumanOnly, which it writes into status.error of every incident of an alert labelled
# observability.krateo.io/analysis=human (internal/incident/writer.go).
HUMAN_ONLY = 'This alert is analysed by a person: no model reads what it matched.'


def check_analysed_by_a_person(chart):
    """An alert labelled observability.krateo.io/analysis=human (copied to its incidents) is analysed by a
    person by design: alert-provider opens its incident Open, with completedAt and HumanOnly in
    status.error, and drops any reanalysis request. Nothing failed, nothing was analysed, nothing would
    answer a Re-run (krateo-057: sre-incident-checks-unknown-20261114-171427 read "The analysis failed")."""
    f = []

    def person(name, state='Open'):
        inc = incident(name, state=state)
        inc['metadata']['labels']['observability.krateo.io/analysis'] = 'human'
        inc['status'] = {'state': state, 'error': HUMAN_ONLY, 'completedAt': '2026-11-14T17:14:27Z'}
        return inc

    out, w = resolve(chart, person('hp-1'), 'by a person')
    expect(f, 'no failure banner and no empty-report sentence', (out['sections']['error'], out['sections']['prose']), (False, False))
    expect(f, 'a notice, information, in the page\'s words',
           (out['sections']['noAnalysis'], w['incident-no-analysis']['type'], w['incident-no-analysis']['title'], w['incident-no-analysis']['description']),
           (True, 'info', 'Analyzed by a person',
            'This alert is analyzed by a person: no model reads what it matched, so no analysis or fix is proposed here.'))
    expect(f, 'no Re-run: alert-provider drops the request', (out['canReanalyze'], 'incident-detail-reanalyze' in actions(w)), (False, False))
    expect(f, 'no "Analyzed" anywhere', ([i['label'] for i in out['metaItems']], out['headerProgress']), (['Started'], None))
    expect(f, 'Close is still offered', out['canClose'], True)
    out, w = resolve(chart, person('hp-2', state='Closed'), 'by a person, closed')
    expect(f, 'closed: still no failure, still said (a label does not change)',
           (out['sections']['error'], out['sections']['noAnalysis']), (False, True))
    plain = incident('hp-3')
    plain['metadata']['labels']['observability.krateo.io/analysis'] = 'model'
    plain['status'].update({'error': 'the model timed out'})
    del plain['status']['howToFix'], plain['status']['rootCause']
    out, _ = resolve(chart, plain, 'another label value')
    expect(f, 'any other label value: a failure is still one', (out['sections']['error'], out['sections']['noAnalysis']), (True, False))
    return f


def check_drawer_form_patches_spec_reanalysis(chart):
    f = []
    out, w = resolve(chart, incident('form-1'), 'form')
    drawer = w['incident-detail-reanalyze']['actions']['openDrawer'][0]
    expect(f, 'the button opens the form in a drawer', (drawer['type'], drawer['resourceRefId']), ('openDrawer', 'incident-reanalyze-form'))
    form = w['incident-reanalyze-form']
    expect(f, 'one field, labelled and nothing more', (list(form['schema']['properties']), form['schema']['properties']['feedback'].get('title'),
           'description' in form['schema']['properties']['feedback']), (['feedback'], 'What is wrong with the analysis (optional)', False))
    act = form['actions']['rest'][0]
    expect(f, 'submit is the PATCH', (form['submitActionId'], act['id'], act['type']), ('reanalyze', 'reanalyze', 'rest'))
    expect(f, 'a merge-patch', act['headers'], ['Content-Type: application/merge-patch+json'])
    body = apply_overrides(act['payload'], act.get('payloadToOverride'), {'feedback': 'the verify jq does not parse'})
    expect(f, 'body with feedback', body, {'spec': {'reanalysis': {'feedback': 'the verify jq does not parse'}}})
    body = apply_overrides(act['payload'], act.get('payloadToOverride'), {})
    expect(f, 'body without feedback is never an empty object', body, {'spec': {'reanalysis': {'feedback': ''}}})
    expect(f, 'feedback is optional, bounded like the CRD',
           (form['schema'].get('required'), form['schema']['properties']['feedback']['maxLength']), (None, 2000))
    ref = ref_template(chart, 'Form', 'incident-reanalyze-form', 'reanalyze-incident', out)
    expect(f, 'target is this incident', (ref['apiVersion'], ref['resource'], ref['name'], ref['namespace'], ref['verb']),
           ('observability.krateo.io/v1alpha1', 'incidents', 'form-1', NS, 'PATCH'))
    return f


def check_meta_items_show_the_reanalysis(chart):
    f = []
    out, _ = resolve(chart, incident('m-1'), 'none')
    expect(f, 'no reanalysis: no item; Open, "Analyzed" is the header\'s alone', [i['label'] for i in out['metaItems']], ['Started'])
    out, _ = resolve(chart, incident('m-1v', state='Verifying'), 'verifying')
    expect(f, 'Verifying: the header says something else, so the row says when it was analyzed',
           [i['label'] for i in out['metaItems']], ['Started', 'Analyzed'])
    out, _ = resolve(chart, incident('m-2', spec_reanalysis={'feedback': 'x'}), 'pending')
    expect(f, 'a waiting request', out['metaItems'][-1], {'label': 'Re-analysis', 'value': 'requested, waiting to start'})
    out, _ = resolve(chart, incident('m-3', reanalysis={'at': '2026-10-21T10:00:00Z', 'count': 2,
                                                        'feedback': 'the verify jq does not parse'}), 'recorded')
    expect(f, 'a recorded reanalysis', out['metaItems'][-1],
           {'label': 'Re-analysis', 'value': '#2 · 2026-10-21 10:00 UTC · "the verify jq does not parse"'})
    out, _ = resolve(chart, incident('m-4', reanalysis={'at': '2026-10-21T10:00:00Z', 'count': 1}), 'no feedback')
    expect(f, 'a recorded reanalysis without feedback', out['metaItems'][-1],
           {'label': 'Re-analysis', 'value': '#1 · 2026-10-21 10:00 UTC'})
    return f


# status.resolution.by -> the meta item a reader sees. "Resolved" only for verify; everything else is
# "Closed". The automated closes (incident-provider's alert-gone, alert-provider's analysis-failed)
# must never read "by a person", and neither may a value the page has not been taught yet.
# A Resolved incident the header names the resolver of (verify, user) has no item: None below.
RESOLUTIONS = [
    ('verify', 'Resolved', None, None),
    ('user', 'Resolved', None, None),
    ('budget-exceeded', 'Resolved', 'Closed', '2026-10-21 10:00 UTC (budget-exceeded)'),
    ('user', 'Closed', 'Closed', '2026-10-21 10:00 UTC by a person'),
    ('alert-gone', 'Closed', 'Closed', '2026-10-21 10:00 UTC automatically: its Alert was deleted'),
    ('analysis-failed', 'Closed', 'Closed', '2026-10-21 10:00 UTC automatically: the analysis failed'),
    ('budget-exceeded', 'Closed', 'Closed', '2026-10-21 10:00 UTC (budget-exceeded)'),
    (None, 'Closed', 'Closed', '2026-10-21 10:00 UTC (unknown)'),
    ('', 'Closed', 'Closed', '2026-10-21 10:00 UTC (unknown)'),
]


def check_meta_items_name_what_ended_it(chart):
    f = []
    for by, state, label, value in RESOLUTIONS:
        inc = incident(f'r-{by or "none"}', state=state)
        inc['status']['resolution'] = {'at': '2026-10-21T10:00:00Z'} if by is None else {'at': '2026-10-21T10:00:00Z', 'by': by}
        out, _ = resolve(chart, inc, f'resolution {by!r}')
        if label is None:
            expect(f, f'{state}, resolution.by={by!r}: the header says it, the meta row does not',
                   [i['label'] for i in out['metaItems']], ['Started', 'Analyzed'])
        else:
            expect(f, f'{state}, resolution.by={by!r}', out['metaItems'][-1], {'label': label, 'value': value})
    inc = incident('r-open')
    out, _ = resolve(chart, inc, 'no resolution')
    expect(f, 'no resolution: no item', [i['label'] for i in out['metaItems']], ['Started'])
    return f


def check_an_unknown_result_shows_what_it_printed(chart):
    f = []
    lc = {'precondition': {'exit': 0, 'since': '2026-10-19T15:31:15Z'},
          'verify': {'exit': 2, 'since': '2026-10-19T15:41:19Z', 'output': JQ_ERROR},
          'apply': {'exit': 2, 'since': '2026-10-19T15:35:00Z', 'output': 'never shown'}}
    out, w = resolve(chart, incident('u-1', state='Verifying', last_checks=lc), 'unknown')
    md2 = w['incident-rem-step-md-2']['markdown']
    if '**Last run:** unknown · since 2026-10-19 15:41 UTC\n\nIt printed:\n\n```text\n' + JQ_ERROR + '\n```' not in md2:
        f.append(f'verify does not show what its run printed: {md2[:500]!r}')
    if 'It printed' in w['incident-rem-step-md-0']['markdown']:
        f.append('a verdict shows output')
    if 'It printed' in w['incident-rem-step-md-1']['markdown']:
        f.append('the apply step shows output')
    return f


def check_flagged_note_offers_the_rerun(chart):
    f = []
    out, w = resolve(chart, incident('fl-1', flagged=True, last_checks={'precondition': {'exit': 0, 'since': '2026-10-20T10:46:00Z'}}),
                     'flagged')
    if 'Apply the fix, re-run the analysis, or close the incident.' not in w['incident-rem-step-md-0']['markdown']:
        f.append('the Flagged note does not offer the re-run')
    expect(f, 'flagged: button', 'incident-detail-reanalyze' in actions(w), True)
    note = {'text': 'Precondition flagged',
            'tooltip': "Its first run already passed, so it can't see the problem, and it no longer runs. "
                       'Apply the fix, re-run the analysis, or close the incident.'}
    for state, want in (('Open', note), ('Verifying', note), ('Resolved', {'text': ''}), ('Closed', {'text': ''})):
        out, w = resolve(chart, incident(f'fl-{state}', state=state, flagged=True,
                                         last_checks={'precondition': {'exit': 0, 'since': '2026-10-20T10:46:00Z'}}), f'flagged {state}')
        header = w['incident-detail-page-header']
        expect(f, f'flagged, {state}: the header note', header.get('note'), want)
        expect(f, f'flagged, {state}: the state pill alone', [t['label'] for t in header['tags']], [state])
    out, w = resolve(chart, incident('fl-not'), 'not flagged')
    expect(f, 'not flagged: an empty note, which draws nothing', w['incident-detail-page-header'].get('note'), {'text': ''})
    return f


CHECKS = [
    check_no_analysis_agent,
    check_analysed_by_a_person,
    check_shown_for_open_and_verifying_only,
    check_drawer_form_patches_spec_reanalysis,
    check_meta_items_show_the_reanalysis,
    check_meta_items_name_what_ended_it,
    check_an_unknown_result_shows_what_it_printed,
    check_flagged_note_offers_the_rerun,
]


def main():
    args = sys.argv[1:]
    crds_dir = None
    if '--crds' in args:
        i = args.index('--crds')
        crds_dir = args[i + 1]
        del args[i:i + 2]
    chart = tbi.Chart(tbi.render(args[0]) if args else tbi.render_portal_and_pages())
    failed = 0
    for check in CHECKS:
        try:
            problems = check(chart)
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
