from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources

WORD_RE = re.compile(r"^[a-z][a-z-]{1,31}$")


def normalize_word(value: str) -> str:
    """Return a lowercase, hyphen-preserving token suitable for the lexicon."""
    return value.strip().lower().replace("'", "").replace("_", "-")


def load_builtin_word_set(name: str) -> set[str]:
    with resources.files("what_town_two_words.data").joinpath(name).open(
        "r", encoding="utf-8"
    ) as handle:
        return {
            normalize_word(line)
            for line in handle
            if line.strip() and not line.lstrip().startswith("#")
        }


def is_candidate_word(value: str) -> bool:
    return bool(WORD_RE.match(value))


@lru_cache(maxsize=65536)
def levenshtein(left: str, right: str) -> int:
    """Small dependency-free Levenshtein distance."""
    if left == right:
        return 0
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for i, lc in enumerate(left, start=1):
        current = [i]
        for j, rc in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (lc != rc),
                )
            )
        previous = current
    return previous[-1]


def singular_key(word: str) -> str:
    """A conservative singular key for rejecting obvious plural pairs."""
    if len(word) > 4 and word.endswith("ies"):
        return f"{word[:-3]}y"
    if len(word) > 4 and word.endswith("ves"):
        return f"{word[:-3]}f"
    if len(word) > 3 and word.endswith("es"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s"):
        return word[:-1]
    return word


@lru_cache(maxsize=65536)
def pronunciation_keys(word: str) -> tuple[str, ...]:
    """CMUdict pronunciation keys, when the optional pronouncing package exists."""
    try:
        import pronouncing  # type: ignore
    except Exception:
        return ()
    phones = pronouncing.phones_for_word(word.replace("-", " "))
    return tuple(sorted(set(phones)))


@lru_cache(maxsize=65536)
def metaphone_key(word: str) -> str:
    """Return a metaphone key, preferring the optional Metaphone package."""
    normalized = word.replace("-", "")
    try:
        from metaphone import doublemetaphone  # type: ignore
    except Exception:
        return rough_metaphone(normalized)
    primary, secondary = doublemetaphone(normalized)
    return primary or secondary or rough_metaphone(normalized)


def rough_metaphone(word: str) -> str:
    """Tiny fallback for phonetic collision checks when Metaphone is unavailable."""
    if not word:
        return ""
    word = _rough_phonetic_normalize(word)
    chars = [word[0]]
    previous = word[0]
    for char in word[1:]:
        if char in "aeiouyhw":
            continue
        if char != previous:
            chars.append(char)
        previous = char
    return "".join(chars)


def _rough_phonetic_normalize(word: str) -> str:
    replacements = (
        ("ght", "t"),
        ("gh", ""),
        ("ph", "f"),
        ("ck", "k"),
        ("qu", "kw"),
        ("x", "ks"),
    )
    for old, new in replacements:
        word = word.replace(old, new)
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return wo