. as $r
| ($r.comp[0].managed // []) as $m
| [ ($r.arch.graph.nodes // [])[]
    | select(.present == true and ((.lifecycle // "") == "")) as $nd
    | $nd.names[] as $nm
    | $m[] | select(.name == $nm and .apiVersion == $nd.apiVersion and .path != "") | {path} ]
| unique
