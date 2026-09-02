"""Source identity — golden vectors generated from llm_wiki's JS
source-identity.ts implementation."""

from backend.wiki.source_identity import (
    source_identity_for_path,
    source_reference_identity,
    source_summary_slug_from_identity,
)

SLUG_GOLDEN = [
    ("paper.pdf", "paper"),
    ("papers/energy/grid-storage.pdf", "6-papers--6-energy--12-grid-storage--1m253bg"),
    ("reading/机器学习 综述.md", "7-reading--7-机器学习-综述--10regzr"),
    ("papers/AI & ML: survey (2026).pdf", "6-papers--18-ai-ml-survey-2026--lkijd"),
]


def test_slug_golden_vectors():
    for identity, expected in SLUG_GOLDEN:
        assert source_summary_slug_from_identity(identity) == expected


def test_long_slug_truncated_to_120():
    # TS spec (source-identity.test.ts:62) asserts only the ≤120 property
    # for long identities — no exact string.
    identity = (
        "notes/2026/A Very Long Title With Many Words That Goes On And On "
        "About Deep Learning Infrastructure And Distributed Training Systems.pdf"
    )
    slug = source_summary_slug_from_identity(identity)
    assert len(slug) <= 120
    assert len(f"wiki/sources/{slug}.md") <= 136
    assert slug.endswith("--lwt8bn")  # stable hash suffix present


def test_synthetic_slug_part_identity_does_not_collide():
    # A filename that LOOKS like an encoded slug is a single path part and
    # returns verbatim — never confused with the multi-part encoding.
    assert source_summary_slug_from_identity("4-a--b--6-config.yaml") == "4-a--b--6-config"
    assert source_summary_slug_from_identity("a/b/config.yaml") != "4-a--b--6-config"


def test_identity_for_path_variants():
    project = "/Users/x/my-wiki"
    assert (
        source_identity_for_path(project, f"{project}/raw/sources/papers/a.pdf")
        == "papers/a.pdf"
    )
    assert (
        source_identity_for_path(project, "raw/sources/b.pdf") == "b.pdf"
    )
    assert (
        source_identity_for_path("/other/proj", "/other/proj/raw/sources/x.pdf")
        == "x.pdf"
    )
    # no raw/sources marker → bare filename fallback
    assert (
        source_identity_for_path(project, "/somewhere/else/c.pdf") == "c.pdf"
    )


def test_reference_identity():
    assert source_reference_identity("raw/sources/papers/a.pdf") == "papers/a.pdf"
    assert (
        source_reference_identity("/proj/raw/sources/x.pdf") == "x.pdf"
    )
    assert source_reference_identity("x.pdf") == "x.pdf"
