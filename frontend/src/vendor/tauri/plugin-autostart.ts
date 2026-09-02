/**
 * Browser shim for `@tauri-apps/plugin-autostart` — a web page cannot
 * register OS autostart; the setting is stored but inert.
 */

export async function isEnabled(): Promise<boolean> {
  return false
}

export async function enable(): Promise<void> {}

export async function disable(): Promise<void> {}
