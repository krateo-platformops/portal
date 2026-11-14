{{/*
portal.facets: the filters, facet counts and hrefs of a filtered overview page, as jq defs.

ONE HELPER, ONE COPY PER PAGE CHART. A `define` cannot cross charts and a page chart cannot ship a
snowplow jq module, so every chart that filters an overview carries this file byte-for-byte, and
scripts/test-facets.py fails when two copies differ (the _duration.tpl precedent). Change every copy
or none. It knows no domain: the page RA computes, once per row, a map `f` of that row's facet values
(`{source: "incidents"}`, `{kind: "Alert", repo: "org/x"}`), and these defs filter and count on it.

THE URL. One query param per facet, its values comma-joined (the Select `mode: multiple` convention):
`?kind=Alert,Prompt&repo=org/x`. Reserved, never a facet: projects (the shell's scope), ask, view,
group, q, range, tab, spotlight. A facet key must not be the name of one of the RA's stages or
errorKeys either: snowplow refuses extras that name one (#1116).

THE RULES (build plan §2.2, decisions Q1 and Q2 of 2026-11-13):
  - a facet's counts apply every OTHER active filter, never its own, so picking a value does not
    zero its siblings (standard faceted search);
  - whether a facet shows is decided over the page's BASE set (before any filter), so a facet does
    not vanish while someone filters with it (Q2): it shows with two or more distinct values, or
    while one of its values is active;
  - a value named in the URL that no row carries is listed, with count 0 and active, so it can be
    removed;
  - each facet lists its 12 largest values, plus any active value outside them;
  - "See all N →" opens the TABLE view filtered to that group: `?<facet>=<value>&view=table` (Q1).

PARAMS ARE PAIRS, NOT AN OBJECT. gojq (snowplow's engine) does not keep object key order, so an href
built from an object would order its query differently under jq and gojq. `fx_params` reads the
extras into `[[key, value], …]` in the order the page lists them, and every href keeps that order.

  fx_list                        a facet value as a list of strings ("" and null drop out).
  fx_sel($k)                     the values of ?<k>=, split on commas, deduplicated.
  fx_filters($keys)              {<key>: [values]} for each facet key.
  fx_pass($filters; $except)     a row (with `.f`) passes every active filter but $except's.
  fx_apply($filters)             the rows of an array that pass every active filter.
  fx_facet($base; $filters; $k; $label; $names)
                                 {key, label, distinct, show, active, values: [{value, label, count,
                                 active}]}; $names maps a value to its display label.
  fx_params($keys)               the extras as ordered pairs, "" for an absent key.
  fx_set($params; $k; $v)        the pairs with <k> set to $v, appended when absent ("" drops it
                                 from an href).
  fx_without($params; $k; $v)    the pairs with one value taken out of <k>.
  fx_href($path; $params)        $path plus the non-empty pairs as a query, @uri-encoded.
  fx_chips($path; $params; $facets)
                                 one removable chip per active value (its href is the URL without
                                 that value), then "Clear all", which keeps only `projects`; [] when
                                 no filter is active.
  fx_seeall($path; $params; $k; $v)
                                 the table view filtered to one group.
*/}}
{{- define "portal.facets" -}}
def fx_list:
  (if type == "array" then . elif . == null then [] else [.] end)
  | map(tostring) | map(select(. != ""));
def fx_sel($k):
  ((.[$k] // "") | tostring | split(",") | map(select(. != "")) | unique);
def fx_filters($keys):
  . as $in | reduce $keys[] as $k ({}; . + {($k): ($in | fx_sel($k))});
def fx_pass($filters; $except):
  (.f // {}) as $f
  | all($filters | to_entries[];
        .key == $except or (.value | length) == 0
        or (.value as $want | any(($f[.key] | fx_list)[]; . as $v | any($want[]; . == $v))));
def fx_apply($filters):
  map(select(fx_pass($filters; null)));
def fx_facet($base; $filters; $k; $label; $names):
  ($filters[$k] // []) as $active
  | ([ $base[] | (.f // {})[$k] | fx_list[] ] | unique) as $all
  | ([ $base[] | select(fx_pass($filters; $k)) | (.f // {})[$k] | fx_list[] ]
     | group_by(.) | map({value: .[0], count: length})) as $counted
  | def fx_count($v): ([ $counted[] | select(.value == $v) | .count ] | .[0]) // 0;
    ([ $all[] | {value: ., count: fx_count(.)} ] | sort_by(-.count, .value) | .[0:12]) as $top
  | ($top + [ $active[] | . as $v | select(any($top[]; .value == $v) | not) | {value: $v, count: fx_count($v)} ]) as $values
  | { key: $k, label: $label, distinct: ($all | length), active: $active,
      show: ((($all | length) >= 2) or (($active | length) > 0)),
      values: [ $values[] | . as $e
                | $e + { label: (($names[$e.value] // $e.value) | tostring),
                         active: any($active[]; . == $e.value) } ] };
def fx_params($keys):
  . as $in | [ $keys[] | [., (($in[.] // "") | tostring)] ];
def fx_set($params; $k; $v):
  if any($params[]; .[0] == $k) then [ $params[] | if .[0] == $k then [$k, $v] else . end ]
  else $params + [[$k, $v]] end;
def fx_without($params; $k; $v):
  fx_set($params; $k; ([ $params[] | select(.[0] == $k) | .[1] | split(",")[] | select(. != "" and . != $v) ] | join(",")));
def fx_href($path; $params):
  ([ $params[] | select(.[1] != "") | .[0] + "=" + (.[1] | @uri) ] | join("&")) as $q
  | if $q == "" then $path else $path + "?" + $q end;
def fx_chips($path; $params; $facets):
  [ $facets[] as $fc | $fc.values[] | select(.active)
    | {label: ($fc.label + ": " + .label), href: fx_href($path; fx_without($params; $fc.key; .value)), removable: true} ]
  | if length == 0 then [] else . + [ {label: "Clear all", href: fx_href($path; [ $params[] | select(.[0] == "projects") ])} ] end;
def fx_seeall($path; $params; $k; $v):
  fx_href($path; fx_set(fx_set($params; $k; $v); "view"; "table"));
{{- end -}}
