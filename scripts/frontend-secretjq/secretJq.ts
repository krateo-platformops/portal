/**
 * Resolving a `payloadToOverride` expression WITHOUT sending a secret field to snowplow's `/jq`.
 *
 * Every `${…}` override used to be resolved by POSTing `{ data: { json: <ALL form values> } }` to
 * `/jq` — so a password typed into a form left the browser once per override (even for overrides
 * that never read it), and a gojq error logged by snowplow could quote it. Now, for a form with
 * secret fields (`format: password` / `writeOnly: true`, see `secretFields.ts`):
 *
 * - an expression in the local grammar below is resolved here, in the browser — nothing is sent;
 * - any other expression that reaches a secret BY NAME — the field or any ancestor of it
 *   (`.json.spec` when `spec.password` is secret) — is REFUSED;
 * - any other expression that could reach one WITHOUT naming it — the whole `.json`, the bare
 *   input `.`, `..`, a computed `[…]` index, `."json"`, getpath/paths/tostream, string
 *   interpolation — is REFUSED too;
 * - what remains goes to `/jq`, with every secret field removed from the data.
 * Refusals happen before any request is made and name the field. Failing closed is the point: the
 * alternative is either leaking the value or silently writing null where it belonged.
 *
 * A form with no secret fields is untouched: the same `/jq` call with the same data as before.
 *
 * LOCAL GRAMMAR (the whole expression; whitespace is allowed inside `${ }`, around the braces,
 * colons and commas of an object, but not inside a path):
 *
 *   expression := "${" body "}"
 *   body       := path | object
 *   path       := ".json" ( "." ident )+
 *   object     := "{" [ entry ( "," entry )* ] "}"
 *   entry      := key ":" path
 *   key        := ident | '"' <chars other than " and \> '"' | "(" path ")"
 *   ident      := [A-Za-z_][A-Za-z0-9_]*
 *
 * Evaluation follows jq: a missing key (or a key under null) is `null`; indexing a string, number,
 * boolean or array with a name is an error (and so a refusal). A computed key `(.json.<field>)`
 * must resolve to a string (jq: "object keys must be strings") and must NOT be (or contain) a
 * secret field — a secret may only ever be a value. Lookups read OWN properties only
 * (`.json.constructor` is null, as in jq). Nothing else — no pipes, no operators, no string
 * concatenation, no optional `?`, no brackets — is accepted.
 */
import type { SecretPath } from './secretFields'
import { ANY_ITEM, ANY_VALUE, isWildcardSegment, omitSecretPaths } from './secretFields'

/** An object key: written literally, or computed from a (non-secret) form field. */
export type LocalKey = { literal: string } | { path: string[] }

export type LocalExpression =
  | { kind: 'path'; path: string[] }
  | { kind: 'object'; entries: Array<[LocalKey, string[]]> }

const IDENT = '[A-Za-z_][A-Za-z0-9_]*'
const PATH_SOURCE = `\\.json(?:\\.${IDENT})+`
const PATH_RE = new RegExp(`^${PATH_SOURCE}$`)
const ENTRY_RE = new RegExp(`^(?:(${IDENT})|"([^"\\\\]*)"|\\(\\s*(${PATH_SOURCE})\\s*\\))\\s*:\\s*(${PATH_SOURCE})$`)

/** `.json.spec.password` → `['spec', 'password']`, relative to the form values. */
const pathSegments = (source: string): string[] => source.split('.').slice(2)

/** The `${ … }` body, trimmed; null when the value is not a whole `${…}` expression. */
const expressionBody = (expression: string): string | null => {
  const trimmed = expression.trim()
  if (!trimmed.startsWith('${') || !trimmed.endsWith('}')) {
    return null
  }
  return trimmed.slice(2, -1).trim()
}

/**
 * Parse an expression in the local grammar. Null for anything else — the caller refuses it when
 * it touches a secret, and sends it to `/jq` (secret-free) when it does not.
 */
export const parseLocalExpression = (expression: string): LocalExpression | null => {
  const body = expressionBody(expression)
  if (body === null) {
    return null
  }
  if (PATH_RE.test(body)) {
    return { kind: 'path', path: pathSegments(body) }
  }
  if (!body.startsWith('{') || !body.endsWith('}')) {
    return null
  }
  const inner = body.slice(1, -1).trim()
  if (inner === '') {
    return { entries: [], kind: 'object' }
  }
  // Split on commas. Safe: a key may be quoted, but the grammar forbids a comma-bearing key from
  // parsing as an entry anyway (the halves fail ENTRY_RE), so a naive split never mis-accepts.
  const entries: Array<[LocalKey, string[]]> = []
  for (const part of inner.split(',')) {
    const match = ENTRY_RE.exec(part.trim())
    if (!match) {
      return null
    }
    const [, ident, quoted, keyPath, valuePath] = match
    const key: LocalKey = keyPath ? { path: pathSegments(keyPath) } : { literal: ident ?? quoted }
    entries.push([key, pathSegments(valuePath)])
  }
  return { entries, kind: 'object' }
}

/** Raised when a path indexes a non-object — jq would error, so the expression is refused. */
class LocalEvaluationError extends Error {}

const lookup = (values: Record<string, unknown>, path: readonly string[]): unknown => {
  let current: unknown = values
  for (const segment of path) {
    if (current === null || current === undefined) {
      return null
    }
    if (typeof current !== 'object' || Array.isArray(current)) {
      throw new LocalEvaluationError(`cannot index ${Array.isArray(current) ? 'array' : typeof current} with "${segment}"`)
    }
    // OWN properties only: `.json.constructor` / `.json.__proto__` are null in jq, as here.
    if (!Object.prototype.hasOwnProperty.call(current, segment)) {
      return null
    }
    current = (current as Record<string, unknown>)[segment]
  }
  return current === undefined ? null : current
}

/** Evaluate a parsed local expression against the form values (the `.json` of the jq input). */
export const evaluateLocalExpression = (expression: LocalExpression, values: Record<string, unknown>): unknown => {
  if (expression.kind === 'path') {
    return lookup(values, expression.path)
  }
  return Object.fromEntries(expression.entries.map(([key, path]) => {
    if ('literal' in key) {
      return [key.literal, lookup(values, path)]
    }
    const computed = lookup(values, key.path)
    if (typeof computed !== 'string') {
      // jq: `{(null): 1}` → "object keys must be strings"
      throw new LocalEvaluationError(`object keys must be strings (.json.${key.path.join('.')} is ${computed === null ? 'null' : typeof computed})`)
    }
    return [computed, lookup(values, path)]
  }))
}

/**
 * Do a value path and a secret path overlap — is one a prefix of the other? A wildcard segment of
 * the secret path matches any segment. `.json.spec` overlaps `spec.password` (it CONTAINS it);
 * `.json.spec.password` overlaps it (it IS it); `.json.spec.size` does not.
 */
export const pathsOverlap = (path: readonly string[], secretPath: SecretPath): boolean => {
  const length = Math.min(path.length, secretPath.length)
  for (let index = 0; index < length; index += 1) {
    const segment = secretPath[index]
    // `*` is an array item: it matches an INDEX only, never a named key (`.json.users.name` is not
    // `users[].password` — a fan-out rewrites its row paths first, see runRestFanOut). `{*}` is any
    // map key.
    const matches = segment === ANY_ITEM ? /^\d+$/.test(path[index]) : segment === ANY_VALUE || segment === path[index]
    if (!matches) {
      return false
    }
  }
  return true
}

/** Every form path a parsed local expression reads: values, and computed keys (separately). */
const readPaths = (expression: LocalExpression): { keys: string[][]; values: string[][] } => {
  if (expression.kind === 'path') {
    return { keys: [], values: [expression.path] }
  }
  return {
    keys: expression.entries.flatMap(([key]) => ('path' in key ? [key.path] : [])),
    values: expression.entries.map(([, path]) => path),
  }
}

const escapeRegExp = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** The field a secret path names, for messages: `spec.auth.token`, `users[].password`, `creds.*`. */
export const secretFieldLabel = (path: SecretPath): string =>
  path.map((segment) => {
    if (segment === ANY_ITEM) {
      return '[]'
    }
    return segment === ANY_VALUE ? '*' : segment
  }).join('.').replace(/\.\[\]/g, '[]')

/** Does the expression index a field by this NAME — `.name`, `."name"` or `["name"]`? */
const indexesName = (expression: string, name: string): boolean => {
  const escaped = escapeRegExp(name)
  return new RegExp(`(?:\\.\\s*${escaped}(?![A-Za-z0-9_])|\\.\\s*"${escaped}"|\\[\\s*"${escaped}"\\s*\\])`).test(expression)
}

/**
 * Which secret fields an expression reaches BY NAME. A secret is reached when the expression
 * indexes ANY named segment of its path — the field itself (`.password`) or any ancestor
 * (`.spec`, `.users`), since reading an ancestor reads the secret inside it. Matched as jq indexes
 * a name — `.x`, `."x"`, `["x"]` — so a string literal that merely contains the word (`"-password"`,
 * `key: "password"`) is not a reference. Over-inclusive on the name alone, on purpose: such an
 * expression is then either evaluated locally (correctly) or refused — never silently stripped.
 */
export const referencedSecretFields = (expression: string, secretPaths: readonly SecretPath[]): SecretPath[] =>
  secretPaths.filter((path) => path.some((segment) => !isWildcardSegment(segment) && indexesName(expression, segment)))

/**
 * Does the expression read the WHOLE form — `.json` not followed by a field name (`.json | keys`,
 * `.json | tojson`, `.json[]`, `{ spec: .json }`)? With secret fields present that is refused:
 * `/jq` would get the form without them and the result would silently differ from what the
 * author wrote.
 */
export const readsWholeForm = (expression: string): boolean =>
  /\.json(?![A-Za-z0-9_])(?!\s*\.\s*[A-Za-z_"])(?!\s*\[\s*")/.test(expression)

/** jq words after which `[` opens an array LITERAL, not an index. */
const JQ_KEYWORDS = new Set(['and', 'as', 'catch', 'def', 'elif', 'else', 'end', 'foreach', 'if', 'import', 'include', 'label', 'not', 'or', 'reduce', 'then', 'try'])

/** An index `[…]` whose content is a literal: `[]`, `[0]`, `[1:2]`, `[:3]`, `["name"]` (blanked). */
const LITERAL_INDEX = /^\s*(?:""|-?\d+|-?\d*\s*:\s*-?\d*)?\s*$/

/** Is the `[` at `index` of `code` an index applied to what precedes it (vs. an array literal)? */
const isPostfixBracket = (code: string, index: number): boolean => {
  let cursor = index - 1
  while (cursor >= 0 && /\s/.test(code[cursor])) {
    cursor -= 1
  }
  if (cursor < 0) {
    return false
  }
  const previous = code[cursor]
  if (/[)\]"?.]/.test(previous)) {
    return true
  }
  if (!/[A-Za-z0-9_]/.test(previous)) {
    return false
  }
  let start = cursor
  while (start > 0 && /[A-Za-z0-9_]/.test(code[start - 1])) {
    start -= 1
  }
  return !JQ_KEYWORDS.has(code.slice(start, cursor + 1))
}

/**
 * Ways to reach a form value WITHOUT naming it, so that the name check above cannot see it. With
 * secret fields present each is refused: `/jq` holds the form without its secrets, so the result
 * would silently lack the credential. Returns why, or null.
 */
export const opaqueAccess = (expression: string): string | null => {
  if (expression.includes('\\(')) {
    return 'uses string interpolation'
  }
  if (/\.\s*"json"|\[\s*"json"\s*\]/.test(expression)) {
    return 'reaches the form by a quoted name'
  }
  // `."password"` / `["password"]`: an escaped quoted key spells a name the name check
  // cannot read.
  if (/(?:\.|\[)\s*"(?:[^"\\]|\\.)*\\(?:[^"\\]|\\.)*"/.test(expression)) {
    return 'uses an escaped quoted key'
  }
  // String literals blanked: what is left is code.
  const code = expression.replace(/"(?:[^"\\]|\\.)*"/g, '""')
  if (/\.\./.test(code)) {
    return 'uses recursive descent (..)'
  }
  if (/(?:^|[^A-Za-z0-9_$])(?:getpath|paths|leaf_paths|tostream|pick|\$__loc__)(?![A-Za-z0-9_])/.test(code)) {
    return 'uses a path-reflective builtin'
  }
  // A bare `.` (the root, or whatever is piped in) — `.json` and `.name` are fine, `. | …`, `.[]`,
  // `.[0]` are not.
  const body = code.trim().replace(/^\$\{/, '').replace(/\}$/, '')
  if (/(?:^|[^A-Za-z0-9_)\]"?.])\.(?![A-Za-z_"])/.test(body)) {
    return 'uses the bare input (.)'
  }
  for (let index = code.indexOf('['); index !== -1; index = code.indexOf('[', index + 1)) {
    if (isPostfixBracket(code, index)) {
      const close = code.indexOf(']', index)
      if (close === -1 || !LITERAL_INDEX.test(code.slice(index + 1, close))) {
        return 'uses a computed index ([…])'
      }
    }
  }
  return null
}

/** An override that cannot be resolved without sending a secret field to `/jq`. */
export class SecretExpressionError extends Error {
  readonly fields: string[]

  readonly override: string

  constructor(override: string, fields: string[], reason: string) {
    super(
      `The action value "${override}" ${reason} the secret field${fields.length === 1 ? '' : 's'} `
      + `${fields.map((field) => `"${field}"`).join(', ')}. Secret fields are never sent to the server for evaluation — `
      + 'reference them only as a plain path (.json.<field>) or an object of plain paths '
      + '({ key: .json.<field>, … }).',
    )
    this.name = 'SecretExpressionError'
    this.fields = fields
    this.override = override
  }
}

export type OverridePlan =
  | { mode: 'jq'; data: Record<string, unknown> }
  /** `touchesSecret`: the value holds (or is) secret material — mask it wherever it is shown. */
  | { mode: 'local'; value: unknown; touchesSecret: boolean }

/**
 * Decide how one `${…}` override is resolved — synchronously, so a refusal happens before ANY
 * override is sent anywhere. Throws SecretExpressionError for a refusal.
 */
export const planOverride = (
  override: string,
  expression: string,
  values: Record<string, unknown>,
  secretPaths: readonly SecretPath[],
): OverridePlan => {
  if (secretPaths.length === 0) {
    return { data: { json: values }, mode: 'jq' }
  }
  const allLabels = secretPaths.map(secretFieldLabel)
  const referenced = referencedSecretFields(expression, secretPaths)
  const parsed = parseLocalExpression(expression)

  if (!parsed) {
    if (referenced.length > 0) {
      throw new SecretExpressionError(override, referenced.map(secretFieldLabel), 'reaches, in an expression that can only be evaluated server-side,')
    }
    if (readsWholeForm(expression)) {
      throw new SecretExpressionError(override, allLabels, 'reads the whole form (.json), which includes')
    }
    const opaque = opaqueAccess(expression)
    if (opaque) {
      throw new SecretExpressionError(override, allLabels, `${opaque}, which could reach`)
    }
    return { data: { json: omitSecretPaths(values, secretPaths) }, mode: 'jq' }
  }

  // In the local grammar: evaluated HERE, so nothing is sent at all.
  const { keys, values: valuePaths } = readPaths(parsed)
  // A key is shown wherever the object goes (the confirm dialog, logs, the object's own key list),
  // so a secret may be a VALUE but never a computed KEY.
  const secretKeys = secretPaths.filter((secret) => keys.some((key) => pathsOverlap(key, secret)))
  if (secretKeys.length > 0) {
    throw new SecretExpressionError(override, secretKeys.map(secretFieldLabel), 'uses, as an object key,')
  }
  // Evaluated while planning, so a local evaluation error (which jq would also raise) is a refusal
  // before any override has been sent anywhere.
  try {
    return {
      mode: 'local',
      touchesSecret: valuePaths.some((path) => secretPaths.some((secret) => pathsOverlap(path, secret))),
      value: evaluateLocalExpression(parsed, values),
    }
  } catch (error) {
    throw new SecretExpressionError(
      override,
      referenced.length > 0 ? referenced.map(secretFieldLabel) : allLabels,
      `could not be evaluated locally (${error instanceof Error ? error.message : String(error)}) and may reach`,
    )
  }
}

/** Run a plan: the local value, or the `/jq` round-trip with the secret-free data. */
export const runOverridePlan = (
  expression: string,
  plan: OverridePlan,
  resolveJq: (expression: string, values: Record<string, unknown>) => Promise<unknown>,
): Promise<unknown> => (plan.mode === 'jq' ? resolveJq(expression, plan.data) : Promise.resolve(plan.value))
