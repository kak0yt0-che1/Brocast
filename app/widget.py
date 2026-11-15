"""Telegram widget (Bot API 10.4 `sendWidget`): a small HTML card shown inside the chat bubble.

The raw HTTP API is used because aiogram may not wrap sendWidget yet. Any failure
returns False so the caller can fall back to a plain text message.
"""
from __future__ import annotations

import html
import logging
import zlib

import httpx

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendWidget"
MAX_HTML_BYTES = 32768
MAX_COMPRESSED_BYTES = 4096

_PART_LABELS = {"morning": "Утро", "afternoon": "День", "evening": "Вечер"}

_STYLE = (
    "body{margin:0;padding:10px 12px;font:14px system-ui,sans-serif;color:#222;background:#fff}"
    "body.dark{color:#eee;background:#1c1c1e}"
    ".t{font-size:34px;font-weight:700;line-height:1}.s{opacity:.7;margin-top:2px}"
    ".p{margin:8px 0;font-size:13px}.r{display:flex;gap:6px;margin-top:8px}"
    ".c{flex:1;text-align:center;padding:6px 2px;border-radius:8px;background:rgba(128,128,128,.15)}"
    ".c b{display:block}.u{margin-top:8px;font-weight:600}"
)
_SCRIPT = "var W=window.TelegramWidget;if(W&&W.colorScheme==='dark')document.body.className='dark';"


def _icon(code: object) -> str:
    if code in {95, 96, 99}:
        return "⛈"
    if code in {71, 73, 75, 77, 85, 86}:
        return "❄️"
    if isinstance(code, int) and (51 <= code <= 67 or 80 <= code <= 82):
        return "🌧"
    if code in {45, 48}:
        return "🌫"
    if code in {2, 3}:
        return "⛅"
    return "☀️"


def _deg(value: object) -> str:
    return f"{round(value):+d}°" if isinstance(value, (int, float)) else "—"


def build_widget_html(context: dict, phrase: str | None, evening: bool = False) -> str:
    today = context.get("today", {})
    cur = context.get("current", {})
    parts = context.get("by_part_of_day", {})
    esc = html.escape
    cells = "".join(
        f'<div class="c">{label}<b>{_deg(parts.get(key, {}).get("temp_c"))}</b></div>'
        for key, label in _PART_LABELS.items()
    )
    umbrella = ""
    rain_hours = context.get("rain_hours_left") or []
    if rain_hours:
        umbrella = '<div class="u">☂️ Зонт с собой!</div>'
    now_t = _deg(cur.get("temp_c")) if evening else _deg(today.get("temp_max_c"))
    sub = (
        f"Сейчас, ощущается {_deg(cur.get('feels_like_c'))}"
        if evening
        else f"{_deg(today.get('temp_min_c'))} … {_deg(today.get('temp_max_c'))}"
    )
    body = (
        f'<div class="t">{_icon(today.get("weather_code"))} {now_t}</div>'
        f'<div class="s">{esc(str(context.get("city", "")))} · {esc(sub)}</div>'
        f'<div class="p">{esc(phrase or "")}</div>{umbrella}<div class="r">{cells}</div>'
    )
    return f"<style>{_STYLE}</style>{body}<script>{_SCRIPT}</script>"


async def send_widget(token: str, chat_id: int, html_doc: str, caption: str) -> bool:
    raw = html_doc.encode("utf-8")
    if len(raw) > MAX_HTML_BYTES or len(zlib.compress(raw, 9)) > MAX_COMPRESSED_BYTES:
        logger.warning("Widget too large, falling back to text")
        return False
    payload = {"chat_id": chat_id, "html": html_doc, "caption": caption[:1000]}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(API_URL.format(token=token), json=payload)
        ok = response.status_code == 200 and response.json().get("ok") is True
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("sendWidget failed: %s", type(exc).__name__)
        return False
    if not ok:
        logger.warning("sendWidget rejected (HTTP %s)", response.status_code)
    return ok
