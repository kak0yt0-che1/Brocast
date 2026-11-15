"""Turns a weather context into a recommendation using local rules — no paid API."""
from __future__ import annotations

_PART_LABELS_RU = {"morning": "утром", "afternoon": "днём", "evening": "вечером"}


def _num(value: object) -> float | None:
    return value if isinstance(value, (int, float)) else None


def build_recommendation(context: dict) -> dict:
    """Plain-mode advice: a headline, a one-liner, and a few concrete tips."""
    today = context.get("today", {})
    parts = context.get("by_part_of_day", {})
    tmax = _num(today.get("temp_max_c"))
    tmin = _num(today.get("temp_min_c"))
    feels_max = _num(today.get("feels_like_max_c"))
    uv = _num(today.get("uv_index_max"))
    gust = _num(today.get("wind_gust_max_kmh"))
    rain = _is_rainy(today)

    tips: list[str] = []
    if rain:
        when = _PART_LABELS_RU.get(_wettest_part(parts))
        tips.append(f"Возьми зонт — {when} вероятен дождь." if when else "Возьми зонт — вероятен дождь.")
    if gust is not None and gust >= 55:
        tips.append("Сильные порывы ветра — куртка и осторожно с незакреплёнными вещами.")
    elif gust is not None and gust >= 35:
        tips.append("Ветрено — пригодится ветровка.")
    if uv is not None and uv >= 8:
        tips.append("Очень высокий УФ — крем, кепка, очки.")
    elif uv is not None and uv >= 6:
        tips.append("Высокий УФ в полдень — нанеси SPF.")
    if tmax is not None and tmax >= 30:
        tips.append("Жара — лёгкая одежда и вода с собой.")
    elif tmax is not None and tmax >= 27:
        tips.append("Тепло — пей больше воды.")
    if tmin is not None and tmin <= -5:
        tips.append("Мороз — шапка, перчатки, тёплая куртка.")
    elif tmin is not None and tmin <= 2:
        tips.append("Холодное утро — оденься теплее.")
    if tmax is not None and tmin is not None and tmax - tmin >= 10 and tmax < 30:
        tips.append("Утром прохладно, днём теплее — одевайся слоями.")
    if not tips:
        tips.append("Спокойный день — ничего особенного не нужно.")

    return {
        "headline": _headline(tmax, tmin, uv, gust, rain),
        "summary": _summary(tmax, tmin, feels_max, rain),
        "tips": tips[:5],
    }


def build_styled(context: dict, phrase: str | None, evening: bool = False) -> str:
    """Persona mode: the fixed phrase plus a short factual line built from the data."""
    fact = _evening_fact_ru(context) if evening else _fact_ru(context)
    return f"{phrase} {fact}" if phrase else fact


def _evening_fact_ru(context: dict) -> str:
    hours = context.get("rain_hours_left") or []
    if not hours:
        return "Сейчас без дождя."
    return f"Дождь ожидается около {hours[0]:02d}:00."


def build_reminder_text(context: dict, phrase: str | None) -> str:
    """Short 'right now' line for custom reminders."""
    cur = context.get("current", {})
    temp, feels = _num(cur.get("temp_c")), _num(cur.get("feels_like_c"))
    parts = []
    if temp is not None:
        parts.append(f"Сейчас {round(temp):+d}°")
        if feels is not None and abs(feels - temp) >= 3:
            parts[-1] += f" (ощущается {round(feels):+d}°)"
    hours = context.get("rain_hours_left") or []
    if hours:
        parts.append(f"дождь около {hours[0]:02d}:00")
    fact = ", ".join(parts) + "." if parts else ""
    return " ".join(x for x in (phrase, fact) if x)


def _is_rainy(today: dict) -> bool:
    prob = _num(today.get("precip_prob_max_pct"))
    total = _num(today.get("precip_sum_mm"))
    return (prob is not None and prob >= 50) or (total is not None and total >= 1.0)


def _wettest_part(parts: dict) -> str | None:
    best, best_prob = None, 0.0
    for label in ("morning", "afternoon", "evening"):
        prob = _num(parts.get(label, {}).get("precip_prob_max_pct"))
        if prob is not None and prob > best_prob:
            best, best_prob = label, prob
    return best if best_prob >= 50 else None


def _headline(tmax, tmin, uv, gust, rain) -> str:
    if rain:
        return "Будет дождь"
    if tmin is not None and tmin <= 2:
        return "Холодный день"
    if tmax is not None and tmax >= 28:
        return "Жаркий день"
    if gust is not None and gust >= 35:
        return "Ветрено"
    if uv is not None and uv >= 7:
        return "Активное солнце"
    return "Спокойный день"


def _summary(tmax, tmin, feels_max, rain) -> str:
    if tmax is None or tmin is None:
        return "Прогноз на сегодня."
    text = f"Сегодня {round(tmin):+d}…{round(tmax):+d}°"
    if feels_max is not None and abs(feels_max - tmax) >= 3:
        text += f" (ощущается как {round(feels_max):+d}°)"
    text += "."
    if rain:
        text += " Ожидается дождь."
    return text


def _fact_ru(context: dict) -> str:
    today = context.get("today", {})
    tmax = _num(today.get("temp_max_c"))
    tmin = _num(today.get("temp_min_c"))
    if tmin is not None and tmax is not None:
        base = f"Сегодня {round(tmin):+d}…{round(tmax):+d}°"
    elif tmax is not None:
        base = f"Сегодня до {round(tmax):+d}°"
    else:
        base = "Сегодня"
    return f"{base}{_condition_ru(today, context.get('by_part_of_day', {}))}."


def _condition_ru(today: dict, parts: dict) -> str:
    if _is_rainy(today):
        part = _wettest_part(parts)
        when = _PART_LABELS_RU.get(part)
        return f", {when} дождь" if when else ", дождь"
    gust = _num(today.get("wind_gust_max_kmh"))
    uv = _num(today.get("uv_index_max"))
    tmax = _num(today.get("temp_max_c"))
    tmin = _num(today.get("temp_min_c"))
    if tmax is not None and tmax >= 28:
        return ", жара"
    if gust is not None and gust >= 35:
        return ", сильный ветер"
    if uv is not None and uv >= 7:
        return ", активное солнце"
    if tmin is not None and tmin <= -5:
        return ", мороз"
    return ", погода спокойная"
