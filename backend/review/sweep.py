"""Review sweep — port of llm_wiki src/lib/sweep-reviews.ts stage 1.

Stage 1 (deterministic, always runs):
- missing-page resolved when a candidate name now exists in the wiki
- duplicate resolved when any affected page no longer exists
Stage 2 (LLM judge, optional): seam left for a future enhancement —
callers can pass a judge function that returns resolved ids.
"""

from __future__ import annotations

from pathlib import Path

from backend.review import store as review_store
from backend.review.models import normalize_review_title_full
from backend.wiki.frontmatter import parse_frontmatter


class WikiIndex:
    def __init__(self):
        self.by_id: set[str] = set()     # lowercased file stems
        self.by_title: set[str] = set()  # lowercased frontmatter titles

    def has_page(self, name: str) -> bool:
        normalized = name.strip().lower()
        if not normalized:
            return False
        if normalized in self.by_id:
            return True
        if normalized.replace(" ", "-") in self.by_id:
            return True
        if normalized in self.by_title:
            return True
        return False


def build_wiki_index(project_path: str) -> WikiIndex:
    index = WikiIndex()
    wiki_root = Path(project_path) / "wiki"
    if not wiki_root.exists():
        return index
    for entry in wiki_root.rglob("*.md"):
        if not entry.is_file():
            continue
        index.by_id.add(entry.stem.lower())
        try:
            content = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        parsed = parse_frontmatter(content)
        frontmatter = parsed.frontmatter or {}
        title = frontmatter.get("title")
        if isinstance(title, str) and title.strip():
            index.by_title.add(title.strip().lower())
    return index


def extract_candidate_names(item: dict) -> list[str]:
    names: set[str] = set()
    cleaned = normalize_review_title_full(item.get("title", ""))
    if cleaned and len(cleaned) <= 100:
        names.add(cleaned)
    for page in item.get("affectedPages") or []:
        base = str(page).split("/")[-1].removesuffix(".md")
        if base:
            names.add(base.lower())
    return list(names)


def _page_stem_exists(stem: str, index: WikiIndex) -> bool:
    return bool(stem) and stem in index.by_id


def sweep_resolved_reviews(
    project_path: str, llm_judge=None
) -> dict:
    """Stage 1 rules + optional stage 2 judge. Returns a summary dict:
    {ruleResolved, llmResolved, total, detail}."""
    pending = [i for i in review_store.load_reviews(project_path) if not i.get("resolved")]
    if not pending:
        return {"ruleResolved": 0, "llmResolved": 0, "total": 0,
                "detail": "No stale review items to clean up"}

    index = build_wiki_index(project_path)
    rule_resolved = 0
    still_pending: list[dict] = []

    for item in pending:
        resolved_by_rule = False
        if item.get("type") == "missing-page":
            names = extract_candidate_names(item)
            if names and any(index.has_page(name) for name in names):
                review_store.patch_review(project_path, item["id"], True, "auto-resolved")
                rule_resolved += 1
                resolved_by_rule = True
        elif item.get("type") == "duplicate":
            affected = item.get("affectedPages") or []
            if affected:
                all_still_exist = all(
                    _page_stem_exists(str(p).split("/")[-1].removesuffix(".md").lower(), index)
                    for p in affected
                )
                if not all_still_exist:
                    review_store.patch_review(project_path, item["id"], True, "auto-resolved")
                    rule_resolved += 1
                    resolved_by_rule = True
        if not resolved_by_rule:
            still_pending.append(item)

    llm_resolved = 0
    if still_pending and llm_judge is not None:
        resolved_ids = llm_judge(still_pending, index)
        for review_id in resolved_ids:
            if review_store.patch_review(project_path, review_id, True, "llm-judged"):
                llm_resolved += 1

    total = rule_resolved + llm_resolved
    parts = []
    if rule_resolved:
        parts.append(f"{rule_resolved} by rules")
    if llm_resolved:
        parts.append(f"{llm_resolved} by LLM")
    detail = (
        f"Auto-resolved {total} stale review item{'' if total == 1 else 's'} ({', '.join(parts)})"
        if total
        else "No stale review items to clean up"
    )
    return {
        "ruleResolved": rule_resolved,
        "llmResolved": llm_resolved,
        "total": total,
        "detail": detail,
    }
