[.crds.items[]?
 | select(.spec.group as $g | ["composition.krateo.io","widgets.templates.krateo.io","templates.krateo.io","core.krateo.io"] | index($g) | not)
 | ((([.spec.versions[] | select(.served and .storage)])[0]) // (([.spec.versions[] | select(.served)])[0])) as $v
 | select($v != null)
 | {group: .spec.group, kind: .spec.names.kind, plural: .spec.names.plural, version: $v.name, scope: .spec.scope,
    owner: (.metadata.annotations["krateo.io/owned-by-restdefinition"] // .metadata.labels["krateo.io/composition-name"] // null),
    statusFields: (($v.schema.openAPIV3Schema.properties.status.properties // {}) | keys),
    conditions: ($v.schema.openAPIV3Schema.properties.status.properties.conditions != null)}]
