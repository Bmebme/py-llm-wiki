# Porting Notes — provenance of code derived from llm_wiki

llm_wiki: <https://github.com/nashsu/llm_wiki> — GPL-3.0, Copyright (C)
2024-2026 Yong Su (nash_su). py-llm-wiki is a derivative work licensed
under the same terms (see LICENSE).

## Frontend

`frontend/src/` is a copy of llm_wiki's `src/` with these changes:

| File | Change |
|------|--------|
| `src/vendor/tauri/*` | NEW — browser shims replacing `@tauri-apps/*` (vite alias) |
| `vite.config.ts`, `package.json`, `index.html` | Adapted: Tauri deps removed, `/api` proxy added |

## Backend ports (algorithm/prompt/format parity)

| py-llm-wiki module | llm_wiki source |
|--------------------|-----------------|
| `backend/core/project.py` | `src-tauri/src/commands/project.rs` (create/open/validate, scaffold content) |
| `backend/core/file_service.py` | `src-tauri/src/api_server.rs` (safe_join, is_public_project_rel, is_text_content_rel, list_tree) + `src-tauri/src/commands/fs.rs` (list_directory semantics) |
| `backend/core/hashing.py` | `src/stores/review-store.ts` (FNV-1a over UTF-16 code units) |
| `backend/core/settings_store.py` | `src/lib/project-store.ts` (app-state.json keys) |
| `backend/core/project_registry.py` | `src-tauri/src/api_server.rs` (load_projects, resolve_project) |
| `backend/api/auth.py`, `router_v1.py`, `sse.py` | `src-tauri/src/api_server.rs` (auth, envelopes, limits, SSE frames) |
| `backend/commands/fs_commands.py` | `src-tauri/src/commands/fs.rs` |
| `backend/api/router_llm.py` | `src/lib/tauri-fetch.ts` + `src-tauri/plugin-http` (CORS-dodging relay pattern) |
| `backend/ingest/*` (parse_blocks, prompts, cache, queue, pipeline, postprocess, sanitize, extract_text, sources, watch) | `src/lib/ingest.ts`, `ingest-cache.ts`, `ingest-queue.ts`, `ingest-sanitize.ts`, `source-lifecycle.ts`, `project-file-sync.ts` + `src-tauri/src/commands/fs.rs` (preprocess_file) |
| `backend/wiki/*` (frontmatter, page_merge, index_log, sources_merge, source_identity, wiki_filename, wikilinks, path_utils) | `src/lib/frontmatter.ts`, `page-merge.ts`, `sources-merge.ts`, `source-identity.ts`, `wiki-filename.ts`, `path-utils.ts` + `ingest.ts` (index/log writers) |
| `backend/llm/*` (providers, client) | `src/lib/llm-providers.ts`, `llm-client.ts`, `endpoint-normalizer.ts` |
| `backend/language/*` | `src/lib/detect-language.ts`, `output-language.ts` |
| `backend/search/*` (tokenize, scoring, engine, graph) | `src-tauri/src/commands/search.rs` + `src-tauri/src/api_server.rs` (handle_graph) |
| `backend/graph/relevance.py` | `src/lib/graph-relevance.ts` (4-signal model, TYPE_AFFINITY) |
| `backend/chat/*` (agent, session, context, cancel) | `src-tauri/src/agent/runtime.rs` (v0 single-round subset), `agent/context.rs` caps, `src/lib/context-budget.ts` |
| `backend/review/*` (models, store, create_page, sweep) | `src/stores/review-store.ts`, `src/lib/review-utils.ts`, `review-create-page.ts`, `sweep-reviews.ts` (stage 1) + `api_server.rs` review endpoints |
| `backend/delete/*` (wiki_cleanup, wiki_page_delete, source_lifecycle, archive) | `src/lib/wiki-cleanup.ts`, `wiki-page-delete.ts`, `source-lifecycle.ts`, `src-tauri/src/commands/project_maintenance.rs` |

Planned ports (later milestones): `ingest.ts` (prompts, FILE/REVIEW
grammar, index/log writers), `search.rs` (tokenizer, scoring, RRF, graph
blend), `graph-relevance.ts`, `context-budget.ts`, `review-store.ts` +
`api_server.rs` review endpoints, `wiki-page-delete.ts` /
`source-lifecycle.ts` (cascade delete), `detect-language.ts` /
`output-language.ts`.

## MCP server

`mcp-server/` is copied from llm_wiki unchanged (MIT) — it speaks the
19828 HTTP contract over HTTP, no code changes needed.

## Deviations from llm_wiki (v0 decisions)

1. **Auth default**: loopback binding defaults to allowUnauthenticated
   (single-user localhost-first); 0.0.0.0 stays fail-closed like the desktop.
2. **Chat agent**: v0 is single-round (retrieve → assemble → stream);
   the SSE event contract is emitted faithfully, the tool loop is deferred.
3. **PDF extraction**: pypdfium2 (same pdfium engine as the desktop's
   pdfium-render); MinerU not ported.
4. **Deferred**: vector search (LanceDB), Deep Research, Chrome clipper,
   file history, dedup, agent skills/shell, scheduled import.
