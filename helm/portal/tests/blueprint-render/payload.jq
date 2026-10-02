(.chart // null) as $chart
| (.rawTemplates // null) as $raw
| ((.values // {}) | if type == "object" then . else {} end) as $vals
| (if ($raw | type) == "object" and (($raw | length) > 0) and ($chart == null)
     then { rawTemplates: $raw }
   elif ($chart | type) == "object" and (($chart.url // "") != "")
     then { chart: ({ url: $chart.url }
                     + (if ($chart.repo // "") != "" then { repo: $chart.repo } else {} end)
                     + (if ($chart.version // "") != "" then { version: $chart.version } else {} end)) }
     else {} end) as $src
| ($src
   + { values: $vals }
   + (if (.releaseName // "") != "" then { releaseName: .releaseName } else {} end)
   + (if (.namespace // "") != "" then { namespace: .namespace } else {} end)
   + (if ((.lookupStubs // null) | type) == "array" and ((.lookupStubs | length) > 0)
        then { lookupStubs: .lookupStubs } else {} end))
