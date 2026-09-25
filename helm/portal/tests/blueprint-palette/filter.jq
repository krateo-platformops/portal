def firstErr($e): (($e // []) | map(if type == "object" then . else {message: tostring} end) | .[0]) // {}
  | {code: (.code // 0), reason: (.reason // ""), message: (.message // "")};
def refused($p): ($p // {}) | ((.evaluated == true) and (.allowed != true)); def forbidden($what): {code: 403, reason: "Forbidden",
  message: ("you may not list " + $what + " across the cluster (SelfSubjectAccessReview: allowed=false)")};
{ custom: (if ((.crdsError // []) | length) > 0 then {error: firstErr(.crdsError)}
           elif refused(.mayListCrds) then {error: forbidden("customresourcedefinitions.apiextensions.k8s.io")}
           else {kinds: (.crds // [])} end),
  compositions: (if ((.compdefsError // []) | length) > 0 then {error: firstErr(.compdefsError)}
                 elif refused(.mayListCompdefs) then {error: forbidden("compositiondefinitions.core.krateo.io")}
                 else {items: [ (.compdefs // [])[] as $c
                                | $c + {running: ([ (.running // [])[] | select(.cd == $c.name and .cdNs == $c.namespace) ] | length)} ]} end),
  runningPartial: (((.runningError // []) | length) > 0) }
