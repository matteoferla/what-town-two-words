from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from .filters import normalize_word


def score_words_with_ollama(
    words: list[str],
    model: str = "llama3.2:1b",
    endpoint: str = "http://localhost:11434/api/generate",
    timeout_s: float = 60.0,
    batch_size: int = 80,
    existing_scores: dict[str, float] | None = None,
    on_batch: Callable[[dict[str, float]], None] | None = None,
    retries: int = 2,
) -> dict[str, float]:
    """Score words for smile-value as a collision tie-breaker using Ollama."""
    clean_words = [normalize_word(word) for word in words]
    scores: dict[str, float] = dict(existing_scores or {})
    remaining = [word for word in clean_words if word not in scores]
    for start in range(0, len(remaining), batch_size):
        batch = remaining[start : start + batch_size]
        batch_scores = _score_batch_with_ollama(
            batch=batch,
            model=model,
            endpoint=endpoint,
            timeout_s=timeout_s,
            retries=retries,
        )
        scores.update(batch_scores)
        if on_batch:
            on_batch(scores)
    return scores


def _score_batch_with_ollama(
    batch: list[str],
    model: str,
    endpoint: str,
    timeout_s: float,
    retries: int = 2,
) -> dict[str, float]:
    prompt = _prompt(batch)
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    body = _post_with_retries(request, timeout_s=timeout_s, retries=retries)

    text = body.get("response", "")
    parsed = _parse_json_object(text)
    scores = parsed.get("scores", parsed)
    return {word: _clamp_float(scores.get(word, 0.0)) for word in batch}


def _post_with_retries(
    request: urllib.request.Request,
    timeout_s: float,
    retries: int,
) -> dict:
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                return json.loads(response.read().decode("utf-8"))
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(min(30.0, 2.0 ** attempt))
    raise RuntimeError(
        "Ollama request failed after retries. The existing score file is still "
        "usable with --resume; try a smaller --batch-size or larger --timeout-s."
    ) from last_error


def _prompt(words: list[str]) -> str:
    joined = json.dumps(words)
    return (
        "You are scoring single English words only as a tie-breaker between "
        "otherwise equally common collision candidates for friendly short map "
        "codes. Score each word independently from 0.0 to 1.0 for SMILE VALUE, "
        "using this hierarchy. 1. Highest: directly comic or playful meaning "
        "such as tickle, giggle, joke, clown, farce. 2. Comic mishap, "
        "awkwardness, slapstick, embarrassment, lewdness, bodily comedy, or "
        "haplessness such as klutz, wobble, pratfall, bonk. 3. Words frequent "
        "in joke setups, pub chat, comic scenes, innuendo, or familiar comic "
        "stock situations such as bar, priest, banana, trousers. 4. Cute, "
        "endearing, or inherently odd referents such as penguin, aardvark. "
        "5. Funny sound or mouthfeel only, such as pickle, noodle, kazoo. "
        "6. Neutral vivid concrete words such as rocket, lantern. 7. Lowest: "
        "abstract, administrative, technical, Latinate in a dry way, medical, "
        "hostile, or bleak words, unless the concept itself is comic. Do not "
        "reward rarity by itself. Prefer actual comic usefulness over merely "
        "unusual spelling. Calibrations: meeting=0.1, absorption=0.0, "
        "abstinence=0.55, tickle=0.9, pickle=0.6, penguin=0.8, "
        "aardvark=0.65, klutz=0.9, farce=0.85, rocket=0.25, invoice=0.0. "
        "Return only JSON like "
        "{\"scores\":{\"penguin\":0.8}}. "
        f"Words: {joined}"
    )


def _parse_json_object(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"Ollama did not return JSON: {text[:200]!r}")
        return json.loads(match.group(0))


def _clamp_float(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number))
