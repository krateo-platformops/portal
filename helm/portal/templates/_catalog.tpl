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
  filter: '.{{ $name }}.data["{{ $index }}-index.json"] | fromjson'
{{- end }}
  continueOnError: true
  errorKey: {{ $name }}Error
{{- end -}}

{{/*
  portal.blueprintsServerUrl — the host of the krateo-blueprints Helm repos. The blueprints-endpoint
  Secret carries it as server-url; portal.catalogLiveDefs names the blueprints channel under it as the
  repo an index-only card installs from. One definition, so the two cannot point at different hosts.
*/}}
{{- define "portal.blueprintsServerUrl" -}}
https://krateo-blueprints.github.io
{{- end -}}

{{/*
  portal.catalogLiveEnabled — "true" when the blueprint RESTActions also read the LIVE blueprints index
  (marketplace.liveIndex, on unless set to false). Never with source=external: there the catalog step
  already IS the live index. Empty otherwise, so it reads as a template condition.
*/}}
{{- define "portal.catalogLiveEnabled" -}}
{{- $mp := .Values.marketplace | default dict -}}
{{- if and (ne ($mp.source | default "configmap") "external") (ne (toString $mp.liveIndex) "false") -}}
true
{{- end -}}
{{- end -}}

{{/*
  portal.catalogLiveStep — the `catalogLive` api step: the LIVE blueprints channel index,
  <server-url>/charts/blueprints/index.yaml, through the blueprints-endpoint Secret (endpointRef; the
  RESTAction never reads the Secret, snowplow resolves the Endpoint). A blueprint the Blueprint Builder
  publishes is merged into that index by its repository's release (krateo-blueprints/builder-scaffold
  -> krateo-blueprints/charts publish-chart.yaml), and the ConfigMap only learns of it when the
  marketplace-catalog chart is released again. Reading the live index as well is what lets the card
  appear on its own. continueOnError: offline, air-gapped or rate-limited, the step is empty and the
  marketplace is exactly the ConfigMap's. Renders nothing when portal.catalogLiveEnabled is empty.

  args: the root context ($)
*/}}
{{- define "portal.catalogLiveStep" -}}
{{- if include "portal.catalogLiveEnabled" . -}}
- name: catalogLive
  endpointRef:
    name: blueprints-endpoint
    namespace: {{ .Release.Namespace }}
  path: /charts/blueprints/index.yaml
  verb: GET
  headers:
    - 'Accept: application/x-yaml, text/yaml, application/json'
  continueOnError: true
  errorKey: catalogLiveError
{{- end }}
{{- end -}}

{{/*
  portal.catalogLiveDefs — jq defs for the head of a blueprint RESTAction's filter that has the
  catalogLive step beside its catalog step. Emits defs only (each ends in `;`) and no comments, so it
  sits in a literal (|) or a folded (>) filter alike.

    withLive   the input with .catalog.entries extended by every live-index chart whose NAME the
               catalog does not carry and whose newest version names the repository that released
               it (krateo.io/source-repo, stamped by the builder-scaffold release). A name the
               catalog has keeps the catalog's entry whole: the curated index stays the authority
               for every name it lists, so the live index adds cards and never re-points one. The
               source-repo rule keeps out what the curation removed on purpose: the live index also
               holds the platform's own charts (portal, installer, krateo-frontend, ...;
               PLATFORM_LEAVES in krateo-blueprints/marketplace hack/build-index.py), which carry no
               such annotation and are not blueprints. Each added version carries repoBase, the channel's
               base URL, because the publisher stores the tarball in a GitHub release, not beside
               index.yaml: the tarball URL with its filename stripped is no Helm repo, and
               core-provider fetches <chart.url>/index.yaml. With no catalogLive step (disabled, or
               source=external) or an unreadable one, it adds nothing.
*/}}
{{- define "portal.catalogLiveDefs" -}}
def withLive:
  (((.catalogLive // {}) | if type == "object" then (.entries // {}) else {} end) | if type == "object" then . else {} end) as $live
  | (((.catalog // {}) | if type == "object" then . else {} end)) as $cat
  | (($cat.entries // {}) | if type == "object" then . else {} end) as $cur
  | .catalog = ($cat | .entries = ($cur + ($live | with_entries(select(($cur[.key] == null) and ((.value | type) == "array") and ((((.value[0] // {}) | if type == "object" then ((.annotations // {})["krateo.io/source-repo"] // "") else "" end) | tostring) != "")) | .value |= map(select(type == "object") | . + {repoBase: "{{ include "portal.blueprintsServerUrl" . }}/charts/blueprints"}))))) ;
{{- end -}}
