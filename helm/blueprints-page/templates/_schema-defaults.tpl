{{/*
  portal.schemaDefaultsDefs — a blueprint's form defaults against its chart's values (core-provider
  2.13.137, portal#576). Approved mockup (Diego): https://claude.ai/artifact/WmME7NoHKtz4LE78EVPk35.
  Exception-only: nothing unless status.schemaDefaults is present, which core-provider omits when the
  two agree, when another definition owns the chart, and on a version older than 2.13.137. Absent
  therefore renders nothing; it is never read from the condition's message.

  Input: the CompositionDefinition's status.schemaDefaults (or null) and the action sentence.
    { differ: [{ path, schema?, values?, kind }]  (sorted by path, at most 10), dropped }
  `schema` and `values` are real JSON values of any type; they are shown as JSON text (a string in
  quotes, an object compact), never by string concatenation of the value itself.
    kind valueDiffers                 "path: form <schema> · chart <values>"
    kind schemaDefaultWithoutValuesKey "path: form default <schema> · no chart value"
    kind valuesValueInvalid           "path: chart value <values> isn't valid for the form"

  schemaDefaultsNotice($sd; $action) -> null, or
    { title, action, total, folded, label, rows: [{line}] }
  total is differ.length + dropped; above 3 the rows are folded under "Show the <total> fields",
  3 or fewer stay open. "+N more" is the last row, from `dropped`, only when it is above 0. The action
  sentence is the caller's: blueprint-detail takes it from the generated reason table
  (_reason-actions.tpl), never from a string here.

  Emits defs only, with no jq `#` comment.
*/}}
{{- define "portal.schemaDefaultsDefs" -}}
def sdRow: (.path // "") as $p | (.kind // "") as $k
  | if $k == "valueDiffers" then $p + ": form " + (.schema | tojson) + " · chart " + (.values | tojson)
    elif $k == "schemaDefaultWithoutValuesKey" then $p + ": form default " + (.schema | tojson) + " · no chart value"
    elif $k == "valuesValueInvalid" then $p + ": chart value " + (.values | tojson) + " isn't valid for the form"
    else $p end;
def schemaDefaultsNotice($sd; $action):
  if ($sd | type) != "object" then null
  else
    (($sd.differ // []) | map(select(type == "object"))) as $differ
    | (($sd.dropped // 0) | if type == "number" and . > 0 then floor else 0 end) as $dropped
    | (($differ | length) + $dropped) as $total
    | if $total == 0 then null
      else {
        title: "Form defaults differ from the chart's values",
        action: $action,
        total: $total,
        folded: ($total > 3),
        label: (if $total > 3 then "Show the " + ($total | tostring) + " fields" elif $total == 1 then "1 field" else ($total | tostring) + " fields" end),
        rows: ([ $differ[] | { line: sdRow } ] + (if $dropped > 0 then [ { line: ("+" + ($dropped | tostring) + " more") } ] else [] end))
      } end
  end;
{{- end -}}
