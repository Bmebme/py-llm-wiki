"""FILE/REVIEW grammar tests — mirrors the hazard fixtures in
llm_wiki's src/lib/ingest-parse.test.ts."""

from backend.ingest.parse_blocks import (
    is_safe_ingest_path,
    parse_file_blocks,
    parse_review_blocks,
)


class TestParseFileBlocks:
    def test_single_block(self):
        text = (
            "---FILE: wiki/entities/foo.md---\n"
            "---\ntype: entity\ntitle: Foo\n---\n\n# Foo\n\nBody.\n"
            "---END FILE---"
        )
        result = parse_file_blocks(text)
        assert len(result.blocks) == 1
        block = result.blocks[0]
        assert block.path == "wiki/entities/foo.md"
        assert "# Foo" in block.content
        assert result.warnings == []
        assert result.truncated_paths == []

    def test_multiple_blocks(self):
        text = (
            "---FILE: wiki/entities/a.md---\n# A\n---END FILE---\n\n"
            "---FILE: wiki/concepts/b.md---\n# B\n---END FILE---"
        )
        result = parse_file_blocks(text)
        assert [b.path for b in result.blocks] == [
            "wiki/entities/a.md",
            "wiki/concepts/b.md",
        ]

    def test_h1_crlf_line_endings(self):
        text = (
            "---FILE: wiki/entities/foo.md---\r\n"
            "# Foo\r\n"
            "---END FILE---\r\n"
        )
        result = parse_file_blocks(text)
        assert len(result.blocks) == 1
        assert "# Foo" in result.blocks[0].content

    def test_h3_marker_whitespace_and_case_variants(self):
        text = (
            "--- FILE: wiki/entities/foo.md ---\n"
            "# Foo\n"
            "--- END FILE ---\n"
            "---file: wiki/concepts/bar.md---\n"
            "# Bar\n"
            "---end file---"
        )
        result = parse_file_blocks(text)
        assert [b.path for b in result.blocks] == [
            "wiki/entities/foo.md",
            "wiki/concepts/bar.md",
        ]

    def test_h5_closer_inside_fence_does_not_truncate(self):
        text = (
            "---FILE: wiki/concepts/ingest-format.md---\n"
            "```\n"
            "---END FILE---\n"
            "```\n"
            "real body continues\n"
            "---END FILE---"
        )
        result = parse_file_blocks(text)
        assert len(result.blocks) == 1
        assert "---END FILE---" in result.blocks[0].content  # the fenced one survives
        assert "real body continues" in result.blocks[0].content

    def test_h2_truncation_surfaces_warning(self):
        text = "---FILE: wiki/entities/foo.md---\n# Foo\n(never closed)"
        result = parse_file_blocks(text)
        assert result.blocks == []
        assert result.truncated_paths == ["wiki/entities/foo.md"]
        assert any("not closed" in w for w in result.warnings)

    def test_h6_empty_path_skipped_with_warning(self):
        text = "---FILE: ---\n# no path\n---END FILE---"
        result = parse_file_blocks(text)
        assert result.blocks == []
        assert any("empty path" in w for w in result.warnings)


class TestSafeIngestPath:
    def test_allows_wiki_paths(self):
        assert is_safe_ingest_path("wiki/entities/gpt-4.md")
        assert is_safe_ingest_path("wiki/概念/知识图谱.md")  # CJK filenames fine

    def test_rejects_traversal(self):
        assert not is_safe_ingest_path("../etc/passwd")
        assert not is_safe_ingest_path("wiki/../../etc/passwd")
        assert not is_safe_ingest_path("wiki/../escape.md")

    def test_rejects_absolute_and_drive_paths(self):
        assert not is_safe_ingest_path("/etc/passwd")
        assert not is_safe_ingest_path("C:/Windows/system32")
        assert not is_safe_ingest_path("\\etc\\passwd")

    def test_rejects_non_wiki_roots(self):
        assert not is_safe_ingest_path("raw/sources/x.md")
        assert not is_safe_ingest_path("schema.md")

    def test_rejects_windows_invalid_names(self):
        assert not is_safe_ingest_path("wiki/concepts/con.md")
        assert not is_safe_ingest_path("wiki/concepts/a:b.md")
        assert not is_safe_ingest_path("wiki/concepts/trailing.")
        assert not is_safe_ingest_path("wiki/concepts/")

    def test_rejects_control_chars_and_empty(self):
        assert not is_safe_ingest_path("wiki/a\x00b.md")
        assert not is_safe_ingest_path("")
        assert not is_safe_ingest_path("   ")

    def test_normalizes_backslashes_for_windows_payloads(self):
        # Windows-style separators must not sneak past the segment checks.
        assert not is_safe_ingest_path("wiki\\..\\..\\etc\\passwd")


class TestParseReviewBlocks:
    def test_review_with_all_fields(self):
        text = (
            "---REVIEW: suggestion | Research topic---\n"
            "A gap worth studying.\n"
            "OPTIONS: Create Page | Skip\n"
            "PAGES: wiki/concepts/a.md, wiki/entities/b.md\n"
            "SEARCH: query one | query two\n"
            "---END REVIEW---"
        )
        items = parse_review_blocks(text, "raw/sources/paper.pdf")
        assert len(items) == 1
        item = items[0]
        assert item["type"] == "suggestion"
        assert item["title"] == "Research topic"
        assert item["sourcePath"] == "raw/sources/paper.pdf"
        assert item["options"] == [
            {"label": "Create Page", "action": "Create Page"},
            {"label": "Skip", "action": "Skip"},
        ]
        assert item["affectedPages"] == ["wiki/concepts/a.md", "wiki/entities/b.md"]
        assert item["searchQueries"] == ["query one", "query two"]
        # Description excludes the OPTIONS/PAGES/SEARCH lines
        assert item["description"] == "A gap worth studying."
        assert "OPTIONS" not in item["description"]

    def test_unknown_type_falls_back_to_confirm(self):
        text = (
            "---REVIEW: banana | Something odd---\nBody.\nOPTIONS: A | B\n---END REVIEW---"
        )
        items = parse_review_blocks(text, "src.md")
        assert items[0]["type"] == "confirm"
        assert items[0]["options"] == [
            {"label": "A", "action": "A"},
            {"label": "B", "action": "B"},
        ]

    def test_missing_option_lines_get_defaults(self):
        text = "---REVIEW: contradiction | X vs Y---\nJust a conflict.\n---END REVIEW---"
        items = parse_review_blocks(text, "src.md")
        assert items[0]["options"] == [
            {"label": "Approve", "action": "Approve"},
            {"label": "Skip", "action": "Skip"},
        ]
        assert items[0]["affectedPages"] is None
        assert items[0]["searchQueries"] is None
