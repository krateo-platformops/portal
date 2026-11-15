{{/*
  portal.readyReasonDefs — the Ready=False reasons that mean CONVERGING, not failed, and the pill
  label each one reads as. Every other Ready=False reason is a failure (NotReady).

  The set is core-provider's, not the portal's: core-provider generates the complete, closed list of
  a composition's Ready reasons (docs/ready-reasons.md, core-provider#515) and marks each with how a
  parent's rollup reads it. The rows with status False and parent verdict `converging` are exactly
  the keys below. ready-reasons.yaml at the repo root records the table this was written against,
  and scripts/test-ready-reasons.py fails when core-provider's table no longer matches it (a reason
  added, removed or reclassified), or when these keys no longer match its converging rows.

  Labels (Diego, 2026-11-15): Creating and Deleting print their own reason; HelmOperationInProgress
  and PendingHelmOperation, both timer-bounded helm operations, read Pending. Each is orange (blue in
  the cluster view's palette), never red. The condition's message, with its own duration, is shown
  verbatim elsewhere; nothing here times anything.

  Emits defs only, on ONE line with no single quote, so it can open a jq program inside a
  single-quoted YAML string ('${ ... }') as well as a filter block. Input of isConvergingFalse: a
  reason (string or null).
*/}}
{{- define "portal.readyReasonDefs" -}}
def readyFalseConverging: {"Creating": "Creating", "Deleting": "Deleting", "HelmOperationInProgress": "Pending", "PendingHelmOperation": "Pending"}; def isConvergingFalse: ((. // "") as $r | readyFalseConverging | has($r));
{{- end -}}
