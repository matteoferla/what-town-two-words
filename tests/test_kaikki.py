import gzip
import json
from pathlib import Path

from what_town_two_words.ingest import extract_kaikki_word_metadata


def test_extract_kaikki_word_metadata_policy(tmp_path: Path):
    path = tmp_path / "kaikki.jsonl.gz"
    entries = [
        {"lang": "English", "word": "glass", "pos": "noun", "senses": [{}]},
        {"lang": "English", "word": "kingly", "pos": "adj", "senses": [{}]},
        {
            "lang": "English",
            "word": "pailfuls",
            "pos": "noun",
            "senses": [{"tags": ["plural"], "form_of": [{"word": "pailful"}]}],
        },
        {
            "lang": "English",
            "word": "dancing",
            "pos": "verb",
            "senses": [{"tags": ["present", "participle"]}],
        },
        {"lang": "English", "word": "dance", "pos": "verb", "senses": [{}]},
        {"lang": "English", "word": "dance", "pos": "noun", "senses": [{}]},
        {"lang": "English", "word": "playfully", "pos": "adv", "senses": [{}]},
        {"lang": "English", "word": "abandons", "pos": "verb", "senses": [{"tags": ["third-person"]}]},
    ]
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")

    accepted, rejected, stats = extract_kaikki_word_metadata(
        str(path),
        ["glass", "kingly", "pailfuls", "dancing", "dance", "playfully", "abandons"],
    )

    assert accepted == {
        "dance": "noun",
        "dancing": "gerund",
        "glass": "noun",
        "kingly": "adjective",
    }
    assert rejected["pailfuls"] == "plural"
    assert rejected["playfully"] == "adverb"
    assert rejected["abandons"] == "third-person"
    assert stats.kept == 4
