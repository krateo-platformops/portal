{{/* jq shared by the /observability tabs (the A4 dashboard layout). Each define is ONE line with no
     single quote, so it can open a single-quoted `${ … }` expression as well as a filter block. */}}

{{/* obs.href — the route a chip on /observability opens: every page param the address holds, with
     the ones in $over changed, in one fixed order, an empty one left out. The page params are the
     page root's krateo.io/filter-keys less `tab`: the time range, the Telemetry log filters, the
     latency view, the Traces filters, the Reconciliation and Components state chips, the search and
     Summary | Full list. `projects` (the shell scope) rides every route. `$tab` names the tab the
     chip lives on, "" for none: the frontend carries the open tab on a same-page navigation and a
     target that names its own wins, so naming it is only belt and braces for an older frontend.
     Input: the request extras (a RESTAction filter's `.`, or a no-apiRef widget's). */}}
{{- define "obs.href" -}}
def obsHref($over; $tab): . as $x | ["projects", "range", "serviceName", "limit", "latencyBy", "traceService", "traceUser", "traceStatus", "status", "componentStatus", "q", "view"] as $keys | (reduce $keys[] as $k ({}; . + {($k): (($x[$k] // "") | tostring)}) + $over) as $p | [ $keys[] | select($p[.] != "") | . + "=" + ($p[.] | @uri) ] + (if $tab == "" then [] else ["tab=" + $tab] end) | "/observability" + (if length == 0 then "" else "?" + join("&") end);
{{- end }}

{{/* obs.search — the page search as one value: ?q= trimmed ($qRaw, for routes and messages) and
     lower-cased ($q, for matching). `hits($q)` is true of a row whose fields (a list of strings)
     contain it, case-insensitively; every row hits an empty search. */}}
{{- define "obs.search" -}}
def qRaw: ((.q // "") | tostring | sub("^\\s+"; "") | sub("\\s+$"; "")); def hits($q): $q == "" or (map(tostring | ascii_downcase) | any(contains($q)));
{{- end }}
