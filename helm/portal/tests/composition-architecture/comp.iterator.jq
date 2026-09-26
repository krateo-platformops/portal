[ .arch.graph.composition? | select(. != null and (.apiVersion // "") != "" and (.resource // "") != "" and (.name // "") != "") ]
