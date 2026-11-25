{{/*
  portal.incidentOrdinalDefs — an incident's place among its alert's incidents ("24th from this
  alert"), ONE RULE for every page that numbers incidents: the incident page's alert chip
  (incident-detail ordinalLabel), the /incidents cards (incidents-list ordinal) and the alert page's
  incidents table. Two pages that number the same incident differently would make the number worse
  than none. The source is here; each page chart that numbers incidents carries a byte-for-byte copy
  under a COPIED FROM header (a `define` lives in the chart that declares it), which
  scripts/test-copied-helpers.py keeps identical. Change this file, then the copies.

  THE RULE. An incident belongs to the alert its observability.krateo.io/alert label names (alert-
  provider sets it), else its spec.alertRef.name. Its time is spec.triggeredAt, else its
  metadata.creationTimestamp. Among the incidents of one alert, in order of time and then of name,
  the first is 1. Only earlier incidents count, so an incident keeps its number as later ones open.
  An incident with no alert has no place.

    incidentAlert   an Incident -> the name of its alert, "" when it names none
    incidentWhen    an Incident -> its time, as the ISO string the object carries ("" when neither)
    incidentPlaces  a list of Incidents (one namespace, as a list call returns them) -> an object
                    from each incident name to its place, 1 for the first; no key for an incident
                    with no alert
    nth             a positive integer -> its English ordinal: 1 "1st", 2 "2nd", 11 "11th", 23 "23rd"

  Defs only, each ending in `;`, with no single quote and no `#`, so it can open a jq program in a
  folded or a literal block.
*/}}
{{- define "portal.incidentOrdinalDefs" -}}
def incidentAlert: ((.metadata.labels // {})["observability.krateo.io/alert"] // .spec.alertRef.name // "");
def incidentWhen: (.spec.triggeredAt // .metadata.creationTimestamp // "");
def incidentPlaces: [ .[] | select(incidentAlert != "") ] | group_by(incidentAlert)
  | map(sort_by([incidentWhen, (.metadata.name // "")]) | to_entries | map({ key: (.value.metadata.name // ""), value: (.key + 1) }))
  | (add // []) | from_entries;
def nth: (. % 100) as $h | (. % 10) as $u
  | tostring + (if $h >= 11 and $h <= 13 then "th" elif $u == 1 then "st" elif $u == 2 then "nd" elif $u == 3 then "rd" else "th" end);
{{- end -}}
