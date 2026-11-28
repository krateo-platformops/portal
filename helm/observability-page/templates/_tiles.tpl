{{/* COPIED FROM helm/portal/templates/_tiles.tpl so this chart can include portal.tileDefs (a define lives in the chart that declares it). scripts/test-copied-helpers.py keeps it byte-identical. */}}
{{/*
  portal.tileDefs — a stat tile's colour and whether its border is highlighted, decided by the
  page's RESTAction so the Listy that draws the tile (rowVariant statistic, frontend >= 1.8.93:
  `color: { value: '{color}' }` with no map, `highlight: '{highlight}'`) is agnostic of what the
  tile counts (owner's decision, 2026-11-24). Colour marks an exception only: a zero is never red
  or orange, and nothing is highlighted when nothing is wrong.

  `tilePaint($alarm; $mark)` takes a tile (an object with `tone` and `value`) and returns it with
  `color` (a palette name, or "" for neutral ink) and `highlight` (a boolean) added; `tone` stays,
  so anything else reading it keeps working.
    - tone "unread", a read that failed: orange, highlighted, on every page alike.
    - a tone in $mark ({tone: colour}) is an exception by its very presence (a check that could not
      be made, a failed last run): that colour, highlighted.
    - a tone in $alarm ({tone: colour}) counts something wrong or waiting on someone (failed,
      NotReady, firing, denied, errors...): neutral and plain while its value is 0, that colour and
      highlighted above 0. A value that is not a number at all is a count that could not be made,
      so it is marked too; a "0" never is.
    - every other tone (a total, a healthy count, a figure that is not a count of anything wrong):
      neutral, plain. Green is not spent on a quiet page.

  ONE DEFINITION, copied byte-for-byte into each page chart whose strips use it (a define lives in
  the chart that declares it); scripts/test-copied-helpers.py fails if a copy drifts. Emits a def
  (ends in `;`) on ONE line with no single quote, so it can sit at the head of a RESTAction filter
  or of a single-quoted `${ … }` widgetDataTemplate expression.
*/}}
{{- define "portal.tileDefs" -}}
def tilePaint($alarm; $mark): ((.tone // "") | tostring) as $t | (.value | if type == "number" then . else (tostring | tonumber? // null) end) as $n | . + (if $t == "unread" then { color: "orange", highlight: true } elif ($mark | has($t)) then { color: $mark[$t], highlight: true } elif ($alarm | has($t)) and ($n == null or $n > 0) then { color: $alarm[$t], highlight: true } else { color: "", highlight: false } end);
{{- end }}
