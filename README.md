# py-llm-wiki

Python/FastAPI B/S port of [llm_wiki](https://github.com/nashsu/llm_wiki) — a personal knowledge base that builds itself (Karpathy's LLM Wiki pattern). The LLM reads your documents, builds a structured wiki, and keeps it current.

**v0 scope (implemented)**: two-step CoT ingest (MD/TXT/PDF/DOCX/Org), hybrid retrieval (tokenized + graph), chat (SSE), wiki browsing/editing, knowledge graph, review system, cascade deletion, source-folder auto-watch, ZIP archive export/import. Single-user, localhost-first; Docker sandbox, multi-user, vector search, Deep Research land later.

**Verification**: `pytest` 170 tests (golden vectors cross-checked against llm_wiki's JS/Rust implementations) + 1807 ported vitest tests + MCP contract replay. See [docs/porting-notes.md](docs/porting-notes.md).

## Architecture

```
browser (React 19, ported from llm_wiki)  ← vite proxy /api →
FastAPI backend (127.0.0.1:19828)
  ├─ /api/v1/*          19828-compatible API (mcp-server works unchanged)
  ├─ /api/v1/tauri/invoke   invoke() command bridge for the frontend shim
  ├─ /api/v1/llm/proxy      transparent relay for frontend LLM calls (CORS)
  └─ backend/ingest, search, chat, review, graph  (Python ports)
```

The wiki project format, `.llm-wiki` state files, LLM prompt templates, and
retrieval/graph algorithms are byte-compatible with llm_wiki — projects
created by either app open in the other, and stay Obsidian-compatible.

## Run

```bash
# backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m backend.main            # http://127.0.0.1:19828

# frontend (dev)
cd frontend && npm install && npm run dev   # http://localhost:1420
```

Open http://localhost:1420 → create a project → configure your LLM
provider in Settings → upload documents.

## Test

```bash
.venv/bin/python -m pytest backend/tests    # mock-LLM suites
cd frontend && npm test                     # pure-logic vitest suites
```

## License

GPL-3.0-or-later. This project is a derivative work of
[llm_wiki](https://github.com/nashsu/llm_wiki) (Copyright 2024-2026 Yong Su,
GPL-3.0): the React frontend is copied and adapted, and the backend ports
llm_wiki's algorithms, prompts, and data formats. See
[docs/porting-notes.md](docs/porting-notes.md) for per-file provenance.
