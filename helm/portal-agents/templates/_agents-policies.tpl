{{/*
portal.agentPolicyRoles — THE role taxonomy for /agents/policies, in reading order.

This list is the single source of truth for three things that MUST agree or the page breaks in a
way helm lint cannot see:

  1. which role Cards + Tables exist            (cards.agents-policies-by-role.yaml)
  2. which resourceRefIds the groups Flex may reference (flex.agents-policies-groups.yaml)
  3. the ORDER the sections appear in

Keeping the order in two hand-written lists is exactly the kind of drift that produces a Card
nothing references (invisible, still fetched) or a resourceRefId with no object behind it (an empty
slot). Both files range over THIS list instead.

`key` must match the role string the agents-policies RESTAction emits in `.role` — the jq
classifier and this taxonomy are two halves of one decision. A key here with no jq branch renders
an always-empty (and therefore never-included) card; a jq role with no key here renders policies
that EXIST BUT ARE NEVER SHOWN while still counting toward the pagehead total, so the page would
claim 29 policies and display 28. That is why "other" — the jq's fallback for a spec block the
classifier does not understand — is carded here like any other role. On a cluster this portal fully
understands it has zero members and the groups Flex omits it entirely; the day a gateway upgrade
introduces a block we have no branch for, the policy surfaces as Unclassified with its attachment
intact instead of vanishing, and its "What it enforces" cell names the spec blocks it carries
rather than an em-dash. Nothing the gateway enforces is ever invisible on this page.

Reading order is identity -> what it may say -> how much -> what happens when it fails -> what we
record -> whose credential -> who may call from a browser. It walks a single request through the
gateway rather than sorting by size; Unclassified sits last because it is an exception, not a step.
*/}}
{{- define "portal.agentPolicyRoles" -}}
[
  {"key":"identity","label":"Identity & authorization",
   "desc":"Who may call what — JWT verification, API-key and basic credentials, external authorization, and the CEL expressions the gateway evaluates on every request."},
  {"key":"guardrail","label":"Prompt guardrails",
   "desc":"Regex guards over prompts and completions; each guard either masks the match or rejects the call."},
  {"key":"limits","label":"Rate & token limits",
   "desc":"The budget for a caller: request rate, token spend, request body size and the deadline."},
  {"key":"resiliency","label":"Resiliency & retry",
   "desc":"Which upstream failures the gateway retries on the caller's behalf, how many times and how fast."},
  {"key":"observability","label":"Observability",
   "desc":"What the gateway records about every hop, and where it ships it."},
  {"key":"passthrough","label":"Credential passthrough",
   "desc":"Backends that receive the caller's own credential instead of one the gateway holds."},
  {"key":"cors","label":"Browser access (CORS)",
   "desc":"Which origins, methods and headers a browser may use against the gateway."},
  {"key":"other","label":"Unclassified",
   "desc":"Policies carrying a spec block this portal does not summarise yet. The blocks each one carries are named in full, so nothing the gateway enforces is missing from this page."}
]
{{- end -}}
