"""Wikilinks graph builder — port of api_server.rs handle_graph /
build_graph (2291-2410). Serves the 19828 GET /graph contract."""

from __future__ import annotations

from pathlib import Path

from backend.search.engine import file_stem, relative_to_project
from backend.search.scoring import extract_title


def extract_type(content: str) -> str:
    for line in content.split("\n"):
        value = line.strip()
        if value.startswith("type:"):
            return value[len("type:"):].strip().strip('"').strip("'").lower()
    return "other"


def _extract_wikilinks(content: str) -> list[str]:
    out: list[str] = []
    rest = content
    while True:
        start = rest.find("[[")
        if start < 0:
            break
        rest = rest[start + 2:]
        end = rest.find("]]")
        if end < 0:
            break
        inner = rest[:end]
        target = inner.split("|")[0].strip()
        if target:
            out.append(target)
        rest = rest[end + 2:]
    return out


def resolve_link(raw: str, ids: set[str]) -> str | None:
    if raw in ids:
        return raw
    normalized = raw.lower().replace(" ", "-")
    for id_ in ids:
        lowered = id_.lower()
        if lowered == normalized or lowered == raw.lower():
            return id_
    return None


def build_graph(project_path: str) -> tuple[list[dict], list[dict]]:
    wiki_root = Path(project_path) / "wiki"
    raw: dict[str, tuple[str, str, str, list[str]]] = {}
    for entry in sorted(wiki_root.rglob("*.md")) if wiki_root.exists() else []:
        if not entry.is_file():
            continue
        try:
            content = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        id_ = entry.stem
        if not id_:
            continue
        title = extract_title(content, entry.name)
        node_type = extract_type(content)
        path = relative_to_project(project_path, entry)
        links = _extract_wikilinks(content)
        raw[id_] = (title, node_type, path, links)

    ids: set[str] = set(raw.keys())
    link_count: dict[str, int] = {id_: 0 for id_ in raw}
    seen: set[str] = set()
    edges: list[dict] = []
    for source, (_, _, _, links) in raw.items():
        for link in links:
            target = resolve_link(link, ids)
            if target is None or target == source:
                continue
            key = f"{source}::{target}" if source < target else f"{target}::{source}"
            if key in seen:
                continue
            seen.add(key)
            link_count[source] += 1
            link_count[target] += 1
            edges.append({"source": source, "target": target, "weight": 1.0})

    nodes = [
        {
            "id": id_,
            "label": label,
            "nodeType": node_type,
            "path": path,
            "linkCount": link_count.get(id_, 0),
        }
        for id_, (label, node_type, path, _) in raw.items()
        if node_type != "query"
    ]
    return nodes, edges


def build_graph_filtered(
    project_path: str,
    q: str | None = None,
    node_type: str | None = None,
    limit: int = 200,
) -> tuple[list[dict], list[dict]]:
    nodes, edges = build_graph(project_path)
    if q:
        q_lower = q.lower()
        nodes = [
            n for n in nodes
            if q_lower in n["id"].lower() or q_lower in n["label"].lower()
        ]
    if node_type:
        wanted = node_type.lower()
        nodes = [n for n in nodes if n["nodeType"] == wanted]
    nodes = nodes[: max(1, min(limit, 1000))]
    node_ids = {n["id"] for n in nodes}
    edges = [e for e in edges if e["source"] in node_ids and e["target"] in node_ids]
    return nodes, edges
