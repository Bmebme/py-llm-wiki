"""Review persistence — .llm-wiki/review.json.

Mirrors the desktop app's discipline (persist.ts + api_server.rs review
handlers): reads sanitize (recompute ids, merge same-id), writes
preserve the raw array (unknown fields survive a PATCH round-trip);
PATCHes mutate only the targeted item.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from backend.core.file_service import FsError
from backend.review.models import (
    merge_review_items,
    normalize_review_items,
    review_id_for,
    union_field,
)

REVIEW_FILE = "review.json"


def _path(project_path: str) -> Path:
    return Path(project_path) / ".llm-wiki" / REVIEW_FILE


def load_reviews(project_path: str) -> list[dict]:
    """Sanitized read: ids recomputed from content, same-id items merged."""
    try:
        raw = json.loads(_path(project_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return normalize_review_items(raw)


def _load_raw(project_path: str) -> list[dict]:
    """Raw array as written (may contain legacy ids / unknown fields)."""
    try:
        raw = json.loads(_path(project_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return raw if isinstance(raw, list) else []


def _save_raw(project_path: str, items: list[dict]) -> None:
    path = _path(project_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def add_review_items(project_path: str, incoming: list[dict]) -> list[dict]:
    """Port of useReviewStore.addItems: dedup on stable id against ALL
    existing items (resolved wins), array fields merged, new items get
    resolved:false + createdAt."""
    result = _load_raw(project_path)
    index_by_id = {str(it.get("id")): idx for idx, it in enumerate(result)}

    for item in incoming:
        item_id = review_id_for(item.get("type", "confirm"), item.get("title", ""))
        existing_idx = index_by_id.get(item_id)
        if existing_idx is not None:
            old = result[existing_idx]
            # addItems inline merge (review-store.ts): old fields
            # preserved (resolved/resolvedAction/createdAt/id), but a
            # non-empty INCOMING description wins, array fields union.
            result[existing_idx] = {
                **old,
                "description": item.get("description") or old.get("description"),
                "sourcePath": item.get("sourcePath")
                if item.get("sourcePath") is not None
                else old.get("sourcePath"),
                "affectedPages": union_field(old.get("affectedPages"), item.get("affectedPages")),
                "searchQueries": union_field(old.get("searchQueries"), item.get("searchQueries")),
            }
            for key in ("sourcePath", "affectedPages", "searchQueries"):
                if result[existing_idx].get(key) is None:
                    result[existing_idx].pop(key, None)
        else:
            new_item = {
                **item,
                "id": item_id,
                "resolved": False,
                "createdAt": int(time.time() * 1000),
            }
            result.append(new_item)
            index_by_id[item_id] = len(result) - 1

    _save_raw(project_path, result)
    return normalize_review_items(result)


def patch_review(project_path: str, review_id: str, resolved: bool | None, action: str | None) -> dict | None:
    """Port of api_server.rs handle_patch_review: empty body = resolve.
    Writes the raw array back with only the targeted item changed."""
    items = _load_raw(project_path)
    for item in items:
        if item.get("id") == review_id:
            if resolved is not None:
                item["resolved"] = resolved
            if resolved:
                if action is not None:
                    item["resolvedAction"] = action
            elif not resolved and "resolvedAction" in item:
                item.pop("resolvedAction", None)
            _save_raw(project_path, items)
            return normalize_review_items([item])[0]
    return None


def bulk_resolve(project_path: str, ids: list[str], action: str | None) -> dict:
    """Port of handle_bulk_resolve_reviews: partial success = 200."""
    items = _load_raw(project_path)
    resolved: list[str] = []
    for item in items:
        if item.get("id") in ids:
            item["resolved"] = True
            if action is not None:
                item["resolvedAction"] = action
            resolved.append(item["id"])
    if resolved:
        _save_raw(project_path, items)
    return {
        "resolved": resolved,
        "notFound": [i for i in ids if i not in resolved],
        "count": len(resolved),
    }
