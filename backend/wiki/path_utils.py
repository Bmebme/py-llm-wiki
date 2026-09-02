"""Path helpers ported from llm_wiki src/lib/path-utils.ts.

normalizePath (5-7), getFileName (22-25), getFileStem (30-34).
"""

from __future__ import annotations


def normalize_path(p: str) -> str:
    """Port of normalizePath: forward slashes only, no other rewriting.

    Note this differs from backend.core.file_service.normalize_path, which
    additionally strips trailing slashes — the TS original does not.
    """
    return p.replace("\\", "/")


def get_file_name(p: str) -> str:
    """Port of getFileName: last slash-separated segment (backslashes normalized)."""
    normalized = p.replace("\\", "/")
    parts = normalized.split("/")
    return parts[-1] if parts else p


def get_file_stem(p: str) -> str:
    """Port of getFileStem: filename without extension (last dot only)."""
    name = get_file_name(p)
    last_dot = name.rfind(".")
    return name[:last_dot] if last_dot > 0 else name
