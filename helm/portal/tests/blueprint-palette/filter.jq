def firstErr($e): (($e // []) | map(if type == "object" then . else {message: tostring} end) | .[0]) // {}
  | {code: (.code // 0), reason: (.reason // ""), message: (.message // "")};
def refused($p): ($p // {}) | ((.evaluated == true) and (.allowed != true)); def unchecked($p; $list): ((($p // {}).evaluated != true) and ((($list // []) | length) == 0)); def forbidden($what): {code: 403, reason: "Forbidden",
  message: ("you may not list " + $what + " across the cluster (SelfSubjectAccessReview: allowed=false)")};
def uncheckable($what; $e): firstErr($e) as $f
  | $f + {message: ("could not check that you may list " + $what + " across the cluster (SelfSubjectAccessReview: "
      + (if $f.code > 0 then ($f.code | tostring) + " " + $f.reason elif $f.message != "" then $f.message else "no answer" end)
      + "), so an empty list is not shown as none")};
{ custom: (if ((.crdsError // []) | length) > 0 then {error: firstErr(.crdsError)}
           elif refused(.mayListCrds) then {error: forbidden("customresourcedefinitions.apiextensions.k8s.io")}
           elif unchecked(.mayListCrds; .crds) then {error: uncheckable("customresourcedefinitions.apiextensions.k8s.io"; .mayListCrdsError)}
           else {kinds: (.crds // [])} end),
  compositions: (if ((.compdefsError // []) | length) > 0 then {error: firstErr(.compdefsError)}
                 elif refused(.mayListCompdefs) then {error: forbidden("compositiondefinitions.core.krateo.io")}
                 elif unchecked(.mayListCompdefs; .compdefs) then {error: uncheckable("compositiondefinitions.core.krateo.io"; .mayListCompdefsError)}
                 else {items: [ (.compdefs // [])[] as $c
                                | $c + {running: ([ (.running // [])[] | select(.cd == $c.name and .cdNs == $c.namespace) ] | length)} ]} end),
  runningPartial: (((.runningError // []) | length) > 0) }
