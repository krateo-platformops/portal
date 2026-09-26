[ (.comp // .) | select(type == "object" and (.metadata.name // "") != "")
  | { uid: (.metadata.uid // ""),
      managed: [ .status.managed[]? | {apiVersion: (.apiVersion // ""), name: (.name // ""), path: (.path // "")} ],
      conditions: (.status.conditions // []) } ]
