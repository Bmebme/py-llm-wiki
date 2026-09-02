"""Save-to-Wiki filename policy — port of wiki-filename.test.ts cases."""

from datetime import datetime, timezone

from backend.wiki.wiki_filename import make_query_file_name, make_query_slug


def test_cjk_titles_keep_characters():
    slug = make_query_slug("知识图谱")
    assert slug == "知识图谱"


def test_ascii_kebab_lowercase():
    assert make_query_slug("Chain of Thought") == "chain-of-thought"


def test_emoji_and_punctuation_stripped():
    assert make_query_slug("AI & ML 🚀 survey!") == "ai-ml-survey"


def test_empty_collapses_to_query():
    assert make_query_slug("!!!") == "query"
    assert make_query_slug("") == "query"


def test_truncated_to_50_chars():
    long_title = "word " * 40
    assert len(make_query_slug(long_title)) <= 50


def test_full_width_normalized():
    # NFKC: full-width A becomes ASCII a
    assert make_query_slug("ＡＩ ＭＬ") == "ai-ml"


def test_file_name_shape_and_utc():
    now = datetime(2026, 4, 23, 14, 30, 52, 123000, tzinfo=timezone.utc)
    result = make_query_file_name("Attention is all you need", now=now)
    assert result["date"] == "2026-04-23"
    assert result["time"] == "143052"
    assert result["fileName"] == "attention-is-all-you-need-2026-04-23-143052.md"
