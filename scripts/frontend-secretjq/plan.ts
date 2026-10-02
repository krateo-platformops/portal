/**
 * plan.ts — runs the frontend's OWN secret-field rules over a resolved Form, so the chart's tests
 * judge a Form's payloadToOverride the way the browser will (krateo-platformops/frontend#424).
 *
 * secretJq.ts and secretFields.ts beside this file are VERBATIM copies of the frontend's
 * ui/src/utils/{secretJq,secretFields}.ts at 32923ec8fe745ba8c3116159d892959f1f6272bf
 * (fix(form): secret fields are masked and never sent to /jq (#423) (#424), released in 1.6.83).
 * Re-copy them, and update the ref above, when the frontend changes them — never edit them here.
 *
 * Input (stdin): { "widgetData": <a Form's RESOLVED widgetData>, "values": <the submitted form values> }
 * Output (stdout): { "secretPaths": [...], "ops": [ { "op": i, "overrides": [ plan… ] } ] } where each
 * plan is one of
 *   { name, mode: "verbatim", value }              a non-${} value, used as written
 *   { name, mode: "local", value, touchesSecret }  resolved in the browser; nothing is sent
 *   { name, mode: "jq", expression, data }         sent to /jq with exactly this (secret-free) data
 *   { name, mode: "refused", error }               the whole submit is refused before any request
 * The secret paths come from the schema the Form renders: stringSchema when it parses, else schema
 * (Form.tsx jsonSchema / secretPaths).
 */
import { readFileSync } from 'node:fs'

import { secretFieldPaths } from './secretFields'
import { planOverride } from './secretJq'

type Override = { name: string; value: unknown }
type Op = { payloadToOverride?: Override[] }

const input = JSON.parse(readFileSync(0, 'utf8')) as { widgetData: Record<string, any>; values: Record<string, unknown> }
const { widgetData, values } = input

let schema = widgetData.schema
if (typeof widgetData.stringSchema === 'string' && widgetData.stringSchema.trim() !== '') {
  try {
    schema = JSON.parse(widgetData.stringSchema)
  } catch {
    // malformed: the Form falls back to `schema`, and so does this
  }
}
const secretPaths = secretFieldPaths(schema)

const plans = (overrides: Override[]) => overrides.map(({ name, value }) => {
  if (typeof value !== 'string' || !value.startsWith('${')) {
    return { mode: 'verbatim', name, value }
  }
  try {
    const plan = planOverride(name, value, values, secretPaths)
    return plan.mode === 'jq'
      ? { data: plan.data, expression: value, mode: 'jq', name }
      : { mode: 'local', name, touchesSecret: plan.touchesSecret, value: plan.value }
  } catch (error) {
    return { error: error instanceof Error ? error.message : String(error), mode: 'refused', name }
  }
})

const actions = [...(widgetData.actions?.rest ?? [])] as Array<{ id: string; ops?: Op[]; payloadToOverride?: Override[] }>
const out = {
  actions: actions.map((action) => ({
    id: action.id,
    ops: (action.ops ?? [{ payloadToOverride: action.payloadToOverride }]).map((op, index) => ({ op: index, overrides: plans(op.payloadToOverride ?? []) })),
  })),
  secretPaths,
}
process.stdout.write(JSON.stringify(out))
