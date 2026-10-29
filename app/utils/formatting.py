import html
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def fmt_money(minor: int) -> str:
    """150000 (копеек) -> '1 500 ₽'."""
    rub, kop = divmod(minor, 100)
    text = f"{rub:,}".replace(",", " ")
    return f"{text},{kop:02d} ₽" if kop else f"{text} ₽"


def fmt_dt(utc: datetime, tz: ZoneInfo) -> str:
    local = utc.replace(tzinfo=UTC).astimezone(tz)
    return f"{local.day} {MONTHS[local.month - 1]}, {local:%H:%M}"


def user_link(user_id: int, name: str) -> str:
    return f'<a href="tg://user?id={user_id}">{html.escape(name)}</a>'


def normalize_phone(raw: str) -> str | None:
    """'8 (900) 123-45-67' -> '+79001234567'. None, если это не похоже на номер."""
    if re.search(r"[^\d\s()+\-]", raw):
        return None
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    if not 10 <= len(digits) <= 15:
        return None
    return "+" + digits


def parse_rubles(raw: str) -> int | None:
    """'1 490' / '1490,50' -> копейки. None, если не число или не положительное."""
    cleaned = raw.replace(" ", "").replace("₽", "").replace(",", ".")
    if not re.fullmatch(r"\d+(\.\d{1,2})?", cleaned):
        return None
    minor = round(float(cleaned) * 100)
    return minor if minor > 0 else None
