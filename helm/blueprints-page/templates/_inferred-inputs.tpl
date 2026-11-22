{{/*
  portal.inferredInputsDefs — say when a blueprint's inputs were INFERRED from its chart's
  values.yaml instead of described by a values.schema.json (core-provider #568 child 7).

  Approved mockup (Diego, 2026-11-22): https://claude.ai/artifact/TqxGgtmyM8smwiE83BMEjD — an info
  notice on the Create form (with core-provider's condition under Details), a shorter one on the Edit
  form, and an "Inputs" line on the blueprint's Details page. Nothing at all for an authored schema.

  Inputs, all raw data:
    the raw values.schema.json STRING from the <resource>-<version>-jsonschema-configmap (what the form
    draws), whose ROOT carries x-krateo-values-schema: {source: "inferred", rules: "1"} only when
    core-provider inferred it (it strips a forged marker from an authored schema: the authored and
    forged-marker ConfigMaps are byte-identical in its envtest sample);
    $cond, the CompositionDefinition's ValuesSchemaAuthored condition (or null): its message goes
    verbatim under Details, and Unknown/OwnedByAnotherDefinition marks a follower;
    $hidden, field names the form hides (listing a field nobody sees would be noise).

  A field ACCEPTS ANY VALUE, measured on core-provider's sample (~/krateo/samples/568-marker, the
  six-leaf fixture in the ConfigMap shape):
    a leaf with no `type`, no `x-kubernetes-int-or-string`, no `properties` and no `items`
      (`nothing: null` → {}, `ratio: 0.5` → {"default":0.5});
    an array whose `items` is {} or absent, named by the ARRAY, the field the user sees
      (`[1, true]`, `[[1],[2]]` → items {}; `empty: []` → no items).
  Not: int-or-string leaves (`replicas: 2`), objects (`labels`), open maps
  (`additionalProperties: true`), arrays of int-or-string (`[1, "a"]`). core-provider's own report
  says `lists[]` where this says `lists`, and has no finding for an empty list: by design, since this
  names what the person filling in the form sees, and the report stays verbatim under Details.
  Sorted explicitly, because jq and snowplow's gojq order object keys differently.

  Emits defs only, with no jq `#` comment, so it also works in a folded (`>`) filter.
  Used by blueprints-page (formdef, blueprint-detail) and, as a guarded copy, compositions-page
  (composition-editdef).
*/}}
{{- define "portal.inferredInputsDefs" -}}
def inferredUntypedPaths($p):
  (.properties // {}) | to_entries[]
  | (if $p == "" then .key else $p + "." + .key end) as $q
  | .value
  | if type != "object" then empty
    elif (has("type") | not) and (has("x-kubernetes-int-or-string") | not) and (has("properties") | not) and (has("items") | not) then $q
    elif .type == "array" and ((.items == null) or (.items == {})) then $q
    else inferredUntypedPaths($q) end;
def inferredSchema:
  (if type == "string" and . != "" then (try fromjson catch null) else null end)
  | if type == "object" and (((.["x-krateo-values-schema"] // {}) | if type == "object" then (.source // "") else "" end) == "inferred") then . else null end;
def inferredAnyValue($hidden):
  [ inferredUntypedPaths("") | select(. as $f | ($hidden // []) | index($f) | not) ] | sort;
def inferredAnyValueSentence($u):
  if ($u | length) == 0 then []
  elif ($u | length) == 1 then [ "1 field accepts any value: " + $u[0] + "." ]
  else [ (($u | length) | tostring) + " fields accept any value: " + ($u | join(", ")) + "." ] end;
def inferredInputsNotices($cond; $hidden; $variant):
  inferredSchema as $s
  | if $s == null then []
    else ($s | inferredAnyValue($hidden)) as $u
    | [ { title: "This form is generated from the chart’s default values",
          description: ([ (if $variant == "edit"
                            then "Each change is checked only against the type of its default. A value the chart can’t use fails when it deploys."
                            else "The blueprint’s author didn’t describe its inputs, so each field is checked only against the type of its default. A value the chart can’t use fails when it deploys, not here." end) ]
                        + inferredAnyValueSentence($u)) }
        + (if $variant != "edit" and ($cond | type) == "object" and (($cond.message // "") | tostring) != ""
           then { details: ("ValuesSchemaAuthored=" + (($cond.status // "") | tostring) + " · " + (($cond.reason // "") | tostring) + "\n" + ($cond.message | tostring)) }
           else {} end) ]
    end;
def inferredInputsLine($cond):
  if ($cond | type) == "object" and ($cond.status // "") == "Unknown" and ($cond.reason // "") == "OwnedByAnotherDefinition" then "Defined by another blueprint"
  else inferredSchema as $s
    | if $s == null then ""
      else ($s | inferredAnyValue([])) as $u
      | "Inferred from the chart’s default values"
        + (if ($u | length) == 0 then "" elif ($u | length) == 1 then " · 1 field accepts any value" else " · " + (($u | length) | tostring) + " fields accept any value" end)
      end
  end;
{{- end -}}
