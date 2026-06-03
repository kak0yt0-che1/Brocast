"""Open-Meteo client and the transform from raw forecast to a compact context."""
from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

DAILY_FIELDS = [
    "weather_code", "temperature_2m_max", "temperature_2m_min",
    "apparent_temperature_max", "apparent_temperature_min", "sunrise", "sunset",
    "precipitation_sum", "precipitation_probability_max", "wind_gusts_10m_max",
    "uv_index_max",
]
HOURLY_FIELDS = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m",
    "precipitation_probability", "precipitation", "weather_code", "cloud_cover",
    "visibility", "wind_speed_10m", "wind_gusts_10m", "uv_index",
]
CURRENT_FIELDS = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m",
    "weather_code", "cloud_cover", "wind_speed_10m", "wind_gusts_10m",
    "precipitation",
]

DAY_PARTS = [("morning", 6, 11), ("afternoon", 12, 17), ("evening", 18, 22)]


class WeatherError(Exception):
    """Forecast could not be fetched or came back malformed."""


class WeatherClient:
    BASE_URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, latitude: float, longitude: float, timeout: float = 10.0) -> None:
        self._latitude = latitude
        self._longitude = longitude
        self._timeout = timeout

    async def fetch(self) -> dict:
        params = {
            "latitude": self._latitude,
            "longitude": self._longitude,
            "daily": ",".join(DAILY_FIELDS),
            "hourly": ",".join(HOURLY_FIELDS),
            "current": ",".join(CURRENT_FIELDS),
            "timezone": "auto",
            "forecast_days": 1,
        }
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(3):
                try:
                    response = await client.get(self.BASE_URL, params=params)
                    response.raise_for_status()
                    data = response.json()
                    _check_shape(data)
                    return data
                except (httpx.HTTPError, ValueError) as exc:
                    last_exc = exc
                    logger.warning("Open-Meteo fetch failed (%d/3): %s", attempt + 1, type(exc).__name__)
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
        raise WeatherError("Open-Meteo request failed after retries") from last_exc


def _check_shape(data: object) -> None:
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    for section in ("daily", "hourly", "current"):
        if not isinstance(data.get(section), dict):
            raise ValueError(f"missing or invalid section: {section}")
    if not data["daily"].get("time") or not data["hourly"].get("time"):
        raise ValueError("empty time axis")


def _numbers(values: list) -> list[float]:
    return [v for v in values if isinstance(v, (int, float))]


def _round(value: object, digits: int = 1) -> float | None:
    return round(value, digits) if isinstance(value, (int, float)) else None


def _avg(values: list) -> float | None:
    nums = _numbers(values)
    return round(sum(nums) / len(nums), 1) if nums else None


def _peak(values: list, fn) -> float | None:
    nums = _numbers(values)
    return round(fn(nums), 1) if nums else None


def build_weather_context(raw: dict, city_name: str) -> dict:
    """Compress the API payload into a small snapshot of today.

    Hourly readings are folded into morning/afternoon/evening buckets so the
    model can see how the day moves without 24 raw rows per field.
    """
    daily, hourly, current = raw["daily"], raw["hourly"], raw["current"]
    today = daily["time"][0]  

    times: list[str] = hourly["time"]
    today_hours = [i for i, t in enumerate(times) if t.startswith(today)]

    def window(field: str, start: int, end: int) -> list:
        series = hourly.get(field) or []
        return [
            series[i] for i in today_hours
            if i < len(series) and start <= int(times[i][11:13]) <= end
        ]

    parts = {
        label: {
            "temp_c": _avg(window("temperature_2m", start, end)),
            "feels_like_c": _avg(window("apparent_temperature", start, end)),
            "humidity_pct": _avg(window("relative_humidity_2m", start, end)),
            "precip_prob_max_pct": _peak(window("precipitation_probability", start, end), max),
            "precip_mm": _round(sum(_numbers(window("precipitation", start, end)))),
            "wind_kmh": _avg(window("wind_speed_10m", start, end)),
            "wind_gust_max_kmh": _peak(window("wind_gusts_10m", start, end), max),
            "cloud_cover_pct": _avg(window("cloud_cover", start, end)),
            "visibility_min_m": _peak(window("visibility", start, end), min),
            "uv_index_max": _peak(window("uv_index", start, end), max),
        }
        for label, start, end in DAY_PARTS
    }

    def today_value(field: str):
        return (daily.get(field) or [None])[0]

    return {
        "city": city_name,
        "date": today,
        "current": {
            "temp_c": _round(current.get("temperature_2m")),
            "feels_like_c": _round(current.get("apparent_temperature")),
            "humidity_pct": _round(current.get("relative_humidity_2m")),
            "cloud_cover_pct": _round(current.get("cloud_cover")),
            "wind_kmh": _round(current.get("wind_speed_10m")),
            "wind_gust_kmh": _round(current.get("wind_gusts_10m")),
            "precip_mm": _round(current.get("precipitation")),
            "weather_code": current.get("weather_code"),
        },
        "today": {
            "temp_max_c": _round(today_value("temperature_2m_max")),
            "temp_min_c": _round(today_value("temperature_2m_min")),
            "feels_like_max_c": _round(today_value("apparent_temperature_max")),
            "feels_like_min_c": _round(today_value("apparent_temperature_min")),
            "precip_sum_mm": _round(today_value("precipitation_sum")),
            "precip_prob_max_pct": _round(today_value("precipitation_probability_max")),
            "wind_gust_max_kmh": _round(today_value("wind_gusts_10m_max")),
            "uv_index_max": _round(today_value("uv_index_max")),
            "sunrise": today_value("sunrise"),
            "sunset": today_value("sunset"),
            "weather_code": today_value("weather_code"),
        },
        "by_part_of_day": parts,
    }
