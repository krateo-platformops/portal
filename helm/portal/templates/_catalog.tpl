{{/*
  portal.catalogStep — render ONE marketplace catalog fetch step, switchable by `.Values.marketplace.source`.

  Every RESTAction that reads the blueprint/operator catalog uses this instead of an inline endpointRef step,
  so a single value flips ALL of them between the in-cluster ConfigMap (default) and the legacy external
  Helm-repo endpoint (one-release fallback) — no per-RESTAction drift.

  Both branches produce the SAME output under the step name — the parsed Helm v1 index object (with `.entries`)
  — so every downstream jq filter is UNCHANGED:
    * external  : endpointRef GET of <endpoint>/charts/<index>/index.yaml; snowplow YAML->JSON's the text/yaml
                  response (external_fetch.go), so `.<name>` is the parsed index.
    * configmap : bare-path GET of the catalog ConfigMap, then a per-step `filter` does
                  `.data["<index>-index.json"] | fromjson` — snowplow does NOT YAML->JSON an in-cluster read
                  and jq has no fromyaml, so the catalog is stored as JSON and fromjson'd here. `.<name>` is
                  again the parsed index.

  continueOnError stays true (a missing ConfigMap / unreachable endpoint degrades to an empty catalog, not a
  broken widget). errorKey is derived from the step name to match the existing <name>Error convention.

  args: dict { ctx: $ (root, for .Values/.Release), name: "<step-name>", index: "blueprints"|"operators" }
*/}}
{{- define "portal.catalogStep" -}}
{{- $ctx := .ctx -}}
{{- $name := .name -}}
{{- $index := .index -}}
{{- $mp := $ctx.Values.marketplace | default dict -}}
{{- $src := $mp.source | default "configmap" -}}
- name: {{ $name }}
{{- if eq $src "external" }}
  endpointRef:
    name: blueprints-endpoint
    namespace: {{ $ctx.Release.Namespace }}
  path: /charts/{{ $index }}/index.yaml
  verb: GET
  headers:
    - 'Accept: application/x-yaml, text/yaml, application/json'
{{- else }}
  path: /api/v1/namespaces/{{ $ctx.Release.Namespace }}/configmaps/{{ $mp.configMapName | default "blueprints-catalog-index" }}
  verb: GET
  headers:
    - 'Accept: application/json'
  filter: '.data["{{ $index }}-index.json"] | fromjson'
{{- end }}
  continueOnError: true
  errorKey: {{ $name }}Error
{{- end -}}
