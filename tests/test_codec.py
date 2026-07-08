from pathlib import Path

from what_town_two_words import LexiconBuilder, LocalWhat2Words, levenshtein
from what_town_two_words.filters import rough_metaphone
from what_town_two_words.lexicon import load_scored_word_list, write_scored_word_list


def test_encode_int_round_trip():
    coder = LocalWhat2Words(towns=["York", "Bath"], words=["penguin", "klutz", "rocket"])

    code = coder.encode_int(5)

    assert code == "york.klutz.rocket"
    assert coder.decode_code(code) == 5


def test_latlon_round_trip_to_cell():
    coder = LocalWhat2Words.from_builtin()

    code = coder.encode_latlon(51.5074, -0.1278, resolution_m=20_000)
    cell = coder.decode_latlon_code(code, resolution_m=20_000)

    assert cell.south <= 51.5074 <= cell.north
    assert cell.west <= -0.1278 <= cell.east


def test_lexicon_filters_close_plural_and_profanity():
    lexicon = LexiconBuilder(min_levenshtein=3).build(
        ["penguin", "penguins", "pengun", "rocket", "shit"]
    )

    assert "penguin" in lexicon.words
    assert "rocket" in lexicon.words
    assert "penguins" in lexicon.rejected
    assert "pengun" in lexicon.rejected
    assert lexicon.rejected["shit"] == "profanity"


def test_levenshtein():
    assert levenshtein("penguin", "penguins") == 1


def test_sample_code_uses_word_scores_as_mild_bias():
    import random

    coder = LocalWhat2Words(towns=["York"], words=["meeting", "penguin", "klutz"])
    rng = random.Random(1)

    samples = [
        coder.sample_code(
            word_scores={"meeting": 0.0, "penguin": 0.5, "klutz": 1.0},
            fun_bias=1.0,
            rng=rng,
        )
        for _ in range(20)
    ]

    assert all(sample.startswith("york.") for sample in samples)
    assert any("klutz" in sample for sample in samples)


def test_ranked_collision_keeps_more_common_word():
    lexicon = LexiconBuilder(
        min_levenshtein=3,
        reject_homophones=False,
        reject_same_metaphone=False,
        reject_plural_pairs=False,
        ranks={"colour": 35, "color": 50},
    ).build(["color", "colour"])

    assert lexicon.words == ("colour",)
    assert lexicon.rejected["color"] == "levenshtein<3:colour"


def test_preference_breaks_rank_ties_only():
    lexicon = LexiconBuilder(
        min_levenshtein=3,
        reject_homophones=False,
        reject_same_metaphone=False,
        reject_plural_pairs=False,
        ranks={"pickle": 35, "fickle": 35, "tickle": 50},
        preferences={"pickle": 0.9, "fickle": 0.1, "tickle": 1.0},
    ).build(["fickle", "pickle", "tickle"])

    assert lexicon.words == ("pickle",)
    assert lexicon.rejected["fickle"] == "levenshtein<3:pickle"
    assert lexicon.rejected["tickle"] == "levenshtein<3:pickle"


def test_rough_metaphone_catches_night_nite():
    assert rough_metaphone("night") == rough_metaphone("nite")


def test_scored_word_list_round_trip(tmp_path: Path):
    path = tmp_path / "filtered_words.txt"

    write_scored_word_list(
        str(path),
        ["meeting", "penguin", "klutz"],
        {"meeting": 0.1, "penguin": 0.6, "klutz": 0.9},
    )
    words, scores = load_scored_word_list(str(path))

    assert words == ["meeting", "penguin", "klutz"]
    assert scores["meeting"] == 0.1
    assert scores["penguin"] == 0.6
    assert scores["klutz"] == 0.9
