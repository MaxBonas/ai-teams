import { readFile } from "node:fs/promises"
import { pathToFileURL } from "node:url"

const baseUrl = process.env.AITEAMS_OPENCODE_BASE_URL
const password = process.env.AITEAMS_OPENCODE_PASSWORD
const sdkEntry = process.env.AITEAMS_OPENCODE_SDK_ENTRY
const directory = process.env.AITEAMS_OPENCODE_DIRECTORY
const modelID = process.env.AITEAMS_OPENCODE_MODEL || "deepseek-v4-flash-free"

if (!baseUrl || !password || !sdkEntry || !directory) {
  throw new Error("missing OpenCode canary environment")
}

const { createOpencodeClient } = await import(pathToFileURL(sdkEntry).href)
const packageJson = JSON.parse(
  await readFile(new URL("../../package.json", pathToFileURL(sdkEntry)), "utf8"),
)
const authorization = `Basic ${Buffer.from(`opencode:${password}`).toString("base64")}`
const client = createOpencodeClient({
  baseUrl,
  directory,
  headers: { Authorization: authorization },
})

const unwrap = (result, operation) => {
  if (result?.error !== undefined) {
    throw new Error(`${operation}: ${JSON.stringify(result.error)}`)
  }
  return result?.data
}

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds))
const waitForStatus = async (sessionID, expected, timeoutMs = 10_000) => {
  const deadline = Date.now() + timeoutMs
  const observed = []
  while (Date.now() < deadline) {
    const statuses = unwrap(await client.session.status(), "session.status") || {}
    const type = statuses[sessionID]?.type || "idle"
    observed.push(type)
    if (type === expected) return observed
    await sleep(50)
  }
  throw new Error(`session ${sessionID} did not reach ${expected}; observed=${observed.join(",")}`)
}

let sessionID
const report = {
  sdk_version: packageJson.version,
  model: `opencode/${modelID}`,
  session_id: null,
  timings_ms: {},
  observed_before_abort: [],
  gates: {
    sdk_health_before: false,
    sdk_session_created: false,
    busy_observed_before_abort: false,
    server_abort_acknowledged: false,
    idle_after_abort: false,
    sdk_health_after: false,
    recovery_prompt_completed: false,
    recovery_marker_exact: false,
    json_schema_accepted: false,
    session_deleted: false,
  },
  structured_output_error: null,
}

try {
  const healthBefore = unwrap(await client.global.health(), "global.health")
  report.gates.sdk_health_before = healthBefore?.healthy === true

  const session = unwrap(
    await client.session.create({ title: "AI Teams SDK cancellation canary" }),
    "session.create",
  )
  sessionID = session.id
  report.session_id = sessionID
  report.gates.sdk_session_created = Boolean(sessionID)

  unwrap(
    await client.session.promptAsync({
      sessionID,
      model: { providerID: "opencode", modelID },
      tools: {},
      parts: [{
        type: "text",
        text: "Synthetic public cancellation canary. Think silently through 100 distinct numbered checks, then return only DONE. Do not use tools.",
      }],
    }),
    "session.promptAsync",
  )
  report.observed_before_abort = await waitForStatus(sessionID, "busy")
  report.gates.busy_observed_before_abort = true

  const abortStarted = performance.now()
  const aborted = unwrap(await client.session.abort({ sessionID }), "session.abort")
  report.timings_ms.abort = Math.round(performance.now() - abortStarted)
  report.gates.server_abort_acknowledged = aborted === true
  await waitForStatus(sessionID, "idle")
  report.gates.idle_after_abort = true

  const healthAfter = unwrap(await client.global.health(), "global.health after abort")
  report.gates.sdk_health_after = healthAfter?.healthy === true

  const marker = "OPENCODE_SDK_RECOVERY_OK"
  const promptStarted = performance.now()
  const response = unwrap(
    await client.session.prompt({
      sessionID,
      model: { providerID: "opencode", modelID },
      tools: {},
      format: {
        type: "json_schema",
        schema: {
          type: "object",
          properties: {
            status: { type: "string" },
            marker: { type: "string" },
          },
          required: ["status", "marker"],
          additionalProperties: false,
        },
      },
      parts: [{
        type: "text",
        text: `Return JSON with status completed and marker ${marker}. No tools.`,
      }],
    }, { signal: AbortSignal.timeout(180_000) }),
    "session.prompt recovery",
  )
  report.timings_ms.recovery_prompt = Math.round(performance.now() - promptStarted)
  const text = (response?.parts || [])
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("")
  let payload
  try {
    payload = JSON.parse(text)
  } catch {
    payload = null
  }
  report.gates.recovery_prompt_completed = response?.info?.finish === "stop"
  report.gates.recovery_marker_exact = payload?.status === "completed" && payload?.marker === marker
  report.structured_output_error = response?.info?.error || null
  report.gates.json_schema_accepted = !response?.info?.error && response?.info?.structured !== undefined
} finally {
  if (sessionID) {
    try {
      const deleted = unwrap(await client.session.delete({ sessionID }), "session.delete")
      report.gates.session_deleted = deleted === true
    } catch (error) {
      report.cleanup_error = error instanceof Error ? error.message : String(error)
    }
  }
}

console.log(JSON.stringify(report))
