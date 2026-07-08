# what-town-two-words

> :warning: :robot: this tool was vibe coded with Codex

<p align="center">
  <img src="assets/icon.png" alt="Cartoon die showing a house, strong arm, and carrot" width="220">
</p>

A local, hackable `town.word.word` encoder inspired by what3words-style codes,
but built from your own word list and British town names.

The package includes:

- profanity blacklist filtering
- optional CMUdict homophone rejection via `pronouncing`
- minimum Levenshtein distance filtering
- optional metaphone collision rejection via `Metaphone`
- plural/singular pair rejection
- optional Ollama smile-value scoring for collision tie-breaks
- reversible integer and approximate lat/lon grid encoding

## Install

```bash
python3 -m pip install -e ".[phonetics,dev]"
```

The package works without optional dependencies, but CMUdict and true metaphone
checks are best with `.[phonetics]`.

## Quick Use

```python
from what_town_two_words import LexiconBuilder, LocalWhat2Words

words = ["meeting", "penguin", "klutz", "button", "rocket", "pickle"]
towns = ["Bristol", "York", "Bath"]

lexicon = LexiconBuilder(min_levenshtein=3).build(words)
coder = LocalWhat2Words(towns=towns, words=lexicon.words)

code = coder.encode_int(12)
assert coder.decode_code(code) == 12
```

For coordinates:

```python
coder = LocalWhat2Words.from_builtin()
code = coder.encode_latlon(51.5074, -0.1278, resolution_m=5000)
cell = coder.decode_latlon_code(code, resolution_m=5000)
print(code, cell.center)
```

## Ollama Tie-Breaks

Run a small local model, for example:

```bash
ollama pull llama3.2:1b
```

Then:

```python
from what_town_two_words import score_words_with_ollama

scores = score_words_with_ollama(
    ["meeting", "penguin", "klutz"],
    model="llama3.2:1b",
)
```

The score is only meant to break ties between otherwise equal collision
candidates. The expected direction is:

```text
meeting < penguin < klutz
```

Use these scores with `build --preferences`; frequency rank still wins before
smile-value is considered.

## Data Pipeline

```bash
what-town-two-words extract-towns data/raw/os-open-names/*.csv \
  --include-multiword \
  --out data/build/city-towns-villages-hamlets.txt

what-town-two-words extract-towns data/raw/os-open-names/*.csv \
  --include-multiword \
  --allowed-type City \
  --allowed-type Town \
  --out data/build/cities-towns.txt

what-town-two-words extract-scowl data/raw/scowl/final/english-words.* \
  --out data/build/candidate_words.txt

what-town-two-words filter-kaikki \
  --kaikki data/raw/kaikki/kaikki.org-dictionary-English.jsonl.gz \
  --words data/build/candidate_words.txt \
  --out data/build/morph_words.txt \
  --metadata-out data/build/morph_metadata.tsv \
  --rejected-out data/build/morph_rejected.tsv

what-town-two-words extract-scowl-ranked \
  --db data/raw/scowl/wordlist/scowl.db \
  --out data/build/word_ranks.tsv \
  --max-size 60 \
  --spelling B

what-town-two-words score data/build/candidate_words.txt \
  --model llama3.2:1b \
  --out data/build/word_preferences.json

what-town-two-words build \
  --words data/build/morph_words.txt \
  --ranks data/build/word_ranks.tsv \
  --preferences data/build/word_preferences.json \
  --out data/build/filtered_words.txt \
  --rejected-out data/build/rejected_words.tsv

what-town-two-words encode-int 12345 \
  --words data/build/filtered_words.txt \
  --towns data/build/city-towns-villages-hamlets.txt

what-town-two-words encode-latlon 51.5074 -0.1278 \
  --words data/build/filtered_words.txt \
  --towns data/build/city-towns-villages-hamlets.txt

```

Use `data/build/cities-towns.txt` instead for a stricter City/Town-only first
component.

This is intentionally local-first: no central lookup service, no remote API, and
no baked-in global address database.

The bundled lists are only seeds for demos and tests. For fine coordinate
resolutions, use a larger filtered word list so `town_count * word_count^2`
comfortably exceeds the number of grid cells in your chosen bounding box.

When collision filters find similar words, the builder processes lower-ranked
words first, so the more common/preferred word is kept. SCOWL `size` is a coarse
rank where lower means more common. 