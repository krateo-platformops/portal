{{/*
  portal.builderPrEnding — the jq `def ending($pr)` both builder lists use to decide how a change
  request ended, and so whether its row leads to Register.

  ONE DEFINITION, because two copies would disagree about the one thing that must not be guessed.
  restaction.blueprint-builder-deliverables and restaction.builder-prs each list the same
  PullRequest CRs; if one of them called a PR merged on weaker evidence than the other, the same
  change would be "Merged" on one page and "Closed" on the next, and one of them would be
  offering to register a chart nobody released.

  The evidence: github-provider-kog writes status.state (open|closed), and a merge is only
  observable as `merged: true` beside state closed. Up to 0.3.1 the PullRequest RestDefinition
  does not list `merged` in additionalStatusFields at all (the CRD status is exactly
  conditions/html_url/number/state), so today every merged PR reads as a plain close. The def
  therefore keeps three endings apart instead of two:
    "merged"  — closed, and the provider says merged: true;
    "closed?" — closed, and the provider does not report `merged` (absent or not a boolean) —
                it may have merged, and nothing here knows;
    "closed"  — closed, and the provider says merged: false;
    ""        — still open, not reconciled yet ("pending"), or no PR at all.
  A caller must never render "closed?" as merged. It can still offer Register — the person checks
  the PR — and once the provider reports the field the same row turns into "merged" or "closed"
  with no change here.

  It reads the projection the RAs build from each PR ({state, merged, mergedKnown}); `mergedKnown`
  is `(.status.merged | type) == "boolean"`, computed where the raw CR is in hand.

  Emits a jq definition (with its trailing `;`) meant for the head of a LITERAL (|) filter block:
    filter: |
      {{- include "portal.builderPrEnding" . | nindent 4 }}
      ...rest of the filter
*/}}
{{- define "portal.builderPrEnding" -}}
def ending($pr):
  if $pr == null or ((($pr.state) // "") != "closed") then ""
  elif (($pr.merged) // false) == true then "merged"
  elif ((($pr.mergedKnown) // false) | not) then "closed?"
  else "closed" end;
{{- end -}}
