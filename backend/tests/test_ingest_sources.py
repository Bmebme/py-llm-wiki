"""Path-helper tests ported from llm_wiki src/lib/source-lifecycle.test.ts
("source-lifecycle path helpers" describe block) against
backend.ingest.sources.
"""

import pytest

from backend.ingest.sources import (
    INGESTABLE_SOURCE_EXTENSIONS,
    folder_context_for_source_path,
    is_ingestable_source_path,
)


class TestIsIngestableSourcePath:
    def test_preprocessed_cache_files_are_not_ingestable(self):
        # Mirrors source-lifecycle.test.ts:54-55 — preprocess writes
        # `raw/sources/.cache/<name>.txt`; those must never re-ingest.
        assert is_ingestable_source_path("raw/sources/.cache/report.pdf.txt") is False
        assert is_ingestable_source_path("/project/raw/sources/.cache/report.pdf.txt") is False

    def test_accepts_supported_ebook_formats(self):
        # Mirrors source-lifecycle.test.ts:59-60.
        assert is_ingestable_source_path("raw/sources/book.epub") is True
        assert is_ingestable_source_path("C:\\project\\raw\\sources\\book.MOBI") is True

    def test_accepts_office_and_rtf_source_variants(self):
        # Mirrors source-lifecycle.test.ts:63-71.
        for path in [
            "report.docm",
            "deck.ppt",
            "show.ppsm",
            "workbook.xlsb",
            "notes.rtf",
        ]:
            assert is_ingestable_source_path(f"raw/sources/{path}") is True

    def test_rejects_non_ingestable_extensions(self):
        assert is_ingestable_source_path("raw/sources/image.png") is False
        assert is_ingestable_source_path("raw/sources/notes") is False  # no extension
        assert is_ingestable_source_path("raw/sources/.hidden.md") is False  # dotfile

    def test_extension_set_matches_source_lifecycle(self):
        # Every extension from source-lifecycle.ts:45-78 must be accepted.
        for ext in [
            "md", "mdx", "txt", "pdf", "doc", "docx", "docm", "ppt", "pps", "pot",
            "pptx", "pptm", "ppsx", "ppsm", "xlsx", "xlsm", "xlsb", "odt", "odp",
            "ods", "xls", "csv", "json", "html", "htm", "rtf", "xml", "yaml",
            "yml", "epub", "mobi", "org",
        ]:
            assert ext in INGESTABLE_SOURCE_EXTENSIONS
            assert is_ingestable_source_path(f"raw/sources/source.{ext}") is True


class TestFolderContextForSourcePath:
    def test_absolute_raw_sources_path_without_leaking_project_prefix(self):
        # Mirrors source-lifecycle.test.ts:77-78.
        assert (
            folder_context_for_source_path("/tmp/project/raw/sources/reports/2026/report.pdf")
            == "reports > 2026"
        )

    def test_root_relative_path(self):
        assert folder_context_for_source_path("raw/sources/reports/2026/report.pdf") == (
            "reports > 2026"
        )

    def test_single_file_at_root_has_no_folder_context(self):
        assert folder_context_for_source_path("raw/sources/report.pdf") == ""

    def test_windows_backslashes_normalized(self):
        assert (
            folder_context_for_source_path("C:\\project\\raw\\sources\\reports\\report.pdf")
            == "reports"
        )

    def test_non_sources_path_is_used_verbatim(self):
        # A path with no raw/sources marker is taken as-is (parent chain
        # of the whole path; note a leading "/" yields an empty first
        # breadcrumb segment, faithful to the TS split("/")).
        assert folder_context_for_source_path("docs/notes/a.md") == "docs > notes"

    def test_custom_sources_root(self):
        assert (
            folder_context_for_source_path("/x/raw/sources/sub/report.pdf", sources_root="/x/raw/sources")
            == "sub"
        )
