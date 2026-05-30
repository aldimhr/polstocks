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
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_FILE = PROJECT_ROOT / "dashboard.html"
WATCHLIST_FILE = PROJECT_ROOT / "watchlist.json"

APP_TITLE = "Indonesia Political-Stock Impact System"
CACHE_TTL_SECONDS = 300
FRESH_ARTICLE_WINDOW = timedelta(hours=24)
SOURCE_TIMEOUT_SECONDS = 5
WIB = timezone(timedelta(hours=7))
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Hermes Political-Stock Mapper; +https://hermes-agent.nousresearch.com)"
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


class RefreshRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)
    force: bool = False


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


def clamp(value: float, low: float = -1.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def text_similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, left.lower().strip(), right.lower().strip()).ratio()


def is_stale_article(published_at: datetime | None) -> bool:
    if not published_at:
        return False
    return now_wib() - published_at > FRESH_ARTICLE_WINDOW


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


def is_relevant_article(article: dict[str, Any]) -> bool:
    text = article_text(article)
    return any(keyword in text for keyword in POLITICAL_SIGNAL_KEYWORDS)


def source_weight(source_name: str) -> float:
    for source in NEWS_SOURCES:
        if source["name"].lower() == source_name.lower():
            return float(source["weight"])
    return 0.7


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


# ---------------------------------------------------------------------------
# News fetching
# ---------------------------------------------------------------------------


def parse_rss_items(source: dict[str, Any], xml_text: str) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    root = ET.fromstring(xml_text)
    items = list(root.findall(".//item"))
    if not items:
        items = list(root.findall(".//{*}item"))
    for item in items[:20]:
        title = safe_text(item, "title")
        link = safe_text(item, "link")
        summary = safe_text(item, "description") or safe_text(item, "encoded")
        published_at = parse_datetime(safe_text(item, "pubDate") or safe_text(item, "date"))
        if not title:
            continue
        articles.append(
            {
                "source": source["name"],
                "headline": strip_tags(title),
                "url": html.unescape(link) if link else source["url"],
                "published_at": published_at or now_wib(),
                "summary": strip_tags(summary),
                "source_weight": float(source["weight"]),
            }
        )
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

    base_url = source["url"].rstrip("/")
    domain_match = re.match(r"https?://([^/]+)", base_url)
    domain = domain_match.group(1) if domain_match else ""
    candidates: list[dict[str, Any]] = []

    anchor_pattern = re.compile(r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', flags=re.I | re.S)
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
            "published_at": now_wib(),
            "summary": description or page_title or title,
            "source_weight": float(source["weight"]),
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


def fetch_source(source: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    try:
        response = requests.get(source["url"], timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
        response.raise_for_status()
        if source["kind"] == "rss":
            return parse_rss_items(source, response.text), None
        return parse_html_signal(source, response.text), None
    except Exception as exc:  # pragma: no cover - network failures are expected in some environments
        return [], f"{source['name']}: {exc}"


def fetch_news_bundle() -> tuple[list[dict[str, Any]], list[str]]:
    articles: list[dict[str, Any]] = []
    warnings: list[str] = []
    with ThreadPoolExecutor(max_workers=min(8, len(NEWS_SOURCES))) as pool:
        futures = {pool.submit(fetch_source, source): source for source in NEWS_SOURCES}
        for future in as_completed(futures):
            source_articles, warning = future.result()
            if warning:
                warnings.append(warning)
            articles.extend(source_articles)

    articles = [article for article in articles if is_relevant_article(article)]
    if not articles:
        warnings.append("No live news articles available right now.")
    return articles, warnings


def dedupe_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [article for article in articles if not is_stale_article(article.get("published_at"))]
    filtered.sort(key=lambda article: article.get("published_at") or now_wib(), reverse=True)
    unique: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for article in filtered:
        url = (article.get("url") or "").strip().lower()
        headline = article.get("headline", "")
        if url and url in seen_urls:
            continue
        duplicate = False
        for existing in unique:
            if url and url == (existing.get("url") or "").strip().lower():
                duplicate = True
                break
            if text_similarity(headline, existing.get("headline", "")) > 0.9:
                duplicate = True
                break
        if duplicate:
            continue
        if url:
            seen_urls.add(url)
        unique.append(article)
    return unique


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


def impacted_tickers_from_text(text: str, sector_hits: set[str]) -> list[str]:
    tickers: list[str] = []
    for ticker, info in STOCK_MASTER.items():
        if any(alias in text for alias in info["aliases"]):
            tickers.append(ticker)
            continue
        if info["sector"] in sector_hits and len(tickers) < 8:
            tickers.append(ticker)
    return tickers[:8]


def analyze_article(article: dict[str, Any], watchlist: list[str]) -> dict[str, Any]:
    text = article_text(article)
    sentiment, sentiment_score, sentiment_confidence = analyze_sentiment(text)
    categories = classify_categories(text)
    entities = extract_entities(text)
    sector_hits = sector_matches(text)
    for category in categories:
        sector_hits.update(CATEGORY_TO_SECTORS.get(category, []))

    impacted_tickers = impacted_tickers_from_text(text, sector_hits)
    for ticker in watchlist:
        info = STOCK_MASTER.get(ticker)
        if not info:
            continue
        if ticker in impacted_tickers:
            continue
        if info["sector"] in sector_hits and len(impacted_tickers) < 10:
            impacted_tickers.append(ticker)

    confidence = clamp(
        0.25
        + 0.12 * len(categories)
        + 0.1 * len(sector_hits)
        + 0.08 * len(entities)
        + 0.2 * sentiment_confidence,
        0.0,
        1.0,
    )
    if article.get("source_weight"):
        confidence = clamp(confidence * float(article["source_weight"]), 0.0, 1.0)

    recency_hours = 0.0
    published_at = article.get("published_at") or now_wib()
    if isinstance(published_at, datetime):
        recency_hours = max(0.0, (now_wib() - published_at).total_seconds() / 3600.0)
    recency_weight = max(0.25, 1.0 - recency_hours / 24.0)

    return {
        **article,
        "sentiment": sentiment,
        "sentiment_score": round(sentiment_score, 3),
        "categories": categories or ["PARLIAMENT_SESSION"],
        "entities": entities,
        "impacted_sectors": sorted(sector_hits),
        "impacted_tickers": impacted_tickers,
        "confidence": round(confidence, 3),
        "recency_weight": round(recency_weight, 3),
        "significance": round(abs(sentiment_score) * confidence * recency_weight * max(1, len(sector_hits)) * 0.6, 3),
    }


def compute_ticker_score(article: dict[str, Any], ticker: str) -> float:
    info = STOCK_MASTER.get(ticker)
    if not info:
        return 0.0
    text = article_text(article)
    sector_hits = set(article.get("impacted_sectors", []))
    category_sectors = set()
    for category in article.get("categories", []):
        category_sectors.update(CATEGORY_TO_SECTORS.get(category, []))
    sector_relevance = 0.0
    if info["sector"] in sector_hits:
        sector_relevance = 1.0
    elif info["sector"] in category_sectors:
        sector_relevance = 0.5
    elif any(keyword in text for keyword in SECTOR_KEYWORDS.get(info["sector"], [])):
        sector_relevance = 1.0
    else:
        sector_relevance = 0.2 if ticker in article.get("impacted_tickers", []) else 0.0

    entity_boost = 1.5 if any(alias in text for alias in info["aliases"]) else 1.0
    if ticker in article.get("impacted_tickers", []):
        entity_boost = max(entity_boost, 1.3)

    model_confidence = float(article.get("confidence", 0.5))
    sentiment_score = float(article.get("sentiment_score", 0.0))
    raw = sentiment_score * sector_relevance * entity_boost * model_confidence
    return clamp(raw, -1.0, 1.0)


# ---------------------------------------------------------------------------
# Refresh orchestration and cache
# ---------------------------------------------------------------------------


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


def build_refresh_payload(
    tickers: list[str],
    force: bool = False,
    news_fetcher: Callable[[], tuple[list[dict[str, Any]], list[str]]] | None = None,
    stock_fetcher: Callable[[list[str]], tuple[dict[str, dict[str, Any]], list[str]]] | None = None,
    market_fetcher: Callable[[], tuple[dict[str, Any], list[str]]] | None = None,
) -> dict[str, Any]:
    requested = [normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)]
    if not requested:
        requested = get_watchlist()
    cache_key = tuple(sorted(requested))

    with CACHE_LOCK:
        cached = CACHE.get(cache_key)
        if cached and not force:
            age = (now_wib() - cached["cached_at"]).total_seconds()
            if age <= CACHE_TTL_SECONDS:
                payload = json.loads(json.dumps(cached["payload"], default=str))
                payload["from_cache"] = True
                payload["cache_key"] = list(cache_key)
                return payload

    news_fetcher = news_fetcher or fetch_news_bundle
    stock_fetcher = stock_fetcher or fetch_stock_quotes
    market_fetcher = market_fetcher or fetch_market_index

    live_articles, news_warnings = news_fetcher()
    articles = dedupe_articles(live_articles)
    watchlist = list(dict.fromkeys(requested))
    analyzed_articles = [analyze_article(article, watchlist) for article in articles]
    analyzed_articles.sort(key=lambda article: (article.get("significance", 0.0), article.get("published_at") or now_wib()), reverse=True)
    meaningful_events = [article for article in analyzed_articles if float(article.get("significance", 0.0)) > 0.05]
    events = (meaningful_events or analyzed_articles)[:10]

    quotes, stock_warnings = stock_fetcher(watchlist)
    market_index, market_warnings = market_fetcher()
    stocks: list[dict[str, Any]] = []
    for ticker in watchlist:
        quote = quotes.get(ticker)
        related_ids = [event_id for event_id, article in ((f"evt_{idx+1:03d}", event) for idx, event in enumerate(events)) if ticker in article.get("impacted_tickers", [])]
        score_inputs = [compute_ticker_score(event, ticker) for event in events]
        recency_weights = [float(event.get("recency_weight", 1.0)) for event in events]
        weighted_total = sum(score * weight for score, weight in zip(score_inputs, recency_weights))
        total_weight = sum(recency_weights) or 1.0
        impact_score = clamp(weighted_total / total_weight, -1.0, 1.0)
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
                "source": (quote or {}).get("source", "unavailable"),
            }
        )

    event_id_map = {f"evt_{idx+1:03d}": event for idx, event in enumerate(events)}
    formatted_events = []
    for event_id, event in event_id_map.items():
        formatted_events.append(
            {
                "id": event_id,
                "headline": event.get("headline", ""),
                "source": event.get("source", ""),
                "url": event.get("url", ""),
                "published_at": event.get("published_at").isoformat(timespec="seconds") if isinstance(event.get("published_at"), datetime) else str(event.get("published_at")),
                "categories": event.get("categories", []),
                "sentiment": event.get("sentiment", "neutral"),
                "sentiment_score": event.get("sentiment_score", 0.0),
                "impacted_sectors": event.get("impacted_sectors", []),
                "impacted_tickers": event.get("impacted_tickers", []),
                "confidence": event.get("confidence", 0.0),
                "significance": event.get("significance", 0.0),
            }
        )

    sector_summary = compute_sector_summary(stocks)
    warnings = news_warnings + stock_warnings + market_warnings
    if not articles:
        warnings.append("No live articles available.")
    if not quotes:
        warnings.append("No live stock quotes available.")

    sources = sorted({event.get("source", "") for event in formatted_events if event.get("source")})
    payload = {
        "fetched_at": now_iso(),
        "from_cache": False,
        "cache_key": list(cache_key),
        "watchlist": watchlist,
        "events": formatted_events,
        "stocks": stocks,
        "sector_summary": sector_summary,
        "market_index": market_index,
        "sources": sources,
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


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"ok": True, "time": now_iso()}


@app.get("/api/watchlist")
def api_get_watchlist() -> dict[str, Any]:
    return {"tickers": get_watchlist()}


@app.get("/api/dashboard")
def api_dashboard() -> dict[str, Any]:
    watchlist = get_watchlist()
    payload = build_refresh_payload(watchlist, force=False)
    return {"watchlist": watchlist, "payload": payload}


@app.put("/api/watchlist")
def api_put_watchlist(payload: WatchlistRequest) -> dict[str, Any]:
    tickers = set_watchlist(payload.tickers)
    return {"tickers": tickers}


@app.post("/api/refresh")
def api_refresh(payload: RefreshRequest) -> JSONResponse:
    result = build_refresh_payload(payload.tickers, force=payload.force)
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
    with CACHE_LOCK:
        CACHE.clear()


def main() -> None:
    import uvicorn

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("backend.main:app", host=host, port=port, reload=False, log_level="info")


if __name__ == "__main__":
    main()
