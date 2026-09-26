[.compdefs.items[]? | select((.status.kind//"")!="" and (.status.apiVersion//"")!="" and (.status.resource//"")!="")
 | {name: .metadata.name, namespace: .metadata.namespace, kind: .status.kind, apiVersion: .status.apiVersion,
    plural: .status.resource, chartVersion: (.spec.chart.version // ""),
    projects: [(.spec.statusDataTemplate // [])[] | .forPath]}]
