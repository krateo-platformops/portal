{{/*
  portal.builderPrEnding — the jq `def ending($pr)` both builder lists use to decide how a change
  request ended, and so whether its row leads to the Install step. The install page's
  change-request note (restaction.blueprint-install-origin) reads the same def, so the page says
  "merged" exactly when the row did.

  ONE DEFINITION, because two copies would disagree about the one thing that must not be guessed.
  restaction.blueprint-builder-deliverables and restaction.builder-prs each list the same
  PullRequest CRs; if one of them called a PR merged on weaker evidence than the other, the same
  change would be "Merged" on one page and "Closed" on the next, and one of them would be
  offering to install a chart nobody released.

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
  A caller must never render "closed?" as merged. It can still offer Install — the install page
  links the PR, so the person checks it where they decide — and once the provider reports the
  field the same row turns into "merged" or "closed" with no change here.

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

{{/*
  portal.builderIsRegistrationFile — a jq boolean over one git-provider LocalResource: is it the
  ROOT compositiondefinition.yaml of a builder publish, the file the Install step pre-fills from?

  ROOT FILE ONLY. fileName is a basename (the CRD forbids slashes; the directory rides in
  toRepo.path), so a blueprint that ships a CompositionDefinition as one of its own TEMPLATES
  would also match on the name alone — templates/compositiondefinition.yaml is a LocalResource
  with fileName compositiondefinition.yaml and path /templates. The registration file is the one
  at the repository root, which is where the release workflow reads it.

  ONE TEST, used by the install form's pre-fill (portal.builderRegistrationStep) AND by both
  builder lists, which offer Install only for a publish that carries this file. If the lists
  tested less than the form reads, a row would offer Install for a publish the form cannot
  pre-fill, and the form would fall back to a helm-index chart that happens to share the name:
  the person would install that chart believing it was theirs.
*/}}
{{- define "portal.builderIsRegistrationFile" -}}
((((.spec.fromResource.fileName) // "") == "compositiondefinition.yaml") and ((((.spec.toRepo.path) // "/") as $p | ($p == "/" or $p == ""))))
{{- end -}}

{{/*
  portal.builderRegistrationStep — the `builderCds` api step: every builder registration file in
  the release namespace, as [{publish: <claim name>, cd: <the file's bytes>}].

  A chart published from the Portal or Blueprint builder is in NO helm index: its release workflow
  pushes it to GHCR as OCI. What the publish does carry is the CompositionDefinition it wrote into
  the change request: builder-publish renders every held file as a git-provider LocalResource
  labelled krateo.io/publish: <claim name>, and the file's bytes ride verbatim in
  spec.fromResource.fromString (templates/localresources.yaml there). The builder wrote that file
  with the OCI url the chart's release pushes to, so its url and version are the pre-fill.

  The claim is matched in the TOP-LEVEL filter (portal.builderRegistrationDefs), not here, because
  it needs the route's `name`, and whether ?extras are visible inside a step filter is not
  verified. This step only projects the list down to each root registration file and the claim
  that wrote it: a LocalResource carries the full bytes of its file, and the rest of the list is
  chart sources nobody here reads. No ?labelSelector=: the snowplow LIST dispatch does not
  guarantee query-param passthrough (restaction.component-detail).

  A plain collection GET with no endpointRef, so snowplow runs it as the caller; continueOnError,
  so a person who may not list LocalResources, or an install without git-provider, falls back to
  the helm index exactly as before.

  Call with the root context: {{- include "portal.builderRegistrationStep" $ | nindent 4 }}
*/}}
{{- define "portal.builderRegistrationStep" -}}
- name: builderCds
  path: /apis/git.krateo.io/v1alpha1/namespaces/{{ .Release.Namespace }}/localresources
  verb: GET
  headers:
    - 'Accept: application/json'
  continueOnError: true
  errorKey: builderCdsError
  filter: >
    [ (.builderCds.items // [])[]
      | select({{ include "portal.builderIsRegistrationFile" . }})
      | { publish: ((.metadata.labels["krateo.io/publish"]) // ""),
          cd: ((.spec.fromResource.fromString) // "") } ]
{{- end -}}

{{/*
  portal.builderRegistrationDefs — the jq defs that decide what /marketplace/<name>/install
  pre-fills from a builder publish. Emits:
    cdField($k)       one `key: value` line of a registration file (spec.chart.url / .version),
                      trailing comment and quotes removed; "" when absent. jq has no YAML parser,
                      and the file is the fixed shape the builders emit — one indented line per
                      key — so a line match is exact. Indented only: the leading whitespace keeps
                      `apiVersion:` from reading as `version:`, and the header comments ("kubectl
                      apply -f https://…") never start with a key.
    indexEntry($bp)   the helm-index entry for $bp, blueprints index first, then operators — the
                      same two indexes the marketplace lists.
    builderFile($bp)  the registration file of claim "publish-<$bp>", whatever the indexes say;
                      "" when there is none.
    builderChart($bp) {url, version} from builderFile($bp) for a name no index carries; both ""
                      otherwise.

  AN INDEX NAME KEEPS ITS INDEX CHART. The same route is the official marketplace card, so a name
  either helm index carries never takes a builder pre-fill. Otherwise anyone who may create a
  BuilderPublish could publish a chart under an official name and re-point that card's Install at
  it, with the owner they typed, and the admin who clicks Install would see a plausible OCI url and
  no warning. What this does NOT close: the builder lists do not read the indexes, so a publish
  that shares an index name and carries a registration file is still offered Install, and lands
  on the index chart. The note above the form says so in that case (builderFile is how it knows),
  so nobody installs the index chart believing it is theirs; refusing the name when it is
  published, in the composer, is the fix.

  CHART_VERSION is not a version. A page set's file carries that placeholder because a release tag
  decides the version and the release stamps it into the published copy; the branch never learns
  it. Pre-filling the placeholder would install a version nothing published, so it becomes "" and
  the person types the tag. A blueprint's file carries Chart.yaml's literal version, which is
  exactly what its merge releases.

  ONE DEFINITION, shared by the install form (restaction.blueprint-install-formdef) and the
  change-request note above it (restaction.blueprint-install-origin): if they decided separately,
  the note could name a change request while the form pre-filled a different chart. Each RA needs
  the catalog, operators and builderCds steps. Emits defs only (each ends in `;`), for the head of
  a LITERAL (|) filter block.
*/}}
{{- define "portal.builderRegistrationDefs" -}}
def cdField($k):
  [ split("\n")[]
    | select(test("^\\s+" + $k + ":"))
    | sub("^\\s+" + $k + ":\\s*"; "") | sub("\\s+#.*$"; "") | sub("\\s+$"; "")
    | ltrimstr("\"") | rtrimstr("\"") | ltrimstr("'") | rtrimstr("'") ]
  | first // "";
def indexEntry($bp): (((.catalog.entries[$bp]?) // (.operators.entries[$bp]?) // [])[0]);
def builderFile($bp):
  if $bp == "" then ""
  else ([ ((.builderCds // []) | if type == "array" then . else [] end)[]
          | select(((.publish) // "") == ("publish-" + $bp)) | ((.cd) // "") ] | first // "") end;
def builderChart($bp):
  ((indexEntry($bp).urls[0]?) // "") as $indexUrl
  | (if $indexUrl != "" then "" else builderFile($bp) end) as $file
  | { url: ($file | cdField("url")),
      version: ($file | cdField("version") | if . == "CHART_VERSION" then "" else . end) };
{{- end -}}
