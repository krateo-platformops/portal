def fetchableCore: ["configmaps", "serviceaccounts", "services", "endpoints", "persistentvolumeclaims", "persistentvolumes", "pods", "namespaces", "nodes", "events", "limitranges", "resourcequotas", "replicationcontrollers", "podtemplates"];
def fetchablePath: (tostring | (split("?") | .[0] // "") | split("/")) as $p
  | if ($p[1] // "") == "apis" then true
    elif ($p[1] // "") == "api" then
      ((if ($p[3] // "") == "namespaces" then ($p[5] // "") else ($p[3] // "") end)) as $res
      | ($res == "" or (fetchableCore | index($res)) != null)
    else false end;
. as $r
| ($r.comp[0].managed // []) as $m
| [ ($r.arch.graph.nodes // [])[]
    | select(.present == true and ((.lifecycle // "") == "")) as $nd
    | $nd.names[] as $nm
    | $m[] | select(.name == $nm and .apiVersion == $nd.apiVersion and .path != "")
    | select(.path | fetchablePath) | {path} ]
| unique
