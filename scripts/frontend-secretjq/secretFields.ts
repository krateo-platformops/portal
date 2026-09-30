/**
 * Secret form fields — the ones a schema marks `format: password` or `writeOnly: true`.
 *
 * A value typed into such a field is a credential. It is rendered masked (Input.Password), never
 * redisplayed in cleartext (the review step shows a mask), never prefilled from anything the page
 * or the browser holds (schema defaults, initialValues, a refetch, a saved draft, an Autopilot
 * draft), never persisted to a local draft, never named to Autopilot, and never sent to
 * snowplow's `/jq` endpoint — see `resolveOverride` in `secretJq.ts` for how an action payload
 * that needs one is built without it leaving the browser except in the write itself.
 *
 * A field is addressed by its PATH in the form values: `['password']` for a top-level field,
 * `['spec', 'auth', 'token']` for one inside a nested object group, `'*'` for "every item" of an
 * array (`['users', '*', 'password']`, `['tokens', '*']`) and `'{*}'` for "every value" of a map
 * (`['creds', '{*}']` for `additionalProperties: { type: string, format: password }`).
 */
import type { JSONSchema4 } from 'json-schema'
import { cloneDeep, get, has, set, unset } from 'lodash'

/** The value the review step shows in place of a secret. */
export const SECRET_MASK = '••••••'

/** Every item of an array, as a path segment. */
export const ANY_ITEM = '*'

/** Every value of a map (`additionalProperties`), as a path segment. */
export const ANY_VALUE = '{*}'

/** A wildcard segment — it names no key an expression could spell. */
export const isWildcardSegment = (segment: string): boolean => segment === ANY_ITEM || segment === ANY_VALUE

export type SecretPath = readonly string[]

const isRecord = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === 'object' && !Array.isArray(value)

/** oneOf / anyOf / allOf branches of a node (object branches only). */
const combinatorBranches = (node: JSONSchema4): JSONSchema4[] =>
  [node.oneOf, node.anyOf, node.allOf].flatMap((list) => (Array.isArray(list) ? list.filter(isRecord) : []))

/** A string type: `'string'`, or a nullable `['string', 'null']`. */
const isStringType = (type: JSONSchema4['type']): boolean => {
  if (type === undefined || type === 'string') {
    return true
  }
  return Array.isArray(type) && type.includes('string') && type.every((each) => each === 'string' || each === 'null')
}

/**
 * Is this schema node a secret string? `writeOnly` is JSON-schema draft 7 (not in the draft-4
 * typings), `format: password` is the OpenAPI spelling; either one makes the field secret. Only a
 * string (untyped, `string` or nullable `['string','null']`) can be a password control — a
 * `writeOnly` object has no single value to mask. A scalar whose oneOf/anyOf/allOf branch is a
 * secret string is a secret too: the renderer does not render combinators, so the whole field
 * fails closed.
 */
export const isSecretSchemaNode = (node: JSONSchema4 | undefined): boolean => {
  if (!node || typeof node !== 'object') {
    return false
  }
  if (!isStringType(node.type)) {
    return false
  }
  if (node.format === 'password' || (node as { writeOnly?: unknown }).writeOnly === true) {
    return true
  }
  return !node.properties && combinatorBranches(node).some(isSecretSchemaNode)
}

/** An array whose items are secret strings (`items: { type: string, format: password }`). */
export const isSecretArrayNode = (node: JSONSchema4 | undefined): boolean =>
  !!node && node.type === 'array' && !!node.items && !Array.isArray(node.items) && isSecretSchemaNode(node.items)

/** Secret paths below one (non-secret) node at `path`: its properties, items, map values, combinators. */
const secretPathsBelow = (node: JSONSchema4, path: readonly string[]): SecretPath[] => {
  const out: SecretPath[] = []
  if (isRecord(node.properties)) {
    // eslint-disable-next-line @typescript-eslint/no-use-before-define
    out.push(...secretFieldPaths(node, path))
  }
  if (node.items && !Array.isArray(node.items)) {
    const { items } = node
    out.push(...(isSecretSchemaNode(items) ? [[...path, ANY_ITEM]] : secretPathsBelow(items, [...path, ANY_ITEM])))
  }
  if (isRecord(node.additionalProperties)) {
    const values = node.additionalProperties
    out.push(...(isSecretSchemaNode(values) ? [[...path, ANY_VALUE]] : secretPathsBelow(values, [...path, ANY_VALUE])))
  }
  // Combinator branches describe the SAME value: walk each at the same path.
  for (const branch of combinatorBranches(node)) {
    out.push(...(isSecretSchemaNode(branch) ? [path] : secretPathsBelow(branch, path)))
  }
  return out
}

const pathKey = (path: SecretPath): string => JSON.stringify(path)

/** Every secret field in a form schema, as paths into the form values (deduplicated). */
export const secretFieldPaths = (schema: JSONSchema4 | undefined, prefix: readonly string[] = []): SecretPath[] => {
  if (!schema || typeof schema !== 'object') {
    return []
  }
  const out: SecretPath[] = []
  if (isRecord(schema.properties)) {
    for (const [key, node] of Object.entries(schema.properties)) {
      const path = [...prefix, key]
      if (isSecretSchemaNode(node)) {
        out.push(path)
      } else if (isRecord(node)) {
        out.push(...secretPathsBelow(node, path))
      }
    }
  }
  // the root's own combinators (a schema composed with allOf)
  if (prefix.length === 0) {
    for (const branch of combinatorBranches(schema)) {
      out.push(...secretFieldPaths(branch, prefix))
    }
  }
  const seen = new Set<string>()
  return out.filter((path) => {
    const key = pathKey(path)
    if (seen.has(key)) {
      return false
    }
    seen.add(key)
    return true
  })
}

/**
 * A copy of `value` with the thing at each path replaced by `replace(existing)` — or removed when
 * `replace` returns undefined. Never mutates the input; objects off the paths are shared.
 */
const rewritePath = (value: unknown, path: readonly string[], replace: (existing: unknown) => unknown): unknown => {
  if (path.length === 0) {
    return value
  }
  const [head, ...rest] = path
  // One element under a wildcard: recurse, or — when the wildcard is the leaf — replace it.
  const each = (element: unknown): unknown => (rest.length === 0 ? replace(element) : rewritePath(element, rest, replace))
  if (head === ANY_ITEM) {
    if (Array.isArray(value)) {
      return value.map(each).filter((element) => element !== undefined)
    }
    // An object list's item. A fan-out action (`fanOutPath`) submits ONE element in place of the
    // whole list, so a lone object here is treated as an item too — failing safe: the secret in
    // it is removed/masked rather than missed.
    if (rest.length === 0) {
      return value === undefined ? value : replace(value)
    }
    return isRecord(value) ? rewritePath(value, rest, replace) : value
  }
  if (head === ANY_VALUE) {
    if (!isRecord(value)) {
      return value
    }
    return Object.fromEntries(Object.entries(value)
      .map(([key, element]) => [key, each(element)] as const)
      .filter(([, element]) => element !== undefined))
  }
  if (!isRecord(value) || !Object.prototype.hasOwnProperty.call(value, head)) {
    return value
  }
  const next = rest.length === 0 ? replace(value[head]) : rewritePath(value[head], rest, replace)
  if (next === undefined) {
    const { [head]: _removed, ...without } = value
    return without
  }
  return { ...value, [head]: next }
}

/** `values` without any secret field. */
export const omitSecretPaths = <T>(values: T, paths: readonly SecretPath[]): T =>
  paths.reduce<unknown>((acc, path) => rewritePath(acc, path, () => undefined), values) as T

/** `values` with every non-empty secret field shown as the mask. */
export const maskSecretPaths = <T>(values: T, paths: readonly SecretPath[]): T =>
  paths.reduce<unknown>((acc, path) => rewritePath(acc, path, (existing) =>
    (existing === undefined || existing === null || existing === '' ? existing : SECRET_MASK)), values) as T

/** Every leaf of a value as the mask — an object keeps its keys (so the human sees WHICH are set). */
const maskLeaves = (value: unknown): unknown => {
  if (Array.isArray(value)) {
    return value.map(maskLeaves)
  }
  if (isRecord(value)) {
    return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, maskLeaves(entry)]))
  }
  return value === undefined || value === null || value === '' ? value : SECRET_MASK
}

const maskSecretObjects = (value: unknown): unknown => {
  if (Array.isArray(value)) {
    return value.map(maskSecretObjects)
  }
  if (!isRecord(value)) {
    return value
  }
  let out: Record<string, unknown> = value
  if (value.kind === 'Secret') {
    out = { ...value }
    if ('data' in out) { out.data = maskLeaves(out.data) }
    if ('stringData' in out) { out.stringData = maskLeaves(out.stringData) }
  }
  if (Array.isArray(out.items)) {
    out = { ...out, items: out.items.map(maskSecretObjects) }
  }
  return out
}

/**
 * A write body as the confirm dialog may SHOW it: a Secret's `data`/`stringData` values masked
 * (their keys kept), and every value at `maskTargets` — the lodash paths of payloadToOverride
 * entries resolved from a secret form field — masked too. The body actually sent is untouched.
 */
export const maskSecretMaterialForDisplay = (value: unknown, maskTargets: readonly string[] = []): unknown => {
  let out = value
  if (maskTargets.length > 0 && isRecord(value)) {
    const copy = cloneDeep(value)
    for (const target of maskTargets) {
      if (has(copy, target)) {
        set(copy, target, maskLeaves(get(copy, target)))
      }
    }
    out = copy
  }
  return maskSecretObjects(out)
}

/**
 * A Kubernetes object that may be (or list) a Secret, without its secret material. Applied to
 * every response and request body before it is handed to `/jq` for a success / error / navigate
 * template: those templates are for `metadata.name` and friends, never for `data`.
 */
export const stripSecretMaterial = (value: unknown): unknown => {
  if (Array.isArray(value)) {
    return value.map(stripSecretMaterial)
  }
  if (!isRecord(value)) {
    return value
  }
  let out: Record<string, unknown> = value
  if (value.kind === 'Secret') {
    const { data: _data, stringData: _stringData, ...rest } = value
    out = rest
  }
  if (Array.isArray(out.items)) {
    out = { ...out, items: out.items.map(stripSecretMaterial) }
  }
  return out
}

/** Every leaf string/number under a value (a secret subtree's plain values). */
const leafScalars = (value: unknown): string[] => {
  if (Array.isArray(value)) {
    return value.flatMap(leafScalars)
  }
  if (isRecord(value)) {
    return Object.values(value).flatMap(leafScalars)
  }
  if (typeof value === 'string' || typeof value === 'number') {
    return [String(value)]
  }
  return []
}

const base64Of = (text: string): string | undefined => {
  try {
    return btoa(String.fromCharCode(...new TextEncoder().encode(text)))
  } catch {
    return undefined
  }
}

/**
 * The secret VALUES a form holds — each as typed and base64-encoded (how a Secret's `data` and an
 * apiserver error quoting it would carry it). Used to scrub anything else that could echo them:
 * an error message, a response body, an audit outcome.
 */
export const secretValuesOf = (values: unknown, paths: readonly SecretPath[]): string[] => {
  const found: string[] = []
  for (const path of paths) {
    rewritePath(values, path, (existing) => {
      found.push(...leafScalars(existing))
      return existing
    })
  }
  // as typed, base64 (a Secret's `data`), and JSON-escaped (an apiserver message quoting it: `ab\"cd`)
  const all = found.filter((value) => value !== '').flatMap((value) => [value, base64Of(value), JSON.stringify(value).slice(1, -1)])
  return [...new Set(all.filter((value): value is string => !!value))]
}

/** A secret shorter than this is redacted only where a string IS it, or quotes it as a whole token. */
const MIN_SUBSTRING_SECRET = 4

/**
 * `value` with every occurrence of a secret value replaced by the mask — deep, in every string
 * and object key. The catch-all for text the page does not control (an apiserver message
 * quoting "Invalid value: \"…\"", an echoed spec).
 */
export const redactSecretValues = <T>(value: T, secrets: readonly string[]): T => {
  if (secrets.length === 0) {
    return value
  }
  const longest = [...secrets].sort((left, right) => right.length - left.length)
  const scrub = (text: string): string => {
    if (longest.includes(text)) {
      return SECRET_MASK
    }
    return longest.reduce((acc, secret) => (secret.length >= MIN_SUBSTRING_SECRET
      ? acc.split(secret).join(SECRET_MASK)
      // too short to replace inside words — but a whole QUOTED token (`Invalid value: "x9!"`) is it
      : acc.split(`"${secret}"`).join(`"${SECRET_MASK}"`).split(`'${secret}'`)
        .join(`'${SECRET_MASK}'`)), text)
  }
  const walk = (node: unknown): unknown => {
    if (typeof node === 'string') {
      return scrub(node)
    }
    if (Array.isArray(node)) {
      return node.map(walk)
    }
    if (isRecord(node)) {
      return Object.fromEntries(Object.entries(node).map(([key, entry]) => [scrub(key), walk(entry)]))
    }
    return node
  }
  return walk(value) as T
}

/**
 * A write RESPONSE as a success / error / navigate template may see it when the form held a
 * secret: only what names the object and says how it went — `apiVersion`, `kind`, `metadata`
 * (without annotations / managedFields), `status`, and a Status' `reason` / `code`. Never `spec`
 * (an echo of the secret), never a Status `message` / `details` (which quote invalid values).
 * `secretTargets` are removed too, and any secret value left anywhere is masked.
 */
export const templateSafeResponse = (
  response: unknown,
  secretTargets: readonly string[],
  secrets: readonly string[],
): unknown => {
  if (!isRecord(response)) {
    return response
  }
  const { annotations: _annotations, managedFields: _managedFields, ...metadata } = isRecord(response.metadata) ? response.metadata : {}
  const picked: Record<string, unknown> = {}
  for (const key of ['apiVersion', 'kind', 'status', 'reason', 'code'] as const) {
    if (key in response) {
      picked[key] = cloneDeep(response[key])
    }
  }
  if (isRecord(response.metadata)) {
    picked.metadata = cloneDeep(metadata)
  }
  for (const target of secretTargets) {
    unset(picked, target)
  }
  return redactSecretValues(stripSecretMaterial(picked), secrets)
}
