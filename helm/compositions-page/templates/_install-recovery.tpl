{{/*
  portal.installRecoveryDefs: the automatic retries of a composition's first install (core-provider #636,
  core-provider > 2.13.128), shown only while the composition is not Ready (Diego, 2026-11-23). Approved
  mockup: https://claude.ai/artifact/6yK98K3k8CGHzeZEGewt3f

  Input: the composition. status.installRecovery {kind, lastAt, count, reason, message, revision} counts
  RETRIES, interrupted attempts included, and is never reset; status.installFailures {count, lastAt,
  lastError} counts consecutive failures and is removed on success. So the line says "retried", never
  "failed N times" (Ready's own message says that, and the numbers differ). Neither field, or a Ready
  composition, gives "". The error is the chart's own, verbatim, and only once core-provider itself calls
  the install failing (Ready reason InstallFailing, from the third failure).

  Defs only, one line each, no single quote.
*/}}
{{- define "portal.installRecoveryDefs" -}}
def installReady: [ (.status.conditions // [])[]? | select(.type == "Ready") ] | first; def installRetried: (.status.installRecovery // {}) as $r | if (installReady.status // "") == "True" then null elif (($r | type) == "object") and (($r.count | type) == "number") and $r.count > 0 then $r else null end; def installRetryAt: try (fromdateiso8601 | ((strftime("%d") | tonumber | tostring) + " " + strftime("%b %H:%M") + " UTC")) catch ""; def installRetryLine: installRetried as $r | if $r == null then "" else ((($r.lastAt // "") | tostring) | installRetryAt) as $at | "Install retried " + (if $r.count == 1 then "once" else (($r.count | tostring) + " times") end) + (if $at == "" then "" elif $r.count == 1 then (", on " + $at) else (", last on " + $at) end) + "." + (if $r.reason == "Failed" then " The attempt before it failed." elif $r.reason == "Interrupted" then " The attempt before it was interrupted." else "" end) end; def installErrorLine: if installRetried == null or (installReady.reason // "") != "InstallFailing" then "" else ((.status.installFailures // {}) | if type == "object" then ((.lastError // "") | tostring) else "" end) as $e | if $e == "" then "" else "Last error: " + $e end end;
{{- end -}}
