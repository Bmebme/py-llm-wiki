"""Language rendering / prompt metadata.

Port of llm_wiki's ``src/lib/language-metadata.ts``.
"""

from __future__ import annotations

LANGUAGE_METADATA: dict[str, dict] = {
    "English": {
        "promptName": "English",
        "htmlLang": "en",
        "direction": "ltr",
        "scriptFamily": "latin",
    },
    "Arabic": {
        "promptName": "Arabic / العربية",
        "htmlLang": "ar",
        "direction": "rtl",
        "scriptFamily": "arabic",
    },
    "Persian": {
        "promptName": "Persian (Farsi / فارسی)",
        "htmlLang": "fa",
        "direction": "rtl",
        "scriptFamily": "arabic",
    },
    "Hebrew": {
        "promptName": "Hebrew / עברית",
        "htmlLang": "he",
        "direction": "rtl",
        "scriptFamily": "other",
    },
    "Chinese": {
        "promptName": "Chinese",
        "htmlLang": "zh-Hans",
        "direction": "ltr",
        "scriptFamily": "cjk",
    },
    "Traditional Chinese": {
        "promptName": "Traditional Chinese",
        "htmlLang": "zh-Hant",
        "direction": "ltr",
        "scriptFamily": "cjk",
    },
    "Japanese": {
        "promptName": "Japanese",
        "htmlLang": "ja",
        "direction": "ltr",
        "scriptFamily": "cjk",
    },
    "Korean": {
        "promptName": "Korean",
        "htmlLang": "ko",
        "direction": "ltr",
        "scriptFamily": "cjk",
    },
    "Czech": {
        "promptName": "Czech / čeština",
        "htmlLang": "cs",
        "direction": "ltr",
        "scriptFamily": "latin",
    },
}

DEFAULT_METADATA = {
    "promptName": "English",
    "direction": "ltr",
    "scriptFamily": "latin",
}


def get_language_metadata(language: str) -> dict:
    """Port of getLanguageMetadata (language-metadata.ts:73-78)."""
    if language in LANGUAGE_METADATA:
        return LANGUAGE_METADATA[language]
    return {**DEFAULT_METADATA, "promptName": language or DEFAULT_METADATA["promptName"]}


def get_language_prompt_name(language: str) -> str:
    return get_language_metadata(language)["promptName"]


def get_text_direction(language: str) -> str:
    return get_language_metadata(language)["direction"]


def get_html_lang(language: str) -> str | None:
    return get_language_metadata(language).get("htmlLang")


def same_script_family(a: str, b: str) -> bool:
    return get_language_metadata(a)["scriptFamily"] == get_language_metadata(b)["scriptFamily"]
