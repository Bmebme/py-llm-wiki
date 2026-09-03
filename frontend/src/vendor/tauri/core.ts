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
  let res: Response
  try {
    res = await fetch("/api/v1/tauri/invoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, args: args ?? {} }),
    })
  } catch (err) {
    throw err
  }
  // 后端本机限流 429: 等一小段随机延迟重试一次（UI 挂载风暴常见于
  // React StrictMode 双执行 + 文件树刷新突发）。
  if (res.status === 429) {
    await new Promise((resolve) => setTimeout(resolve, 300 + Math.random() * 400))
    res = await fetch("/api/v1/tauri/invoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command, args: args ?? {} }),
    })
  }
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
