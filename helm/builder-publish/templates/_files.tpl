{{/*
  THE HELD FILES, from either input shape.

  `files` is an array of {path, content} — what the Autopilot rail sends, because it composes the
  list in code. A human authoring in the portal cannot produce that: the Form widget renders an
  array only as an enum multi-select (SchemaFields `controlFor`), so an array of objects has no
  control and a blueprint could only ever be authored by the agent. That was the last real UI
  parity gap.

  `filesBundle` is the same information in a shape a human CAN author with an existing control:
  a MAP of in-repo path to file contents.

      { "Chart.yaml": "apiVersion: v2\nname: my-blueprint\n",
        "templates/deployment.yaml": "apiVersion: apps/v1\nkind: Deployment\n" }

  A map because that is what the Form widget can render: `SchemaFields` gives a `type: object`
  with no `properties` a JSON textarea (JsonValueInput) and submits a real object. A plain
  `type: string` would render a single-line Input — unusable for a chart tree — and an array of
  objects has no control at all. So the shape is chosen by what an existing widget can produce,
  which is the whole point: no new widget kind, no four-piece release.

  Taking a map rather than a YAML string also removes an entire failure class — there is no
  parse step here, so a malformed bundle is rejected by the form before it is ever submitted.

  PRECEDENCE: filesBundle wins when set. A hard either/or would be better, and is not expressible
  here — values.yaml ships a placeholder `files` entry so a bare `helm template` renders, which
  means "files is set" cannot distinguish a caller from the chart's own default. Rather than guess
  at that, the rule is stated: if you send a bundle, the bundle is what publishes.

  NOR IS IT EXPRESSIBLE IN THE SCHEMA, which is where it used to live and what broke this chart.
  values.schema.json carried `anyOf: [{required:[files]},{required:[filesBundle]}]` — correct JSON
  Schema, and fatal to CRD generation: core-provider copies `type` and
  `x-kubernetes-preserve-unknown-fields` into each anyOf branch, and Kubernetes forbids both inside
  a branch of a STRUCTURAL schema:

    spec.validation.openAPIV3Schema.properties[spec].anyOf[0].type:
      Forbidden: must be empty to be structural

  The CompositionDefinition therefore sat Ready=False / Synced=False and the served CRD stayed on
  the PREVIOUS chart version — which has no `filesBundle` at all, so a structural CRD silently
  PRUNED it off every claim that sent one. The human authoring form submits exactly that field, so
  its publishes committed nothing and opened empty change requests.

  The either/or is now unenforced by construction, and that is survivable because neither real
  caller can hit it: the rail always sends `files` (buildClaimPublish), the form always sends
  `filesBundle`, and `files` carries `minItems: 1` so an explicitly-empty array is already refused.
  A claim setting NEITHER inherits the placeholder above and opens a one-README change request —
  visible and harmless, where the alternative was a chart that cannot generate a CRD at all.

  Order is deterministic (sortAlpha), so re-publishing the same bundle produces the same
  LocalResource indices instead of reshuffling them into a confusing diff.
*/}}
{{- define "builder-publish.files" -}}
{{- $files := .Values.files -}}
{{- $bundle := .Values.filesBundle -}}
{{- if $bundle -}}
{{- $out := list -}}
{{- range $path := (keys $bundle | sortAlpha) -}}
{{- $content := get $bundle $path -}}
{{- if not (kindIs "string" $content) -}}
{{- fail (printf "builder-publish: filesBundle entry %q must be the file's contents as a string; got %s" $path (kindOf $content)) -}}
{{- end -}}
{{- $out = append $out (dict "path" $path "content" $content) -}}
{{- end -}}
{{- if not $out -}}
{{- fail "builder-publish: filesBundle is empty — a publish with no files would open an empty change request" -}}
{{- end -}}
{{- toYaml $out -}}
{{- else -}}
{{- toYaml ($files | default list) -}}
{{- end -}}
{{- end -}}
