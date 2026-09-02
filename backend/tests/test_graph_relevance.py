"""4-signal relevance model — mirrors graph-relevance.test.ts cases."""

import math

from backend.graph.relevance import (
    RetrievalGraph,
    RetrievalNode,
    calculate_relevance,
    get_related_nodes,
)


def node(
    id_: str,
    title: str = "",
    type_: str = "concept",
    sources=(),
    out_links=(),
    in_links=(),
):
    return RetrievalNode(
        id=id_,
        title=title or id_,
        type=type_,
        path=f"wiki/concepts/{id_}.md",
        sources=tuple(sources),
        out_links=frozenset(out_links),
        in_links=frozenset(in_links),
    )


def graph(nodes: list[RetrievalNode]) -> RetrievalGraph:
    return RetrievalGraph(nodes={n.id: n for n in nodes})


def test_direct_link_signal():
    a = node("a", out_links=["b"])
    b = node("b", in_links=["a"])
    relevance = calculate_relevance(a, b, graph([a, b]))
    # forward (1) × 3.0 + type affinity (0.8 × 1.0)
    assert math.isclose(relevance, 3.0 + 0.8, rel_tol=1e-9)


def test_source_overlap_signal():
    a = node("a", sources=["paper.pdf"])
    b = node("b", sources=["paper.pdf"])
    # 1 shared source × 4.0 + affinity 0.8
    assert math.isclose(calculate_relevance(a, b, graph([a, b])), 4.0 + 0.8, rel_tol=1e-9)


def test_adamic_adar_signal():
    hub = node("hub", out_links=["a", "b"])
    a = node("a", in_links=["hub"])
    b = node("b", in_links=["hub"])
    g = graph([hub, a, b])
    # Common neighbor hub: degree 2 → 1/ln(2) × 1.5 + affinity 0.8
    expected = (1 / math.log(2)) * 1.5 + 0.8
    assert math.isclose(calculate_relevance(a, b, g), expected, rel_tol=1e-9)


def test_self_relevance_is_zero():
    a = node("a", sources=["x"])
    assert calculate_relevance(a, a, graph([a])) == 0


def test_unknown_type_affinity_defaults_to_point_five():
    a = node("a", type_="weird-custom-type")
    b = node("b", type_="another-custom-type")
    assert math.isclose(
        calculate_relevance(a, b, graph([a, b])), 0.5, rel_tol=1e-9
    )


def test_get_related_nodes_ranking():
    a = node("a", out_links=["b", "c"])
    b = node("b", in_links=["a"], sources=["s.pdf"])
    c = node("c", in_links=["a"])
    g = graph([a, b, c])
    related = get_related_nodes("a", g, limit=5)
    ids = [n.id for (n, _) in related]
    assert ids[0] == "b"  # b has both direct link AND source overlap
    assert "c" in ids


def test_negative_relevance_pairs_excluded():
    a = node("a", type_="entity")
    b = node("b", type_="entity")
    related = get_related_nodes("a", graph([a, b]), limit=5)
    # Type affinity alone still scores 0.8 > 0, so relatedness exists;
    # the desktop returns pairs with relevance > 0 — verify that gate.
    assert any(n.id == "b" for n, _ in related)
