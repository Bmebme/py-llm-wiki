"""Path-safety vectors for safe_join / is_public_project_rel / list_tree —
mirroring the behavior of llm_wiki's api_server.rs."""

import pytest

from backend.core import file_service
from backend.core.file_service import FsError


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "index.md").write_text("# Index", encoding="utf-8")
    (tmp_path / "raw" / "sources").mkdir(parents=True)
    (tmp_path / "schema.md").write_text("# Schema", encoding="utf-8")
    (tmp_path / "purpose.md").write_text("# Purpose", encoding="utf-8")
    (tmp_path / ".llm-wiki").mkdir()
    (tmp_path / ".llm-wiki" / "secret.md").write_text("secret", encoding="utf-8")
    return str(tmp_path)


class TestSafeJoin:
    def test_simple_relative(self, project):
        path = file_service.safe_join(project, "wiki/index.md")
        assert path.name == "index.md"

    def test_leading_slash_treated_as_relative(self, project):
        # Rust parity: safe_join trims leading '/' BEFORE the is_absolute
        # check (api_server.rs:950-951), so "/etc/passwd" resolves inside
        # the project on Unix. The real defense is the canonicalization
        # containment check below.
        path = file_service.safe_join(project, "/etc/passwd")
        assert str(path).startswith(project)

    def test_parent_dir_rejected(self, project):
        with pytest.raises(FsError, match="Path traversal is not allowed"):
            file_service.safe_join(project, "../etc/passwd")

    def test_dotdot_nested_rejected(self, project):
        with pytest.raises(FsError, match="Path traversal is not allowed"):
            file_service.safe_join(project, "wiki/../../etc/passwd")

    def test_dot_segment(self, project):
        # "." is a CurDir component — Rust keeps it in the join; result
        # must still stay inside the project.
        path = file_service.safe_join(project, "wiki/./index.md")
        assert "index.md" in str(path)


class TestPublicRel:
    @pytest.mark.parametrize(
        "rel,expected",
        [
            ("purpose.md", True),
            ("schema.md", True),
            ("wiki/index.md", True),
            ("raw/sources/a.pdf", True),
            ("wiki/../purpose.md", False),
            (".llm-wiki/secret.md", False),
            ("README.md", False),
            ("/etc/passwd", False),
        ],
    )
    def test_is_public(self, rel, expected):
        assert file_service.is_public_project_rel(rel) is expected


class TestListTree:
    def test_hides_dotfiles_and_symlinks(self, project):
        (file_service.safe_join(project, "wiki") / ".hidden.md").write_text("x")
        (file_service.safe_join(project, "wiki") / "link.md").symlink_to("index.md")
        files = file_service.list_tree(
            project, file_service.safe_join(project, "wiki"), True, 100, file_service.count_ref()
        )
        names = {f["name"] for f in files}
        assert ".hidden.md" not in names
        assert "link.md" not in names
        assert "index.md" in names

    def test_max_files_limit(self, project):
        wiki = file_service.safe_join(project, "wiki")
        for i in range(10):
            (wiki / f"p{i}.md").write_text("x")
        with pytest.raises(FsError, match="exceeds maxFiles"):
            file_service.list_tree(project, wiki, True, 5, file_service.count_ref())

    def test_dirs_first_then_alphabetical(self, project):
        wiki = file_service.safe_join(project, "wiki")
        (wiki / "b.md").write_text("x")
        (wiki / "a.md").write_text("x")
        files = file_service.list_tree(
            project, wiki, True, 100, file_service.count_ref()
        )
        entries = [(f["is_dir"], f["name"]) for f in files]
        assert entries[0] == (True, "entities")  # only dir is entities/
        assert [n for _, n in entries if not n.startswith("entities")] == [
            "a.md",
            "b.md",
            "index.md",
        ]
