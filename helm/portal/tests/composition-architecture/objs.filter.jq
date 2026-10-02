[ (.objs // .) | select(type == "object" and (.metadata.name // "") != "")
  | { apiVersion: (.apiVersion // ""),
      metadata: { name: .metadata.name, namespace: (.metadata.namespace // ""), creationTimestamp: (.metadata.creationTimestamp // "") },
      status: (.status // {}) } ]
