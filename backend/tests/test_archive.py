"""Project archive export/import round-trip + index rebuild."""

from pathlib import Path

import pytest

from backend.core import project as project_core
from backend.core.file_service import FsError
from backend.delete.archive import (
    export_project_archive,
    import_project_archive,
    rebuild_wiki_index,
)


@pytest.fixture()
def wiki_project(tmp_path):
    project = project_core.create_project("archive-demo", str(tmp_path))
    project_core.ensure_project_id(project.path)
    root = tmp_path / "archive-demo"
    (root / "wiki" / "concepts").mkdir(parents=True, exist_ok=True)
    (root / "wiki" / "concepts" / "attention.md").write_text(
        "---\ntype: concept\ntitle: Attention\n---\n\n# Attention\n\nBody.",
        encoding="utf-8",
    )
    (root / "raw" / "sources" / "paper.md").write_text("# Paper", encoding="utf-8")
    return project


def test_archive_round_trip(wiki_project, tmp_path):
    source = wiki_project.path
    archive_path = str(tmp_path / "out" / "demo.llmwiki.zip")
    (tmp_path / "out").mkdir(parents=True, exist_ok=True)
    export_project_archive(source, archive_path)
    assert Path(archive_path).is_file()

    imported_root = import_project_archive(archive_path, str(tmp_path / "restored"))
    # llm_wiki desktop archives import as valid wikis with the same files
    project_core.open_project(imported_root)
    assert (Path(imported_root) / "wiki" / "concepts" / "attention.md").exists()
    assert (Path(imported_root) / "raw" / "sources" / "paper.md").exists()
    assert (Path(imported_root) / ".llm-wiki" / "project.json").exists()


def test_export_destination_inside_project_rejected(wiki_project):
    with pytest.raises(FsError, match="outside the project"):
        export_project_archive(wiki_project.path, f"{wiki_project.path}/inside.zip")


def test_import_zip_slip_guard(tmp_path):
    import zipfile

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../../escaped.txt", "pwned")
    with pytest.raises(FsError, match="escapes"):
        import_project_archive(str(evil), str(tmp_path / "victim"))


def test_rebuild_index_groups_pages(wiki_project):
    result = rebuild_wiki_index(wiki_project.path)
    assert result["pages"] >= 1
    index = (Path(wiki_project.path) / "wiki" / "index.md").read_text(encoding="utf-8")
    assert "## Concepts" in index
    assert "[[concepts/attention]] — Attention" in index
