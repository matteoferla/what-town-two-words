from __future__ import annotations

import math
import random
from dataclasses import dataclass
from importlib import resources
from typing import Iterable

from .filters import normalize_word

EARTH_M_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class Cell:
    south: float
    west: float
    north: float
    east: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.south + self.north) / 2, (self.west + self.east) / 2)


@dataclass(frozen=True)
class LocalWhat2Words:
    towns: tuple[str, ...]
    words: tuple[str, ...]
    bbox: tuple[float, float, float, float] = (49.8, -8.7, 60.9, 1.9)

    def __init__(
        self,
        towns: Iterable[str],
        words: Iterable[str],
        bbox: tuple[float, float, float, float] = (49.8, -8.7, 60.9, 1.9),
    ) -> None:
        clean_towns = tuple(dict.fromkeys(t.strip().lower() for t in towns if t.strip()))
        clean_words = tuple(dict.fromkeys(normalize_word(word) for word in words if word.strip()))
        if not clean_towns:
            raise ValueError("at least one town is required")
        if len(clean_words) < 2:
            raise ValueError("at least two words are required")
        object.__setattr__(self, "towns", clean_towns)
        object.__setattr__(self, "words", clean_words)
        object.__setattr__(self, "bbox", bbox)

    @classmethod
    def from_builtin(cls) -> "LocalWhat2Words":
        return cls(
            towns=_load_builtin_lines("british_towns.txt"),
            words=_load_builtin_lines("words.txt"),
        )

    @property
    def capacity(self) -> int:
        return len(self.towns) * len(self.words) * len(self.words)

    def encode_int(self, value: int) -> str:
        if value < 0 or value >= self.capacity:
            raise ValueError(f"value must be in [0, {self.capacity})")
        town_index, remainder = divmod(value, len(self.words) * len(self.words))
        first_index, second_index = divmod(remainder, len(self.words))
        return ".".join(
            (
                self.towns[town_index],
                self.words[first_index],
                self.words[second_index],
            )
        )

    def decode_code(self, code: str) -> int:
        parts = code.strip().lower().split(".")
        if len(parts) != 3:
            raise ValueError("code must look like town.word.word")
        town, first, second = parts
        try:
            town_index = self.towns.index(town)
            first_index = self.words.index(first)
            second_index = self.words.index(second)
        except ValueError as exc:
            raise ValueError(f"unknown code part in {code!r}") from exc
        return (
            town_index * len(self.words) * len(self.words)
            + first_index * len(self.words)
            + second_index
        )

    def sample_code(
        self,
        word_scores: dict[str, float] | None = None,
        fun_bias: float = 0.35,
        rng: random.Random | None = None,
    ) -> str:
        """Sample a town.word.word code, with only words mildly fun-biased."""
        generator = rng or random
        town = generator.choice(self.towns)
        weights = self._word_weights(word_scores or {}, fun_bias)
        first = generator.choices(self.words, weights=weights, k=1)[0]
        second = generator.choices(self.words, weights=weights, k=1)[0]
        return ".".join((town, first, second))

    def encode_latlon(self, lat: float, lon: float, resolution_m: float = 100.0) -> str:
        index, _rows, _cols = self._latlon_to_index(lat, lon, resolution_m)
        if index >= self.capacity:
            raise ValueError(
                "lexicon capacity is too small for this bbox/resolution; "
                f"need >{index}, have {self.capacity}"
            )
        return self.encode_int(index)

    def decode_latlon_code(self, code: str, resolution_m: float = 100.0) -> Cell:
        value = self.decode_code(code)
        south, west, north, east = self.bbox
        rows, cols = self._grid_shape(resolution_m)
        if value >= rows * cols:
            raise ValueError("code is outside the coordinate grid")
        row, col = divmod(value, cols)
        lat_step = (north - south) / rows
        lon_step = (east - west) / cols
        return Cell(
            south=south + row * lat_step,
            west=west + col * lon_step,
            north=south + (row + 1) * lat_step,
            east=west + (col + 1) * lon_step,
        )

    def _latlon_to_index(
        self, lat: float, lon: float, resolution_m: float
    ) -> tuple[int, int, int]:
        south, west, north, east = self.bbox
        if not (south <= lat <= north and west <= lon <= east):
            raise ValueError("coordinate is outside bbox")
        rows, cols = self._grid_shape(resolution_m)
        row = min(rows - 1, max(0, int((lat - south) / (north - south) * rows)))
        col = min(cols - 1, max(0, int((lon - west) / (east - west) * cols)))
        return row * cols + col, rows, cols

    def _grid_shape(self, resolution_m: float) -> tuple[int, int]:
        if resolution_m <= 0:
            raise ValueError("resolution_m must be positive")
        south, west, north, east = self.bbox
        mid_lat = (south + north) / 2
        lat_m = (north - south) * EARTH_M_PER_DEG_LAT
        lon_m = (east - west) * EARTH_M_PER_DEG_LAT * math.cos(math.radians(mid_lat))
        rows = max(1, math.ceil(lat_m / resolution_m))
        cols = max(1, math.ceil(lon_m / resolution_m))
        return rows, cols

    def _word_weights(self, word_scores: dict[str, float], fun_bias: float) -> list[float]:
        if fun_bias < 0:
            raise ValueError("fun_bias must be >= 0")
        return [
            1.0 + fun_bias * min(1.0, max(0.0, float(word_scores.get(word, 0.0))))
            for word in self.words
        ]


def _load_builtin_lines(name: str) -> list[str]:
    with resources.files("what_town_two_words.data").joinpath(name).open(
        "r", encoding="utf-8"
    ) as handle:
        return [
            line.strip().lower()
            for line in handle
            if line.strip() and not line.lstrip().startswith("#")
       