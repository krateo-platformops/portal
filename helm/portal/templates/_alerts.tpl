{{/*
portal.alertWhereEvents — the WHERE prefix every Kubernetes-EVENT alert shares.

Events arrive with ResourceAttributes['telemetry.source'] = 'k8s-events' and carry the whole event
object as JSON in Body. This prefix is also what makes event alerts structurally immune to the
self-match problem that log alerts have: the observability stack echoes each alert's own query into
its container logs, and container logs carry source = '' — so an event alert can never count the
log line in which HyperDX repeats the alert's own filter.
*/}}
{{- define "portal.alertWhereEvents" -}}
ResourceAttributes['telemetry.source'] = 'k8s-events'
{{- end -}}

{{/*
portal.alertWhereLogs — the WHERE prefix every CONTAINER-LOG alert shares.

Log alerts do NOT get the immunity described above, so they must exclude the observability stack by
ServiceName or they diagnose themselves: HyperDX logs the query it is running, that line contains
the alert's own search terms, and the alert counts it. The exclusion list is the observability
pipeline plus the agents that read it.
*/}}
{{- define "portal.alertWhereLogs" -}}
ResourceAttributes['telemetry.source'] = '' AND ServiceName NOT IN ('krateo-observability', 'krateo-alert-troubleshooter', 'krateo-clickstack', 'krateo-clickstack-clickhouse-clickhouse', 'krateo-clickstack-keeper-keeper', 'krateo-clickstack-mongodb', 'clickhouse-mcp-server', 'clickhouse-operator', 'incident-agent', 'krateo-autopilot', 'kagent', 'repo-mcp-server')
{{- end -}}

{{/*
portal.alertEnabled — is this catalogue entry shipped?
Call: {{ include "portal.alertEnabled" (dict "ctx" . "entry" $entry) }} — non-empty means yes.

core     ships whenever .Values.alerts.enabled
extended ships only when .Values.alerts.extended is also set
anything named in .Values.alerts.disabled is skipped regardless of tier.
*/}}
{{- define "portal.alertEnabled" -}}
{{- $a := .ctx.Values.alerts -}}
{{- if $a.enabled -}}
{{- if not (has .entry.name (default (list) $a.disabled)) -}}
{{- if or (eq .entry.tier "core") $a.extended -}}yes{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
portal.alertThreshold — the entry's threshold, unless the deployer overrode it in
.Values.alerts.thresholds.<name>. Thresholds are the one field an MSP always retunes per cluster,
because the right number depends on a baseline only that cluster knows.
*/}}
{{- define "portal.alertThreshold" -}}
{{- $o := index (default (dict) .ctx.Values.alerts.thresholds) .entry.name -}}
{{- if not (kindIs "invalid" $o) -}}{{ $o }}{{- else -}}{{ .entry.threshold }}{{- end -}}
{{- end -}}
