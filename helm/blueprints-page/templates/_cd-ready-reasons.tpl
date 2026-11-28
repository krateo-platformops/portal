{{/* COPIED FROM helm/portal/templates/_cd-ready-reasons.tpl so this chart can include portal.cdReadyDefs (a define lives in the chart that declares it). scripts/test-copied-helpers.py keeps it byte-identical. */}}
{{/*
  portal.cdReadyDefs — a blueprint's state, from its CompositionDefinition's Ready and Synced.

  THE VERDICTS ARE core-provider's, not the portal's: core-provider publishes the complete, closed
  list of a CompositionDefinition's condition reasons as JSON generated from its code
  (docs/cd-ready-reasons.json, core-provider#667), each Ready reason with how a parent composition's
  rollup reads it (`verdict`: healthy, converging, failed or unevaluated). cdReadyVerdict maps every
  Ready reason in it to that verdict, and is GENERATED: run scripts/gen-reason-defs.py, which writes it
  from the JSON at the core-provider ref it pins (CORE_PROVIDER_REF) into this file and its copy; never
  edit it by hand. cdSyncedPaused, the portal's, lists the Synced reasons that mean the definition is
  paused on purpose (krateo.io/paused), which leaves Ready as it was. The generator's --check (the
  ready-reasons workflow) fails when this file is not what it would write, and
  scripts/test-cd-ready-reasons.py checks both defs against the JSON on its own. Keyed by reason
  alone: a reason two writers both write (Creating, Deleting) is one entry in the JSON, and the
  generator refuses a Ready reason that carries two verdicts.

  cdState: input a CompositionDefinition's status.conditions (array or null); output {state, reason}.
  The first state that applies:
    paused      Synced=False with a cdSyncedPaused reason. Never a failure; reason names Ready's.
    failed      a Ready reason whose verdict is failed (Unavailable, RemoteChartUnreachable,
                SchemaDefaultContradiction, ValuesSchemaMissing, DerivedNameTooLong). reason = Ready's
                reason and message, which say what is broken (each JSON pointer, for
                SchemaDefaultContradiction; each name and its length, for DerivedNameTooLong);
                Synced=False only repeats it as "observe failed: ...".
    drift       Ready=True and Synced=False: still serving, but the last pass failed. reason = Synced's.
    failed      otherwise Synced=False: the pass failed. reason = Synced's reason and message.
    ready       Ready=True.
    installing  a converging Ready reason (Creating, Deleting), or no Ready condition yet.
    unobserved  the unevaluated verdict (NotObserved: the runtime could not observe the definition).
                Shown as pending, as a composition's ObserveFailed is (#439).
                NotObserved puts its error on Synced, so with Synced=False it is failed above.
  A Ready reason the table does not list (a cluster on a newer core-provider than this portal) is read
  by its status: False is failed, True healthy, anything else unevaluated. It never reads as converging.

  Emits defs only, on ONE line with no single quote, so it can open a jq program inside a filter block
  or a single-quoted YAML string ('${ ... }'). COPIED byte-identical into blueprints-page
  (scripts/test-copied-helpers.py), because a define lives in the chart that declares it.
*/}}
{{- define "portal.cdReadyDefs" -}}
def cdReadyVerdict: {"Available": "healthy", "ScaledToZero": "healthy", "Creating": "converging", "Unavailable": "failed", "RemoteChartUnreachable": "failed", "SchemaDefaultContradiction": "failed", "ValuesSchemaMissing": "failed", "DerivedNameTooLong": "failed", "Deleting": "converging", "NotObserved": "unevaluated", "CreateWithheldByPolicy": "failed", "UpdateWithheldByPolicy": "healthy"}; def cdSyncedPaused: ["ReconcilePaused"]; def cdWithMessage($x): ($x.reason // "") + (if ($x.message // "") != "" then " · " + $x.message else "" end); def cdState: (. // []) as $c | ([ $c[]? | select(.type == "Ready") ][0] // {}) as $r | ([ $c[]? | select(.type == "Synced") ][0] // {}) as $s | (cdReadyVerdict[$r.reason // ""] // (if $r.status == "False" then "failed" elif $r.status == "True" then "healthy" elif $r == {} then "converging" else "unevaluated" end)) as $v | (if $s.status == "False" and (cdSyncedPaused | index($s.reason // "")) != null then "paused" elif $v == "failed" then "failed" elif $s.status == "False" and $r.status == "True" then "drift" elif $s.status == "False" then "failed" elif $r.status == "True" then "ready" elif $v == "converging" then "installing" else "unobserved" end) as $st | {state: $st, reason: (if $st == "paused" then "Paused" + (if ($r.reason // "") != "" then " · Ready: " + $r.reason else "" end) elif $st == "failed" and $v == "failed" then cdWithMessage($r) elif $st == "failed" or $st == "drift" then cdWithMessage($s) else ($r.reason // "") end)};
{{- end -}}
