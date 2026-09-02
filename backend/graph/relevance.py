"""4-signal relevance model — port of llm_wiki src/lib/graph-relevance.ts.

Signals: direct links ×3.0, source overlap ×4.0, Adamic-Adar ×1.5,
type affinity ×1.0. Graph is 1-hop (the desktop's TS graph has no
2-hop decay; the only hop-expansion is the search.rs 1-hop blend).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from backend.search.scoring import extract_title
from backend.wiki.frontmatter import parse_frontmatter
from backend.wiki.sources_merge import parse_sources

WEIGHTS = {
    "directLink": 3.0,
    "sourceOverlap": 4.0,
    "commonNeighbor": 1.5,
    "typeAffinity": 1.0,
}

TYPE_AFFINITY: dict[str, dict[str, float]] = {
    "entity": {"concept": 1.2, "entity": 0.8, "source": 1.0, "synthesis": 1.0, "query": 0.8},
    "concept": {"entity": 1.2, "concept": 0.8, "source": 1.0, "synthesis": 1.2, "query": 1.0},
    "source": {"entity": 1.0, "concept": 1.0, "source": 0.5, "query": 0.8, "synthesis": 1.0},
    "query": {"concept": 1.0, "entity": 0.8, "synthesis": 1.0, "source": 0.8, "query": 0.5},
    "synthesis": {"concept": 1.2, "entity": 1.0, "source": 1.0, "query": 1.0, "synthesis": 0.8},
}

WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]")

_cache: dict[tuple, "RetrievalGraph"] = {}


@dataclass
class RetrievalNode:
    id: str
    title: str
    type: str
    path: str
    sources: tuple[str, ...]
    out_links: frozenset[str]
    in_links: frozenset[str]


@dataclass
class RetrievalGraph:
    nodes: dict[str, RetrievalNode]
    data_version: int = 0


def _file_name_to_id(file_name: str) -> str:
    return file_name.removesuffix(".md")


def _extract_wikilinks(content: str) -> list[str]:
    return [m.group(1) for m in WIKILINK_RE.finditer(content)]


def _resolve_target(link_target: str, node_ids: set[str]) -> str | None:
    """Port of resolveTarget: exact id → lowercase → kebab-case."""
    if link_target in node_ids:
        return link_target
    lower = link_target.lower()
    for id_ in node_ids:
        if id_.lower() == lower:
            return id_
    kebab = lower.replace(" ", "-")
    for id_ in node_ids:
        if id_.lower() == kebab:
            return id_
    return None


def build_retrieval_graph(project_path: str, data_version: int = 0) -> RetrievalGraph:
    cache_key = (project_path, data_version)
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    wiki_root = Path(project_path) / "wiki"
    empty = RetrievalGraph(nodes={}, data_version=data_version)
    if not wiki_root.exists():
        _cache[cache_key] = empty
        return empty

    raw_nodes: list[dict] = []
    for entry in sorted(wiki_root.rglob("*.md")):
        if not entry.is_file():
            continue
        id_ = _file_name_to_id(entry.name)
        try:
            content = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        parsed = parse_frontmatter(content)
        frontmatter = parsed.frontmatter or {}
        raw_nodes.append({
            "id": id_,
            "title": str(frontmatter.get("title") or entry.name.removesuffix(".md").replace("-", " ")),
            "type": str(frontmatter.get("type") or "other"),
            "path": entry.as_posix(),
            "sources": parse_sources(content),
            "raw_links": _extract_wikilinks(content),
        })

    node_ids = {n["id"] for n in raw_nodes}
    out_links_map: dict[str, set[str]] = {id_: set() for id_ in node_ids}
    in_links_map: dict[str, set[str]] = {id_: set() for id_ in node_ids}

    for raw in raw_nodes:
        for link_target in raw["raw_links"]:
            resolved = _resolve_target(link_target, node_ids)
            if resolved is None or resolved == raw["id"]:
                continue
            out_links_map[raw["id"]].add(resolved)
            in_links_map[resolved].add(raw["id"])

    nodes: dict[str, RetrievalNode] = {}
    for raw in raw_nodes:
        id_ = raw["id"]
        nodes[id_] = RetrievalNode(
            id=id_,
            title=raw["title"],
            type=raw["type"],
            path=raw["path"],
            sources=tuple(raw["sources"]),
            out_links=frozenset(out_links_map.get(id_, set())),
            in_links=frozenset(in_links_map.get(id_, set())),
        )

    graph = RetrievalGraph(nodes=nodes, data_version=data_version)
    _cache[cache_key] = graph
    return graph


def clear_graph_cache() -> None:
    _cache.clear()


def _get_neighbors(node: RetrievalNode) -> set[str]:
    return set(node.out_links) | set(node.in_links)


def _get_node_degree(node: RetrievalNode) -> int:
    return len(node.out_links) + len(node.in_links)


def calculate_relevance(
    node_a: RetrievalNode, node_b: RetrievalNode, graph: RetrievalGraph
) -> float:
    if node_a.id == node_b.id:
        return 0.0

    # Signal 1: Direct links (×3.0)
    forward = 1 if node_b.id in node_a.out_links else 0
    backward = 1 if node_a.id in node_b.out_links else 0
    direct_link_score = (forward + backward) * WEIGHTS["directLink"]

    # Signal 2: Source overlap (×4.0)
    sources_a = set(node_a.sources)
    shared_source_count = sum(1 for s in node_b.sources if s in sources_a)
    source_overlap_score = shared_source_count * WEIGHTS["sourceOverlap"]

    # Signal 3: Adamic-Adar common neighbors (×1.5)
    neighbors_a = _get_neighbors(node_a)
    neighbors_b = _get_neighbors(node_b)
    adamic_adar = 0.0
    for neighbor_id in neighbors_a & neighbors_b:
        neighbor = graph.nodes.get(neighbor_id)
        if neighbor is not None:
            degree = _get_node_degree(neighbor)
            adamic_adar += 1 / math.log(max(degree, 2))
    common_neighbor_score = adamic_adar * WEIGHTS["commonNeighbor"]

    # Signal 4: Type affinity (×1.0)
    affinity_map = TYPE_AFFINITY.get(node_a.type)
    affinity = (affinity_map or {}).get(node_b.type, 0.5)
    type_affinity_score = affinity * WEIGHTS["typeAffinity"]

    return (
        direct_link_score + source_overlap_score + common_neighbor_score + type_affinity_score
    )


def get_related_nodes(
    node_id: str, graph: RetrievalGraph, limit: int = 5
) -> list[tuple[RetrievalNode, float]]:
    source_node = graph.nodes.get(node_id)
    if source_node is None:
        return []
    scored: list[tuple[RetrievalNode, float]] = []
    for id_, node in graph.nodes.items():
        if id_ == node_id:
            continue
        relevance = calculate_relevance(source_node, node, graph)
        if relevance > 0:
            scored.append((node, relevance))
    scored.sort(key=lambda pair: -pair[1])
    return scored[:limit]
