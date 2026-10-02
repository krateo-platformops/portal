{{/*
  portal.reviewDefs — the jq the Platform Review pages share (restaction.platform-reviews,
  restaction.review-proposal, restaction.review-run). ONE COPY, because the list and the detail
  page must call the same proposal "Kind not served" or "Target unverified" on the same evidence.

  Include it at the top of a LITERAL (|) filter:
      filter: |
        {{- include "portal.reviewDefs" . | nindent 4 }}
        ...rest of the filter

  Kind check. Only a yaml change names a kind; markdown and diff get the neutral "na". A group-version
  absent from /apis, or a kind absent from its group-version's discovery list, is "notServed". Any
  question discovery did not answer (the /apis read failed, or the per-group read errored) is
  "unknown" and renders NOTHING: no answer is not evidence of absence.

  Target check. status.conditions[TargetResolved], written from nightly-review 0.1.21. NotFoundOrPrivate
  is "unverified" and never more: an anonymous GitHub check cannot tell a missing repository from a
  private one. Unknown (CheckFailed / CheckDisabled) renders nothing.

  Decision. The portal records a decision in spec.decision (a main-resource merge-PATCH, as the
  caller; decidedBy/decidedAt are stamped by nightly-review's admission policy, never sent). It is
  read FIRST so the page changes at once; status is the nightly mirror. The one exception is Merged,
  which only the service writes and which a PrOpen decision must not hide (nightly-review 0.1.23).
*/}}
{{- define "portal.reviewDefs" -}}
def rv_ts: if (. // "") == "" then null else (.[0:19] + "Z") end;
def rv_epoch: rv_ts | if . == null then null else (fromdateiso8601? // null) end;
def rv_when: if (. // "") == "" then "—" else (.[0:10] + " " + .[11:16] + " UTC") end;
def rv_dur($a; $b):
  ($a | rv_epoch) as $s | ($b | rv_epoch) as $e
  | if $s == null or $e == null then "—"
    else ($e - $s) as $d
    | if $d < 1 then "under 1 s"
      elif $d < 60 then (($d | floor | tostring) + " s")
      else ((($d / 60) | floor | tostring) + " m " + ((($d | floor) % 60) | tostring) + " s") end
    end;
def rv_num: tostring as $s
  | if ($s | test("^[0-9]{4,}$")) then ([ range(($s | length); 0; -3) | $s[([. - 3, 0] | max):.] ] | reverse | join(",")) else $s end;
def rv_phase:
  (.status.phase // "") as $s
  | if $s == "Merged" then $s
    else (.spec.decision.phase // (if $s == "" then "Proposed" else $s end)) end;
def rv_decision:
  { phase: rv_phase,
    by: (.spec.decision.decidedBy // .status.decidedBy // ""),
    at: (.spec.decision.decidedAt // .status.decidedAt // ""),
    reason: (.spec.decision.reason // .status.reason // ""),
    claim: (.spec.decision.claim // "") };
def rv_has($x): any(.[]?; . == $x);
def rv_gvk:
  if ((.spec.change.format // "") != "yaml") then null
  else (.spec.change.content // "") as $c
    | { gv: ([ $c | match("(?m)^apiVersion:[ \\t]*[\"']?([^\\s\"'#]+)") ][0].captures[0].string // ""),
        kind: ([ $c | match("(?m)^kind:[ \\t]*[\"']?([^\\s\"'#]+)") ][0].captures[0].string // "") }
    | if .gv == "" or .kind == "" then null else . end
  end;
def rv_kindcheck($served; $kinds):
  (.spec.change.format // "") as $f
  | if $f == "markdown" or $f == "diff" then { state: "na" }
    else rv_gvk as $g
    | if $g == null or $served == null then { state: "unknown" }
      elif ($served | rv_has($g.gv) | not) then ($g + { state: "notServed" })
      elif ($kinds[$g.gv] // null) == null then ($g + { state: "unknown" })
      elif ($kinds[$g.gv] | rv_has($g.kind) | not) then ($g + { state: "notServed" })
      else ($g + { state: "served" }) end
    end;
def rv_target:
  ([ (.status.conditions // [])[] | select(.type == "TargetResolved") ][0] // null) as $c
  | if $c == null then { state: "none" }
    elif $c.status == "True" then { state: "found", message: ($c.message // "") }
    elif $c.reason == "NotFoundOrPrivate" then { state: "unverified", message: ($c.message // "") }
    elif $c.reason == "InvalidRepo" then { state: "invalid", message: ($c.message // "") }
    else { state: "unanswered", message: ($c.message // ""), reason: ($c.reason // "") } end;
def rv_pills($served; $kinds):
  rv_kindcheck($served; $kinds) as $k | rv_target as $t
  | [ (if $k.state == "notServed" then { type: "Kind not served", status: "False", color: "orange" }
       elif $k.state == "na" then { type: "Kind check n/a", status: "Unknown", color: "gray" }
       else empty end),
      (if $t.state == "unverified" then { type: "Target unverified", status: "Unknown", color: "gray" }
       elif $t.state == "invalid" then { type: "Target invalid", status: "False", color: "orange" }
       else empty end) ];
def rv_phase_color:
  if . == "Merged" then "green" elif . == "PrOpen" then "blue" elif . == "Failed" then "red" else "gray" end;
def rv_run_color:
  if . == "PartiallyCompleted" then "orange"
  elif . == "Failed" then "red" elif . == "Running" then "blue" else "gray" end;
def rv_sources:
  [ (.spec.evidence // [])[] | (.source // "unknown") ] | group_by(.)
  | map(.[0] + (if length > 1 then " ×" + (length | tostring) else "" end)) | join(" · ");
def rv_root($up; $n):
  if $n > 32 then . elif ($up[.] // null) != null then ($up[.] | rv_root($up; $n + 1)) else . end;
def rv_not_read:
  [ (.status.evidence // {}) | to_entries[]
    | select(.value.ok == false or .value.empty == true)
    | .key + ": " + ((.value.error // .value.note // (if .value.empty == true then "returned nothing" else "not read" end)) | rtrimstr("; ")) ]
  | join(" · ");
def rv_served_gvs: if (.apisError // null) != null then null else (.apis // null) end;
def rv_kinds_by_gv:
  (.kinds // []) | if type == "array" then . else [.] end
  | map(select(type == "object" and (.gv // "") != "") | { key: .gv, value: (.kinds // []) }) | from_entries;
def rv_claim: "review-" + ((.metadata.name // "") | ltrimstr("p-"));
{{- end }}

{{/*
  portal.reviewDiscoverySteps — the two api steps the kind check reads. /apis (group discovery is
  readable by every authenticated user and carries no tenant data), then one group-version list per
  SERVED group-version a yaml change names. A group-version /apis does not list is not fetched: its
  absence is already the answer, and a 404 per step would read as an error. `dependsOn` orders the
  steps after `$source`, the step holding the proposals as {items: [...]}, or a single proposal.
*/}}
{{- define "portal.reviewDiscoverySteps" -}}
- name: apis
  path: /apis
  verb: GET
  dependsOn:
    name: {{ .source }}
  headers:
    - 'Accept: application/json'
  continueOnError: true
  errorKey: apisError
  filter: '[ (.apis.groups // [])[] | (.versions // [])[] | .groupVersion ] + ["v1"]'
- name: kinds
  dependsOn:
    name: apis
    iterator: >-
      {{- include "portal.fetchableDefs" . | nindent 6 }}
      {{ printf "(.apis // []) as $served | [ (.%s | if type == \"object\" and has(\"items\") then .items else [.] end)[]?" .source }}
        | select((.spec.change.format // "") == "yaml") | (.spec.change.content // "")
        | ([ match("(?m)^apiVersion:[ \\t]*[\"']?([^\\s\"'#]+)") ][0].captures[0].string // "") ]
      | unique | map(select(. != "" and (. as $g | any($served[]; . == $g))))
      | map({ gv: ., discovery: (if (. | contains("/")) then "/apis/" + . else "/api/" + . end) })
      | map(select(.discovery | fetchablePath))
  path: ${ .discovery }
  verb: GET
  headers:
    - 'Accept: application/json'
  continueOnError: true
  errorKey: kindsError
  filter: '[ { gv: (.kinds.groupVersion // ""), kinds: [ (.kinds.resources // [])[] | select((.name // "") | contains("/") | not) | .kind ] } ]'
{{- end }}
