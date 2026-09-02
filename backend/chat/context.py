"""Chat context assembly — port of the context budget + the Rust
agent's context.rs caps (v0 single-round flavor).

v0 simplification (documented): the tool-loop planner is absent; the
context is assembled once from purpose/schema/index/top-pages/history
and streamed to the model. Cap values follow the Rust context.rs
defaults: overview 8k, schema 6k, history 12k.
"""

from __future__ import annotations

from backend.core.context_budget import compute_context_budget

CAP_PURPOSE = 8_000
CAP_SCHEMA = 6_000
CAP_HISTORY = 12_000


def _cap(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n\n[...trimmed for prompt budget...]"


def build_chat_system_prompt(
    purpose: str,
    schema: str,
    index: str,
    language_directive: str,
    max_context_size: int | None,
) -> str:
    budget = compute_context_budget(max_context_size)
    index_budget = int(budget["indexBudget"])
    return "\n\n".join(
        filter(None, [
            "You are a knowledgeable assistant for this personal wiki. "
            "Answer the user's question using the provided wiki pages as "
            "primary evidence.",
            language_directive,
            "Citation rule: cite wiki pages by their number in square "
            "brackets, e.g. [1], [2]. Cite only pages you actually used.",
            f"## Wiki Purpose\n{_cap(purpose, CAP_PURPOSE)}" if purpose else "",
            f"## Wiki Schema\n{_cap(schema, CAP_SCHEMA)}" if schema else "",
            f"## Wiki Index\n{_cap(index, index_budget)}" if index else "",
        ])
    )


def build_chat_user_content(
    question: str,
    pages: list[dict],
    history: list[dict],
    page_budget: int,
    max_page_size: int,
) -> str:
    """Numbered pages with full content, history folded in. Pages are
    prioritized by combined search + graph relevance (already sorted by
    the search engine)."""
    sections: list[str] = []
    if history:
        history_lines = []
        for message in history:
            role = "User" if message["role"] == "user" else "Assistant"
            history_lines.append(f"{role}: {_cap(str(message['content']), 4000)}")
        sections.append("## Conversation History\n" + "\n\n".join(history_lines))

    page_sections: list[str] = []
    used_budget = 0
    for index, page in enumerate(pages, start=1):
        if used_budget >= page_budget:
            break
        content = _cap(page.get("content") or "", max_page_size)
        if not content.strip():
            continue
        used_budget += len(content)
        title = page.get("title") or page["path"]
        path = page["path"]
        page_sections.append(f"### [{index}] {title}\n(path: {path})\n\n{content}")
    if page_sections:
        sections.append("## Wiki Pages\n" + "\n\n".join(page_sections))
    else:
        sections.append("## Wiki Pages\n(No matching wiki pages were found.)")

    sections.append(f"## Question\n{question}")
    return "\n\n".join(sections)
