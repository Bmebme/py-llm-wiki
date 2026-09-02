/**
 * Browser shim for `@tauri-apps/api/window`.
 *
 * src/lib/theme.ts guards native window calls behind isTauriRuntime()
 * so these methods are never invoked in the browser; the module only
 * needs to import cleanly and expose the Theme type.
 */

export type Theme = "light" | "dark"

export function getCurrentWindow(): {
  setTheme: (theme: Theme) => Promise<void>
  setBackgroundColor: (color: string) => Promise<void>
  close: () => Promise<void>
  minimize: () => Promise<void>
} {
  return {
    setTheme: async () => {},
    setBackgroundColor: async () => {},
    close: async () => {},
    minimize: async () => {},
  }
}
