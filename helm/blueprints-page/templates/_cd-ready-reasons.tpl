{{/* COPIED FROM helm/portal/templates/_cd-ready-reasons.tpl so this chart can include portal.cdReadyDefs (a define lives in the chart that declares it). scripts/test-copied-helpers.py keeps it byte-identical. */}}
{{/*
  portal.cdReadyDefs — a blueprint's state, from its CompositionDefinition's Ready and Synced.

  THE VERDICTS ARE core-provider's, not the portal's: core-provider generates the complete, closed list
  of a CompositionDefinition's condition reasons (docs/cd-ready-reasons.md, core-provider#557) and marks
  each Ready reason with how a parent composition's rollup reads it (`parent verdict`). cdReadyVerdict
  maps every Ready reason in that table to its verdict, in one word (`unevaluated (rolls up as
  healthy)` is "unevaluated"). cdSyncedPaused lists the Synced reasons that mean the definition is
  paused on purpose (krateo.io/paused), which leaves Ready as it was. cd-ready-reasons.yaml at the repo
  root records the table this was written against, and scripts/test-cd-ready-reasons.py fails when
  core-provider's table no longer matches it (a reason added, removed or reclassified), or when these
  defs no longer match its rows. Keyed by reason alone: a reason two writers both write (Creating,
  Deleting) carries the same status and verdict from each, and the test fails the day it does not.

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
                Shown as pending, as a composition's ObserveFailed is (ready-reasons.yaml, #439).
                NotObserved puts its error on Synced, so with Synced=False it is failed above.
  A Ready reason the table does not list (a cluster on a newer core-provider than this portal) is read
  by its status: False is failed, True healthy, anything else unevaluated. It never reads as converging.

  Emits defs only, on ONE line with no single quote, so it can open a jq program inside a filter block
  or a single-quoted YAML string ('${ ... }'). COPIED byte-identical into blueprints-page
  (scripts/test-copied-helpers.py), because a define lives in the chart that declares it.
*/}}
{{- define "portal.cdReadyDefs" -}}
def cdReadyVerdict: {"Available": "healthy", "ScaledToZero": "healthy", "Creating": "converging", "Unavailable": "failed", "RemoteChartUnreachable": "failed", "SchemaDefaultContradiction": "failed", "ValuesSchemaMissing": "failed", "DerivedNameTooLong": "failed", "Deleting": "converging", "NotObserved": "unevaluated"}; def cdSyncedPaused: ["ReconcilePaused"]; def cdWithMessage($x): ($x.reason // "") + (if ($x.message // "") != "" then " · " + $x.message else "" end); def cdState: (. // []) as $c | ([ $c[]? | select(.type == "Ready") ][0] // {}) as $r | ([ $c[]? | select(.type == "Synced") ][0] // {}) as $s | (cdReadyVerdict[$r.reason // ""] // (if $r.status == "False" then "failed" elif $r.status == "True" then "healthy" elif $r == {} then "converging" else "unevaluated" end)) as $v | (if $s.status == "False" and (cdSyncedPaused | index($s.reason // "")) != null then "paused" elif $v == "failed" then "failed" elif $s.status == "False" and $r.status == "True" then "drift" elif $s.status == "False" then "failed" elif $r.status == "True" then "ready" elif $v == "converging" then "installing" else "unobserved" end) as $st | {state: $st, reason: (if $st == "paused" then "Paused" + (if ($r.reason // "") != "" then " · Ready: " + $r.reason else "" end) elif $st == "failed" and $v == "failed" then cdWithMessage($r) elif $st == "failed" or $st == "drift" then cdWithMessage($s) else ($r.reason // "") end)};
{{- end -}}
