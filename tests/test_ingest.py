from pathlib import Path

from what_town_two_words.ingest import extract_os_open_names_towns, load_scowl_words


def test_extract_os_open_names_towns(tmp_path: Path):
    csv_path = tmp_path / "names.csv"
    csv_path.write_text(
        "ID,NAME1,LOCAL_TYPE\n"
        "1,York,City\n"
        "2,Little Snoring,Village\n"
        "3,A1 Road,Named Road\n",
        encoding="utf-8",
    )

    towns, stats = extract_os_open_names_towns([str(csv_path)], include_multiword=True)

    assert towns == ["little-snoring", "york"]
    assert stats.kept == 2


def test_load_scowl_words(tmp_path: Path):
    path = tmp_path / "words.txt"
    path.write_text("penguin\nmeeting/M\ncan't\nklutz\nox\n", encoding="utf-8")

    words, stats = load_scowl_words([str(path)], min_len=3, max_len=8)

    assert words == ["klutz", "meeting", "penguin"]
    assert stats.kept ==