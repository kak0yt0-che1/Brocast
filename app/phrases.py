"""Style phrase pools. The app picks a phrase; the model only wraps advice around it."""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)

CATEGORIES = ("heat", "rain", "cool_windy", "freezing", "chaotic", "mild")

_DEFAULT_PATH = Path(__file__).with_name("phrases.json")


class PhrasePool:
    def __init__(self, pools: dict[str, list[str]]) -> None:
        self._pools = pools

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PhrasePool":
        target = Path(path) if path else _DEFAULT_PATH
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Phrase pool unavailable (%s); styling disabled", type(exc).__name__)
            return cls({})
        if not isinstance(raw, dict):
            logger.warning("phrases.json is not an object; styling disabled")
            return cls({})
        pools = {
            category: [p for p in raw.get(category, []) if isinstance(p, str) and p.strip()]
            for category in CATEGORIES
        }
        return cls(pools)

    def pick(self, category: str) -> str | None:
        phrases = self._pools.get(category)
        return random.choice(phrases) if phrases else None


def categorize_weather(context: dict) -> str:
    """Pick the style category that fits today.

    Checked worst-first: freezing, rain, heat, cool/windy, a big day-long swing,
    and finally "mild" for a calm, dry, roughly +17..+24 day worth no warning.
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

    if at_most(temp_max, 3):
        return "freezing"
    if rainy:
        return "chaotic" if big_swing else "rain"
    if at_least(temp_max, 28) or at_least(uv_max, 7):
        return "heat"
    if at_least(today.get("wind_gust_max_kmh"), 35) or at_most(temp_max, 16):
        return "cool_windy"
    if big_swing:
        return "chaotic"
    return "mild"
