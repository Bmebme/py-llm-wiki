"""Cascade deletion — mirrors wiki-page-delete.test.ts and
source-lifecycle.test.ts cases."""

from pathlib import Path

import pytest

from backend.core import project as project_core
from backend.delete.source_lifecycle import delete_source_files
from backend.delete.wiki_cleanup import (
    clean_index_listing,
    normalize_wiki_ref_key,
    strip_deleted_wikilinks,
)
from backend.delete.wiki_page_delete import cascade_delete_wiki_pages_with_refs


class TestNormalizeWikiRefKey:
    def test_collapses_case_space_hyphen_underscore(self):
        assert normalize_wiki_ref_key("KV Cache") == "kvcache"
        assert normalize_wiki_ref_key("kv-cache") == "kvcache"
        assert normalize_wiki_ref_key("kv_cache") == "kvcache"
        assert normalize_wiki_ref_key("wiki/concepts/kv-cache.md") == "kvcache"


class TestIndexAndWikilinkCleanup:
    def test_index_entry_dropped_for_deleted(self):
        text = "- [[kv-cache]] — description\n- [[OpenAI]] — kept\n## Section\n"
        cleaned = clean_index_listing(text, {"kvcache"})
        assert "[[kv-cache]]" not in cleaned
        assert "[[OpenAI]]" in cleaned
        assert "## Section" in cleaned

    def test_similar_slugs_not_collaterally_deleted(self):
        text = "- [[ai]] — removed\n- [[OpenAI]] — kept\n- [[AI Safety]] — kept\n"
        cleaned = clean_index_listing(text, {"ai"})
        assert "[[OpenAI]]" in cleaned
        assert "[[AI Safety]]" in cleaned

    def test_strip_deleted_wikilinks_keeps_display(self):
        text = "See [[deleted|the display]] and [[kept]]."
        cleaned = strip_deleted_wikilinks(text, {"deleted"})
        assert "the display" in cleaned
        assert "[[deleted" not in cleaned
        assert "[[kept]]" in cleaned


@pytest.fixture()
def wiki_project(tmp_path):
    project = project_core.create_project("delete-demo", str(tmp_path))
    project_core.ensure_project_id(project.path)
    root = tmp_path / "delete-demo"
    (root / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "sources").mkdir(parents=True, exist_ok=True)
    (root / "raw" / "sources" / "paper-a.md").write_text("# Paper A", encoding="utf-8")
    return project


def test_source_delete_cascades_to_owned_pages(wiki_project, tmp_path):
    root = tmp_path / "delete-demo"
    (root / "wiki" / "sources").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "sources" / "paper-a.md").write_text(
        '---\ntype: source\ntitle: "Source: paper-a.md"\nsources: ["paper-a.md"]\n---\n\n# Paper A\n',
        encoding="utf-8",
    )
    (root / "wiki" / "concepts" / "concept-a.md").write_text(
        '---\ntype: concept\ntitle: Concept A\nsources: ["paper-a.md"]\nrelated: []\n---\n\n# Concept A\n\nSee [[paper-a]].\n',
        encoding="utf-8",
    )
    (root / "wiki" / "concepts" / "concept-b.md").write_text(
        '---\ntype: concept\ntitle: Concept B\nsources: ["paper-a.md", "paper-b.md"]\n---\n\n# Concept B\n',
        encoding="utf-8",
    )

    result = delete_source_files(str(root), ["paper-a.md"])
    assert result["deletedSources"] == ["paper-a.md"]
    assert not (root / "raw" / "sources" / "paper-a.md").exists()
    assert not (root / "wiki" / "sources" / "paper-a.md").exists()
    assert not (root / "wiki" / "concepts" / "concept-a.md").exists()

    # Shared-source preservation: concept-b keeps paper-b.md only.
    concept_b = (root / "wiki" / "concepts" / "concept-b.md").read_text(encoding="utf-8")
    assert "paper-b.md" in concept_b
    assert '"paper-a.md"' not in concept_b.replace('"paper-b.md"', "")


def test_shared_entity_survives_alone(wiki_project, tmp_path):
    root = tmp_path / "delete-demo"
    (root / "wiki" / "entities").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "sources").mkdir(parents=True, exist_ok=True)
    # The source summary page is owned solely by paper-a → doomed, and
    # its deletion registers the keys the wikilink sweep strips.
    (root / "wiki" / "sources" / "paper-a.md").write_text(
        '---\ntype: source\ntitle: "Source: paper-a.md"\nsources: ["paper-a.md"]\n---\n\n# Paper A\n',
        encoding="utf-8",
    )
    (root / "wiki" / "entities" / "shared.md").write_text(
        '---\ntype: entity\ntitle: Shared\nsources: ["paper-a.md", "paper-b.md"]\n---\n\n# Shared\n\nSee [[paper-a]].\n',
        encoding="utf-8",
    )
    result = delete_source_files(str(root), ["paper-a.md"])
    assert (root / "wiki" / "entities" / "shared.md").exists()
    content = (root / "wiki" / "entities" / "shared.md").read_text(encoding="utf-8")
    assert "paper-b.md" in content
    assert "[[paper-a]]" not in content  # wikilink stripped to plain text


def test_wiki_page_cascade_sweeps_refs(wiki_project, tmp_path):
    root = tmp_path / "delete-demo"
    (root / "wiki" / "concepts" / "target.md").write_text(
        '---\ntype: concept\ntitle: Target Page\n---\n\n# Target Page\n',
        encoding="utf-8",
    )
    (root / "wiki" / "concepts" / "referrer.md").write_text(
        '---\ntype: concept\ntitle: Referrer\nrelated: [target-page]\n---\n\n# Referrer\n\nSee [[Target Page]].\n',
        encoding="utf-8",
    )
    index = root / "wiki" / "index.md"
    index.write_text(
        "# Wiki Index\n\n## Recently Updated\n\n- [[target-page]] — Target Page\n",
        encoding="utf-8",
    )

    result = cascade_delete_wiki_pages_with_refs(
        str(root), [str(root / "wiki" / "concepts" / "target.md")]
    )
    assert len(result["deletedPaths"]) == 1
    assert not (root / "wiki" / "concepts" / "target.md").exists()

    referrer = (root / "wiki" / "concepts" / "referrer.md").read_text(encoding="utf-8")
    assert "[[Target Page]]" not in referrer
    assert "related: [target-page]" not in referrer

    index_content = index.read_text(encoding="utf-8")
    assert "[[target-page]]" not in index_content
