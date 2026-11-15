"""Style phrase pools. The app picks a random phrase for the day's category and the user's tone."""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

CATEGORIES = (
    "heat", "rain", "cool_windy", "freezing", "chaotic", "mild",
    "storm_thunder", "blizzard", "umbrella",
)
TONES = ("bro", "nice", "mix")  # bro = blunt/rude, nice = kind, mix = both
TONE_LABELS = {"bro": "😎 По-братски", "nice": "🤗 По-доброму", "mix": "🎲 Микс"}

THUNDER_CODES = {95, 96, 99}
SNOW_CODES = {71, 73, 75, 77, 85, 86}

_DIR = Path(__file__).parent
_DEFAULT_PATHS = {"bro": _DIR / "phrases.json", "nice": _DIR / "phrases_nice.json"}


def _load_pools(path: Path) -> dict[str, list[str]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Phrase pool %s unavailable (%s)", path.name, type(exc).__name__)
        return {}
    if not isinstance(raw, dict):
        logger.warning("%s is not an object; ignoring", path.name)
        return {}
    return {
        category: [p for p in raw.get(category, []) if isinstance(p, str) and p.strip()]
        for category in CATEGORIES
    }


class PhrasePool:
    def __init__(self, pools: dict[str, dict[str, list[str]]]) -> None:
        self._pools = pools  # tone -> category -> phrases
        self._last: dict[tuple[str, str], str] = {}

    @classmethod
    def load(cls, paths: dict[str, Path] | None = None) -> "PhrasePool":
        paths = paths or _DEFAULT_PATHS
        return cls({tone: _load_pools(path) for tone, path in paths.items()})

    def pick(self, category: str, tone: str = "mix") -> str | None:
        """Random phrase, avoiding an immediate repeat of the previous one for the same slot."""
        tones = ["bro", "nice"] if tone == "mix" else [tone if tone in self._pools else "bro"]
        candidates = [p for t in tones for p in self._pools.get(t, {}).get(category, [])]
        if not candidates:  # chosen tone has nothing for this category: use any tone
            candidates = [p for pools in self._pools.values() for p in pools.get(category, [])]
        if not candidates:
            return None
        key = (tone, category)
        fresh = [p for p in candidates if p != self._last.get(key)] or candidates
        choice = random.choice(fresh)
        self._last[key] = choice
        return choice


def categorize_weather(context: dict) -> str:
    """Pick the style category that fits today.

    Checked worst-first: thunderstorm, blizzard, freezing, rain, heat, cool/windy,
    a big day-long swing, and finally "mild" for a calm, dry, roughly +17..+24 day.
    """
    today = context.get("today", {})
    temp_max = today.get("temp_max_c")
    temp_min = today.get("temp_min_c")
    uv_max = today.get("uv_index_max")

    def at_least(value: object, threshold: float) -> bool:
        return isinstance(value, (int, float)) and value >= threshold

    def at_most(value: object, threshold: float) -> bool:
        return isinstance(value, (int, float)) and value <= threshold

    rainy = at_least(today.get("precip_prob_max_pct"), 50) or at_least(today.get("precip_sum_mm"), 1.0)
    big_swing = (
        isinstance(temp_max, (int, float))
        and isinstance(temp_min, (int, float))
        and temp_max - temp_min >= 12
    )

    code = today.get("weather_code")
    gust = today.get("wind_gust_max_kmh")
    if code in THUNDER_CODES:
        return "storm_thunder"
    if code in SNOW_CODES and (at_least(gust, 35) or at_most(temp_max, -5)):
        return "blizzard"
    if at_most(temp_max, 3):
        return "freezing"
    if rainy:
        return "chaotic" if big_swing else "rain"
    if at_least(temp_max, 28) or at_least(uv_max, 7):
        return "heat"
    if at_least(gust, 35) or at_most(temp_max, 16):
        return "cool_windy"
    if big_swing:
        return "chaotic"
    return "mild"
