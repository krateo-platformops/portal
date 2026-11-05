def fetchableCore: ["configmaps", "serviceaccounts", "services", "endpoints", "persistentvolumeclaims", "persistentvolumes", "pods", "namespaces", "nodes", "events", "limitranges", "resourcequotas", "replicationcontrollers", "podtemplates"];
def fetchablePath: (tostring | (split("?") | .[0] // "") | split("/")) as $p
  | if ($p[1] // "") == "apis" then true
    elif ($p[1] // "") == "api" then
      ((if ($p[3] // "") == "namespaces" then ($p[5] // "") else ($p[3] // "") end)) as $res
      | ($res == "" or (fetchableCore | index($res)) != null)
    else false end;
def fetchableTarget: (tostring | select(fetchablePath and (test("[?#%\\\\]") | not))) as $path | ($path | split("/")) as $p
  | (if ($p[1] // "") == "api" then { g: "", v: ($p[2] // ""), r: $p[3:] } else { g: ($p[2] // ""), v: ($p[3] // ""), r: $p[4:] } end) as $t
  | (if ($t.r[0] // "") == "namespaces" and ($t.r | length) > 2 then { ns: $t.r[1], r: $t.r[2:] } else { ns: "", r: $t.r } end) as $n
  | { g: $t.g, v: $t.v, ns: $n.ns, pl: ($n.r[0] // ""), nm: ($n.r[1] // "") } as $o
  | select(($n.r | length) <= 2 and $o.v != "" and ([$o.g, $o.v, $o.ns, $o.pl, $o.nm] | all(. != "." and . != "..")))
  | select(((if $o.g == "" then "/api/" + $o.v else "/apis/" + $o.g + "/" + $o.v end) + (if $o.ns == "" then "" else "/namespaces/" + $o.ns end) + (if $o.pl == "" then "" else "/" + $o.pl end) + (if $o.nm == "" then "" else "/" + $o.nm end)) == $path)
  | $o;
. as $r
| ($r.comp[0].managed // []) as $m
| [ ($r.arch.graph.nodes // [])[]
    | select(.present == true and ((.lifecycle // "") == "")) as $nd
    | $nd.names[] as $nm
    | $m[] | select(.name == $nm and .apiVersion == $nd.apiVersion and .path != "")
    | (.path | fetchableTarget) ]
| unique
