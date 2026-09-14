{{/*
portal.tierNamespace — resolve a widget tier to a namespace.
Call: {{ include "portal.tierNamespace" (dict "ctx" . "tier" "admin") }}
Returns .Values.tiers.<tier> if non-empty, else .Release.Namespace (so the
default single-namespace layout is preserved and RBAC tiering is opt-in).
*/}}
{{- define "portal.tierNamespace" -}}
{{- $ns := index (default (dict) .ctx.Values.tiers) .tier -}}
{{- if $ns -}}{{ $ns }}{{- else -}}{{ .ctx.Release.Namespace }}{{- end -}}
{{- end -}}
