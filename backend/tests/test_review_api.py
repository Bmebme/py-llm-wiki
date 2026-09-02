"""Review endpoints (19828 contract) + create-page + sweep — mirrors
llm_wiki's api_server.rs review tests and sweep-reviews.test.ts."""

import json

import pytest
from fastapi.testclient import TestClient

from backend.core import project as project_core
from backend.core import project_registry
from backend.main import app
from backend.review import store as review_store

client = TestClient(app)


@pytest.fixture()
def review_project(tmp_path):
    project = project_core.create_project("review-demo", str(tmp_path))
    project_core.ensure_project_id(project.path)
    project_registry.register(project.id, project.name, project.path)
    return project


def _add_review(project, **overrides):
    item = {
        "type": "suggestion",
        "title": "Research topic X",
        "description": "A gap worth studying.",
        "options": [{"label": "Create Page", "action": "Create Page"},
                    {"label": "Skip", "action": "Skip"}],
    }
    item.update(overrides)
    return review_store.add_review_items(project.path, [item])[0]


def test_get_reviews_default_unresolved(review_project):
    added = _add_review(review_project)
    review_store.patch_review(review_project.path, added["id"], True, "label")
    _add_review(review_project, title="Other topic")

    resp = client.get("/api/v1/projects/current/reviews")
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == "unresolved"
    assert body["count"] == 1
    assert body["reviews"][0]["title"] == "Other topic"


def test_get_reviews_filter_type_and_limit(review_project):
    _add_review(review_project, type="contradiction", title="X vs Y")
    _add_review(review_project, type="suggestion", title="Another")

    resp = client.get("/api/v1/projects/current/reviews?type=contradiction")
    assert resp.json()["count"] == 1
    assert resp.json()["reviews"][0]["type"] == "contradiction"

    resp = client.get("/api/v1/projects/current/reviews?limit=1")
    assert resp.json()["count"] == 1

    resp = client.get("/api/v1/projects/current/reviews?status=bogus")
    assert resp.status_code == 400


def test_patch_review_resolve_and_reopen(review_project):
    added = _add_review(review_project)

    resp = client.patch(
        f"/api/v1/projects/current/reviews/{added['id']}", json={"resolved": True, "action": "Skip"}
    )
    assert resp.status_code == 200
    assert resp.json()["resolved"] is True

    resp = client.patch(
        f"/api/v1/projects/current/reviews/{added['id']}", json={"resolved": False}
    )
    assert resp.json()["resolved"] is False
    items = review_store.load_reviews(review_project.path)
    assert "resolvedAction" not in items[0]

    resp = client.patch("/api/v1/projects/current/reviews/review-nope", json={})
    assert resp.status_code == 404


def test_bulk_resolve(review_project):
    a = _add_review(review_project, title="Topic A")
    b = _add_review(review_project, title="Topic B")

    resp = client.post(
        "/api/v1/projects/current/reviews/resolve",
        json={"ids": [a["id"], "review-missing"], "action": "label"},
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["resolved"] == [a["id"]]
    assert body["notFound"] == ["review-missing"]
    assert body["count"] == 1

    resp = client.post("/api/v1/projects/current/reviews/resolve", json={"ids": []})
    assert resp.status_code == 400


def test_create_page_drafts(review_project):
    from backend.review.create_page import create_review_page_drafts

    item = {"type": "missing-page", "title": "Missing page: Attention Mechanism",
            "description": "Missing entity and concept pages for attention."}
    drafts = create_review_page_drafts(item, "Create Page")
    # TS parity: ENTITY_RE is checked before CONCEPT_RE, so a description
    # mentioning both types yields entity drafts.
    assert any(d["pageType"] == "entity" for d in drafts)
    assert all(d["dir"] == "entities" for d in drafts)

    item_bilingual = {"type": "missing-page", "title": "缺失页面：知识图谱",
                      "description": "缺少知识图谱页面"}
    drafts2 = create_review_page_drafts(item_bilingual, "Create Page")
    titles = [d["title"] for d in drafts2]
    assert any("知识图谱" in t for t in titles)


def test_write_drafts_updates_wiki(review_project):
    from backend.review.create_page import write_review_page_drafts

    item = {"type": "missing-page", "title": "Missing page: Attention Mechanism",
            "description": "Attention pages are missing."}
    written = write_review_page_drafts(review_project.path, item, "Create Page")
    assert written
    page_path = review_project.path
    from pathlib import Path

    for rel in written:
        assert (Path(page_path) / rel).exists()
    index = (Path(page_path) / "wiki/index.md").read_text(encoding="utf-8")
    assert "## Recently Updated" in index


class TestSweepStage1:
    def test_missing_page_resolved_when_page_exists(self, review_project):
        from pathlib import Path

        from backend.review.sweep import sweep_resolved_reviews

        _add_review(review_project, type="missing-page", title="Attention Mechanism")
        project_root = Path(review_project.path)
        (project_root / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
        (project_root / "wiki" / "concepts" / "attention-mechanism.md").write_text(
            "---\ntype: concept\ntitle: Attention Mechanism\n---\n\n# Attention Mechanism\n",
            encoding="utf-8",
        )
        result = sweep_resolved_reviews(review_project.path)
        assert result["ruleResolved"] == 1
        assert result["total"] == 1
        reviews = review_store.load_reviews(review_project.path)
        assert reviews[0]["resolved"] is True
        assert reviews[0]["resolvedAction"] == "auto-resolved"

    def test_duplicate_resolved_when_page_gone(self, review_project):
        from backend.review.sweep import sweep_resolved_reviews

        _add_review(
            review_project,
            type="duplicate",
            title="Duplicate page: Foo",
            affectedPages=["wiki/concepts/foo.md"],
        )
        result = sweep_resolved_reviews(review_project.path)
        assert result["ruleResolved"] == 1

    def test_empty_queue(self, review_project):
        from backend.review.sweep import sweep_resolved_reviews

        result = sweep_resolved_reviews(review_project.path)
        assert result["total"] == 0
