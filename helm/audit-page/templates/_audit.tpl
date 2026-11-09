{{/* Helpers shared by the /audit page (AUDIT-9, installer#415). Each jq define is ONE line, so it
     can be included inside a single-quoted `${ … }` expression; none of them uses a single quote. */}}

{{/* WHAT A FILTER MAY CONTAIN. These checks sit between a URL query param and the SQL string
     literal it lands in, so they ALLOW rather than strip: a value with any other character is
     refused and the stage makes no call (the obs.traces.valid rule). None of the classes admits a
     quote, a backslash, a space or a percent sign.
       namespace: a Kubernetes namespace name (RFC 1123 label, at most 63 characters). The same
                  check runs on every name the namespaces stage returns before it goes into SQL.
       user:      the class obs.traces.valid allows for a user (usernames, emails,
                  system:serviceaccount:…, OIDC subjects with + / =). @uri-encoded in the URL.
       outcome:   the audit library's three words (snowplow internal/support/audit Event.Outcome).
       traceId:   32 lowercase hex characters, as the exporter writes it. */}}
{{- define "audit.valid" -}}
def okNs: test("^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$"); def okNsFilter: . == "" or okNs; def okUser: . == "" or test("^[A-Za-z0-9._@:+=/-]{1,256}$"); def okOutcome: . == "" or . == "success" or . == "failure" or . == "denied"; def okTraceId: test("^[0-9a-f]{32}$");
{{- end }}

{{/* WHICH ROWS THE CALLER MAY SEE, from the three apiserver stages every audit RESTAction runs
     (see restaction.audit-records.yaml for why each one asks what it asks):
       all: the caller may list the audit trail (auditrecords.audit.krateo.io) in EVERY namespace,
            a grant only a ClusterRoleBinding can make, so no namespace predicate is applied and
            records of deleted namespaces, or with none, are shown;
       acc: otherwise, the namespaces where the caller may list a composition kind, each checked
            as a namespace name again before it goes into SQL. Empty means no call at all.
     Evaluated against the resolve dict. */}}
{{- define "audit.access" -}}
{all: (.allNamespaces == true), acc: ([ (.namespaces // []) | if type == "array" then .[] else empty end | strings | select(okNs) ] | unique)}
{{- end }}

{{/* The route a filter chip opens: the current filters with one changed. `projects` is the shell
     scope and rides every route, as on the other pages. */}}
{{- define "audit.route" -}}
def auditRoute($over): ({range: ((.range // "24h") | tostring), auditNamespace: ((.auditNamespace // "") | tostring), auditOutcome: ((.auditOutcome // "") | tostring), auditUser: ((.auditUser // "") | tostring), projects: ((.projects // "") | tostring)} + $over) as $q | ["range", "auditNamespace", "auditOutcome", "auditUser", "projects"] | map(select($q[.] != "") | . + "=" + ($q[.] | @uri)) | "/audit?" + join("&");
{{- end }}

{{/* The window a ?range selects: 1h, 24h (the default; anything else is 24h too), 7d, 30d. No
     unbounded choice: the trail keeps 365 days, and one screen does not need them. */}}
{{- define "audit.window" -}}
(if .r == "1h" then "1%20HOUR" elif .r == "7d" then "7%20DAY" elif .r == "30d" then "30%20DAY" else "24%20HOUR" end) as $iv
{{- end }}

{{/* SQL TEXT IS ENCODED HERE, AT RENDER TIME, so every %XX in a stage path is a STRING LITERAL of
     the path program. snowplow (internal/pathtemplate, snowplow#1123) sends literals as written and
     escapes every spliced value once, `%` included, so a fragment built at request time would be
     encoded twice — the portal 1.8.99 Traces bug. Go's QueryEscape writes a space as `+`, which
     a query string reads as a space but ClickHouse's own encoding test (no bare `+`) would not, so
     it becomes %20; a literal `+` was already %2B. The output holds only unreserved characters and
     %XX: no quote can close the jq string or the YAML scalar around it. */}}
{{- define "audit.enc" -}}
{{- . | urlquery | replace "+" "%20" -}}
{{- end }}

{{/* The audit population: the two scopes that write audit records into krateo_audit. The table
     also holds the agent gateway's access log (ScopeName `request`) and test rows; neither is an
     audit record and neither may appear. Ends where the window is spliced in. */}}
{{- define "audit.sql.population" -}}
{{- include "audit.enc" "ScopeName IN ('github.com/krateo-platformops/snowplow/audit', 'rest-dynamic-controller/audit') AND Timestamp > now() - INTERVAL " -}}
{{- end }}

{{/* A preview (dry run): the key the shared audit library writes from v0.4.0, or the krateo.action
     suffix snowplow writes today (`call.dryRun`, `restaction.dryRun`). */}}
{{- define "audit.sql.dryRun" -}}
(LogAttributes['krateo.audit.dry_run'] = 'true' OR endsWith(LogAttributes['krateo.action'], '.dryRun'))
{{- end }}

{{/* Whether the record's trace was kept: its TraceId is in otel_traces, which keeps 7 days. The
     inner set is the audit trail's own trace ids for those 7 days, so the lookup is bounded by the
     trail, not by every trace. TraceId is a LogRecord field, not an attribute. */}}
{{- define "audit.sql.kept" -}}
(TraceId != '' AND TraceId IN (SELECT TraceId FROM otel_traces WHERE Timestamp > now() - INTERVAL 7 DAY AND TraceId IN (SELECT TraceId FROM krateo_audit WHERE ScopeName IN ('github.com/krateo-platformops/snowplow/audit', 'rest-dynamic-controller/audit') AND Timestamp > now() - INTERVAL 7 DAY AND TraceId != '')))
{{- end }}

{{/* A record this page cannot fully read: no user.name, no namespace, no target (a resource, a
     kind or a server address) or an outcome outside the library's three words. It is still shown,
     with "—" where a field is missing; the count goes in the note. The table's own classification
     (audit.format, auditConforming) is the same rule, and the note compares the two. */}}
{{- define "audit.sql.unread" -}}
(LogAttributes['user.name'] = '' OR LogAttributes['k8s.namespace.name'] = '' OR (LogAttributes['k8s.resource.resource'] = '' AND LogAttributes['k8s.resource.kind'] = '' AND LogAttributes['server.address'] = '') OR LogAttributes['outcome'] NOT IN ('success', 'failure', 'denied'))
{{- end }}

{{/* The predicates request input adds, after the window, as jq bound to variables: the access
     predicate ($acc) and the three chip filters ($nsC, $usrC, $ocC). Each is an `if` whose branches
     are literals, so snowplow sends them as written; the only spliced values are the checked
     namespace, user (@uri-encoded) and outcome, and the namespace list, which is @uri-encoded here. */}}
{{- define "audit.sql.filters" -}}
([39] | implode) as $q | (if .all then "" else "{{ include "audit.enc" " AND LogAttributes['k8s.namespace.name'] IN (" }}" + (.acc | map($q + . + $q) | join(", ") | @uri) + "%29" end) as $acc | (if .ns == "" then "" else "{{ include "audit.enc" " AND LogAttributes['k8s.namespace.name'] = '" }}" + .ns + "%27" end) as $nsC | (if .usr == "" then "" else "{{ include "audit.enc" " AND LogAttributes['user.name'] = '" }}" + (.usr | @uri) + "%27" end) as $usrC | (if .oc == "" then "" else "{{ include "audit.enc" " AND LogAttributes['outcome'] = '" }}" + .oc + "%27" end) as $ocC
{{- end }}

{{/* Display formats. A record time is epoch milliseconds, shown in UTC to the second. Action comes
     from the HTTP method and is never guessed: POST Create, PATCH/PUT Update, DELETE Delete, no
     method "—", any other method verbatim. An export (krateo.action `export`, snowplow
     internal/support/audit Event.Action) is a read sent as GET or POST, so it is named for what it
     is before the method is read, or a POST export would show as Create.
     Resource is the plural (k8s.resource.resource), or the Kind where a producer writes only that
     (rest-dynamic-controller), then its API group — `core` for a plural with no group, as kubectl
     says it. A Table cell has one colour, so the group cannot be grey beside a plain plural; it
     follows a middle dot instead. A `rest.write` record names the external host it wrote to. */}}
{{- define "audit.format" -}}
def auditUtc: (tonumber? // 0) as $ms | (($ms / 1000) | floor | todate) as $iso | $iso[0:10] + " " + $iso[11:19]; def auditAction: if .action == "export" then "Export" elif .method == "POST" then "Create" elif .method == "PATCH" or .method == "PUT" then "Update" elif .method == "DELETE" then "Delete" elif .method == "" then "—" else .method end; def auditResource: (if .resource != "" then .resource else .kind end) as $r | (if $r == "" then "" elif .group != "" then .group elif .resource != "" then "core" else "" end) as $g | (if .action == "rest.write" and .server != "" then .server else "" end) as $s | ([ $r, $g ] | map(select(. != "")) | join(" · ")) as $rg | if $rg == "" and $s == "" then "—" elif $s == "" then $rg elif $rg == "" then "on " + $s else $rg + ", on " + $s end; def auditOutcomeTag: (if .outcome == "" then "—" else .outcome end) as $w | {label: ($w + (if .code == "" then "" else " · " + .code end)), color: (if .outcome == "success" then "green" elif .outcome == "failure" then "red" elif .outcome == "denied" then "orange" else "gray" end)}; def auditTrace: if .untraced then "no trace (producer)" elif .link != "" then "open ↗" elif .kept then "kept" else "—" end; def dash: if . == "" then "—" else . end;
{{- end }}

{{/* THE THREE STAGES THAT DECIDE WHICH ROWS THE CALLER MAY SEE, shared by every audit RESTAction
     (restaction.audit-records.yaml says why each asks what it asks). Each runs on snowplow's
     ServiceAccount and is narrowed per item by the CALLER's RBAC (userAccessFilter,
     resolvers/restactions/api/refilter.go applyUserAccessFilterOnPig), and each fails CLOSED: an
     error, an unresolved resource set or a missing identity leaves the list empty and
     `allNamespaces` false. snowplow refuses a request whose extras name a stage or an errorKey
     (#1116, resolvers/restactions/api/reserved_extras.go), so `?namespaces=` or
     `?allNamespaces=true` cannot stand in for a stage that failed. */}}
{{- define "audit.accessStages" -}}
- name: crds
  path: /apis/core.krateo.io/v1alpha1/compositiondefinitions
  verb: GET
  headers:
    - 'Accept: application/json'
  userAccessFilter:
    verb: list
    group: core.krateo.io
    resource: compositiondefinitions
    namespaceFrom: .metadata.namespace
  filter: "[.crds.items[]? | select(.status.resource != null) | .status.resource] | unique"
  continueOnError: true
  errorKey: crdsError
- name: namespaces
  path: /api/v1/namespaces
  verb: GET
  headers:
    - 'Accept: application/json'
  dependsOn:
    name: crds
  userAccessFilter:
    verb: list
    group: composition.krateo.io
    resourcesFrom: "[ (.crds // [])[] ]"
    namespaceFrom: .metadata.name
  filter: "[.namespaces.items[]? | .metadata.name]"
  continueOnError: true
  errorKey: namespacesError
{{- /* The cluster-scope check names ONE static resource, the platform's own audit trail kind,
       so it does not depend on which compositions are installed: with no CompositionDefinition
       `crds` is empty, a resourcesFrom over it fails closed, and an admin would have seen
       nothing. RBAC matches resource strings, so the check holds whether or not the AuditRecord
       CRD is served. No dependsOn: it reads nothing another stage produces. */}}
- name: allNamespaces
  path: /api/v1/namespaces/{{ .Values.namespace }}
  verb: GET
  headers:
    - 'Accept: application/json'
  userAccessFilter:
    verb: list
    group: audit.krateo.io
    resource: auditrecords
    namespaceFrom: .metadata.namespace
  filter: '((.allNamespaces // {}) | (.metadata.name // "")) != ""'
  continueOnError: true
  errorKey: allNamespacesError
{{- end }}

{{/* The request input every audit query reads, checked, joined with the caller's access. Yields
     nothing — so the ClickHouse stage makes NO call — when a filter holds a character outside its
     class or the caller may see no namespace. */}}
{{- define "audit.iterator" -}}
{{ include "audit.valid" . }} [ ({r: ((.range // "24h") | tostring), ns: ((.auditNamespace // "") | tostring), usr: ((.auditUser // "") | tostring), oc: ((.auditOutcome // "") | tostring)} + ({{ include "audit.access" . }})) | select((.ns | okNsFilter) and (.usr | okUser) and (.oc | okOutcome) and (.all or (.acc | length) > 0)) ]
{{- end }}

{{/* What a range is called in a sentence. */}}
{{- define "audit.rangeLabel" -}}
def rangeLabel: if . == "1h" then "the last hour" elif . == "7d" then "the last 7 days" elif . == "30d" then "the last 30 days" else "the last 24 hours" end;
{{- end }}

{{/* The namespace the observability chart installs its pages into, where the trace page's root
     (`page-trace-detail`) lives. It goes into an apiserver path, so anything but a namespace name
     fails the render. */}}
{{- define "audit.traceNamespace" -}}
{{- $n := .Values.traceNamespace | default "" -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$" $n) -}}
{{- fail (printf "traceNamespace %q: must be a namespace name (the observability page chart's `namespace`)" $n) -}}
{{- end -}}
{{- $n -}}
{{- end }}
