"""Date stamping regression — stamp_generated_frontmatter_dates must not
duplicate the closing fence (real-LLM ingest caught this)."""

from backend.ingest.postprocess import (
    stamp_generated_frontmatter_dates,
    stamp_generated_log_date,
)


def test_stamp_does_not_duplicate_closing_fence():
    content = "---\ntype: source\ntitle: T\ncreated: 2020-01-01\n---\n\n# Body\n"
    stamped = stamp_generated_frontmatter_dates(content, "2026-08-17")
    assert stamped.count("---") == 2
    assert "created: 2026-08-17" in stamped
    assert "updated: 2026-08-17" in stamped
    assert stamped.rstrip().endswith("# Body")


def test_stamp_appends_missing_dates():
    content = "---\ntype: concept\ntitle: T\n---\n\n# Body\n"
    stamped = stamp_generated_frontmatter_dates(content, "2026-08-17")
    assert stamped.count("---") == 2
    assert "created: 2026-08-17" in stamped
    assert "updated: 2026-08-17" in stamped


def test_stamp_log_date():
    stamped = stamp_generated_log_date("## [YYYY-MM-DD] ingest | x", "2026-08-17")
    assert stamped == "## [2026-08-17] ingest | x"


def test_no_frontmatter_unchanged():
    content = "# No frontmatter\n"
    assert stamp_generated_frontmatter_dates(content, "2026-08-17") == content
