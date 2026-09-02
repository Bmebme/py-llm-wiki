"""Create-Page action for review items — port of llm_wiki
src/lib/review-create-page.ts."""

from __future__ import annotations

import re

ACTION_PREFIX_RE = re.compile(
    r"^(Create|Save|Add|Missing page|Missing pages|缺失页面|缺少页面|创建|保存|新增)[:：\s-]*",
    re.IGNORECASE,
)
ENTITY_RE = re.compile(r"\b(entity|entities)\b|实体", re.IGNORECASE)
CONCEPT_RE = re.compile(r"\b(concept|concepts)\b|概念", re.IGNORECASE)


def clean_candidate_title(value: str) -> str:
    return re.sub(
        r"^[\s\"'“”‘’`[\]【】()（）]+|[\s\"'“”‘’`[\]【】()（）:：.。]+$",
        "",
        re.sub(
            r"\s*(entity|entities|concept|concepts|实体|概念)\s*(page|pages|页面|页)?\s*$",
            "",
            re.sub(
                r"\s*(page|pages|页面|页)\s*$",
                "",
                re.sub(
                    r"^(missing|缺失|缺少)\s*",
                    "",
                    ACTION_PREFIX_RE.sub("", value),
                    flags=re.IGNORECASE,
                ),
                flags=re.IGNORECASE,
            ),
            flags=re.IGNORECASE,
        ),
    ).strip()


def split_candidate_list(value: str) -> list[str]:
    value = re.sub(r"\band\b", ",", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+和\s+", ",", value)
    return [
        t for t in (clean_candidate_title(p) for p in re.split(r"[,，、;；\n]+", value))
        if t
    ]


def extract_missing_page_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    segments = [
        re.sub(r"\s+", " ", s).strip()
        for s in re.split(r"[\n。]+", text)
        if re.sub(r"\s+", " ", s).strip()
    ]

    for segment in segments:
        colon_tail = re.search(r"[:：]\s*(.+)$", segment)
        if colon_tail:
            candidates.extend(split_candidate_list(colon_tail.group(1)))

        chinese_missing = re.search(
            r"(?:缺少|缺失|未创建|没有)\s*([^；;]+?)(?:等)?\s*(?:实体|概念)?\s*(?:页面|页)(?:缺失|不存在|未创建)?",
            segment,
            re.IGNORECASE,
        )
        if chinese_missing and chinese_missing.group(1):
            candidates.extend(split_candidate_list(chinese_missing.group(1)))

        english_missing = re.search(
            r"missing\s+(?:entity|entities|concept|concepts|page|pages)?\s*([^.;]+?)(?:\s+pages?|\s+entities?|\s+concepts?)?$",
            segment,
            re.IGNORECASE,
        )
        if english_missing and english_missing.group(1):
            candidates.extend(split_candidate_list(english_missing.group(1)))

    if not candidates:
        candidates.append(clean_candidate_title(segments[0] if segments else "") or "Untitled")

    return list(dict.fromkeys(candidates))


def detect_page_type(action: str, review_type: str, text: str) -> str:
    combined = f"{action}\n{text}"
    if ENTITY_RE.search(combined):
        return "entity"
    if CONCEPT_RE.search(combined):
        return "concept"
    if re.search(r"comparison|compare|比较", combined, re.IGNORECASE):
        return "comparison"
    if re.search(r"synthesis|综合", combined, re.IGNORECASE):
        return "synthesis"
    if review_type == "missing-page":
        return "concept"
    if review_type == "contradiction":
        return "query"
    if review_type == "suggestion":
        return "query"
    return "query"


def dir_for_page_type(page_type: str) -> str:
    return {
        "entity": "entities",
        "concept": "concepts",
        "comparison": "comparisons",
        "synthesis": "synthesis",
        "query": "queries",
    }[page_type]


def create_review_page_drafts(item: dict, action: str) -> list[dict]:
    text = f"{item.get('title', '')}\n{item.get('description', '')}"
    page_type = detect_page_type(action, item.get("type", "suggestion"), text)
    titles = (
        extract_missing_page_candidates(text)
        if item.get("type") == "missing-page"
        else [clean_candidate_title(item.get("title", "")) or "Untitled"]
    )
    return [
        {"title": title, "pageType": page_type, "dir": dir_for_page_type(page_type)}
        for title in titles
    ]


def write_review_page_drafts(project_path: str, item: dict, action: str) -> list[str]:
    """Write the drafted pages into the wiki and return their paths."""
    from datetime import date

    from backend.core.file_service import write_text
    from backend.wiki.index_log import update_wiki_index_deterministically
    from backend.wiki.wiki_filename import make_query_slug

    today = date.today().isoformat()
    drafts = create_review_page_drafts(item, action)
    written: list[str] = []
    for draft in drafts:
        slug = make_query_slug(draft["title"])
        rel = f"wiki/{draft['dir']}/{slug}.md"
        content = "\n".join([
            "---",
            f"type: {draft['pageType']}",
            f"title: {draft['title']}",
            f"created: {today}",
            f"updated: {today}",
            "tags: []",
            "related: []",
            "sources: []",
            "---",
            "",
            f"# {draft['title']}",
            "",
            item.get("description") or "",
            "",
        ])
        write_text(Path(project_path) / rel, content)
        written.append(rel)
    if written:
        update_wiki_index_deterministically(project_path, written)
    return written


from pathlib import Path  # noqa: E402
