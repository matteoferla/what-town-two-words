from __future__ import annotations

import argparse
from pathlib import Path

from .codec import LocalWhat2Words
from .ingest import (
    extract_kaikki_word_metadata,
    extract_os_open_names_towns,
    extract_scowl_ranked_words,
    load_ranks,
    load_scores,
    load_scowl_words,
    write_lines,
    write_metadata,
    write_ranks,
    write_scores,
)
from .lexicon import LexiconBuilder, find_rank_tie_collision_words, load_word_list
from .lexicon import load_scored_word_list, write_rejections, write_scored_word_list
from .ollama import score_words_with_ollama


def main() -> None:
    parser = argparse.ArgumentParser(prog="what-town-two-words")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--words", required=True)
    build.add_argument("--out", required=True)
    build.add_argument("--min-levenshtein", type=int, default=3)
    build.add_argument("--scores")
    build.add_argument("--ranks")
    build.add_argument("--preferences")
    build.add_argument("--rejected-out")

    towns = sub.add_parser("extract-towns")
    towns.add_argument("csv", nargs="+")
    towns.add_argument("--out", required=True)
    towns.add_argument("--include-multiword", action="store_true")
    towns.add_argument(
        "--allowed-type",
        action="append",
        help="OS Open Names LOCAL_TYPE to keep; repeat for multiple values.",
    )

    scowl = sub.add_parser("extract-scowl")
    scowl.add_argument("word_files", nargs="+")
    scowl.add_argument("--out", required=True)
    scowl.add_argument("--min-len", type=int, default=3)
    scowl.add_argument("--max-len", type=int, default=12)

    ranked_scowl = sub.add_parser("extract-scowl-ranked")
    ranked_scowl.add_argument("--db", required=True)
    ranked_scowl.add_argument("--out", required=True)
    ranked_scowl.add_argument("--max-size", type=int, default=60)
    ranked_scowl.add_argument("--spelling", default="B")
    ranked_scowl.add_argument("--variant-level", type=int, default=1)
    ranked_scowl.add_argument("--min-len", type=int, default=3)
    ranked_scowl.add_argument("--max-len", type=int, default=12)

    morph = sub.add_parser("filter-kaikki")
    morph.add_argument("--kaikki", required=True)
    morph.add_argument("--words", required=True)
    morph.add_argument("--out", required=True)
    morph.add_argument("--metadata-out")
    morph.add_argument("--rejected-out")

    ties = sub.add_parser("extract-ties")
    ties.add_argument("--words", required=True)
    ties.add_argument("--ranks", required=True)
    ties.add_argument("--out", required=True)
    ties.add_argument("--min-levenshtein", type=int, default=3)

    score = sub.add_parser("score")
    score.add_argument("input")
    score.add_argument("--out", required=True)
    score.add_argument("--model", default="llama3.2:1b")
    score.add_argument("--batch-size", type=int, default=80)
    score.add_argument("--timeout-s", type=float, default=180.0)
    score.add_argument("--retries", type=int, default=2)
    score.add_argument("--resume", action="store_true")
    score.add_argument("--force", action="store_true")
    score.add_argument("--max-new", type=int)

    encode_int = sub.add_parser("encode-int")
    encode_int.add_argument("value", type=int)
    _add_word_town_args(encode_int)

    sample = sub.add_parser("sample")
    sample.add_argument("--fun-bias", type=float, default=0.35)
    sample.add_argument("--count", type=int, default=1)
    _add_word_town_args(sample)

    encode_latlon = sub.add_parser("encode-latlon")
    encode_latlon.add_argument("lat", type=float)
    encode_latlon.add_argument("lon", type=float)
    encode_latlon.add_argument("--resolution-m", type=float, default=100.0)
    _add_word_town_args(encode_latlon)

    args = parser.parse_args()
    if args.command == "extract-towns":
        names, stats = extract_os_open_names_towns(
            args.csv,
            allowed_types=set(args.allowed_type) if args.allowed_type else None,
            include_multiword=args.include_multiword,
        )
        write_lines(args.out, names)
        print(f"read={stats.read} kept={stats.kept} rejected={stats.rejected}")
        return

    if args.command == "extract-scowl":
        words, stats = load_scowl_words(
            args.word_files,
            min_len=args.min_len,
            max_len=args.max_len,
        )
        write_lines(args.out, words)
        print(f"read={stats.read} kept={stats.kept} rejected={stats.rejected}")
        return

    if args.command == "extract-scowl-ranked":
        ranks, stats = extract_scowl_ranked_words(
            args.db,
            max_size=args.max_size,
            spelling=args.spelling,
            variant_level=args.variant_level,
            min_len=args.min_len,
            max_len=args.max_len,
        )
        write_ranks(args.out, ranks)
        print(f"read={stats.read} kept={stats.kept} rejected={stats.rejected}")
        return

    if args.command == "filter-kaikki":
        words = load_word_list(args.words)
        accepted, rejected, stats = extract_kaikki_word_metadata(args.kaikki, words)
        write_lines(args.out, accepted)
        if args.metadata_out:
            write_metadata(args.metadata_out, accepted)
        if args.rejected_out:
            write_metadata(args.rejected_out, rejected)
        print(f"read={stats.read} kept={stats.kept} rejected={stats.rejected}")
        return

    if args.command == "extract-ties":
        words = load_word_list(args.words)
        ranks = load_ranks(args.ranks)
        tied_words = find_rank_tie_collision_words(
            words,
            ranks,
            min_levenshtein=args.min_levenshtein,
        )
        write_lines(args.out, tied_words)
        print(f"kept={len(tied_words)}")
        return

    if args.command == "score":
        values = load_word_list(args.input)
        total_values = len(values)
        existing_scores = (
            load_scores(args.out)
            if args.resume and not args.force and Path(args.out).exists()
            else {}
        )
        if args.max_new is not None:
            values = [word for word in values if word not in existing_scores][: args.max_new]

        def checkpoint(scores: dict[str, float]) -> None:
            write_scores(args.out, scores)
            print(f"scored={len(scores)}/{total_values}", flush=True)

        scores = score_words_with_ollama(
            values,
            model=args.model,
            batch_size=args.batch_size,
            timeout_s=args.timeout_s,
            existing_scores=existing_scores,
            on_batch=checkpoint,
            retries=args.retries,
        )
        write_scores(args.out, scores)
        print(f"scored={len(scores)}")
        return

    if args.command == "build":
        words = load_word_list(args.words)
        scores = load_scores(args.scores) if args.scores else {}
        ranks = load_ranks(args.ranks) if args.ranks else None
        preferences = load_scores(args.preferences) if args.preferences else None
        lexicon = LexiconBuilder(
            min_levenshtein=args.min_levenshtein,
            ranks=ranks,
            preferences=preferences,
        ).build(words)
        write_scored_word_list(args.out, lexicon.words, scores)
        if args.rejected_out:
            write_rejections(args.rejected_out, lexicon.rejected)
        print(f"kept={len(lexicon.words)} rejected={len(lexicon.rejected)}")
        return

    words, embedded_scores = load_scored_word_list(args.words)
    coder = LocalWhat2Words(
        towns=load_word_list(args.towns),
        words=words,
    )
    if args.command == "encode-int":
        print(coder.encode_int(args.value))
    elif args.command == "sample":
        for _ in range(args.count):
            print(
                coder.sample_code(
                    word_scores=embedded_scores,
                    fun_bias=args.fun_bias,
                )
            )
    elif args.command == "encode-latlon":
        print(coder.encode_latlon(args.lat, args.lon, args.resolution_m))


def _add_word_town_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--words", required=True)
    parser.add_argument("--towns", required=True)


if __name__ == "__main__":
    main