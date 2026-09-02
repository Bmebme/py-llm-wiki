"""Two-step CoT ingest pipeline — port of llm_wiki src/lib/ingest.ts
autoIngestImpl (659-1451) + writeFileBlocks (1868-2049).

v0 simplifications (documented):
- Long sources are truncated to the ingest source budget instead of the
  desktop's semantic chunking (that stays a future enhancement).
- Page merging uses the deterministic layer only (array union + locked
  fields + owned-source body replacement); the LLM body merger is a
  future seam in backend/wiki/page_merge.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from backend.core import settings_store
from backend.core.context_budget import compute_context_budget
from backend.core.file_service import FsError, read_text, write_text
from backend.ingest import cache, queue as ingest_queue
from backend.ingest.extract_text import extract_text
from backend.ingest.parse_blocks import parse_file_blocks, parse_review_blocks
from backend.ingest.postprocess import (
    build_deterministic_ingest_log,
    build_fallback_source_summary,
    canonicalize_sources_field,
    stamp_generated_frontmatter_dates,
    stamp_generated_log_date,
)
from backend.ingest.prompts import (
    build_analysis_prompt,
    build_generation_prompt,
    build_review_suggestion_prompt,
    trim_long_text,
)
from backend.ingest.queue import IngestCancelled, UsageLimitError
from backend.ingest.sanitize import sanitize_ingested_file_content
from backend.ingest.sources import folder_context_for_source_path
from backend.review import store as review_store
from backend.wiki.frontmatter import parse_frontmatter
from backend.wiki.index_log import (
    append_ingest_log,
    update_wiki_index_deterministically,
)
from backend.wiki.page_merge import merge_page_content_deterministic
from backend.wiki.source_identity import (
    source_identity_for_path,
    source_reference_identity,
    source_summary_slug_from_identity,
)
from backend.wiki.sources_merge import parse_sources

AGGREGATE_WIKI_PATHS = ["wiki/index.md", "wiki/overview.md", "wiki/log.md"]

INGEST_GENERATION_TOKENS_DEFAULT = 8_192
INGEST_GENERATION_TOKENS_128K = 16_384
INGEST_GENERATION_TOKENS_256K = 24_576
INGEST_GENERATION_TOKENS_512K = 32_768
LONG_SOURCE_MIN_BUDGET = 8_000
LONG_SOURCE_MAX_SINGLE_PASS_BUDGET = 300_000


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def compute_ingest_source_budget(max_context_size: int | None, stable_context_length: int) -> int:
    """Port of computeIngestSourceBudget (ingest.ts:2551-2558)."""
    max_ctx = compute_context_budget(max_context_size)["maxCtx"]
    stable_reserve = min(int(max_ctx * 0.25), max(12_000, stable_context_length))
    instruction_reserve = max(12_000, int(max_ctx * 0.08))
    available = max_ctx - int(max_ctx * 0.15) - stable_reserve - instruction_reserve
    upper = min(LONG_SOURCE_MAX_SINGLE_PASS_BUDGET, max(LONG_SOURCE_MIN_BUDGET, int(max_ctx * 0.6)))
    return _clamp(int(available), LONG_SOURCE_MIN_BUDGET, upper)


def compute_ingest_generation_max_tokens(max_context_size: int | None) -> int:
    max_ctx = compute_context_budget(max_context_size)["maxCtx"]
    if max_ctx >= 512_000:
        return INGEST_GENERATION_TOKENS_512K
    if max_ctx >= 256_000:
        return INGEST_GENERATION_TOKENS_256K
    if max_ctx >= 128_000:
        return INGEST_GENERATION_TOKENS_128K
    return INGEST_GENERATION_TOKENS_DEFAULT


def compute_ingest_review_max_tokens(max_context_size: int | None) -> int:
    return min(8_192, max(4_096, compute_ingest_generation_max_tokens(max_context_size) // 2))


def is_owned_only_by_source(content: str, source_identity: str) -> bool:
    """Port of isOwnedOnlyBySource (ingest.ts:2060-2067)."""
    sources = parse_sources(content)
    if not sources:
        return False
    expected = source_reference_identity(source_identity).lower()
    return all(
        source_reference_identity(source).lower() == expected for source in sources
    )


def _resolve_llm_config(explicit: dict | None) -> dict:
    if explicit is not None:
        return explicit
    state = settings_store.load() or {}
    cfg = state.get("llmConfig")
    if not isinstance(cfg, dict) or not cfg.get("provider"):
        raise FsError(
            "No LLM provider configured — set one in Settings before ingesting."
        )
    return cfg


class IngestPipeline:
    """Stateful per-call pipeline with injectable LLM client for tests."""

    def __init__(self, project_path: str, llm_config: dict | None = None, http_client=None):
        self.project_path = project_path.rstrip("/")
        self.llm_config = llm_config
        self.http_client = http_client
        self.warnings: list[str] = []
        self.hard_failures: list[str] = []
        self.truncated_paths: list[str] = []
        self.written_paths: list[str] = []

    # --- LLM helpers ------------------------------------------------------

    async def _generate(self, messages: list[dict], overrides: dict | None, max_tokens: int) -> str:
        from backend.llm.client import generate_text
        from backend.llm.providers import get_provider_config

        config = _resolve_llm_config(self.llm_config)
        provider_config = get_provider_config(config)
        return await generate_text(
            messages,
            config,
            provider_config,
            max_tokens=max_tokens,
            overrides=overrides,
            client=self.http_client,
        )

    def _check_cancelled(self) -> None:
        queue = ingest_queue.get_queue(self.project_path)
        if queue._cancel_requested.is_set():  # noqa: SLF001 - same package family
            raise IngestCancelled()

    # --- main flow ----------------------------------------------------------

    async def run(self, task: ingest_queue.IngestTask) -> list[str]:
        self._check_cancelled()
        source_path = Path(self.project_path) / task.sourcePath
        if not source_path.exists():
            raise FsError(f"Source file does not exist: {task.sourcePath}")

        source_identity = source_identity_for_path(self.project_path, task.sourcePath)
        source_content = extract_text(str(source_path))

        # Incremental cache: unchanged sources with all written files
        # still on disk skip the LLM round-trips entirely.
        cached = cache.check_ingest_cache(self.project_path, source_identity, source_content)
        if cached is not None:
            return cached

        schema = self._read_optional("schema.md")
        purpose = self._read_optional("purpose.md")
        index = self._read_optional("wiki/index.md") or "# Wiki Index\n"
        overview = self._read_optional("wiki/overview.md")

        max_ctx = self.llm_config.get("maxContextSize") if self.llm_config else None
        stable_length = len(schema) + len(purpose) + len(index) + len(overview)
        source_budget = compute_ingest_source_budget(max_ctx, stable_length)
        source_context = trim_long_text(source_content, source_budget)

        folder_context = (
            folder_context_for_source_path(task.sourcePath)
            if task.sourcePath.startswith("raw/sources/")
            else None
        )

        summary_path = f"wiki/sources/{source_summary_slug_from_identity(source_identity)}.md"

        # ── Step 1: Analysis ────────────────────────────────────────
        self._check_cancelled()
        analysis = await self._generate(
            [
                {
                    "role": "system",
                    "content": build_analysis_prompt(purpose, index, source_context, schema),
                },
                {
                    "role": "user",
                    "content": (
                        f"Analyze this source document:\n\n**File:** {source_identity}"
                        f"{f'\n**Folder context:** {folder_context}' if folder_context else ''}"
                        f"\n\n---\n\n{source_context}"
                    ),
                },
            ],
            {"temperature": 0.1, "reasoning": {"mode": "off"}},
            max_tokens=4096,
        )

        # ── Step 2: Generation ──────────────────────────────────────
        self._check_cancelled()
        generation = await self._generate(
            [
                {
                    "role": "system",
                    "content": build_generation_prompt(
                        schema, purpose, index, source_identity, overview,
                        source_context, summary_path,
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Source document to process: **{source_identity}**\n\n"
                        "The Stage 1 analysis below is CONTEXT to inform your output. Do NOT echo\n"
                        "its tables, bullet points, or prose. Your output must be FILE/REVIEW\n"
                        "blocks as specified in the system prompt — nothing else.\n\n"
                        "## Stage 1 Analysis (context only — do not repeat)\n\n"
                        f"{analysis}\n\n"
                        "## Source Context\n\n"
                        f"{source_context}\n\n"
                        "---\n\n"
                        f"Now emit the FILE blocks for the wiki files derived from **{source_identity}**.\n"
                        "Your response MUST begin with `---FILE:` as the very first characters.\n"
                        "No preamble. No analysis prose. Start immediately."
                    ),
                },
            ],
            {"temperature": 0.1, "reasoning": {"mode": "off"}},
            max_tokens=compute_ingest_generation_max_tokens(max_ctx),
        )

        # ── Write phase ─────────────────────────────────────────────
        self._write_blocks(generation, source_identity, summary_path)
        if not any(
            p.startswith("wiki/sources/") for p in self.written_paths
        ) and not self.hard_failures:
            self._write_fallback_summary(source_identity, analysis, summary_path)

        # Deterministic log entry when the model omitted one.
        if not any(p == "wiki/log.md" for p in self.written_paths):
            from backend.wiki.index_log import current_wiki_date

            append_ingest_log(
                self.project_path, source_identity, current_wiki_date()
            )
        update_wiki_index_deterministically(self.project_path, self.written_paths)

        # ── Review items ────────────────────────────────────────────
        review_items = parse_review_blocks(generation, source_identity)
        block_count = len(parse_file_blocks(generation).blocks)
        if len(generation) >= 10_000 or block_count >= 4:
            self._check_cancelled()
            review_prompt = build_review_suggestion_prompt(
                purpose, index, source_identity, analysis,
                source_context, generation, max_ctx,
            )
            try:
                extra = await self._generate(
                    [{"role": "user", "content": review_prompt}],
                    {"temperature": 0.1, "reasoning": {"mode": "off"}},
                    max_tokens=compute_ingest_review_max_tokens(max_ctx),
                )
                review_items.extend(parse_review_blocks(extra, source_identity))
            except FsError:
                # The review stage is best-effort: the prompt explicitly
                # allows an empty output ("If there is nothing worth
                # reviewing, output nothing") and a failed extra stage
                # must not fail the whole ingest.
                self.warnings.append(
                    "Dedicated review stage produced no usable output; "
                    "skipping extra review suggestions."
                )
        if review_items:
            review_store.add_review_items(self.project_path, review_items)

        # Cache entry only when the ingest had zero hard failures and no
        # truncated FILE blocks (ingest.ts:1399-1412).
        if not self.hard_failures and not self.truncated_paths:
            cache.save_ingest_cache(
                self.project_path, source_identity, source_content, self.written_paths
            )
        return self.written_paths

    # --- write phase --------------------------------------------------------

    def _read_optional(self, rel: str) -> str:
        path = Path(self.project_path) / rel
        try:
            return read_text(path)
        except FsError:
            return ""

    def _write_blocks(self, generation: str, source_identity: str, summary_path: str) -> None:
        import os

        if os.environ.get("PY_LLM_WIKI_DEBUG_GENERATIONS"):
            Path("/tmp/py-llm-wiki-debug-generation.txt").write_text(
                generation, encoding="utf-8"
            )
        parsed = parse_file_blocks(generation)
        self.warnings.extend(parsed.warnings)
        self.truncated_paths = parsed.truncated_paths
        from backend.wiki.index_log import current_wiki_date

        today = current_wiki_date()

        for block in parsed.blocks:
            self._check_cancelled()
            relative_path = block.path
            if relative_path.startswith("wiki/sources/"):
                relative_path = summary_path
            if relative_path in AGGREGATE_WIKI_PATHS:
                self.warnings.append(
                    f'Ignored model-generated "{relative_path}"; aggregate '
                    "navigation is maintained by the application."
                )
                continue

            content = sanitize_ingested_file_content(block.content)
            if relative_path == "wiki/log.md":
                content = stamp_generated_log_date(content, today)
            elif relative_path != "wiki/index.md":
                content = stamp_generated_frontmatter_dates(content, today)
            if relative_path not in ("wiki/log.md", "wiki/index.md"):
                content = canonicalize_sources_field(content, source_identity)

            full_path = Path(self.project_path) / relative_path
            try:
                if relative_path == "wiki/log.md":
                    existing = self._try_read(relative_path)
                    appended = f"{existing}\n\n{content.strip()}" if existing else content.strip()
                    write_text(full_path, appended)
                elif relative_path == "wiki/index.md":
                    write_text(full_path, content)
                else:
                    existing = self._try_read(relative_path)
                    replace_body = bool(existing and is_owned_only_by_source(existing, source_identity))
                    merged = merge_page_content_deterministic(
                        content, existing, source_identity, replace_body, today
                    )
                    to_write = canonicalize_sources_field(merged, source_identity)
                    write_text(full_path, to_write)
                self.written_paths.append(relative_path)
            except FsError as exc:
                message = f'Failed to write "{relative_path}": {exc}'
                self.warnings.append(message)
                self.hard_failures.append(relative_path)

    def _try_read(self, rel: str) -> str | None:
        try:
            return read_text(Path(self.project_path) / rel)
        except FsError:
            return None

    def _write_fallback_summary(self, source_identity: str, analysis: str, summary_path: str) -> None:
        from backend.wiki.index_log import current_wiki_date

        content = build_fallback_source_summary(
            source_identity, analysis, current_wiki_date()
        )
        try:
            write_text(Path(self.project_path) / summary_path, content)
            self.written_paths.append(summary_path)
        except FsError as exc:
            self.hard_failures.append(summary_path)
            self.warnings.append(f'Failed to write "{summary_path}": {exc}')


async def process_task(
    project_path: str, task: ingest_queue.IngestTask,
    llm_config: dict | None = None, http_client=None,
) -> list[str]:
    """Queue-processor entry point: run the full ingest for one task."""
    pipeline = IngestPipeline(project_path, llm_config, http_client)
    return await pipeline.run(task)


def bind_queue_processors(
    llm_config: dict | None = None, http_client=None
) -> None:
    """Attach the pipeline to every existing queue and future queues.

    Called at app startup (lifespan) and by tests with mocks.
    """
    for queue in ingest_queue._registry.values():  # noqa: SLF001
        queue.processor = lambda pp, task: process_task(pp, task, llm_config, http_client)
    # Future queues get it lazily through get_queue + bind at startup.
    ingest_queue._DEFAULT_PROCESSOR = (  # noqa: SLF001
        lambda pp, task: process_task(pp, task, llm_config, http_client)
    )
