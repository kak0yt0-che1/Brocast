"""Inline keyboards for the bot menu."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

PRESET_HOURS = range(6, 23) 


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🌤 Сейчас", callback_data="now")
    kb.button(text="🕗 Время", callback_data="time_menu")
    kb.button(text="⚙️ Статус", callback_data="status")
    kb.button(text="🔕 Выключить", callback_data="stop")
    kb.adjust(2, 2)
    return kb.as_markup()


def time_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for hour in PRESET_HOURS:
        kb.button(text=f"{hour:02d}:00", callback_data=f"set:{hour:02d}:00")
    kb.adjust(4)
    kb.row(
        InlineKeyboardButton(text="✍️ Своё время", callback_data="time_custom"),
        InlineKeyboardButton(text="‹ Назад", callback_data="menu"),
    )
    return kb.as_markup()
