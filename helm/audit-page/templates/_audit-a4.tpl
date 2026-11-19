{{/* Helpers of the /audit dashboard (mockup A4): the search, the links that keep it, and the
     activity buckets. They live here, not in _audit.tpl, because _audit.tpl is copied byte for
     byte into dashboard-page (test-dashboard-inbox.py), which has no search. Each jq define is ONE
     line, so it can be included inside a single-quoted `${ … }` expression; none of them uses a
     single quote. */}}

{{/* WHAT THE SEARCH MAY CONTAIN. `?q=` is free text, so unlike the chip filters it is not checked
     against a character class: it never becomes SQL text at all. The query names a ClickHouse
     query parameter, {q:String}, and the value travels as the separate URL parameter `param_q`,
     which ClickHouse binds as a String (HTTP interface, "Queries with Parameters"): a quote, a
     parenthesis or a semicolon in it is a character to look for, not syntax. ClickHouse reads a
     parameter in its escaped format, so a backslash is doubled (audit.sql.search) to be searched
     for as itself.
       qText:  trimmed, as the other A4 pages read ?q=.
       okQ:    at most 200 characters and no control character (a newline or a tab would be read
               as an escape or a field break); anything else is refused like a bad chip filter and
               the stage makes no call. */}}
{{- define "audit.q.valid" -}}
def qText: tostring | sub("^\\s+"; "") | sub("\\s+$"; ""); def okQ: (length <= 200) and (test("[\\x00-\\x1f\\x7f]") | not);
{{- end }}

{{/* The request input of a ClickHouse stage on this page: audit.iterator (_audit.tpl) with the
     search added, checked the same way. Yields nothing — no call — when any input is refused or
     the caller may see no namespace. */}}
{{- define "audit.iteratorQ" -}}
{{ include "audit.valid" . }} {{ include "audit.q.valid" . }} [ ({r: ((.range // "24h") | tostring), ns: ((.auditNamespace // "") | tostring), usr: ((.auditUser // "") | tostring), oc: ((.auditOutcome // "") | tostring), q: ((.q // "") | qText)} + ({{ include "audit.access" . }})) | select((.ns | okNsFilter) and (.usr | okUser) and (.oc | okOutcome) and (.q | okQ) and (.all or (.acc | length) > 0)) ]
{{- end }}

{{/* THE SEARCH PREDICATE, as jq bound to $qC (the SQL, a literal) and $qP (the URL parameter that
     carries the value). Case-insensitive `contains` over what a record shows: the user, the action
     (as the page names it — Create, Update, Delete, Export — and as written: krateo.action and the
     HTTP method), the resource, kind, group and name, the namespace, the outcome and the external
     host. Both are empty when there is no search, so the SQL is then exactly the unsearched one. */}}
{{- define "audit.sql.search" -}}
{{- $fields := list "LogAttributes['user.name']" "CASE WHEN LogAttributes['krateo.action'] = 'export' THEN 'Export' WHEN LogAttributes['http.request.method'] = 'POST' THEN 'Create' WHEN LogAttributes['http.request.method'] IN ('PATCH', 'PUT') THEN 'Update' WHEN LogAttributes['http.request.method'] = 'DELETE' THEN 'Delete' ELSE '' END" "LogAttributes['krateo.action']" "LogAttributes['http.request.method']" "LogAttributes['k8s.resource.resource']" "LogAttributes['k8s.resource.kind']" "LogAttributes['k8s.resource.group']" "LogAttributes['k8s.resource.name']" "LogAttributes['k8s.namespace.name']" "LogAttributes['outcome']" "LogAttributes['server.address']" -}}
{{- $terms := list -}}
{{- range $fields -}}{{- $terms = append $terms (printf "positionCaseInsensitiveUTF8(%s, {q:String}) > 0" .) -}}{{- end -}}
(if .q == "" then "" else "{{ include "audit.enc" (printf " AND (%s)" (join " OR " $terms)) }}" end) as $qC | (if .q == "" then "" else "&param_q=" + (.q | gsub("\\\\"; "\\\\") | @uri) end) as $qP
{{- end }}

{{/* The route a link on the page opens: the current filters, the view and the search, with the
     ones in $over changed. audit.route (_audit.tpl) without ?view and ?q, which every link here
     must keep. `range` is always written, as audit.route writes it. */}}
{{- define "audit.href" -}}
def auditHref($over): ({range: ((.range // "24h") | tostring), auditNamespace: ((.auditNamespace // "") | tostring), auditOutcome: ((.auditOutcome // "") | tostring), auditUser: ((.auditUser // "") | tostring), projects: ((.projects // "") | tostring), view: (if ((.view // "") | tostring) == "table" then "table" else "" end), q: ((.q // "") | tostring | sub("^\\s+"; "") | sub("\\s+$"; ""))} + $over) as $p | ["range", "auditNamespace", "auditOutcome", "auditUser", "projects", "view", "q"] | map(select($p[.] != "") | . + "=" + ($p[.] | @uri)) | "/audit?" + join("&");
{{- end }}

{{/* THE ACTIVITY BUCKETS of a range: 1h per 5 minutes (12), 24h per hour (24, the default), 7d and
     30d per day (7, 30), the newest bucket the current one. $bk is the SQL interval (a literal,
     encoded at render time like audit.window); auditBuckets is the jq side, the same sizes in
     seconds. ClickHouse buckets with toStartOfInterval in UTC, which for these sizes is a multiple
     of the size since the epoch, the same arithmetic the jq does. */}}
{{- define "audit.bucket" -}}
(if .r == "1h" then "5%20MINUTE" elif .r == "7d" or .r == "30d" then "1%20DAY" else "1%20HOUR" end) as $bk
{{- end }}
{{- define "audit.buckets" -}}
def auditBuckets: if . == "1h" then {size: 300, n: 12, unit: "minute"} elif . == "7d" then {size: 86400, n: 7, unit: "day"} elif . == "30d" then {size: 86400, n: 30, unit: "day"} else {size: 3600, n: 24, unit: "hour"} end;
{{- end }}
