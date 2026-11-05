{{/* COPIED FROM helm/portal/templates/_fetchable.tpl when every page became its own chart (#261).

     Copied rather than referenced: a `define` lives in the chart that declares it, so calling
     portal.* from here renders "template not defined" and fails the install. The set is a FIXPOINT
     over the includes, not a hand-kept list — reviews-page needed _fetchable.tpl only because
     _review.tpl calls into it, which a one-level scan missed and a render caught. */}}
{{/*
  portal.fetchableDefs — the jq defs that keep a data-driven step from reading Secrets.

  A step whose path is built from DATA (a composition's status.managed[].path, a route's
  group/version/plural, a discovery path) can name any resource, Secrets included — and snowplow
  serves a secrets GET from a cluster-wide informer it registers on first touch, every Secret's
  data with it (scripts/lint-ra-secrets.py). So every such step's iterator keeps only the paths
  fetchablePath accepts:
    - any path under /apis/<group> (no Secret lives outside the core group);
    - a core path (/api/<version>/...) only when it names no resource (discovery, a namespace) or
      a resource in fetchableCore — an explicit ALLOWLIST, so a core kind added to Kubernetes is
      unread until someone adds it here, and Secrets are never in it.
  fetchableTarget applies that guard and splits the path into the single values portal.targetPath
  needs, in one step, and yields nothing for a path it cannot rebuild exactly.
  lint-ra-secrets.py accepts a data-driven step only when its path is portal.targetPath over an item
  from fetchableTarget, or one field of the item (`${ .path }`), its iterator BEGINS with these defs exactly as rendered here (whitespace aside),
  defines neither again after them, and applies fetchablePath to that field; fetchableCore names
  no secret. Change the defs here, never in a template: a copy anywhere else fails the lint.

  Emits defs only (each ends in `;`): put it at the head of an iterator or filter.
*/}}
{{- define "portal.fetchableDefs" -}}
def fetchableCore: ["configmaps", "serviceaccounts", "services", "endpoints", "persistentvolumeclaims", "persistentvolumes", "pods", "namespaces", "nodes", "events", "limitranges", "resourcequotas", "replicationcontrollers", "podtemplates"];
def fetchablePath: (tostring | (split("?") | .[0] // "") | split("/")) as $p
  | if ($p[1] // "") == "apis" then true
    elif ($p[1] // "") == "api" then
      ((if ($p[3] // "") == "namespaces" then ($p[5] // "") else ($p[3] // "") end)) as $res
      | ($res == "" or (fetchableCore | index($res)) != null)
    else false end;
def fetchableTarget: (tostring | select(fetchablePath and (test("[?#%\\\\]") | not))) as $path | ($path | split("/")) as $p
  | (if ($p[1] // "") == "api" then { g: "", v: ($p[2] // ""), r: $p[3:] } else { g: ($p[2] // ""), v: ($p[3] // ""), r: $p[4:] } end) as $t
  | (if ($t.r[0] // "") == "namespaces" and ($t.r | length) > 2 then { ns: $t.r[1], r: $t.r[2:] } else { ns: "", r: $t.r } end) as $n
  | { g: $t.g, v: $t.v, ns: $n.ns, pl: ($n.r[0] // ""), nm: ($n.r[1] // "") } as $o
  | select(($n.r | length) <= 2 and $o.v != "" and ([$o.g, $o.v, $o.ns, $o.pl, $o.nm] | all(. != "." and . != "..")))
  | select(((if $o.g == "" then "/api/" + $o.v else "/apis/" + $o.g + "/" + $o.v end) + (if $o.ns == "" then "" else "/namespaces/" + $o.ns end) + (if $o.pl == "" then "" else "/" + $o.pl end) + (if $o.nm == "" then "" else "/" + $o.nm end)) == $path)
  | $o;
{{- end -}}

{{/*
  portal.targetPath — the path template for a step whose item came from fetchableTarget.

  Every value spliced into a stage path is one segment on snowplow #1128 and later, so a whole path
  held in one field is refused there. This builds the same path from single values, which renders the
  same on every snowplow. A Kubernetes path has a fixed grammar (a group or the core API, a version, an
  optional namespace, then a resource and a name), so nothing here depends on how many segments it has.
  Measured 2026-11-05: 1822 status.managed paths on krateo-057 and alpitour, none outside that grammar.
*/}}
{{- define "portal.targetPath" -}}
(if .g == "" then "/api/" + .v else "/apis/" + .g + "/" + .v end) + (if .ns == "" then "" else "/namespaces/" + .ns end) + (if .pl == "" then "" else "/" + .pl end) + (if .nm == "" then "" else "/" + .nm end)
{{- end -}}
