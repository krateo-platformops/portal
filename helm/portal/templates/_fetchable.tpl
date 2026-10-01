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
  lint-ra-secrets.py accepts a data-driven step only when its iterator carries these defs, applies
  fetchablePath, and fetchableCore names no secret.

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
{{- end -}}
