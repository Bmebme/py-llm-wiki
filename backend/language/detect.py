"""Primary-language detection by Unicode script ranges.

Port of llm_wiki's ``src/lib/detect-language.ts``, v0 simplified:

- Faithful: the script-range counting (CJK, Hiragana/Katakana, Hangul,
  Arabic/Persian, Hebrew, Cyrillic, Greek, Thai, Hindi, and the rest of
  the TS script table), the "Japanese + Kanji wins over Chinese" rule,
  the 2-char commitment threshold, and the Arabic-vs-Persian scorer.
- Simplified: the deep per-language Latin diacritic logic
  (``detectLatinLanguage`` in the TS) is NOT ported — any text without a
  recognized non-Latin script returns "English". TS cases that relied on
  diacritics (Vietnamese, Czech, French, German, Spanish, Polish,
  Portuguese, ...) therefore resolve to "English" here; they are the
  "skipped" Latin-language cases.
"""

from __future__ import annotations

import unicodedata


def get_script(cp: int) -> str | None:
    """Port of getScript (detect-language.ts:122-226)."""
    # CJK Unified Ideographs (Chinese/Japanese Kanji)
    if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or
            0x20000 <= cp <= 0x2A6DF or 0xF900 <= cp <= 0xFAFF):
        return "Chinese"
    # Japanese Hiragana + Katakana
    if (0x3040 <= cp <= 0x309F or 0x30A0 <= cp <= 0x30FF or
            0x31F0 <= cp <= 0x31FF or 0xFF65 <= cp <= 0xFF9F):
        return "Japanese"
    # Korean Hangul
    if (0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF or
            0x3130 <= cp <= 0x318F):
        return "Korean"
    # Arabic
    if (0x0600 <= cp <= 0x06FF or 0x0750 <= cp <= 0x077F or
            0x08A0 <= cp <= 0x08FF or 0xFB50 <= cp <= 0xFDFF or
            0xFE70 <= cp <= 0xFEFF):
        return "Arabic"
    # Hebrew
    if (0x0590 <= cp <= 0x05FF or 0xFB1D <= cp <= 0xFB4F):
        return "Hebrew"
    # Thai
    if 0x0E00 <= cp <= 0x0E7F:
        return "Thai"
    # Devanagari (Hindi, Sanskrit, Marathi, Nepali)
    if 0x0900 <= cp <= 0x097F:
        return "Hindi"
    # Bengali
    if 0x0980 <= cp <= 0x09FF:
        return "Bengali"
    # Tamil
    if 0x0B80 <= cp <= 0x0BFF:
        return "Tamil"
    # Telugu
    if 0x0C00 <= cp <= 0x0C7F:
        return "Telugu"
    # Kannada
    if 0x0C80 <= cp <= 0x0CFF:
        return "Kannada"
    # Malayalam
    if 0x0D00 <= cp <= 0x0D7F:
        return "Malayalam"
    # Gujarati
    if 0x0A80 <= cp <= 0x0AFF:
        return "Gujarati"
    # Gurmukhi (Punjabi)
    if 0x0A00 <= cp <= 0x0A7F:
        return "Punjabi"
    # Myanmar (Burmese)
    if 0x1000 <= cp <= 0x109F:
        return "Burmese"
    # Khmer (Cambodian)
    if 0x1780 <= cp <= 0x17FF:
        return "Khmer"
    # Lao
    if 0x0E80 <= cp <= 0x0EFF:
        return "Lao"
    # Georgian
    if 0x10A0 <= cp <= 0x10FF or 0x2D00 <= cp <= 0x2D2F:
        return "Georgian"
    # Armenian
    if 0x0530 <= cp <= 0x058F:
        return "Armenian"
    # Ethiopic (Amharic)
    if 0x1200 <= cp <= 0x137F:
        return "Amharic"
    # Tibetan
    if 0x0F00 <= cp <= 0x0FFF:
        return "Tibetan"
    # Sinhala
    if 0x0D80 <= cp <= 0x0DFF:
        return "Sinhala"
    # Cyrillic (Russian, Ukrainian, Bulgarian, etc.) — default Cyrillic
    # to Russian, matching the TS.
    if 0x0400 <= cp <= 0x04FF or 0x0500 <= cp <= 0x052F:
        return "Russian"
    # Greek
    if 0x0370 <= cp <= 0x03FF or 0x1F00 <= cp <= 0x1FFF:
        return "Greek"

    return None


_PERSIAN_CHARS = {"پ", "چ", "ژ", "گ"}  # پ چ ژ گ
_PERSIAN_CHARS_LIGHT = {"ک", "ی"}  # ک ی
_ARABIC_CHARS = {"ك", "ي", "ة", "ى", "إ", "أ", "ؤ", "ئ"}  # ك ي ة ى إ أ ؤ ئ

_PERSIAN_WORDS = [
    "این", "است", "که", "برای", "های", "را", "در", "به", "از", "می", "یک",
]
_ARABIC_WORDS = [
    "ال", "في", "من", "على", "هذا", "هذه", "إلى", "التي", "الذي", "كان",
]


def _normalize_letters(text: str) -> str:
    """JS ``text.replace(/[^\\p{L}\\p{N}]+/gu, " ")`` wrapped in spaces."""
    out = [" "]
    for ch in text:
        cat = unicodedata.category(ch)
        if cat.startswith("L") or cat.startswith("N"):
            out.append(ch)
        else:
            out.append(" ")
    out.append(" ")
    return "".join(out)


def detect_arabic_script_language(text: str) -> str:
    """Port of detectArabicScriptLanguage (detect-language.ts:53-120).

    Conservative: falls back to "Arabic" unless the Persian signal is
    clearly stronger (persianScore >= 3 and > arabicScore).
    """
    persian_score = 0
    arabic_score = 0

    for ch in text:
        if ch in _PERSIAN_CHARS:
            persian_score += 3
        elif ch in _PERSIAN_CHARS_LIGHT:
            persian_score += 1
        elif ch in _ARABIC_CHARS:
            arabic_score += 1

    normalized = _normalize_letters(text)
    for word in _PERSIAN_WORDS:
        if f" {word} " in normalized:
            persian_score += 2
    for word in _ARABIC_WORDS:
        if f" {word} " in normalized:
            arabic_score += 2

    if persian_score >= 3 and persian_score > arabic_score:
        return "Persian"
    return "Arabic"


def detect_language(text: str) -> str:
    """Port of detectLanguage (detect-language.ts:5-51), v0 simplified.

    Latin-script input (including Latin text with diacritics) returns
    "English" — the TS per-language Latin detection is not ported.
    """
    counts: dict[str, int] = {}

    for ch in text:
        cp = ord(ch)
        if cp < 0x80:
            continue  # skip ASCII initially
        script = get_script(cp)
        if script:
            counts[script] = counts.get(script, 0) + 1

    # Japanese uses BOTH Hiragana/Katakana and Kanji; pure Chinese uses
    # ONLY Kanji. Any Japanese script chars alongside Kanji => Japanese.
    if counts.get("Japanese", 0) > 0 and counts.get("Chinese", 0) > 0:
        return "Japanese"

    max_script = ""
    max_count = 0
    for script, count in counts.items():
        if count > max_count:
            max_script = script
            max_count = count

    if max_script == "Arabic" and max_count >= 2:
        return detect_arabic_script_language(text)

    if max_script and max_count >= 2:
        return max_script

    # Latin-script languages: simplified — no diacritic logic, so Latin
    # text (and ASCII-only text) resolves to English.
    return "English"
