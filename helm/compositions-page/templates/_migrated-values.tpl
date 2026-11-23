{{/* COPIED FROM helm/blueprints-page/templates/_migrated-values.tpl so compositions-page can include portal.migratedValuesDefs (a define lives in the chart that declares it). Keep it byte-identical below this header: scripts/test-copied-helpers.py. */}}
{{/*
  portal.migratedValuesDefs — after a blueprint version migration, the compositions whose values the
  served version refuses (core-provider #623, condition MigratedValuesValid). Editing a refused field
  is refused until its value satisfies the new version; the composition keeps running.

  Approved mockup (Diego, 2026-11-23): https://claude.ai/artifact/MbNWkNtpomFXsWJPArjk3U. Exception-only:
  nothing when every composition passed or before any migration.

  Input: the CompositionDefinition's status.migratedValues (or null) and its MigratedValuesValid
  condition (or null). The field, measured on core-provider's envtest sample
  (~/krateo/samples/614-migrated-values):
    { version, refusedCount, uncheckedCount,
      refused:   [{ namespace, name, fields: [...] (capped 5), fieldCount (uncapped) }]  (capped 10),
      unchecked: [{ namespace, name }]                                                   (capped 10) }
  absent on AllValid and before any migration; ALSO absent, with the condition Unknown, when the
  instances could not be listed. Absent therefore means "no data", never "all clear".

  migratedValuesDetails($mv; $cond) -> { line, alert }      the blueprint Details row and its Alert
  migratedValuesEditNotices($mv; $ns; $name; $version)      the Edit form's `notices` for one composition

  Emits defs only, with no jq `#` comment, so it also works in a folded (`>`) filter.
  Used by blueprints-page (blueprint-detail) and, as a guarded copy, compositions-page (composition-editdef).
*/}}
{{- define "portal.migratedValuesDefs" -}}
def mvPlural($n; $one; $many): ($n | tostring) + " " + (if $n == 1 then $one else $many end);
def mvRefusedLabel: (.namespace // "") + "/" + (.name // "") + (if ((.fields // []) | length) > 0 then " (" + ((.fields // []) | join(", ")) + (if (.fieldCount // 0) > ((.fields // []) | length) then ", and " + (((.fieldCount // 0) - ((.fields // []) | length)) | tostring) + " more" else "" end) + ")" else "" end);
def mvName: (.namespace // "") + "/" + (.name // "");
def mvMore($count; $listed): if $count > $listed then ", and " + (($count - $listed) | tostring) + " more" else "" end;
def migratedValuesDetails($mv; $cond):
  if ($mv | type) == "object" and (($mv.refusedCount // 0) > 0) then
    ($mv.version // "the new version") as $v
    | ($mv.refused // []) as $r
    | {
        line: (mvPlural($mv.refusedCount; "composition holds"; "compositions hold") + " values version " + $v + " refuses: " + ([ $r[] | mvRefusedLabel ] | join("; ")) + mvMore($mv.refusedCount; ($r | length))),
        alert: {
          type: "warning",
          title: (if $mv.refusedCount == 1 then "1 composition can’t be edited until its values are fixed" else (($mv.refusedCount | tostring) + " compositions can’t be edited until their values are fixed") end),
          description: ("Version " + $v + " refuses " + (if $mv.refusedCount == 1 and ($r | length) == 1 then "what " + ($r[0] | mvName) + " holds for " + (($r[0].fields // []) | join(", ")) + ". Saving a change to " + (if (($r[0].fieldCount // 0)) == 1 then "that field" else "those fields" end) + " is refused until the value satisfies the new version. The composition keeps running."
                        else "values they hold. Saving a change to a refused field is refused until its value satisfies the new version; each composition’s Edit form names its fields. The compositions keep running." end))
        }
      }
  elif ($mv | type) == "object" and (($mv.uncheckedCount // 0) > 0) then
    ($mv.version // "the new version") as $v
    | ($mv.unchecked // []) as $u
    | {
        line: (mvPlural($mv.uncheckedCount; "composition"; "compositions") + " couldn’t be checked against " + $v + ": " + ([ $u[] | mvName ] | join(", ")) + mvMore($mv.uncheckedCount; ($u | length))),
        alert: {
          type: "info",
          title: (mvPlural($mv.uncheckedCount; "composition"; "compositions") + " couldn’t be checked"),
          description: ("The dry run against " + $v + " failed for a reason other than " + (if $mv.uncheckedCount == 1 then "its" else "their" end) + " values, so whether they pass isn’t known. core-provider checks again on its next reconcile.")
        }
      }
  elif ($cond | type) == "object" and ($cond.status // "") == "Unknown" and ($mv | type) != "object" then
    { line: "Not checked yet", alert: null }
  else { line: "", alert: null } end;
def migratedValuesEditNotices($mv; $ns; $name; $version):
  if ($mv | type) != "object" then []
  else
    ($mv.version // "the new version") as $v
    | ([ ($mv.refused // [])[] | select((.namespace // "") == $ns and (.name // "") == $name) ] | first) as $mine
    | ([ ($mv.unchecked // [])[] | select((.namespace // "") == $ns and (.name // "") == $name) ] | first) as $unchecked
    | if $mine != null then
        (($mine.fieldCount // (($mine.fields // []) | length))) as $n
        | [ { type: "warning",
              title: ("Version " + $v + " refuses this composition’s current value for " + mvPlural($n; "field"; "fields")),
              description: [ ("Saving a change to " + (($mine.fields // []) | join(", ")) + mvMore($n; (($mine.fields // []) | length)) + " is refused until " + (if $n == 1 then "its value satisfies" else "their values satisfy" end) + " " + $v + ". Fix " + (if $n == 1 then "it" else "them" end) + " here, then save.") ] } ]
      elif $unchecked != null then
        [ { title: ("This composition couldn’t be checked against version " + $v),
            description: [ "The check failed for a reason other than its values, so whether they pass isn’t known. core-provider checks again on its next reconcile." ] } ]
      elif (($mv.refusedCount // 0) > (($mv.refused // []) | length)) and $version == $v then
        [ { type: "warning",
            title: ("Version " + $v + " refuses some compositions’ values"),
            description: [ ((($mv.refusedCount // 0) | tostring) + " compositions hold values it refuses, and only the first " + ((($mv.refused // []) | length) | tostring) + " are named, so this one may be among them. If saving is refused, the error names the field.") ] } ]
      else [] end
  end;
{{- end -}}
