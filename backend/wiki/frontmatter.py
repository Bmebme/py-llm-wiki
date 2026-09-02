"""Frontmatter parsing ported from llm_wiki src/lib/frontmatter.ts.

parseFrontmatter (37-65): strict top-of-file match first, then an
unanchored fallback locator for LLM-corrupted pages with a junk prefix
(code fence opener / `frontmatter:` key), capped at 6 prefix lines.
Two-pass YAML parse with a single round of "wikilink-list" repair
(`related: [[a]], [[b]]` -> `related: ["[[a]]", "[[b]]"]`).

The TS side uses js-yaml with the JSON_SCHEMA; here pyyaml's
safe_load is used. Values are normalized to `str | list[str]`
(stringifyScalar, 193-204): numbers/booleans stringified, dates
(which js-yaml JSON_SCHEMA keeps as strings but pyyaml parses as
datetime) rendered via isoformat so `created: 2026-04-09` yields
"2026-04-09" in both implementations.
"""

from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass

import yaml

# Strict, anchored detector. Both fence lines must be on their own line.
FM_BLOCK_STRICT_RE = re.compile(r"^---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)")

# Same shape as STRICT but unanchored — used only when STRICT failed.
FM_BLOCK_ANYWHERE_RE = re.compile(r"\n---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)")

MAX_PREFIX_LINES_BEFORE_FRONTMATTER = 6

FRONTMATTER_TYPE = dict[str, str | list[str]]


@dataclass
class FrontmatterParse:
    frontmatter: FRONTMATTER_TYPE | None
    body: str
    raw_block: str


def parse_frontmatter(content: str) -> FrontmatterParse:
    """Port of parseFrontmatter (frontmatter.ts:37-65)."""
    located = _locate_frontmatter_block(content)
    if located is None:
        return FrontmatterParse(frontmatter=None, body=content, raw_block="")

    yaml_payload, raw_block, body = located

    parsed: object
    try:
        parsed = yaml.safe_load(yaml_payload)
    except yaml.YAMLError:
        try:
            parsed = yaml.safe_load(_repair_wikilink_lists(yaml_payload))
        except yaml.YAMLError:
            return FrontmatterParse(frontmatter=None, body=body, raw_block=raw_block)

    return FrontmatterParse(frontmatter=_normalize(parsed), body=body, raw_block=raw_block)


def _locate_frontmatter_block(
    content: str,
) -> tuple[str, str, str] | None:
    """Port of locateFrontmatterBlock (frontmatter.ts:76-130)."""
    strict = FM_BLOCK_STRICT_RE.match(content)
    if strict:
        return (strict.group(1), strict.group(0), content[len(strict.group(0)):])

    fallback = FM_BLOCK_ANYWHERE_RE.search(content)
    if not fallback:
        return None

    open_idx = fallback.start() + 1  # skip the leading `\n`
    if _line_number_at(content, open_idx) > MAX_PREFIX_LINES_BEFORE_FRONTMATTER:
        return None

    raw_block = content[open_idx:open_idx + len(fallback.group(0)) - 1]
    body_after_fm = content[open_idx + len(raw_block):]

    # If the prefix that pushed us into the fallback is a ```yaml /
    # ```yml (or bare ```) code fence opener, strip the matching
    # CLOSING fence at the head of the body too (frontmatter.ts:106-123).
    prefix = content[:open_idx]
    if re.match(r"^\s*```(?:yaml|yml)?\s*\r?\n$", prefix, re.IGNORECASE):
        stripped = re.sub(r"^\s*```\s*(?:\r?\n|$)", "", body_after_fm, count=1)
        return (fallback.group(1), raw_block, stripped)

    return (fallback.group(1), raw_block, body_after_fm)


def _line_number_at(s: str, index: int) -> int:
    """1-based line number that a given character index sits on (133-139)."""
    return s.count("\n", 0, index) + 1


def _repair_wikilink_lists(payload: str) -> str:
    """Port of repairWikilinkLists (frontmatter.ts:156-172)."""
    line_re = re.compile(
        r"^(\s*[A-Za-z_][\w-]*\s*:\s*)(\[\[[^\]]+\]\](?:\s*,\s*\[\[[^\]]+\]\])+)\s*$"
    )
    out: list[str] = []
    for line in payload.split("\n"):
        m = line_re.match(line)
        if not m:
            out.append(line)
            continue
        items = [
            f'"{s.strip()}"'
            for s in m.group(2).split(",")
            if s.strip()
        ]
        out.append(f"{m.group(1)}[{', '.join(items)}]")
    return "\n".join(out)


def _normalize(parsed: object) -> FRONTMATTER_TYPE | None:
    """Port of normalize (frontmatter.ts:180-191)."""
    if not parsed or not isinstance(parsed, dict):
        return None
    out: FRONTMATTER_TYPE = {}
    for key, value in parsed.items():
        if isinstance(value, list):
            out[key] = [_stringify_scalar(v) for v in value]
            continue
        out[key] = _stringify_scalar(value)
    return out


def _stringify_scalar(v: object) -> str:
    """Port of stringifyScalar (frontmatter.ts:193-204)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bool):
        # js-yaml String(true) is "true".
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    # Object / nested array -> JSON so the user still sees something.
    try:
        return json.dumps(v)
    except (TypeError, ValueError):
        return str(v)
