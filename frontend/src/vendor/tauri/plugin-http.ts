/**
 * Browser shim for `@tauri-apps/plugin-http`.
 *
 * The desktop app made provider HTTP calls through Rust to dodge CORS
 * (MiniMax / Volcengine Ark / on-prem gateways don't send browser-
 * friendly headers). The web port keeps the same pattern: requests are
 * relayed through POST /api/v1/llm/proxy, which streams the upstream
 * SSE body back verbatim.
 */

export async function fetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const url = String(input)
  const headers: Record<string, string> = {}
  new Headers(init?.headers ?? {}).forEach((value, key) => {
    headers[key] = value
  })

  const body =
    typeof init?.body === "string" ? init.body : init?.body ?? undefined

  const res = await globalThis.fetch("/api/v1/llm/proxy", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      method: init?.method ?? "GET",
      headers,
      body,
    }),
  })

  // The relay surfaces the upstream status in a header so the caller's
  // response.ok checks behave exactly as they would have via Tauri.
  const status = Number(res.headers.get("x-proxy-status") ?? 200)
  return new Response(res.body, {
    status,
    statusText: status === 200 ? "OK" : "Error",
    headers: {
      "Content-Type": res.headers.get("content-type") ?? "text/plain",
    },
  })
}
