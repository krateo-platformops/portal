#!/usr/bin/env python3
"""The blueprint header's health pill, over the chart's OWN jq.

A CompositionDefinition's health is Ready plus Synced (core-provider). Its other conditions can be
False on a healthy definition, so they must never make the pill read Degraded:

  AuthnMappingApplied  False/AuthnNotInstalled: no authn CRD on the controller's cluster (core-provider#504)
  ControllerActive     False/ScaledToZero: an idle opted-in controller; Ready reads True/ScaledToZero
  SchemaFullyEnforced  False/ConstraintsNotEnforced: informational (krateo-057's installer CD carries it)
  AuthnDelegation      False/DelegationUnsupported: informational
  ApiRefRBACComplete   False only explains a Synced=False, which Ready/Synced already carry

The old rule was "any condition False -> Degraded", which read a converging definition (Ready
Unknown, or no Ready yet) carrying one of these as Degraded instead of Pending.

The pill reads cdState (helm/portal/templates/_cd-ready-reasons.tpl), which classifies the Ready
reason by the verdict core-provider declares for it (docs/cd-ready-reasons.md). Ready=False is no
longer a failure by itself: Creating and Deleting are CONVERGING (Pending), and only a failed reason
(Unavailable, RemoteChartUnreachable, SchemaDefaultContradiction, ValuesSchemaMissing, DerivedNameTooLong)
reads NotReady (red, the condition's word, portal#542); a failure from Synced=False alone reads
Degraded. Synced=False/
ReconcilePaused reads Paused. The run starts with a control: with the verdict table emptied, every
Ready=False falls back to failed (the "any Ready=False" rule), and that must FAIL exactly the
CONVERGING cases, or the cases are not testing the table.

UNDER THE HEADER, THE ERROR ALERT (portal#542; mockup revision 3, "cannot be served"). When the
pill reads that failure from Ready=False, the page mounts exactly ONE red Alert, for that reason:
blueprint-detail's definitionAlert (cdReadyFailedAlert, _reason-actions.tpl) gives its title (the
portal's, CD_FAILED_TITLES in scripts/gen-reason-defs.py), its body (core-provider's action), its tag
(the reason) and its Details (the condition's message). This runs blueprint-detail's filter, the
page Flex's items and the Alert's templates as they ship, over: DerivedNameTooLong (the approved
action and the message under Details), every failed reason (its own title and a non-empty action), a
reason the JSON does not list (the generic title, never empty), and the cases that mount nothing:
Ready=True, a converging Ready=False, Ready=False with no reason (a core-provider older than the
reason: the absence rule), no conditions, Ready Unknown with Synced=False, and a paused definition.
Three controls: with the titles emptied the failed cases must lose their Alert, without the
"listed but not failed" guard the converging cases must gain one, and without the paused gate the
paused case must gain one.

THE STRINGS ARE THE VALUES core-provider EMITS (its condition types and reasons, as core-provider
listed them for this change). A misspelt type would pass silently, because an unknown type is simply
ignored by the new rule.
"""
import json
import re
import os
import subprocess
import sys
import tempfile

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CHART = os.environ.get('BLUEPRINTS_CHART') or os.path.join(REPO, 'helm', 'blueprints-page')
JQ = os.environ.get('JQ', 'jq')

VERDICTS = re.compile(r'def cdReadyVerdict: \{[^}]*\};')
NO_VERDICTS = 'def cdReadyVerdict: {};'


def c(type_, status, reason=''):
    return {'type': type_, 'status': status, 'reason': reason}


# (name, conditions, label, color). The INFORMATIONAL cases are the ones the old rule got wrong.
CASES = [
    ('no conditions', [], 'Unknown', 'gray'),
    ('healthy', [c('Ready', 'True', 'Available'), c('Synced', 'True', 'ReconcileSuccess')], 'Healthy', 'green'),
    ('drift', [c('Ready', 'True', 'Available'), c('Synced', 'False', 'ReconcileError')], 'Drift', 'violet'),
    ('ready false', [c('Ready', 'False', 'Unavailable'), c('Synced', 'True', 'ReconcileSuccess')], 'NotReady', 'red'),
    ('ready false, synced false', [c('Ready', 'False', 'Unavailable'), c('Synced', 'False', 'ReconcileError'),
                                   c('ApiRefRBACComplete', 'False', 'Missing')], 'NotReady', 'red'),
    ('ready unknown', [c('Ready', 'Unknown', 'NotObserved')], 'Pending', 'orange'),
    ('healthy + authn not installed', [c('Ready', 'True', 'Available'), c('Synced', 'True', 'ReconcileSuccess'),
                                       c('AuthnMappingApplied', 'False', 'AuthnNotInstalled')], 'Healthy', 'green'),
    ('scaled to zero', [c('Ready', 'True', 'ScaledToZero'), c('Synced', 'True', 'ReconcileSuccess'),
                        c('ControllerActive', 'False', 'ScaledToZero')], 'Healthy', 'green'),
    # --- informational: the old rule read each of these as Degraded/red ---
    ('INFORMATIONAL converging + authn not installed', [c('Ready', 'Unknown', 'NotObserved'),
                                                       c('AuthnMappingApplied', 'False', 'AuthnNotInstalled')], 'Pending', 'orange'),
    ('INFORMATIONAL converging + scaled to zero', [c('Ready', 'Unknown', 'NotObserved'),
                                                  c('ControllerActive', 'False', 'ScaledToZero')], 'Pending', 'orange'),
    ('INFORMATIONAL converging + schema not fully enforced', [c('Ready', 'Unknown', 'NotObserved'),
                                                             c('SchemaFullyEnforced', 'False', 'ConstraintsNotEnforced')], 'Pending', 'orange'),
    ('INFORMATIONAL no Ready yet + delegation unsupported', [c('AuthnDelegation', 'False', 'DelegationUnsupported')], 'Pending', 'orange'),
    # --- core-provider's verdicts (docs/cd-ready-reasons.md) ---
    ('schema default contradiction', [c('Ready', 'False', 'SchemaDefaultContradiction'),
                                      c('Synced', 'False', 'ReconcileError')], 'NotReady', 'red'),
    ('remote chart unreachable', [c('Ready', 'False', 'RemoteChartUnreachable'), c('Synced', 'True', 'ReconcileSuccess')],
     'NotReady', 'red'),
    ('values schema missing', [c('Ready', 'False', 'ValuesSchemaMissing'), c('Synced', 'True', 'ReconcileSuccess')],
     'NotReady', 'red'),
    ('derived name too long', [c('Ready', 'False', 'DerivedNameTooLong'), c('Synced', 'True', 'ReconcileSuccess')],
     'NotReady', 'red'),
    ('paused', [c('Ready', 'True', 'Available'), c('Synced', 'False', 'ReconcilePaused')], 'Paused', 'gray'),
    # The management-policy reasons (core-provider cd-ready-reasons.md, provider-runtime): a create the
    # policy withholds will not happen by itself (failed); an update it withholds leaves a working
    # definition (healthy); an unparseable policy or deletion policy is a Synced=False like any other.
    ('create withheld by policy', [c('Ready', 'False', 'CreateWithheldByPolicy'), c('Synced', 'True', 'ReconcileSuccess')], 'NotReady', 'red'),
    ('update withheld by policy', [c('Ready', 'True', 'UpdateWithheldByPolicy'), c('Synced', 'True', 'ReconcileSuccess')], 'Healthy', 'green'),
    ('management policy invalid', [c('Ready', 'True', 'Available'), c('Synced', 'False', 'ManagementPolicyInvalid')], 'Drift', 'violet'),
    ('deletion policy invalid', [c('Ready', 'True', 'Available'), c('Synced', 'False', 'DeletionPolicyInvalid')], 'Drift', 'violet'),
    ('not observed, error on Synced', [c('Ready', 'Unknown', 'NotObserved'), c('Synced', 'False', 'ReconcileError')],
     'Degraded', 'red'),
    ('a reason this portal does not know, False', [c('Ready', 'False', 'SomethingNew2028')], 'NotReady', 'red'),
    ('CONVERGING creating', [c('Ready', 'False', 'Creating'), c('Synced', 'True', 'ReconcileSuccess')], 'Pending', 'orange'),
    ('CONVERGING deleting', [c('Ready', 'False', 'Deleting')], 'Pending', 'orange'),
]


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
        proc = subprocess.run(['helm', 'template', 'b', copy, '--set', 'namespace=krateo-blueprints'],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f'helm template failed:\n{proc.stderr.strip()}')
        return [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]


def tags_program(docs):
    header = next(d for d in docs if d.get('metadata', {}).get('name') == 'blueprint-detail-page-header')
    expr = next(t['expression'] for t in header['spec']['widgetDataTemplate'] if t['forPath'] == 'tags')
    return expr.strip().removeprefix('${').removesuffix('}').strip()


def pill(program, conditions):
    data = {'detail': {'status': {'conditions': conditions}}}
    proc = subprocess.run([JQ, '-c', program], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(f'jq failed: {proc.stderr.strip()}')
    got = json.loads(proc.stdout)[0]
    return got['label'], got['color']


def failures(program, verbose):
    bad = []
    for name, conditions, label, color in CASES:
        got = pill(program, conditions)
        if got != (label, color):
            bad.append(name)
            if verbose:
                print(f'FAIL {name}: got {got[0]}/{got[1]}, want {label}/{color}')
    return bad


# ---- the error Alert -------------------------------------------------------------------------------

ALERT = 'alert-blueprint-detail-definition-failed'
HEADER, GRID = 'blueprint-detail-page-header', 'blueprint-detail-grid'
GENERIC = "This blueprint can't be offered"
TITLES = re.compile(r'def cdReadyFailedTitles: \{[^}]*\};')
PAUSED_GATE = 'if ($conds | cdState | .state) != "paused" then'
GUARD = ' or ((cdReasonActions.Ready.False[$why] // null) != null and (cdReadyFailedTitles[$why] // null) == null)'
DERIVED_MESSAGE = ('derived names do not fit: controller Deployment name '
                   '"data-platform-observability-stack-v10-20-30-controller" is 66 characters, over the 63 allowed')
FAILED = ['Unavailable', 'RemoteChartUnreachable', 'SchemaDefaultContradiction', 'ValuesSchemaMissing',
          'DerivedNameTooLong', 'CreateWithheldByPolicy']


def cm(type_, status, reason='', message=''):
    return {'type': type_, 'status': status, 'reason': reason, 'message': message}


# (name, conditions, the Alert drawn or None for no Alert). A drawn Alert is {title, description, tag, details}.
ALERT_CASES = [
    ('derived name too long', [cm('Ready', 'False', 'DerivedNameTooLong', DERIVED_MESSAGE), cm('Synced', 'True', 'ReconcileSuccess')],
     {'title': "This blueprint can't be offered: a name derived from it is too long",
      'description': "Shorten the chart's name or its version, then publish it again.",
      'tag': 'DerivedNameTooLong', 'details': DERIVED_MESSAGE}),
    ('a failed reason the JSON does not list', [cm('Ready', 'False', 'SomethingNew2028', 'the new reason says why')],
     {'title': GENERIC, 'description': '', 'tag': 'SomethingNew2028', 'details': 'the new reason says why'}),
    ('NONE ready', [cm('Ready', 'True', 'Available'), cm('Synced', 'True', 'ReconcileSuccess')], None),
    ('NONE update withheld (Ready=True)', [cm('Ready', 'True', 'UpdateWithheldByPolicy')], None),
    ('NONE CONVERGING creating', [cm('Ready', 'False', 'Creating', 'waiting for the CRD')], None),
    ('NONE CONVERGING deleting', [cm('Ready', 'False', 'Deleting')], None),
    ('NONE Ready=False without a reason (absence rule)', [cm('Ready', 'False', '', 'something')], None),
    ('NONE no conditions', [], None),
    ('NONE Ready unknown, Synced failed', [cm('Ready', 'Unknown', 'NotObserved'), cm('Synced', 'False', 'ReconcileError', 'boom')], None),
    ('NONE paused', [cm('Ready', 'False', 'DerivedNameTooLong', DERIVED_MESSAGE), cm('Synced', 'False', 'ReconcilePaused')], None),
] + [(f'failed {r}', [cm('Ready', 'False', r, f'{r} message')], 'own') for r in FAILED if r != 'DerivedNameTooLong']


def jq(program, data):
    proc = subprocess.run([JQ, '-c', program], input=json.dumps(data), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError(f'jq failed: {proc.stderr.strip()[:400]}')
    return json.loads(proc.stdout)


def template(doc, for_path):
    e = next(t['expression'] for t in doc['spec']['widgetDataTemplate'] if t['forPath'] == for_path).strip()
    return e.removeprefix('${').removesuffix('}').strip()


def drawn_page(docs, filter_program, conditions):
    """(the page's item ids, the Alert as drawn or None)."""
    by = {d['metadata']['name']: d for d in docs if d.get('metadata', {}).get('name')}
    compdef = {'apiVersion': 'core.krateo.io/v1alpha1', 'kind': 'CompositionDefinition',
               'metadata': {'name': 'data-platform-observability-stack', 'namespace': 'krateo-system',
                            'creationTimestamp': '2026-11-23T00:00:00Z'},
               'spec': {'chart': {'repo': 'data-platform-observability-stack', 'version': '10.20.30'}},
               'status': {'conditions': conditions}}
    out = jq(filter_program, {'name': compdef['metadata']['name'], 'namespace': 'krateo-system', 'compdef': compdef,
                              'catalog': {'entries': {}}, 'comps': {'items': []}})
    ids = [i['resourceRefId'] for i in jq(template(by['page-blueprint-detail'], 'items'), out)]
    if ALERT not in ids:
        return ids, None
    alert = by[ALERT]
    drawn = {t['forPath']: jq(t['expression'].strip()[2:-1], out) for t in alert['spec']['widgetDataTemplate']}
    return ids, {**alert['spec']['widgetData'], **drawn}


def alert_failures(docs, filter_program, verbose):
    bad = []
    titles = set()
    for name, conditions, want in ALERT_CASES:
        ids, got = drawn_page(docs, filter_program, conditions)
        problem = None
        if want is None:
            if got is not None or ids.count(ALERT):
                problem = f'an Alert is mounted: {got}'
        elif got is None:
            problem = 'no Alert'
        elif ids.count(ALERT) != 1 or ids.index(ALERT) != ids.index(HEADER) + 1 or ids.index(GRID) != ids.index(ALERT) + 1:
            problem = f'the Alert is not mounted once, between the header and the grid: {ids}'
        elif (got.get('type'), got.get('showIcon')) != ('error', True):
            problem = f'not a red Alert: type {got.get("type")!r}'
        elif want == 'own':
            reason = conditions[0]['reason']
            if (not got['title'].startswith(GENERIC + ': ') or got['title'] in titles or not got['description']
                    or got['tag'] != reason or got['details'] != f'{reason} message'):
                problem = f'not its own title, a non-empty action, its tag and its message: {got}'
            titles.add(got['title'])
        else:
            have = {k: got.get(k) for k in want}
            if have != want:
                problem = f'got {have}, want {want}'
        if problem:
            bad.append(name)
            if verbose:
                print(f'FAIL alert, {name}: {problem}')
    return bad


def main():
    docs = render()
    program = tags_program(docs)
    if len(VERDICTS.findall(program)) != 1:
        print('FAIL the pill does not read cdState\'s verdict table exactly once')
        return 1
    # Control: with no verdicts, every Ready=False is failed. That must fail exactly the CONVERGING cases.
    old = failures(VERDICTS.sub(NO_VERDICTS, program), verbose=False)
    converging = [name for name, *_ in CASES if name.startswith('CONVERGING')]
    if old != converging:
        print(f'FAIL control: without the verdict table the pill failed {old}, expected exactly {converging}')
        return 1
    print(f'[PASS] control: without core-provider\'s verdicts, the {len(converging)} converging cases read NotReady')
    bad = failures(program, verbose=True)
    print(f'{len(CASES) - len(bad)}/{len(CASES)} blueprint pill cases ok')

    ra = next(d for d in docs if d.get('kind') == 'RESTAction' and d['metadata']['name'] == 'blueprint-detail')
    filter_program = ra['spec']['filter']
    if len(TITLES.findall(filter_program)) != 1 or filter_program.count(GUARD) != 1 or filter_program.count(PAUSED_GATE) != 1:
        print('FAIL blueprint-detail does not read cdReadyFailedAlert\'s titles, its guard and the paused gate exactly once')
        return 1
    # Controls: without titles the failed reasons the JSON lists read "not failed", so every Alert of a
    # listed failed reason must go; without the guard a converging Ready=False gets the generic Alert.
    want_lost = [n for n, _, w in ALERT_CASES if w is not None and n != 'a failed reason the JSON does not list']
    lost = alert_failures(docs, TITLES.sub('def cdReadyFailedTitles: {};', filter_program), verbose=False)
    if lost != want_lost:
        print(f'FAIL control: without the titles the Alert cases failed {lost}, expected exactly {want_lost}')
        return 1
    want_gained = [n for n, *_ in ALERT_CASES if n.startswith('NONE CONVERGING')]
    gained = alert_failures(docs, filter_program.replace(GUARD, ''), verbose=False)
    if gained != want_gained:
        print(f'FAIL control: without the guard the Alert cases failed {gained}, expected exactly {want_gained}')
        return 1
    gained = alert_failures(docs, filter_program.replace(PAUSED_GATE, 'if true then'), verbose=False)
    if gained != ['NONE paused']:
        print(f'FAIL control: without the paused gate the Alert cases failed {gained}, expected exactly [\'NONE paused\']')
        return 1
    print(f'[PASS] controls: without the titles {len(want_lost)} failed reasons lose their Alert; without the guard '
          f'{len(want_gained)} converging reasons gain one; without the paused gate the paused one gains one')
    alert_bad = alert_failures(docs, filter_program, verbose=True)
    print(f'{len(ALERT_CASES) - len(alert_bad)}/{len(ALERT_CASES)} blueprint error Alert cases ok')
    return min(len(bad) + len(alert_bad), 125)


if __name__ == '__main__':
    sys.exit(main())
