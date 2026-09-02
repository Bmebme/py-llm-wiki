"""Ingest prompts — byte-compatible port of llm_wiki src/lib/ingest.ts.

buildAnalysisPrompt (2148-2204), buildGenerationPrompt (2209-2377),
buildReviewSuggestionPrompt (2379-2436). The prompt text is the product:
do not "improve" wording — behavior parity depends on it.
"""

from __future__ import annotations

from datetime import date

from backend.core.context_budget import compute_context_budget

# src/lib/wiki-page-types.ts GENERATION_WIKI_TYPES
GENERATION_WIKI_TYPES = [
    "source", "entity", "concept", "comparison", "query", "synthesis",
    "thesis", "methodology", "finding",
]


def current_wiki_date() -> str:
    return date.today().isoformat()


def language_rule(source_content: str = "") -> str:
    """TS languageRule() reads the configured outputLanguage from the
    settings store and passes the source text as detection fallback."""
    from backend.core import settings_store
    from backend.language.output import build_language_directive

    state = settings_store.load() or {}
    configured = state.get("outputLanguage")
    return build_language_directive(configured, source_content)


def trim_long_text(text: str, max_chars: int) -> str:
    """Port of trimLongText (ingest.ts:2697-2700)."""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n\n[...trimmed for prompt budget...]"


def build_analysis_prompt(
    purpose: str,
    index: str,
    source_content: str = "",
    schema: str = "",
) -> str:
    """Port of buildAnalysisPrompt (ingest.ts:2148-2204)."""
    return "\n".join(
        filter(None, [
            "You are an expert research analyst. Read the source document and produce a structured analysis.",
            "Do not output chain-of-thought, hidden reasoning, or a thinking transcript. Reason internally and write only the concise final analysis.",
            "",
            language_rule(source_content),
            "",
            "Your analysis should cover:",
            "",
            "## Key Entities",
            "List people, organizations, products, datasets, tools mentioned. For each:",
            "- Name and type",
            "- Role in the source (central vs. peripheral)",
            "- Whether it likely already exists in the wiki (check the index)",
            "",
            "## Key Concepts",
            "List theories, methods, techniques, phenomena. For each:",
            "- Name and brief definition",
            "- Why it matters in this source",
            "- Whether it likely already exists in the wiki",
            "",
            "## Main Arguments & Findings",
            "- What are the core claims or results?",
            "- What evidence supports them?",
            "- How strong is the evidence?",
            "- Which named subject is each claim about? Do not transfer claims, limits, or evaluations from one entity/model/product/method to another just because they share keywords.",
            "",
            "## Connections to Existing Wiki",
            "- What existing pages does this source relate to?",
            "- Does it strengthen, challenge, or extend existing knowledge?",
            "",
            "## Contradictions & Tensions",
            "- Does anything in this source conflict with existing wiki content?",
            "- Are there internal tensions or caveats?",
            "",
            "## Recommendations",
            "- What wiki pages should be created or updated?",
            "- If the project schema (below) defines page types beyond entity/concept (e.g. goal, habit, reflection, finding, decision, meeting), and the source genuinely contains matching content, recommend pages of those types — name the type explicitly. Only when the source actually supports it; never invent goals/habits/journal entries that aren't in the source.",
            "- What should be emphasized vs. de-emphasized?",
            "- Any open questions worth flagging for the user?",
            "",
            "Be thorough but concise. Focus on what's genuinely important.",
            "",
            "If a folder context is provided, use it as a hint for categorization — the folder structure often reflects the user's organizational intent (e.g., 'papers/energy' suggests the file is an energy-related paper).",
            "",
            f"## Project Schema (page types available — map source content to schema-defined types when it fits)\n{schema}" if schema else "",
            f"## Wiki Purpose (for context)\n{purpose}" if purpose else "",
            f"## Current Wiki Index (for checking existing content)\n{index}" if index else "",
        ])
    )


def build_generation_prompt(
    schema: str,
    purpose: str,
    index: str,
    source_file_name: str,
    overview: str | None = None,
    source_content: str = "",
    source_summary_path: str | None = None,
) -> str:
    """Port of buildGenerationPrompt (ingest.ts:2209-2377)."""
    # Use original filename (without extension) as the source summary page name
    source_base_name = re_sub_ext(source_file_name)
    summary_path = source_summary_path or f"wiki/sources/{source_base_name}.md"
    today = current_wiki_date()

    schema_block = (
        "\n".join([
            "## Project Schema and Routing (AUTHORITATIVE)",
            schema,
            "",
            "Use this schema as the primary routing rule for page types and directories.",
            "If it defines custom folders or distinctions (for example people, technologies, organizations, methods, or cases), write pages into those schema-defined folders instead of forcing them into wiki/entities/ or wiki/concepts/.",
            "Use wiki/entities/ and wiki/concepts/ only when the schema does not provide a more specific destination.",
            "Every generated page's frontmatter type must match the schema directory used in its FILE path.",
        ])
        if schema else ""
    )

    return "\n".join(
        filter(None, [
            "You are a wiki maintainer. Based on the analysis provided, generate wiki files.",
            "Do not output chain-of-thought, hidden reasoning, or explanatory preamble. Reason internally and output only the requested FILE/REVIEW blocks.",
            "",
            language_rule(source_content),
            "",
            "## IMPORTANT: Source File",
            f"The original source file is: **{source_file_name}**",
            "All wiki pages generated from this source MUST include this filename in their frontmatter `sources` field.",
            f"Today's date is **{today}**. Use this exact date for all new `created`, `updated`, and wiki/log.md ingest dates.",
            "",
            schema_block,
            "",
            "## What to generate",
            "",
            f"1. A source summary page at **{summary_path}** (MUST use this exact path)",
            "2. Entity or schema-defined typed pages for key named things identified in the analysis. Prefer schema-defined directories when present; otherwise use wiki/entities/.",
            "3. Concept or schema-defined typed pages for key ideas, methods, techniques, and abstractions. Prefer schema-defined directories when present; otherwise use wiki/concepts/.",
            "4. A log entry for wiki/log.md (just the new entry to append, format: ## [YYYY-MM-DD] ingest | Title)",
            "Do not generate wiki/index.md or wiki/overview.md. The application maintains aggregate navigation separately so large wikis are never rewritten through model output.",
            "",
            "## Frontmatter Rules (CRITICAL — parser is strict)",
            "",
            "Every page begins with a YAML frontmatter block. Format rules, in order of importance:",
            "",
            "1. The VERY FIRST line of the file MUST be exactly `---` (three hyphens, nothing else).",
            "   Do NOT wrap the file in a ```yaml ... ``` code fence.",
            "   Do NOT prefix it with a `frontmatter:` key or any other line.",
            "2. Each frontmatter line is a `key: value` pair on its own line.",
            "3. The frontmatter ends with another `---` line on its own.",
            "4. The next line after the closing `---` is the start of the page body.",
            "5. Arrays use the standard YAML inline form `[a, b, c]` (no outer brackets around each item).",
            "   Wikilinks belong in the BODY only — never write `related: [[a]], [[b]]` (invalid YAML);",
            "   write `related: [a, b]` with bare slugs.",
            "",
            "Required fields and types:",
            f"  • type     — one of the known types ({' | '.join(GENERATION_WIKI_TYPES)}), or a custom type explicitly defined by the project schema",
            '  • title    — string (quote it if it contains a colon, e.g. `title: "Foo: Bar"`)',
            f"  • created  — {today} for new pages (YYYY-MM-DD, no quotes)",
            f"  • updated  — {today} for new pages (same as created)",
            "  • tags     — array of bare strings: `tags: [microbiology, ai]`",
            "  • related  — array of bare wiki page slugs: `related: [foo, bar-baz]`. Do NOT include",
            '               `wiki/`, `.md`, or `[[…]]` here — slugs only.',
            f'  • sources  — array of source filenames; MUST include "{source_file_name}".',
            "",
            "Concrete example of a complete, parseable page (everything between the two `---` lines",
            "is the frontmatter; the heading and prose below are the body):",
            "",
            "    ---",
            "    type: entity",
            "    title: Example Entity",
            f"    created: {today}",
            f"    updated: {today}",
            "    tags: [example, demo]",
            "    related: [related-slug-1, related-slug-2]",
            f'    sources: ["{source_file_name}"]',
            "    ---",
            "",
            "    # Example Entity",
            "",
            "    Body content goes here. Use [[wikilink]] syntax in the body for cross-references.",
            "",
            "Other rules:",
            "- Use [[wikilink]] syntax in the BODY for cross-references between pages",
            "- If you include images, use wiki-root-relative paths such as `media/source-slug/image.png`; never output absolute filesystem paths.",
            "- Preserve subject boundaries: when a source discusses multiple entities/models/products/methods, keep claims, evaluations, limitations, benchmark results, and recommendations attached to the exact subject they describe.",
            "- Do not merge or generalize a claim about one subject into another subject's page solely because they share terms (for example context window size, benchmark name, dataset, architecture, or feature name).",
            "- If a page needs to mention another subject for comparison, write it explicitly as a comparison and cite which source/frontmatter `sources` entry supports that statement.",
            "- Use kebab-case for Latin-script filenames; for Chinese/Japanese/Korean titles keep the CJK characters (do NOT romanize to pinyin/romaji or translate to English)",
            "- Derive filenames from the page title in the mandatory output language, but short proper nouns and technical identifiers take precedence: preserve names such as OpenAI, GPT-5, Transformer, CLIP, ImageNet, PyTorch, CUDA, GitHub, arXiv, React, LanceDB, AnyTXT, MinerU, model names, dataset names, tool names, and code identifiers in their standard original form. Do not put raw URLs, citation strings, or full paper titles directly into file paths; convert surrounding descriptive prose to a safe readable title. For Chinese/Japanese/Korean prose titles, keep readable CJK characters in the filename instead of translating the slug to English.",
            "- Preserve structured source data verbatim: copy SQL DDL / CREATE TABLE statements, schema definitions, API signatures, configuration, and tabular data into fenced code blocks (or Markdown tables) in the source summary page instead of paraphrasing them. Exact column names, types, constraints, primary/foreign keys, and indexes must survive ingest — a prose-only summary that drops them loses the structure the user imported the source to keep.",
            "- Follow the analysis recommendations on what to emphasize",
            "- If the analysis found connections to existing pages, add cross-references",
            "",
            "## Review block types",
            "",
            "After all FILE blocks, optionally emit REVIEW blocks for anything that needs human judgment:",
            "",
            "- contradiction: the analysis found conflicts with existing wiki content",
            "- duplicate: an entity/concept might already exist under a different name in the index",
            "- missing-page: an important concept is referenced but has no dedicated page",
            "- suggestion: ideas for further research, related sources to look for, or connections worth exploring",
            "",
            "Only create reviews for things that genuinely need human input. Don't create trivial reviews.",
            "",
            "## OPTIONS allowed values (only these predefined labels):",
            "",
            "- contradiction: OPTIONS: Create Page | Skip",
            "- duplicate: OPTIONS: Create Page | Skip",
            "- missing-page: OPTIONS: Create Page | Skip",
            "- suggestion: OPTIONS: Create Page | Skip",
            "",
            "The user also has a 'Deep Research' button (auto-added by the system) that triggers web search.",
            "Do NOT invent custom option labels. Only use 'Create Page' and 'Skip'.",
            "",
            "For suggestion and missing-page reviews, the SEARCH field must contain 2-3 web search queries",
            "(keyword-rich, specific, suitable for a search engine — NOT titles or sentences). Example:",
            "  SEARCH: automated technical debt detection AI generated code | software quality metrics LLM code generation | static analysis tools agentic software development",
            "",
            f"## Wiki Purpose\n{purpose}" if purpose else "",
            f"## Current Wiki Index (preserve all existing entries, add new ones)\n{index}" if index else "",
            f"## Current Overview (update this to reflect the new source)\n{overview}" if overview else "",
            "",
            # ── OUTPUT FORMAT MUST BE THE LAST SECTION — models weight recent instructions highest ──
            "## Output Format (MUST FOLLOW EXACTLY — this is how the parser reads your response)",
            "",
            "Your ENTIRE response consists of FILE blocks followed by optional REVIEW blocks. Nothing else.",
            "",
            "FILE block template:",
            "```",
            "---FILE: wiki/path/to/page.md---",
            "(complete file content with YAML frontmatter)",
            "---END FILE---",
            "```",
            "",
            "REVIEW block template (optional, after all FILE blocks):",
            "```",
            "---REVIEW: type | Title---",
            "Description of what needs the user's attention.",
            "OPTIONS: Create Page | Skip",
            "PAGES: wiki/page1.md, wiki/page2.md",
            "SEARCH: query 1 | query 2 | query 3",
            "---END REVIEW---",
            "```",
            "",
            "## Output Requirements (STRICT — deviations will cause parse failure)",
            "",
            "1. The FIRST character of your response MUST be `-` (the opening of `---FILE:`).",
            "2. DO NOT output any preamble such as \"Here are the files:\", \"Based on the analysis...\", or any introductory prose.",
            "3. DO NOT echo or restate the analysis — that was stage 1's job. Your job is to emit FILE blocks.",
            "4. DO NOT output markdown tables, bullet lists, or headings outside of FILE/REVIEW blocks.",
            "5. DO NOT output any trailing commentary after the last `---END FILE---` or `---END REVIEW---`.",
            "6. Between blocks, use only blank lines — no prose.",
            "7. FILE block prose (body, explanations, descriptions, section text) must use the mandatory output language specified below. Preserve proper nouns, acronyms, model names, dataset names, tool/library names, code identifiers, URLs, file names, citation strings, paper titles, and technical terms with no widely-used localized equivalent in their standard original form, including in page names and section headings.",
            "",
            "If you start with anything other than `---FILE:`, the entire response will be discarded.",
            "",
            # Repeat the language directive at the very end so it wins the
            # "most recent instruction" tie-breaker.
            "---",
            "",
            language_rule(source_content),
        ])
    )


def build_review_suggestion_prompt(
    purpose: str,
    index: str,
    source_identity: str,
    analysis: str,
    source_context: str,
    generation: str,
    max_context_size: int | None,
) -> str:
    """Port of buildReviewSuggestionPrompt (ingest.ts:2379-2436)."""
    max_ctx = compute_context_budget(max_context_size)["maxCtx"]
    section_cap = max(4_000, int(max_ctx * 0.15))
    index_cap = max(3_000, int(section_cap * 0.8))
    return "\n".join(
        filter(None, [
            "You are identifying high-value follow-up research items for a personal wiki.",
            "Do not output chain-of-thought, hidden reasoning, or explanatory preamble.",
            "",
            language_rule(source_context),
            "",
            "Your job is NOT to generate wiki pages. The wiki page generation already happened.",
            "Output only REVIEW blocks for unresolved knowledge gaps that deserve human attention or Deep Research.",
            "",
            "Create REVIEW blocks only for genuinely useful follow-up work:",
            "- missing-page: an important entity/concept is referenced but still lacks a dedicated page",
            "- suggestion: a research question, source type, or comparison that would materially improve the wiki",
            "- contradiction: a conflict or tension that requires user judgment",
            "- duplicate: likely duplicate pages/names that need user review",
            "",
            "Prefer 1-5 high-signal reviews. If there is nothing worth reviewing, output nothing.",
            "For suggestion and missing-page reviews, include a SEARCH line with 2-3 keyword-rich web search queries separated by ` | `.",
            "Use only these options: OPTIONS: Create Page | Skip",
            "",
            "REVIEW block template:",
            "```",
            "---REVIEW: suggestion | Precise title---",
            "Concise description of the gap and why it matters.",
            "OPTIONS: Create Page | Skip",
            "PAGES: wiki/page1.md, wiki/page2.md",
            "SEARCH: query 1 | query 2 | query 3",
            "---END REVIEW---",
            "```",
            "",
            "Return REVIEW blocks only. Do not output FILE blocks. Do not wrap the response in markdown fences.",
            "",
            f"## Wiki Purpose\n{purpose}" if purpose else "",
            f"## Current Wiki Index\n{trim_long_text(index, index_cap)}" if index else "",
            "",
            f"## Source\n{source_identity}",
            "",
            "## Stage 1 Analysis",
            trim_long_text(analysis, section_cap),
            "",
            "## Source Context",
            trim_long_text(source_context, section_cap),
            "",
            "## Generated Wiki Output",
            trim_long_text(generation, section_cap),
        ])
    )


def re_sub_ext(filename: str) -> str:
    import re

    return re.sub(r"\.[^.]+$", "", filename)
