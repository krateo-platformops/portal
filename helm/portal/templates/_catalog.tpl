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

  POST-FILTER (#302). A caller that needs to PROJECT the index must pass `postFilter` instead of
  appending its own `filter:` key after this include. Two keys in one YAML map decode last-wins, so
  the caller's filter silently REPLACED the configmap branch's fromjson, the projection then read
  `.entries` on the raw ConfigMap (which has none), and `// {}` turned that into an empty catalog —
  no error, zero rows. Measured on 057: global-search and blueprint-builder-deliverables had lost
  fromjson entirely while the five callers that append nothing kept it.

  The composition RE-WRAPS rather than chaining, so a caller's jq is byte-identical in both modes.
  A per-step filter receives an ENVELOPE keyed by the step name (which is why the configmap branch
  reads `.<name>.data[...]`), but after `fromjson` the parsed index is the bare value — so a plain
  `<fromjson> | <postFilter>` would leave the caller's `.<name>.entries` looking at nothing. Wrapping
  the result back under the step name restores the envelope the caller is written against:
    configmap : filter: '{<name>: (.<name>.data["<index>-index.json"] | fromjson)} | <postFilter>'
    external  : filter: '<postFilter>'        (the step output is already the parsed index)
  Omit postFilter and the behaviour is exactly as before.

  args: dict { ctx: $ (root, for .Values/.Release), name: "<step-name>", index: "blueprints"|"operators",
               postFilter: "<single-line jq>" (optional) }
*/}}
{{- define "portal.catalogStep" -}}
{{- $ctx := .ctx -}}
{{- $name := .name -}}
{{- $index := .index -}}
{{- $post := .postFilter | default "" -}}
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
{{- if $post }}
  filter: '{{ $post }}'
{{- end }}
{{- else }}
  path: /api/v1/namespaces/{{ $ctx.Release.Namespace }}/configmaps/{{ $mp.configMapName | default "blueprints-catalog-index" }}
  verb: GET
  headers:
    - 'Accept: application/json'
{{- if $post }}
  filter: '{{ "{" }}{{ $name }}: (.{{ $name }}.data["{{ $index }}-index.json"] | fromjson){{ "}" }} | {{ $post }}'
{{- else }}
  filter: '.{{ $name }}.data["{{ $index }}-index.json"] | fromjson'
{{- end }}
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

    withLive   the input with .catalog.entries merged with every live-index chart that a krateo-blueprints
               repository published: its newest version's `sources` lists
               https://github.com/krateo-blueprints/<repo>, which PUBLISHING.md requires of every chart,
               or it carries krateo.io/source-repo, which builder-scaffold repos commit. The owner is
               compared case-insensitively, as GitHub resolves it: the builders write `sources` with the
               owner as the person typed it, so Krateo-Blueprints/<repo> is the same repository. The
               prefix keeps its trailing slash, so krateo-blueprints-evil/<repo> is not. THE LIVE INDEX
               LEADS (Diego, 2026-11-07): for a name both carry, the live versions come first and the
               catalog's versions the live index lacks stay below them, newest first by version. So a
               publish shows on the next read, and the catalog is the fallback for offline or air-gapped
               installs and for names the live index does not have. A live chart with neither signal
               adds nothing, which keeps out the platform's own charts (portal, installer, ...; their
               sources are krateo-platformops repos) and any same-named chart from elsewhere. Each
               live version carries repoBase, the channel's base URL, because the publisher stores the
               tarball in a GitHub release, not beside index.yaml, and core-provider fetches
               <chart.url>/index.yaml. With no catalogLive step (disabled, or source=external) or an
               unreadable one, the catalog is unchanged.
*/}}
{{- define "portal.catalogLiveDefs" -}}
def withLiveVer: (tostring | sub("^v"; "") | split("-")[0] | split(".") | map(tonumber? // 0));
def withLiveOwned: (type == "object") and ((((.annotations // {})["krateo.io/source-repo"] // "") | tostring) != "" or any(((.sources // []) | if type == "array" then .[] else empty end); tostring | ascii_downcase | startswith("https://github.com/krateo-blueprints/")));
def withLive:
  (((.catalogLive // {}) | if type == "object" then (.entries // {}) else {} end) | if type == "object" then . else {} end) as $live
  | (((.catalog // {}) | if type == "object" then . else {} end)) as $cat
  | (($cat.entries // {}) | if type == "object" then . else {} end) as $cur
  | ($live | with_entries(select(((.value | type) == "array") and ((.value[0] // null) | withLiveOwned)) | .value |= map(select(type == "object") | . + {repoBase: "{{ include "portal.blueprintsServerUrl" . }}/charts/blueprints"}))) as $add
  | .catalog = ($cat | .entries = ($cur + ($add | with_entries(.key as $k | .value as $lv
      | .value = (($lv + ((($cur[$k] // []) | if type == "array" then . else [] end) | map(select(type == "object") | select(.version as $v | ($lv | any(.[]; .version == $v)) | not))))
                  | sort_by(.version | withLiveVer) | reverse))))) ;
{{- end -}}
