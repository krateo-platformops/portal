((.extras.compositionName // .extras.name) // "") as $n
| (.arch.metadata.labels // {}) as $l
| (try (.arch.data.graph | fromjson) catch null) as $g
| if $n != "" and ($l["krateo.io/composition-name"] // "") == $n and ($g | type) == "object" and $g.v == 1
  then {graph: $g, compositionId: ($l["krateo.io/composition-id"] // "")}
  else null end
