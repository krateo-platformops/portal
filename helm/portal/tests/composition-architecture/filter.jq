# Input: snowplow's resolve dict — the request extras at the top level, each stage's (step-filtered)
# output under its name, and each stage's errors, accumulated as a list, under its errorKey.
def truthy: . != null and . != false and . != "" and . != 0 and . != [] and . != {};  # Helm `if`
def pathOf($rw): $rw | ltrimstr(".") | split(".");                                    # dotted keys only
def errKind:
  if type == "object" then
    (if (.code // 0) == 403 or .reason == "Forbidden" then "forbidden"
     elif (.code // 0) == 404 or .reason == "NotFound" then "notfound" else "error" end)
  elif type == "string" then
    (if test("forbidden|not authorized"; "i") then "forbidden"
     elif test("not found"; "i") then "notfound" else "error" end)
  else "error" end;
def nameIn: [capture("\"(?<n>[^\"]+)\" (is forbidden|not found)")][0].n // null;
def errName:
  if type == "object" then (.details.name // ((.message // "") | nameIn))
  elif type == "string" then nameIn
  else null end;
def errs($k): [ ($k // []) | if type == "array" then .[] else . end ];
def exception:                                         # K8s-native tokens, exception-only
  (.status.conditions // []) as $c
  | ([ $c[] | select(.type == "Synced" and .status == "False") ][0]) as $s
  | ([ $c[] | select((.type == "Ready" or .type == "Available") and .status == "False") ][0]) as $r
  | if $s != null then {label: "NotSynced", reason: ($s.reason // ""), message: ($s.message // "")}
    elif $r != null then {label: "NotReady", reason: ($r.reason // ""), message: ($r.message // "")}
    else null end;

(.arch.graph // null) as $g
| (errs(.archErr) | map(errKind)) as $archErrs
| if $g == null then
    { architecture: false,
      access: (if any($archErrs[]; . == "forbidden") then "forbidden"
               elif any($archErrs[]; . == "error") then "error" else null end) }
  elif (.comp[0]? // null) == null then
    { architecture: true, chart: $g.chart, composition: ($g.composition.name? // ""), readable: false, states: $g.states,
      access: (if any(errs(.compErr)[]; errKind == "forbidden") then "forbidden" else "error" end),
      level: null, state: null, allReady: null, waitingOn: [], progress: null, since: null, next: [],
      nodes: [ ($g.nodes // [])[] | select((.lifecycle // "") == "")
               | {id, kind, apiVersion, class, level, present, forEach: (.forEach // false), readyWhen: (.readyWhen // null),
                  parents: [ .dependsOn[]? | select(.active) | .ref ], expected: (.names | length),
                  phase: (if .present then "unknown" else "absent" end)} ] }
  else
  (.comp[0]? // null) as $comp
  | ($comp.managed // []) as $managed
  | ([ .objs ] | flatten | map(select(type == "object" and (.metadata.name // "") != ""))) as $got
  | (errs(.objErr) | map(select(errKind == "forbidden") | errName) | map(select(. != null))) as $denied
  | (errs(.objErr) | map(select(errKind == "notfound") | errName) | map(select(. != null))) as $missing
  | [ ($g.nodes // [])[] | select((.lifecycle // "") == "") ] as $seq
  # 1. each instance, from what was rendered (status.managed) and what the caller could read
  | [ $seq[] | . as $n
      | [ .names[]? as $nm
          | ([ $got[] | select(.metadata.name == $nm and (.apiVersion == "" or .apiVersion == $n.apiVersion)) ][0]) as $o
          | (any($managed[]; .name == $nm and .apiVersion == $n.apiVersion)) as $rendered
          | { name: $nm,
              read: (if $o != null then "ok"
                     elif ($rendered | not) then "withheld"
                     elif ($denied | index($nm)) != null then "unreadable"
                     elif ($missing | index($nm)) != null then "missing"
                     else "unavailable" end),
              satisfied: (if $o == null then null
                          elif ($n.readyWhen // "") == "" then true
                          else ((try ($o | getpath(pathOf($n.readyWhen))) catch null) | truthy) end),
              value: (if $o != null and ($n.readyWhen // "") != "" then (try ($o | getpath(pathOf($n.readyWhen))) catch null) else null end),
              since: (if $o != null then ($o.metadata.creationTimestamp // null) else null end),
              exception: (if $o != null then ($o | exception) else null end) } ] as $inst
      | $n + { instances: $inst } ] as $nodes0
  # 2. a rendered dependent proves its ready: true dependencies held at the last render (gate parity)
  | ([ $nodes0[] | select(.present)
       | select(any(.instances[]; .read != "withheld"))
       | .dependsOn[]? | select(.active and .ready) | .ref ] | unique) as $proven
  | [ $nodes0[] | . as $n
      | (($proven | index($n.id)) != null) as $isProven
      | (($n.readyWhen // "") == "") as $existence
      | [ .instances[] | . + { ok: (if .satisfied == true then true
                                   elif .read == "ok" then false
                                   elif .read == "withheld" then false
                                   elif .read == "missing" then false
                                   elif $existence or $isProven then true
                                   else null end) } ] as $inst
      | { id, kind, apiVersion, class, level, present, forEach: (.forEach // false), readyWhen: (.readyWhen // null),
          parents: [ .dependsOn[]? | select(.active) | .ref ],
          waits: [ .dependsOn[]? | select(.active and .ready) | {ref, all: (.all // false)} ],
          expected: ($inst | length),
          rendered: ([ $inst[] | select(.read != "withheld") ] | length),
          satisfied: ([ $inst[] | select(.ok == true) ] | length),
          unknown: ([ $inst[] | select(.ok == null) ] | length),
          unreadable: ([ $inst[] | select(.read == "unreadable") ] | length),
          inferred: any($inst[]; .ok == true and .read != "ok"),
          since: ([ $inst[] | .since | select(. != null) ] | min),
          exception: ([ $inst[] | .exception | select(. != null) ][0]),
          instances: $inst } ] as $nodes
  # 3. the level: the first level (of those this composition uses) whose nodes are not all satisfied
  | ([ $nodes[] | select(.present) | .level ] | unique) as $levels
  | ([ $levels[] as $l | select(any($nodes[]; .present and .level == $l and .satisfied < .expected)) | $l ][0]) as $cur
  | ($cur == null) as $all
  | (if $all then ($levels | max) else $cur end) as $level
  | [ $nodes[] | . + { phase: (if (.present | not) then "absent"
                               elif .satisfied == .expected then "done"
                               elif .unknown > 0 and .unreadable > 0 then "unreadable"
                               elif .unknown > 0 then "unavailable"
                               elif .rendered == 0 and .expected > 0 then "withheld"
                               else "waiting" end) } ] as $nodes2
  | { architecture: true,
      chart: $g.chart,
      composition: ($g.composition.name? // ""),
      readable: ($comp != null),
      states: $g.states,
      level: $level,
      state: (if $level == null then null else ($g.states[$level] // null) end),
      allReady: $all,
      waitingOn: (if $all then [] else [ $nodes2[] | select(.present and .level == $cur and .satisfied < .expected)
                   | {id, kind, pending: (.expected - .satisfied), phase} ] end),
      progress: (if $all then null else ([ $nodes2[] | select(.present and .level == $cur) ]
                   | {satisfied: (map(.satisfied) | add), expected: (map(.expected) | add)}) end),
      since: (if $all then null else ([ $nodes2[] | select(.present and .level == $cur) | .since | select(. != null) ] | min) end),
      next: (if $all then [] else [ $nodes2[] | select(.present and .level == ($levels | map(select(. > $cur)) | min)) | .id ] end),
      nodes: $nodes2 }
  end
