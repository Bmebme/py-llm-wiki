"""Hybrid search engine — port of search.rs search_project_inner
(323-478), apply_rrf_scores (480-501), graph_result_quota (507-515),
blend_graph_results (517-655), search_mode (686-696).

v0: keyword + graph blend. The vector leg (query_embedding) is accepted
and ignored — the contract fields (mode/vectorHits) stay stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from backend.search.scoring import score_file
from backend.search.tokenize import tokenize_query, trim_query_punctuation
from backend.wiki.wikilinks import extract_wikilinks

DEFAULT_RESULTS = 20
MAX_RESULTS = 50
RRF_K = 60.0
MAX_SEARCH_FILES = 10_000
MIN_GRAPH_RESULT_RATIO = 0.15
MAX_GRAPH_RESULT_RATIO = 0.30
MAX_GRAPH_SEEDS = 20


def normalize_path(path: str) -> str:
    return path.replace("\\", "/")


def file_stem(path: str) -> str:
    return Path(path).stem


def relative_to_project(project_path: str, path) -> str:
    try:
        return Path(path).relative_to(Path(project_path)).as_posix()
    except ValueError:
        return Path(path).as_posix()


@dataclass
class GraphPage:
    path: str
    title: str
    links: list[str]
    content: str


def _extract_wikilinks_rust(content: str) -> list[str]:
    """search.rs extract_wikilinks: manual scan, `|` display part dropped."""
    links: list[str] = []
    rest = content
    while True:
        start = rest.find("[[")
        if start < 0:
            break
        rest = rest[start + 2:]
        end = rest.find("]]")
        if end < 0:
            break
        target = rest[:end].split("|")[0].strip()
        if target:
            links.append(target)
        rest = rest[end + 2:]
    return links


def search_response_to_api(response: dict) -> dict:
    """19828-contract camelCase shape (api_server.rs ProjectSearchResponse
    + ProjectSearchResult) — shared by the API router and the invoke bridge."""
    results = [
        {
            "path": r["path"],
            "title": r["title"],
            "snippet": r["snippet"],
            "titleMatch": r.get("title_match", False),
            "score": r.get("score", 0.0),
            **({"vectorScore": r["vector_score"]} if r.get("vector_score") is not None else {}),
            **({"images": r["images"]} if r.get("images") else {}),
            **({"content": r["content"]} if r.get("content") else {}),
            **({"graphRelatedTo": r["graph_related_to"]} if r.get("graph_related_to") else {}),
        }
        for r in response["results"]
    ]
    return {
        "mode": response["mode"],
        "tokenHits": response["tokenHits"],
        "vectorHits": response["vectorHits"],
        "graphHits": response["graphHits"],
        "results": results,
    }


def search_project_inner(
    project_path: str,
    query: str,
    top_k: int = DEFAULT_RESULTS,
    include_content: bool = False,
    query_embedding: list[float] | None = None,
) -> dict:
    if not query.strip():
        raise ValueError("query is required")
    limit = max(1, min(top_k, MAX_RESULTS))
    tokens = tokenize_query(query)
    effective_tokens = tokens if tokens else [query.strip().lower()]
    query_phrase = trim_query_punctuation(query.lower())

    results: list[dict] = []
    page_paths_by_stem: dict[str, str] = {}
    graph_pages: dict[str, GraphPage] = {}

    wiki_root = Path(project_path) / "wiki"
    if wiki_root.exists():
        searched_files = 0
        for entry in sorted(wiki_root.rglob("*.md")):
            if not entry.is_file():
                continue
            searched_files += 1
            if searched_files > MAX_SEARCH_FILES:
                break
            try:
                content = entry.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            stem = entry.stem
            previous = page_paths_by_stem.get(stem)
            if previous is not None:
                # duplicate wiki page stem (vector page_id collision
                # warning in the desktop; last wins there too)
                pass
            page_paths_by_stem[stem] = relative_to_project(project_path, entry)
            relative_path = relative_to_project(project_path, entry)
            hit = score_file(
                entry.name, content, effective_tokens, query_phrase, query,
                include_content=include_content,
            )
            graph_pages[normalize_path(relative_path)] = GraphPage(
                path=relative_path,
                title=hit["title"] if hit else entry.stem,
                links=_extract_wikilinks_rust(content),
                content=content,
            )
            if hit is not None:
                hit["path"] = relative_path
                results.append(hit)

    # Sort by score desc, then path asc (Rust BTreeMap/rank parity).
    results.sort(key=lambda r: (-r["score"], r["path"]))
    token_rank = {normalize_path(r["path"]): idx + 1 for idx, r in enumerate(results)}

    # v0: no vector leg. vector fields stay degenerate.
    vector_hits = 0
    vector_score: dict[str, float] = {}

    if query_embedding:
        # Accepted for contract compatibility; LanceDB lands later.
        pass

    graph_hits = blend_graph_results(results, graph_pages, limit, vector_hits)

    return {
        "mode": search_mode(not token_rank, vector_hits, graph_hits),
        "tokenHits": len(token_rank),
        "vectorHits": vector_hits,
        "graphHits": graph_hits,
        "results": results,
    }


def search_mode(token_rank_empty: bool, vector_hits: int, graph_hits: int) -> str:
    if graph_hits > 0:
        return "hybrid"
    if vector_hits == 0:
        return "keyword"
    if token_rank_empty:
        return "vector"
    return "hybrid"


def graph_result_quota(limit: int, vector_hits: int) -> int:
    if limit < 2:
        return 0
    vector_coverage = min(vector_hits, limit) / limit
    ratio = MAX_GRAPH_RESULT_RATIO - (
        MAX_GRAPH_RESULT_RATIO - MIN_GRAPH_RESULT_RATIO
    ) * vector_coverage
    return max(1, min(limit - 1, int(limit * ratio + 0.999)))


def normalize_graph_alias(value: str) -> str:
    return (
        value.split("#")[0]
        .strip()
        .removesuffix(".md")
        .replace("\\", "/")
        .replace(" ", "-")
        .lower()
    )


def blend_graph_results(
    ranked_results: list[dict],
    pages: dict[str, GraphPage],
    limit: int,
    vector_hits: int,
) -> int:
    """Port of blend_graph_results (search.rs:517-655)."""
    if not ranked_results or not pages:
        del ranked_results[limit:]
        return 0

    aliases: dict[str, str] = {}
    for normalized_path, page in pages.items():
        wiki_relative = page.path.removeprefix("wiki/")
        stem = file_stem(page.path)
        for alias in (page.path, wiki_relative, stem, page.title):
            aliases[normalize_graph_alias(alias)] = normalized_path

    adjacency: dict[str, set[str]] = {}
    for source, page in pages.items():
        for link in page.links:
            target = aliases.get(normalize_graph_alias(link))
            if target is None or source == target:
                continue
            adjacency.setdefault(source, set()).add(target)
            adjacency.setdefault(target, set()).add(source)

    seed_paths = [
        normalize_path(r["path"]) for r in ranked_results[: min(limit, MAX_GRAPH_SEEDS)]
    ]
    seed_set = set(seed_paths)
    candidate_scores: dict[str, float] = {}
    candidate_seeds: dict[str, set[str]] = {}

    for rank, seed in enumerate(seed_paths):
        neighbors = adjacency.get(seed)
        if not neighbors:
            continue
        for neighbor in neighbors:
            if neighbor in seed_set:
                continue
            candidate_scores[neighbor] = candidate_scores.get(neighbor, 0.0) + 1.0 / (rank + 1)
            seed_page = pages.get(seed)
            if seed_page is not None:
                candidate_seeds.setdefault(neighbor, set()).add(seed_page.title)

    candidates = sorted(
        candidate_scores.items(), key=lambda kv: (-kv[1], kv[0])
    )[: graph_result_quota(limit, vector_hits)]
    if not candidates:
        del ranked_results[limit:]
        return 0

    selected_paths = {path for path, _ in candidates}
    existing: dict[str, dict] = {}
    ranked_paths: list[str] = []
    for result in ranked_results:
        path = normalize_path(result["path"])
        ranked_paths.append(path)
        existing[path] = result

    graph_count = len(candidates)
    base_limit = limit - graph_count
    base_results: list[dict] = []
    for path in ranked_paths:
        if path in selected_paths:
            continue
        result = existing.get(path)
        if result is not None:
            base_results.append(result)
        if len(base_results) >= base_limit:
            break

    for path, graph_score in candidates:
        if path in existing:
            result = existing.pop(path)
            result["graph_related_to"] = sorted(
                candidate_seeds.get(path, set())
            )
            base_results.append(result)
            continue
        page = pages.get(path)
        if page is None:
            continue
        related_titles = sorted(candidate_seeds.get(path, set()))
        related = ", ".join(related_titles)
        base_results.append({
            "path": page.path,
            "title": page.title,
            "snippet": f"Graph neighbor of {related}",
            "title_match": False,
            "score": graph_score / (RRF_K + 1.0),
            "vector_score": None,
            "images": _extract_image_refs(page.content),
            "content": page.content,
            "graph_related_to": related_titles,
        })

    ranked_results[:] = base_results
    return graph_count


def _extract_image_refs(content: str) -> list[dict]:
    from backend.search.scoring import extract_image_refs

    return extract_image_refs(content)
