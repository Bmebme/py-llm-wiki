"""FNV-1a golden vectors generated from llm_wiki's JS implementation
(src/stores/review-store.ts reviewIdFor) — the Python port must produce
identical ids, including for astral characters (surrogate pairs)."""

from backend.core.hashing import fnv1a_32, fnv1a_32_hex

# key -> hex32, produced by running the exact JS code under node:
#   h = 0x811c9dc5; h ^= key.charCodeAt(i); h = Math.imul(h, 0x01000193)
GOLDEN = [
    ("missing-page::attention is all you need", "72945afd"),
    ("missing-page::chain-of-thought", "1475d96c"),
    ("contradiction::vit vs cnn for segmentation", "11f690f8"),
    ("duplicate::知识图谱", "955a9fc4"),
    ("confirm::scaling laws", "1b6b7e69"),
    ("suggestion::emoji 🚀 test", "daf2dd7e"),  # astral char → surrogate pair
]


def test_fnv1a_32_golden_vectors():
    for key, expected in GOLDEN:
        assert fnv1a_32_hex(key) == expected, key


def test_fnv1a_32_raw_value():
    assert fnv1a_32("missing-page::attention is all you need") == 0x72945AFD


def test_empty_string_offset():
    assert fnv1a_32("") == 0x811C9DC5
