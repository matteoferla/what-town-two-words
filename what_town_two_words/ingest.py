from __future__ import annotations

import csv
import gzip
import json
import sqlite3
import zipfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .filters import is_candidate_word, normalize_word

OS_NAME_COLUMNS = ("NAME1", "name1", "Name1", "NAME", "name", "Name")
OS_TYPE_COLUMNS = ("LOCAL_TYPE", "local_type", "Local_Type", "type", "Type")
TOWN_TYPES = {
    "city",
    "town",
    "village",
    "hamlet",
    "suburban area",
    "other settlement",
}
OS_OPEN_NAMES_HEADER = (
    "ID",
    "NAMES_URI",
    "NAME1",
    "NAME1_LANG",
    "NAME2",
    "NAME2_LANG",
    "TYPE",
    "LOCAL_TYPE",
    "GEOMETRY_X",
    "GEOMETRY_Y",
    "MOST_DETAIL_VIEW_RES",
    "LEAST_DETAIL_VIEW_RES",
    "MBR_XMIN",
    "MBR_YMIN",
    "MBR_XMAX",
    "MBR_YMAX",
    "POSTCODE_DISTRICT",
    "POSTCODE_DISTRICT_URI",
    "POPULATED_PLACE",
    "POPULATED_PLACE_URI",
    "POPULATED_PLACE_TYPE",
    "DISTRICT_BOROUGH",
    "DISTRICT_BOROUGH_URI",
    "DISTRICT_BOROUGH_TYPE",
    "COUNTY_UNITARY",
    "COUNTY_UNITARY_URI",
    "COUNTY_UNITARY_TYPE",
    "REGION",
    "REGION_URI",
    "COUNTRY",
    "COUNTRY_URI",
    "RELATED_SPATIAL_OBJECT",
    "SAME_AS_DBPEDIA",
    "SAME_AS_GEONAMES",
)


@dataclass(frozen=True)
class ExtractionStats:
    read: int
    kept: int
    rejected: dict[str, int]


def extract_os_open_names_towns(
    paths: Iterable[str],
    allowed_types: set[str] | None = None,
    include_multiword: bool = False,
) -> tuple[list[str], ExtractionStats]:
    """Extract settlement names from OS Open Names CSV files."""
    allowed = {value.lower() for value in (allowed_types or TOWN_TYPES)}
    names: set[str] = set()
    rejected: Counter[str] = Counter()
    read = 0

    for path in paths:
        if path.lower().endswith(".zip"):
            path_read, path_names, path_rejected = _extract_os_zip(path, allowed, include_multiword)
            read += path_read
            names.update(path_names)
            rejected.update(path_rejected)
            continue
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                continue
            if not _looks_like_header(reader.fieldnames):
                handle.seek(0)
                reader = csv.DictReader(handle, fieldnames=OS_OPEN_NAMES_HEADER)
            name_column = _first_present(reader.fieldnames, OS_NAME_COLUMNS)
            type_column = _first_present(reader.fieldnames, OS_TYPE_COLUMNS)
            if not name_column:
                raise ValueError(f"could not find OS name column in {path}")

            for row in reader:
                read += 1
                if type_column:
                    local_type = (row.get(type_column) or "").strip().lower()
                    if local_type and local_type not in allowed:
                        rejected["type"] += 1
                        continue
                raw_name = row.get(name_column) or ""
                town = normalize_place_name(raw_name)
                if not town:
                    rejected["empty"] += 1
                    continue
                if not include_multiword and "-" in town:
                    rejected["multiword"] += 1
                    continue
                if not all(is_candidate_word(part) for part in town.split("-")):
                    rejected["shape"] += 1
                    continue
                names.add(town)

    return sorted(names), ExtractionStats(read=read, kept=len(names), rejected=dict(rejected))


def _extract_os_zip(
    path: str,
    allowed: set[str],
    include_multiword: bool,
) -> tuple[int, set[str], Counter[str]]:
    names: set[str] = set()
    rejected: Counter[str] = Counter()
    read = 0
    with zipfile.ZipFile(path) as archive:
        for member in archive.namelist():
            if not member.startswith("Data/") or not member.endswith(".csv"):
                continue
            with archive.open(member) as raw:
                text = (line.decode("utf-8-sig") for line in raw)
                reader = csv.DictReader(text, fieldnames=OS_OPEN_NAMES_HEADER)
                for row in reader:
                    read += 1
                    local_type = (row.get("LOCAL_TYPE") or "").strip().lower()
                    if local_type and local_type not in allowed:
                        rejected["type"] += 1
                        continue
                    town = normalize_place_name(row.get("NAME1") or "")
                    if not town:
                        rejected["empty"] += 1
                        continue
                    if not include_multiword and "-" in town:
                        rejected["multiword"] += 1
                        continue
                    if not all(is_candidate_word(part) for part in town.split("-")):
                        rejected["shape"] += 1
                        continue
                    names.add(town)
    return read, names, rejected


def load_scowl_words(
    paths: Iterable[str],
    min_len: int = 3,
    max_len: int = 12,
) -> tuple[list[str], ExtractionStats]:
    """Load words from SCOWL-style or plain one-word-per-line files."""
    words: set[str] = set()
    rejected: Counter[str] = Counter()
    read = 0
    for path in paths:
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                read += 1
                token = _strip_scowl_line(line)
                if not token:
                    rejected["empty"] += 1
                    continue
                if "'" in token or "_" in token:
                    rejected["shape"] += 1
                    continue
                if token != token.lower():
                    rejected["case"] += 1
                    continue
                word = normalize_word(token)
                if not is_candidate_word(word):
                    rejected["shape"] += 1
                    continue
                if not min_len <= len(word) <= max_len:
                    rejected["length"] += 1
                    continue
                words.add(word)
    return sorted(words), ExtractionStats(read=read, kept=len(words), rejected=dict(rejected))


def extract_kaikki_word_metadata(
    jsonl_path: str,
    candidates: Iterable[str],
) -> tuple[dict[str, str], dict[str, str], ExtractionStats]:
    """Classify candidate words using Kaikki/Wiktextract JSONL metadata.

    Returns (accepted, rejected, stats), where accepted/rejected map word to a
    short reason. The policy keeps noun/adjective lemmas and gerund/present
    participle forms; it rejects plurals, past forms, pure adverbs, and pure
    base verbs.
    """
    wanted = {normalize_word(word) for word in candidates}
    seen: dict[str, _KaikkiDecision] = {}
    read = 0
    rejected_rows: Counter[str] = Counter()
    with _open_text_maybe_gzip(jsonl_path) as handle:
        for line in handle:
            read += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                rejected_rows["json"] += 1
                continue
            if entry.get("lang") not in {None, "English"}:
                continue
            word = normalize_word(str(entry.get("word", "")))
            if word not in wanted:
                continue
            decision = _classify_kaikki_entry(entry)
            current = seen.get(word)
            seen[word] = decision if current is None else current.merge(decision)

    accepted: dict[str, str] = {}
    rejected: dict[str, str] = {}
    for word in sorted(wanted):
        decision = seen.get(word)
        if decision is None:
            rejected[word] = "missing"
        elif decision.accept_reason:
            accepted[word] = decision.accept_reason
        else:
            rejected[word] = decision.reject_reason or "unsupported-pos"

    stats = ExtractionStats(
        read=read,
        kept=len(accepted),
        rejected=dict(Counter(rejected.values()) + rejected_rows),
    )
    return accepted, rejected, stats


@dataclass(frozen=True)
class _KaikkiDecision:
    accept_reason: str | None = None
    reject_reason: str | None = None

    def merge(self, other: "_KaikkiDecision") -> "_KaikkiDecision":
        if self.accept_reason:
            return self
        if other.accept_reason:
            return other
        return self if self.reject_reason else other


def _classify_kaikki_entry(entry: dict) -> _KaikkiDecision:
    pos = str(entry.get("pos", "")).lower()
    tags = _entry_tags(entry)
    if "plural" in tags:
        return _KaikkiDecision(reject_reason="plural")
    if {"past", "participle"} <= tags or "past" in tags:
        return _KaikkiDecision(reject_reason="past-form")
    if "third-person" in tags:
        return _KaikkiDecision(reject_reason="third-person")
    if "comparative" in tags or "superlative" in tags:
        return _KaikkiDecision(reject_reason="comparative-superlative")

    if pos in {"noun", "name"}:
        return _KaikkiDecision(accept_reason="noun")
    if pos == "adj":
        return _KaikkiDecision(accept_reason="adjective")
    if pos == "verb" and ("gerund" in tags or {"present", "participle"} <= tags):
        return _KaikkiDecision(accept_reason="gerund")
    if pos == "adv":
        return _KaikkiDecision(reject_reason="adverb")
    if pos == "verb":
        return _KaikkiDecision(reject_reason="verb")
    return _KaikkiDecision(reject_reason=f"pos:{pos or 'unknown'}")


def _entry_tags(entry: dict) -> set[str]:
    tags = {str(tag).lower() for tag in entry.get("tags", ())}
    for sense in entry.get("senses", ()) or ():
        tags.update(str(tag).lower() for tag in sense.get("tags", ()) or ())
    return tags


def _open_text_maybe_gzip(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="ignore")
    return open(path, "r", encoding="utf-8", errors="ignore")


def extract_scowl_ranked_words(
    db_path: str,
    max_size: int = 60,
    spelling: str = "B",
    variant_level: int = 1,
    min_len: int = 3,
    max_len: int = 12,
) -> tuple[dict[str, int], ExtractionStats]:
    """Extract words with SCOWL size as a frequency rank; lower is more common."""
    spellings = ("_", spelling)
    regions = ("", "GB" if spelling in {"B", "Z"} else "")
    query = """
        select word, min(size) as rank
        from scowl_v0
        where size <= ?
          and variant_level <= ?
          and spelling in (?, ?)
          and region in (?, ?)
          and category = ''
        group by word
    """
    ranks: dict[str, int] = {}
    rejected: Counter[str] = Counter()
    read = 0
    with sqlite3.connect(db_path) as conn:
        for token, rank in conn.execute(
            query,
            (max_size, variant_level, spellings[0], spellings[1], regions[0], regions[1]),
        ):
            read += 1
            clean = _clean_candidate_token(token)
            if not clean:
                rejected["shape"] += 1
                continue
            if not min_len <= len(clean) <= max_len:
                rejected["length"] += 1
                continue
            ranks[clean] = min(int(rank), ranks.get(clean, int(rank)))
    return ranks, ExtractionStats(read=read, kept=len(ranks), rejected=dict(rejected))


def write_lines(path: str, values: Iterable[str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for value in values:
            handle.write(f"{value}\n")


def write_metadata(path: str, values: dict[str, str]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for word, reason in sorted(values.items()):
            handle.write(f"{word}\t{reason}\n")


def write_scores(path: str, scores: dict[str, float]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(dict(sorted(scores.items())), handle, indent=2, sort_keys=True)
        handle.write("\n")


def load_scores(path: str) -> dict[str, float]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    return {normalize_word(key): float(value) for key, value in data.items()}


def write_ranks(path: str, ranks: dict[str, int]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for word, rank in sorted(ranks.items(), key=lambda item: (item[1], item[0])):
            handle.write(f"{word}\t{rank}\n")


def load_ranks(path: str) -> dict[str, int]:
    ranks: dict[str, int] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            ranks[normalize_word(parts[0])] = int(float(parts[1]))
    return ranks


def normalize_place_name(value: str) -> str:
    text = normalize_word(value)
    text = text.replace("&", " and ")
    parts = [part for part in text.replace(".", " ").split() if part]
    return "-".join(parts)


def _first_present(fieldnames: list[str], candidates: tuple[str, ...]) -> str | None:
    by_lower = {name.lower(): name for name in fieldnames}
    for candidate in candidates:
        if candidate.lower() in by_lower:
            return by_lower[candidate.lower()]
    return None


def _looks_like_header(fieldnames: list[str]) -> bool:
    lowered = {name.lower().lstrip("\ufeff") for name in fieldnames}
    return "name1" in lowered or "local_type" in lowered


def _strip_scowl_line(line: str) -> str:
    text = line.strip()
    if not text or text.startswith("#"):
        return ""
    token = text.split()[0]
    return token.split("/")[0]


def _clean_candidate_token(token: str) -> str:
    if not token or "'" in token or "_" in token or token != token.lower():
        return ""
    word = normalize_word(token)
    if not is_candidate_word(word):
        return ""
    return word
