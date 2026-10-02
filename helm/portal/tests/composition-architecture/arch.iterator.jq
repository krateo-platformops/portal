[ { namespace: ((.compositionNamespace // .namespace) // ""), name: ((.compositionName // .name) // "") } | select(.namespace != "" and .name != "") ]
