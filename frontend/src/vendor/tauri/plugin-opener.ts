/**
 * Browser shim for `@tauri-apps/plugin-opener`.
 *
 * openUrl opens a new tab; openPath (reveal in OS file manager) has no
 * browser equivalent — callers guard on it or the button is disabled.
 */

export async function openUrl(url: string): Promise<void> {
  window.open(url, "_blank", "noopener,noreferrer")
}

export async function openPath(_path: string): Promise<void> {
  // No browser equivalent for revealing a path in Finder/Explorer.
}

export async function revealItemInDir(_path: string): Promise<void> {}
