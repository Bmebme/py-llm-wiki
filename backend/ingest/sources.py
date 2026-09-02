"""Source-path helpers ported from llm_wiki src/lib/source-lifecycle.ts.

  INGESTABLE_SOURCE_EXTENSIONS      (45-78)
  isIngestableSourcePath            (232-239)
  folderContextForSourcePath        (241-253)
"""

from __future__ import annotations

# source-lifecycle.ts:45-78 — every extension the desktop app can ingest.
INGESTABLE_SOURCE_EXTENSIONS = frozenset({
    "md",
    "mdx",
    "txt",
    "pdf",
    "doc",
    "docx",
    "docm",
    "ppt",
    "pps",
    "pot",
    "pptx",
    "pptm",
    "ppsx",
    "ppsm",
    "xlsx",
    "xlsm",
    "xlsb",
    "odt",
    "odp",
    "ods",
    "xls",
    "csv",
    "json",
    "html",
    "htm",
    "rtf",
    "xml",
    "yaml",
    "yml",
    "epub",
    "mobi",
    "org",
})


def _normalize_path(path: str) -> str:
    """normalizePath (path-utils.ts:5-7): backslashes -> forward slashes.

    Deliberately does NOT strip trailing slashes — the TS original does
    not either (unlike backend.core.file_service.normalize_path).
    """
    return path.replace("\\", "/")


def is_ingestable_source_path(path: str) -> bool:
    """Port of isIngestableSourcePath (source-lifecycle.ts:232-239).

    False for anything under a `.cache` directory segment, dotfiles, and
    extensions outside INGESTABLE_SOURCE_EXTENSIONS (extension compared
    case-insensitively).
    """
    normalized = _normalize_path(path)
    if ".cache" in normalized.split("/"):
        return False
    parts = normalized.split("/")
    file_name = parts[-1] if parts else ""
    if not file_name or file_name.startswith("."):
        return False
    ext = file_name.split(".")[-1].lower() if "." in file_name else ""
    return ext in INGESTABLE_SOURCE_EXTENSIONS if ext else False


def folder_context_for_source_path(source_path: str, sources_root: str = "raw/sources") -> str:
    """Port of folderContextForSourcePath (source-lifecycle.ts:241-253).

    Derives the folder breadcrumb for a source file: the parent folder
    chain below the sources root, joined with " > ", e.g.
    "/tmp/project/raw/sources/reports/2026/report.pdf" -> "reports > 2026".
    A path already containing a "/raw/sources/" marker is sliced at that
    marker (so project prefixes never leak into the context); any other
    path is used verbatim (parent chain of the whole path).
    """
    path = _normalize_path(source_path)
    root = _normalize_path(sources_root)
    raw_marker = "/raw/sources/"
    if path.startswith(f"{root}/"):
        rel = path[len(root) + 1:]
    elif raw_marker in path:
        rel = path[path.index(raw_marker) + len(raw_marker):]
    else:
        rel = path
    parts = rel.split("/")
    parts.pop()
    return " > ".join(parts)
