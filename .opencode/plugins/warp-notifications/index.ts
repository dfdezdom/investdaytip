// Warp terminal notifications for OpenCode V2.
//
// This is a local port of the official V1 plugin `@warp-dot-dev/opencode-warp@0.1.7`
// (https://github.com/warpdotdev/opencode-warp) to the OpenCode V2 plugin API.
// The upstream package still ships a V1 entrypoint (`export const WarpPlugin = ...`),
// which V2 refuses to load with:
//   "Plugin must export a default definition with an id and an effect or setup function."
//
// V1 -> V2 mapping used here (see https://opencode.ai/v2/docs/build/plugins/migrate-v1):
//   export const WarpPlugin({ client, directory }) => ({ ...hooks })   V1
//   export default { id, setup(ctx) }                                 V2
//   directory                                          -> ctx.location.directory
//   client.session.get({ path: { id } })                -> ctx.session.get({ sessionID })
//   client.session.messages({ path: { id } })           -> ctx.session.context({ sessionID })
//   client.app.log(...)                                 -> console.* (no V2 equivalent)
//   returned `event` hook                               -> ctx.event.subscribe()
//   returned `chat.message`                             -> ctx.session.hook("prompt", ...)
//   returned `tool.execute.before` / `tool.execute.after` -> ctx.tool.hook(...)
//
// No package import is required: V2 only validates that the default export has an
// `id` plus an `effect` or `setup` function.

import { writeFileSync } from "node:fs"
import path from "node:path"

const PLUGIN_ID = "warp-notifications"
const PLUGIN_VERSION = "0.1.7"
const NOTIFICATION_TITLE = "warp://cli-agent"
const PLUGIN_MAX_PROTOCOL_VERSION = 1

type Json = Record<string, any>

function inWarp(): boolean {
  return Boolean(process.env.WARP_CLI_AGENT_PROTOCOL_VERSION)
}

function negotiateProtocolVersion(): number {
  const warpVersion = parseInt(process.env.WARP_CLI_AGENT_PROTOCOL_VERSION || "1", 10)
  if (isNaN(warpVersion)) return PLUGIN_MAX_PROTOCOL_VERSION
  return Math.min(warpVersion, PLUGIN_MAX_PROTOCOL_VERSION)
}

function buildPayload(event: string, sessionId: string, cwd: string, extraFields: Json = {}): string {
  const base = {
    v: negotiateProtocolVersion(),
    agent: "opencode",
    event,
    session_id: sessionId,
    cwd,
    project: cwd ? path.basename(cwd) : "",
  }
  return JSON.stringify({ ...base, ...extraFields })
}

/**
 * Send a Warp notification via OSC 777 escape sequence.
 * Only emits when Warp declares cli-agent protocol support, avoiding garbled
 * output in other terminals (and working over SSH).
 *
 * On Unix we write to /dev/tty so the sequence bypasses any stdout redirection
 * or terminal-multiplexer capture. On Windows /dev/tty does not exist, so we
 * fall back to process.stdout.
 */
function warpNotify(title: string, body: string): void {
  if (!inWarp()) return
  const sequence = `\x1b]777;notify;${title};${body}\x07`
  try {
    writeFileSync("/dev/tty", sequence)
  } catch {
    try {
      process.stdout.write(sequence)
    } catch {
      // Silently ignore if stdout is also unavailable.
    }
  }
}

function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str
  return str.slice(0, maxLen - 3) + "..."
}

/** Extract plain text from a list of V1 `Part` or V2 content items. */
function extractText(parts: any): string {
  if (!Array.isArray(parts)) return ""
  return parts
    .map((part: any) =>
      part && part.type === "text" && typeof part.text === "string" && part.text ? part.text : "",
    )
    .filter(Boolean)
    .join(" ")
}

/**
 * V1 exposed the tool name as `perm.type`; V2's `permission.asked` payload is
 * `{ id, sessionID, action, resources, save, metadata, source, message }`.
 * Read every known shape so the notification stays meaningful either way.
 */
function buildPermissionPayload(perm: Json, cwd: string): string {
  const source = Array.isArray(perm.source) ? perm.source[0] : undefined
  const toolName: string =
    perm.action ?? perm.type ?? perm.permission ?? source?.type ?? "unknown"

  const metadata: Json = perm.metadata ?? {}
  let toolPreview = ""
  if (typeof metadata.command === "string") {
    toolPreview = metadata.command
  } else if (typeof metadata.file_path === "string") {
    toolPreview = metadata.file_path
  } else if (typeof metadata.filePath === "string") {
    toolPreview = metadata.filePath
  } else if (typeof perm.message === "string" && perm.message) {
    toolPreview = perm.message
  } else if (Array.isArray(perm.resources) && perm.resources.length > 0) {
    toolPreview = perm.resources.join(" ")
  } else {
    const raw = JSON.stringify(metadata)
    toolPreview = raw === undefined ? "" : raw.slice(0, 80)
  }

  let summary = `Wants to run ${toolName}`
  if (toolPreview) summary += `: ${truncate(toolPreview, 120)}`

  return buildPayload("permission_request", perm.sessionID ?? "", cwd, {
    summary,
    tool_name: toolName,
    tool_input: metadata,
  })
}

export default {
  id: PLUGIN_ID,

  async setup(ctx: Json) {
    const cwd: string = ctx?.location?.directory || ""

    if (!inWarp()) {
      // Not fatal: warpNotify() is gated on the same variable, so hooks stay
      // registered but emit nothing outside Warp. The background service inherits
      // its environment at start-up, so warn once instead of failing the load.
      console.warn(
        "[warp-notifications] WARP_CLI_AGENT_PROTOCOL_VERSION not set — notifications stay quiet until OpenCode runs inside Warp.",
      )
    }

    const subagentCache = new Map<string, boolean>()

    async function isSubagentSession(sessionId?: string | null): Promise<boolean> {
      if (!sessionId) return false
      const cached = subagentCache.get(sessionId)
      if (cached !== undefined) return cached
      try {
        const res = await ctx.session.get({ sessionID: sessionId })
        const session = res && typeof res === "object" && "data" in res ? res.data : res
        const result = Boolean(session?.parentID)
        subagentCache.set(sessionId, result)
        return result
      } catch {
        // If we cannot fetch the session, fall through and notify anyway.
        return false
      }
    }

    async function maybeWarpNotify(sessionId?: string | null, body = ""): Promise<void> {
      if (!inWarp() || !body) return
      if (await isSubagentSession(sessionId)) return
      warpNotify(NOTIFICATION_TITLE, body)
    }

    /** Hooks must never block or reject — notifications are best-effort. */
    function fireAndForget(work: Promise<void>, label: string): void {
      work.catch((err) => {
        console.error(`[warp-notifications] ${label} failed:`, err)
      })
    }

    /** V1 delivered `{ type, properties }`; the internal V2 bus uses `{ type, data }`. */
    function propsOf(event: Json | undefined): Json {
      if (!event || typeof event !== "object") return {}
      return event.properties ?? event.data ?? event.payload ?? {}
    }

    async function handleSessionContext(sessionId?: string | null): Promise<{ query: string; response: string }> {
      let query = ""
      let response = ""
      if (!sessionId) return { query, response }
      try {
        const res = await ctx.session.context({ sessionID: sessionId })
        const messages = res && typeof res === "object" && "data" in res ? res.data : res
        if (Array.isArray(messages)) {
          const reversed = [...messages].reverse()
          // V2: flat messages with `type`; V1: wrapped `{ info: { role }, parts }`.
          const lastUser = reversed.find((m: Json) => m?.type === "user" || m?.info?.role === "user")
          if (lastUser) query = typeof lastUser.text === "string" ? lastUser.text : extractText(lastUser.parts)
          const lastAssistant = reversed.find(
            (m: Json) => m?.type === "assistant" || m?.info?.role === "assistant",
          )
          if (lastAssistant) response = extractText(lastAssistant.parts ?? lastAssistant.content)
        }
      } catch {
        // Without messages we still send the notification, just without query/response.
      }
      return { query, response }
    }

    async function handleEvent(event: Json): Promise<void> {
      const type = event?.type
      const p = propsOf(event)
      const sessionID: string | undefined = p.sessionID ?? p.info?.id ?? p.session?.id

      switch (type) {
        case "session.created": {
          const parentID = p.parentID ?? p.info?.parentID
          if (sessionID) subagentCache.set(sessionID, Boolean(parentID))
          // Subagent sessions must not notify the user.
          if (parentID) return
          await maybeWarpNotify(
            sessionID,
            buildPayload("session_start", sessionID ?? "", cwd, {
              plugin_version: PLUGIN_VERSION,
            }),
          )
          return
        }
        case "session.idle": {
          if (!inWarp()) return
          const { query, response } = await handleSessionContext(sessionID)
          await maybeWarpNotify(
            sessionID,
            buildPayload("stop", sessionID ?? "", cwd, {
              query: truncate(query, 200),
              response: truncate(response, 200),
              transcript_path: "",
            }),
          )
          return
        }
        // V1 `permission.updated` was renamed to `permission.asked` in V2.
        case "permission.asked":
        case "permission.updated": {
          await maybeWarpNotify(sessionID, buildPermissionPayload(p, cwd))
          return
        }
        case "permission.replied": {
          // V1 used `response`, V2 uses `reply`; both use the "reject" value.
          const reply = p.reply ?? p.response
          if (reply === "reject") return
          await maybeWarpNotify(
            sessionID,
            buildPayload("permission_replied", sessionID ?? "", cwd),
          )
          return
        }
        default:
          return
      }
    }

    // V1 `event` hook -> V2 public event stream subscription.
    const controller = new AbortController()
    fireAndForget(
      (async () => {
        try {
          for await (const event of ctx.event.subscribe({ signal: controller.signal })) {
            try {
              await handleEvent(event)
            } catch (err) {
              console.error("[warp-notifications] event handler failed:", err)
            }
          }
        } catch (err) {
          // AbortError on cleanup is expected.
          if ((err as { name?: string })?.name !== "AbortError") {
            console.error("[warp-notifications] event subscription ended:", err)
          }
        }
      })(),
      "event subscription",
    )

    // V1 `chat.message` -> fires once per new user prompt (prompt admission).
    // `message.updated` fires repeatedly per message and could clobber the
    // completion notification, which is why the prompt hook is used instead.
    await ctx.session.hook("prompt", (event: Json) => {
      const queryText: string = event?.prompt?.text ?? ""
      if (!queryText) return
      const sessionID = event?.sessionID
      fireAndForget(
        maybeWarpNotify(
          sessionID,
          buildPayload("prompt_submit", sessionID ?? "", cwd, {
            query: truncate(queryText, 200),
          }),
        ),
        "prompt_submit",
      )
    })

    // Used to detect the built-in `question` tool so Warp can notify that input
    // is needed.
    await ctx.tool.hook("execute.before", (event: Json) => {
      if (event?.tool !== "question") return
      const sessionID = event?.sessionID
      fireAndForget(
        maybeWarpNotify(
          sessionID,
          buildPayload("question_asked", sessionID ?? "", cwd, {
            tool_name: event?.tool,
          }),
        ),
        "question_asked",
      )
    })

    // Tool completion — fires after every tool call.
    await ctx.tool.hook("execute.after", (event: Json) => {
      const sessionID = event?.sessionID
      fireAndForget(
        maybeWarpNotify(
          sessionID,
          buildPayload("tool_complete", sessionID ?? "", cwd, {
            tool_name: event?.tool,
          }),
        ),
        "tool_complete",
      )
    })

    return () => controller.abort()
  },
}
