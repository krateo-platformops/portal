{{/*
  portal.readyReasonDefs — the Ready=False reasons that mean CONVERGING, not failed, and the pill
  label each one reads as. Every other Ready=False reason is a failure (NotReady).

  The set is core-provider's, not the portal's: core-provider publishes the complete, closed list of
  a composition's condition reasons as JSON generated from its code (docs/ready-reasons.json,
  core-provider#667), each with how a parent's rollup reads it (`verdict`). The keys below are its
  Ready entries with status False and verdict `converging`, and the map is GENERATED: run
  scripts/gen-reason-defs.py, which writes it from the JSON at the core-provider ref it pins
  (CORE_PROVIDER_REF) into this file and every copy; never edit it by hand. Its --check (the
  ready-reasons workflow) fails when this file is not what it would write, and
  scripts/test-ready-reasons.py checks the keys against the JSON on its own.

  Labels (Diego, 2026-11-15) are the portal's, kept in the generator (PILL_LABELS), which refuses a
  converging reason without one: Creating and Deleting print their own reason;
  HelmOperationInProgress and PendingHelmOperation, both timer-bounded helm operations, read Pending.
  Each is orange (blue in the cluster view's palette), never red. The condition's message, with its
  own duration, is shown verbatim elsewhere; nothing here times anything.

  Emits defs only, on ONE line with no single quote, so it can open a jq program inside a
  single-quoted YAML string ('${ ... }') as well as a filter block. Input of isConvergingFalse: a
  reason (string or null).
*/}}
{{- define "portal.readyReasonDefs" -}}
def readyFalseConverging: {"Creating": "Creating", "Deleting": "Deleting", "HelmOperationInProgress": "Pending", "PendingHelmOperation": "Pending"}; def isConvergingFalse: ((. // "") as $r | readyFalseConverging | has($r));
{{- end -}}
