{{/*
  duration.label — how every duration STATISTIC in the portal reads: a number of milliseconds in,
  one label out. Under one second it is whole milliseconds ("830 ms"); from one second up it is
  seconds with ONE decimal ("1.2 s"). No thousands separator, ever: "1,189 ms" was antd Statistic's
  English grouping, and an Italian reader takes that comma for a decimal point (Diego, 2026-11-13).

  ONE DEFINITION, carried BYTE-FOR-BYTE by compositions-page, observability-page and portal-agents.
  A `define` lives in the chart that declares it, and snowplow's shared jq modules cannot be shipped
  by a page chart (a custom module comes from ONE ConfigMap snowplow's own chart renders; a built-in
  one is embedded in the binary), so the copies are tested instead: test-duration-labels.py fails
  if they differ, and runs the table below through each chart's rendered copy. Change all three
  together.

  THE EDGES, decided once:
    - the input is rounded to the nearest millisecond FIRST, and that rounded value picks the unit:
      999.4 -> "999 ms", 999.6 -> "1.0 s". "1000 ms" can never appear.
    - seconds are rounded to the nearest tenth, halves away from zero: 1049 -> "1.0 s",
      1050 -> "1.1 s", 59950 -> "60.0 s".
    - no minutes: 61234 -> "61.2 s", 1234567 -> "1234.6 s". The label is built here as text, so
      nothing groups it.
    - a number or a numeric string is read; null, "", NaN, an infinity or anything else is "—"
      (ClickHouse writes NaN for an average over no rows, and jq reads "-nan" as NaN where gojq
      refuses it — so it is caught here, not left to the engine). A caller with its own no-data
      rule (a zero sample count) applies it before calling this.

  A STATISTIC TAKES THE WHOLE LABEL AS ITS VALUE, with no suffix. antd re-groups a value that looks
  like a number ("1234.6" renders "1,234.6"), and a value carrying its unit never looks like one.

  THE TRACE VIEW DOES NOT USE THIS. Its span durations (traceDuration in observability-page
  _traces.tpl, from nanoseconds) keep microseconds and two decimals on purpose.

  Emits a def (ends in `;`) on ONE line with no single quote, so it can sit at the head of a
  single-quoted `${ … }` expression or a RESTAction filter.
*/}}
{{- define "duration.label" -}}
def durationLabel: (if type == "number" then . elif type == "string" then (tonumber? // null) else null end) as $ms | if $ms == null or ($ms | isnan) or ($ms | isinfinite) then "—" else ($ms | round) as $r | if $r < 1000 then ($r | tostring) + " ms" else (($ms / 100) | round) as $d | (($d / 10) | floor | tostring) + "." + (($d % 10) | tostring) + " s" end end;
{{- end }}
