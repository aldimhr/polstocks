"""Pure utility functions: dates, text, normalization, helpers."""

from __future__ import annotations

import difflib
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, time as dtime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from backend.config import (
    WIB, STOCK_MASTER, DEFAULT_EVENT_WINDOW, EVENT_WINDOWS,
)

def now_wib() -> datetime:
    return datetime.now(WIB)


def now_iso() -> str:
    return now_wib().isoformat(timespec="seconds")


def normalize_ticker(value: str) -> str:
    value = (value or "").strip().upper()
    if not value:
        return ""
    return value if value.endswith(".JK") else f"{value}.JK"


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def safe_text(node: ET.Element | None, wanted: str) -> str:
    if node is None:
        return ""
    for child in node.iter():
        if local_name(child.tag) == wanted and child.text:
            return child.text.strip()
    return ""


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WIB)
        return dt.astimezone(WIB)
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=WIB)
            return dt.astimezone(WIB)
        except Exception:
            continue
    return None


_INDONESIAN_MONTHS = {
    "januari": 1,
    "februari": 2,
    "maret": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "agustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "desember": 12,
}

_ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _parse_human_date_text(value: str | None) -> datetime | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", str(value).strip())
    match = re.search(
        r"(?P<day>\d{1,2})\s+(?P<month>[A-Za-zÀ-ÿ]+)\s+(?P<year>\d{4})(?:\s+(?P<hour>\d{1,2})[.:](?P<minute>\d{2})(?::(?P<second>\d{2}))?\s*(?P<tz>WIB|WITA|WIT|UTC|GMT)?)?",
        text,
        flags=re.I,
    )
    if not match:
        return None
    month_name = match.group("month").strip().lower()
    month = _INDONESIAN_MONTHS.get(month_name) or _ENGLISH_MONTHS.get(month_name)
    if not month:
        return None
    day = int(match.group("day"))
    year = int(match.group("year"))
    hour = int(match.group("hour") or 0)
    minute = int(match.group("minute") or 0)
    second = int(match.group("second") or 0)
    tz_name = (match.group("tz") or "WIB").upper()
    tz = {
        "WIB": WIB,
        "WITA": timezone(timedelta(hours=8)),
        "WIT": timezone(timedelta(hours=9)),
        "UTC": timezone.utc,
        "GMT": timezone.utc,
    }.get(tz_name, WIB)
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=tz).astimezone(WIB)
    except Exception:
        return None


def extract_html_published_at(html_text: str) -> datetime | None:
    if not html_text:
        return None
    meta_patterns = (
        r'<meta[^>]+(?:property|name)=["\']article:published_time["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+(?:property|name)=["\']og:published_time["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+(?:property|name)=["\']article:modified_time["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+(?:property|name)=["\']date["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+(?:property|name)=["\']pubdate["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+(?:property|name)=["\']publishdate["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+(?:property|name)=["\']dc\.date(?:\.issued)?["\'][^>]+content=["\']([^"\']+)',
    )
    for pattern in meta_patterns:
        match = re.search(pattern, html_text, flags=re.I)
        if match:
            parsed = parse_datetime(match.group(1))
            if parsed:
                return parsed
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html_text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(re.sub(r"\s+", " ", text))
    visible_patterns = (
        r"(?:dipublikasikan pada|published on|posted on|diterbitkan pada|terbit pada)\s+([0-9]{1,2}\s+[A-Za-zÀ-ÿ]+\s+[0-9]{4}(?:\s+[0-9]{1,2}[.:][0-9]{2}(?::[0-9]{2})?\s*(?:WIB|WITA|WIT|UTC|GMT)?)?)",
        r"([0-9]{1,2}\s+[A-Za-zÀ-ÿ]+\s+[0-9]{4}(?:\s+[0-9]{1,2}[.:][0-9]{2}(?::[0-9]{2})?\s*(?:WIB|WITA|WIT|UTC|GMT)?)?)",
    )
    for pattern in visible_patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            parsed = _parse_human_date_text(match.group(1))
            if parsed:
                return parsed
    return None


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def normalize_match_text(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def collect_phrase_hits(text: str, phrases: list[str]) -> list[str]:
    normalized_text = normalize_match_text(text)
    hits: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        normalized_phrase = normalize_match_text(phrase)
        if not normalized_phrase:
            continue
        if normalized_phrase in normalized_text and normalized_phrase not in seen:
            hits.append(normalized_phrase)
            seen.add(normalized_phrase)
    return hits


def normalize_event_window(value: str | None) -> str:
    key = str(value or DEFAULT_EVENT_WINDOW).strip().lower()
    return key if key in EVENT_WINDOWS else DEFAULT_EVENT_WINDOW


def event_window_config(window: str | None) -> dict[str, Any]:
    return EVENT_WINDOWS[normalize_event_window(window)]


def event_window_delta(window: str | None) -> timedelta:
    return event_window_config(window)["delta"]


def event_window_label(window: str | None) -> str:
    return str(event_window_config(window)["label"])


def text_similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, left.lower().strip(), right.lower().strip()).ratio()


def is_stale_article(published_at: datetime | None, window: str | None = None) -> bool:
    if not published_at:
        return False
    return now_wib() - published_at > event_window_delta(window)


def within_trading_hours(ts: datetime | None = None) -> bool:
    ts = ts or now_wib()
    if ts.weekday() >= 5:
        return False
    current = ts.time()
    return dtime(9, 0) <= current <= dtime(15, 0)


def sector_for_ticker(ticker: str) -> str:
    return STOCK_MASTER.get(ticker, {}).get("sector", "Financials")


def company_name_for_ticker(ticker: str) -> str:
    return STOCK_MASTER.get(ticker, {}).get("name", ticker.replace(".JK", ""))


def article_text(article: dict[str, Any]) -> str:
    parts = [article.get("headline", ""), article.get("summary", ""), article.get("source", "")]
    return " ".join(p for p in parts if p).lower()


