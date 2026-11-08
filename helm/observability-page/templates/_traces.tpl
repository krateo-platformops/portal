{{/* jq shared by the Traces tab and the trace page (installer#404). Each define is ONE line, so it
     can be included inside a single-quoted `${ … }` expression; none of them uses a single quote. */}}

{{/* WHAT A FILTER MAY CONTAIN. These are the only checks between a URL query param and the SQL
     string literal it lands in, so they ALLOW rather than strip: a value with any other character is
     refused, and the step makes no call at all. Stripping (the obs-log-stream idiom) would turn
     `ad'min` into `admin` and show another user's spans. None of these classes admits a quote, a
     backslash, a space or a percent sign, so a value that passes cannot leave the literal or change
     the URL's encoding.
       service: OTel ServiceName (`composition-dynamic-controller`, `agentgateway`).
       user:    the gateway's `jwt.sub` — a username, an email, `system:serviceaccount:…`, or an
                OIDC subject (base64: `+`, `/`, `=`). `@uri`-encoded before it goes into the URL.
       status:  the exporter's three words, `Unset`, `Ok`, `Error` (pdata StatusCode.String()).
       traceId: 32 lowercase hex characters, which is how the exporter writes it. */}}
{{- define "obs.traces.valid" -}}
def okService: . == "" or test("^[A-Za-z0-9._-]{1,128}$"); def okUser: . == "" or test("^[A-Za-z0-9._@:+=/-]{1,256}$"); def okStatus: . == "" or . == "Ok" or . == "Error" or . == "Unset"; def okTraceId: test("^[0-9a-f]{32}$");
{{- end }}

{{/* The route a filter chip opens: the current filters with one changed, always on the Traces tab.
     The tab rides in the URL because the Tabs widget remounts whenever the query string changes and
     opens on `?tab=` (else the first tab) — without it every chip click would land on
     Reconciliation. `projects` is the shell scope and rides every route. */}}
{{- define "obs.traces.route" -}}
def tracesRoute($over): ({range: ((.range // "24h") | tostring), traceService: ((.traceService // "") | tostring), traceUser: ((.traceUser // "") | tostring), traceStatus: ((.traceStatus // "") | tostring), projects: ((.projects // "") | tostring)} + $over) as $q | ["range", "traceService", "traceUser", "traceStatus", "projects"] | map(select($q[.] != "") | . + "=" + ($q[.] | @uri)) | "/observability?" + join("&") + "&tab=Traces";
{{- end }}

{{/* Display formats. Duration is nanoseconds (the exporter writes End - Start in ns); a start time
     is epoch milliseconds, shown in UTC with milliseconds. */}}
{{- define "obs.traces.format" -}}
def traceDuration: (tonumber? // 0) as $ns | if $ns <= 0 then "0 ms" elif $ns < 1000000 then ((($ns / 1000) | floor | tostring) + " µs") elif $ns < 1000000000 then ((($ns / 1000000) | floor | tostring) + " ms") elif $ns < 9995000000 then ((($ns / 10000000) | round) as $c | (($c / 100) | floor | tostring) + "." + ("0" + (($c % 100) | tostring))[-2:] + " s") else ((($ns / 100000000) | round) as $d | (($d / 10) | floor | tostring) + "." + (($d % 10) | tostring) + " s") end; def traceUtc: (tonumber? // 0) as $ms | (($ms / 1000) | floor | todate) as $iso | $iso[0:10] + " " + $iso[11:19] + "." + ("00" + (($ms % 1000) | floor | tostring))[-3:]; def traceShort: if length > 12 then .[0:4] + "…" + .[-4:] else . end; def traceStatusTag: if . == "Error" then {label: "Error", color: "red"} elif . == "Ok" then {label: "OK", color: "green"} elif . == "Unset" then {label: "Unset", color: "gray"} else {label: (if . == "" then "—" else . end), color: "gray"} end;
{{- end }}

{{/* The service the span list leaves out while no Service filter is chosen (`tracesHiddenService`,
     HyperDX's own instrumentation by default), as a jq string literal. It is a chart value, not
     request input, but it still lands in an SQL literal, so a value outside the service class
     fails the render instead of reaching ClickHouse. */}}
{{- define "obs.traces.hidden" -}}
{{- $h := .Values.tracesHiddenService | default "" -}}
{{- if not (regexMatch "^[A-Za-z0-9._-]{0,128}$" $h) -}}
{{- fail (printf "tracesHiddenService %q: only letters, digits, '.', '_' and '-' (it goes into SQL)" $h) -}}
{{- end -}}
{{- $h | toJson -}}
{{- end }}

{{/* What the filter bar says while that service is left out. */}}
{{- define "obs.traces.hiddenLabel" -}}
{{- $h := .Values.tracesHiddenService | default "" -}}
{{- if eq $h "krateo-observability" -}}HyperDX internals hidden{{- else -}}{{ $h }} spans hidden{{- end -}}
{{- end }}
