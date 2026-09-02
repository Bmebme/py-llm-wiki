"""End-to-end ingest with a mocked LLM — the M1 acceptance test.

Covers: two-step CoT (two LLM calls), FILE/REVIEW parsing, page writes
with frontmatter, deterministic log + index updates, review persistence
with stable ids, incremental cache (second run = zero LLM calls), and
queue integration.
"""

import asyncio
import json

import httpx
import pytest

from backend.core import project as project_core
from backend.ingest import cache, pipeline, queue as ingest_queue
from backend.ingest.queue import IngestTask

ANALYSIS_RESPONSE = """## Key Entities
- Foo System — a database engine

## Key Concepts
- KV caching

## Main Arguments & Findings
- Foo System accelerates reads 3x.

## Connections to Existing Wiki
- none

## Contradictions & Tensions
- none

## Recommendations
- create entity page for Foo System, concept page for KV caching
"""

GENERATION_RESPONSE = """---FILE: wiki/sources/test-paper.md---
---
type: source
title: "Source: test-paper.md"
created: 2026-08-14
updated: 2026-08-14
tags: []
related: []
sources: ["test-paper.md"]
---

# Source: test-paper.md

A paper about Foo System.
---END FILE---

---FILE: wiki/entities/foo-system.md---
---
type: entity
title: Foo System
created: 2026-08-14
updated: 2026-08-14
tags: [database]
related: [kv-caching]
sources: ["test-paper.md"]
---

# Foo System

A database engine. See [[kv-caching]].
---END FILE---

---FILE: wiki/concepts/kv-caching.md---
---
type: concept
title: KV Caching
created: 2026-08-14
updated: 2026-08-14
tags: []
related: [foo-system]
sources: ["test-paper.md"]
---

# KV Caching

Cache technique used by [[foo-system]].
---END FILE---

---FILE: wiki/log.md---
## [2026-08-14] ingest | test-paper.md
---END FILE---

---REVIEW: missing-page | KV Caching Deep Dive---
An important concept referenced but lacking detail.
OPTIONS: Create Page | Skip
PAGES: wiki/concepts/kv-caching.md
SEARCH: kv caching database internals | key value store cache design
---END REVIEW---
"""


def _mock_openai_transport(responses: list[str]) -> httpx.MockTransport:
    """OpenAI-style non-streaming responder: returns the canned texts in
    order, then raises if called more times than expected."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = calls["n"]
        calls["n"] += 1
        if index >= len(responses):
            raise AssertionError(f"LLM called {index + 1} times, expected {len(responses)}")
        return httpx.Response(
            200,
            json={
                "id": f"chatcmpl-mock-{index}",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": responses[index]},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    return httpx.MockTransport(handler)


LLM_CONFIG = {
    "provider": "openai",
    "apiKey": "sk-mock",
    "model": "gpt-4o",
    "streamingEnabled": False,
    "maxContextSize": 204800,
}


@pytest.fixture()
def wiki_project(tmp_path):
    project = project_core.create_project("ingest-demo", str(tmp_path))
    project_core.ensure_project_id(project.path)
    (tmp_path / "ingest-demo" / "raw" / "sources").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ingest-demo" / "raw" / "sources" / "test-paper.md").write_text(
        "# Test Paper\n\nFoo System accelerates reads 3x via KV caching.\n",
        encoding="utf-8",
    )
    return project


def _task(source_path: str) -> IngestTask:
    return IngestTask(
        id="task-1", projectId="pid", sourcePath=source_path, status="pending"
    )


def test_full_ingest_flow(wiki_project, tmp_path):
    # 4 FILE blocks triggers the dedicated review-suggestion stage (a
    # third LLM call per ingest.ts shouldRunDedicatedReviewStage) — the
    # mock answers it with an empty response (nothing further to review).
    transport = _mock_openai_transport([ANALYSIS_RESPONSE, GENERATION_RESPONSE, ""])
    http_client = httpx.AsyncClient(transport=transport)

    async def run():
        written = await pipeline.process_task(
            wiki_project.path, _task("raw/sources/test-paper.md"),
            llm_config=LLM_CONFIG, http_client=http_client,
        )
        await http_client.aclose()
        return written

    written = asyncio.run(run())
    project_root = tmp_path / "ingest-demo"

    # Pages written with correct frontmatter + canonicalized sources
    assert any(p.startswith("wiki/sources/") for p in written)
    summary_path = next(p for p in written if p.startswith("wiki/sources/"))
    summary = (project_root / summary_path).read_text(encoding="utf-8")
    assert summary.startswith("---\n")
    assert 'sources: ["test-paper.md"]' in summary

    entity = (project_root / "wiki/entities/foo-system.md").read_text(encoding="utf-8")
    assert "type: entity" in entity
    assert "[[kv-caching]]" in entity

    # Deterministic log + index
    log = (project_root / "wiki/log.md").read_text(encoding="utf-8")
    assert "ingest | test-paper.md" in log
    index = (project_root / "wiki/index.md").read_text(encoding="utf-8")
    assert "## Recently Updated" in index
    assert "foo-system" in index

    # Review persisted with the stable JS-compatible id
    reviews = json.loads(
        (project_root / ".llm-wiki" / "review.json").read_text(encoding="utf-8")
    )
    assert len(reviews) == 1
    assert reviews[0]["id"].startswith("review-")
    assert reviews[0]["resolved"] is False
    assert reviews[0]["searchQueries"] == [
        "kv caching database internals",
        "key value store cache design",
    ]

    # Cache entry written (no hard failures)
    entries = cache.read_cache_entries(wiki_project.path)
    assert "test-paper.md" in entries
    assert entries["test-paper.md"]["filesWritten"]

    # Second run: cache hit, zero LLM calls
    transport2 = _mock_openai_transport([])
    http_client2 = httpx.AsyncClient(transport=transport2)

    async def run_cached():
        written2 = await pipeline.process_task(
            wiki_project.path, _task("raw/sources/test-paper.md"),
            llm_config=LLM_CONFIG, http_client=http_client2,
        )
        await http_client2.aclose()
        return written2

    written2 = asyncio.run(run_cached())
    assert written2 == written


def test_fallback_summary_when_model_omits_source_page(wiki_project, tmp_path):
    generation_without_summary = GENERATION_RESPONSE.replace(
        "---FILE: wiki/sources/test-paper.md---", "---FILE: wiki/nope---"
    )
    transport = _mock_openai_transport([ANALYSIS_RESPONSE, generation_without_summary, ""])
    http_client = httpx.AsyncClient(transport=transport)

    async def run():
        written = await pipeline.process_task(
            wiki_project.path, _task("raw/sources/test-paper.md"),
            llm_config=LLM_CONFIG, http_client=http_client,
        )
        await http_client.aclose()
        return written

    written = asyncio.run(run())
    project_root = tmp_path / "ingest-demo"
    summary_paths = [p for p in written if p.startswith("wiki/sources/")]
    assert len(summary_paths) == 1
    fallback = (project_root / summary_paths[0]).read_text(encoding="utf-8")
    assert "type: source" in fallback
    assert "## Key Entities" in fallback  # analysis preserved in recovery page


def test_queue_worker_runs_pipeline(wiki_project, tmp_path):
    transport = _mock_openai_transport([ANALYSIS_RESPONSE, GENERATION_RESPONSE, ""])
    http_client = httpx.AsyncClient(transport=transport)

    async def run():
        q = ingest_queue.get_queue(wiki_project.path)
        q.processor = lambda pp, task: pipeline.process_task(
            pp, task, llm_config=LLM_CONFIG, http_client=http_client
        )
        await q.enqueue("raw/sources/test-paper.md")
        # Drive the worker loop manually until the task is done.
        for _ in range(5):
            await q._worker_loop_once()  # noqa: SLF001
            if q.tasks[0].status == "done":
                break
        await http_client.aclose()
        return q

    q = asyncio.run(run())
    assert q.tasks[0].status == "done"
    assert (tmp_path / "ingest-demo" / "wiki" / "entities" / "foo-system.md").exists()
