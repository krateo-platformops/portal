# blueprints-catalog jq fixtures

Self-contained jq fixtures for the `blueprints-catalog` RESTAction filter that backs the
marketplace **head-stat count** and the **sort control**.

`filter.jq` is the exact jq program that ships (extracted from the helm-rendered
`restaction.blueprints-catalog.yaml`). The `input.*.json` files simulate snowplow's RA
input: each named api-step output (`catalog`, `operators`, `compdefs`, and `catalogLive` in the
`live-index` case) at the top level,
plus the request extras (`sort`, `source`, `category`, `q`, `spotlight`). The catalog and
operators inputs are the shape snowplow produces after its YAML→JSON conversion of the two
helm-repo `index.yaml` files.

The tiny fixture catalog = 3 blueprints + 2 operators with distinct `created` dates and
keywords, so name-order and recent-order are unambiguous.

## Run

```sh
for c in default sort-name sort-recent source-operator \
         source-blueprint.sort-recent category-aws sort-invalid live-index.sort-recent; do
  diff <(jq -f filter.jq "input.$c.json" | jq '{total,count,countLabel,selectedSort,selectedSource,selectedCategory,cardOrder:[.cards[].name],sorts:[.sorts[]|{key,active,route}],sources:[.sources[]|{key,active,route}]}') \
       "expected.$c.json" && echo "$c OK" || echo "$c FAIL"
done
```

## What each case proves

| case | proves |
|------|--------|
| `default` / `sort-name` | head-stat `count`/`total` + `countLabel:charts`; grid sorted name A→Z |
| `sort-recent` | grid sorted by `created` DESC (most recently updated first) |
| `source-operator` | `count` reflects the filter (2), `countLabel:operators`, `total` stays 5 |
| `source-blueprint.sort-recent` | sort composes with the source facet (count 3, blueprints, recent order) |
| `category-aws` | `count` reflects the category facet; sort routes preserve `category=aws` |
| `sort-invalid` (`?sort=popular`) | an unsupported sort falls back to `name` (never fabricated) |
| `live-index.sort-recent` | the live index adds a chart a blueprint repository released (`krateo.io/source-repo`) under a name the catalog lacks, installing from the channel base (`repoUrl`); a live copy of a curated name leaves the curated card alone; a platform chart with no source-repo stays out |

## Sorts offered vs omitted

Offered (data-backed): **name** (every entry has a `name`) and **recent** (every entry has
a `created` RFC3339 timestamp — verified 100% coverage on both live indexes, 311 blueprints +
86 operators). Omitted: popularity / downloads / rating / install-count — the helm index
carries **no** such field, so those sorts would be fabricated and are not offered.
