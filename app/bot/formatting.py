"""Locale formatting for user-facing text. Times are shown in WIB (Asia/Jakarta), the default for D1 Indonesia."""

from datetime import datetime
from zoneinfo import ZoneInfo

WIB = ZoneInfo("Asia/Jakarta")
MONTHS = {
    "id": ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}


def format_date(value: datetime, lang: str) -> str:
    local = value.astimezone(WIB)
    months = MONTHS.get(lang, MONTHS["id"])
    return f"{local.day} {months[local.month - 1]} {local.year}"


def format_number(value: int, lang: str) -> str:
    """Thousands separators: 1.300 in Indonesian, 1,300 in English."""
    text = f"{value:,}"
    return text.replace(",", ".") if lang == "id" else text


def format_rupiah(value: int, lang: str) -> str:
    return f"Rp{format_number(value, 'id')}" if lang == "id" else f"IDR {format_number(value, 'en')}"
