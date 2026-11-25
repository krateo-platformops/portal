{{/*
  portal.marketplaceInstalledDefs — whether a Marketplace chart is INSTALLED here, and as which
  CompositionDefinition. ONE rule for the catalog grid's tiles (restaction.blueprints-catalog) and the
  chart's detail page (marketplace-detail), so a tile and its page cannot disagree. They did: the tile
  joined by the chart's helm repo and the page by the CompositionDefinition's NAME, so acme-feedback
  (installed from oci://ghcr.io/krateo-blueprints/charts/acme-feedback) offered "Install" on its tile
  and "Configure" on its page (UX audit MKT-2), a chart installed under another name through the
  Install form's `name` was installed on the tile only, and a same-named chart from a foreign repo on
  the page only.

  CHART IDENTITY (Diego, 2026-11-25: "same name counts"). A CompositionDefinition is an install of
  catalog chart <name> when
    helm-repo  spec.chart.repo is <name> and spec.chart.url is the base of one of the entry's versions
               (a version's tarball URL with the file name dropped, or the live channel's base it
               carries as repoBase), trailing slashes ignored; or
    OCI        spec.chart.repo is empty, spec.chart.url is oci://…, and its last path segment, with a
               :tag or @digest dropped, is <name>.
  The CompositionDefinition's own name plays no part. With several, the first by namespace, then name.

  Input: the CompositionDefinitions projected as objects with name, ns, version, repo, url (more fields
  ride along). installedOf($name; $entries; $cds) returns the matching object, or null.
  $entries is the catalog entry's versions (the index's array for <name>), [] when it has none.
  Emits defs only, with no jq `#` comment, so it also works in a folded (`>`) filter.
*/}}
{{- define "portal.marketplaceInstalledDefs" -}}
def installedChartName: tostring | sub("@[^/]*$"; "") | sub("/+$"; "") | split("/") | last | sub(":[^:/]*$"; "");
def installedBases: [ .[]? | objects | (.repoBase // ((.urls[0]? // "") | tostring | sub("/[^/]+$"; ""))) | tostring | sub("/+$"; "") | select(. != "") ] | unique;
def installedOf($name; $entries; $cds):
  (($entries // []) | installedBases) as $bases
  | [ ($cds // [])[] | objects
      | select( (((.repo // "") != "") and (.repo == $name) and (((.url // "") | tostring | sub("/+$"; "")) as $u | any($bases[]; . == $u)))
                or (((.repo // "") == "") and ((.url // "") | tostring | startswith("oci://")) and (((.url // "") | installedChartName) == $name)) ) ]
  | sort_by((.ns // ""), (.name // "")) | (.[0] // null);
{{- end -}}
