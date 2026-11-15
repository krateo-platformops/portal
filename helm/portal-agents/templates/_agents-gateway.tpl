{{/*
portal.agentsGateway: whether the agent gateway can reach an agent, as jq defs.

ONE COPY PER CHART THAT ASKS. portal-agents (restaction.agents-list, the /agents Gateway column)
and dashboard-page (restaction.dashboard-inbox, the "agent not reachable through the gateway" card)
each carry this file. A `define` cannot cross charts, so the two copies are kept byte-for-byte the
same and scripts/test-dashboard-inbox.py fails when they differ. Change both or neither.

WHAT IT DECIDES. An Agent going Ready says the CONTROLLER accepted it. It says nothing about whether
anything can call it: every hop crosses the agentgateway, and the gateway only forwards to an agent
that has BOTH
  (a) an HTTPRoute whose rule matches `x-kagent-host: <name>.<namespace>`, which is how the gateway
      picks the backend at all, and
  (b) Allow clauses naming that same host in the gateway's authorization policy. There are TWO of
      them: one for the anonymous `/.well-known/agent-card.json` discovery GET and one for the
      authenticated call.
Miss either and the agent reports Ready and Accepted while every call to it is refused
(krateo-blueprint-agent, 2026-09-03: Ready, delegated to, 403 at the agent-card hop for a week).

THREE-STATE, NEVER TWO. "No route matches this host" is a finding ONLY when routes were readable AND
some route came back. An empty list is snowplow's silent userAccessFilter refilter (a caller without
the gateway.networking.k8s.io grant is served [], not a 403) and must never be drawn as "this agent
is unreachable". The same holds for the policies: the verdict is withheld unless at least one
Allow-action authorization policy is visible. `agw_known` is that test, and `agw_gap` answers ""
for every agent while it is false.

The defs read the resolve dict's `routes` and `policies` stages (an HTTPRoute list and an
AgentgatewayPolicy list); the callers pass in the two stages' error text ("" when the read worked).
  agw_hosts                        the x-kagent-host values the gateway routes. Header matches are
                                   EXACT string equality in Gateway API, and header NAMES are
                                   case-insensitive per HTTP, so the name is downcased and the
                                   value is not.
  agw_allow_pols                   Allow-action authorization policies only. A Require or Deny
                                   policy naming the same host is not an allowance.
  agw_allow_exprs                  their matchExpressions.
  agw_known($rtErr; $polErr)       both reads worked AND each returned something to judge by.
  agw_unknown_why($rtErr; $polErr) why the verdict is withheld, as a clause ("" when it is not).
  agw_gap($host; $hosts; $exprs; $known)
                                   the MISSING ARTEFACT for one agent's host, or "" (registered, or
                                   not checked). The two Allow clauses are told apart by the path
                                   they gate: the agent-card clause carries
                                   `request.path.endsWith('/.well-known/agent-card.json')`, the call
                                   clause is the bare host match. "no Allow rule" is reserved for an
                                   agent the policy does not mention at all; "calls not allowed" is
                                   the narrower case where only the agent-card clause survives;
                                   "agent card refused" is the reverse, how krateo-blueprint-agent
                                   failed.
*/}}
{{- define "portal.agentsGateway" -}}
def agw_hosts:
  [ (.routes.items // [])[] | (.spec.rules // [])[] | (.matches // [])[] | (.headers // [])[]
    | select( ((.name // "") | ascii_downcase) == "x-kagent-host" ) | (.value // "") ]
  | unique;
def agw_allow_pols:
  [ (.policies.items // [])[]
    | select( (((.spec.traffic // {}).authorization) // null) != null )
    | select( ((.spec.traffic.authorization.action) // "Allow") == "Allow" ) ];
def agw_allow_exprs:
  [ agw_allow_pols[] | ((.spec.traffic.authorization.policy.matchExpressions // [])[]) ];
def agw_known($rtErr; $polErr):
  ( ($rtErr == "") and (((.routes.items // []) | length) > 0) )
  and ( ($polErr == "") and ((agw_allow_pols | length) > 0) );
def agw_unknown_why($rtErr; $polErr):
  [ (if $rtErr != "" then ("the HTTPRoute read failed — " + $rtErr)
     elif ((.routes.items // []) | length) == 0 then "no HTTPRoute is visible to you"
     else empty end),
    (if $polErr != "" then ("the AgentgatewayPolicy read failed — " + $polErr)
     elif (agw_allow_pols | length) == 0 then "no gateway Allow policy is visible to you"
     else empty end) ] | join("; ");
def agw_gap($host; $hosts; $exprs; $known):
  ( any($hosts[]; . == $host) ) as $routed
  | ( ("'" + $host + "'") as $q
      | ( [ $exprs[] | select(contains($q)) ] ) as $mine
      | { card: ( ( [ $mine[] | select(contains("agent-card")) ] | length ) > 0 ),
          call: ( ( [ $mine[] | select(contains("agent-card") | not) ] | length ) > 0 ) } ) as $allow
  | if ($known | not) then ""
    elif ($routed | not) and (($allow.card or $allow.call) | not) then "not routed · no Allow rule"
    elif ($routed | not) then "not routed"
    elif (($allow.card or $allow.call) | not) then "no Allow rule"
    elif ($allow.call | not) then "calls not allowed"
    elif ($allow.card | not) then "agent card refused"
    else "" end;
{{- end -}}
