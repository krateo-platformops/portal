{{/*
  portal.blueprintContentsDefs — "What it creates" for a blueprint, from its CompositionDefinition.

  core-provider renders the blueprint's chart at its DEFAULT values when it registers or its chart
  changes, and records the kinds in status.contents (core-provider#599, merged as #605): exactly what
  `helm get manifest` would show, subcharts in, hooks and crds/ out. UX review round 3, approved mockup
  https://claude.ai/artifact/XTHrG6nao9fagYyEf7gL7q. Input: the CompositionDefinition, or null when the
  blueprint is not installed. $cur: the installed chart version. States, none of them an empty list
  that would read as "creates nothing":
    notInstalled  no CompositionDefinition (the marketplace page): known once installed
    none          no status.contents: a core-provider older than the field, or not reconciled yet
    unknown       status.contents.unknown, set INSTEAD of kinds: the chart cannot render at its
                  defaults; helm's offline message (no apiserver text), capped at 1024 bytes
    empty         rendered, and it creates nothing at its defaults: said in words
    known         the kinds; core-provider caps them at 50 and counts the rest in `omitted`, ABSENT
                  when complete; `stale` while the recorded chartVersion is not the installed one,
                  since core-provider keeps the last list during a re-read
  Emits defs only, with no jq `#` comment, so it also works in a folded (`>`) filter.
  Used by blueprints-page (restaction.blueprint-detail) and, as a guarded copy, marketplace-page.
*/}}
{{- define "portal.blueprintContentsDefs" -}}
def contentsPlural($n; $one): ($n | tostring) + " " + $one + (if $n == 1 then "" else "s" end);
def blueprintContents($cur):
  (if . == null then null else (.status.contents // null) end) as $ct
  | if . == null then
      { state: "notInstalled", extra: "not installed", kinds: [], stale: false, foot: "",
        title: "Known once installed.",
        body: "What a blueprint creates is read when it is installed on this cluster, at its default values. Install it to see the list here and on its blueprint page." }
    elif ($ct | type) != "object" then
      { state: "none", extra: "—", kinds: [], stale: false, foot: "",
        title: "Contents not available.",
        body: "This cluster's core-provider doesn't record what a blueprint creates yet." }
    elif ((($ct.unknown // "") | tostring) != "") then
      { state: "unknown", extra: "unknown", kinds: [], stale: false, foot: "",
        title: "Not known at default values.",
        body: ("core-provider: " + ($ct.unknown | tostring)),
        hint: "A composition shows what it created on its own page." }
    else
      ([ ($ct.kinds // [])[] | objects
         | { kind: (.kind // ""), group: (.apiVersion // ""), count: (.count // 0),
             countLabel: ("×" + ((.count // 0) | tostring)) } ]) as $kinds
      | (($ct.omitted // 0) | if type == "number" then . else 0 end) as $omitted
      | (($ct.chartVersion // "") | tostring) as $cv
      | ($cur != "" and $cv != "" and $cv != $cur) as $stale
      | (($kinds | length) + $omitted) as $nKinds
      | ([ $kinds[].count ] | add // 0) as $nObjects
      | if $nKinds == 0 then
          { state: "empty", extra: "0 kinds", kinds: [], stale: $stale, foot: "",
            title: "Creates nothing at default values.",
            body: ("Chart " + $cv + " renders no resources until its values ask for some.") }
        else
          { state: "known", kinds: $kinds, stale: $stale,
            extra: (if $stale then "from " + $cv else contentsPlural($nKinds; "kind") end),
            foot: ("At default values · "
                   + (if $stale then "read from chart " + $cv + "; the installed chart is " + $cur + " and is being read"
                      elif $omitted > 0 then "chart " + $cv + " · and " + contentsPlural($omitted; "more kind") + " not listed"
                      else "chart " + $cv + " · " + contentsPlural($nObjects; "resource") + " of " + contentsPlural($nKinds; "kind") end)) }
        end
    end;
{{- end -}}
