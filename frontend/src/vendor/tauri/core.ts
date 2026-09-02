/**
 * Browser shim for `@tauri-apps/api/core`.
 *
 * `invoke` relays to the FastAPI backend's POST /api/v1/tauri/invoke
 * dispatcher, which implements the same command surface as the desktop
 * Rust backend. `convertFileSrc` maps an absolute filesystem path to
 * the backend asset endpoint.
 */

export async function invoke<T>(
  command: string,
  args?: Record<string, unknown>,
): Promise<T> {
  const res = await fetch("/api/v1/tauri/invoke", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ command, args: args ?? {} }),
  })
  const data = await res.json()
  if (!data.ok) {
    throw new Error(data.error ?? `Command failed: ${command}`)
  }
  return data.value as T
}

/** Absolute path → backend asset URL (served with the right content type). */
export function convertFileSrc(filePath: string): string {
  return `/api/v1/asset?path=${encodeURIComponent(filePath)}`
}
