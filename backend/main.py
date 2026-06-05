#!/usr/bin/env python3
"""FastAPI backend for the Indonesia Political-Stock Impact System.

This rewrite follows the simplified SPEC.md / ARCHITECTURE.md docs:
- on-demand refresh
- RSS + government source fetching
- stock quote fetching
- heuristic NLP fallback with category, sentiment, entities, and sector mapping
- in-memory cache with 5 minute TTL
- watchlist endpoints
- browser dashboard served from a static HTML file
"""

from __future__ import annotations

import difflib
import html
import json
import math
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, time as dtime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_FILE = PROJECT_ROOT / "dashboard.html"
WATCHLIST_FILE = PROJECT_ROOT / "watchlist.json"
COMPANY_KNOWLEDGE_FILE = PROJECT_ROOT / "company_knowledge.json"
POLICY_SIGNAL_RULES_FILE = PROJECT_ROOT / "policy_signal_rules.json"
MARKET_VALIDATION_CONFIG_FILE = PROJECT_ROOT / "market_validation_config.json"
SOURCE_REGISTRY_FILE = PROJECT_ROOT / "source_registry.json"
SOURCE_OUTCOME_HISTORY_FILE = PROJECT_ROOT / "data/source_outcome_history.json"

APP_TITLE = "Indonesia Political-Stock Impact System"
CACHE_TTL_SECONDS = 300
DEFAULT_EVENT_WINDOW = "24h"
EVENT_WINDOWS = {
    "24h": {"delta": timedelta(hours=24), "label": "24 jam terakhir", "days": 1},
    "7d": {"delta": timedelta(days=7), "label": "7 hari terakhir", "days": 7},
    "30d": {"delta": timedelta(days=30), "label": "30 hari terakhir", "days": 30},
}
STOCK_HISTORY_WINDOWS = {
    "24h": {"range": "1d", "interval": "5m", "label": "24 jam terakhir"},
    "7d": {"range": "7d", "interval": "1h", "label": "7 hari terakhir"},
    "30d": {"range": "1mo", "interval": "1d", "label": "30 hari terakhir"},
}
SOURCE_TIMEOUT_SECONDS = 5
WIB = timezone(timedelta(hours=7))
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Hermes Political-Stock Mapper; +https://hermes-agent.nousresearch.com)"
}
SOURCE_TYPE_RANKS = {
    "government": 5.0,
    "regulator": 4.8,
    "company": 4.6,
    "media": 3.6,
    "profile": 2.7,
    "other": 2.4,
}

POLITICAL_SIGNAL_KEYWORDS = [
    "presiden",
    "wapres",
    "menteri",
    "kementerian",
    "kabinet",
    "dpr",
    "pemerintah",
    "politik",
    "kebijakan",
    "regulasi",
    "peraturan",
    "anggaran",
    "apbn",
    "bank indonesia",
    "bi rate",
    "suku bunga",
    "inflasi",
    "ojk",
    "kpk",
    "korupsi",
    "tersangka",
    "ekspor",
    "impor",
    "tarif",
    "hilirisasi",
    "pemilu",
    "pilkada",
    "sidang",
    "ruu",
    "uu ",
    "perpres",
    "perppu",
    "setkab",
]

SECTORS = [
    "Energy",
    "Basic Materials",
    "Industrials",
    "Consumer Cyclicals",
    "Consumer Non-Cyclicals",
    "Healthcare",
    "Financials",
    "Properties & Real Estate",
    "Technology",
    "Infrastructures",
    "Transportation & Logistics",
]

DEFAULT_WATCHLIST = [
    "BBCA.JK",
    "BBRI.JK",
    "BMRI.JK",
    "TLKM.JK",
    "ASII.JK",
    "GOTO.JK",
    "BYAN.JK",
    "ADRO.JK",
    "UNVR.JK",
    "ICBP.JK",
    "PTBA.JK",
    "ANTM.JK",
    "INDF.JK",
    "SMGR.JK",
    "KLBF.JK",
    "HMSP.JK",
    "PGAS.JK",
    "JSMR.JK",
    "EXCL.JK",
    "INCO.JK",
    "TOWR.JK",
    "MNCN.JK",
    "ITMG.JK",
    "HRUM.JK",
    "BSDE.JK",
    "CPIN.JK",
    "JPFA.JK",
    "ESSA.JK",
    "BRPT.JK",
    "MEDC.JK",
]

STOCK_SEED = [
    ("BBCA.JK", "Bank Central Asia", "Financials", ("bca", "bank central asia")),
    ("BBRI.JK", "Bank Rakyat Indonesia", "Financials", ("bri", "bank rakyat indonesia")),
    ("BMRI.JK", "Bank Mandiri", "Financials", ("mandiri", "bank mandiri")),
    ("TLKM.JK", "Telkom Indonesia", "Technology", ("telkom", "telkom indonesia", "indihome")),
    ("ASII.JK", "Astra International", "Consumer Cyclicals", ("astra", "astra international")),
    ("GOTO.JK", "GoTo Gojek Tokopedia", "Technology", ("goto", "gojek", "tokopedia")),
    ("BYAN.JK", "Bayan Resources", "Energy", ("bayan", "bayan resources")),
    ("ADRO.JK", "Adaro Energy Indonesia", "Energy", ("adaro", "adaro energy")),
    ("UNVR.JK", "Unilever Indonesia", "Consumer Non-Cyclicals", ("unilever", "unvr")),
    ("ICBP.JK", "Indofood CBP Sukses Makmur", "Consumer Non-Cyclicals", ("icbp", "indofood cbp")),
    ("PTBA.JK", "Bukit Asam", "Energy", ("ptba", "bukit asam")),
    ("ANTM.JK", "Aneka Tambang", "Basic Materials", ("antam", "anekatambang", "anek tambang")),
    ("INDF.JK", "Indofood Sukses Makmur", "Consumer Non-Cyclicals", ("indf", "indofood")),
    ("SMGR.JK", "Semen Indonesia", "Basic Materials", ("smgr", "semen indonesia")),
    ("KLBF.JK", "Kalbe Farma", "Healthcare", ("kalbe", "klbf")),
    ("HMSP.JK", "H.M. Sampoerna", "Consumer Non-Cyclicals", ("hmsp", "sampoerna")),
    ("PGAS.JK", "Perusahaan Gas Negara", "Energy", ("pgas", "pgn", "perusahaan gas negara")),
    ("JSMR.JK", "Jasa Marga", "Infrastructures", ("jsmr", "jasa marga")),
    ("EXCL.JK", "XL Axiata", "Technology", ("excl", "xl axiata")),
    ("INCO.JK", "Vale Indonesia", "Basic Materials", ("inco", "vale indonesia")),
    ("TOWR.JK", "Sarana Menara Nusantara", "Infrastructures", ("towr", "sarana menara")),
    ("MNCN.JK", "MNC Digital Entertainment", "Technology", ("mncn", "mncn", "mnc digital")),
    ("ITMG.JK", "Indo Tambangraya Megah", "Energy", ("itmg", "indo tambangraya")),
    ("HRUM.JK", "Harum Energy", "Energy", ("hrum", "harum energy")),
    ("BSDE.JK", "Bumi Serpong Damai", "Properties & Real Estate", ("bsde", "bumi serpong damai")),
    ("CPIN.JK", "Charoen Pokphand Indonesia", "Consumer Non-Cyclicals", ("cpin", "charoen pokphand")),
    ("JPFA.JK", "Japfa Comfeed Indonesia", "Consumer Non-Cyclicals", ("jpfa", "japfa")),
    ("ESSA.JK", "Esa Tebu Energi", "Energy", ("essa", "essa industries")),
    ("BRPT.JK", "Barito Pacific", "Basic Materials", ("brpt", "barito pacific")),
    ("MEDC.JK", "Medco Energi Internasional", "Energy", ("medc", "medco", "medco energi")),
]

STOCK_MASTER = {
    ticker: {"ticker": ticker, "name": name, "sector": sector, "aliases": [a.lower() for a in aliases]}
    for ticker, name, sector, aliases in STOCK_SEED
}

TICKER_EXPOSURE_PROFILES = {
    "BBCA.JK": {"themes": ["BANKING_LIQUIDITY", "HOUSING", "INFRASTRUCTURE", "DIGITAL_PUBLIC"], "keywords": ["bank", "kredit", "mortgage", "transaction", "payment"]},
    "BBRI.JK": {"themes": ["BANKING_LIQUIDITY", "FOOD_SECURITY", "INFRASTRUCTURE"], "keywords": ["micro", "umkm", "kredit", "bank"]},
    "BMRI.JK": {"themes": ["BANKING_LIQUIDITY", "INFRASTRUCTURE", "DOWNSTREAMING"], "keywords": ["corporate", "bank", "loan", "project finance"]},
    "TLKM.JK": {"themes": ["DIGITAL_PUBLIC"], "keywords": ["telecom", "broadband", "data center", "connectivity"]},
    "ASII.JK": {"themes": ["INFRASTRUCTURE", "BANKING_LIQUIDITY", "FOOD_SECURITY"], "keywords": ["automotive", "heavy equipment", "distribution"]},
    "GOTO.JK": {"themes": ["DIGITAL_PUBLIC", "FOOD_SECURITY"], "keywords": ["platform", "digital", "e-commerce", "payments"]},
    "BYAN.JK": {"themes": ["ENERGY_TRANSITION", "TRADE_RESTRICTION"], "keywords": ["coal", "energy", "mining"]},
    "ADRO.JK": {"themes": ["ENERGY_TRANSITION", "DOWNSTREAMING", "TRADE_RESTRICTION"], "keywords": ["coal", "energy", "smelter"]},
    "UNVR.JK": {"themes": ["FOOD_SECURITY", "TRADE_RESTRICTION"], "keywords": ["consumer", "household", "fmcg"]},
    "ICBP.JK": {"themes": ["FOOD_SECURITY", "TRADE_RESTRICTION"], "keywords": ["food", "consumer", "staple"]},
    "PTBA.JK": {"themes": ["ENERGY_TRANSITION", "TRADE_RESTRICTION"], "keywords": ["coal", "energy", "mining"]},
    "ANTM.JK": {"themes": ["DOWNSTREAMING", "ENERGY_TRANSITION", "TRADE_RESTRICTION"], "keywords": ["nickel", "mineral", "smelter", "gold"]},
    "INDF.JK": {"themes": ["FOOD_SECURITY", "TRADE_RESTRICTION"], "keywords": ["food", "staple", "consumer"]},
    "SMGR.JK": {"themes": ["HOUSING", "INFRASTRUCTURE"], "keywords": ["cement", "construction", "building materials"]},
    "KLBF.JK": {"themes": ["FOOD_SECURITY"], "keywords": ["healthcare", "pharma", "nutrition"]},
    "HMSP.JK": {"themes": ["TRADE_RESTRICTION"], "keywords": ["tobacco", "excise", "consumer"]},
    "PGAS.JK": {"themes": ["ENERGY_TRANSITION", "DOWNSTREAMING"], "keywords": ["gas", "pipeline", "energy"]},
    "JSMR.JK": {"themes": ["INFRASTRUCTURE", "HOUSING"], "keywords": ["toll", "road", "traffic"]},
    "EXCL.JK": {"themes": ["DIGITAL_PUBLIC"], "keywords": ["telecom", "connectivity", "broadband"]},
    "INCO.JK": {"themes": ["DOWNSTREAMING", "ENERGY_TRANSITION", "TRADE_RESTRICTION"], "keywords": ["nickel", "mining", "smelter"]},
    "TOWR.JK": {"themes": ["DIGITAL_PUBLIC"], "keywords": ["tower", "telecom", "connectivity"]},
    "MNCN.JK": {"themes": ["DIGITAL_PUBLIC"], "keywords": ["media", "broadcast", "digital"]},
    "ITMG.JK": {"themes": ["ENERGY_TRANSITION", "TRADE_RESTRICTION"], "keywords": ["coal", "energy", "mining"]},
    "HRUM.JK": {"themes": ["DOWNSTREAMING", "ENERGY_TRANSITION", "TRADE_RESTRICTION"], "keywords": ["nickel", "coal", "mining"]},
    "BSDE.JK": {"themes": ["HOUSING", "BANKING_LIQUIDITY", "INFRASTRUCTURE"], "keywords": ["property", "township", "real estate"]},
    "CPIN.JK": {"themes": ["FOOD_SECURITY", "TRADE_RESTRICTION"], "keywords": ["poultry", "feed", "food"]},
    "JPFA.JK": {"themes": ["FOOD_SECURITY", "TRADE_RESTRICTION"], "keywords": ["poultry", "feed", "food"]},
    "ESSA.JK": {"themes": ["ENERGY_TRANSITION", "FOOD_SECURITY"], "keywords": ["ammonia", "fertilizer", "energy"]},
    "BRPT.JK": {"themes": ["ENERGY_TRANSITION", "DOWNSTREAMING", "INFRASTRUCTURE"], "keywords": ["petrochemical", "industrial", "energy"]},
    "MEDC.JK": {"themes": ["ENERGY_TRANSITION", "TRADE_RESTRICTION"], "keywords": ["oil", "gas", "energy"]},
}

SECTOR_KEYWORDS = {
    "Energy": ["energi", "energy", "minyak", "gas", "migas", "bbm", "coal", "batubara", "batu bara", "listrik", "geothermal", "renewable"],
    "Basic Materials": ["tambang", "mineral", "nikel", "nickel", "emas", "smelter", "pupuk", "semen", "copper", "baja", "komoditas", "hilirisasi"],
    "Industrials": ["konstruksi", "proyek", "pengadaan", "manufacturing", "pabrik", "defense", "bandara", "pelabuhan", "jalan", "pembangunan", "procurement"],
    "Consumer Cyclicals": ["otomotif", "ritel", "retail", "pariwisata", "tourism", "hotel", "travel", "mobil", "motor", "consumer cyclicals"],
    "Consumer Non-Cyclicals": ["makanan", "minuman", "food", "staple", "pangan", "poultry", "rokok", "household", "consumer non-cyclicals", "fmcg"],
    "Healthcare": ["kesehatan", "farmasi", "obat", "rumah sakit", "hospital", "healthcare", "medical"],
    "Financials": ["bank", "finansial", "financial", "kredit", "suku bunga", "bi rate", "pinjaman", "financing", "loan"],
    "Properties & Real Estate": ["properti", "property", "real estate", "perumahan", "housing", "apartemen", "realty", "estate"],
    "Technology": ["digital", "teknologi", "technology", "telekomunikasi", "telecom", "data center", "e-commerce", "internet", "platform", "cloud"],
    "Infrastructures": ["infrastruktur", "infrastructure", "transport", "tol", "jalan tol", "logistik", "logistics", "public works", "pelabuhan", "airport", "bandara"],
    "Transportation & Logistics": ["transportasi", "transportation", "logistik", "logistics", "penerbangan", "airline", "shipping", "cargo", "freight", "distribution"],
}

CATEGORY_RULES = {
    "CABINET_RESHUFFLE": ["reshuffle", "perombakan", "kabinet", "pelantikan", "dismissal", "dicopot", "diganti", "appoint"],
    "REGULATION_NEW": ["uu", "ruu", "perpres", "perppu", "permen", "new regulation", "regulation", "kebijakan baru", "peraturan baru", "disahkan"],
    "REGULATION_REPEAL": ["dicabut", "dibatalkan", "repeal", "revoked", "withdrawn", "dihapus", "annul"],
    "ELECTION_EVENT": ["pemilu", "pilkada", "kampanye", "election", "voting", "results", "hasil pemilu"],
    "CORRUPTION_CASE": ["kpk", "korupsi", "suap", "ott", "tersangka", "vonis", "penangkapan", "arrest", "corruption"],
    "STATE_BUDGET": ["apbn", "anggaran", "budget", "fiscal", "defisit", "belanja negara", "state budget"],
    "TRADE_POLICY": ["ekspor", "impor", "tarif", "bea masuk", "larangan ekspor", "kuota", "trade policy", "trade"],
    "ENERGY_POLICY": ["batubara", "batu bara", "coal", "oil", "gas", "energi", "migas", "quota", "renewable", "energy policy", "hilirisasi"],
    "INVESTMENT_POLICY": ["investasi", "fdi", "investment", "bkpm", "hilirisasi", "izin investasi", "omnibus"],
    "MONETARY_SIGNAL": ["bank indonesia", "bi rate", "suku bunga", "inflasi", "monetary", "rupiah", "rate decision"],
    "PARLIAMENT_SESSION": ["dpr", "komisi", "sidang", "hearing", "rapat kerja", "parlemen", "committee"],
    "PROTEST_UNREST": ["demo", "demonstrasi", "unjuk rasa", "strike", "mogok", "protest", "riot", "unrest"],
}

CATEGORY_TO_SECTORS = {
    "CABINET_RESHUFFLE": SECTORS,
    "REGULATION_NEW": ["Industrials", "Financials", "Energy", "Basic Materials", "Properties & Real Estate", "Technology"],
    "REGULATION_REPEAL": ["Industrials", "Financials", "Energy", "Basic Materials", "Properties & Real Estate", "Technology"],
    "ELECTION_EVENT": SECTORS,
    "CORRUPTION_CASE": ["Financials", "Industrials", "Basic Materials", "Energy", "Properties & Real Estate"],
    "STATE_BUDGET": ["Financials", "Industrials", "Infrastructures", "Properties & Real Estate"],
    "TRADE_POLICY": ["Consumer Cyclicals", "Consumer Non-Cyclicals", "Basic Materials", "Industrials"],
    "ENERGY_POLICY": ["Energy", "Basic Materials", "Industrials"],
    "INVESTMENT_POLICY": ["Financials", "Industrials", "Properties & Real Estate", "Technology"],
    "MONETARY_SIGNAL": ["Financials", "Properties & Real Estate", "Consumer Cyclicals"],
    "PARLIAMENT_SESSION": ["Financials", "Industrials", "Energy", "Basic Materials"],
    "PROTEST_UNREST": ["Consumer Cyclicals", "Industrials", "Infrastructures", "Transportation & Logistics"],
}

POLICY_THEMES = {
    "HOUSING": {
        "keywords": ["housing", "perumahan", "rumah subsidi", "subsidi rumah", "properti", "mortgage", "kpr", "apartemen"],
        "sectors": ["Properties & Real Estate", "Basic Materials", "Financials", "Industrials"],
        "channel": "housing demand, mortgage flows, and building-material execution",
        "exposure_type": "demand",
    },
    "INFRASTRUCTURE": {
        "keywords": ["infrastruktur", "jalan tol", "public works", "pelabuhan", "bandara", "logistik", "proyek", "construction"],
        "sectors": ["Infrastructures", "Industrials", "Basic Materials", "Transportation & Logistics", "Financials"],
        "channel": "project execution, traffic growth, and construction-material demand",
        "exposure_type": "project",
    },
    "FOOD_SECURITY": {
        "keywords": ["pangan", "food security", "beras", "gula", "ayam", "poultry", "feed", "staple", "fertilizer"],
        "sectors": ["Consumer Non-Cyclicals", "Transportation & Logistics", "Basic Materials"],
        "channel": "staple demand, agricultural inputs, and distribution volumes",
        "exposure_type": "supply_chain",
    },
    "ENERGY_TRANSITION": {
        "keywords": ["energi", "oil", "gas", "renewable", "listrik", "bbm", "migas", "geothermal", "quota"],
        "sectors": ["Energy", "Industrials", "Basic Materials"],
        "channel": "energy pricing, quotas, and upstream/downstream project economics",
        "exposure_type": "regulatory",
    },
    "DOWNSTREAMING": {
        "keywords": ["hilirisasi", "smelter", "nikel", "nickel", "mineral", "downstreaming", "refinery"],
        "sectors": ["Basic Materials", "Energy", "Industrials", "Infrastructures"],
        "channel": "mineral processing, smelter buildout, and industrial-estate utilization",
        "exposure_type": "asset",
    },
    "BANKING_LIQUIDITY": {
        "keywords": ["bank indonesia", "bi rate", "suku bunga", "likuiditas", "kredit", "loan", "pinjaman", "inflasi"],
        "sectors": ["Financials", "Properties & Real Estate", "Consumer Cyclicals"],
        "channel": "funding costs, credit demand, and financing activity",
        "exposure_type": "financing",
    },
    "DIGITAL_PUBLIC": {
        "keywords": ["digital", "e-government", "telecom", "data center", "cloud", "internet", "platform"],
        "sectors": ["Technology", "Infrastructures", "Financials"],
        "channel": "digital infrastructure demand and public-service digitization spend",
        "exposure_type": "demand",
    },
    "DEFENSE_PROCUREMENT": {
        "keywords": ["defense", "pertahanan", "militer", "procurement", "pengadaan", "strategis"],
        "sectors": ["Industrials", "Transportation & Logistics", "Basic Materials", "Financials"],
        "channel": "state procurement, logistics support, and strategic industrial demand",
        "exposure_type": "procurement",
    },
    "TRADE_RESTRICTION": {
        "keywords": ["ekspor", "impor", "tarif", "bea masuk", "kuota", "larangan ekspor", "trade"],
        "sectors": ["Basic Materials", "Consumer Non-Cyclicals", "Industrials", "Energy"],
        "channel": "price realization, volume restrictions, and import-substitution effects",
        "exposure_type": "regulatory",
    },
}

MIN_RELATIONSHIP_SCORE = 3.0
MIN_EVIDENCE_QUALITY = 2.0

NEWS_SOURCES = [
    {"name": "Antara Terkini", "url": "https://www.antaranews.com/rss/terkini.xml", "kind": "rss", "weight": 1.0},
    {"name": "Antara Top News", "url": "https://www.antaranews.com/rss/top-news.xml", "kind": "rss", "weight": 0.95},
    {"name": "Antara Ekonomi Bursa", "url": "https://www.antaranews.com/rss/ekonomi-bursa.xml", "kind": "rss", "weight": 0.95},
    {"name": "CNBC Indonesia", "url": "https://www.cnbcindonesia.com/rss", "kind": "rss", "weight": 0.9},
    {"name": "CNN Indonesia Nasional", "url": "https://www.cnnindonesia.com/nasional/rss", "kind": "rss", "weight": 0.85},
    {"name": "CNN Indonesia Ekonomi", "url": "https://www.cnnindonesia.com/ekonomi/rss", "kind": "rss", "weight": 0.85},
    {"name": "Detik Finance", "url": "https://finance.detik.com/rss", "kind": "rss", "weight": 0.8},
    {"name": "Sekretariat Kabinet", "url": "https://setkab.go.id", "kind": "html", "weight": 1.0},
    {"name": "OJK", "url": "https://www.ojk.go.id", "kind": "html", "weight": 0.9},
    {"name": "KPK", "url": "https://www.kpk.go.id/id/berita/siaran-pers", "kind": "html", "weight": 0.9},
]

app = FastAPI(title=APP_TITLE, version="1.0.0")


WATCHLIST_LOCK = threading.Lock()
CACHE_LOCK = threading.Lock()
WATCHLIST_STATE = list(DEFAULT_WATCHLIST)
CACHE: dict[str, Any] = {}
COMPANY_KNOWLEDGE: dict[str, dict[str, Any]] = {}
POLICY_SIGNAL_RULES: dict[str, Any] = {}
MARKET_VALIDATION_CONFIG: dict[str, Any] = {}
SOURCE_REGISTRY: dict[str, Any] = {}


class RefreshRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)
    force: bool = False
    window: str = DEFAULT_EVENT_WINDOW


class WatchlistRequest(BaseModel):
    tickers: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def score_political_relevance(article: dict[str, Any]) -> dict[str, Any]:
    text = article_text(article)
    rules = POLICY_SIGNAL_RULES or load_policy_signal_rules()
    relevance_rules = rules.get("political_relevance", {}) if isinstance(rules, dict) else {}
    institution_terms = relevance_rules.get("institution_terms", [])
    legal_terms = relevance_rules.get("legal_terms", [])
    action_terms = relevance_rules.get("action_terms", [])
    weak_context_terms = relevance_rules.get("weak_context_terms", [])
    non_political_terms = relevance_rules.get("non_political_terms", [])

    institution_hits = [term for term in institution_terms if term in text]
    legal_hits = [term for term in legal_terms if term in text]
    action_hits = [term for term in action_terms if term in text]
    weak_hits = [term for term in weak_context_terms if term in text]
    non_political_hits = [term for term in non_political_terms if term in text]
    keyword_hits = [keyword for keyword in POLITICAL_SIGNAL_KEYWORDS if keyword in text]

    score = 0.0
    score += min(0.45, 0.14 * len(institution_hits))
    score += min(0.3, 0.12 * len(legal_hits))
    score += min(0.2, 0.08 * len(action_hits))
    if article.get("source_type") in {"government", "regulator"}:
        score += 0.12
    if keyword_hits:
        score += min(0.15, 0.03 * len(keyword_hits))
    score -= min(0.2, 0.06 * len(weak_hits))
    score -= min(0.45, 0.18 * len(non_political_hits))
    score = clamp(score, 0.0, 1.0)

    if score >= 0.6:
        label = "political"
    elif score >= 0.3:
        label = "maybe"
    else:
        label = "not_political"

    return {
        "relevance_score": round(score, 3),
        "relevance_label": label,
        "relevance_signals": {
            "institutions": institution_hits[:5],
            "legal": legal_hits[:5],
            "actions": action_hits[:5],
            "keyword_hits": keyword_hits[:8],
        },
        "relevance_penalties": {
            "weak_context": weak_hits[:5],
            "non_political": non_political_hits[:5],
        },
    }


def detect_event_stage(text: str) -> dict[str, Any]:
    text = str(text or "").lower()
    rules = (POLICY_SIGNAL_RULES or load_policy_signal_rules()).get("event_stage_rules", {})
    priority = ["revoked", "delayed", "effective", "approved", "enforced", "proposal", "debate"]
    hits_map = {stage: [term for term in rules.get(stage, []) if term in text] for stage in priority}
    for stage in priority:
        hits = hits_map.get(stage, [])
        if hits:
            confidence = clamp(0.45 + 0.12 * len(hits), 0.0, 1.0)
            return {"event_stage": stage, "event_stage_confidence": round(confidence, 3), "event_stage_signals": hits[:5]}
    return {"event_stage": "unspecified", "event_stage_confidence": 0.25, "event_stage_signals": []}


def detect_negation_or_reversal(text: str) -> dict[str, Any]:
    text = str(text or "").lower()
    rules = POLICY_SIGNAL_RULES or load_policy_signal_rules()
    negation_hits = [term for term in rules.get("negation_terms", []) if term in text]
    reversal_hits = [term for term in rules.get("reversal_terms", []) if term in text]
    return {
        "negation_hits": negation_hits[:5],
        "reversal_hits": reversal_hits[:5],
        "is_reversal": bool(reversal_hits),
        "is_tentative": any(term in text for term in ["wacana", "usulan", "rencana", "berencana", "kajian"]),
    }


def is_relevant_article(article: dict[str, Any]) -> bool:
    relevance = score_political_relevance(article)
    return relevance.get("relevance_label") == "political"


def source_weight(source_name: str) -> float:
    for source in NEWS_SOURCES:
        if source["name"].lower() == source_name.lower():
            return float(source["weight"])
    return 0.7


def infer_source_type(source_name: str = "", url: str = "") -> str:
    source_name = (source_name or "").lower()
    url = (url or "").lower()
    if any(token in source_name or token in url for token in ["ojk", "kpk", "bank indonesia", "bi.go.id"]):
        return "regulator"
    if "idx.co.id" in url or any(token in source_name or token in url for token in ["investor", "/ir/", "annualreport", "sustainability-report", "corporate action"]):
        return "company"
    if any(token in source_name or token in url for token in ["setkab", "sekretariat kabinet", "presiden.go.id", ".go.id"]):
        return "government"
    if "finance.yahoo.com" in url or "profile" in url:
        return "profile"
    if any(token in source_name or token in url for token in ["antara", "cnbc", "cnn", "detik", "kompas", "tempo", "beritasatu"]):
        return "media"
    return "other"


def normalize_domain(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        value = urlsplit(value).netloc or value
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if value.startswith("www."):
        value = value[4:]
    return value


def canonicalize_article_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = re.sub(r"/amp/?$", "", parsed.path or "", flags=re.I)
    path = re.sub(r"/{2,}", "/", path)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if path == "/":
        path = ""
    return urlunsplit((scheme, netloc, path, "", ""))


def canonical_source_key(article: dict[str, Any]) -> str:
    profile = article.get("source_profile", {}) if isinstance(article.get("source_profile", {}), dict) else {}
    candidates = [
        str(profile.get("duplicate_grouping") or "").strip().lower(),
        normalize_domain(str(profile.get("canonical_domain") or "")),
        normalize_domain(str(article.get("canonical_domain") or "")),
        normalize_domain(canonicalize_article_url(str(article.get("url") or ""))),
        normalize_match_text(article.get("source", "")),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return "unknown"


def claim_signature(article: dict[str, Any]) -> str:
    headline = normalize_match_text(article.get("headline", ""))
    summary = normalize_match_text(article.get("summary", ""))
    entities = normalize_match_text(" ".join(str(item) for item in article.get("entities", []) if str(item).strip()))
    text_bits = [bit for bit in [headline, summary, entities] if bit]
    if not text_bits:
        return canonical_source_key(article)
    signature = "::".join(text_bits[:3])
    return signature[:320]


def _article_merge_priority(article: dict[str, Any]) -> tuple[float, int, float, datetime]:
    published_at = article.get("published_at") if isinstance(article.get("published_at"), datetime) else now_wib()
    try:
        quality_score = float(article.get("source_quality_score", 0.0))
    except Exception:
        quality_score = 0.0
    try:
        tier = int(article.get("source_tier", 4) or 4)
    except Exception:
        tier = 4
    try:
        source_weight = float(article.get("source_weight", 0.0))
    except Exception:
        source_weight = 0.0
    return (quality_score, -tier, source_weight, published_at)


def merge_duplicate_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for article in articles:
        candidate = dict(article)
        metadata = source_metadata_for(str(candidate.get("source") or ""), str(candidate.get("url") or ""))
        candidate.setdefault("source_profile", metadata.get("source_profile", {}))
        candidate.setdefault("source_type", metadata.get("source_type", "other"))
        candidate.setdefault("source_tier", metadata.get("source_tier", 4))
        candidate.setdefault("canonical_domain", metadata.get("canonical_domain", ""))
        candidate.setdefault("source_quality_score", metadata.get("source_quality_score", 0.0))
        candidate["canonical_url"] = canonicalize_article_url(str(candidate.get("url") or ""))
        candidate["claim_signature"] = claim_signature(candidate)
        matched_group = None
        for group in groups:
            exemplar = group[0]
            if candidate["canonical_url"] and candidate["canonical_url"] == exemplar.get("canonical_url"):
                matched_group = group
                break
            if candidate["claim_signature"] == exemplar.get("claim_signature"):
                matched_group = group
                break
            if text_similarity(candidate.get("headline", ""), exemplar.get("headline", "")) >= 0.92:
                if text_similarity(candidate.get("summary", ""), exemplar.get("summary", "")) >= 0.84 or candidate["canonical_url"] == exemplar.get("canonical_url"):
                    matched_group = group
                    break
        if matched_group is None:
            groups.append([candidate])
        else:
            matched_group.append(candidate)

    merged_articles: list[dict[str, Any]] = []
    for group in groups:
        group.sort(key=_article_merge_priority, reverse=True)
        canonical = dict(group[0])
        canonical_url = canonical.get("canonical_url") or canonicalize_article_url(str(canonical.get("url") or ""))
        source_names: list[str] = []
        source_urls: list[str] = []
        source_types: list[str] = []
        for article in group:
            source_name = str(article.get("source") or "").strip()
            if source_name and source_name not in source_names:
                source_names.append(source_name)
            normalized_url = canonicalize_article_url(str(article.get("url") or ""))
            if normalized_url and normalized_url not in source_urls:
                source_urls.append(normalized_url)
            source_type = str(article.get("source_type") or "").strip()
            if source_type and source_type not in source_types:
                source_types.append(source_type)

        latest_published_at = max(
            [article.get("published_at") for article in group if isinstance(article.get("published_at"), datetime)],
            default=canonical.get("published_at") or now_wib(),
        )
        alternate_urls = [url for url in source_urls if url != canonical_url]

        canonical.update(
            {
                "url": canonical_url or canonical.get("url", ""),
                "canonical_url": canonical_url,
                "duplicate_group_id": canonical.get("claim_signature") or claim_signature(canonical),
                "duplicate_count": len(group),
                "source_names": source_names,
                "source_urls": source_urls,
                "source_types": source_types,
                "alternate_urls": alternate_urls,
                "latest_published_at": latest_published_at,
            }
        )
        canonical.update(source_quality_metrics_for_article(canonical))
        merged_articles.append(canonical)

    merged_articles.sort(key=lambda article: (article.get("published_at") or now_wib(), _article_merge_priority(article)), reverse=True)
    return merged_articles


def _source_registry_defaults() -> dict[str, Any]:
    return {"sources": [], "by_name": {}, "by_domain": {}, "by_canonical_domain": {}}


def normalize_source_registry(raw: Any) -> dict[str, Any]:
    records = raw.get("sources", []) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        return _source_registry_defaults()

    normalized_sources: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    by_domain: dict[str, dict[str, Any]] = {}

    for record in records:
        if not isinstance(record, dict):
            continue
        canonical_name = str(record.get("name", "")).strip()
        if not canonical_name:
            continue

        aliases = [str(item).strip() for item in record.get("aliases", []) if str(item).strip()]
        domains = [normalize_domain(item) for item in record.get("domains", []) if normalize_domain(item)]
        raw_canonical_domain = str(record.get("canonical_domain", "")).strip().lower()
        canonical_domain = normalize_domain(raw_canonical_domain)
        if canonical_domain and canonical_domain not in domains:
            domains.append(canonical_domain)
        if not raw_canonical_domain and domains:
            raw_canonical_domain = domains[0]
        display_canonical_domain = raw_canonical_domain or canonical_domain

        source_type = str(record.get("source_type") or infer_source_type(canonical_name, canonical_domain)).strip().lower() or "other"
        try:
            tier = int(record.get("tier", 4))
        except Exception:
            tier = 4
        tier = max(1, min(4, tier))
        try:
            trust_weight = float(record.get("trust_weight", 0.5))
        except Exception:
            trust_weight = 0.5
        trust_weight = clamp(trust_weight, 0.0, 1.0)

        country_focus = str(record.get("country_focus", "mixed")).strip().lower() or "mixed"
        notes = str(record.get("notes", "")).strip()
        duplicate_grouping = str(record.get("duplicate_grouping") or canonical_domain or canonical_name).strip().lower()
        if not duplicate_grouping:
            duplicate_grouping = normalize_match_text(canonical_name)

        profile = {
            **record,
            "name": canonical_name,
            "canonical_name": canonical_name,
            "aliases": aliases,
            "domains": domains,
            "canonical_domain": display_canonical_domain,
            "source_type": source_type,
            "tier": tier,
            "trust_weight": trust_weight,
            "country_focus": country_focus,
            "notes": notes,
            "duplicate_grouping": duplicate_grouping,
        }

        normalized_sources.append(profile)

        name_keys = {normalize_match_text(canonical_name), normalize_match_text(profile.get("canonical_name", canonical_name))}
        name_keys.update(normalize_match_text(alias) for alias in aliases)
        for key in {key for key in name_keys if key}:
            by_name[key] = profile

        for domain in domains:
            by_domain[domain] = profile

    return {
        "sources": normalized_sources,
        "by_name": by_name,
        "by_domain": by_domain,
        "by_canonical_domain": dict(by_domain),
    }


def load_source_registry() -> dict[str, Any]:
    if not SOURCE_REGISTRY_FILE.exists():
        return _source_registry_defaults()
    try:
        raw = json.loads(SOURCE_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _source_registry_defaults()
    return normalize_source_registry(raw)


def _fallback_source_profile(name: str = "", url: str = "") -> dict[str, Any]:
    parsed = urlsplit(url or "")
    domain = normalize_domain(parsed.netloc or (url if "." in url and "/" not in url else ""))
    source_type = infer_source_type(name, url)
    canonical_name = name.strip() or domain or "Unknown source"
    return {
        "name": canonical_name,
        "canonical_name": canonical_name,
        "aliases": [],
        "domains": [domain] if domain else [],
        "canonical_domain": domain,
        "source_type": source_type,
        "tier": 4,
        "trust_weight": 0.5,
        "country_focus": "mixed",
        "notes": "Fallback profile inferred from source name or URL.",
        "duplicate_grouping": normalize_match_text(canonical_name) or domain or "unknown",
    }


def source_profile_for_domain(domain: str) -> dict[str, Any]:
    normalized_domain = normalize_domain(domain)
    if not normalized_domain:
        return _fallback_source_profile(url=domain)
    registry = SOURCE_REGISTRY or load_source_registry()
    profile = registry.get("by_domain", {}).get(normalized_domain)
    if profile:
        return dict(profile)
    return _fallback_source_profile(url=normalized_domain)


def source_profile_for_name(name: str) -> dict[str, Any]:
    normalized_name = normalize_match_text(name)
    registry = SOURCE_REGISTRY or load_source_registry()
    profile = registry.get("by_name", {}).get(normalized_name)
    if profile:
        return dict(profile)
    if name and "://" in name:
        return source_profile_for_url(name)
    return _fallback_source_profile(name=name)


def source_profile_for_url(url: str) -> dict[str, Any]:
    parsed = urlsplit(url or "")
    domain = parsed.netloc or (url if "." in url and "/" not in url else "")
    if domain:
        registry = SOURCE_REGISTRY or load_source_registry()
        profile = registry.get("by_domain", {}).get(normalize_domain(domain))
        if profile:
            return dict(profile)
    return _fallback_source_profile(url=url)


def source_profile_resolution(source_name: str = "", url: str = "") -> tuple[dict[str, Any], str]:
    registry = SOURCE_REGISTRY or load_source_registry()
    normalized_name = normalize_match_text(source_name)
    if normalized_name:
        profile = registry.get("by_name", {}).get(normalized_name)
        if profile:
            return dict(profile), "registry_name"

    parsed = urlsplit(url or "")
    domain = parsed.netloc or (url if "." in url and "/" not in url else "")
    normalized_domain = normalize_domain(domain)
    if normalized_domain:
        profile = registry.get("by_domain", {}).get(normalized_domain)
        if profile:
            return dict(profile), "registry_domain"

    if source_name and "://" in source_name:
        return _fallback_source_profile(url=source_name), "inferred_fallback"
    if source_name or url:
        return _fallback_source_profile(name=source_name, url=url), "inferred_fallback"
    return _fallback_source_profile(), "inferred_fallback"


def source_quality_score_for_profile(profile: dict[str, Any]) -> float:
    try:
        tier = int(profile.get("tier", 4))
    except Exception:
        tier = 4
    tier = max(1, min(4, tier))
    try:
        trust_weight = float(profile.get("trust_weight", 0.5))
    except Exception:
        trust_weight = 0.5
    tier_factor = clamp((5 - tier) / 4.0, 0.25, 1.0)
    return round(clamp(trust_weight, 0.0, 1.0) * tier_factor, 3)


def source_freshness_score(published_at: datetime | None, source_profile: dict[str, Any] | None = None) -> float:
    if not isinstance(published_at, datetime):
        return 0.5
    profile = source_profile or {}
    source_type = str(profile.get("source_type") or "other")
    age_hours = max(0.0, (now_wib() - published_at).total_seconds() / 3600.0)
    half_life = profile.get("freshness_half_life_hours", 0.0)
    try:
        half_life = float(half_life)
    except Exception:
        half_life = 0.0
    if half_life <= 0:
        half_life = {
            "government": 120.0,
            "regulator": 96.0,
            "company": 84.0,
            "media": 48.0,
            "profile": 36.0,
            "other": 24.0,
        }.get(source_type, 36.0)
    decay = 0.5 ** (age_hours / max(half_life, 1.0))
    floor = {
        "government": 0.35,
        "regulator": 0.3,
        "company": 0.25,
        "media": 0.15,
        "profile": 0.12,
        "other": 0.1,
    }.get(source_type, 0.15)
    if age_hours <= 0:
        return 1.0
    return round(clamp(decay, floor, 1.0), 3)


def source_quality_metrics_for_article(article: dict[str, Any]) -> dict[str, Any]:
    profile = article.get("source_profile", {}) if isinstance(article.get("source_profile", {}), dict) else {}
    if not profile:
        profile = source_metadata_for(str(article.get("source") or ""), str(article.get("url") or "")).get("source_profile", {})
    published_at = article.get("latest_published_at") if isinstance(article.get("latest_published_at"), datetime) else article.get("published_at")
    source_type = str(article.get("source_type") or profile.get("source_type") or infer_source_type(str(article.get("source") or ""), str(article.get("url") or "")))
    base_quality = float(article.get("source_quality_score", source_quality_score_for_profile(profile)) or 0.0)
    freshness = source_freshness_score(published_at if isinstance(published_at, datetime) else None, profile)
    try:
        duplicate_count = max(1, int(article.get("duplicate_count", 1) or 1))
    except Exception:
        duplicate_count = 1
    duplicate_penalty = 1.0 / (1.0 + 0.22 * max(0, duplicate_count - 1))
    relevance_score = float(article.get("relevance_score", 0.0) or 0.0)
    relevance_label = str(article.get("relevance_label", "") or "")
    event_stage = str(article.get("event_stage", "") or "")
    direct_language_bonus = 1.0
    if source_type in {"government", "regulator", "company"} and relevance_score >= 0.5 and relevance_label != "not_political":
        direct_language_bonus += 0.08
    if event_stage in {"approved", "effective", "enforced", "revoked"}:
        direct_language_bonus += 0.05
    if source_type in {"media", "profile", "other"} and relevance_label in {"maybe", "not_political"}:
        direct_language_bonus -= 0.08
    source_quality = clamp(base_quality * (0.55 + 0.45 * freshness) * duplicate_penalty * direct_language_bonus, 0.0, 1.0)
    age_hours = 0.0
    if isinstance(published_at, datetime):
        age_hours = max(0.0, (now_wib() - published_at).total_seconds() / 3600.0)
    coverage_warning = ""
    if freshness < 0.28:
        coverage_warning = "stale_coverage"
    elif duplicate_count > 1 and source_quality < 0.65:
        coverage_warning = "duplicated_coverage"
    elif source_quality < 0.35:
        coverage_warning = "thin_source_coverage"
    return {
        "source_age_hours": round(age_hours, 1),
        "source_freshness_score": round(freshness, 3),
        "source_quality_score": round(source_quality, 3),
        "source_tier": int(article.get("source_tier", profile.get("tier", 4)) or 4),
        "coverage_warning": coverage_warning,
    }


def corroboration_family_key(profile: dict[str, Any], source_name: str = "", url: str = "") -> str:
    candidates = [
        normalize_match_text(str(profile.get("syndication_group") or "")),
        normalize_match_text(str(profile.get("duplicate_grouping") or "")),
        normalize_domain(str(profile.get("canonical_domain") or "")),
        normalize_domain(canonicalize_article_url(url)),
        normalize_match_text(source_name),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return "unknown"


def corroboration_domain_key(profile: dict[str, Any], source_name: str = "", url: str = "") -> str:
    syndication_group = normalize_match_text(str(profile.get("syndication_group") or ""))
    if syndication_group:
        return syndication_group
    domain = normalize_domain(str(profile.get("canonical_domain") or "")) or normalize_domain(canonicalize_article_url(url))
    if domain:
        return domain
    return corroboration_family_key(profile, source_name, url)


def corroboration_coverage_items(article: dict[str, Any]) -> list[dict[str, Any]]:
    profile = article.get("source_profile", {}) if isinstance(article.get("source_profile", {}), dict) else {}
    source_type = str(article.get("source_type") or profile.get("source_type") or infer_source_type(str(article.get("source") or ""), str(article.get("url") or "")))
    try:
        duplicate_count = max(1, int(article.get("duplicate_count", 1) or 1))
    except Exception:
        duplicate_count = 1

    source_names = [str(item).strip() for item in article.get("source_names", []) if str(item).strip()] if isinstance(article.get("source_names", []), list) else []
    source_urls = [str(item).strip() for item in article.get("source_urls", []) if str(item).strip()] if isinstance(article.get("source_urls", []), list) else []
    source_types = [str(item).strip().lower() for item in article.get("source_types", []) if str(item).strip()] if isinstance(article.get("source_types", []), list) else []
    if not source_names and str(article.get("source") or "").strip():
        source_names = [str(article.get("source") or "").strip()]
    if not source_urls and str(article.get("url") or "").strip():
        source_urls = [canonicalize_article_url(str(article.get("url") or "")) or str(article.get("url") or "").strip()]
    if not source_types and source_type:
        source_types = [source_type]

    item_count = max(duplicate_count, len(source_names), len(source_urls), len(source_types), 1)
    items: list[dict[str, Any]] = []
    default_quality = float(article.get("source_quality_score", source_quality_score_for_profile(profile)) or 0.0)
    for idx in range(item_count):
        item_source_name = source_names[idx] if idx < len(source_names) else (source_names[-1] if source_names else str(article.get("source") or "").strip())
        item_url = source_urls[idx] if idx < len(source_urls) else (source_urls[-1] if source_urls else str(article.get("url") or "").strip())
        metadata = source_metadata_for(item_source_name, item_url)
        item_profile = metadata.get("source_profile", {}) if isinstance(metadata.get("source_profile", {}), dict) else {}
        item_source_type = source_types[idx] if idx < len(source_types) else str(metadata.get("source_type") or source_type or "other")
        try:
            item_source_tier = int(metadata.get("source_tier", item_profile.get("tier", article.get("source_tier", 4))) or 4)
        except Exception:
            item_source_tier = 4
        item_quality = float(metadata.get("source_quality_score", default_quality) or default_quality)
        family_key = corroboration_family_key(item_profile, item_source_name, item_url)
        items.append(
            {
                "source_name": item_source_name,
                "url": item_url,
                "source_type": item_source_type,
                "source_tier": item_source_tier,
                "source_quality_score": item_quality,
                "family_key": family_key,
                "domain_key": corroboration_domain_key(item_profile, item_source_name, item_url),
            }
        )
    return items


def source_corroboration_metrics_for_article(article: dict[str, Any]) -> dict[str, Any]:
    profile = article.get("source_profile", {}) if isinstance(article.get("source_profile", {}), dict) else {}
    if not profile:
        profile = source_metadata_for(str(article.get("source") or ""), str(article.get("url") or "")).get("source_profile", {})
    source_type = str(article.get("source_type") or profile.get("source_type") or infer_source_type(str(article.get("source") or ""), str(article.get("url") or "")))
    try:
        source_tier = int(article.get("source_tier", profile.get("tier", 4)) or 4)
    except Exception:
        source_tier = 4

    coverage_items = corroboration_coverage_items(article)
    raw_coverage_count = max(1, len(coverage_items))
    unique_families = {str(item.get("family_key") or "").strip() for item in coverage_items if str(item.get("family_key") or "").strip()}
    unique_domains = {str(item.get("domain_key") or "").strip() for item in coverage_items if str(item.get("domain_key") or "").strip()}
    independent_source_count = max(1, len(unique_families))
    independent_domain_count = max(1, len(unique_domains))
    syndicated_coverage_count = max(0, raw_coverage_count - independent_source_count)
    source_type_count = max(1, len({str(item.get("source_type") or "").strip().lower() for item in coverage_items if str(item.get("source_type") or "").strip()}))
    source_quality = float(article.get("source_quality_score", source_quality_score_for_profile(profile)) or 0.0)
    official_source = source_type in {"government", "regulator", "company"} or source_tier <= 2

    corroboration_agreement_score = clamp(
        0.45 + 0.2 * max(0, independent_source_count - 1) + 0.2 * max(0, independent_domain_count - 1) + 0.15 * max(0, source_type_count - 1),
        0.0,
        1.0,
    )
    corroboration_multiplier = clamp(
        1.0
        + 0.12 * max(0, independent_source_count - 1)
        + 0.14 * max(0, independent_domain_count - 1)
        + 0.05 * max(0, source_type_count - 1)
        + (0.05 if official_source else 0.0),
        1.0,
        1.35,
    )

    if official_source and independent_source_count <= 1:
        corroboration_label = "official_source"
    elif independent_domain_count > 1 and independent_source_count > 1:
        corroboration_label = "independently_corroborated"
    elif independent_source_count > 1:
        corroboration_label = "corroborated"
    elif source_quality < 0.4 or source_tier >= 4:
        corroboration_label = "single_weak_source"
    else:
        corroboration_label = "single_source"

    return {
        "source_tier": source_tier,
        "raw_coverage_count": raw_coverage_count,
        "independent_coverage_count": independent_source_count,
        "syndicated_coverage_count": syndicated_coverage_count,
        "independent_domain_count": independent_domain_count,
        "corroboration_source_count": independent_source_count,
        "corroboration_domain_count": independent_domain_count,
        "corroboration_source_type_count": source_type_count,
        "corroboration_agreement_score": round(corroboration_agreement_score, 3),
        "corroboration_multiplier": round(corroboration_multiplier, 3),
        "corroboration_label": corroboration_label,
    }


def source_metadata_for(source_name: str = "", url: str = "") -> dict[str, Any]:
    profile, resolution_method = source_profile_resolution(source_name, url)
    if profile.get("canonical_domain") and not url:
        profile = source_profile_for_domain(str(profile.get("canonical_domain", "")))
    return {
        "source_profile": profile,
        "source_type": profile.get("source_type", infer_source_type(source_name, url)),
        "source_tier": int(profile.get("tier", 4) or 4),
        "canonical_domain": profile.get("canonical_domain", ""),
        "source_quality_score": source_quality_score_for_profile(profile),
        "source_profile_resolution": resolution_method,
        "used_registry_profile": resolution_method.startswith("registry_"),
    }


def source_type_rank(source_type: str | None) -> float:
    return float(SOURCE_TYPE_RANKS.get(str(source_type or "other"), SOURCE_TYPE_RANKS["other"]))


def normalize_watchlist_values(raw: Any) -> list[str]:
    values: list[str] = []
    if isinstance(raw, dict):
        raw = raw.get("tickers", [])
    if isinstance(raw, list):
        seen: set[str] = set()
        for ticker in raw:
            normalized_ticker = normalize_ticker(str(ticker))
            if normalized_ticker and normalized_ticker not in seen:
                seen.add(normalized_ticker)
                values.append(normalized_ticker)
    return values


def normalize_company_knowledge(raw: Any) -> dict[str, dict[str, Any]]:
    records = raw.get("companies", []) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        ticker = normalize_ticker(str(record.get("ticker", "")))
        policy_exposures = [str(item).strip() for item in record.get("policy_exposures", []) if str(item).strip()]
        policy_channels = [str(item).strip() for item in record.get("policy_channels", []) if str(item).strip()]
        evidence = []
        for item in record.get("evidence", []):
            if not isinstance(item, dict) or not str(item.get("url", "")).startswith(("http://", "https://")):
                continue
            source_type = str(item.get("source_type") or infer_source_type(str(item.get("label", "")), str(item.get("url", ""))))
            evidence.append(
                {
                    **item,
                    "source_type": source_type,
                    "source_date": str(item.get("source_date") or "").strip() or None,
                    "quality_rank": round(source_type_rank(source_type), 2),
                }
            )
        if not ticker or not policy_exposures or not policy_channels or not evidence:
            continue
        policy_channel_details = []
        for item in record.get("policy_channel_details", []):
            if not isinstance(item, dict):
                continue
            channel = str(item.get("channel", "")).strip()
            if not channel:
                continue
            keywords = [normalize_match_text(keyword) for keyword in item.get("keywords", []) if normalize_match_text(keyword)]
            direction_map_raw = item.get("direction_map", {}) if isinstance(item.get("direction_map"), dict) else {}
            direction_map = {
                str(key).strip().lower(): [normalize_match_text(token) for token in value if normalize_match_text(token)]
                for key, value in direction_map_raw.items()
                if isinstance(value, list)
            }
            policy_channel_details.append(
                {
                    "channel": channel,
                    "keywords": keywords,
                    "confidence": clamp(float(item.get("confidence", 0.5)), 0.0, 1.0),
                    "direction_map": direction_map,
                }
            )
        exposure_factors_raw = record.get("exposure_factors", {}) if isinstance(record.get("exposure_factors"), dict) else {}
        exposure_factors = {
            "revenue_exposure": [str(item).strip() for item in exposure_factors_raw.get("revenue_exposure", []) if str(item).strip()],
            "input_cost_exposure": [str(item).strip() for item in exposure_factors_raw.get("input_cost_exposure", []) if str(item).strip()],
            "financing_sensitivity": str(exposure_factors_raw.get("financing_sensitivity", "unknown")).strip().lower() or "unknown",
            "regulatory_dependency": str(exposure_factors_raw.get("regulatory_dependency", "unknown")).strip().lower() or "unknown",
            "export_import_dependency": str(exposure_factors_raw.get("export_import_dependency", "unknown")).strip().lower() or "unknown",
        }
        market_validation_proxy_raw = record.get("market_validation_proxy", {}) if isinstance(record.get("market_validation_proxy"), dict) else {}
        market_validation_proxy = {
            "symbol": str(market_validation_proxy_raw.get("symbol", ticker)).strip() or ticker,
            "kind": str(market_validation_proxy_raw.get("kind", "ticker")).strip() or "ticker",
        }
        normalized[ticker] = {
            **record,
            "ticker": ticker,
            "policy_exposures": policy_exposures,
            "policy_channels": policy_channels,
            "business_lines": [str(item).strip() for item in record.get("business_lines", []) if str(item).strip()],
            "aliases": [str(item).strip().lower() for item in record.get("aliases", []) if str(item).strip()],
            "evidence": evidence,
            "policy_channel_details": policy_channel_details,
            "exposure_factors": exposure_factors,
            "market_validation_proxy": market_validation_proxy,
        }
    return normalized


def load_company_knowledge_from_disk() -> dict[str, dict[str, Any]]:
    if not COMPANY_KNOWLEDGE_FILE.exists():
        return {}
    try:
        raw = json.loads(COMPANY_KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return normalize_company_knowledge(raw)


def company_knowledge_for_ticker(ticker: str) -> dict[str, Any]:
    return COMPANY_KNOWLEDGE.get(normalize_ticker(ticker), {})


def normalize_policy_signal_rules(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    political_relevance = raw.get("political_relevance", {}) if isinstance(raw.get("political_relevance"), dict) else {}
    event_stage_rules = raw.get("event_stage_rules", {}) if isinstance(raw.get("event_stage_rules"), dict) else {}
    return {
        "political_relevance": {
            "institution_terms": [str(item).strip().lower() for item in political_relevance.get("institution_terms", []) if str(item).strip()],
            "legal_terms": [str(item).strip().lower() for item in political_relevance.get("legal_terms", []) if str(item).strip()],
            "action_terms": [str(item).strip().lower() for item in political_relevance.get("action_terms", []) if str(item).strip()],
            "weak_context_terms": [str(item).strip().lower() for item in political_relevance.get("weak_context_terms", []) if str(item).strip()],
            "non_political_terms": [str(item).strip().lower() for item in political_relevance.get("non_political_terms", []) if str(item).strip()],
        },
        "event_stage_rules": {
            str(name).strip().lower(): [str(item).strip().lower() for item in values if str(item).strip()]
            for name, values in event_stage_rules.items()
            if isinstance(values, list)
        },
        "negation_terms": [str(item).strip().lower() for item in raw.get("negation_terms", []) if str(item).strip()],
        "reversal_terms": [str(item).strip().lower() for item in raw.get("reversal_terms", []) if str(item).strip()],
        "thread_match_terms": [str(item).strip().lower() for item in raw.get("thread_match_terms", []) if str(item).strip()],
    }


def load_policy_signal_rules() -> dict[str, Any]:
    if not POLICY_SIGNAL_RULES_FILE.exists():
        return normalize_policy_signal_rules({})
    try:
        raw = json.loads(POLICY_SIGNAL_RULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return normalize_policy_signal_rules({})
    return normalize_policy_signal_rules(raw)


def normalize_market_validation_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    windows_raw = raw.get("windows", {}) if isinstance(raw.get("windows"), dict) else {}
    thresholds_raw = raw.get("thresholds", {}) if isinstance(raw.get("thresholds"), dict) else {}
    baseline_raw = raw.get("baseline", {}) if isinstance(raw.get("baseline"), dict) else {}
    fallback_raw = raw.get("fallback", {}) if isinstance(raw.get("fallback"), dict) else {}
    return {
        "windows": {
            str(name).strip(): {
                "range": str(config.get("range", "")).strip(),
                "interval": str(config.get("interval", "")).strip(),
            }
            for name, config in windows_raw.items()
            if isinstance(config, dict)
        },
        "thresholds": {
            "price_sigma": float(thresholds_raw.get("price_sigma", 2.0) or 2.0),
            "volume_ratio": float(thresholds_raw.get("volume_ratio", 1.5) or 1.5),
        },
        "baseline": {
            "lookback_periods": int(baseline_raw.get("lookback_periods", 20) or 20),
            "min_points": int(baseline_raw.get("min_points", 5) or 5),
        },
        "fallback": {
            "status": str(fallback_raw.get("status", "predicted_only")).strip() or "predicted_only",
            "reason": str(fallback_raw.get("reason", "market history unavailable")).strip() or "market history unavailable",
        },
    }


def load_market_validation_config() -> dict[str, Any]:
    if not MARKET_VALIDATION_CONFIG_FILE.exists():
        return normalize_market_validation_config({})
    try:
        raw = json.loads(MARKET_VALIDATION_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return normalize_market_validation_config({})
    return normalize_market_validation_config(raw)


def load_watchlist_from_disk() -> list[str]:
    if not WATCHLIST_FILE.exists():
        return list(DEFAULT_WATCHLIST)
    try:
        raw = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return list(DEFAULT_WATCHLIST)
    values = normalize_watchlist_values(raw)
    return values or list(DEFAULT_WATCHLIST)


def save_watchlist_to_disk(tickers: list[str]) -> None:
    WATCHLIST_FILE.write_text(
        json.dumps({"tickers": tickers, "updated_at": now_iso()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_watchlist() -> list[str]:
    with WATCHLIST_LOCK:
        return list(WATCHLIST_STATE)


def set_watchlist(tickers: list[str]) -> list[str]:
    normalized = normalize_watchlist_values(tickers) or list(DEFAULT_WATCHLIST)
    with WATCHLIST_LOCK:
        WATCHLIST_STATE[:] = normalized
    try:
        save_watchlist_to_disk(normalized)
    except Exception:
        pass
    return list(normalized)


# Load persisted watchlist after helper definitions are available.
with WATCHLIST_LOCK:
    WATCHLIST_STATE[:] = load_watchlist_from_disk()
COMPANY_KNOWLEDGE.update(load_company_knowledge_from_disk())
POLICY_SIGNAL_RULES.update(load_policy_signal_rules())
MARKET_VALIDATION_CONFIG.update(load_market_validation_config())
SOURCE_REGISTRY.update(load_source_registry())


# ---------------------------------------------------------------------------
# News fetching
# ---------------------------------------------------------------------------


def _extract_loose_xml_text(block: str, tag_name: str) -> str:
    tag = re.escape(tag_name)
    match = re.search(rf"<(?:[\w.-]+:)?{tag}\b[^>]*>(.*?)</(?:[\w.-]+:)?{tag}>", block, flags=re.I | re.S)
    return match.group(1).strip() if match else ""


def parse_rss_items(source: dict[str, Any], xml_text: str) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    source_metadata = source_metadata_for(source.get("name", ""), source.get("url", ""))

    def add_item(title: str, link: str, summary: str, published_raw: str) -> None:
        if not title:
            return
        published_at = parse_datetime(published_raw)
        articles.append(
            {
                "source": source["name"],
                "headline": strip_tags(title),
                "url": html.unescape(link) if link else source["url"],
                "published_at": published_at or now_wib(),
                "summary": strip_tags(summary),
                "source_weight": float(source["weight"]),
                **source_metadata,
            }
        )

    parsed = False
    try:
        root = ET.fromstring(xml_text)
        items = list(root.findall(".//item"))
        if not items:
            items = list(root.findall(".//{*}item"))
        for item in items[:80]:
            title = safe_text(item, "title")
            link = safe_text(item, "link")
            summary = safe_text(item, "description") or safe_text(item, "encoded")
            published_at = safe_text(item, "pubDate") or safe_text(item, "date")
            add_item(title, link, summary, published_at)
        parsed = bool(items)
    except Exception:
        parsed = False

    if articles or parsed:
        return articles

    # Fallback for malformed RSS/Atom payloads where strict XML parsing fails.
    for item_match in re.finditer(r"<item\b[^>]*>(.*?)</item>", xml_text, flags=re.I | re.S):
        item_block = item_match.group(1)
        title = _extract_loose_xml_text(item_block, "title")
        link = _extract_loose_xml_text(item_block, "link")
        summary = _extract_loose_xml_text(item_block, "description") or _extract_loose_xml_text(item_block, "encoded")
        published_raw = _extract_loose_xml_text(item_block, "pubDate") or _extract_loose_xml_text(item_block, "date")
        add_item(title, link, summary, published_raw)
        if len(articles) >= 80:
            break

    return articles


def parse_html_signal(source: dict[str, Any], html_text: str) -> list[dict[str, Any]]:
    page_title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.I | re.S)
    if match:
        page_title = strip_tags(match.group(1))
    heading = ""
    for tag in ("h1", "h2", "h3"):
        match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html_text, flags=re.I | re.S)
        if match:
            heading = strip_tags(match.group(1))
            break
    description = ""
    match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html_text, flags=re.I)
    if match:
        description = strip_tags(match.group(1))

    source_published_at = extract_html_published_at(html_text) or now_wib()

    base_url = source["url"].rstrip("/")
    domain_match = re.match(r"https?://([^/]+)", base_url)
    domain = domain_match.group(1) if domain_match else ""
    candidates: list[dict[str, Any]] = []
    source_metadata = source_metadata_for(source.get("name", ""), source.get("url", ""))

    anchor_pattern = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', flags=re.I | re.S)
    for href, inner_html in anchor_pattern.findall(html_text):
        title = strip_tags(inner_html)
        if len(title) < 28:
            continue
        href = html.unescape(href.strip())
        if href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        if href.startswith("/"):
            href = f"{base_url}{href}"
        elif href.startswith("//"):
            href = f"https:{href}"
        elif href.startswith("http"):
            if domain and domain not in href:
                continue
        else:
            continue
        item = {
            "source": source["name"],
            "headline": title,
            "url": href,
            "published_at": source_published_at,
            "summary": description or page_title or title,
            "source_weight": float(source["weight"]),
            **source_metadata,
        }
        if is_relevant_article(item):
            candidates.append(item)

    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in candidates:
        if item["url"] in seen_urls:
            continue
        if any(text_similarity(item["headline"], existing["headline"]) > 0.92 for existing in deduped):
            continue
        seen_urls.add(item["url"])
        deduped.append(item)
        if len(deduped) >= 8:
            break

    return deduped


def enrich_html_article_dates(articles: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = {
        "date_enrichment_attempted": 0,
        "date_enrichment_success_count": 0,
        "date_fallback_count": 0,
    }
    for article in articles:
        url = str(article.get("url") or "").strip()
        if not url:
            stats["date_fallback_count"] += 1
            continue
        stats["date_enrichment_attempted"] += 1
        try:
            original_published_at = article.get("published_at") if isinstance(article.get("published_at"), datetime) else None
            response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
            response.raise_for_status()
            published_at = extract_html_published_at(response.text)
            if published_at:
                article["published_at"] = published_at
                if not original_published_at or published_at != original_published_at:
                    stats["date_enrichment_success_count"] += 1
                continue
        except Exception:
            pass
        stats["date_fallback_count"] += 1
    return articles, stats


def build_source_diagnostic(
    source: dict[str, Any],
    *,
    status: str,
    articles: list[dict[str, Any]] | None = None,
    warning: str | None = None,
    date_stats: dict[str, int] | None = None,
) -> dict[str, Any]:
    metadata = source_metadata_for(str(source.get("name") or ""), str(source.get("url") or ""))
    resolution_method = str(metadata.get("source_profile_resolution", "inferred_fallback") or "inferred_fallback")
    canonical_name = str(metadata.get("source_profile", {}).get("canonical_name") or source.get("name") or "Unknown source").strip() or "Unknown source"
    article_count = len(articles or [])
    stats = date_stats or {}
    return {
        "name": canonical_name,
        "kind": str(source.get("kind") or "unknown"),
        "status": status,
        "warning": str(warning or ""),
        "article_count": article_count,
        "used_registry_profile": bool(metadata.get("used_registry_profile")),
        "resolution_method": resolution_method,
        "date_enrichment_attempted": bool(stats.get("date_enrichment_attempted", 0)),
        "date_enrichment_success_count": int(stats.get("date_enrichment_success_count", 0) or 0),
        "date_fallback_count": int(stats.get("date_fallback_count", 0) or 0),
    }


def summarize_source_diagnostics_from_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for article in articles:
        source_name = str(article.get("source") or "Unknown source").strip() or "Unknown source"
        grouped.setdefault(source_name, []).append(article)

    diagnostics: list[dict[str, Any]] = []
    for source_name, group in sorted(grouped.items()):
        first = group[0]
        metadata = source_metadata_for(source_name, str(first.get("url") or ""))
        resolution_method = str(first.get("source_profile_resolution") or metadata.get("source_profile_resolution", "inferred_fallback") or "inferred_fallback")
        source_profile = first.get("source_profile", {}) if isinstance(first.get("source_profile", {}), dict) else metadata.get("source_profile", {})
        diagnostics.append(
            {
                "name": str(source_profile.get("canonical_name") or source_name).strip() or source_name,
                "kind": str(first.get("source_kind") or "provided"),
                "status": "inferred_ok" if group else "empty",
                "warning": "",
                "article_count": len(group),
                "used_registry_profile": bool(first.get("used_registry_profile", metadata.get("used_registry_profile", False))),
                "resolution_method": resolution_method,
                "date_enrichment_attempted": None,
                "date_enrichment_success_count": None,
                "date_fallback_count": None,
            }
        )
    return diagnostics


def build_source_health_summary(sources: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_sources = sources if isinstance(sources, list) else []
    normalized_events = events if isinstance(events, list) else []
    relationships = [
        relationship
        for event in normalized_events
        for relationship in (event.get("stock_relationships", []) if isinstance(event.get("stock_relationships", []), list) else [])
        if isinstance(relationship, dict)
    ]

    ok_source_count = sum(1 for source in normalized_sources if str(source.get("status") or "").strip().lower() == "ok")
    errored_source_count = sum(1 for source in normalized_sources if str(source.get("status") or "").strip().lower() == "error")
    empty_source_count = sum(1 for source in normalized_sources if str(source.get("status") or "").strip().lower() == "empty")
    warning_source_count = sum(1 for source in normalized_sources if str(source.get("warning") or "").strip())
    registry_backed_source_count = sum(1 for source in normalized_sources if bool(source.get("used_registry_profile")))
    fallback_source_count = sum(
        1
        for source in normalized_sources
        if not bool(source.get("used_registry_profile"))
        or str(source.get("resolution_method") or "").strip().lower() in {"inferred_fallback", "url_inference", "heuristic_fallback", "unknown"}
    )
    date_enrichment_success_count = sum(int(source.get("date_enrichment_success_count", 0) or 0) for source in normalized_sources)
    date_fallback_count = sum(int(source.get("date_fallback_count", 0) or 0) for source in normalized_sources)

    def event_warning_count(warning: str) -> int:
        return sum(1 for event in normalized_events if str(event.get("coverage_warning") or "").strip() == warning)

    return {
        "source_count": len(normalized_sources),
        "ok_source_count": ok_source_count,
        "fallback_source_count": fallback_source_count,
        "errored_source_count": errored_source_count,
        "empty_source_count": empty_source_count,
        "warning_source_count": warning_source_count,
        "registry_backed_source_count": registry_backed_source_count,
        "date_enrichment_success_count": date_enrichment_success_count,
        "date_fallback_count": date_fallback_count,
        "displayed_event_count": len(normalized_events),
        "relationship_count": len(relationships),
        "conflicted_relationship_count": sum(1 for relationship in relationships if bool(relationship.get("source_conflict"))),
        "independent_corroborated_relationship_count": sum(1 for relationship in relationships if str(relationship.get("corroboration_label") or "") == "independently_corroborated"),
        "weak_single_source_relationship_count": sum(1 for relationship in relationships if str(relationship.get("corroboration_label") or "") == "single_weak_source"),
        "syndicated_coverage_count": sum(int(relationship.get("syndicated_coverage_count", 0) or 0) for relationship in relationships),
        "stale_event_count": event_warning_count("stale_coverage"),
        "thin_event_count": event_warning_count("thin_source_coverage"),
        "duplicated_event_count": event_warning_count("duplicated_coverage"),
    }


def unpack_news_fetch_result(result: Any) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    if not isinstance(result, tuple):
        return [], ["News fetcher returned an invalid result shape."], []

    if len(result) == 3:
        articles, warnings, diagnostics = result
    elif len(result) == 2:
        articles, warnings = result
        diagnostics = summarize_source_diagnostics_from_articles(articles if isinstance(articles, list) else [])
    else:
        return [], ["News fetcher returned an unsupported tuple shape."], []

    normalized_articles = articles if isinstance(articles, list) else []
    normalized_warnings = warnings if isinstance(warnings, list) else [str(warnings)] if warnings else []
    normalized_diagnostics = diagnostics if isinstance(diagnostics, list) else []
    return normalized_articles, normalized_warnings, normalized_diagnostics


def fetch_source(
    source: dict[str, Any],
    include_diagnostic: bool = False,
) -> tuple[list[dict[str, Any]], str | None] | tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    try:
        response = requests.get(source["url"], timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
        response.raise_for_status()
        if source["kind"] == "rss":
            articles = parse_rss_items(source, response.text)
            if not articles:
                warning = f"{source['name']}: no RSS items extracted"
                if include_diagnostic:
                    return [], warning, build_source_diagnostic(source, status="empty", warning=warning)
                return [], warning
            if include_diagnostic:
                return articles, None, build_source_diagnostic(source, status="ok", articles=articles)
            return articles, None
        articles = parse_html_signal(source, response.text)
        if not articles:
            warning = f"{source['name']}: no article links extracted"
            if include_diagnostic:
                return [], warning, build_source_diagnostic(source, status="empty", warning=warning)
            return [], warning
        enriched_articles, date_stats = enrich_html_article_dates(articles)
        if include_diagnostic:
            return enriched_articles, None, build_source_diagnostic(source, status="ok", articles=enriched_articles, date_stats=date_stats)
        return enriched_articles, None
    except Exception as exc:  # pragma: no cover - network failures are expected in some environments
        warning = f"{source['name']}: {exc}"
        if include_diagnostic:
            return [], warning, build_source_diagnostic(source, status="error", warning=warning)
        return [], warning


def fetch_news_bundle() -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    articles: list[dict[str, Any]] = []
    warnings: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(NEWS_SOURCES))) as pool:
        futures = {pool.submit(fetch_source, source, True): source for source in NEWS_SOURCES}
        for future in as_completed(futures):
            source_articles, warning, source_diagnostic = future.result()
            if warning:
                warnings.append(warning)
            diagnostics.append(source_diagnostic)
            articles.extend(source_articles)

    articles = [article for article in articles if is_relevant_article(article)]
    if not articles:
        warnings.append("No live news articles available right now.")
    diagnostics.sort(key=lambda item: str(item.get("name") or ""))
    return articles, warnings, diagnostics


def dedupe_articles(articles: list[dict[str, Any]], window: str = DEFAULT_EVENT_WINDOW) -> list[dict[str, Any]]:
    filtered = [article for article in articles if not is_stale_article(article.get("published_at"), window)]
    filtered.sort(key=lambda article: article.get("published_at") or now_wib(), reverse=True)
    return merge_duplicate_articles(filtered)


# ---------------------------------------------------------------------------
# Stock fetching
# ---------------------------------------------------------------------------



def fetch_live_quote(ticker: str) -> dict[str, Any]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker)}?range=1d&interval=1d&includePrePost=false&events=div,splits"
    response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
    response.raise_for_status()
    payload = response.json()["chart"]["result"][0]
    meta = payload["meta"]
    closes = [value for value in payload.get("indicators", {}).get("quote", [{}])[0].get("close", []) if value is not None]
    price = meta.get("regularMarketPrice")
    change_pct = meta.get("regularMarketChangePercent")
    change_points = meta.get("regularMarketChange")
    volume = meta.get("regularMarketVolume")
    market_time = meta.get("regularMarketTime")
    previous_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None and closes:
        price = closes[-1]
    if change_points is None and price is not None and previous_close not in (None, 0):
        change_points = float(price) - float(previous_close)
    if change_pct is None and change_points is not None and previous_close not in (None, 0):
        change_pct = (float(change_points) / float(previous_close)) * 100.0
    market_dt = datetime.fromtimestamp(market_time, tz=WIB) if market_time else now_wib()
    return {
        "ticker": ticker,
        "name": company_name_for_ticker(ticker),
        "sector": sector_for_ticker(ticker),
        "price": float(price) if price is not None else None,
        "change_pct": float(change_pct) if change_pct is not None else None,
        "volume": int(volume) if volume is not None else None,
        "market_time": market_dt,
        "after_hours": (not within_trading_hours(market_dt)) and (now_wib() - market_dt > timedelta(minutes=30)),
        "source": "yahoo-finance",
    }


def fetch_stock_quotes(tickers: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    tickers = [normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)]
    quotes: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    if not tickers:
        return quotes, ["No tickers requested."]
    with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as pool:
        futures = {pool.submit(fetch_live_quote, ticker): ticker for ticker in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                quotes[ticker] = future.result()
            except Exception as exc:  # pragma: no cover - network failures are expected in some environments
                warnings.append(f"{ticker}: {exc}")
    return quotes, warnings


def fetch_market_index() -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    symbol = "%5EJKSE"
    urls = [
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=5m&includePrePost=false&events=div,splits",
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d&includePrePost=false&events=div,splits",
    ]
    last_error: str | None = None
    for url in urls:
        try:
            response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
            response.raise_for_status()
            payload = response.json()["chart"]["result"][0]
            meta = payload["meta"]
            closes = [value for value in payload.get("indicators", {}).get("quote", [{}])[0].get("close", []) if value is not None]
            price = meta.get("regularMarketPrice")
            change_pct = meta.get("regularMarketChangePercent")
            change_points = meta.get("regularMarketChange")
            market_time = meta.get("regularMarketTime")
            previous_close = meta.get("chartPreviousClose") or meta.get("previousClose")
            if price is None and closes:
                price = closes[-1]
            if change_points is None and price is not None and previous_close not in (None, 0):
                change_points = float(price) - float(previous_close)
            if change_pct is None and change_points is not None and previous_close not in (None, 0):
                change_pct = (float(change_points) / float(previous_close)) * 100.0
            market_dt = datetime.fromtimestamp(market_time, tz=WIB) if market_time else now_wib()
            return {
                "symbol": "^JKSE",
                "name": "IHSG",
                "value": float(price) if price is not None else None,
                "change_pct": float(change_pct) if change_pct is not None else None,
                "change_points": float(change_points) if change_points is not None else None,
                "series": [float(value) for value in closes[-48:]],
                "market_time": market_dt.isoformat(timespec="seconds"),
                "source": "yahoo-finance",
            }, warnings
        except Exception as exc:  # pragma: no cover - network failures are expected in some environments
            last_error = str(exc)
    if last_error:
        warnings.append(f"IHSG: {last_error}")
    return {
        "symbol": "^JKSE",
        "name": "IHSG",
        "value": None,
        "change_pct": None,
        "change_points": None,
        "series": [],
        "market_time": now_iso(),
        "source": "unavailable",
    }, warnings


def stock_history_window_config(window: str | None) -> dict[str, Any]:
    return STOCK_HISTORY_WINDOWS[normalize_event_window(window)]


def fetch_ticker_history(ticker: str, window: str | None = None) -> dict[str, Any]:
    normalized_ticker = normalize_ticker(ticker)
    config = stock_history_window_config(window)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(normalized_ticker)}"
        f"?range={quote(config['range'])}&interval={quote(config['interval'])}&includePrePost=false&events=div,splits"
    )
    warnings: list[str] = []
    try:
        response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
        response.raise_for_status()
        chart = response.json().get("chart", {}).get("result", [])
        if not chart:
            raise ValueError("empty history")
        result = chart[0]
        meta = result.get("meta", {})
        quote_data = result.get("indicators", {}).get("quote", [{}])[0]
        raw_timestamps = list(result.get("timestamp", []) or [])
        raw_closes = list(quote_data.get("close", []) or [])
        series_pairs = [
            (int(ts), float(price))
            for ts, price in zip(raw_timestamps, raw_closes)
            if ts is not None and price is not None
        ]
        prices = [price for _, price in series_pairs]
        series = [
            {
                "time": datetime.fromtimestamp(ts, tz=WIB).isoformat(timespec="seconds"),
                "value": price,
            }
            for ts, price in series_pairs
        ]
        volumes = [float(value) for value in quote_data.get("volume", []) if value is not None]
        price = meta.get("regularMarketPrice")
        change_pct = meta.get("regularMarketChangePercent")
        change_points = meta.get("regularMarketChange")
        volume = meta.get("regularMarketVolume")
        market_time = meta.get("regularMarketTime")
        previous_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None and prices:
            price = prices[-1]
        if change_points is None and price is not None and previous_close not in (None, 0):
            change_points = float(price) - float(previous_close)
        if change_pct is None and change_points is not None and previous_close not in (None, 0):
            change_pct = (float(change_points) / float(previous_close)) * 100.0
        market_dt = datetime.fromtimestamp(market_time, tz=WIB) if market_time else now_wib()
        start_price = prices[0] if prices else None
        period_change_points = float(price) - float(start_price) if price is not None and start_price not in (None, 0) else None
        period_change_pct = (period_change_points / float(start_price)) * 100.0 if period_change_points is not None and start_price not in (None, 0) else None
        return {
            "ticker": normalized_ticker,
            "name": company_name_for_ticker(normalized_ticker),
            "sector": sector_for_ticker(normalized_ticker),
            "window": normalize_event_window(window),
            "window_label": event_window_label(window),
            "range": config["range"],
            "interval": config["interval"],
            "price": float(price) if price is not None else None,
            "change_pct": float(change_pct) if change_pct is not None else None,
            "change_points": float(change_points) if change_points is not None else None,
            "period_change_pct": float(period_change_pct) if period_change_pct is not None else None,
            "period_change_points": float(period_change_points) if period_change_points is not None else None,
            "volume": int(volume) if volume is not None else None,
            "series": prices,
            "history": series,
            "series_points": len(prices),
            "series_start": series[0]["time"] if series else None,
            "series_end": series[-1]["time"] if series else None,
            "series_high": max(prices) if prices else None,
            "series_low": min(prices) if prices else None,
            "market_time": market_dt.isoformat(timespec="seconds"),
            "source": "yahoo-finance",
            "warnings": warnings,
        }
    except Exception as exc:  # pragma: no cover - network failures are environment-dependent
        warnings.append(f"history unavailable for {normalized_ticker}: {exc}")
        return {
            "ticker": normalized_ticker,
            "name": company_name_for_ticker(normalized_ticker),
            "sector": sector_for_ticker(normalized_ticker),
            "window": normalize_event_window(window),
            "window_label": event_window_label(window),
            "range": config["range"],
            "interval": config["interval"],
            "price": None,
            "change_pct": None,
            "change_points": None,
            "period_change_pct": None,
            "period_change_points": None,
            "volume": None,
            "series": [],
            "history": [],
            "series_points": 0,
            "series_start": None,
            "series_end": None,
            "series_high": None,
            "series_low": None,
            "market_time": now_iso(),
            "source": "unavailable",
            "warnings": warnings,
        }


def fetch_market_validation_series(ticker: str, range_name: str, interval: str) -> dict[str, Any]:
    normalized_ticker = normalize_ticker(ticker)
    knowledge = company_knowledge_for_ticker(normalized_ticker)
    proxy = knowledge.get("market_validation_proxy", {}) if isinstance(knowledge.get("market_validation_proxy"), dict) else {}
    symbol = str(proxy.get("symbol", normalized_ticker)).strip() or normalized_ticker
    warnings: list[str] = []
    if not symbol:
        return {
            "ticker": normalized_ticker,
            "symbol": normalized_ticker,
            "range": range_name,
            "interval": interval,
            "prices": [],
            "volumes": [],
            "market_time": now_iso(),
            "source": "unavailable",
            "warnings": ["validation symbol unavailable"],
        }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range={quote(range_name)}&interval={quote(interval)}&includePrePost=false&events=div,splits"
    try:
        response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
        response.raise_for_status()
        payload = response.json().get("chart", {}).get("result", [])
        if not payload:
            raise ValueError("empty validation history")
        result = payload[0]
        quote_data = result.get("indicators", {}).get("quote", [{}])[0]
        closes = [float(value) for value in quote_data.get("close", []) if value is not None]
        volumes = [float(value) for value in quote_data.get("volume", []) if value is not None]
        market_time = result.get("meta", {}).get("regularMarketTime")
        return {
            "ticker": normalized_ticker,
            "symbol": symbol,
            "range": range_name,
            "interval": interval,
            "prices": closes,
            "volumes": volumes,
            "market_time": datetime.fromtimestamp(market_time, tz=WIB).isoformat(timespec="seconds") if market_time else now_iso(),
            "source": "yahoo-finance",
            "warnings": warnings,
        }
    except Exception as exc:  # pragma: no cover - network failures are environment-dependent
        warnings.append(f"validation history unavailable for {symbol}: {exc}")
        return {
            "ticker": normalized_ticker,
            "symbol": symbol,
            "range": range_name,
            "interval": interval,
            "prices": [],
            "volumes": [],
            "market_time": now_iso(),
            "source": "unavailable",
            "warnings": warnings,
        }


def validation_window_for_article(article: dict[str, Any]) -> str:
    published_at = article.get("published_at")
    if isinstance(published_at, datetime) and (now_wib() - published_at) <= timedelta(days=1):
        return "30m"
    return "1d"


def sample_stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))


def validate_market_reaction(
    article: dict[str, Any],
    ticker: str,
    quote: dict[str, Any] | None,
    relationship: dict[str, Any],
    fetcher: Callable[[str, str, str], dict[str, Any]] | None = None,
    series_cache: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = MARKET_VALIDATION_CONFIG or load_market_validation_config()
    windows = config.get("windows", {}) if isinstance(config, dict) else {}
    baseline_cfg = config.get("baseline", {}) if isinstance(config, dict) else {}
    thresholds = config.get("thresholds", {}) if isinstance(config, dict) else {}
    fallback = config.get("fallback", {}) if isinstance(config, dict) else {}

    validation_window = validation_window_for_article(article)
    window_cfg = windows.get(validation_window) or windows.get("1d") or {"range": "1mo", "interval": "1d"}
    range_name = str(window_cfg.get("range", "1mo"))
    interval = str(window_cfg.get("interval", "1d"))
    fetcher = fetcher or fetch_market_validation_series

    cache_key = (normalize_ticker(ticker), validation_window)
    if series_cache is not None and cache_key in series_cache:
        series = series_cache[cache_key]
    else:
        series = fetcher(ticker, range_name, interval)
        if series_cache is not None:
            series_cache[cache_key] = series

    prices = [float(value) for value in series.get("prices", []) if value is not None]
    volumes = [float(value) for value in series.get("volumes", []) if value is not None]
    warnings = [str(item) for item in series.get("warnings", []) if str(item).strip()]
    lookback_periods = max(int(baseline_cfg.get("lookback_periods", 20) or 20), 3)
    min_points = max(int(baseline_cfg.get("min_points", 5) or 5), 3)

    fallback_status = str(fallback.get("status", "predicted_only") or "predicted_only")
    fallback_reason = str(fallback.get("reason", "market history unavailable") or "market history unavailable")
    base_result = {
        "validation_status": fallback_status,
        "validation_window": validation_window,
        "abnormal_return": 0.0,
        "abnormal_volume_ratio": 0.0,
        "validation_score": 0.0,
        "validation_reason": fallback_reason,
        "validation_warnings": warnings,
        "validation_series_source": series.get("source", "unavailable"),
    }

    if not quote or not relationship:
        return {
            **base_result,
            "validation_status": "unvalidated",
            "validation_reason": "missing quote or relationship",
        }

    if len(prices) < min_points or len(volumes) < min_points:
        return {
            **base_result,
            "validation_status": "insufficient_data" if warnings else fallback_status,
            "validation_reason": warnings[0] if warnings else fallback_reason,
        }

    recent_prices = prices[-(lookback_periods + 1):]
    recent_volumes = volumes[-(lookback_periods + 1):]
    returns = []
    for previous, current in zip(recent_prices[:-1], recent_prices[1:]):
        if previous not in (None, 0):
            returns.append((float(current) - float(previous)) / float(previous))
    if len(returns) < max(2, min_points - 1):
        return {
            **base_result,
            "validation_status": "insufficient_data",
            "validation_reason": "not enough return history for baseline",
        }

    baseline_returns = returns[:-1] or returns
    observed_return = returns[-1]
    baseline_volumes = recent_volumes[:-1] or recent_volumes
    observed_volume = recent_volumes[-1]
    mean_return = sum(baseline_returns) / len(baseline_returns)
    sigma_return = sample_stddev(baseline_returns)
    return_z = abs(observed_return - mean_return) / sigma_return if sigma_return > 1e-9 else abs(observed_return - mean_return) * 100.0
    avg_volume = sum(baseline_volumes) / len(baseline_volumes) if baseline_volumes else 0.0
    volume_ratio = (observed_volume / avg_volume) if avg_volume > 0 else 0.0

    expected_direction = str(relationship.get("impact_direction", "neutral"))
    aligned = True
    if expected_direction == "positive":
        aligned = observed_return > 0
    elif expected_direction == "negative":
        aligned = observed_return < 0

    price_sigma_threshold = float(thresholds.get("price_sigma", 2.0) or 2.0)
    volume_ratio_threshold = float(thresholds.get("volume_ratio", 1.5) or 1.5)
    signal_strength = min(1.0, return_z / max(price_sigma_threshold, 0.1))
    volume_strength = min(1.0, volume_ratio / max(volume_ratio_threshold, 0.1)) if volume_ratio_threshold > 0 else 1.0
    validation_score = clamp(0.65 * signal_strength + 0.35 * volume_strength, 0.0, 1.0)

    if aligned and return_z >= price_sigma_threshold and volume_ratio >= volume_ratio_threshold:
        status = "confirmed"
        reason = "price and volume move align with predicted direction"
    elif not aligned and abs(observed_return) > 0.002:
        status = "rejected"
        reason = "market move conflicts with predicted direction"
    else:
        status = "predicted_only"
        reason = "market move is too weak or noisy to confirm the prediction"

    return {
        "validation_status": status,
        "validation_window": validation_window,
        "abnormal_return": round(observed_return - mean_return, 4),
        "abnormal_volume_ratio": round(volume_ratio, 3),
        "validation_score": round(validation_score, 3),
        "validation_reason": reason,
        "validation_warnings": warnings,
        "validation_series_source": series.get("source", "unavailable"),
    }


# ---------------------------------------------------------------------------
# NLP / scoring
# ---------------------------------------------------------------------------


def analyze_sentiment(text: str) -> tuple[str, float, float]:
    text = text.lower()
    positive_hits = sum(text.count(word) for word in ["naik", "positif", "dorong", "dukung", "stabil", "penguatan", "untung", "berhasil", "investasi", "pertumbuhan", "pemulihan"])
    negative_hits = sum(text.count(word) for word in ["turun", "negatif", "tekan", "jatuh", "risiko", "krisis", "masalah", "korupsi", "batal", "melemah", "larang", "polemik"])
    raw = positive_hits - negative_hits
    if positive_hits == 0 and negative_hits == 0:
        return "neutral", 0.0, 0.35
    score = clamp(raw / max(positive_hits + negative_hits, 1), -1.0, 1.0)
    if score > 0.12:
        return "positive", score, min(1.0, 0.35 + 0.12 * positive_hits)
    if score < -0.12:
        return "negative", score, min(1.0, 0.35 + 0.12 * negative_hits)
    return "neutral", score, 0.45


def classify_categories(text: str) -> list[str]:
    hits = []
    for category, keywords in CATEGORY_RULES.items():
        if any(keyword in text for keyword in keywords):
            hits.append(category)
    return hits[:4]


def extract_entities(text: str) -> list[str]:
    entities: list[str] = []
    for ticker, info in STOCK_MASTER.items():
        if any(alias in text for alias in info["aliases"]):
            entities.append(info["name"])
    for match in re.findall(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,}){0,3}\b", text):
        if match not in entities and len(match) > 3:
            entities.append(match)
    return entities[:12]


def sector_matches(text: str) -> set[str]:
    matches: set[str] = set()
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            matches.add(sector)
    return matches


def detect_policy_themes(text: str) -> list[dict[str, Any]]:
    themes: list[dict[str, Any]] = []
    for name, config in POLICY_THEMES.items():
        hit_count = sum(1 for keyword in config["keywords"] if keyword in text)
        if hit_count:
            themes.append(
                {
                    "name": name,
                    "keyword_hits": hit_count,
                    "sectors": list(config["sectors"]),
                    "channel": config["channel"],
                    "exposure_type": config["exposure_type"],
                }
            )
    themes.sort(key=lambda item: item["keyword_hits"], reverse=True)
    return themes[:4]


def policy_specificity_score(categories: list[str], themes: list[dict[str, Any]], text: str) -> float:
    score = 1.0
    score += min(1.5, 0.55 * len(categories))
    score += min(1.0, 0.35 * len(themes))
    if any(keyword in text for keyword in ["perpres", "perppu", "ruu", "uu ", "apbn", "anggaran", "tarif", "kuota", "izin"]):
        score += 0.8
    return min(5.0, score)


def evidence_quality_score(article: dict[str, Any], themes: list[dict[str, Any]], direct_alias_hit: bool, company_evidence: list[dict[str, Any]] | None = None) -> float:
    article_source_rank = source_type_rank(article.get("source_type"))
    company_rank = max((float(item.get("quality_rank") or source_type_rank(item.get("source_type"))) for item in (company_evidence or [])), default=0.0)
    source_quality = clamp(float(article.get("source_quality_score", 0.0) or 0.0), 0.0, 1.0)
    source_freshness = clamp(float(article.get("source_freshness_score", source_quality) or 0.0), 0.0, 1.0)
    base = 0.6 + 0.32 * article_source_rank + 0.55 * source_quality + 0.3 * source_freshness + 0.2 * max(float(article.get("source_weight") or 0.0), 0.4)
    if article.get("url"):
        base += 0.2
    if len(themes) >= 2:
        base += 0.25
    if direct_alias_hit:
        base += 0.35
    if company_rank:
        base += min(0.9, 0.18 * company_rank)
    return min(5.0, base)


def recency_weight_for_article(article: dict[str, Any], window: str = DEFAULT_EVENT_WINDOW) -> tuple[float, float]:
    recency_hours = 0.0
    published_at = article.get("published_at") or now_wib()
    if isinstance(published_at, datetime):
        recency_hours = max(0.0, (now_wib() - published_at).total_seconds() / 3600.0)
    window_hours = max(event_window_delta(window).total_seconds() / 3600.0, 1.0)
    recency_weight = max(0.2, 1.0 - recency_hours / window_hours)
    return recency_hours, recency_weight


def infer_article_policy_signal(text: str) -> dict[str, list[str]]:
    supportive_terms = [
        "dorong",
        "stimulus",
        "percepat",
        "subsidi",
        "insentif",
        "relaksasi",
        "permudah",
        "turunkan bunga",
        "dukungan",
        "tambahan anggaran",
        "berlaku",
        "sahkan",
    ]
    restrictive_terms = [
        "larang",
        "batasi",
        "pembatasan",
        "kuota",
        "quota",
        "tarif",
        "bea masuk",
        "moratorium",
        "tekan",
        "perketat",
    ]
    relief_terms = [
        "batalkan",
        "cabut",
        "hapus",
        "longgarkan",
        "relaksasi",
        "buka kembali",
    ]
    return {
        "supportive_hits": collect_phrase_hits(text, supportive_terms),
        "restrictive_hits": collect_phrase_hits(text, restrictive_terms),
        "relief_hits": collect_phrase_hits(text, relief_terms),
    }


def match_policy_channels(text: str, knowledge: dict[str, Any], themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not knowledge:
        return []
    theme_names = {str(theme.get("name", "")).strip() for theme in themes if str(theme.get("name", "")).strip()}
    if not theme_names:
        return []
    article_signal = infer_article_policy_signal(text)
    business_lines = [str(item).strip() for item in knowledge.get("business_lines", []) if str(item).strip()]
    exposures = knowledge.get("exposure_factors", {}) if isinstance(knowledge.get("exposure_factors"), dict) else {}
    revenue_exposure = {str(item).strip() for item in exposures.get("revenue_exposure", []) if str(item).strip()}
    input_cost_exposure = {str(item).strip() for item in exposures.get("input_cost_exposure", []) if str(item).strip()}
    matched: list[dict[str, Any]] = []
    for detail in knowledge.get("policy_channel_details", []):
        if not isinstance(detail, dict):
            continue
        channel = str(detail.get("channel", "")).strip()
        if not channel:
            continue
        theme_hits = sorted(theme_names & set(knowledge.get("policy_exposures", [])))
        detail_text = " ".join([channel, *[str(item) for item in detail.get("keywords", [])]])
        detail_theme_hits = []
        for theme_name in theme_hits:
            theme_keywords = POLICY_THEMES.get(theme_name, {}).get("keywords", [])
            if collect_phrase_hits(detail_text, theme_keywords) or normalize_match_text(theme_name) in normalize_match_text(detail_text):
                detail_theme_hits.append(theme_name)
        if not detail_theme_hits:
            detail_theme_hits = theme_hits
        theme_keywords = [
            keyword
            for theme_name in detail_theme_hits
            for keyword in POLICY_THEMES.get(theme_name, {}).get("keywords", [])
        ]
        revenue_hits = sorted(set(detail_theme_hits) & revenue_exposure)
        input_cost_hits = sorted(set(detail_theme_hits) & input_cost_exposure)
        keyword_hits = collect_phrase_hits(text, detail.get("keywords", []))
        theme_keyword_hits = collect_phrase_hits(text, theme_keywords)
        business_line_hits = collect_phrase_hits(text, business_lines)
        if not detail_theme_hits or (not keyword_hits and not theme_keyword_hits and not business_line_hits and not revenue_hits and not input_cost_hits):
            continue
        direction_map = detail.get("direction_map", {}) if isinstance(detail.get("direction_map"), dict) else {}
        positive_direction_hits = collect_phrase_hits(text, direction_map.get("positive", []))
        negative_direction_hits = collect_phrase_hits(text, direction_map.get("negative", []))
        confidence = clamp(
            0.2
            + 0.3 * float(detail.get("confidence", 0.5))
            + 0.12 * len(detail_theme_hits)
            + 0.1 * len(keyword_hits)
            + 0.09 * len(theme_keyword_hits)
            + 0.1 * len(business_line_hits)
            + 0.08 * len(revenue_hits)
            + 0.05 * len(input_cost_hits),
            0.0,
            1.0,
        )
        matched.append(
            {
                "channel": channel,
                "channel_confidence": round(confidence, 3),
                "matched_themes": detail_theme_hits,
                "keyword_hits": keyword_hits,
                "theme_keyword_hits": theme_keyword_hits,
                "business_line_hits": business_line_hits,
                "revenue_exposure_hits": revenue_hits,
                "input_cost_exposure_hits": input_cost_hits,
                "positive_direction_hits": positive_direction_hits,
                "negative_direction_hits": negative_direction_hits,
                "article_signal": article_signal,
            }
        )
    matched.sort(
        key=lambda item: (
            item["channel_confidence"],
            len(item["keyword_hits"]) + len(item.get("theme_keyword_hits", [])),
            len(item["business_line_hits"]),
        ),
        reverse=True,
    )
    return matched[:4]


def score_company_exposure(knowledge: dict[str, Any], matched_channels: list[dict[str, Any]], direct_alias_hit: bool) -> dict[str, Any]:
    exposure_factors = knowledge.get("exposure_factors", {}) if isinstance(knowledge.get("exposure_factors"), dict) else {}
    if direct_alias_hit:
        return {
            "company_exposure": 5.0,
            "channel_confidence": round(max((float(item.get("channel_confidence", 0.0)) for item in matched_channels), default=1.0), 3),
            "exposure_factors": exposure_factors,
            "exposure_rationale": "Direct company mention in the source creates a first-order linkage.",
        }
    if not matched_channels:
        return {
            "company_exposure": 0.0,
            "channel_confidence": 0.0,
            "exposure_factors": exposure_factors,
            "exposure_rationale": "No matched company-specific policy channel.",
        }
    avg_channel_confidence = sum(float(item.get("channel_confidence", 0.0)) for item in matched_channels) / len(matched_channels)
    financing_bonus = {"low": 0.15, "medium": 0.35, "high": 0.55}.get(str(exposure_factors.get("financing_sensitivity", "unknown")), 0.0)
    regulatory_bonus = {"low": 0.1, "medium": 0.2, "high": 0.35}.get(str(exposure_factors.get("regulatory_dependency", "unknown")), 0.0)
    trade_bonus = {"low": 0.05, "medium": 0.15, "high": 0.3}.get(str(exposure_factors.get("export_import_dependency", "unknown")), 0.0)
    revenue_bonus = 0.18 * sum(len(item.get("revenue_exposure_hits", [])) for item in matched_channels)
    cost_penalty = 0.08 * sum(len(item.get("input_cost_exposure_hits", [])) for item in matched_channels)
    exposure = clamp(2.45 + 1.55 * avg_channel_confidence + financing_bonus + regulatory_bonus + trade_bonus + revenue_bonus - cost_penalty, 0.0, 5.0)
    return {
        "company_exposure": round(exposure, 2),
        "channel_confidence": round(avg_channel_confidence, 3),
        "exposure_factors": exposure_factors,
        "exposure_rationale": f"Matched {len(matched_channels)} company-specific policy channel(s) with avg confidence {avg_channel_confidence:.2f}.",
    }


def expected_direction_for_company(themes: list[dict[str, Any]], matched_channels: list[dict[str, Any]], knowledge: dict[str, Any]) -> dict[str, Any]:
    theme_names = {str(theme.get("name", "")).strip() for theme in themes if str(theme.get("name", "")).strip()}
    exposure_factors = knowledge.get("exposure_factors", {}) if isinstance(knowledge.get("exposure_factors"), dict) else {}
    positive_score = 0.0
    negative_score = 0.0
    rationale_parts: list[str] = []
    article_signal = matched_channels[0].get("article_signal", {}) if matched_channels else {"supportive_hits": [], "restrictive_hits": [], "relief_hits": []}
    supportive_hits = list(article_signal.get("supportive_hits", []))
    restrictive_hits = list(article_signal.get("restrictive_hits", []))
    relief_hits = list(article_signal.get("relief_hits", []))

    for channel in matched_channels:
        channel_themes = set(channel.get("matched_themes", [])) or theme_names
        positive_direction_hits = channel.get("positive_direction_hits", [])
        negative_direction_hits = channel.get("negative_direction_hits", [])
        positive_score += 0.9 * len(positive_direction_hits)
        negative_score += 0.9 * len(negative_direction_hits)
        if channel.get("keyword_hits"):
            rationale_parts.append(f"{channel['channel']} via {', '.join(channel['keyword_hits'][:3])}")

        if channel_themes & {"HOUSING", "BANKING_LIQUIDITY", "INFRASTRUCTURE", "DIGITAL_PUBLIC", "DOWNSTREAMING", "FOOD_SECURITY", "DEFENSE_PROCUREMENT"}:
            positive_score += 0.9 * len(supportive_hits)
            positive_score += 0.8 * len(relief_hits)
            negative_score += 0.7 * len(restrictive_hits)
        if "TRADE_RESTRICTION" in channel_themes:
            if restrictive_hits and relief_hits:
                positive_score += 1.7
                rationale_parts.append("restriction rollback improves trade realization")
            elif restrictive_hits:
                negative_score += 1.7
                rationale_parts.append("trade restriction pressures export/import volumes")
            elif supportive_hits:
                positive_score += 0.6
        if "ENERGY_TRANSITION" in channel_themes:
            if restrictive_hits and relief_hits:
                positive_score += 1.0
            elif restrictive_hits:
                negative_score += 1.0
            elif supportive_hits:
                positive_score += 0.7

    if "TRADE_RESTRICTION" in theme_names and str(exposure_factors.get("export_import_dependency", "unknown")) == "high":
        if restrictive_hits and not relief_hits:
            negative_score += 0.8
        elif restrictive_hits and relief_hits:
            positive_score += 0.8
    if theme_names & {"BANKING_LIQUIDITY", "HOUSING"} and str(exposure_factors.get("financing_sensitivity", "unknown")) in {"medium", "high"}:
        positive_score += 0.5 * len(supportive_hits)
        negative_score += 0.4 * len(restrictive_hits)

    delta = positive_score - negative_score
    if delta >= 0.75:
        impact_direction = "positive"
    elif delta <= -0.75:
        impact_direction = "negative"
    elif positive_score > 0.0 and negative_score > 0.0:
        impact_direction = "mixed"
    else:
        impact_direction = "neutral"

    if not rationale_parts:
        rationale_parts.append("direction inferred from matched policy themes and company exposures")
    return {
        "impact_direction": impact_direction,
        "direction_rationale": "; ".join(rationale_parts[:3]),
        "positive_score": round(positive_score, 2),
        "negative_score": round(negative_score, 2),
    }


def relationship_type_for_link(direct_alias_hit: bool, matched_channels: list[dict[str, Any]]) -> str | None:
    if direct_alias_hit:
        return "direct"
    if matched_channels:
        return "indirect"
    return None


def relationship_confidence_label(confidence: float, coverage_warning: str = "") -> str:
    warning = str(coverage_warning or "").strip()
    if warning == "stale_coverage" and confidence < 0.7:
        return "predicted_only"
    if confidence >= 0.8:
        return "high_confidence"
    if confidence >= 0.65:
        return "confirmed"
    if confidence >= 0.4:
        return "low_confidence"
    if confidence >= 0.2:
        return "predicted_only"
    return "insufficient_data"


def article_source_domain(article: dict[str, Any]) -> str:
    profile = article.get("source_profile", {}) if isinstance(article.get("source_profile", {}), dict) else {}
    candidates = [
        str(article.get("canonical_domain") or "").strip(),
        str(profile.get("canonical_domain") or "").strip(),
        canonicalize_article_url(str(article.get("url") or "")),
    ]
    for candidate in candidates:
        normalized = normalize_domain(candidate)
        if normalized:
            return normalized
    return ""


def corroboration_group_key(article: dict[str, Any], relationship: dict[str, Any]) -> tuple[str, str, str]:
    ticker = normalize_ticker(str(relationship.get("ticker") or ""))
    direction = str(relationship.get("impact_direction") or "neutral").strip().lower() or "neutral"
    policy_channel = str(relationship.get("policy_channel") or "").strip().lower() or "__any__"
    return ticker, direction, policy_channel


def corroboration_multiplier_for_group(supports: list[dict[str, Any]]) -> tuple[float, int, int, int, int]:
    coverage_items = [coverage for item in supports for coverage in item.get("coverage_items", [])]
    raw_coverage_count = max(1, len(coverage_items))
    unique_family_records: dict[str, dict[str, Any]] = {}
    for coverage in coverage_items:
        family_key = str(coverage.get("family_key") or "").strip()
        if not family_key:
            continue
        existing = unique_family_records.get(family_key)
        if existing is None or float(coverage.get("source_quality_score", 0.0) or 0.0) > float(existing.get("source_quality_score", 0.0) or 0.0):
            unique_family_records[family_key] = coverage

    independent_source_count = max(1, len(unique_family_records))
    independent_domain_count = max(1, len({str(item.get("domain_key") or "").strip() for item in unique_family_records.values() if str(item.get("domain_key") or "").strip()}))
    syndicated_coverage_count = max(0, raw_coverage_count - independent_source_count)
    official_count = sum(1 for item in unique_family_records.values() if int(item.get("source_tier", 4) or 4) <= 1)
    avg_quality = (
        sum(float(item.get("source_quality_score", 0.0) or 0.0) for item in unique_family_records.values()) / independent_source_count
        if independent_source_count
        else 0.0
    )

    if independent_source_count <= 1:
        multiplier = 0.66 + (0.18 if official_count else 0.0) + 0.10 * avg_quality + 0.04 * min(independent_domain_count, 2)
    else:
        multiplier = 0.70 + 0.12 * min(independent_source_count, 4) + 0.09 * min(independent_domain_count, 3) + 0.08 * avg_quality + 0.12 * min(official_count, 2)
        if independent_source_count >= 2 and independent_domain_count >= 2:
            multiplier += 0.03
    return clamp(multiplier, 0.55, 1.25), raw_coverage_count, independent_domain_count, independent_source_count, syndicated_coverage_count


def apply_corroboration_to_events(events: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for event in events:
        source_key = canonical_source_key(event)
        source_domain = article_source_domain(event)
        source_tier = int(event.get("source_tier", 4) or 4)
        source_quality_score = float(event.get("source_quality_score", 0.0) or 0.0)
        coverage_items = corroboration_coverage_items(event)
        for relationship in event.get("stock_relationships", []):
            key = corroboration_group_key(event, relationship)
            if not key[0]:
                continue
            groups.setdefault(key, []).append(
                {
                    "event": event,
                    "relationship": relationship,
                    "source_key": source_key,
                    "domain": source_domain,
                    "source_tier": source_tier,
                    "source_quality_score": source_quality_score,
                    "coverage_items": coverage_items,
                }
            )

    for supports in groups.values():
        multiplier, raw_coverage_count, domain_count, source_count, syndicated_coverage_count = corroboration_multiplier_for_group(supports)
        corroboration_score = clamp((multiplier - 0.55) / 0.70, 0.0, 1.0)
        for item in supports:
            relationship = item["relationship"]
            relationship_confidence = clamp(float(relationship.get("relationship_confidence", relationship.get("confidence", 0.0)) or 0.0) * multiplier, 0.0, 1.0)
            evidence_strength = clamp(float(relationship.get("evidence_strength", 0.0) or 0.0) * max(1.0, min(multiplier, 1.15)), 0.0, 1.0)
            relationship.update(
                {
                    "corroboration_count": raw_coverage_count,
                    "raw_coverage_count": raw_coverage_count,
                    "independent_coverage_count": source_count,
                    "syndicated_coverage_count": syndicated_coverage_count,
                    "independent_domain_count": domain_count,
                    "corroboration_domain_count": domain_count,
                    "corroboration_source_count": source_count,
                    "corroboration_multiplier": round(multiplier, 3),
                    "corroboration_score": round(corroboration_score, 3),
                    "corroboration_agreement_score": round(corroboration_score, 3),
                    "corroboration_label": (
                        "official_source"
                        if source_count <= 1 and any(int(item.get("source_tier", 4) or 4) <= 1 for item in supports)
                        else "independently_corroborated"
                        if source_count >= 2 and domain_count >= 2
                        else "corroborated"
                        if source_count > 1
                        else "single_weak_source"
                        if any(int(item.get("source_tier", 4) or 4) >= 4 for item in supports)
                        else "single_source"
                    ),
                    "relationship_confidence": round(relationship_confidence, 3),
                    "confidence": round(relationship_confidence, 3),
                    "evidence_strength": round(evidence_strength, 3),
                    "confidence_label": relationship_confidence_label(relationship_confidence, str(relationship.get("coverage_warning", ""))),
                }
            )


def _source_outcome_history_defaults() -> dict[str, Any]:
    return {"sources": {}}


def normalize_source_outcome_history(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _source_outcome_history_defaults()
    normalized_sources: dict[str, dict[str, Any]] = {}
    for key, value in raw.get("sources", {}).items() if isinstance(raw.get("sources", {}), dict) else {}:
        normalized_key = str(key or "").strip().lower()
        if not normalized_key or not isinstance(value, dict):
            continue
        try:
            sample_size = max(0, int(value.get("sample_size", 0) or 0))
        except Exception:
            sample_size = 0
        try:
            weighted_outcome_sum = float(value.get("weighted_outcome_sum", 0.0) or 0.0)
        except Exception:
            weighted_outcome_sum = 0.0
        normalized_sources[normalized_key] = {
            "sample_size": sample_size,
            "weighted_outcome_sum": weighted_outcome_sum,
        }
    return {"sources": normalized_sources}


def load_source_outcome_history() -> dict[str, Any]:
    try:
        raw = json.loads(SOURCE_OUTCOME_HISTORY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _source_outcome_history_defaults()
    except Exception:
        return _source_outcome_history_defaults()
    return normalize_source_outcome_history(raw)


def save_source_outcome_history(history: dict[str, Any]) -> None:
    normalized = normalize_source_outcome_history(history)
    SOURCE_OUTCOME_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_OUTCOME_HISTORY_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_reliability_history_key(source_name: str = "", url: str = "", source_profile: dict[str, Any] | None = None) -> str:
    profile = source_profile if isinstance(source_profile, dict) else {}
    parsed = urlsplit(url or "")
    candidates = [
        normalize_domain(str(profile.get("canonical_domain") or "")),
        normalize_domain(parsed.netloc or (url if "." in url and "/" not in url else "")),
        normalize_match_text(str(profile.get("canonical_name") or "")),
        normalize_match_text(source_name),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return "unknown"


def source_outcome_weight(validation_status: str, validation_score: float) -> float:
    status = str(validation_status or "unvalidated").strip().lower() or "unvalidated"
    try:
        score = clamp(float(validation_score or 0.0), 0.0, 1.0)
    except Exception:
        score = 0.0
    if status == "confirmed":
        return 0.6 + 0.4 * score
    if status == "rejected":
        return -(0.6 + 0.4 * score)
    if status == "predicted_only":
        return 0.1 * score
    return 0.0


def historical_reliability_metrics(history: dict[str, Any], history_key: str) -> dict[str, Any]:
    sources = history.get("sources", {}) if isinstance(history, dict) else {}
    entry = sources.get(str(history_key or "").strip().lower(), {}) if isinstance(sources, dict) else {}
    try:
        sample_size = max(0, int(entry.get("sample_size", 0) or 0))
    except Exception:
        sample_size = 0
    try:
        weighted_outcome_sum = float(entry.get("weighted_outcome_sum", 0.0) or 0.0)
    except Exception:
        weighted_outcome_sum = 0.0
    reliability_score = clamp(weighted_outcome_sum / sample_size, -1.0, 1.0) if sample_size else 0.0
    stability = clamp(sample_size / 5.0, 0.0, 1.0)
    multiplier = clamp(1.0 + 0.1 * reliability_score * stability, 0.85, 1.15)
    return {
        "historical_reliability_multiplier": round(multiplier, 3),
        "historical_outcome_sample_size": sample_size,
        "historical_reliability_score": round(reliability_score, 3),
    }


def record_source_outcome(history: dict[str, Any], history_key: str, validation_status: str, validation_score: float) -> dict[str, Any]:
    normalized = normalize_source_outcome_history(history)
    key = str(history_key or "").strip().lower()
    if not key or key == "unknown":
        return normalized
    weight = source_outcome_weight(validation_status, validation_score)
    if abs(weight) <= 1e-9:
        return normalized
    entry = normalized.setdefault("sources", {}).setdefault(key, {"sample_size": 0, "weighted_outcome_sum": 0.0})
    sample_size = max(0, int(entry.get("sample_size", 0) or 0))
    weighted_outcome_sum = float(entry.get("weighted_outcome_sum", 0.0) or 0.0)
    if sample_size >= 20:
        sample_size = 19
        weighted_outcome_sum *= 0.95
    entry["sample_size"] = sample_size + 1
    entry["weighted_outcome_sum"] = round(clamp(weighted_outcome_sum + weight, -20.0, 20.0), 4)
    return normalized


def validation_outcome_multiplier(validation_status: str, validation_score: float) -> float:
    status = str(validation_status or "unvalidated").strip().lower() or "unvalidated"
    try:
        score = clamp(float(validation_score or 0.0), 0.0, 1.0)
    except Exception:
        score = 0.0
    base = {
        "confirmed": 1.08,
        "predicted_only": 0.98,
        "insufficient_data": 0.94,
        "rejected": 0.86,
        "unvalidated": 1.0,
    }.get(status, 1.0)
    if status == "confirmed":
        base += 0.04 * score
    elif status == "predicted_only":
        base += 0.02 * score
    elif status == "insufficient_data":
        base -= 0.02 * (1.0 - score)
    elif status == "rejected":
        base -= 0.06 * score
    return round(clamp(base, 0.8, 1.15), 3)


def calibrate_source_confidence_from_validation(
    source_confidence: float,
    validation_status: str,
    validation_score: float,
    historical_reliability_multiplier: float = 1.0,
) -> float:
    try:
        base_confidence = clamp(float(source_confidence or 0.0), 0.0, 1.0)
    except Exception:
        base_confidence = 0.0
    try:
        historical_multiplier = clamp(float(historical_reliability_multiplier or 1.0), 0.85, 1.15)
    except Exception:
        historical_multiplier = 1.0
    multiplier = validation_outcome_multiplier(validation_status, validation_score)
    return round(clamp(base_confidence * historical_multiplier * multiplier, 0.0, 1.0), 3)


def source_conflict_scope_key(event: dict[str, Any], ticker: str) -> str:
    def normalized(value: Any) -> str:
        text = str(value or "").strip().lower()
        return text

    scope_parts = [normalize_ticker(ticker)]
    for value in [event.get("thread_id"), event.get("duplicate_group_id"), event.get("claim_signature")]:
        token = normalized(value)
        if token:
            scope_parts.append(token)
            return "::".join(scope_parts)
    return scope_parts[0]



def apply_source_conflicts_to_events(events: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        source_key = canonical_source_key(event)
        source_domain = article_source_domain(event)
        source_tier = int(event.get("source_tier", 4) or 4)
        for relationship in event.get("stock_relationships", []):
            direction = str(relationship.get("impact_direction") or "neutral").strip().lower() or "neutral"
            if direction not in {"positive", "negative"}:
                continue
            ticker = normalize_ticker(str(relationship.get("ticker") or ""))
            if not ticker:
                continue
            groups.setdefault(source_conflict_scope_key(event, ticker), []).append(
                {
                    "event": event,
                    "relationship": relationship,
                    "direction": direction,
                    "source_key": source_key,
                    "domain": source_domain,
                    "source_tier": source_tier,
                }
            )

    for supports in groups.values():
        positive = [item for item in supports if item["direction"] == "positive"]
        negative = [item for item in supports if item["direction"] == "negative"]
        if not positive or not negative:
            continue
        total_count = len(positive) + len(negative)
        opposing_label = {"positive": len(negative), "negative": len(positive)}
        conflict_score = clamp(min(len(positive), len(negative)) / max(total_count, 1), 0.0, 1.0)
        for item in supports:
            relationship = item["relationship"]
            opposing_count = opposing_label.get(item["direction"], 0)
            penalty = clamp(1.0 - (0.15 * opposing_count) - (0.05 * max(0, total_count - 2)), 0.65, 1.0)
            relationship_confidence = clamp(float(relationship.get("relationship_confidence", relationship.get("confidence", 0.0)) or 0.0) * penalty, 0.0, 1.0)
            evidence_strength = clamp(float(relationship.get("evidence_strength", 0.0) or 0.0) * penalty, 0.0, 1.0)
            current_warning = str(relationship.get("coverage_warning", "")).strip()
            new_warning = current_warning or "source_conflict"
            relationship.update(
                {
                    "source_conflict": True,
                    "source_conflict_count": opposing_count,
                    "source_conflict_total_count": total_count,
                    "source_conflict_score": round(conflict_score, 3),
                    "source_conflict_penalty": round(penalty, 3),
                    "source_conflict_label": "conflicted",
                    "coverage_warning": new_warning,
                    "relationship_confidence": round(relationship_confidence, 3),
                    "confidence": round(relationship_confidence, 3),
                    "evidence_strength": round(evidence_strength, 3),
                    "confidence_label": relationship_confidence_label(relationship_confidence, new_warning),
                }
            )


def build_stock_relationships(
    article: dict[str, Any],
    watchlist: list[str],
    categories: list[str],
    sector_hits: set[str],
    themes: list[dict[str, Any]],
    sentiment_confidence: float,
    window: str = DEFAULT_EVENT_WINDOW,
) -> list[dict[str, Any]]:
    text = article_text(article)
    relationships: list[dict[str, Any]] = []
    recency_hours, _ = recency_weight_for_article(article, window)
    for ticker in watchlist:
        info = STOCK_MASTER.get(ticker)
        if not info:
            continue

        knowledge = company_knowledge_for_ticker(ticker)
        knowledge_alias_hits = [alias for alias in knowledge.get("aliases", []) if alias in text]
        alias_hits = [alias for alias in info["aliases"] if alias in text] + knowledge_alias_hits
        direct_alias_hit = bool(alias_hits)
        if not knowledge and not direct_alias_hit:
            continue

        profile = TICKER_EXPOSURE_PROFILES.get(ticker, {"themes": [], "keywords": []})
        profile_theme_names = set(profile.get("themes", []))
        knowledge_theme_names = set(knowledge.get("policy_exposures", []))
        matched_themes = [theme for theme in themes if theme["name"] in (profile_theme_names | knowledge_theme_names)]
        matched_channels = match_policy_channels(text, knowledge, matched_themes or themes)
        relationship_type = relationship_type_for_link(direct_alias_hit, matched_channels)
        if not relationship_type:
            continue

        exposure = score_company_exposure(knowledge, matched_channels, direct_alias_hit)
        transmission_clarity = 5.0 if direct_alias_hit else clamp(2.5 + 2.0 * float(exposure.get("channel_confidence", 0.0)), 0.0, 5.0)
        company_exposure = float(exposure.get("company_exposure", 0.0))
        if transmission_clarity <= 0.0 or company_exposure <= 0.0:
            continue

        specificity = policy_specificity_score(categories, themes, text)
        timing = max(1.0, min(5.0, 5.0 - recency_hours / max(6.0, event_window_delta(window).total_seconds() / 21600.0)))
        evidence_quality = evidence_quality_score(article, matched_themes or themes, direct_alias_hit, knowledge.get("evidence", []))
        direction = expected_direction_for_company(matched_themes or themes, matched_channels, knowledge)
        source_quality = clamp(float(article.get("source_quality_score", 0.0) or 0.0), 0.0, 1.0)
        source_freshness = clamp(float(article.get("source_freshness_score", 1.0) or 0.0), 0.0, 1.0)
        corroboration = source_corroboration_metrics_for_article(article)
        source_tier = int(corroboration.get("source_tier", article.get("source_tier", 4)) or 4)
        try:
            duplicate_count = max(1, int(article.get("duplicate_count", 1) or 1))
        except Exception:
            duplicate_count = 1
        redundancy_factor = 1.0 / (1.0 + 0.18 * max(0, duplicate_count - 1))
        source_confidence = clamp(0.35 + 0.65 * (source_quality * source_freshness), 0.0, 1.0)
        if source_tier <= 2:
            source_confidence = clamp(source_confidence + 0.05, 0.0, 1.0)

        score = (
            0.24 * specificity
            + 0.26 * transmission_clarity
            + 0.24 * company_exposure
            + 0.14 * timing
            + 0.12 * evidence_quality
        )
        confidence = clamp((score / 5.0) * (0.7 + 0.3 * sentiment_confidence), 0.0, 1.0)
        relationship_confidence = clamp(confidence * source_confidence * redundancy_factor * float(corroboration.get("corroboration_multiplier", 1.0)), 0.0, 1.0)
        evidence_strength = clamp((evidence_quality / 5.0) * source_confidence * redundancy_factor * float(corroboration.get("corroboration_multiplier", 1.0)), 0.0, 1.0)
        confidence_label = relationship_confidence_label(relationship_confidence, str(article.get("coverage_warning", "")))
        if evidence_quality < MIN_EVIDENCE_QUALITY or score < MIN_RELATIONSHIP_SCORE:
            continue

        primary_theme = (matched_themes or themes or [{"channel": "company-specific transmission path", "exposure_type": "company"}])[0]
        policy_channel = matched_channels[0]["channel"] if matched_channels else (knowledge.get("policy_channels") or [primary_theme["channel"]])[0]
        summary = knowledge.get("summary") or ""
        article_source_type = str(article.get("source_type") or infer_source_type(article.get("source", ""), article.get("url", "")))
        article_evidence_rank = round(source_type_rank(article_source_type), 2)
        company_evidence_rank = round(max((float(item.get("quality_rank") or source_type_rank(item.get("source_type"))) for item in knowledge.get("evidence", [])), default=0.0), 2)
        evidence_label = f"{article_source_type} article"
        if direct_alias_hit:
            rationale = f"{company_name_for_ticker(ticker)} is mentioned directly in the article"
        else:
            rationale = f"{ticker} survives through matched transmission paths instead of broad sector overlap"
        evidence = []
        if direct_alias_hit:
            evidence.append("company/entity mentioned in article")
        if matched_themes:
            evidence.append(f"matched policy theme: {matched_themes[0]['name'].replace('_', ' ').title()}")
        if policy_channel:
            evidence.append(f"policy channel: {policy_channel}")
        if direction.get("direction_rationale"):
            evidence.append(f"direction: {direction['direction_rationale']}")
        evidence.append(f"article source tier: {article_source_type} ({article_evidence_rank:.2f})")
        for item in knowledge.get("evidence", [])[:2]:
            evidence.append(f"{item.get('label', 'source')} [{item.get('source_type', 'other')}]: {item.get('url', '')}")

        relationships.append(
            {
                "ticker": ticker,
                "company_name": company_name_for_ticker(ticker),
                "sector": info["sector"],
                "relationship_type": relationship_type,
                "policy_specificity": round(specificity, 2),
                "transmission_clarity": round(transmission_clarity, 2),
                "company_exposure": round(company_exposure, 2),
                "timing": round(timing, 2),
                "evidence_quality": round(evidence_quality, 2),
                "article_source_type": article_source_type,
                "article_evidence_rank": article_evidence_rank,
                "company_evidence_rank": company_evidence_rank,
                "evidence_label": evidence_label,
                "relevance_score": round(score, 2),
                "confidence": round(relationship_confidence, 3),
                "relationship_confidence": round(relationship_confidence, 3),
                "source_confidence": round(source_confidence, 3),
                "evidence_strength": round(evidence_strength, 3),
                "confidence_label": confidence_label,
                "rationale": rationale,
                "policy_channel": policy_channel,
                "matched_policy_channels": matched_channels,
                "channel_confidence": round(float(exposure.get("channel_confidence", 0.0)), 3),
                "impact_direction": direction.get("impact_direction", "neutral"),
                "direction_rationale": direction.get("direction_rationale", ""),
                "exposure_type": primary_theme["exposure_type"],
                "exposure_factors": exposure.get("exposure_factors", {}),
                "knowledge_summary": summary,
                "company_evidence": knowledge.get("evidence", []),
                "evidence": evidence[:7],
                "source_tier": source_tier,
                "raw_coverage_count": corroboration.get("raw_coverage_count", 1),
                "independent_coverage_count": corroboration.get("independent_coverage_count", corroboration.get("corroboration_source_count", 1)),
                "syndicated_coverage_count": corroboration.get("syndicated_coverage_count", 0),
                "independent_domain_count": corroboration.get("independent_domain_count", corroboration.get("corroboration_domain_count", 1)),
                "corroboration_source_count": corroboration.get("corroboration_source_count", 1),
                "corroboration_domain_count": corroboration.get("corroboration_domain_count", 1),
                "corroboration_source_type_count": corroboration.get("corroboration_source_type_count", 1),
                "corroboration_agreement_score": corroboration.get("corroboration_agreement_score", 0.0),
                "corroboration_multiplier": corroboration.get("corroboration_multiplier", 1.0),
                "corroboration_label": corroboration.get("corroboration_label", "single_source"),
                "source_conflict": False,
                "source_conflict_count": 0,
                "source_conflict_total_count": 0,
                "source_conflict_score": 0.0,
                "source_conflict_penalty": 1.0,
                "source_conflict_label": "aligned",
            }
        )

    relationships.sort(
        key=lambda item: (item["relevance_score"], item["confidence"], item["relationship_type"] == "direct"),
        reverse=True,
    )
    return relationships[:8]


def analyze_article(article: dict[str, Any], watchlist: list[str], window: str = DEFAULT_EVENT_WINDOW) -> dict[str, Any]:
    text = article_text(article)
    relevance = score_political_relevance(article)
    stage = detect_event_stage(text)
    reversal = detect_negation_or_reversal(text)
    sentiment, sentiment_score, sentiment_confidence = analyze_sentiment(text)
    categories = classify_categories(text)
    entities = extract_entities(text)
    sector_hits = sector_matches(text)
    for category in categories:
        sector_hits.update(CATEGORY_TO_SECTORS.get(category, []))
    themes = detect_policy_themes(text)
    for theme in themes:
        sector_hits.update(theme["sectors"])

    article_quality = source_quality_metrics_for_article(article)
    article_context = {**article, **article_quality}

    stock_relationships = build_stock_relationships(
        article=article_context,
        watchlist=watchlist,
        categories=categories or ["PARLIAMENT_SESSION"],
        sector_hits=sector_hits,
        themes=themes,
        sentiment_confidence=sentiment_confidence,
        window=window,
    )
    impacted_tickers = [item["ticker"] for item in stock_relationships]

    stage_weight = {
        "proposal": 0.68,
        "debate": 0.8,
        "approved": 1.08,
        "effective": 1.15,
        "enforced": 1.05,
        "delayed": 0.66,
        "revoked": 0.72,
        "unspecified": 0.85,
    }.get(stage.get("event_stage", "unspecified"), 0.85)
    confidence = clamp(
        (
            0.1
            + 0.25 * float(relevance.get("relevance_score", 0.0))
            + 0.08 * len(categories)
            + 0.08 * len(sector_hits)
            + 0.08 * len(entities)
            + 0.1 * len(themes)
            + 0.16 * sentiment_confidence
            + 0.12 * float(stage.get("event_stage_confidence", 0.0))
        ) * stage_weight,
        0.0,
        1.0,
    )
    if article.get("source_weight"):
        confidence = clamp(confidence * float(article["source_weight"]), 0.0, 1.0)
    confidence = clamp(confidence * (0.55 + 0.45 * float(article_context.get("source_quality_score", 0.5))), 0.0, 1.0)

    _, recency_weight = recency_weight_for_article(article, window)
    avg_relevance = sum(link["relevance_score"] for link in stock_relationships) / len(stock_relationships) if stock_relationships else 0.0

    return {
        **article_context,
        "sentiment": sentiment,
        "sentiment_score": round(sentiment_score, 3),
        "relevance_score": relevance.get("relevance_score", 0.0),
        "relevance_label": relevance.get("relevance_label", "not_political"),
        "relevance_signals": relevance.get("relevance_signals", {}),
        "relevance_penalties": relevance.get("relevance_penalties", {}),
        "event_stage": stage.get("event_stage", "unspecified"),
        "event_stage_confidence": stage.get("event_stage_confidence", 0.0),
        "event_stage_signals": stage.get("event_stage_signals", []),
        "is_reversal": reversal.get("is_reversal", False),
        "is_tentative": reversal.get("is_tentative", False),
        "reversal_hits": reversal.get("reversal_hits", []),
        "negation_hits": reversal.get("negation_hits", []),
        "categories": categories or ["PARLIAMENT_SESSION"],
        "entities": entities,
        "policy_themes": [theme["name"] for theme in themes],
        "policy_channels": [theme["channel"] for theme in themes],
        "impacted_sectors": sorted(sector_hits),
        "impacted_tickers": impacted_tickers,
        "stock_relationships": stock_relationships,
        "confidence": round(confidence, 3),
        "recency_weight": round(recency_weight, 3),
        "window": normalize_event_window(window),
        "significance": round((0.35 + abs(sentiment_score) + avg_relevance / 5.0) * float(relevance.get("relevance_score", 0.0)) * confidence * recency_weight * (0.55 + 0.45 * float(article_context.get("source_quality_score", 0.5))) * 0.45, 3),
    }


def compute_ticker_score(article: dict[str, Any], ticker: str) -> float:
    relationship = next((item for item in article.get("stock_relationships", []) if item.get("ticker") == ticker), None)
    if not relationship:
        return 0.0
    sentiment_score = float(article.get("sentiment_score", 0.0))
    relevance_factor = float(relationship.get("relevance_score", 0.0)) / 5.0
    confidence = float(
        relationship.get(
            "relationship_confidence",
            relationship.get("confidence", article.get("confidence", 0.5)),
        )
    )
    source_confidence = float(relationship.get("source_confidence", article.get("source_quality_score", 0.5)))
    evidence_strength = float(relationship.get("evidence_strength", confidence))
    relationship_multiplier = {"direct": 1.0, "indirect": 0.82}.get(relationship.get("relationship_type"), 0.5)
    confidence_multiplier = clamp(0.45 + 0.55 * max(0.0, source_confidence), 0.25, 1.0)
    evidence_multiplier = clamp(0.5 + 0.5 * max(0.0, evidence_strength), 0.25, 1.0)
    validation_multiplier = validation_outcome_multiplier(
        str(relationship.get("validation_status", article.get("validation_status", "unvalidated"))),
        float(relationship.get("validation_score", article.get("validation_score", 0.0)) or 0.0),
    )
    direction = str(relationship.get("impact_direction", "neutral"))
    if direction == "positive":
        directional_sentiment = max(abs(sentiment_score), 0.45)
    elif direction == "negative":
        directional_sentiment = -max(abs(sentiment_score), 0.45)
    elif direction == "mixed":
        directional_sentiment = 0.35 * sentiment_score
    else:
        directional_sentiment = 0.0
    raw = directional_sentiment * relevance_factor * confidence * relationship_multiplier * confidence_multiplier * evidence_multiplier * validation_multiplier
    return clamp(raw, -1.0, 1.0)


# ---------------------------------------------------------------------------
# Refresh orchestration and cache
# ---------------------------------------------------------------------------


def sort_stocks_by_impact(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for index, stock in enumerate(stocks):
        relationship_count = int(stock.get("relationship_count", 0) or 0)
        impact_score = float(stock.get("impact_score", 0.0) or 0.0)
        impacted = relationship_count > 0 or abs(impact_score) > 0.0001
        ranked.append(
            (
                0 if impacted else 1,
                -abs(impact_score),
                -float(relationship_count),
                index,
                stock,
            )
        )
    ranked.sort()
    return [stock for *_, stock in ranked]


def compute_sector_summary(stocks: list[dict[str, Any]]) -> dict[str, float]:
    totals = {sector: 0.0 for sector in SECTORS}
    counts = {sector: 0 for sector in SECTORS}
    for stock in stocks:
        sector = stock.get("sector", "")
        if sector not in totals:
            continue
        totals[sector] += float(stock.get("impact_score", 0.0))
        counts[sector] += 1
    return {
        sector: round(totals[sector] / counts[sector], 3) if counts[sector] else 0.0
        for sector in SECTORS
    }


THREAD_CATEGORY_FAMILIES = {
    "REGULATION_NEW": "REGULATION",
    "REGULATION_REPEAL": "REGULATION",
    "STATE_BUDGET": "FISCAL",
    "MONETARY_SIGNAL": "MONETARY",
    "TRADE_POLICY": "TRADE_POLICY",
    "ENERGY_POLICY": "ENERGY_POLICY",
    "INVESTMENT_POLICY": "INVESTMENT_POLICY",
    "PARLIAMENT_SESSION": "LEGISLATIVE",
}


EVENT_STAGE_ORDER = {
    "proposal": 1,
    "debate": 2,
    "approved": 3,
    "effective": 4,
    "enforced": 5,
    "delayed": 1,
    "revoked": 0,
    "unspecified": 1,
}


THREAD_STATUS_RANK = {
    "active": 1,
    "confirmed": 2,
    "contested": 3,
    "reversed": 4,
}


def normalize_thread_token(value: Any, fallback: str = "general") -> str:
    token = normalize_match_text(value)
    return token.replace(" ", "-") if token else fallback


def thread_category_family(article: dict[str, Any]) -> str:
    categories = article.get("categories", []) if isinstance(article.get("categories", []), list) else []
    for category in categories:
        normalized = str(category or "").strip().upper()
        if normalized:
            return THREAD_CATEGORY_FAMILIES.get(normalized, normalized)
    return "GENERAL"


def thread_institution_label(article: dict[str, Any]) -> str:
    relevance_signals = article.get("relevance_signals", {}) if isinstance(article.get("relevance_signals"), dict) else {}
    institutions = relevance_signals.get("institutions", []) if isinstance(relevance_signals.get("institutions", []), list) else []
    if institutions:
        return str(institutions[0])
    source = str(article.get("source", "")).strip()
    if source:
        return source
    return str(article.get("source_type") or "general")


def thread_entity_label(article: dict[str, Any]) -> str:
    for relationship in article.get("stock_relationships", []):
        if relationship.get("relationship_type") == "direct" and relationship.get("company_name"):
            return str(relationship.get("company_name"))
    impacted_tickers = article.get("impacted_tickers", []) if isinstance(article.get("impacted_tickers", []), list) else []
    if impacted_tickers:
        return str(impacted_tickers[0])
    entities = article.get("entities", []) if isinstance(article.get("entities", []), list) else []
    if entities:
        return str(entities[0])
    return "market"


def thread_focus_label(article: dict[str, Any]) -> str:
    rules = POLICY_SIGNAL_RULES or load_policy_signal_rules()
    focus_terms = rules.get("thread_match_terms", []) if isinstance(rules, dict) else []
    hits = collect_phrase_hits(article_text(article), focus_terms)
    if hits:
        return str(hits[0])
    channels = article.get("policy_channels", []) if isinstance(article.get("policy_channels", []), list) else []
    if channels:
        return str(channels[0])
    return "general"


def build_event_thread_key(article: dict[str, Any]) -> str:
    top_theme = (article.get("policy_themes") or ["general"])[0]
    institution = thread_institution_label(article)
    entity = thread_entity_label(article)
    category_family = thread_category_family(article)
    return "::".join(
        [
            normalize_thread_token(top_theme),
            normalize_thread_token(institution),
            normalize_thread_token(entity),
            normalize_thread_token(category_family.lower()),
        ]
    )


def event_primary_direction(event: dict[str, Any]) -> str:
    directions = [str(item.get("impact_direction", "neutral")) for item in event.get("stock_relationships", []) if item.get("impact_direction")]
    if "negative" in directions and "positive" in directions:
        return "mixed"
    if directions:
        return directions[0]
    sentiment_score = float(event.get("sentiment_score", 0.0))
    if sentiment_score >= 0.2:
        return "positive"
    if sentiment_score <= -0.2:
        return "negative"
    return "neutral"


def summarize_thread_status(thread_events: list[dict[str, Any]]) -> tuple[str, int, str]:
    contradiction_count = 0
    contradiction_reasons: list[str] = []
    seen_positive_progress = False
    seen_negative_progress = False
    previous_direction = None
    previous_stage_rank = None
    for event in thread_events:
        stage = str(event.get("event_stage") or "unspecified")
        stage_rank = EVENT_STAGE_ORDER.get(stage, EVENT_STAGE_ORDER["unspecified"])
        direction = event_primary_direction(event)
        if stage in {"approved", "effective", "enforced"}:
            seen_positive_progress = True
        if stage in {"delayed", "revoked"} or bool(event.get("is_reversal")):
            seen_negative_progress = True
            if seen_positive_progress or previous_stage_rank not in {None, 0, 1}:
                contradiction_count += 1
                contradiction_reasons.append(f"latest coverage weakens earlier thread via {stage}")
        if previous_stage_rank is not None and stage_rank < previous_stage_rank and stage in {"delayed", "revoked", "proposal", "debate"}:
            contradiction_count += 1
            contradiction_reasons.append(f"event stage moved backward to {stage}")
        if previous_direction and direction in {"positive", "negative"} and previous_direction in {"positive", "negative"} and direction != previous_direction:
            contradiction_count += 1
            contradiction_reasons.append(f"impact direction flipped from {previous_direction} to {direction}")
        previous_direction = direction if direction != "mixed" else previous_direction
        previous_stage_rank = stage_rank

    latest_event = thread_events[-1]
    latest_stage = str(latest_event.get("event_stage") or "unspecified")
    if latest_stage in {"delayed", "revoked"} or bool(latest_event.get("is_reversal")):
        status = "reversed"
    elif contradiction_count > 0 or (seen_positive_progress and seen_negative_progress):
        status = "contested"
    elif len(thread_events) >= 2 and latest_stage in {"approved", "effective", "enforced"}:
        status = "confirmed"
    else:
        status = "active"
    summary = contradiction_reasons[0] if contradiction_reasons else ""
    return status, contradiction_count, summary


def group_articles_into_threads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        key = build_event_thread_key(event)
        grouped.setdefault(key, []).append(event)

    thread_summaries: list[dict[str, Any]] = []
    for index, (thread_key, thread_events) in enumerate(grouped.items(), start=1):
        thread_events.sort(key=lambda item: item.get("published_at") or now_wib())
        latest_event = thread_events[-1]
        top_theme = (latest_event.get("policy_themes") or ["general"])[0]
        institution = thread_institution_label(latest_event)
        entity = thread_entity_label(latest_event)
        category_family = thread_category_family(latest_event)
        focus = thread_focus_label(latest_event)
        thread_id = f"thr_{normalize_thread_token(thread_key, fallback=str(index))[:72]}"
        thread_status, contradiction_count, contradiction_summary = summarize_thread_status(thread_events)
        latest_stage = str(latest_event.get("event_stage") or "unspecified")
        headline = str(latest_event.get("headline") or "")
        published_at = latest_event.get("published_at")
        latest_published_at = published_at if isinstance(published_at, datetime) else now_wib()
        summary = {
            "thread_id": thread_id,
            "thread_key": thread_key,
            "thread_status": thread_status,
            "article_count": len(thread_events),
            "latest_event_stage": latest_stage,
            "latest_headline": headline,
            "latest_published_at": latest_published_at,
            "contradiction_count": contradiction_count,
            "contradiction_summary": contradiction_summary,
            "top_theme": top_theme,
            "institution": institution,
            "entity": entity,
            "category": category_family,
            "focus": focus,
        }
        thread_summaries.append(summary)
        for event in thread_events:
            event["thread_id"] = thread_id
            event["thread_status"] = thread_status
            event["thread_key"] = thread_key
            event["thread_contradiction_count"] = contradiction_count
            event["thread_latest_event_stage"] = latest_stage

    thread_summaries.sort(
        key=lambda item: (
            THREAD_STATUS_RANK.get(str(item.get("thread_status")), 0),
            int(item.get("contradiction_count", 0)),
            item.get("latest_published_at") or now_wib(),
            int(item.get("article_count", 0)),
        ),
        reverse=True,
    )
    return thread_summaries




def build_event_tracking(events: list[dict[str, Any]], window: str = DEFAULT_EVENT_WINDOW) -> dict[str, Any]:
    normalized_window = normalize_event_window(window)
    event_threads = group_articles_into_threads(events)
    buckets: dict[str, dict[str, Any]] = {}
    theme_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for event in events:
        published_at = event.get("published_at")
        if isinstance(published_at, datetime):
            bucket_key = published_at.astimezone(WIB).date().isoformat()
        else:
            bucket_key = str(published_at or now_iso())[:10]
        bucket = buckets.setdefault(bucket_key, {"date": bucket_key, "event_count": 0, "total_significance": 0.0, "top_headline": event.get("headline", "")})
        bucket["event_count"] += 1
        bucket["total_significance"] += float(event.get("significance", 0.0))
        if float(event.get("significance", 0.0)) >= float(bucket.get("max_significance", -1.0)):
            bucket["max_significance"] = float(event.get("significance", 0.0))
            bucket["top_headline"] = event.get("headline", "")
        source = str(event.get("source", "")).strip()
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1
        for theme in event.get("policy_themes", []):
            if theme:
                theme_counts[theme] = theme_counts.get(theme, 0) + 1
        for category in event.get("categories", []):
            if category:
                category_counts[category] = category_counts.get(category, 0) + 1

    timeline = []
    for day in sorted(buckets):
        bucket = buckets[day]
        count = max(int(bucket["event_count"]), 1)
        timeline.append(
            {
                "date": day,
                "event_count": bucket["event_count"],
                "avg_significance": round(bucket["total_significance"] / count, 3),
                "max_significance": round(float(bucket.get("max_significance", 0.0)), 3),
                "top_headline": bucket.get("top_headline", ""),
            }
        )

    top_sources = [
        {"name": name, "count": count}
        for name, count in sorted(source_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[:5]
    ]
    top_themes = [
        {"name": name, "count": count}
        for name, count in sorted(theme_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[:5]
    ]
    top_categories = [
        {"name": name, "count": count}
        for name, count in sorted(category_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[:5]
    ]
    strongest_day = max(timeline, key=lambda item: (item["event_count"], item["max_significance"]), default=None)
    total_events = len(events)
    avg_significance = round(sum(float(event.get("significance", 0.0)) for event in events) / total_events, 3) if total_events else 0.0
    contested_thread_count = sum(1 for thread in event_threads if thread.get("thread_status") in {"contested", "reversed"})
    reversed_thread_count = sum(1 for thread in event_threads if thread.get("thread_status") == "reversed")
    return {
        "window": normalized_window,
        "window_label": event_window_label(normalized_window),
        "timeline": timeline,
        "top_sources": top_sources,
        "top_themes": top_themes,
        "top_categories": top_categories,
        "summary": {
            "total_events": total_events,
            "thread_count": len(event_threads),
            "contested_thread_count": contested_thread_count,
            "reversed_thread_count": reversed_thread_count,
            "avg_significance": avg_significance,
            "strongest_day": strongest_day,
            "strongest_theme": top_themes[0] if top_themes else None,
        },
    }



def build_reasoning_summary(events: list[dict[str, Any]], event_threads: list[dict[str, Any]], stocks: list[dict[str, Any]]) -> dict[str, Any]:
    def bump(counts: dict[str, int], key: str, *, fallback: str = "unknown") -> None:
        normalized = str(key or fallback).strip() or fallback
        counts[normalized] = counts.get(normalized, 0) + 1

    relevance_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    thread_counts: dict[str, int] = {}
    validation_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}

    for event in events:
        bump(relevance_counts, event.get("relevance_label"), fallback="not_political")
        bump(stage_counts, event.get("event_stage"), fallback="unspecified")
        for relationship in event.get("stock_relationships", []):
            bump(validation_counts, relationship.get("validation_status"), fallback="unvalidated")
            bump(direction_counts, relationship.get("impact_direction"), fallback="neutral")

    for thread in event_threads:
        bump(thread_counts, thread.get("thread_status"), fallback="active")

    def to_buckets(counts: dict[str, int], *, order: list[str] | None = None) -> list[dict[str, Any]]:
        ordered_keys = order or []
        seen = set()
        buckets: list[dict[str, Any]] = []
        for key in ordered_keys:
            if key in counts:
                buckets.append({"name": key, "count": counts[key]})
                seen.add(key)
        for name, count in sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True):
            if name in seen:
                continue
            buckets.append({"name": name, "count": count})
        return buckets[:5]

    confirmed = validation_counts.get("confirmed", 0)
    predicted = validation_counts.get("predicted_only", 0)
    insufficient = validation_counts.get("insufficient_data", 0)
    contested = thread_counts.get("contested", 0)
    reversed_threads = thread_counts.get("reversed", 0)
    summary_bits = [
        f"{confirmed} confirmed links" if confirmed else None,
        f"{predicted} predicted-only links" if predicted else None,
        f"{insufficient} insufficient-data links" if insufficient else None,
        f"{contested} contested threads" if contested else None,
        f"{reversed_threads} reversed threads" if reversed_threads else None,
    ]
    summary_line = " · ".join(bit for bit in summary_bits if bit) or "No reasoning summary yet"

    return {
        "summary_line": summary_line,
        "relevance_breakdown": to_buckets(relevance_counts, order=["political", "maybe", "not_political"]),
        "stage_breakdown": to_buckets(stage_counts, order=["proposal", "debate", "approved", "effective", "enforced", "delayed", "revoked", "unspecified"]),
        "thread_breakdown": to_buckets(thread_counts, order=["active", "confirmed", "contested", "reversed"]),
        "validation_breakdown": to_buckets(validation_counts, order=["confirmed", "predicted_only", "rejected", "insufficient_data", "unvalidated"]),
        "direction_breakdown": to_buckets(direction_counts, order=["positive", "negative", "neutral", "mixed"]),
    }


def build_dashboard_cues(payload: dict[str, Any]) -> dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    source_health = normalized_payload.get("source_health_summary", {}) if isinstance(normalized_payload.get("source_health_summary", {}), dict) else {}
    reasoning = normalized_payload.get("reasoning_summary", {}) if isinstance(normalized_payload.get("reasoning_summary", {}), dict) else {}
    stocks = normalized_payload.get("stocks", []) if isinstance(normalized_payload.get("stocks", []), list) else []
    events = normalized_payload.get("events", []) if isinstance(normalized_payload.get("events", []), list) else []

    conflicted_relationship_count = int(source_health.get("conflicted_relationship_count", 0) or 0)
    weak_single_source_relationship_count = int(source_health.get("weak_single_source_relationship_count", 0) or 0)
    fallback_source_count = int(source_health.get("fallback_source_count", 0) or 0)
    stale_event_count = int(source_health.get("stale_event_count", 0) or 0)
    thin_event_count = int(source_health.get("thin_event_count", 0) or 0)
    duplicated_event_count = int(source_health.get("duplicated_event_count", 0) or 0)
    displayed_event_count = int(source_health.get("displayed_event_count", normalized_payload.get("displayed_event_count", 0) or 0) or 0)
    relationship_count = int(source_health.get("relationship_count", 0) or 0)
    source_count = int(source_health.get("source_count", 0) or 0)

    historical_sample_count = sum(
        1
        for stock in stocks
        if isinstance(stock, dict) and int(stock.get("historical_outcome_sample_size", 0) or 0) > 0
    )
    contested_thread_count = sum(
        1
        for event in events
        if isinstance(event, dict) and str(event.get("thread_status") or "").strip().lower() in {"contested", "reversed"}
    )
    confirmed_count = next(
        (int(item.get("count", 0) or 0) for item in reasoning.get("validation_breakdown", []) if str(item.get("name") or "") == "confirmed"),
        0,
    ) if isinstance(reasoning.get("validation_breakdown", []), list) else 0
    predicted_count = next(
        (int(item.get("count", 0) or 0) for item in reasoning.get("validation_breakdown", []) if str(item.get("name") or "") == "predicted_only"),
        0,
    ) if isinstance(reasoning.get("validation_breakdown", []), list) else 0

    status = "healthy"
    if conflicted_relationship_count > 0 or contested_thread_count > 0:
        status = "fragile"
    elif weak_single_source_relationship_count > 0 or fallback_source_count > 0 or stale_event_count > 0 or thin_event_count > 0 or duplicated_event_count > 0 or predicted_count > confirmed_count:
        status = "watch"

    chips: list[dict[str, Any]] = []
    if conflicted_relationship_count > 0:
        chips.append({"label": f"{conflicted_relationship_count} conflicting signal{'s' if conflicted_relationship_count != 1 else ''}", "tone": "neg"})
    if contested_thread_count > 0:
        chips.append({"label": f"{contested_thread_count} contested thread{'s' if contested_thread_count != 1 else ''}", "tone": "warn"})
    if weak_single_source_relationship_count > 0:
        chips.append({"label": f"{weak_single_source_relationship_count} weak single-source link{'s' if weak_single_source_relationship_count != 1 else ''}", "tone": "warn"})
    if fallback_source_count > 0:
        chips.append({"label": f"{fallback_source_count} fallback source profile{'s' if fallback_source_count != 1 else ''}", "tone": "warn"})
    if stale_event_count > 0:
        chips.append({"label": f"{stale_event_count} stale event{'s' if stale_event_count != 1 else ''}", "tone": "warn"})
    if historical_sample_count > 0:
        chips.append({"label": f"{historical_sample_count} source histor{'y' if historical_sample_count == 1 else 'ies'} calibrated", "tone": "info"})
    if confirmed_count > 0:
        chips.append({"label": f"{confirmed_count} confirmed link{'s' if confirmed_count != 1 else ''}", "tone": "pos"})
    if not chips:
        chips.append({"label": "No major robustness alerts", "tone": "muted"})

    if status == "fragile":
        headline = "Robustness signals need caution: conflicts or contested threads are affecting the batch."
    elif status == "watch":
        headline = "Source mix is usable but still leaning on thin, fallback, or predicted-only evidence."
    else:
        headline = "Coverage looks healthy: stronger source support with no major robustness alerts in the current batch."

    return {
        "headline": headline,
        "status": status,
        "chips": chips[:5],
        "counts": {
            "displayed_event_count": displayed_event_count,
            "relationship_count": relationship_count,
            "source_count": source_count,
            "conflicted_relationship_count": conflicted_relationship_count,
            "weak_single_source_relationship_count": weak_single_source_relationship_count,
            "fallback_source_count": fallback_source_count,
            "contested_thread_count": contested_thread_count,
            "historical_sample_count": historical_sample_count,
            "stale_event_count": stale_event_count,
            "thin_event_count": thin_event_count,
            "duplicated_event_count": duplicated_event_count,
        },
    }


def _background_refresh(
    cache_key: tuple,
    tickers: list[str],
    window: str,
    news_fetcher: Callable | None,
    stock_fetcher: Callable | None,
    market_fetcher: Callable | None,
) -> None:
    """Rebuild cache in background (stale-while-revalidate)."""
    try:
        build_refresh_payload(
            tickers, force=True, window=window,
            news_fetcher=news_fetcher, stock_fetcher=stock_fetcher, market_fetcher=market_fetcher,
        )
    except Exception:
        pass  # keep stale cache alive


def build_refresh_payload(
    tickers: list[str],
    force: bool = False,
    window: str = DEFAULT_EVENT_WINDOW,
    news_fetcher: Callable[[], tuple[list[dict[str, Any]], list[str]]] | None = None,
    stock_fetcher: Callable[[list[str]], tuple[dict[str, dict[str, Any]], list[str]]] | None = None,
    market_fetcher: Callable[[], tuple[dict[str, Any], list[str]]] | None = None,
) -> dict[str, Any]:
    normalized_window = normalize_event_window(window)
    requested = [normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)]
    if not requested:
        requested = get_watchlist()
    cache_key = (normalized_window, *sorted(requested))

    with CACHE_LOCK:
        cached = CACHE.get(cache_key)
        if cached:
            age = (now_wib() - cached["cached_at"]).total_seconds()
            if age <= CACHE_TTL_SECONDS and not force:
                payload = json.loads(json.dumps(cached["payload"], default=str))
                payload["from_cache"] = True
                payload["cache_key"] = list(cache_key)
                payload["window"] = normalized_window
                payload["window_label"] = event_window_label(normalized_window)
                return payload
            # Stale refresh: return stale data immediately,
            # refresh in background so the user never sees a 502.
            # Skip this when force=True — caller wants a fresh computation.
            if cached["payload"] and not force:
                payload = json.loads(json.dumps(cached["payload"], default=str))
                payload["from_cache"] = True
                payload["stale"] = True
                payload["cache_key"] = list(cache_key)
                payload["window"] = normalized_window
                payload["window_label"] = event_window_label(normalized_window)
                _bg_key = cache_key
                _bg_tickers = list(requested)
                _bg_window = normalized_window
                _bg_nf = news_fetcher
                _bg_sf = stock_fetcher
                _bg_mf = market_fetcher
                threading.Thread(
                    target=_background_refresh,
                    args=(_bg_key, _bg_tickers, _bg_window, _bg_nf, _bg_sf, _bg_mf),
                    daemon=True,
                ).start()
                return payload

    news_fetcher = news_fetcher or fetch_news_bundle
    stock_fetcher = stock_fetcher or fetch_stock_quotes
    market_fetcher = market_fetcher or fetch_market_index

    live_articles, news_warnings, source_diagnostics = unpack_news_fetch_result(news_fetcher())
    articles = dedupe_articles(live_articles, normalized_window)
    watchlist = list(dict.fromkeys(requested))
    analyzed_articles = [analyze_article(article, watchlist, normalized_window) for article in articles]
    analyzed_articles.sort(key=lambda article: (article.get("significance", 0.0), article.get("published_at") or now_wib()), reverse=True)
    meaningful_events = [article for article in analyzed_articles if float(article.get("significance", 0.0)) > 0.015]
    ranked_events = meaningful_events or analyzed_articles
    event_threads = group_articles_into_threads(ranked_events)
    events = ranked_events[:10]
    apply_corroboration_to_events(events)
    apply_source_conflicts_to_events(events)

    quotes, stock_warnings = stock_fetcher(watchlist)
    market_index, market_warnings = market_fetcher()
    validation_warnings: list[str] = []
    validation_cache: dict[tuple[str, str], dict[str, Any]] = {}
    source_outcome_history = load_source_outcome_history()
    updated_source_outcome_history = source_outcome_history
    for event in events:
        history_key = source_reliability_history_key(
            str(event.get("source") or ""),
            str(event.get("url") or ""),
            event.get("source_profile", {}) if isinstance(event.get("source_profile", {}), dict) else {},
        )
        history_metrics = historical_reliability_metrics(source_outcome_history, history_key)
        for relationship in event.get("stock_relationships", []):
            ticker = normalize_ticker(relationship.get("ticker", ""))
            validation = validate_market_reaction(
                event,
                ticker,
                quotes.get(ticker),
                relationship,
                series_cache=validation_cache,
            )
            validation_status = str(validation.get("validation_status", "unvalidated"))
            validation_score = float(validation.get("validation_score", 0.0) or 0.0)
            validation["validation_multiplier"] = validation_outcome_multiplier(
                validation_status,
                validation_score,
            )
            relationship.update(validation)
            relationship.update(history_metrics)
            relationship["source_confidence"] = calibrate_source_confidence_from_validation(
                relationship.get("source_confidence", event.get("source_quality_score", 0.5)),
                validation_status,
                validation_score,
                historical_reliability_multiplier=float(history_metrics.get("historical_reliability_multiplier", 1.0) or 1.0),
            )
            updated_source_outcome_history = record_source_outcome(
                updated_source_outcome_history,
                history_key,
                validation_status,
                validation_score,
            )
            for warning in validation.get("validation_warnings", []):
                if warning:
                    validation_warnings.append(f"{ticker} validation: {warning}")
    if updated_source_outcome_history != source_outcome_history:
        save_source_outcome_history(updated_source_outcome_history)
    stocks: list[dict[str, Any]] = []
    for ticker in watchlist:
        quote = quotes.get(ticker)
        related_links = []
        for idx, event in enumerate(events):
            link = next((item for item in event.get("stock_relationships", []) if item.get("ticker") == ticker), None)
            if link:
                related_links.append((f"evt_{idx+1:03d}", link))
        related_ids = [event_id for event_id, _ in related_links]
        score_inputs = [compute_ticker_score(event, ticker) for event in events]
        recency_weights = [float(event.get("recency_weight", 1.0)) for event in events]
        weighted_total = sum(score * weight for score, weight in zip(score_inputs, recency_weights))
        total_weight = sum(recency_weights) or 1.0
        impact_score = clamp(weighted_total / total_weight, -1.0, 1.0)
        strongest_link = max(related_links, key=lambda item: item[1].get("relevance_score", 0.0), default=None)
        knowledge = company_knowledge_for_ticker(ticker)
        stocks.append(
            {
                "ticker": ticker,
                "name": (quote or {}).get("name") or company_name_for_ticker(ticker),
                "sector": (quote or {}).get("sector") or sector_for_ticker(ticker),
                "price": (quote or {}).get("price"),
                "change_pct": (quote or {}).get("change_pct"),
                "volume": (quote or {}).get("volume"),
                "after_hours": bool((quote or {}).get("after_hours")),
                "impact_score": round(impact_score, 3),
                "related_event_ids": related_ids,
                "relationship_count": len(related_links),
                "relationship_type": strongest_link[1].get("relationship_type") if strongest_link else None,
                "relevance_score": strongest_link[1].get("relevance_score") if strongest_link else None,
                "confidence": strongest_link[1].get("confidence") if strongest_link else 0.0,
                "relationship_confidence": strongest_link[1].get("relationship_confidence") if strongest_link else 0.0,
                "confidence_label": strongest_link[1].get("confidence_label") if strongest_link else "insufficient_data",
                "source_confidence": strongest_link[1].get("source_confidence") if strongest_link else 0.0,
                "evidence_strength": strongest_link[1].get("evidence_strength") if strongest_link else 0.0,
                "rationale": strongest_link[1].get("rationale") if strongest_link else "No evidence-backed political link in current batch.",
                "policy_channel": strongest_link[1].get("policy_channel") if strongest_link else None,
                "matched_policy_channels": strongest_link[1].get("matched_policy_channels") if strongest_link else [],
                "channel_confidence": strongest_link[1].get("channel_confidence") if strongest_link else 0.0,
                "impact_direction": strongest_link[1].get("impact_direction") if strongest_link else "neutral",
                "direction_rationale": strongest_link[1].get("direction_rationale") if strongest_link else "",
                "exposure_factors": strongest_link[1].get("exposure_factors") if strongest_link else knowledge.get("exposure_factors", {}),
                "knowledge_summary": strongest_link[1].get("knowledge_summary") if strongest_link else knowledge.get("summary", ""),
                "company_evidence": strongest_link[1].get("company_evidence") if strongest_link else knowledge.get("evidence", []),
                "article_source_type": strongest_link[1].get("article_source_type") if strongest_link else None,
                "article_evidence_rank": strongest_link[1].get("article_evidence_rank") if strongest_link else None,
                "company_evidence_rank": strongest_link[1].get("company_evidence_rank") if strongest_link else max((item.get("quality_rank", 0.0) for item in knowledge.get("evidence", [])), default=0.0),
                "evidence_label": strongest_link[1].get("evidence_label") if strongest_link else None,
                "source_tier": strongest_link[1].get("source_tier") if strongest_link else None,
                "corroboration_source_count": strongest_link[1].get("corroboration_source_count") if strongest_link else 0,
                "corroboration_domain_count": strongest_link[1].get("corroboration_domain_count") if strongest_link else 0,
                "corroboration_source_type_count": strongest_link[1].get("corroboration_source_type_count") if strongest_link else 0,
                "corroboration_agreement_score": strongest_link[1].get("corroboration_agreement_score") if strongest_link else 0.0,
                "corroboration_multiplier": strongest_link[1].get("corroboration_multiplier") if strongest_link else 1.0,
                "corroboration_label": strongest_link[1].get("corroboration_label") if strongest_link else "single_source",
                "corroboration_count": strongest_link[1].get("corroboration_count") if strongest_link else 0,
                "corroboration_score": strongest_link[1].get("corroboration_score") if strongest_link else 0.0,
                "validation_status": strongest_link[1].get("validation_status") if strongest_link else "unvalidated",
                "validation_window": strongest_link[1].get("validation_window") if strongest_link else None,
                "abnormal_return": strongest_link[1].get("abnormal_return") if strongest_link else 0.0,
                "abnormal_volume_ratio": strongest_link[1].get("abnormal_volume_ratio") if strongest_link else 0.0,
                "validation_score": strongest_link[1].get("validation_score") if strongest_link else 0.0,
                "validation_multiplier": strongest_link[1].get("validation_multiplier") if strongest_link else 1.0,
                "historical_reliability_multiplier": strongest_link[1].get("historical_reliability_multiplier") if strongest_link else 1.0,
                "historical_outcome_sample_size": strongest_link[1].get("historical_outcome_sample_size") if strongest_link else 0,
                "historical_reliability_score": strongest_link[1].get("historical_reliability_score") if strongest_link else 0.0,
                "validation_reason": strongest_link[1].get("validation_reason") if strongest_link else "",
                "source_conflict": strongest_link[1].get("source_conflict") if strongest_link else False,
                "source_conflict_count": strongest_link[1].get("source_conflict_count") if strongest_link else 0,
                "source_conflict_total_count": strongest_link[1].get("source_conflict_total_count") if strongest_link else 0,
                "source_conflict_score": strongest_link[1].get("source_conflict_score") if strongest_link else 0.0,
                "source_conflict_penalty": strongest_link[1].get("source_conflict_penalty") if strongest_link else 1.0,
                "source_conflict_label": strongest_link[1].get("source_conflict_label") if strongest_link else "aligned",
                "source": (quote or {}).get("source", "unavailable"),
            }
        )
    stocks = sort_stocks_by_impact(stocks)

    event_id_map = {f"evt_{idx+1:03d}": event for idx, event in enumerate(events)}
    formatted_events = []
    for event_id, event in event_id_map.items():
        formatted_events.append(
            {
                "id": event_id,
                "headline": event.get("headline", ""),
                "source": event.get("source", ""),
                "source_type": event.get("source_type") or infer_source_type(event.get("source", ""), event.get("url", "")),
                "url": event.get("url", ""),
                "published_at": event.get("published_at").isoformat(timespec="seconds") if isinstance(event.get("published_at"), datetime) else str(event.get("published_at")),
                "categories": event.get("categories", []),
                "sentiment": event.get("sentiment", "neutral"),
                "sentiment_score": event.get("sentiment_score", 0.0),
                "impacted_sectors": event.get("impacted_sectors", []),
                "impacted_tickers": event.get("impacted_tickers", []),
                "policy_themes": event.get("policy_themes", []),
                "policy_channels": event.get("policy_channels", []),
                "stock_relationships": event.get("stock_relationships", []),
                "event_stage": event.get("event_stage", "unspecified"),
                "thread_id": event.get("thread_id"),
                "thread_status": event.get("thread_status", "active"),
                "thread_contradiction_count": event.get("thread_contradiction_count", 0),
                "confidence": event.get("confidence", 0.0),
                "window": normalized_window,
                "significance": event.get("significance", 0.0),
                "source_age_hours": event.get("source_age_hours", 0.0),
                "source_freshness_score": event.get("source_freshness_score", 0.0),
                "source_quality_score": event.get("source_quality_score", 0.0),
                "coverage_warning": event.get("coverage_warning", ""),
            }
        )

    sector_summary = compute_sector_summary(stocks)
    tracking = build_event_tracking(ranked_events, normalized_window)
    reasoning_summary = build_reasoning_summary(events, event_threads, stocks)
    warnings = news_warnings + stock_warnings + market_warnings + validation_warnings
    coverage_warnings = sorted({str(event.get("coverage_warning", "")).strip() for event in events if str(event.get("coverage_warning", "")).strip()})
    if "stale_coverage" in coverage_warnings:
        warnings.append("Some article coverage is stale; fresher evidence would improve confidence.")
    if "thin_source_coverage" in coverage_warnings:
        warnings.append("Some article coverage is thin; the current thread may need more independent sources.")
    if "duplicated_coverage" in coverage_warnings:
        warnings.append("Some article coverage is duplicated across mirrored sources.")
    if any(bool(relationship.get("source_conflict")) for event in events for relationship in event.get("stock_relationships", [])):
        warnings.append("Some article coverage is conflicting across sources.")
    if not articles:
        warnings.append("No live articles available.")
    if not quotes:
        warnings.append("No live stock quotes available.")

    sources = source_diagnostics or summarize_source_diagnostics_from_articles(articles)
    source_health_summary = build_source_health_summary(sources, formatted_events)
    payload = {
        "fetched_at": now_iso(),
        "from_cache": False,
        "cache_key": list(cache_key),
        "window": normalized_window,
        "window_label": event_window_label(normalized_window),
        "watchlist": watchlist,
        "events": formatted_events,
        "event_threads": [
            {
                **thread,
                "latest_published_at": thread.get("latest_published_at").isoformat(timespec="seconds") if isinstance(thread.get("latest_published_at"), datetime) else str(thread.get("latest_published_at") or ""),
            }
            for thread in event_threads
        ],
        "displayed_event_count": len(formatted_events),
        "total_event_count": len(ranked_events),
        "hidden_event_count": max(0, len(ranked_events) - len(formatted_events)),
        "reasoning_summary": reasoning_summary,
        "stocks": stocks,
        "sector_summary": sector_summary,
        "tracking": tracking,
        "market_index": market_index,
        "sources": sources,
        "source_health_summary": source_health_summary,
        "warnings": warnings,
    }

    with CACHE_LOCK:
        CACHE[cache_key] = {"cached_at": now_wib(), "payload": payload}

    return payload


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------


def read_frontend_html() -> str:
    if FRONTEND_FILE.exists():
        return FRONTEND_FILE.read_text(encoding="utf-8")
    return "<html><body><h1>Politics Stock Mapper</h1></body></html>"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    # Serve the dashboard file directly so the browser sees the exact UI
    # the user edited, without any backend rewriting or template wrapping.
    return FileResponse(FRONTEND_FILE, media_type="text/html", headers={"Cache-Control": "no-store"})


@app.head("/")
def index_head() -> Response:
    # Some ingress/health checks probe the site with HEAD; keep them green.
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "time": now_iso()}


@app.head("/healthz")
def healthz_head() -> Response:
    return Response(status_code=200)


@app.head("/api/dashboard")
def api_dashboard_head() -> Response:
    return Response(status_code=200)


@app.head("/api/ticker/{ticker}")
def api_ticker_head(ticker: str) -> Response:
    return Response(status_code=200)


@app.get("/api/watchlist")
def api_get_watchlist() -> dict[str, Any]:
    return {"tickers": get_watchlist()}


@app.get("/api/dashboard")
def api_dashboard(window: str = DEFAULT_EVENT_WINDOW) -> dict[str, Any]:
    watchlist = get_watchlist()
    payload = build_refresh_payload(watchlist, force=False, window=window)
    dashboard_cues = build_dashboard_cues(payload)
    return {"watchlist": watchlist, "reasoning_summary": payload.get("reasoning_summary", {}), "dashboard_cues": dashboard_cues, "payload": payload}


@app.get("/api/ticker/{ticker}")
def api_ticker_detail(ticker: str, window: str = DEFAULT_EVENT_WINDOW) -> dict[str, Any]:
    return fetch_ticker_history(ticker, window)


@app.put("/api/watchlist")
def api_put_watchlist(payload: WatchlistRequest) -> dict[str, Any]:
    tickers = set_watchlist(payload.tickers)
    return {"tickers": tickers}


@app.post("/api/refresh")
def api_refresh(payload: RefreshRequest) -> JSONResponse:
    result = build_refresh_payload(payload.tickers, force=payload.force, window=payload.window)
    return JSONResponse(result)


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------


def reset_runtime_state() -> None:
    with WATCHLIST_LOCK:
        WATCHLIST_STATE[:] = list(DEFAULT_WATCHLIST)
    try:
        save_watchlist_to_disk(WATCHLIST_STATE)
    except Exception:
        pass
    COMPANY_KNOWLEDGE.clear()
    COMPANY_KNOWLEDGE.update(load_company_knowledge_from_disk())
    POLICY_SIGNAL_RULES.clear()
    POLICY_SIGNAL_RULES.update(load_policy_signal_rules())
    MARKET_VALIDATION_CONFIG.clear()
    MARKET_VALIDATION_CONFIG.update(load_market_validation_config())
    SOURCE_REGISTRY.clear()
    SOURCE_REGISTRY.update(load_source_registry())
    with CACHE_LOCK:
        CACHE.clear()


@app.on_event("startup")
def _prewarm_cache() -> None:
    """Warm the cache in background so the first request is never cold."""
    threading.Thread(
        target=lambda: build_refresh_payload(get_watchlist(), force=True, window=DEFAULT_EVENT_WINDOW),
        daemon=True,
    ).start()


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("backend.main:app", host=host, port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
