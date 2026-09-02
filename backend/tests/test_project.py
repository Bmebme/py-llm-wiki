"""Project scaffold — content parity with llm_wiki project.rs."""

from backend.core import project as project_core
from backend.core.file_service import FsError


def test_create_project_scaffold(tmp_path):
    p = project_core.create_project("demo", str(tmp_path))
    root = tmp_path / "demo"
    assert p.name == "demo"

    # Required directories
    for d in ["raw/sources", "raw/assets", "wiki/entities", "wiki/concepts",
              "wiki/sources", "wiki/queries", "wiki/comparisons", "wiki/synthesis"]:
        assert (root / d).is_dir(), d

    # schema.md contains the page-type table (spot check byte content)
    schema = (root / "schema.md").read_text(encoding="utf-8")
    assert "| entity | wiki/entities/ |" in schema
    assert "| synthesis | wiki/synthesis/ |" in schema

    # purpose.md placeholders
    purpose = (root / "purpose.md").read_text(encoding="utf-8")
    assert "## Key Questions" in purpose
    assert "> TBD" in purpose

    # index.md grouped sections
    index = (root / "wiki/index.md").read_text(encoding="utf-8")
    for section in ["## Entities", "## Concepts", "## Sources",
                    "## Queries", "## Comparisons", "## Synthesis"]:
        assert section in index

    # log.md dated entry
    log = (root / "wiki/log.md").read_text(encoding="utf-8")
    assert "- Project created" in log
    assert "## " in log

    # overview.md frontmatter
    overview = (root / "wiki/overview.md").read_text(encoding="utf-8")
    assert "type: overview" in overview

    # Obsidian config
    assert (root / ".obsidian/app.json").exists()
    assert (root / ".obsidian/core-plugins.json").exists()


def test_create_project_existing_dir_fails(tmp_path):
    (tmp_path / "demo").mkdir()
    import pytest

    with pytest.raises(FsError, match="Directory already exists"):
        project_core.create_project("demo", str(tmp_path))


def test_open_project_validates(tmp_path):
    import pytest

    (tmp_path / "not-a-wiki").mkdir()
    with pytest.raises(FsError, match="missing schema.md"):
        project_core.open_project(str(tmp_path / "not-a-wiki"))

    valid = project_core.create_project("demo", str(tmp_path))
    p = project_core.open_project(valid.path)
    assert p.name == "demo"
    assert p.path == valid.path


def test_ensure_project_id_stable(tmp_path):
    project_core.create_project("demo", str(tmp_path))
    root = tmp_path / "demo"
    first = project_core.ensure_project_id(str(root))
    second = project_core.ensure_project_id(str(root))
    assert first == second
    assert project_core.read_project_id(str(root)) == first
