/**
 * Browser shim for `@tauri-apps/api/event`.
 *
 * The desktop app consumed `agent-event` (chat streaming) and
 * `file-sync://*` (source watch) Tauri events. The web port delivers
 * agent-event over the chat SSE stream (src/lib/web-agent.ts dispatches
 * into this module); file-sync lands in M5. Other events are inert.
 */

export type UnlistenFn = () => void

type EventHandler = (event: { payload: unknown }) => void

const handlers = new Map<string, Set<EventHandler>>()

export async function listen<T>(
  event: string,
  handler: (event: { payload: T }) => void,
): Promise<UnlistenFn> {
  const set = handlers.get(event) ?? new Set<EventHandler>()
  set.add(handler as EventHandler)
  handlers.set(event, set)
  return () => {
    set.delete(handler as EventHandler)
    if (set.size === 0) handlers.delete(event)
  }
}

export async function emit<T>(event: string, payload?: T): Promise<void> {
  const set = handlers.get(event)
  if (!set) return
  for (const handler of set) {
    handler({ payload })
  }
}

/** Dispatch an agent-event frame (called by web-agent.ts). */
export function dispatchAgentEvent(payload: unknown): void {
  const set = handlers.get("agent-event")
  if (!set) return
  for (const handler of set) {
    handler({ payload })
  }
}
