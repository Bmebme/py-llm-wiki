"""Output-language selection and the MANDATORY OUTPUT LANGUAGE directive.

Port of llm_wiki's ``src/lib/output-language.ts``. The directive and
reminder texts must match the TS byte-for-byte — they are prompts.

The TS reads the user's configured output language from a Zustand store;
this port takes it as the ``configured`` argument (None or "auto" means
auto-detect from the fallback text).
"""

from __future__ import annotations

from backend.language.detect import detect_language
from backend.language.metadata import get_language_prompt_name


def get_output_language(configured: str | None, fallback_text: str = "") -> str:
    """Port of getOutputLanguage (output-language.ts:11-17).

    Explicit non-"auto" setting wins; otherwise detect the language of
    ``fallback_text`` (defaulting to English when empty).
    """
    if configured and configured != "auto":
        return configured
    return detect_language(fallback_text or "English")


def build_language_directive(configured: str | None, fallback_text: str = "") -> str:
    """Port of buildLanguageDirective (output-language.ts:22-35).

    Byte-for-byte match with the TS directive text (a system-prompt
    injection, so exact wording matters).
    """
    lang = get_output_language(configured, fallback_text)
    prompt_lang = get_language_prompt_name(lang)
    return "\n".join([
        f"## ⚠️ MANDATORY OUTPUT LANGUAGE: {prompt_lang}",
        "",
        f"Write surrounding natural-language prose in **{prompt_lang}**.",
        f"All generated prose, including prose titles and section headings, must be in {prompt_lang}.",
        "Do not translate, transliterate, or describe proper nouns and technical identifiers unless the source already uses a well-established localized form.",
        "Preserve organization names, product names, model names, dataset names, tool/library names, acronyms, code identifiers, file names, URLs, paper titles, citation strings, and technical terms that have no widely-used localized equivalent in their standard original form.",
        f"The source material or wiki content may be in a different language; use it as evidence, but keep generated prose in {prompt_lang}.",
        "This language rule overrides weaker style instructions, but it does not override the proper-noun and technical-identifier preservation rule above.",
    ])


def build_language_reminder(configured: str | None, fallback_text: str = "") -> str:
    """Port of buildLanguageReminder (output-language.ts:40-43).

    One-line reminder for placing right before the user's current message.
    """
    lang = get_output_language(configured, fallback_text)
    return (
        f"REMINDER: Write prose in {get_language_prompt_name(lang)}; "
        "preserve names, acronyms, identifiers, URLs, file names, and paper titles in their standard original form."
    )
