{{/* Helpers for the Token usage tab's spend view (restaction.agents-cost).

     Each jq define is ONE line where it is spliced into a single-line expression, and every value
     taken from .Values is checked here, so nothing unvalidated reaches a path or SQL. */}}

{{/* A string literal of a RESTAction path, encoded once. snowplow passes the program's string
     literals through as written (internal/pathtemplate), so the SQL text must arrive encoded; a
     space is %20, never `+`. */}}
{{- define "agentsCost.enc" -}}
{{- . | urlquery | replace "+" "%20" -}}
{{- end }}

{{/* The namespace of the agent gateway and of its price list. A caller sees spend only when they
     may `get` Gateways here. */}}
{{- define "agentsCost.namespace" -}}
{{- $n := .Values.costs.namespace | default "" -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$" $n) -}}
{{- fail (printf "costs.namespace %q: must be a namespace name" $n) -}}
{{- end -}}
{{- $n -}}
{{- end }}

{{- define "agentsCost.catalogConfigMap" -}}
{{- $n := .Values.costs.catalogConfigMap | default "" -}}
{{- if not (regexMatch "^[a-z0-9]([-.a-z0-9]{0,251}[a-z0-9])?$" $n) -}}
{{- fail (printf "costs.catalogConfigMap %q: must be a ConfigMap name" $n) -}}
{{- end -}}
{{- $n -}}
{{- end }}

{{- define "agentsCost.catalogKey" -}}
{{- $n := .Values.costs.catalogKey | default "" -}}
{{- if not (regexMatch "^[-._a-zA-Z0-9]{1,253}$" $n) -}}
{{- fail (printf "costs.catalogKey %q: must be a ConfigMap data key" $n) -}}
{{- end -}}
{{- $n -}}
{{- end }}

{{/* Goes into SQL as a quoted string, so no quote or backslash can be in it. */}}
{{- define "agentsCost.comparisonCaller" -}}
{{- $n := .Values.costs.comparisonCaller | default "" -}}
{{- if not (regexMatch "^[A-Za-z0-9._@:+=/-]{1,256}$" $n) -}}
{{- fail (printf "costs.comparisonCaller %q: must be a JWT subject of [A-Za-z0-9._@:+=/-]" $n) -}}
{{- end -}}
{{- $n -}}
{{- end }}

{{/* The window, as absolute epoch seconds, so a preset and a picked range read the same SQL.
     ?range is a preset (1h 6h 12h 24h 3d 7d, default 24h) or `custom`, which the RangePicker writes
     with ?from and ?to (epoch seconds: the start of the first day and the end of the last, in the
     viewer's time zone). The window is clamped to the 7 days the access log and the spans are kept,
     and never ends after now. The chart's bucket follows the span (about 12 to 30 bars). The
     catalog's context tiers become a SQL expression over the call's input tokens `i`: the gateway
     picks a model's tier per call (the highest contextOver below the call's input), so the query
     groups by the highest threshold the call exceeds and the filter applies the tier the gateway
     would; the thresholds are checked to be whole numbers before they reach SQL. Evaluated against
     the resolve dict; binds $range, $custom, $from0, $to0, $from, $to, $span, $bk and $tier. */}}
{{- define "agentsCost.window" -}}
((.range // "24h") | tostring) as $r0 | (now | floor) as $now | ((.from // "") | tostring) as $f0 | ((.to // "") | tostring) as $t0 | ($r0 == "custom" and ($f0 | test("^[0-9]{9,11}$")) and ($t0 | test("^[0-9]{9,11}$"))) as $custom | ({"1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400, "3d": 259200, "7d": 604800}) as $presets | (if $custom then "custom" elif $presets[$r0] != null then $r0 else "24h" end) as $range | (if $custom then ($f0 | tonumber) else $now - $presets[$range] end) as $from0 | (if $custom then ($t0 | tonumber) else $now end) as $to0 | ([$from0, $now - 604800] | max) as $from | ([$to0, $now] | min) as $to | ($to - $from) as $span | (if $span <= 3600 then 300 elif $span <= 21600 then 900 elif $span <= 43200 then 1800 elif $span <= 90000 then 3600 elif $span <= 259200 then 10800 else 21600 end) as $bk | ([ (.catalog // {}) | objects | .[] | objects | (.models // {}) | objects | .[] | objects | (.tiers // []) | arrays | .[] | objects | .contextOver | numbers | select(. > 0 and . < 1000000000 and . == floor) ] | unique | reverse) as $ts | (if ($ts | length) == 0 then "0" else ("multiIf(" + ([ $ts[] | "i > " + tostring + ", " + tostring ] | join(", ")) + ", 0)") end) as $tier
{{- end }}

{{/* The agent gateway's access log: one row per model call it proxied. A retried OTLP export can
     store a row twice, so rows are made distinct on the call's own time and client address first.
     `cost_usd` is what the gateway charged the call when it made it, priced with the list it had
     then, so a later price change does not reprice the past. A call the gateway could not price
     carries none (or 0, when its price list failed to load): `hc` keeps those apart, and the filter
     prices them with today's list.
     `cmp` marks the incident comparison: a call by the comparison caller's own identity that did
     not come from an agent runtime (every agent reaches the model through an OpenAI SDK client;
     the alert provider's comparison is a plain HTTP POST). Split around $bk, $tier and the window. */}}
{{- define "agentsCost.sql.gw1" }}SELECT toUnixTimestamp(toStartOfInterval(ts, INTERVAL {{ end }}
{{- define "agentsCost.sql.gw2" }} SECOND)) AS t, usr, team, principal, model, provider, cmp, {{ end }}
{{- define "agentsCost.sql.gw3" }} AS tier, cost > 0 AS hc, count() AS calls, sum(i) AS inp, sum(c) AS cached, sum(o) AS outp, sum(r) AS reas, sum(cost) AS rec FROM (SELECT DISTINCT Timestamp AS ts, LogAttributes['src.addr'] AS src, LogAttributes['jwt.sub'] AS usr, LogAttributes['team'] AS team, LogAttributes['principal'] AS principal, if(LogAttributes['model'] != '', LogAttributes['model'], LogAttributes['gen_ai.request.model']) AS model, LogAttributes['gen_ai.provider.name'] AS provider, (LogAttributes['jwt.sub'] = '{{ include "agentsCost.comparisonCaller" . }}' AND NOT match(LogAttributes['user_agent'], '^(Async)?OpenAI/')) AS cmp, toUInt64OrZero(LogAttributes['gen_ai.usage.input_tokens']) AS i, toUInt64OrZero(LogAttributes['gen_ai.usage.cache_read.input_tokens']) AS c, toUInt64OrZero(LogAttributes['gen_ai.usage.output_tokens']) AS o, toUInt64OrZero(LogAttributes['gen_ai.usage.reasoning_tokens']) AS r, toFloat64OrZero(LogAttributes['cost_usd']) AS cost FROM otel_logs WHERE ScopeName = 'request' AND has(mapKeys(LogAttributes), 'gen_ai.usage.input_tokens') AND Timestamp >= toDateTime({{ end }}
{{- define "agentsCost.sql.until" }}) AND Timestamp < toDateTime({{ end }}
{{- define "agentsCost.sql.gw4" }})) GROUP BY t, usr, team, principal, model, provider, cmp, tier, hc ORDER BY t LIMIT 20000 FORMAT JSON{{ end }}

{{/* Spend per agent. The gateway cannot tell agents apart (they share a ModelConfig and send the
     caller's token), and the agent runtime's own span per model call cannot be priced on its own:
     it reports no cached tokens (0 on every kagent span), while the gateway sees most agent input
     served from cache. So each span (`kagent.app_name` = <namespace>__NS__<name>, `-` written `_`)
     is matched to the gateway's row for the SAME call: same model, same input and output token
     counts, the nearest within a minute. The pairing is one to one: each span keeps its nearest
     row, then each row keeps its nearest span, so two identical calls can never both claim one row
     (the span left over is counted as missing, which is what happened to its row). The matched
     rows carry the gateway's own tokens, provider and recorded cost, and are priced like every
     other gateway row.

     Both sides are timed at the call's END. The gateway logs a call when it completes; a span is
     stamped when it starts, seconds to minutes earlier, so its end is Timestamp + Duration. A
     matched call counts in the window its gateway row is in, the rule every other figure follows,
     so the agents never add up to more than the Spend figure. The match itself looks a minute past
     both edges, so a call that straddles one is still found. `spans` counts the matched spans that
     end inside the window and the second part counts every such span, so a call with no gateway
     row is counted and said, not guessed. Spans are read from 15 minutes before the window, longer
     than the gateway lets a model call run (10 minutes). Only the agents the caller can read are
     asked for. Split around the window, the agent list and $tier. */}}
{{- define "agentsCost.sql.agA" }}WITH toDateTime({{ end }}
{{- define "agentsCost.sql.agB" }}) AS wf, toDateTime({{ end }}
{{- define "agentsCost.sql.agC" }}) AS wt, [{{ end }}
{{- define "agentsCost.sql.agD" }}] AS apps SELECT 'm' AS k, app, model, provider, {{ end }}
{{- define "agentsCost.sql.agE" }} AS tier, cost > 0 AS hc, countIf(gin) AS calls, sumIf(i, gin) AS inp, sumIf(c, gin) AS cached, sumIf(o, gin) AS outp, sumIf(r, gin) AS reas, sumIf(cost, gin) AS rec, countIf(sin) AS spans FROM (SELECT * FROM (SELECT s.app AS app, g.model AS model, g.provider AS provider, g.i AS i, g.c AS c, g.o AS o, g.r AS r, g.cost AS cost, (g.ts >= wf AND g.ts < wt) AS gin, (s.te >= wf AND s.te < wt) AS sin, g.ts AS gts, g.src AS gsrc, abs(toUnixTimestamp64Milli(g.ts) - toUnixTimestamp64Milli(s.te)) AS gap FROM (SELECT DISTINCT SpanId AS sid, addNanoseconds(Timestamp, Duration) AS te, SpanAttributes['kagent.app_name'] AS app, SpanAttributes['gen_ai.request.model'] AS model, toUInt64OrZero(SpanAttributes['gen_ai.usage.input_tokens']) AS i, toUInt64OrZero(SpanAttributes['gen_ai.usage.output_tokens']) AS o FROM otel_traces WHERE has(mapKeys(SpanAttributes), 'kagent.app_name') AND has(mapKeys(SpanAttributes), 'gen_ai.usage.input_tokens') AND has(apps, SpanAttributes['kagent.app_name']) AND Timestamp >= wf - INTERVAL 15 MINUTE AND Timestamp < wt + INTERVAL 1 MINUTE AND te >= wf - INTERVAL 1 MINUTE AND te < wt + INTERVAL 1 MINUTE) AS s INNER JOIN (SELECT DISTINCT Timestamp AS ts, LogAttributes['src.addr'] AS src, if(LogAttributes['model'] != '', LogAttributes['model'], LogAttributes['gen_ai.request.model']) AS model, LogAttributes['gen_ai.provider.name'] AS provider, toUInt64OrZero(LogAttributes['gen_ai.usage.input_tokens']) AS i, toUInt64OrZero(LogAttributes['gen_ai.usage.cache_read.input_tokens']) AS c, toUInt64OrZero(LogAttributes['gen_ai.usage.output_tokens']) AS o, toUInt64OrZero(LogAttributes['gen_ai.usage.reasoning_tokens']) AS r, toFloat64OrZero(LogAttributes['cost_usd']) AS cost FROM otel_logs WHERE ScopeName = 'request' AND has(mapKeys(LogAttributes), 'gen_ai.usage.input_tokens') AND Timestamp >= wf - INTERVAL 1 MINUTE AND Timestamp < wt + INTERVAL 1 MINUTE) AS g ON s.model = g.model AND s.i = g.i AND s.o = g.o WHERE gap < 60000 ORDER BY gap LIMIT 1 BY s.sid) ORDER BY gap LIMIT 1 BY gts, gsrc) GROUP BY app, model, provider, tier, hc UNION ALL SELECT 's' AS k, app, '' AS model, '' AS provider, 0 AS tier, false AS hc, uniqExact(sid) AS calls, 0 AS inp, 0 AS cached, 0 AS outp, 0 AS reas, 0 AS rec, 0 AS spans FROM (SELECT SpanId AS sid, SpanAttributes['kagent.app_name'] AS app, addNanoseconds(Timestamp, Duration) AS te FROM otel_traces WHERE has(mapKeys(SpanAttributes), 'kagent.app_name') AND has(mapKeys(SpanAttributes), 'gen_ai.usage.input_tokens') AND has(apps, SpanAttributes['kagent.app_name']) AND Timestamp >= wf - INTERVAL 15 MINUTE AND Timestamp < wt AND te >= wf AND te < wt) GROUP BY app FORMAT JSON{{ end }}

{{/* Number handling, pricing and display, for the filter.

     PRICING, for a call the gateway did not price when it made it, IS THE GATEWAY'S OWN
     (agentgateway crates/agentgateway/src/llm/catalog): a model is
     looked up under the provider that served it, then under any provider of the catalog; the
     tier is the one with the highest contextOver the call's input exceeds; cached input is split
     out of input and billed at cacheRead when the model has that rate; reasoning is part of
     output and billed at the reasoning rate, else the output rate. Rates are USD per 1M tokens,
     written as strings. A model with no entry, or an entry with no rate, is unpriced: counted,
     never priced at 0. */}}
{{- define "agentsCost.defs" -}}
def num: if type == "number" then . elif type == "string" then (tonumber? // 0) else 0 end;
def rate: if type == "number" then . elif type == "string" then (tonumber? // null) else null end;
def errText($e): if $e == null then "" else ([ ($e | if type == "array" then .[] else . end) | if type == "object" then (.message // .reason // tojson) elif . == null then "" else tostring end ] | map(select(. != "")) | unique | join("; ")) end;
def commas: tostring | if length <= 3 then . else ((.[0:(length % 3)]) as $h | ([$h] + [range(length % 3; length; 3) as $i | .[$i:$i+3]]) | map(select(. != "")) | join(",")) end;
def fixed($d): pow(10; $d) as $m | ((. * $m) | round) as $n | (($n / $m) | floor) as $w | (($n - $w * $m) | round | tostring) as $f | ($w | commas) + (if $d == 0 then "" else "." + (if ($d - ($f | length)) > 0 then ("0000000000"[0:($d - ($f | length))]) else "" end) + $f end);
def usd: if . == null then "—" elif . <= 0 then "$0.00" elif . < 0.001 then "< $0.001" elif . < 0.1 then "$" + fixed(3) else "$" + fixed(2) end;
def compact: if . >= 1000000000 then (. / 1000000000 | fixed(1)) + "B" elif . >= 1000000 then (. / 1000000 | fixed(1)) + "M" elif . >= 1000 then (. / 1000 | fixed(1)) + "k" else (round | tostring) end;
def plain($d): fixed($d) | gsub(","; "");
def usdCell: if . == null then "—" elif . >= 0.1 then plain(2) elif . > 0 then plain(4) else "0.00" end;
def kTokens: . / 1000 | plain(1);
def shareCell($of): if $of <= 0 or . == null then "—" else (. * 100 / $of) | plain(1) end;
def entryFor($cat; $prov; $model): [ ([$prov] + ($cat | keys))[] | select(. != "") as $p | (($cat[$p] // {}).models // {})[$model] | objects ] | first;
def ratesFor($e; $tier): ($e.rates // {}) as $base | ([ ($e.tiers // [])[] | objects | select(((.contextOver | num) > 0) and ((.contextOver | num) <= $tier)) ] | sort_by(.contextOver | num) | last) as $t | if $t == null then $base else $base + ($t.rates // {}) end;
def price($r; $u): ($r.input | rate) as $ri | ($r.cacheRead | rate) as $rc | ($r.output | rate) as $ro | ($r.reasoning | rate) as $rr | (if $rc != null then [([$u.i - $u.c, 0] | max), $u.c] else [$u.i, 0] end) as $in | ([$u.o - $u.r, 0] | max) as $out | ($in[0] * ($ri // 0) + $in[1] * ($rc // 0) + $out * ($ro // 0) + $u.r * ($rr // $ro // 0)) / 1000000;
def costOf($cat; $prov; $x): entryFor($cat; $prov; $x.model) as $e | if $e == null then null else (ratesFor($e; $x.tier) as $r | if ($r | length) == 0 then null else price($r; $x) end) end;
def sums: { calls: (map(.calls) | add // 0), i: (map(.i) | add // 0), c: (map(.c) | add // 0), o: (map(.o) | add // 0), usd: (map(.usd // 0) | add // 0), unpriced: (map(select(.usd == null) | .calls) | add // 0) };
{{- end }}
