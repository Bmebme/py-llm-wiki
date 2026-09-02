# py-llm-wiki Architecture

## Process model

Single FastAPI process on `127.0.0.1:19828` (uvicorn, async). The React
frontend runs via Vite dev server on `:1420` proxying `/api` to the
backend — same-origin, no CORS in dev.

```
┌─────────────────────────┐        ┌──────────────────────────────────┐
│ Browser (React 19)      │        │ FastAPI backend                  │
│  llm_wiki UI ported     │ /api   │  router_v1    19828 contract     │
│  @tauri-apps → vendor   │───────▶│  router_tauri invoke() bridge    │
│  shims (invoke/fetch)   │  SSE   │  router_fs    browser fs APIs    │
│  settings via store shim│◀───────│  router_llm   HTTP relay (CORS)  │
└─────────────────────────┘        │  commands/    fs, project, misc  │
                                   │  core/        registry, settings │
                                   │  ingest/ search/ chat/ review/   │
                                   └──────────────┬───────────────────┘
                                                  │ project dirs on disk
                                                  ▼
                              ~/Projects/<wiki>/  (llm_wiki-compatible
                                                  format + .llm-wiki state)
```

## Key flows

- **Frontend `invoke(cmd, args)`** → `POST /api/v1/tauri/invoke` →
  `backend/commands/*` dispatcher. Commands return Tauri-shaped payloads
  (`{ok: true, value: ...}`) so the UI code stays untouched.
- **LLM calls from the UI** → `POST /api/v1/llm/proxy` raw relay →
  upstream provider SSE streamed back (dodges provider CORS).
- **LLM calls from the backend** (ingest, chat agent) → `backend/llm/*`
  direct httpx client, no relay.
- **Settings** → `GET/PUT /api/v1/settings` on `~/.py-llm-wiki/
  app-state.json`; the browser store shim mirrors it in memory.

## Compatibility contracts

See the implementation plan and [porting-notes.md](porting-notes.md):
project directory format, `.llm-wiki` JSON formats, wiki frontmatter,
the 19828 HTTP API (envelope, SSE frames, token auth), and the search /
graph / budget algorithms are byte- and behavior-compatible with
llm_wiki.

## State ownership

- `~/.py-llm-wiki/app-state.json` — global settings (llmConfig,
  apiConfig, projectRegistry, recentProjects, ...)
- `<project>/.llm-wiki/*` — per-project state (ingest-cache, queue,
  review.json, conversations, agent-sessions)
- In-memory only: project registry cache, per-project asyncio locks,
  chat stream cancellation registry
