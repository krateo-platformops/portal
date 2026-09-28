(.chart // null) as $chart
| (.rawTemplates // null) as $raw
| (($chart != null) and (($chart.url // "") != "")) as $hasChart
| (($raw | type) == "object" and (($raw | length) > 0)) as $hasRaw
| (if ($hasChart and $hasRaw) then "provide EXACTLY ONE chart source — chart{url,...} or rawTemplates, not both"
   elif ((($hasChart | not)) and (($hasRaw | not))) then "no chart source — provide chart{url,version?,repo?} or rawTemplates{path:content}"
   else "" end) as $argErr
| (.render // {}) as $r
| ((($r.error // "") | tostring) as $svc
   | if $svc != "" then $svc
     elif (.renderError != null) then (.renderError | tostring)
     else "" end) as $svcErr
| (if $argErr != "" then $argErr else $svcErr end) as $err
| ([ ($r.objects // [])[]
     | { apiVersion: (.apiVersion // ""),
         kind: (.kind // "Object"),
         name: (.name // ""),
         namespace: (.namespace // ""),
         yaml: (.yaml // "") } ]) as $objects
| ({ objects: (if $err != "" then [] else $objects end) }
   + (if $err != "" then { error: $err } else {} end)
   + (if ($err == "") and ($r.valuesSchema != null) then { valuesSchema: $r.valuesSchema } else {} end)
   + (if ($err == "") and (($r.lookups // null) | type) == "array" then { lookups: $r.lookups } else {} end))
