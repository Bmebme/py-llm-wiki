"""Project create/open/validate — port of llm_wiki src-tauri/src/commands/project.rs.

The scaffold content is byte-identical so generated wikis are
indistinguishable from the desktop app's output (and templates from
src/lib/templates.ts can be layered on later).
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

from backend.core import file_service
from backend.core.file_service import FsError

PROJECT_DIRS = [
    "raw/sources",
    "raw/assets",
    "wiki/entities",
    "wiki/concepts",
    "wiki/sources",
    "wiki/queries",
    "wiki/comparisons",
    "wiki/synthesis",
]

# project.rs:42-118
SCHEMA_CONTENT = """# Wiki Schema

## Page Types

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things (models, companies, people, datasets) |
| concept | wiki/concepts/ | Ideas, techniques, phenomena |
| source | wiki/sources/ | Papers, articles, talks, blog posts |
| query | wiki/queries/ | Open questions under investigation |
| comparison | wiki/comparisons/ | Side-by-side analysis of related entities |
| synthesis | wiki/synthesis/ | Cross-cutting summaries and conclusions |

## Naming Conventions

- Files: `kebab-case.md`
- Entities: match official name where possible (e.g., `gpt-4.md`, `openai.md`)
- Concepts: descriptive noun phrases (e.g., `chain-of-thought.md`)
- Sources: `author-year-slug.md` (e.g., `wei-2022-chain-of-thought.md`)
- Queries: question as slug (e.g., `does-scale-improve-reasoning.md`)

## Frontmatter

All pages must include YAML frontmatter:

```yaml
---
type: entity | concept | source | query | comparison | synthesis | overview
title: Human-readable title
tags: []
related: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

Source pages also include:
```yaml
authors: []
year: YYYY
url: ""
venue: ""
```

## Index Format

`wiki/index.md` lists all pages grouped by type. Each entry:
```
- [[page-slug]] — one-line description
```

## Log Format

`wiki/log.md` records research activity in reverse chronological order:
```
## YYYY-MM-DD

- Action taken / finding noted
```

## Cross-referencing Rules

- Use `[[page-slug]]` syntax to link between wiki pages
- Every entity and concept should appear in `wiki/index.md`
- Queries link to the sources and concepts they draw on
- Synthesis pages cite all contributing sources via `related:`

## Contradiction Handling

When sources contradict each other:
1. Note the contradiction in the relevant concept or entity page
2. Create or update a query page to track the open question
3. Link both sources from the query page
4. Resolve in a synthesis page once sufficient evidence exists
"""

# project.rs:122-151
PURPOSE_CONTENT = """# Project Purpose

## Goal

<!-- What are you trying to understand or build? -->

## Key Questions

<!-- List the primary questions driving this research -->

1.
2.
3.

## Scope

<!-- What is in scope? What is explicitly out of scope? -->

**In scope:**
-

**Out of scope:**
-

## Thesis

<!-- Your current working hypothesis or conclusion (update as research progresses) -->

> TBD
"""

# project.rs:155-168
INDEX_CONTENT = """# Wiki Index

## Entities

## Concepts

## Sources

## Queries

## Comparisons

## Synthesis
"""

# project.rs:183-193
OVERVIEW_CONTENT = """---
type: overview
title: Project Overview
tags: []
related: []
---

# Overview

<!-- Provide a high-level summary of what this wiki covers and its current state. Update regularly as understanding deepens. -->
"""

# project.rs:201-235
OBSIDIAN_APP_CONFIG = """{
  "attachmentFolderPath": "raw/assets",
  "userIgnoreFilters": [
    ".cache",
    ".llm-wiki",
    ".superpowers"
  ],
  "useMarkdownLinks": false,
  "newLinkFormat": "shortest",
  "showUnsupportedFiles": false
}"""

OBSIDIAN_APPEARANCE = """{
  "baseFontSize": 16,
  "theme": "obsidian"
}"""

OBSIDIAN_CORE_PLUGINS = """{
  "file-explorer": true,
  "global-search": true,
  "graph": true,
  "backlink": true,
  "tag-pane": true,
  "page-preview": true,
  "outgoing-link": true,
  "starred": true
}"""


class Project:
    __slots__ = ("id", "name", "path")

    def __init__(self, id_: str, name: str, path: str):
        self.id = id_
        self.name = name
        self.path = path

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "path": self.path}


def create_project(name: str, parent_path: str) -> Project:
    """Port of create_project_impl (project.rs:16-242)."""
    root = Path(parent_path) / name
    if root.exists():
        raise FsError(f"Directory already exists: '{root}'")

    for dirname in PROJECT_DIRS:
        try:
            (root / dirname).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FsError(f"Failed to create directory '{dirname}': {exc}") from exc

    today = date.today().isoformat()

    _write_file_inner(root / "schema.md", SCHEMA_CONTENT)
    _write_file_inner(root / "purpose.md", PURPOSE_CONTENT)
    _write_file_inner(root / "wiki/index.md", INDEX_CONTENT)
    _write_file_inner(
        root / "wiki/log.md",
        f"""# Research Log

## {today}

- Project created
""",
    )
    _write_file_inner(root / "wiki/overview.md", OVERVIEW_CONTENT)

    try:
        (root / ".obsidian").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FsError(f"Failed to create .obsidian: {exc}") from exc
    _write_file_inner(root / ".obsidian/app.json", OBSIDIAN_APP_CONFIG)
    _write_file_inner(root / ".obsidian/appearance.json", OBSIDIAN_APPEARANCE)
    _write_file_inner(root / ".obsidian/core-plugins.json", OBSIDIAN_CORE_PLUGINS)

    return Project(id_="", name=name, path=root.as_posix())


def open_project(path: str) -> Project:
    """Port of open_project (project.rs:244-264)."""
    root = Path(path)
    validate_wiki_project_root(root)
    name = root.name or "Unknown"
    return Project(id_="", name=name, path=path.replace("\\", "/"))


def validate_wiki_project_root(root: Path) -> None:
    """Port of validate_wiki_project_root (project.rs:342-364)."""
    if not root.exists():
        raise FsError(f"Path does not exist: '{root}'")
    if not root.is_dir():
        raise FsError(f"Path is not a directory: '{root}'")
    if not (root / "schema.md").exists():
        raise FsError(f"Not a valid wiki project (missing schema.md): '{root}'")
    if not (root / "wiki").is_dir():
        raise FsError(f"Not a valid wiki project (missing wiki/ directory): '{root}'")


def ensure_project_id(project_path: str) -> str:
    """Read or create .llm-wiki/project.json {id, createdAt} — port of
    llm_wiki src/lib/project-identity.ts."""
    llm_wiki_dir = Path(project_path) / ".llm-wiki"
    project_file = llm_wiki_dir / "project.json"
    if project_file.exists():
        try:
            parsed = json.loads(project_file.read_text(encoding="utf-8"))
            pid = parsed.get("id")
            if pid:
                return str(pid)
        except (OSError, json.JSONDecodeError):
            pass
    llm_wiki_dir.mkdir(parents=True, exist_ok=True)
    pid = str(uuid.uuid4())
    import time

    payload = {"id": pid, "createdAt": int(time.time() * 1000)}
    tmp = project_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(project_file)
    return pid


def read_project_id(project_path: str) -> str | None:
    project_file = Path(project_path) / ".llm-wiki" / "project.json"
    try:
        parsed = json.loads(project_file.read_text(encoding="utf-8"))
        pid = parsed.get("id")
        return str(pid) if pid else None
    except (OSError, json.JSONDecodeError):
        return None


def project_name_from_path(path: str) -> str:
    return Path(path).name or "Project"


def _write_file_inner(path: Path, contents: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    except OSError as exc:
        raise FsError(f"Failed to write file '{path}': {exc}") from exc
