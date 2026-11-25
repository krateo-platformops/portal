{{/* COPIED FROM helm/portal/templates/_composition-state.tpl so this chart can include portal.compositionStateDefs (a define lives in the chart that declares it). scripts/test-copied-helpers.py keeps it byte-identical. */}}
{{/*
  portal.compositionStateDefs — ONE state for a composition, whatever page shows it (visual audit
  STATE-1, 2026-11-25: one paused composition read Paused on /compositions, Ready on its blueprint
  and "need attention" beside the list; a composition nothing can assess read Pending in one place and
  Unknown in the next). Every page that names a composition's state reads it from here, so two pages
  can no longer disagree about one object.

  The rule is the Compositions list's (portal 1.8.172), in this order, the first that holds:
    paused      Ready's reason is ReconcilePaused or ReconcileGracefullyPaused. Paused wins: a paused
                composition reports the Ready it had before the pause, so it is never Ready, never
                NotReady and never converging.
    failed      NotReady: Ready=False with a reason that is not converging (portal.readyReasonDefs),
                or an unrecognised Ready=Unknown whose reconcile fails (Synced=False).
    degraded    Ready=True, but the workload projection says unhealthy (status.workloadHealthy false).
    healthy     Ready=True and not Synced=False.
    drift       Ready=True and Synced=False.
    unreadable  Ready=Unknown, ChildrenForbidden (Children unreadable).
    unknown     Ready=Unknown, ZeroEvidence: nothing can assess it. Unknown, never Pending.
    pending     anything else: converging, or no verdict yet.

  Input of compositionVerdict: an object with `conditions` (status.conditions) and `wh`
  (status.workloadHealthy, null when the engine does not project it). Output: the facts the rule
  reads, and `state`. compositionStateName maps a state to the word every page prints.
  Needs portal.readyReasonDefs before it (isConvergingFalse). Defs only, no single quote and no `#`,
  so it can open a jq program in a folded or literal block.
*/}}
{{- define "portal.compositionStateDefs" -}}
def compositionVerdict: ((.conditions // []) | if type == "array" then . else [] end) as $c | (.wh) as $wh
  | (any($c[]?; .type == "Ready" and .status == "True")) as $ready
  | (any($c[]?; .type == "Ready" and .status == "False" and (.reason | isConvergingFalse | not))
     or (any($c[]?; .type == "Ready" and .status == "Unknown" and .reason != "ZeroEvidence" and .reason != "ChildrenForbidden")
         and any($c[]?; .type == "Synced" and .status == "False"))) as $notready
  | (any($c[]?; .type == "Ready" and (.reason == "ReconcilePaused" or .reason == "ReconcileGracefullyPaused"))) as $paused
  | (any($c[]?; .type == "Synced" and .status == "False")) as $unsynced
  | ($ready and ($wh == false)) as $degraded
  | (any($c[]?; .type == "Ready" and .status == "Unknown" and .reason == "ChildrenForbidden")) as $unreadable
  | (any($c[]?; .type == "Ready" and .status == "Unknown" and .reason == "ZeroEvidence")) as $silent
  | { ready: $ready, notready: $notready, paused: $paused, unsynced: $unsynced, degraded: $degraded, unreadable: $unreadable, silent: $silent,
      state: (if $paused then "paused" elif $notready then "failed" elif $degraded then "degraded"
              elif $ready and ($unsynced | not) then "healthy" elif $ready and $unsynced then "drift"
              elif $unreadable then "unreadable" elif $silent then "unknown" else "pending" end) };
def compositionStateName: {healthy: "Ready", paused: "Paused", degraded: "Degraded", drift: "Drift", failed: "NotReady", unreadable: "Children unreadable", unknown: "Unknown", pending: "Pending"}[.] // .;
{{- end -}}
