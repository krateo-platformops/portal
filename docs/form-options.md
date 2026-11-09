---
type: Usage
title: portal — options sources for schema forms
description: How the portal wires a blueprint field's x-krateo-options to a RESTAction (blueprint-formdef / composition-editdef → optionRefs → GET refs), the shared form-options-namespaces source, and the CI-enforced rules for any RESTAction annotated krateo.io/form-options.
resource: oci://ghcr.io/krateo-platformops/charts/portal
tags: [portal, authoring, forms, restactions]
timestamp: 2026-10-27T00:00:00Z
---

# Options sources for schema forms

A blueprint field can load its options from a RESTAction:
`"x-krateo-options": { "ref": "zones", "dependsOn": { "field": "region", "param": "region" } }`.
A chart keeps writing this keyword in its own `values.schema.json`. This page is the portal's side.

**The frontend no longer reads the keyword.** Since 1.8.18 the Form reads `widgetData` lists
(`autocomplete`, `dependencies`), so the portal translates. A Form CR written by hand uses those
lists directly; see the frontend's
[form-schema-extensions](https://github.com/krateo-platformops/frontend/blob/main/ui/docs/form-schema-extensions.md).

**Wiring.** `blueprint-formdef` (create) and `composition-editdef` (edit) read the blueprint's
`values.schema.json` from core-provider's jsonschema ConfigMap and return two things. `optionRefs`
holds every `x-krateo-options.ref`, once, and `form.blueprint-create` and `form.composition-edit`
turn each into a GET `resourcesRefs` entry, so snowplow resolves the RESTAction's path and `allowed`
for the caller. `autocomplete` and `dependencies` hold one entry per sourced field, with its dotted
name. A bare `ref` names a RESTAction in the blueprint's own namespace; `<namespace>/<name>` names
any other; a `ref` that is not a Kubernetes name is dropped. A source under `allOf` or `if` has no
dotted name, so it gets no entry. The generated CRD has no `x-krateo-*` keys, so a create form built
from it (no jsonschema ConfigMap) has no options, and its `notice` says so.

The formdef also splices the identity fields (`__composition_name__`,
`__composition_namespace__`) into the schema's TOP-LEVEL `properties`, found by a scan that
skips strings and nested objects: a schema may put an `if: { properties: … }` before its own.

**Shared sources** in the release namespace:

| RESTAction | Answers |
| --- | --- |
| `form-options-namespaces` | the namespaces the caller can list |

**Rules** for any RESTAction annotated `krateo.io/form-options: "true"`, checked in CI by
`scripts/lint-form-options.py`: a value from `extras` never reaches a step path unless piped
through `@uri` (the lint evaluates each path with a hostile value), no step reads Secrets, and
the filter answers with a list. A worked blueprint with its sources:
[examples/form-options](../examples/form-options/README.md).
