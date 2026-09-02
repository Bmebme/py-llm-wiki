/**
 * Browser shim for `@tauri-apps/plugin-store`.
 *
 * The desktop app persisted settings through the Tauri Store plugin to
 * app-state.json; the FastAPI backend owns that file now and exposes it
 * at GET/PUT /api/v1/settings. This shim keeps the `load(...).get/set/
 * delete` surface so src/lib/project-store.ts works unchanged.
 */

interface StoreOptions {
  autoSave?: boolean
  defaults?: Record<string, unknown>
}

class BrowserStore {
  private cache: Record<string, unknown> | null = null
  private inflight: Promise<Record<string, unknown>> | null = null

  constructor(
    readonly name: string,
    private readonly defaults: Record<string, unknown>,
  ) {}

  private async state(): Promise<Record<string, unknown>> {
    if (this.cache) return this.cache
    if (!this.inflight) {
      this.inflight = (async () => {
        let state: Record<string, unknown>
        try {
          const res = await fetch("/api/v1/settings")
          const data = await res.json()
          state = data.ok ? { ...this.defaults, ...data } : { ...this.defaults }
        } catch {
          state = { ...this.defaults }
        }
        this.cache = state
        return state
      })()
    }
    return this.inflight
  }

  async get<T>(key: string): Promise<T | undefined> {
    const state = await this.state()
    return state[key] as T | undefined
  }

  async set(key: string, value: unknown): Promise<void> {
    const state = await this.state()
    state[key] = value
    await this.flush()
  }

  async delete(key: string): Promise<void> {
    const state = await this.state()
    delete state[key]
    await this.flush()
  }

  async save(): Promise<void> {
    await this.flush()
  }

  private async flush(): Promise<void> {
    if (!this.cache) return
    await fetch("/api/v1/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(this.cache),
    })
  }
}

export async function load(
  name: string,
  options: StoreOptions = {},
): Promise<BrowserStore> {
  return new BrowserStore(name, options.defaults ?? {})
}
