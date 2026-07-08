from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .filters import (
    is_candidate_word,
    levenshtein,
    load_builtin_word_set,
    metaphone_key,
    normalize_word,
    pronunciation_keys,
    singular_key,
)


@dataclass(frozen=True)
class Lexicon:
    words: tuple[str, ...]
    scores: dict[str, float] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)


@dataclass
class LexiconBuilder:
    min_levenshtein: int = 3
    reject_homophones: bool = True
    reject_same_metaphone: bool = True
    reject_plural_pairs: bool = True
    require_cmudict: bool = False
    profanity: set[str] | None = None
    ranks: dict[str, int] | None = None
    preferences: dict[str, float] | None = None

    def build(self, candidates: Iterable[str]) -> Lexicon:
        profanity = self.profanity or load_builtin_word_set("profanity.txt")
        ordered = self._ordered_candidates(candidates)

        accepted: list[str] = []
        distance_index = _DeleteIndex(max_distance=self.min_levenshtein - 1)
        rejected: dict[str, str] = {}
        metaphones: dict[str, str] = {}
        pronunciations: dict[str, str] = {}
        singulars: dict[str, str] = {}

        if self.require_cmudict and self.reject_homophones:
            try:
                import pronouncing  # noqa: F401
            except Exception as exc:
                raise RuntimeError(
                    "CMUdict homophone filtering requires the optional "
                    "'pronouncing' package. Install with .[phonetics]."
                ) from exc

        for raw in ordered:
            word = normalize_word(raw)
            reason = self._basic_rejection(word, profanity)
            if reason:
                rejected[word] = reason
                continue

            close_to = self._too_close(word, distance_index)
            if close_to:
                rejected[word] = f"levenshtein<{self.min_levenshtein}:{close_to}"
                continue

            if self.reject_plural_pairs:
                singular = singular_key(word)
                if singular in singulars:
                    rejected[word] = f"plural-pair:{singulars[singular]}"
                    continue

            if self.reject_same_metaphone:
                key = metaphone_key(word)
                if key and key in metaphones:
                    rejected[word] = f"metaphone:{metaphones[key]}"
                    continue

            if self.reject_homophones:
                conflict = self._homophone_conflict(word, pronunciations)
                if conflict:
                    rejected[word] = f"homophone:{conflict}"
                    continue

            accepted.append(word)
            distance_index.add(word)
            if self.reject_plural_pairs:
                singulars[singular_key(word)] = word
            if self.reject_same_metaphone:
                metaphones[metaphone_key(word)] = word
            if self.reject_homophones:
                for key in pronunciation_keys(word):
                    pronunciations[key] = word

        return Lexicon(words=tuple(sorted(accepted)), rejected=rejected)

    def _ordered_candidates(self, candidates: Iterable[str]) -> list[str]:
        unique = {normalize_word(word) for word in candidates if word.strip()}
        ranks = self.ranks or {}
        preferences = self.preferences or {}
        unknown_rank = max(ranks.values(), default=100) + 100
        return sorted(
            unique,
            key=lambda word: (
                ranks.get(word, unknown_rank),
                -_clamped_preference(preferences.get(word, 0.0)),
                len(word),
                word,
            ),
        )

    def _basic_rejection(self, word: str, profanity: set[str]) -> str | None:
        if not is_candidate_word(word):
            return "shape"
        if word in profanity:
            return "profanity"
        return None

    def _too_close(self, word: str, distance_index: "_DeleteIndex") -> str | None:
        return distance_index.find_within(word)

    def _homophone_conflict(self, word: str, pronunciations: dict[str, str]) -> str | None:
        for key in pronunciation_keys(word):
            if key in pronunciations:
                return pronunciations[key]
        return None


def load_word_list(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as handle:
        return [
            normalize_word(line.split()[0])
            for line in handle
            if line.strip() and not line.lstrip().startswith("#")
        ]


def load_scored_word_list(path: str) -> tuple[list[str], dict[str, float]]:
    words: list[str] = []
    scores: dict[str, float] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split()
            word = normalize_word(parts[0])
            words.append(word)
            if len(parts) > 1:
                try:
                    scores[word] = min(1.0, max(0.0, float(parts[1])))
                except ValueError:
                    scores[word] = 0.0
    return words, scores


def write_scored_word_list(
    path: str,
    words: Iterable[str],
    scores: dict[str, float] | None = None,
) -> None:
    score_map = scores or {}
    with open(path, "w", encoding="utf-8") as handle:
        for raw in words:
            word = normalize_word(raw)
            if word in score_map:
                handle.write(f"{word}\t{float(score_map[word]):.4f}\n")
            else:
                handle.write(f"{word}\n")


def write_rejections(path: str, rejected: dict[str, str]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for word, reason in sorted(rejected.items()):
            handle.write(f"{word}\t{reason}\n")


def find_rank_tie_collision_words(
    candidates: Iterable[str],
    ranks: dict[str, int],
    min_levenshtein: int = 3,
) -> tuple[str, ...]:
    """Return words that collide with another word at the same frequency rank."""
    unique = sorted({normalize_word(word) for word in candidates if word.strip()})
    by_rank: dict[int, list[str]] = {}
    unknown_rank = max(ranks.values(), default=100) + 100
    for word in unique:
        by_rank.setdefault(ranks.get(word, unknown_rank), []).append(word)

    tied: set[str] = set()
    for words in by_rank.values():
        tied.update(_rank_ties_by_distance(words, min_levenshtein))
        tied.update(_rank_ties_by_key(words, singular_key))
        tied.update(_rank_ties_by_key(words, metaphone_key))
        tied.update(_rank_ties_by_pronunciation(words))
    return tuple(sorted(tied))


def _rank_ties_by_distance(words: list[str], min_levenshtein: int) -> set[str]:
    tied: set[str] = set()
    distance_index = _DeleteIndex(max_distance=min_levenshtein - 1)
    for word in words:
        close_to = distance_index.find_within(word)
        if close_to:
            tied.add(word)
            tied.add(close_to)
        distance_index.add(word)
    return tied


def _rank_ties_by_key(words: list[str], key_fn) -> set[str]:
    tied: set[str] = set()
    seen: dict[str, str] = {}
    for word in words:
        key = key_fn(word)
        if not key:
            continue
        other = seen.get(key)
        if other:
            tied.add(word)
            tied.add(other)
        else:
            seen[key] = word
    return tied


def _rank_ties_by_pronunciation(words: list[str]) -> set[str]:
    tied: set[str] = set()
    seen: dict[str, str] = {}
    for word in words:
        for key in pronunciation_keys(word):
            other = seen.get(key)
            if other:
                tied.add(word)
                tied.add(other)
            else:
                seen[key] = word
    return tied


class _DeleteIndex:
    def __init__(self, max_distance: int) -> None:
        self.max_distance = max_distance
        self.index: dict[str, list[str]] = {}

    def add(self, word: str) -> None:
        for key in _delete_keys(word, self.max_distance):
            self.index.setdefault(key, []).append(word)

    def find_within(self, word: str) -> str | None:
        if self.max_distance < 0:
            return None
        seen: set[str] = set()
        for key in _delete_keys(word, self.max_distance):
            for other in self.index.get(key, ()):
                if other in seen:
                    continue
                seen.add(other)
                if levenshtein(word, other) <= self.max_distance:
                    return other
        return None


def _delete_keys(word: str, max_deletions: int) -> set[str]:
    keys = {word}
    frontier = {word}
    for _ in range(max_deletions):
        next_frontier: set[str] = set()
        for value in frontier:
            if not value:
                continue
            for index in range(len(value)):
                deleted = value[:index] + value[index + 1 :]
                if deleted not in keys:
                    keys.add(deleted)
                    next_frontier.add(deleted)
        frontier = next_frontier
    return keys


def _clamped_preference(value: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
