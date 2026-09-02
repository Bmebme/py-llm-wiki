/**
 * Browser replacement for the desktop's `invoke("agent_start_turn_stream")`
 * + `listen("agent-event")` pair.
 *
 * The FastAPI backend streams the 19828 SSE contract
 * (event: meta/agent/done/cancelled/error); every `agent` frame is
 * dispatched into the vendor event shim with the same
 * `{sessionId, runId, event}` payload shape the desktop Tauri events
 * carried, so chat-panel's existing handler runs unchanged.
 */

import { dispatchAgentEvent } from "@/vendor/tauri/event"

export interface AgentTurnRequest {
  [key: string]: unknown
}

/**
 * Start a streaming agent turn. Resolves when the terminal
 * done/cancelled/error frame arrives (or the stream ends).
 */
export async function startAgentTurnStream(
  projectId: string,
  request: AgentTurnRequest,
): Promise<void> {
  const response = await fetch(`/api/v1/projects/${encodeURIComponent(projectId)}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...request, stream: true }),
  })
  if (!response.ok || !response.body) {
    let message = `Agent chat request failed (${response.status})`
    try {
      const data = await response.json()
      if (data.error) message = data.error
    } catch {
      // non-JSON error body — keep the status message
    }
    throw new Error(message)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  let sessionId = (request.sessionId as string) ?? ""
  let runId = (request.runId as string) ?? ""

  const dispatchFrame = (eventName: string, data: unknown) => {
    if (eventName === "meta" && data && typeof data === "object") {
      const meta = data as { sessionId?: string; runId?: string }
      if (meta.sessionId) sessionId = meta.sessionId
      if (meta.runId) runId = meta.runId
      return
    }
    if (eventName === "agent") {
      dispatchAgentEvent({ sessionId, runId, event: data })
      return
    }
    if (eventName === "error" || eventName === "cancelled") {
      const payload = data as { error?: string } | null
      dispatchAgentEvent({
        sessionId,
        runId,
        event: { type: "error", message: payload?.error ?? `Agent turn ${eventName}` },
      })
    }
    // done carries the aggregate; chat-panel already has the streamed
    // deltas, so nothing to dispatch.
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let newlineIndex: number
    while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newlineIndex).replace(/\r$/, "")
      buffer = buffer.slice(newlineIndex + 1)
      if (line.startsWith("event: ")) {
        const eventName = line.slice(7).trim()
        // data line follows on the next line(s)
        let dataLine = ""
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const nextIndex = buffer.indexOf("\n")
          if (nextIndex < 0) break
          const candidate = buffer.slice(0, nextIndex).replace(/\r$/, "")
          buffer = buffer.slice(nextIndex + 1)
          if (candidate.startsWith("data: ")) {
            dataLine += candidate.slice(6)
          } else if (candidate === "") {
            break
          } else if (candidate.startsWith("event: ")) {
            // Missing blank separator — put the line back and stop.
            buffer = candidate + "\n" + buffer
            break
          } else if (candidate.startsWith(":")) {
            // keepalive comment — ignore
          } else {
            break
          }
        }
        if (eventName && dataLine) {
          try {
            dispatchFrame(eventName, JSON.parse(dataLine))
          } catch {
            // malformed frame — skip, the backend guarantees terminals
          }
        }
      } else if (line.startsWith(":") || line === "") {
        // heartbeat comment / separator — ignore
      }
    }
  }
}

/** Stop an in-flight turn (the backend cancels the stream server-side). */
export async function cancelAgentTurn(
  projectId: string,
  sessionId: string,
): Promise<void> {
  await fetch(
    `/api/v1/projects/${encodeURIComponent(projectId)}/chat/${encodeURIComponent(sessionId)}/cancel`,
    { method: "POST" },
  )
}
