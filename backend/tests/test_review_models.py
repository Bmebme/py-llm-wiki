"""Review id stability — golden vectors generated from llm_wiki's JS
implementation (src/stores/review-store.ts reviewIdFor)."""

import time

from backend.review import store
from backend.review.models import (
    merge_review_items,
    normalize_review_title_full,
    review_id_for,
)

GOLDEN_IDS = [
    (("missing-page", "Attention is all you need"), "review-72945afd"),
    (("missing-page", "Missing page: Chain-of-Thought"), "review-1475d96c"),
    (("contradiction", "ViT vs CNN for segmentation"), "review-11f690f8"),
    (("duplicate", "重复页面:  知识图谱  "), "review-955a9fc4"),
    (("confirm", "Scaling Laws"), "review-1b6b7e69"),
    (("suggestion", "emoji 🚀 test"), "review-daf2dd7e"),
]


def test_review_ids_match_js_golden_vectors():
    for (review_type, title), expected in GOLDEN_IDS:
        assert review_id_for(review_type, title) == expected


def test_normalize_title_matches_js():
    assert normalize_review_title_full("Missing page: Chain-of-Thought") == "chain-of-thought"
    assert normalize_review_title_full(" 缺失页面：  Foo  ") == "foo"
    assert normalize_review_title_full("Possible Duplicate: X") == "x"
    assert normalize_review_title_full("plain title") == "plain title"


def test_merge_review_items_resolved_wins():
    now = int(time.time() * 1000)
    old = {
        "id": "review-x", "type": "suggestion", "title": "T",
        "description": "old desc", "options": [{"label": "Skip", "action": "Skip"}],
        "resolved": True, "resolvedAction": "label", "createdAt": now - 5,
    }
    new = {
        "id": "review-x", "type": "suggestion", "title": "T",
        "description": "new desc",
        "options": [{"label": "Create Page", "action": "Create Page"}],
        "resolved": False, "createdAt": now,
    }
    merged = merge_review_items(old, new)
    assert merged["resolved"] is True
    assert merged["resolvedAction"] == "label"
    # JS mergeReviewItems prefers the FIRST (a) non-empty description;
    # the addItems path (store.add_review_items) prefers incoming instead.
    assert merged["description"] == "old desc"
    assert merged["createdAt"] == now - 5  # earliest kept
    assert len(merged["options"]) == 2  # options merged by action


def test_store_resolved_state_survives_reingest(tmp_path):
    incoming = {
        "type": "suggestion",
        "title": "Research X",
        "description": "gap",
        "options": [{"label": "Create Page", "action": "Create Page"},
                    {"label": "Skip", "action": "Skip"}],
        "sourcePath": "raw/sources/a.md",
    }
    first = store.add_review_items(str(tmp_path), [incoming])
    review_id = first[0]["id"]

    # resolve it
    patched = store.patch_review(str(tmp_path), review_id, True, "label")
    assert patched["resolved"] is True

    # re-ingest the same content — resolved state must survive
    again = store.add_review_items(str(tmp_path), [incoming])
    assert len(again) == 1
    assert again[0]["id"] == review_id
    assert again[0]["resolved"] is True
    assert again[0]["resolvedAction"] == "label"


def test_store_bulk_resolve(tmp_path):
    items = [
        {"type": "suggestion", "title": "A topic", "description": "d",
         "options": []},
        {"type": "suggestion", "title": "B topic", "description": "d",
         "options": []},
    ]
    added = store.add_review_items(str(tmp_path), items)
    ids = [i["id"] for i in added]
    result = store.bulk_resolve(str(tmp_path), [ids[0], "review-missing"], "label")
    assert result["resolved"] == [ids[0]]
    assert result["notFound"] == ["review-missing"]
    assert result["count"] == 1

    loaded = store.load_reviews(str(tmp_path))
    by_id = {i["id"]: i for i in loaded}
    assert by_id[ids[0]]["resolved"] is True
    assert by_id[ids[1]]["resolved"] is False


def test_store_unknown_fields_survive_roundtrip(tmp_path):
    incoming = {
        "type": "contradiction", "title": "X vs Y", "description": "d",
        "options": [],
        "customFutureField": {"keep": True},
    }
    store.add_review_items(str(tmp_path), [incoming])
    raw_path = tmp_path / ".llm-wiki" / "review.json"
    import json

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw[0]["customFutureField"] == {"keep": True}
