"""Search engine tests — tokenizer, scoring, and graph blend behavior
mirroring search.rs unit tests."""

import pytest

from backend.search import engine
from backend.search.engine import search_project_inner
from backend.search.scoring import score_file
from backend.search.tokenize import tokenize_query


class TestTokenizeQuery:
    def test_english_words_and_stopwords(self):
        tokens = tokenize_query("what is chain of thought")
        assert "chain" in tokens
        assert "thought" in tokens
        assert "what" not in tokens  # stop word
        assert "is" not in tokens
        assert "of" not in tokens

    def test_cjk_bigrams_and_chars(self):
        tokens = tokenize_query("知识图谱")
        assert "知识" in tokens
        assert "识图" in tokens
        assert "图谱" in tokens
        assert "知" in tokens  # single char (not a stop word)
        assert "识" in tokens
        assert "谱" in tokens
        assert "知识图谱" in tokens  # full token

    def test_cjk_stop_word_chars_excluded(self):
        # Rust parity: "的什么" is one CJK token (not a stop word itself);
        # bigrams + non-stop single chars + full token come out — only
        # stop-word chars like 的 are dropped.
        tokens = tokenize_query("的什么")
        assert "的" not in tokens
        assert "什" in tokens
        assert "么" in tokens
        assert "的什么" in tokens

    def test_short_tokens_dropped(self):
        assert "a" not in tokenize_query("a b c")
        assert tokenize_query("a") == []

    def test_punctuation_is_separator(self):
        tokens = tokenize_query("chain-of-thought_parsing")
        # '-' and '_' are ASCII punctuation → separators
        assert "chain" in tokens
        assert "thought" in tokens
        assert "parsing" in tokens
        assert "chain-of-thought" not in tokens


class TestScoreFile:
    def test_filename_exact_dominates(self):
        result = score_file(
            "chain-of-thought.md",
            "# Chain of Thought\n\nbody",
            ["chain", "thought"],
            "chain-of-thought",
            "chain of thought",
        )
        assert result is not None
        assert result["score"] >= 200.0  # filename exact bonus

    def test_title_phrase_bonus(self):
        result = score_file(
            "notes.md",
            "# Chain of Thought\n\nbody",
            ["chain"],
            "chain of thought",
            "chain of thought",
        )
        assert result["score"] >= 50.0

    def test_content_phrase_occurrences(self):
        content = "# Title\n\n" + "attention " * 12
        result = score_file("x.md", content, ["attention"], "attention", "attention")
        assert result is not None
        # 12 occurrences capped at 10 × 20
        assert result["score"] >= 200.0

    def test_no_match_returns_none(self):
        assert score_file("x.md", "# Title\n\nbody", ["zzz"], "zzzqqq", "zzz qqq") is None

    def test_snippet_contains_context(self):
        content = "x" * 200 + "needle" + "y" * 200
        result = score_file("x.md", content, ["needle"], "needle", "needle")
        assert "needle" in result["snippet"]
        assert result["snippet"].startswith("...")


class TestEngine:
    @pytest.fixture()
    def wiki(self, tmp_path):
        (tmp_path / "wiki" / "concepts").mkdir(parents=True)
        (tmp_path / "wiki" / "entities").mkdir(parents=True)
        (tmp_path / "wiki" / "concepts" / "attention.md").write_text(
            "---\ntype: concept\ntitle: Attention Mechanism\n---\n\n# Attention Mechanism\n\n"
            "Attention computes weighted sums. See [[transformer]].",
            encoding="utf-8",
        )
        (tmp_path / "wiki" / "concepts" / "transformer.md").write_text(
            "---\ntype: concept\ntitle: Transformer\n---\n\n# Transformer\n\n"
            "The architecture stacks layers. See [[attention]].",
            encoding="utf-8",
        )
        (tmp_path / "wiki" / "entities" / "unrelated.md").write_text(
            "---\ntype: entity\ntitle: Unrelated Entity\n---\n\n# Unrelated Entity\n\n"
            "Nothing to do with attention.",
            encoding="utf-8",
        )
        return str(tmp_path)

    def test_keyword_ranking(self, wiki):
        response = search_project_inner(wiki, "attention mechanism")
        assert response["mode"] == "keyword"
        paths = [r["path"] for r in response["results"]]
        assert paths[0].endswith("attention.md")

    def test_include_content_supplies_page_bodies(self, wiki):
        # Chat depends on this: without it the agent receives empty
        # pages and answers "no matching wiki pages" (real-LLM bug).
        response = search_project_inner(wiki, "attention", include_content=True)
        assert response["results"]
        for result in response["results"]:
            assert isinstance(result.get("content"), str)
            assert result["content"].strip()
        without = search_project_inner(wiki, "attention", include_content=False)
        assert "content" not in without["results"][0]

    def test_empty_query_rejected(self, wiki):
        with pytest.raises(ValueError, match="query is required"):
            search_project_inner(wiki, "   ")

    def test_graph_blend_adds_neighbors(self, wiki):
        # transformer.md links [[attention]] but its body never contains
        # the query words — it can only enter the window via the graph
        # blend (a NEW neighbor, which is what graphHits counts).
        response = search_project_inner(wiki, "weighted sums", top_k=5)
        paths = [r["path"] for r in response["results"]]
        assert any(p.endswith("transformer.md") for p in paths)
        assert response["graphHits"] > 0
        assert response["mode"] == "hybrid"

    def test_graph_blend_quota(self):
        assert engine.graph_result_quota(1, 0) == 0
        assert engine.graph_result_quota(10, 0) >= 1
        # Full vector coverage → minimum graph share
        low = engine.graph_result_quota(20, 20)
        high = engine.graph_result_quota(20, 0)
        assert low <= high
