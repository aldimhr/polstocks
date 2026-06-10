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
import os
import re
import sqlite3
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, time as dtime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from backend.config import BACKEND_DB_PATH
from backend.weights import get_weight, get_all_weights, get_overrides, apply_overrides, reset_to_defaults

from backend.stocks import (
    _ema, fetch_stock_quotes, fetch_ticker_history, sort_stocks_by_impact, compute_sector_summary,
    THREAD_STATUS_RANK,
)
from backend.events import (
    normalize_thread_token, thread_category_family,
    thread_institution_label, thread_entity_label, thread_focus_label,
    build_event_thread_key, summarize_thread_status,
    build_event_tracking, build_reasoning_summary, build_dashboard_cues,
    _background_refresh,
)
from backend.sources import (
    score_political_relevance, detect_event_stage, detect_negation_or_reversal,
    infer_source_type, merge_duplicate_articles, load_source_registry, normalize_watchlist_values,
    load_company_knowledge_from_disk, company_knowledge_for_ticker,
    load_policy_signal_rules, load_market_validation_config, get_watchlist,
    canonicalize_article_url, parse_rss_items, parse_html_signal, fetch_source,
    source_profile_for_domain, source_profile_for_name, source_profile_for_url,
    source_freshness_score, source_metadata_for,
    summarize_source_diagnostics_from_articles, build_source_health_summary,
    unpack_news_fetch_result, fetch_news_bundle,
)
from backend.scoring import (
    evidence_quality_score, relationship_confidence_label, analyze_article,
)
from backend.validation import (
    apply_corroboration_to_events,
    _source_outcome_history_defaults, normalize_source_outcome_history,
    source_reliability_history_key, historical_reliability_metrics, channel_reliability_metrics,
    record_source_outcome, validation_outcome_multiplier,
    calibrate_source_confidence_from_validation, apply_source_conflicts_to_events,
    fetch_market_validation_series, validate_market_reaction,
)
from backend.state import (
    WATCHLIST_LOCK, CACHE_LOCK, WATCHLIST_STATE,
    CACHE, COMPANY_KNOWLEDGE, POLICY_SIGNAL_RULES,
    MARKET_VALIDATION_CONFIG, SOURCE_REGISTRY,
)



# Compatibility exports: tests and older callers import helper functions from
# backend.main even after the deduplication moved implementations to modules.
__all__ = [
    "DEFAULT_WATCHLIST", "MIN_RELATIONSHIP_SCORE", "PROJECT_ROOT", "SECTORS",
    "WATCHLIST_STATE", "_source_outcome_history_defaults", "analyze_article",
    "app", "article_text", "build_event_tracking", "build_refresh_payload",
    "canonicalize_article_url", "company_knowledge_for_ticker",
    "company_name_for_ticker", "detect_event_stage", "detect_negation_or_reversal",
    "event_window_label", "evidence_quality_score", "fetch_market_validation_series",
    "fetch_source", "get_watchlist", "group_articles_into_threads",
    "load_market_validation_config", "load_policy_signal_rules",
    "load_source_registry", "load_watchlist_from_disk", "merge_duplicate_articles",
    "normalize_source_outcome_history", "now_iso", "now_wib", "parse_datetime",
    "parse_html_signal", "parse_rss_items", "requests", "reset_runtime_state",
    "score_political_relevance", "sector_for_ticker", "set_watchlist",
    "source_freshness_score", "source_metadata_for", "source_profile_for_domain",
    "source_profile_for_name", "source_profile_for_url", "validate_market_reaction",
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_FILE = PROJECT_ROOT / "dashboard.html"
MINIAPP_FILE = PROJECT_ROOT / "miniapp.html"
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
    "24h": {"delta": timedelta(hours=24), "label": "last 24 hours", "days": 1},
    "7d": {"delta": timedelta(days=7), "label": "last 7 days", "days": 7},
    "30d": {"delta": timedelta(days=30), "label": "last 30 days", "days": 30},
    "3mo": {"delta": timedelta(days=90), "label": "last 3 months", "days": 90},
}
STOCK_HISTORY_WINDOWS = {
    "24h": {"range": "60d", "interval": "5m", "label": "last 24 hours"},   # 60d is Yahoo max for 5-min
    "7d": {"range": "6mo", "interval": "1h", "label": "last 7 days"},
    "30d": {"range": "6mo", "interval": "1d", "label": "last 30 days"},
    "3mo": {"range": "6mo", "interval": "1d", "label": "last 3 months"},
}
SOURCE_TIMEOUT_SECONDS = 5
WIB = timezone(timedelta(hours=7))
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Hermes Political-Stock Mapper; +https://hermes-agent.nousresearch.com)"
}

# ── Bot SQLite helpers (per-user watchlist) ──
BOT_DB_PATH = os.getenv(
    "POLSTOCK_BOT_DB",
    os.path.join(os.path.dirname(__file__), "..", "..", "polstock_bot", "polstock.db"),
)

def get_user_watchlist_from_bot_db(user_id: int) -> list[str]:
    """Read per-user watchlist from the Telegram bot's SQLite database."""
    db_path = os.path.abspath(BOT_DB_PATH)
    if not os.path.exists(db_path):
        return []
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT ticker FROM user_watchlists WHERE user_id = ? ORDER BY added_at",
            (user_id,),
        )
        tickers = [row["ticker"] for row in cur.fetchall()]
        conn.close()
        return tickers
    except Exception:
        return []

def save_user_watchlist_to_bot_db(user_id: int, tickers: list[str]) -> None:
    """Write per-user watchlist to the Telegram bot's SQLite database."""
    db_path = os.path.abspath(BOT_DB_PATH)
    if not os.path.exists(db_path):
        return
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        conn.execute("DELETE FROM user_watchlists WHERE user_id = ?", (user_id,))
        conn.executemany(
            "INSERT INTO user_watchlists (user_id, ticker) VALUES (?, ?)",
            [(user_id, t) for t in tickers],
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

# ── Backend-owned SQLite DB (source outcomes, event cache) ──────────
BACKEND_DB_PATH = BACKEND_DB_PATH

def _backend_conn() -> sqlite3.Connection:
    """Open the backend SQLite DB with production-safe lock handling."""
    BACKEND_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BACKEND_DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_backend_db() -> None:
    """Create backend-owned tables if they don't exist."""
    conn = _backend_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS source_outcomes (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                data TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events_cache (
                event_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                cached_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    finally:
        conn.close()

def load_source_outcome_history() -> dict[str, Any]:
    """Load source outcome history from SQLite (falls back to JSON file on first run)."""
    conn = _backend_conn()
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS source_outcomes (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))")
        cur = conn.execute("SELECT data FROM source_outcomes WHERE id = 1")
        row = cur.fetchone()
        if row:
            return normalize_source_outcome_history(json.loads(row[0]))
    except Exception:
        pass
    finally:
        conn.close()
    # Fallback: migrate from JSON file if exists
    try:
        raw = json.loads(SOURCE_OUTCOME_HISTORY_FILE.read_text(encoding="utf-8"))
        history = normalize_source_outcome_history(raw)
        save_source_outcome_history(history)  # persist to SQLite
        return history
    except Exception:
        return _source_outcome_history_defaults()

def save_source_outcome_history(history: dict[str, Any]) -> None:
    """Save source outcome history to SQLite."""
    normalized = normalize_source_outcome_history(history)
    conn = _backend_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO source_outcomes (id, data, updated_at) VALUES (1, ?, datetime('now'))",
            (json.dumps(normalized, ensure_ascii=False, sort_keys=True),),
        )
        conn.commit()
    finally:
        conn.close()


def save_cache_to_db(cache_key: tuple, payload: dict[str, Any]) -> None:
    """Persist a cache entry to SQLite for cold-start recovery."""
    key_str = json.dumps(list(cache_key))
    conn = _backend_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO events_cache (event_id, data, cached_at) VALUES (?, ?, datetime('now'))",
            (key_str, json.dumps(payload, default=str)),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def load_cache_from_db() -> dict[tuple, dict[str, Any]]:
    """Load cached payloads from SQLite on startup."""
    result: dict[tuple, dict[str, Any]] = {}
    conn = _backend_conn()
    try:
        cur = conn.execute("SELECT event_id, data, cached_at FROM events_cache")
        for row in cur.fetchall():
            try:
                key_tuple = tuple(json.loads(row[0]))
                payload = json.loads(row[1])
                cached_at = parse_datetime(row[2]) if row[2] else now_wib()
                result[key_tuple] = {"cached_at": cached_at, "payload": payload}
            except Exception:
                continue
    except Exception:
        pass
    finally:
        conn.close()
    return result


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
    {"name": "Antara Ekonomi", "url": "https://www.antaranews.com/rss/ekonomi", "kind": "rss", "weight": 0.9},
    {"name": "Antara Politik", "url": "https://www.antaranews.com/rss/politik", "kind": "rss", "weight": 0.85},
    {"name": "CNBC Indonesia", "url": "https://www.cnbcindonesia.com/rss", "kind": "rss", "weight": 0.9},
    {"name": "CNBC Market", "url": "https://www.cnbcindonesia.com/market/rss", "kind": "rss", "weight": 0.9},
    {"name": "CNBC News", "url": "https://www.cnbcindonesia.com/news/rss", "kind": "rss", "weight": 0.85},
    {"name": "CNN Indonesia Nasional", "url": "https://www.cnnindonesia.com/nasional/rss", "kind": "rss", "weight": 0.85},
    {"name": "CNN Indonesia Ekonomi", "url": "https://www.cnnindonesia.com/ekonomi/rss", "kind": "rss", "weight": 0.85},
    {"name": "Detik Finance", "url": "https://finance.detik.com/rss", "kind": "rss", "weight": 0.8},
    {"name": "Tempo", "url": "https://www.tempo.co/rss", "kind": "rss", "weight": 0.79},
    {"name": "Kontan", "url": "https://www.kontan.co.id", "kind": "html", "weight": 0.82},
    {"name": "Bisnis Indonesia", "url": "https://www.bisnis.com", "kind": "html", "weight": 0.83},
    {"name": "Sekretariat Kabinet", "url": "https://setkab.go.id", "kind": "html", "weight": 1.0},
    {"name": "Kemenkeu", "url": "https://www.kemenkeu.go.id/informasi-publik/publikasi/siaran-pers", "kind": "html", "weight": 0.95},
    {"name": "OJK", "url": "https://www.ojk.go.id", "kind": "html", "weight": 0.9},
    {"name": "KPK", "url": "https://www.kpk.go.id/id/berita/siaran-pers", "kind": "html", "weight": 0.9},
    {"name": "CSIS Indonesia", "url": "https://www.csis.or.id/publication", "kind": "html", "weight": 0.75},
]

app = FastAPI(title=APP_TITLE, version="1.0.0")


# ── Rate Limiting ───────────────────────────────────────────────

_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX_REQUESTS = 120  # per window per IP
_rate_store: dict[str, list[float]] = {}
_rate_lock = threading.Lock()


@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    """Simple sliding-window rate limiter per client IP."""
    client_ip = request.client.host if request.client else "unknown"
    now = _time.time()

    with _rate_lock:
        # Clean old entries and count current window
        if client_ip not in _rate_store:
            _rate_store[client_ip] = []
        timestamps = _rate_store[client_ip]
        # Remove entries outside the window
        cutoff = now - _RATE_LIMIT_WINDOW
        _rate_store[client_ip] = [t for t in timestamps if t > cutoff]
        current_count = len(_rate_store[client_ip])

        if current_count >= _RATE_LIMIT_MAX_REQUESTS:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded. Try again later."},
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
            )
        _rate_store[client_ip].append(now)

    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(_RATE_LIMIT_MAX_REQUESTS)
    response.headers["X-RateLimit-Remaining"] = str(max(0, _RATE_LIMIT_MAX_REQUESTS - current_count - 1))
    return response


# Periodically clean up stale IPs
def _cleanup_rate_store():
    while True:
        _time.sleep(300)  # every 5 min
        with _rate_lock:
            cutoff = _time.time() - _RATE_LIMIT_WINDOW * 2
            stale = [ip for ip, ts in _rate_store.items() if not ts or ts[-1] < cutoff]
            for ip in stale:
                del _rate_store[ip]

threading.Thread(target=_cleanup_rate_store, daemon=True, name="rate-limit-cleanup").start()


class RefreshRequest(BaseModel):
    tickers: list[str] = Field(default_factory=list)
    force: bool = False
    window: str = DEFAULT_EVENT_WINDOW


class WatchlistRequest(BaseModel):
    tickers: list[str]


class HistoricalBackfillRequest(BaseModel):
    articles: list[dict[str, Any]] = Field(default_factory=list)
    dry_run: bool = True
    min_timestamp_confidence: float = 0.8


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


def load_watchlist_from_disk() -> list[str]:
    """Load watchlist from disk, using main.py's WATCHLIST_FILE."""
    if not WATCHLIST_FILE.exists():
        return list(DEFAULT_WATCHLIST)
    try:
        raw = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return list(DEFAULT_WATCHLIST)
    values = normalize_watchlist_values(raw)
    return values or list(DEFAULT_WATCHLIST)


def save_watchlist_to_disk(tickers: list[str]) -> None:
    """Save watchlist to disk, using main.py's WATCHLIST_FILE."""
    WATCHLIST_FILE.write_text(
        json.dumps({"tickers": tickers, "updated_at": now_iso()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


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


def dedupe_articles(articles: list[dict[str, Any]], window: str = DEFAULT_EVENT_WINDOW) -> list[dict[str, Any]]:
    filtered = [article for article in articles if not is_stale_article(article.get("published_at"), window)]
    filtered.sort(key=lambda article: article.get("published_at") or now_wib(), reverse=True)
    return merge_duplicate_articles(filtered)


# ---------------------------------------------------------------------------
# Stock fetching
# ---------------------------------------------------------------------------



def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Compute Relative Strength Index (RSI) from closing prices."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    recent = deltas[-(period):]
    gains = [d if d > 0 else 0 for d in recent]
    losses = [-d if d < 0 else 0 for d in recent]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def compute_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float] | None:
    """Compute MACD. Returns dict with 'macd', 'signal', 'histogram'."""
    if len(closes) < slow + signal:
        return None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    offset = len(ema_fast) - len(ema_slow)
    macd_line = [f - s for f, s in zip(ema_fast[offset:], ema_slow)]
    if len(macd_line) < signal:
        return None
    signal_line = _ema(macd_line, signal)
    if not signal_line:
        return None
    histogram = macd_line[-1] - signal_line[-1]
    return {
        "macd": round(macd_line[-1], 4),
        "signal": round(signal_line[-1], 4),
        "histogram": round(histogram, 4),
    }


def compute_sma(closes: list[float], period: int) -> float | None:
    """Compute Simple Moving Average."""
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 4)


def compute_trend(closes: list[float]) -> dict[str, Any] | None:
    """Compute trend indicators: SMA20, SMA50, crossover signal."""
    sma20 = compute_sma(closes, 20)
    sma50 = compute_sma(closes, 50)
    if sma20 is None or sma50 is None:
        return None
    current_price = closes[-1]
    if sma20 > sma50:
        trend = "bullish"
        strength = (sma20 - sma50) / sma50
    elif sma20 < sma50:
        trend = "bearish"
        strength = (sma50 - sma20) / sma50
    else:
        trend = "neutral"
        strength = 0.0
    return {
        "sma20": sma20, "sma50": sma50, "price": round(current_price, 2),
        "above_sma20": current_price > sma20, "above_sma50": current_price > sma50,
        "trend": trend, "trend_strength": round(strength, 4),
    }


def compute_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """Compute Average True Range — measures volatility."""
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)
    if len(true_ranges) < period:
        return None
    # Wilder's smoothing (EMA-like)
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 4)


def fetch_index_change(symbol: str, label: str) -> tuple[float | None, str]:
    """Fetch current day change_pct for a market index. Returns (change_pct, direction)."""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range=2d&interval=1d"
        response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        meta = result.get("meta", {})
        price = meta.get("regularMarketPrice")
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price and prev_close and prev_close != 0:
            change_pct = ((price - prev_close) / prev_close) * 100.0
            direction = "up" if change_pct > 0.15 else "down" if change_pct < -0.15 else "flat"
            return round(change_pct, 2), direction
    except Exception:
        pass
    return None, "flat"


def fetch_usd_idr() -> tuple[float | None, str]:
    """Fetch USD/IDR exchange rate change. Returns (change_pct, direction)."""
    return fetch_index_change("USDIDR=X", "USD/IDR")


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
            quote_data = payload.get("indicators", {}).get("quote", [{}])[0]
            closes = [value for value in quote_data.get("close", []) if value is not None]
            raw_timestamps = list(payload.get("timestamp", []) or [])
            raw_opens = list(quote_data.get("open", []) or [])
            raw_highs = list(quote_data.get("high", []) or [])
            raw_lows = list(quote_data.get("low", []) or [])
            raw_closes = list(quote_data.get("close", []) or [])
            # Build OHLC series for candlestick chart
            ohlc_series = []
            for i, ts in enumerate(raw_timestamps):
                if ts is None:
                    continue
                o = raw_opens[i] if i < len(raw_opens) else None
                h = raw_highs[i] if i < len(raw_highs) else None
                low = raw_lows[i] if i < len(raw_lows) else None
                c = raw_closes[i] if i < len(raw_closes) else None
                if all(v is not None for v in (o, h, low, c)):
                    ohlc_series.append({
                        "time": int(ts),
                        "open": float(o),
                        "high": float(h),
                        "low": float(low),
                        "close": float(c),
                    })
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
                "ohlc_series": ohlc_series,
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
        "ohlc_series": [],
        "market_time": now_iso(),
        "source": "unavailable",
    }, warnings


def analyze_sentiment(text: str) -> tuple[str, float, float]:
    """Sentiment analysis using expanded keyword lexicon (via nlp module)."""
    from backend.nlp import analyze_sentiment_ml
    return analyze_sentiment_ml(text[:512])


def extract_entities(text: str) -> list[str]:
    """Extract named entities using IndoBERT NER (via nlp module)."""
    from backend.nlp import extract_entities_ml
    return extract_entities_ml(text[:512])


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
    evidence_strength = float(relationship.get("evidence_strength", confidence))
    relationship_multiplier = {"direct": 1.0, "indirect": 0.82}.get(relationship.get("relationship_type"), 0.5)
    evidence_multiplier = clamp(0.5 + 0.5 * max(0.0, evidence_strength), 0.25, 1.0)
    direction = str(relationship.get("impact_direction", "neutral"))
    if direction == "positive":
        directional_sentiment = max(abs(sentiment_score), 0.45)
    elif direction == "negative":
        directional_sentiment = -max(abs(sentiment_score), 0.45)
    elif direction == "mixed":
        directional_sentiment = 0.35 * sentiment_score
    else:
        directional_sentiment = 0.0
    # NOTE: validation_multiplier and confidence_multiplier (source_confidence)
    # are already baked into relationship_confidence by build_refresh_payload.
    # Applying them here would double-penalize. Only evidence_multiplier and
    # relationship_multiplier are independent signals not yet in the base.
    raw = directional_sentiment * relevance_factor * confidence * relationship_multiplier * evidence_multiplier
    return clamp(raw, -1.0, 1.0)


# ---------------------------------------------------------------------------
# Refresh orchestration and cache
# ---------------------------------------------------------------------------


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

    # ── Novelty detection: dampen repeated/similar events ──
    # Count category+ticker combinations across the batch
    cat_ticker_counts: dict[str, int] = {}
    for article in analyzed_articles:
        cats = article.get("categories", ["_unknown"])
        for rel in article.get("stock_relationships", []):
            tk = rel.get("ticker", "")
            for cat in cats:
                key = f"{cat}:{tk}"
                cat_ticker_counts[key] = cat_ticker_counts.get(key, 0) + 1

    # Track seen counts for sequential novelty dampening
    _seen_cat_ticker: dict[str, int] = {}

    # ── Sentiment momentum: compare per-ticker sentiment across articles ──
    ticker_sentiments: dict[str, list[float]] = {}
    for article in analyzed_articles:
        s = float(article.get("sentiment_score", 0.0))
        if s == 0.0:
            continue
        for rel in article.get("stock_relationships", []):
            tk = rel.get("ticker", "")
            if tk:
                ticker_sentiments.setdefault(tk, []).append(s)

    ticker_avg_sentiment: dict[str, float] = {
        tk: sum(vals) / len(vals) for tk, vals in ticker_sentiments.items() if vals
    }

    # Apply novelty + momentum to each article's stock relationships
    for article in analyzed_articles:
        cats = article.get("categories", ["_unknown"])
        for rel in article.get("stock_relationships", []):
            tk = rel.get("ticker", "")
            if not tk:
                continue

            # Novelty: first event=1.0, 2nd=0.8, 3rd=0.6, 4+=0.4
            novelty_key = f"{cats[0]}:{tk}" if cats else f"_unknown:{tk}"
            _seen_cat_ticker[novelty_key] = _seen_cat_ticker.get(novelty_key, 0) + 1
            count = _seen_cat_ticker[novelty_key]
            if count <= 1:
                novelty = 1.0
            elif count == 2:
                novelty = 0.8
            elif count == 3:
                novelty = 0.6
            else:
                novelty = 0.4
            rel["novelty_factor"] = round(novelty, 2)

            # Momentum: compare this article's sentiment vs ticker average
            art_sentiment = float(article.get("sentiment_score", 0.0))
            avg = ticker_avg_sentiment.get(tk, 0.0)
            if avg != 0.0 and art_sentiment != 0.0:
                if art_sentiment > avg * 1.2:
                    momentum = 1.1  # strengthening
                elif art_sentiment < avg * 0.8:
                    momentum = 0.9  # weakening
                else:
                    momentum = 1.0  # stable
            else:
                momentum = 1.0
            rel["momentum_factor"] = round(momentum, 2)

            # Apply both factors to relevance_score (which feeds into scoring)
            current_relevance = float(rel.get("relevance_score", 0.0))
            rel["relevance_score"] = round(current_relevance * novelty * momentum, 3)

    analyzed_articles.sort(key=lambda article: (article.get("significance", 0.0), article.get("published_at") or now_wib()), reverse=True)
    meaningful_events = [article for article in analyzed_articles if float(article.get("significance", 0.0)) > 0.015]
    ranked_events = meaningful_events or analyzed_articles
    event_threads = group_articles_into_threads(ranked_events)
    # Propagate thread_status to individual relationships
    for event in ranked_events:
        thread_status = str(event.get("thread_status", "active") or "active")
        for relationship in event.get("stock_relationships", []):
            relationship.setdefault("thread_status", thread_status)
    events = ranked_events[:10]
    apply_corroboration_to_events(events)
    apply_source_conflicts_to_events(events)

    quotes, stock_warnings = stock_fetcher(watchlist)
    market_index, market_warnings = market_fetcher()

    # Market context factor — flat market dampens directional predictions
    ihsg_change = abs(float(market_index.get("change_pct") or 0.0))
    if ihsg_change < get_weight("market_flat_threshold"):
        market_context_factor = get_weight("market_flat_mult")
    elif ihsg_change < get_weight("market_mild_threshold"):
        market_context_factor = get_weight("market_mild_mult")
    elif ihsg_change < 0.60:
        market_context_factor = 0.95   # mild movement: slight dampening
    elif ihsg_change > 1.5:
        market_context_factor = 1.08   # strong trend: boost aligned signals
    elif ihsg_change > get_weight("market_strong_threshold"):
        market_context_factor = get_weight("market_strong_mult")
    else:
        market_context_factor = 1.0    # normal market
    ihsg_direction = "positive" if float(market_index.get("change_pct") or 0.0) > 0 else "negative"

    # Fetch RSI, MACD, SMA, and ATR for each watchlist stock (concurrent)
    rsi_cache: dict[str, float | None] = {}
    macd_cache: dict[str, dict[str, float] | None] = {}
    trend_cache: dict[str, dict[str, Any] | None] = {}
    atr_cache: dict[str, float | None] = {}
    def _fetch_indicators(ticker: str) -> tuple[str, float | None, dict | None, dict | None, float | None]:
        for _attempt in range(2):
            try:
                hist = fetch_ticker_history(ticker, "3mo")
                closes = hist.get("series", [])
                prices = [float(p) for p in closes if p is not None]
                if len(prices) < 15:
                    continue  # retry if insufficient data
                rsi = compute_rsi(prices)
                macd = compute_macd(prices) if len(prices) >= 35 else None
                trend = compute_trend(prices) if len(prices) >= 50 else None
                # ATR from OHLC data
                ohlc = hist.get("ohlc_series", [])
                if len(ohlc) >= 15:
                    highs = [float(d["high"]) for d in ohlc]
                    lows = [float(d["low"]) for d in ohlc]
                    cls = [float(d["close"]) for d in ohlc]
                    atr = compute_atr(highs, lows, cls)
                else:
                    atr = None
                return ticker, rsi, macd, trend, atr
            except Exception:
                continue
        return ticker, None, None, None, None
    with ThreadPoolExecutor(max_workers=min(len(watchlist), 3)) as pool:
        for ticker, rsi, macd, trend, atr in pool.map(lambda t: _fetch_indicators(t), watchlist):
            rsi_cache[ticker] = rsi
            macd_cache[ticker] = macd
            trend_cache[ticker] = trend
            atr_cache[ticker] = atr

    # Foreign market correlation: S&P 500 and Nikkei
    sp500_change, sp500_dir = fetch_index_change("^GSPC", "S&P 500")
    nikkei_change, nikkei_dir = fetch_index_change("^N225", "Nikkei 225")
    # Aggregate: if both foreign markets agree, stronger signal
    foreign_direction = "flat"
    if sp500_dir == nikkei_dir and sp500_dir != "flat":
        foreign_direction = sp500_dir
    elif sp500_dir != "flat":
        foreign_direction = sp500_dir  # S&P has more weight
    elif nikkei_dir != "flat":
        foreign_direction = nikkei_dir

    # Currency impact: USD/IDR
    usd_idr_change, usd_idr_dir = fetch_usd_idr()

    # Sentiment momentum: track sentiment trend across recent events per ticker
    sentiment_momentum: dict[str, str] = {}  # ticker -> "strengthening" | "weakening" | "stable"
    ticker_sentiments: dict[str, list[float]] = {}
    for event in events:
        for rel in event.get("stock_relationships", []):
            t = normalize_ticker(rel.get("ticker", ""))
            if t:
                score = float(event.get("sentiment_score", 0.0) or 0.0)
                ticker_sentiments.setdefault(t, []).append(score)
    for t, scores in ticker_sentiments.items():
        if len(scores) >= 3:
            # Compare first half avg vs second half avg
            mid = len(scores) // 2
            first_avg = sum(scores[:mid]) / mid
            second_avg = sum(scores[mid:]) / (len(scores) - mid)
            delta = second_avg - first_avg
            if delta > 0.15:
                sentiment_momentum[t] = "strengthening"
            elif delta < -0.15:
                sentiment_momentum[t] = "weakening"
            else:
                sentiment_momentum[t] = "stable"

    # Sector correlation: count UNIQUE STORIES per sector+direction (not raw articles)
    sector_direction_counts: dict[str, dict[str, int]] = {}  # sector -> {positive: N, negative: N}
    sector_thread_ids: dict[str, dict[str, set[str]]] = {}  # sector -> {direction -> set of thread_ids}
    for event in events:
        thread_id = event.get("thread_id", "")
        for rel in event.get("stock_relationships", []):
            sector = rel.get("sector", "")
            direction = str(rel.get("impact_direction", "neutral"))
            if sector and direction in ("positive", "negative"):
                if sector not in sector_thread_ids:
                    sector_thread_ids[sector] = {}
                if direction not in sector_thread_ids[sector]:
                    sector_thread_ids[sector][direction] = set()
                if thread_id:
                    sector_thread_ids[sector][direction].add(thread_id)
                else:
                    sector_thread_ids[sector][direction].add(f"_article_{id(event)}")
    for sector, directions in sector_thread_ids.items():
        sector_direction_counts[sector] = {d: len(tids) for d, tids in directions.items()}

    # Event clustering: count UNIQUE STORIES (threads) per ticker, not raw articles
    # Same story from Detik + CNBC + Kompas should count as 1 event, not 3
    ticker_event_counts: dict[str, int] = {}
    ticker_thread_ids: dict[str, set[str]] = {}
    for event in events:
        thread_id = event.get("thread_id", "")
        for rel in event.get("stock_relationships", []):
            t = normalize_ticker(rel.get("ticker", ""))
            if t:
                if t not in ticker_thread_ids:
                    ticker_thread_ids[t] = set()
                if thread_id:
                    ticker_thread_ids[t].add(thread_id)
                else:
                    # Fallback: count articles without thread_id individually
                    ticker_thread_ids[t].add(f"_article_{id(event)}")
    for t, thread_ids in ticker_thread_ids.items():
        ticker_event_counts[t] = len(thread_ids)

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
            # Apply channel reliability metrics
            primary_channel_for_metrics = str(relationship.get("policy_channel", ""))
            ch_metrics = channel_reliability_metrics(updated_source_outcome_history, primary_channel_for_metrics)
            relationship.update(ch_metrics)
            # Apply validation multiplier directly to relationship confidence
            raw_confidence = float(relationship.get("confidence", 0.0) or 0.0)
            val_mult = float(validation.get("validation_multiplier", 1.0) or 1.0)
            if val_mult != 1.0:
                adjusted = clamp(raw_confidence * val_mult, 0.0, 1.0)
                relationship["confidence"] = round(adjusted, 3)
                relationship["relationship_confidence"] = round(adjusted, 3)
                relationship["confidence_label"] = relationship_confidence_label(adjusted, str(relationship.get("coverage_warning", "")))
                relationship["validation_confidence_delta"] = round(adjusted - raw_confidence, 3)

            # Volume anomaly signal — unusual volume after event = real market reaction
            vol_ratio = float(relationship.get("abnormal_volume_ratio", 0.0) or 0.0)
            vol_mult = 1.0
            if vol_ratio > 3.0:
                vol_mult = get_weight("volume_3x_mult")
            elif vol_ratio > 2.0:
                vol_mult = get_weight("volume_2x_mult")
            elif vol_ratio > 1.5:
                vol_mult = 1.03   # moderate volume increase
            elif vol_ratio < 0.4 and vol_ratio > 0:
                vol_mult = get_weight("volume_low_mult")
            if vol_mult != 1.0:
                cur_conf = float(relationship.get("confidence", 0.0) or 0.0)
                adjusted_vol = clamp(cur_conf * vol_mult, 0.0, 1.0)
                relationship["confidence"] = round(adjusted_vol, 3)
                relationship["relationship_confidence"] = round(adjusted_vol, 3)
                relationship["volume_signal"] = round(vol_mult, 3)

            # Market context — flat market dampens directional predictions
            rel_direction = str(relationship.get("impact_direction", "neutral"))
            if rel_direction in ("positive", "negative") and market_context_factor != 1.0:
                # For strong trends, only boost predictions that align with the trend
                if market_context_factor > 1.0 and rel_direction == ihsg_direction:
                    mkt_mult = market_context_factor
                elif market_context_factor > 1.0:
                    mkt_mult = 1.0  # counter-trend: no boost
                else:
                    mkt_mult = market_context_factor  # flat market: dampen all
                cur_conf = float(relationship.get("confidence", 0.0) or 0.0)
                adjusted_mkt = clamp(cur_conf * mkt_mult, 0.0, 1.0)
                relationship["confidence"] = round(adjusted_mkt, 3)
                relationship["relationship_confidence"] = round(adjusted_mkt, 3)
                relationship["market_context_factor"] = round(mkt_mult, 3)

            # Technical alignment — composite of RSI + MACD + SMA (capped at ±15%)
            # These three indicators all measure the same thing (technical momentum),
            # so combining them prevents triple-counting.
            ticker_for_rsi = normalize_ticker(relationship.get("ticker", ""))
            if rel_direction in ("positive", "negative"):
                tech_signals = []  # each is -1 (bearish), 0 (neutral), or +1 (bullish)
                rsi_val = rsi_cache.get(ticker_for_rsi)
                if rsi_val is not None:
                    if rel_direction == "positive":
                        if rsi_val >= get_weight("rsi_overbought_extreme"):
                            tech_signals.append(-1.0)  # extremely overbought
                        elif rsi_val >= get_weight("rsi_overbought"):
                            tech_signals.append(-0.5)  # overbought
                        elif rsi_val <= get_weight("rsi_oversold_extreme"):
                            tech_signals.append(1.0)   # extremely oversold
                        elif rsi_val <= get_weight("rsi_oversold"):
                            tech_signals.append(0.5)   # oversold
                    elif rel_direction == "negative":
                        if rsi_val <= get_weight("rsi_oversold_extreme"):
                            tech_signals.append(-1.0)  # extremely oversold (bad for negative)
                        elif rsi_val <= get_weight("rsi_oversold"):
                            tech_signals.append(-0.5)
                        elif rsi_val >= get_weight("rsi_overbought_extreme"):
                            tech_signals.append(1.0)   # extremely overbought (good for negative)
                        elif rsi_val >= get_weight("rsi_overbought"):
                            tech_signals.append(0.5)
                    relationship["rsi_value"] = round(rsi_val, 1)

                macd_data = macd_cache.get(ticker_for_rsi)
                if macd_data is not None:
                    hist = macd_data["histogram"]
                    if rel_direction == "positive":
                        tech_signals.append(1.0 if hist > 0 else -1.0 if hist < 0 else 0)
                    elif rel_direction == "negative":
                        tech_signals.append(1.0 if hist < 0 else -1.0 if hist > 0 else 0)
                    relationship["macd_histogram"] = round(hist, 4)

                trend_data = trend_cache.get(ticker_for_rsi)
                if trend_data is not None:
                    stock_trend = trend_data["trend"]
                    if rel_direction == "positive":
                        tech_signals.append(1.0 if stock_trend == "bullish" else -1.0 if stock_trend == "bearish" else 0)
                    elif rel_direction == "negative":
                        tech_signals.append(1.0 if stock_trend == "bearish" else -1.0 if stock_trend == "bullish" else 0)
                    relationship["sma_trend"] = stock_trend

                # Combine: average signal → capped composite factor (max ±15%)
                if tech_signals:
                    avg_signal = sum(tech_signals) / len(tech_signals)
                    # Map to factor: +1 avg → 1.15, -1 avg → 0.85, 0 → 1.0
                    tech_cap = get_weight("technical_cap")
                    composite_mult = 1.0 + (avg_signal * tech_cap)
                    composite_mult = max(1.0 - tech_cap, min(1.0 + tech_cap, composite_mult))
                    if composite_mult != 1.0:
                        cur_conf = float(relationship.get("confidence", 0.0) or 0.0)
                        adjusted_tech = clamp(cur_conf * composite_mult, 0.0, 1.0)
                        relationship["confidence"] = round(adjusted_tech, 3)
                        relationship["relationship_confidence"] = round(adjusted_tech, 3)
                    # Store individual factors for transparency
                    if rsi_val is not None:
                        relationship["rsi_factor"] = round(composite_mult, 3) if rsi_val is not None else 1.0
                    if macd_data is not None:
                        relationship["macd_factor"] = round(composite_mult, 3)
                    if trend_data is not None:
                        relationship["trend_factor"] = round(composite_mult, 3)

            # Event clustering — multiple events about same ticker = stronger signal
            event_count = ticker_event_counts.get(ticker_for_rsi, 0)
            if event_count >= 3:
                cluster_mult = get_weight("cluster_3plus_mult")
            elif event_count >= 2:
                cluster_mult = get_weight("cluster_2_mult")
            else:
                cluster_mult = 1.0
            if cluster_mult != 1.0:
                cur_conf = float(relationship.get("confidence", 0.0) or 0.0)
                adjusted_cluster = clamp(cur_conf * cluster_mult, 0.0, 1.0)
                relationship["confidence"] = round(adjusted_cluster, 3)
                relationship["relationship_confidence"] = round(adjusted_cluster, 3)
            relationship["event_cluster_count"] = event_count
            relationship["event_cluster_factor"] = round(cluster_mult, 3)

            # ATR volatility signal — high volatility stocks are more likely to move
            atr_val = atr_cache.get(ticker_for_rsi)
            atr_mult = 1.0
            if atr_val is not None:
                current_price = float(relationship.get("price", 0) or 0)
                if current_price > 0:
                    atr_pct = (atr_val / current_price) * 100.0
                    relationship["atr_value"] = round(atr_val, 2)
                    relationship["atr_pct"] = round(atr_pct, 2)
                    if rel_direction in ("positive", "negative"):
                        if atr_pct > get_weight("atr_very_high_pct"):
                            atr_mult = get_weight("atr_very_high_mult")
                        elif atr_pct > get_weight("atr_high_pct"):
                            atr_mult = get_weight("atr_high_mult")
                        elif atr_pct < get_weight("atr_low_pct"):
                            atr_mult = get_weight("atr_low_mult")
                        if atr_mult != 1.0:
                            cur_conf = float(relationship.get("confidence", 0.0) or 0.0)
                            adjusted_atr = clamp(cur_conf * atr_mult, 0.0, 1.0)
                            relationship["confidence"] = round(adjusted_atr, 3)
                            relationship["relationship_confidence"] = round(adjusted_atr, 3)
            relationship["atr_factor"] = round(atr_mult, 3)

            # Sector correlation — multiple stocks in same sector agreeing = stronger signal
            rel_sector = str(relationship.get("sector", ""))
            sector_mult = 1.0
            same_dir_count = 0
            if rel_sector and rel_direction in ("positive", "negative"):
                sector_counts = sector_direction_counts.get(rel_sector, {})
                same_dir_count = sector_counts.get(rel_direction, 0)
                if same_dir_count >= 4:
                    sector_mult = get_weight("sector_4plus_mult")
                elif same_dir_count >= 2:
                    sector_mult = get_weight("sector_2plus_mult")
                if sector_mult != 1.0:
                    cur_conf = float(relationship.get("confidence", 0.0) or 0.0)
                    adjusted_sector = clamp(cur_conf * sector_mult, 0.0, 1.0)
                    relationship["confidence"] = round(adjusted_sector, 3)
                    relationship["relationship_confidence"] = round(adjusted_sector, 3)
            relationship["sector_correlation_count"] = same_dir_count
            relationship["sector_correlation_factor"] = round(sector_mult, 3)

            # Foreign market correlation — global trend alignment
            foreign_mult = 1.0
            if rel_direction in ("positive", "negative") and foreign_direction != "flat":
                if rel_direction == "positive" and foreign_direction == "up":
                    foreign_mult = get_weight("foreign_aligned_mult")
                elif rel_direction == "negative" and foreign_direction == "down":
                    foreign_mult = get_weight("foreign_aligned_mult")
                elif rel_direction == "positive" and foreign_direction == "down":
                    foreign_mult = get_weight("foreign_against_mult")
                elif rel_direction == "negative" and foreign_direction == "up":
                    foreign_mult = get_weight("foreign_against_mult")
                if foreign_mult != 1.0:
                    cur_conf = float(relationship.get("confidence", 0.0) or 0.0)
                    adjusted_foreign = clamp(cur_conf * foreign_mult, 0.0, 1.0)
                    relationship["confidence"] = round(adjusted_foreign, 3)
                    relationship["relationship_confidence"] = round(adjusted_foreign, 3)
            relationship["foreign_market_factor"] = round(foreign_mult, 3)

            # Sentiment momentum — sentiment getting stronger/weaker over recent events
            s_momentum = sentiment_momentum.get(ticker_for_rsi, "stable")
            momentum_mult = 1.0
            if s_momentum != "stable" and rel_direction in ("positive", "negative"):
                if s_momentum == "strengthening" and rel_direction == "positive":
                    momentum_mult = get_weight("momentum_strong_mult")
                elif s_momentum == "weakening" and rel_direction == "negative":
                    momentum_mult = get_weight("momentum_strong_mult")
                elif s_momentum == "strengthening" and rel_direction == "negative":
                    momentum_mult = get_weight("momentum_weakening_mult")
                elif s_momentum == "weakening" and rel_direction == "positive":
                    momentum_mult = get_weight("momentum_weakening_mult")
                if momentum_mult != 1.0:
                    cur_conf = float(relationship.get("confidence", 0.0) or 0.0)
                    adjusted_mom = clamp(cur_conf * momentum_mult, 0.0, 1.0)
                    relationship["confidence"] = round(adjusted_mom, 3)
                    relationship["relationship_confidence"] = round(adjusted_mom, 3)
            relationship["sentiment_momentum"] = s_momentum
            relationship["sentiment_momentum_factor"] = round(momentum_mult, 3)

            # Currency impact — USD/IDR affects export/import stocks differently
            currency_mult = 1.0
            if usd_idr_dir != "flat" and rel_direction in ("positive", "negative"):
                exposure_factors = relationship.get("exposure_factors", {})
                export_dep = str(exposure_factors.get("export_import_dependency", "low")).lower()
                if export_dep in ("high", "medium"):
                    # Determine if stock is exporter or importer based on sector
                    rel_sector = str(relationship.get("sector", "")).lower()
                    is_exporter = any(s in rel_sector for s in ("basic materials", "energy", "mining"))
                    is_importer = any(s in rel_sector for s in ("consumer", "food", "retail"))
                    if usd_idr_dir == "up":  # IDR weakening
                        if is_exporter and rel_direction == "positive":
                            currency_mult = get_weight("currency_exporter_mult")
                        elif is_importer and rel_direction == "negative":
                            currency_mult = get_weight("currency_importer_mult")
                        elif is_exporter and rel_direction == "negative":
                            currency_mult = get_weight("currency_against_mult")
                        elif is_importer and rel_direction == "positive":
                            currency_mult = get_weight("currency_against_mult")
                    elif usd_idr_dir == "down":  # IDR strengthening
                        if is_importer and rel_direction == "positive":
                            currency_mult = get_weight("currency_exporter_mult")
                        elif is_exporter and rel_direction == "negative":
                            currency_mult = get_weight("currency_importer_mult")
                        elif is_importer and rel_direction == "negative":
                            currency_mult = get_weight("currency_against_mult")
                        elif is_exporter and rel_direction == "positive":
                            currency_mult = get_weight("currency_against_mult")
                    if currency_mult != 1.0:
                        cur_conf = float(relationship.get("confidence", 0.0) or 0.0)
                        adjusted_curr = clamp(cur_conf * currency_mult, 0.0, 1.0)
                        relationship["confidence"] = round(adjusted_curr, 3)
                        relationship["relationship_confidence"] = round(adjusted_curr, 3)
            relationship["currency_factor"] = round(currency_mult, 3)

            relationship["source_confidence"] = calibrate_source_confidence_from_validation(
                relationship.get("source_confidence", event.get("source_quality_score", 0.5)),
                validation_status,
                validation_score,
                historical_reliability_multiplier=float(history_metrics.get("historical_reliability_multiplier", 1.0) or 1.0),
            )
            primary_channel = ""
            matched_channels = relationship.get("matched_policy_channels", [])
            if isinstance(matched_channels, list) and matched_channels:
                primary_channel = str(matched_channels[0].get("channel", "")) if isinstance(matched_channels[0], dict) else ""
            if not primary_channel:
                primary_channel = str(relationship.get("policy_channel", ""))
            updated_source_outcome_history = record_source_outcome(
                updated_source_outcome_history,
                history_key,
                validation_status,
                validation_score,
                channel=primary_channel,
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
        raw_impact = weighted_total / total_weight
        cal_scale = float(get_weight("calibration_scale_factor"))
        cal_cap = float(get_weight("calibration_score_abs_cap"))
        impact_score = clamp(raw_impact * cal_scale, -cal_cap, cal_cap)
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
                "cross_window_status": strongest_link[1].get("cross_window_status") if strongest_link else None,
                "cross_window_divergent": strongest_link[1].get("cross_window_divergent", False) if strongest_link else False,
                "channel_reliability_multiplier": strongest_link[1].get("channel_reliability_multiplier", 1.0) if strongest_link else 1.0,
                "channel_outcome_sample_size": strongest_link[1].get("channel_outcome_sample_size", 0) if strongest_link else 0,
                "channel_reliability_score": strongest_link[1].get("channel_reliability_score", 0.0) if strongest_link else 0.0,
                "validation_confidence_delta": strongest_link[1].get("validation_confidence_delta", 0.0) if strongest_link else 0.0,
                "source_conflict": strongest_link[1].get("source_conflict") if strongest_link else False,
                "source_conflict_count": strongest_link[1].get("source_conflict_count") if strongest_link else 0,
                "source_conflict_total_count": strongest_link[1].get("source_conflict_total_count") if strongest_link else 0,
                "source_conflict_score": strongest_link[1].get("source_conflict_score") if strongest_link else 0.0,
                "source_conflict_penalty": strongest_link[1].get("source_conflict_penalty") if strongest_link else 1.0,
                "source_conflict_label": strongest_link[1].get("source_conflict_label") if strongest_link else "aligned",
                "source_fetch_status": strongest_link[1].get("source_fetch_status", "unknown") if strongest_link else "unknown",
                "source": (quote or {}).get("source", "unavailable"),
                "rsi_value": rsi_cache.get(ticker),
                "rsi_factor": strongest_link[1].get("rsi_factor", 1.0) if strongest_link else 1.0,
                "macd": macd_cache.get(ticker),
                "macd_factor": strongest_link[1].get("macd_factor", 1.0) if strongest_link else 1.0,
                "trend": trend_cache.get(ticker),
                "trend_factor": strongest_link[1].get("trend_factor", 1.0) if strongest_link else 1.0,
                "event_cluster_count": ticker_event_counts.get(ticker, 0),
                "event_cluster_factor": strongest_link[1].get("event_cluster_factor", 1.0) if strongest_link else 1.0,
                "atr_value": atr_cache.get(ticker),
                "atr_pct": strongest_link[1].get("atr_pct") if strongest_link else None,
                "atr_factor": strongest_link[1].get("atr_factor", 1.0) if strongest_link else 1.0,
                "sector_correlation_count": strongest_link[1].get("sector_correlation_count", 0) if strongest_link else 0,
                "sector_correlation_factor": strongest_link[1].get("sector_correlation_factor", 1.0) if strongest_link else 1.0,
                "foreign_market_factor": strongest_link[1].get("foreign_market_factor", 1.0) if strongest_link else 1.0,
                "sentiment_momentum": strongest_link[1].get("sentiment_momentum") or "stable" if strongest_link else "stable",
                "sentiment_momentum_factor": strongest_link[1].get("sentiment_momentum_factor", 1.0) if strongest_link else 1.0,
                "currency_factor": strongest_link[1].get("currency_factor", 1.0) if strongest_link else 1.0,
            }
        )
    # Compute signal_strength: composite of confidence × corroboration × validation × technical alignment
    cal_conf_floor = float(get_weight("calibration_confidence_floor"))
    for stock in stocks:
        raw_conf = float(stock.get("relationship_confidence", 0.0) or 0.0)
        # Boost confidence so that non-zero signals are meaningfully above zero
        conf = max(raw_conf, cal_conf_floor) if raw_conf > 0.0 else 0.0
        corrobor = min(1.0, float(stock.get("corroboration_count", 0) or 0) / 3.0)  # 3+ sources = 1.0
        val_mult = float(stock.get("validation_multiplier", 1.0) or 1.0)
        # Technical alignment: count how many indicators agree with direction
        direction = stock.get("impact_direction", "neutral")
        tech_agree = 0
        tech_total = 0
        rsi_val = rsi_cache.get(stock.get("ticker", ""))
        if rsi_val is not None:
            tech_total += 1
            if direction == "positive" and rsi_val < 70:
                tech_agree += 1
            elif direction == "negative" and rsi_val > 30:
                tech_agree += 1
            elif direction == "neutral":
                tech_agree += 0.5
        macd_data = macd_cache.get(stock.get("ticker", ""))
        if macd_data and isinstance(macd_data, dict):
            tech_total += 1
            hist = float(macd_data.get("histogram", 0) or 0)
            if direction == "positive" and hist > 0:
                tech_agree += 1
            elif direction == "negative" and hist < 0:
                tech_agree += 1
            elif direction == "neutral":
                tech_agree += 0.5
        trend_data = trend_cache.get(stock.get("ticker", ""))
        if trend_data and isinstance(trend_data, dict):
            tech_total += 1
            trend_dir = trend_data.get("trend", "neutral")
            if direction == "positive" and trend_dir == "bullish":
                tech_agree += 1
            elif direction == "negative" and trend_dir == "bearish":
                tech_agree += 1
            elif direction == "neutral":
                tech_agree += 0.5
        tech_alignment = tech_agree / tech_total if tech_total > 0 else 0.5
        # Composite: weighted average
        signal_strength = clamp(
            0.35 * conf + 0.25 * corrobor + 0.20 * val_mult + 0.20 * tech_alignment,
            0.0, 1.0,
        )
        stock["signal_strength"] = round(signal_strength, 3)

    # Phase 2: Add technical indicators and trade signals to each stock
    from backend.stocks import compute_bollinger_bands, compute_support_resistance, detect_volume_spike, generate_trade_signal, fetch_ticker_history
    for stock in stocks:
        ticker = stock.get("ticker", "")
        # Fetch OHLC data for Bollinger Bands and support/resistance
        try:
            ticker_hist = fetch_ticker_history(ticker, window="3mo")
        except Exception:
            ticker_hist = None
        ohlc_series = (ticker_hist or {}).get("ohlc_series", [])
        volume_series_raw = (ticker_hist or {}).get("volume_series", [])
        closes = [float(e.get("close", 0) or e.get("value", 0) or 0) for e in ohlc_series if e.get("close") or e.get("value")]
        volumes = [float(v.get("volume", 0) or v.get("value", 0) or 0) for v in volume_series_raw if v.get("volume") or v.get("value")]

        # Compute RSI, MACD, trend, ATR from the same 3mo data
        series_prices = [float(p) for p in (ticker_hist or {}).get("series", []) if p is not None]
        if series_prices and len(series_prices) >= 15:
            rsi_cache[ticker] = compute_rsi(series_prices)
        if series_prices and len(series_prices) >= 35:
            macd_cache[ticker] = compute_macd(series_prices)
        if series_prices and len(series_prices) >= 50:
            trend_cache[ticker] = compute_trend(series_prices)
        if len(ohlc_series) >= 15:
            highs_atr = [float(d["high"]) for d in ohlc_series]
            lows_atr = [float(d["low"]) for d in ohlc_series]
            closes_atr = [float(d["close"]) for d in ohlc_series]
            atr_cache[ticker] = compute_atr(highs_atr, lows_atr, closes_atr)

        # Update stock dict with freshly computed indicators
        stock["rsi_value"] = rsi_cache.get(ticker)
        stock["atr_value"] = atr_cache.get(ticker)
        _macd = macd_cache.get(ticker)
        if isinstance(_macd, dict):
            stock["macd"] = _macd
            stock["macd_histogram"] = _macd.get("histogram")
        _trend = trend_cache.get(ticker)
        if isinstance(_trend, dict):
            stock["trend"] = _trend
            stock["trend_factor"] = _trend.get("trend_strength", 1.0)

        # Bollinger Bands
        bb = compute_bollinger_bands(closes, period=20, std_dev=2.0)
        stock["bollinger"] = bb

        # Support/Resistance
        sr = compute_support_resistance(ohlc_series, lookback=50)
        stock["support_resistance"] = sr

        # Volume spike
        vol = detect_volume_spike(volumes, period=20)
        stock["volume_spike"] = vol

        # Trade signal
        trend_data = trend_cache.get(ticker) or {}
        macd_data = macd_cache.get(ticker) or {}
        rsi_val = rsi_cache.get(ticker)
        trade = generate_trade_signal(
            price=stock.get("price", 0) or 0,
            signal_strength=stock.get("signal_strength", 0),
            impact_direction=stock.get("impact_direction", "neutral"),
            rsi=rsi_val,
            macd_histogram=macd_data.get("histogram") if isinstance(macd_data, dict) else None,
            trend_direction=trend_data.get("trend", "neutral") if isinstance(trend_data, dict) else "neutral",
            atr=atr_cache.get(ticker),
            bb_percent_b=bb.get("percent_b"),
            volume_spike_ratio=vol.get("spike_ratio"),
        )
        stock["trade_signal"] = trade

    # Tag each stock with pinned/portfolio status
    from backend.signals import get_pinned_tickers, get_portfolio_tickers
    _pinned = get_pinned_tickers()
    _portfolio = get_portfolio_tickers()
    for stock in stocks:
        t = stock.get("ticker", "")
        is_pinned = t in _pinned
        is_portfolio = t in _portfolio
        stock["pinned"] = is_pinned or is_portfolio
        stock["in_portfolio"] = is_portfolio
        stock["pin_source"] = "portfolio" if is_portfolio else ("manual" if is_pinned else None)

    # Trading signal decision layer
    from backend.trading_signals import (
        classify_signal, compute_sector_avg_rsi,
        apply_market_regime, deduplicate_by_sector,
        apply_signal_decay,
    )

    # Query existing signal snapshots for signal decay
    _snapshot_ages: dict[str, int] = {}  # ticker -> days_since_signal
    try:
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect(str(BACKEND_DB_PATH), timeout=5)
        _conn.row_factory = _sqlite3.Row
        _today = now_wib().strftime("%Y-%m-%d")
        _rows = _conn.execute(
            """SELECT ticker, snapshot_date,
                      CAST(julianday(?) - julianday(snapshot_date) AS INTEGER) as age_days
               FROM daily_signal_snapshots
               WHERE action != 'IGNORE'
               GROUP BY ticker
               HAVING snapshot_date = MAX(snapshot_date)""",
            (_today,),
        ).fetchall()
        for _r in _rows:
            age = int(_r["age_days"] or 0)
            if age >= 0:
                _snapshot_ages[_r["ticker"]] = age
        _conn.close()
    except Exception:
        pass

    # Compute sector-relative RSI averages for signal quality boost
    sector_avg_rsi = compute_sector_avg_rsi(stocks)

    for stock in stocks:
        stock["trading_signal"] = classify_signal(stock, sector_avg_rsi)

    # Apply signal decay: stale WATCH signals lose strength over time
    for stock in stocks:
        ticker = stock.get("ticker", "")
        age = _snapshot_ages.get(ticker, 0)
        if age > 1:
            ts = stock.get("trading_signal") or {}
            apply_signal_decay(ts, age)
            stock["trading_signal"] = ts

    # Apply IHSG market regime filter (suppress BUY in downtrend)
    # market_index is already in scope from the fetch_market_index() call above
    for stock in stocks:
        ts = stock.get("trading_signal") or {}
        if ts.get("action") == "BUY":
            apply_market_regime(ts, market_index)
            stock["trading_signal"] = ts

    stocks = sort_stocks_by_impact(stocks)

    # Sector deduplication: keep strongest WATCH per sector
    all_signals_for_dedup = []
    for stock in stocks:
        ts = stock.get("trading_signal") or {}
        if ts.get("action") not in ("IGNORE",):
            all_signals_for_dedup.append({
                "ticker": stock.get("ticker"),
                "sector": stock.get("sector", "unknown"),
                **ts,
            })
    deduped = deduplicate_by_sector(all_signals_for_dedup)
    deduped_tickers = {s["ticker"] for s in deduped}
    for stock in stocks:
        ts = stock.get("trading_signal") or {}
        if ts.get("action") not in ("IGNORE",) and stock.get("ticker") not in deduped_tickers:
            stock["trading_signal"] = {
                **ts,
                "action": "IGNORE",
                "reasons": ts.get("reasons", []) + ["Sector dedup — stronger peer selected"],
            }

    # Save signal snapshots for decay tracking and historical evaluation
    _snapshot_date = now_wib().strftime("%Y-%m-%d")
    try:
        import sqlite3 as _snap_db
        _conn = _snap_db.connect(str(BACKEND_DB_PATH), timeout=5)
        for stock in stocks:
            ts = stock.get("trading_signal") or {}
            if ts.get("action") == "IGNORE":
                continue
            _conn.execute(
                """INSERT OR REPLACE INTO daily_signal_snapshots
                   (snapshot_date, ticker, action, time_horizon, signal_tier,
                    entry_price, stop_loss, take_profit, signal_strength,
                    reason_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _snapshot_date,
                    stock.get("ticker", ""),
                    ts.get("action", "IGNORE"),
                    ts.get("time_horizon", "7d"),
                    ts.get("signal_tier", "D"),
                    ts.get("entry_price"),
                    ts.get("stop_loss"),
                    ts.get("take_profit"),
                    ts.get("signal_strength", 0),
                    json.dumps(ts.get("reasons", []), ensure_ascii=False),
                    now_wib().isoformat(timespec="seconds"),
                ),
            )
        _conn.commit()
        _conn.close()
    except Exception:
        pass  # non-critical: don't break dashboard on snapshot failure

    # Build ticker→trading_signal lookup for tier propagation to predictions
    _ticker_signals: dict[str, dict[str, Any]] = {}
    for stock in stocks:
        ts = stock.get("trading_signal") or {}
        if ts.get("action") != "IGNORE":
            _ticker_signals[stock.get("ticker", "")] = ts

    # Phase 3: Log BUY signals to history and send Telegram alerts
    # Uses trading_signal (new system) instead of trade_signal (old system).
    # SELL signals suppressed — 0% hit rate on 24 live predictions.
    _actionable_signals: list[dict[str, Any]] = []
    for stock in stocks:
        ts = stock.get("trading_signal") or {}
        action = ts.get("action", "IGNORE")
        if action != "BUY":
            continue
        sig_record = {
            "ticker": stock.get("ticker", ""),
            "action": action,
            "signal_strength": ts.get("signal_strength", 0),
            "price_at_signal": ts.get("entry_price") or stock.get("price", 0),
            "stop_loss": ts.get("stop_loss"),
            "take_profit": ts.get("take_profit"),
            "risk_reward": None,
            "timeframe": ts.get("time_horizon"),
            "reasons": ts.get("reasons", []),
            "event_headline": stock.get("headline", ""),
            "event_source": stock.get("source", ""),
            # Phase 2: new fields from trading_signal
            "time_horizon": ts.get("time_horizon"),
            "signal_tier": ts.get("signal_tier"),
            "signal_type": ts.get("signal_type"),
            "event_score": ts.get("event_score"),
            "tech_score": ts.get("tech_score"),
            "tech_confirmation_count": ts.get("tech_confirmation_count"),
        }
        _actionable_signals.append(sig_record)
    if _actionable_signals:
        try:
            from backend.signals import log_signal
            for _sig in _actionable_signals:
                log_signal(
                    ticker=_sig["ticker"], action=_sig["action"],
                    signal_strength=_sig["signal_strength"],
                    price_at_signal=_sig["price_at_signal"],
                    stop_loss=_sig["stop_loss"], take_profit=_sig["take_profit"],
                    risk_reward=_sig["risk_reward"], timeframe=_sig["timeframe"],
                    reasons=_sig["reasons"],
                    event_headline=_sig["event_headline"],
                    event_source=_sig["event_source"],
                    signal_source="auto",
                    time_horizon=_sig.get("time_horizon"),
                    signal_tier=_sig.get("signal_tier"),
                    signal_type=_sig.get("signal_type"),
                    event_score=_sig.get("event_score"),
                    tech_score=_sig.get("tech_score"),
                    tech_confirmation_count=_sig.get("tech_confirmation_count"),
                )
        except Exception:
            pass  # non-critical
        try:
            from backend.alerts import check_and_alert
            check_and_alert(_actionable_signals)
        except Exception:
            pass  # non-critical: don't break dashboard on alert failure

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
                "sentiment_confidence": event.get("sentiment_confidence", 0.0),
                "entities": event.get("entities", []),
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
                "confidence_label": event.get("confidence_label", relationship_confidence_label(float(event.get("confidence", 0.0) or 0.0))),
                "window": normalized_window,
                "significance": event.get("significance", 0.0),
                "source_age_hours": event.get("source_age_hours", 0.0),
                "source_freshness_score": event.get("source_freshness_score", 0.0),
                "source_quality_score": event.get("source_quality_score", 0.0),
                "coverage_warning": event.get("coverage_warning", ""),
                "source_fetch_status": str(event.get("source_profile_resolution", "unknown") or "unknown"),
            }
        )

    # Inject trading signal data into formatted_events for tier propagation
    for fe in formatted_events:
        for rel in fe.get("stock_relationships", []):
            tk = rel.get("ticker", "")
            if tk in _ticker_signals:
                ts = _ticker_signals[tk]
                rel["time_horizon"] = ts.get("time_horizon")
                rel["signal_tier"] = ts.get("signal_tier")
                rel["signal_type"] = ts.get("signal_type")
                rel["event_score"] = ts.get("event_score")
                rel["tech_score"] = ts.get("tech_score")
                rel["tech_confirmation_count"] = ts.get("tech_confirmation_count")

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
        "global_markets": {
            "sp500_change": sp500_change,
            "sp500_direction": sp500_dir,
            "nikkei_change": nikkei_change,
            "nikkei_direction": nikkei_dir,
            "foreign_direction": foreign_direction,
            "usd_idr_change": usd_idr_change,
            "usd_idr_direction": usd_idr_dir,
        },
        "sources": sources,
        "source_health_summary": source_health_summary,
        "warnings": warnings,
    }

    with CACHE_LOCK:
        CACHE[cache_key] = {"cached_at": now_wib(), "payload": payload}

    # Persist to SQLite for cold-start recovery
    save_cache_to_db(cache_key, payload)

    # Record predictions for backtest (fire-and-forget)
    try:
        from backend.backtest import record_predictions_from_events
        threading.Thread(
            target=record_predictions_from_events,
            args=(formatted_events, quotes),
            daemon=True,
        ).start()
    except Exception:
        pass

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


@app.get("/app", response_class=HTMLResponse)
def miniapp() -> FileResponse:
    """Telegram Mini App frontend."""
    return FileResponse(MINIAPP_FILE, media_type="text/html", headers={"Cache-Control": "no-store"})


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
def api_get_watchlist(user_id: int | None = None) -> dict[str, Any]:
    if user_id is not None:
        tickers = get_user_watchlist_from_bot_db(user_id)
        if tickers:
            return {"tickers": tickers, "user_id": user_id}
    return {"tickers": get_watchlist()}


@app.get("/api/watchlist/all")
def api_get_all_tickers() -> dict[str, Any]:
    """Return all available tickers with pin/portfolio status."""
    from backend.signals import get_pinned_tickers, get_portfolio_tickers
    pinned = get_pinned_tickers()
    portfolio = get_portfolio_tickers()

    tickers = []
    for ticker in sorted(STOCK_MASTER.keys()):
        info = STOCK_MASTER[ticker]
        is_pinned = ticker in pinned
        is_portfolio = ticker in portfolio
        tickers.append({
            "ticker": ticker,
            "name": info.get("name", ticker.replace(".JK", "")),
            "sector": info.get("sector", "?"),
            "pinned": is_pinned or is_portfolio,  # portfolio tickers auto-pinned
            "in_portfolio": is_portfolio,
            "pin_source": "portfolio" if is_portfolio else ("manual" if is_pinned else None),
        })

    # Sort: pinned first, then alphabetical
    tickers.sort(key=lambda t: (not t["pinned"], t["ticker"]))

    return {
        "tickers": tickers,
        "total": len(tickers),
        "pinned_count": sum(1 for t in tickers if t["pinned"]),
    }


@app.post("/api/watchlist/pin/{ticker}")
def api_pin_ticker(ticker: str) -> dict[str, Any]:
    """Pin a ticker to the top of the watchlist."""
    from backend.signals import pin_ticker
    from backend.utils import normalize_ticker
    ticker = normalize_ticker(ticker)
    if not ticker:
        return {"error": "Invalid ticker"}
    pin_ticker(ticker)
    return {"ok": True, "ticker": ticker, "pinned": True}


@app.delete("/api/watchlist/pin/{ticker}")
def api_unpin_ticker(ticker: str) -> dict[str, Any]:
    """Unpin a ticker from the watchlist."""
    from backend.signals import unpin_ticker
    from backend.utils import normalize_ticker
    ticker = normalize_ticker(ticker)
    if not ticker:
        return {"error": "Invalid ticker"}
    unpin_ticker(ticker)
    return {"ok": True, "ticker": ticker, "pinned": False}


@app.get("/api/dashboard")
def api_dashboard(window: str = DEFAULT_EVENT_WINDOW, user_id: int | None = None) -> dict[str, Any]:
    if user_id is not None:
        tickers = get_user_watchlist_from_bot_db(user_id)
        watchlist = tickers if tickers else get_watchlist()
    else:
        watchlist = get_watchlist()
    payload = build_refresh_payload(watchlist, force=False, window=window)
    dashboard_cues = build_dashboard_cues(payload)
    from backend.nlp import get_nlp_status
    return {"watchlist": watchlist, "reasoning_summary": payload.get("reasoning_summary", {}), "dashboard_cues": dashboard_cues, "nlp_status": get_nlp_status(), "payload": payload}


@app.get("/api/ticker/{ticker}")
def api_ticker_detail(ticker: str, window: str = DEFAULT_EVENT_WINDOW) -> dict[str, Any]:
    data = fetch_ticker_history(ticker, window)
    # Add technical indicators to response
    closes = data.get("series", [])
    prices = [p for p in closes if p is not None]
    if len(prices) >= 35:
        macd = compute_macd(prices)
        if macd:
            data["macd"] = macd
    if len(prices) >= 50:
        trend = compute_trend(prices)
        if trend:
            data["trend"] = trend
    # ATR from OHLC data
    ohlc = data.get("ohlc_series", [])
    if len(ohlc) >= 15:
        highs = [float(d["high"]) for d in ohlc]
        lows = [float(d["low"]) for d in ohlc]
        cls = [float(d["close"]) for d in ohlc]
        atr = compute_atr(highs, lows, cls)
        if atr is not None:
            data["atr"] = atr
            if prices:
                data["atr_pct"] = round((atr / prices[-1]) * 100, 2)
    # RSI
    if len(prices) >= 15:
        rsi = compute_rsi(prices)
        if rsi is not None:
            data["rsi"] = rsi
    # Bollinger Bands
    if len(prices) >= 20:
        from backend.stocks import compute_bollinger_bands, compute_support_resistance, detect_volume_spike
        bb = compute_bollinger_bands(prices, period=20, std_dev=2.0)
        if bb:
            data["bollinger"] = bb
        # Support/Resistance from OHLC
        sr = compute_support_resistance(ohlc, lookback=50)
        if sr:
            data["support_resistance"] = sr
        # Volume spike
        volumes = [float(d.get("volume", 0) or 0) for d in ohlc if d.get("volume")]
        if len(volumes) >= 20:
            vol = detect_volume_spike(volumes, period=20)
            if vol:
                data["volume_spike"] = vol
    return data


@app.put("/api/watchlist")
def api_put_watchlist(payload: WatchlistRequest, user_id: int | None = None) -> dict[str, Any]:
    if user_id is not None:
        save_user_watchlist_to_bot_db(user_id, payload.tickers)
        return {"tickers": payload.tickers, "user_id": user_id}
    tickers = set_watchlist(payload.tickers)
    return {"tickers": tickers}


@app.get("/api/alert_prefs")
def api_get_alert_prefs(user_id: int | None = None) -> dict[str, Any]:
    """Read alert preferences from the bot's SQLite database."""
    defaults = {"alert_min_impact": 0, "alert_categories": [], "alert_quiet_start": -1, "alert_quiet_end": -1}
    if user_id is None:
        return defaults
    conn = sqlite3.connect(str(BOT_DB_PATH))
    try:
        cur = conn.execute(
            "SELECT alert_min_impact, alert_categories, alert_quiet_start, alert_quiet_end FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return defaults
        cats = [c.strip().upper() for c in (row[1] or "").split(",") if c.strip()]
        return {
            "alert_min_impact": row[0] or 0,
            "alert_categories": cats,
            "alert_quiet_start": row[2] if row[2] is not None else -1,
            "alert_quiet_end": row[3] if row[3] is not None else -1,
        }
    except Exception:
        return defaults
    finally:
        conn.close()


@app.put("/api/alert_prefs")
def api_put_alert_prefs(payload: dict[str, Any], user_id: int | None = None) -> dict[str, Any]:
    """Update alert preferences in the bot's SQLite database."""
    if user_id is None:
        return {"error": "user_id required"}
    conn = sqlite3.connect(str(BOT_DB_PATH))
    try:
        # Ensure user exists
        conn.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        updates = []
        params = []
        if "alert_min_impact" in payload:
            updates.append("alert_min_impact = ?")
            params.append(float(payload["alert_min_impact"]))
        if "alert_categories" in payload:
            updates.append("alert_categories = ?")
            cats = payload["alert_categories"]
            params.append(",".join(cats) if isinstance(cats, list) else str(cats))
        if "alert_quiet_start" in payload:
            updates.append("alert_quiet_start = ?")
            params.append(int(payload["alert_quiet_start"]))
        if "alert_quiet_end" in payload:
            updates.append("alert_quiet_end = ?")
            params.append(int(payload["alert_quiet_end"]))
        if updates:
            params.append(user_id)
            conn.execute(f"UPDATE users SET {', '.join(updates)} WHERE user_id = ?", params)
            conn.commit()
        return {"ok": True, "user_id": user_id}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ── Signal History API ────────────────────────────────────────────────────


@app.get("/api/signals/history")
def api_signal_history(
    limit: int = 50,
    action: str | None = None,
    ticker: str | None = None,
    outcome: str | None = None,
    time_horizon: str | None = None,
    signal_tier: str | None = None,
    signal_type: str | None = None,
) -> dict[str, Any]:
    """Query signal history with optional filters."""
    from backend.signals import get_signal_history, get_signal_stats, init_signal_tables
    init_signal_tables()
    signals = get_signal_history(
        limit=limit, action=action, ticker=ticker, outcome=outcome,
        time_horizon=time_horizon, signal_tier=signal_tier, signal_type=signal_type,
    )
    stats = get_signal_stats()
    return {"signals": signals, "stats": stats}


@app.post("/api/signals/resolve")
def api_signal_resolve() -> dict[str, Any]:
    """Resolve pending signals against current prices."""
    from backend.signals import resolve_signals, get_signal_history
    # Get all pending signal tickers
    pending = get_signal_history(limit=500, outcome="pending")
    tickers = list({s["ticker"] for s in pending})
    if not tickers:
        return {"resolved": [], "message": "No pending signals"}

    # Fetch current prices
    from backend.stocks import fetch_live_quote
    prices: dict[str, float] = {}
    for t in tickers:
        try:
            q = fetch_live_quote(t)
            if q and q.get("price"):
                prices[t] = float(q["price"])
        except Exception:
            continue

    resolved = resolve_signals(prices)
    return {"resolved": resolved, "prices_checked": len(prices)}


@app.get("/api/portfolio")
def api_portfolio(
    status: str = "open",
) -> dict[str, Any]:
    """Get portfolio positions and risk summary."""
    from backend.signals import init_signal_tables, _get_conn
    init_signal_tables()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM portfolio WHERE status = ? ORDER BY entry_date DESC", (status,)
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM portfolio LIMIT 0").description]
        positions = [dict(zip(cols, row)) for row in rows]

        # Risk summary
        open_positions = [p for p in positions if p["status"] == "open"]
        total_exposure = sum(p.get("entry_price", 0) * p.get("shares", 0) for p in open_positions)
        sectors: dict[str, int] = {}
        for p in open_positions:
            sec = p.get("sector") or "unknown"
            sectors[sec] = sectors.get(sec, 0) + 1

        # P&L for closed positions
        total_pnl = sum(p.get("pnl") or 0 for p in positions if p["status"] == "closed")
        wins = sum(1 for p in positions if p["status"] == "closed" and (p.get("pnl") or 0) > 0)
        losses = sum(1 for p in positions if p["status"] == "closed" and (p.get("pnl") or 0) < 0)
        closed_count = wins + losses

        return {
            "positions": positions,
            "summary": {
                "open_count": len(open_positions),
                "total_exposure": round(total_exposure, 2),
                "sector_breakdown": sectors,
                "closed_count": closed_count,
                "total_pnl": round(total_pnl, 2),
                "win_rate": round(wins / closed_count, 3) if closed_count > 0 else None,
            },
        }
    finally:
        conn.close()


@app.get("/api/portfolio/live")
def api_portfolio_live() -> dict[str, Any]:
    """Get open positions with live P&L based on current market prices."""
    from backend.signals import init_signal_tables, _get_conn
    from backend.stocks import fetch_stock_quotes
    init_signal_tables()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM portfolio WHERE status = 'open' ORDER BY entry_date DESC"
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM portfolio LIMIT 0").description]
        positions = [dict(zip(cols, row)) for row in rows]

        if not positions:
            return {"positions": [], "summary": {"open_count": 0, "total_invested": 0, "total_current_value": 0, "total_unrealized_pnl": 0, "total_unrealized_pct": 0}}

        # Fetch live prices for all tickers in portfolio
        tickers = list(dict.fromkeys(p["ticker"] for p in positions))
        quotes, _ = fetch_stock_quotes(tickers)
        price_map = {t: q.get("price") for t, q in quotes.items() if q.get("price")}

        total_invested = 0.0
        total_current_value = 0.0

        enriched = []
        for p in positions:
            entry = p.get("entry_price", 0)
            shares = p.get("shares", 0)
            direction = p.get("direction", "long")
            invested = entry * shares
            current_price = price_map.get(p["ticker"])
            lots = shares // 100

            pos: dict[str, Any] = {
                "id": p["id"],
                "ticker": p["ticker"],
                "direction": direction,
                "lots": lots,
                "shares": shares,
                "entry_price": entry,
                "invested": round(invested, 2),
                "stop_loss": p.get("stop_loss"),
                "take_profit": p.get("take_profit"),
                "sector": p.get("sector"),
                "entry_date": p.get("entry_date"),
            }

            if current_price and current_price > 0:
                if direction == "long":
                    pnl = (current_price - entry) * shares
                else:
                    pnl = (entry - current_price) * shares
                pnl_pct = (pnl / invested * 100) if invested else 0
                current_value = current_price * shares
                pos["current_price"] = round(current_price, 2)
                pos["unrealized_pnl"] = round(pnl, 2)
                pos["unrealized_pct"] = round(pnl_pct, 3)
                pos["current_value"] = round(current_value, 2)
                total_invested += invested
                total_current_value += current_value
            else:
                pos["current_price"] = None
                pos["unrealized_pnl"] = None
                pos["unrealized_pct"] = None
                pos["current_value"] = None
                total_invested += invested
                total_current_value += invested  # assume no change if price unavailable

            enriched.append(pos)

        total_unrealized = total_current_value - total_invested
        total_unrealized_pct = (total_unrealized / total_invested * 100) if total_invested else 0

        # Closed P&L for all-time summary
        closed_rows = conn.execute(
            "SELECT pnl FROM portfolio WHERE status = 'closed'"
        ).fetchall()
        realized_pnl = sum(r[0] or 0 for r in closed_rows)
        closed_wins = sum(1 for r in closed_rows if (r[0] or 0) > 0)
        closed_losses = sum(1 for r in closed_rows if (r[0] or 0) < 0)
        closed_total = closed_wins + closed_losses

        return {
            "positions": enriched,
            "summary": {
                "open_count": len(enriched),
                "total_invested": round(total_invested, 2),
                "total_current_value": round(total_current_value, 2),
                "total_unrealized_pnl": round(total_unrealized, 2),
                "total_unrealized_pct": round(total_unrealized_pct, 3),
                "realized_pnl": round(realized_pnl, 2),
                "all_time_pnl": round(realized_pnl + total_unrealized, 2),
                "closed_trades": closed_total,
                "win_rate": round(closed_wins / closed_total, 3) if closed_total else None,
            },
        }
    finally:
        conn.close()


@app.get("/api/portfolio/history")
def api_portfolio_history(limit: int = 50) -> dict[str, Any]:
    """Get closed positions (trade history)."""
    from backend.signals import init_signal_tables, _get_conn
    init_signal_tables()
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM portfolio WHERE status = 'closed' ORDER BY exit_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM portfolio LIMIT 0").description]
        closed = [dict(zip(cols, row)) for row in rows]

        total_pnl = sum(p.get("pnl") or 0 for p in closed)
        wins = sum(1 for p in closed if (p.get("pnl") or 0) > 0)
        losses = sum(1 for p in closed if (p.get("pnl") or 0) < 0)
        count = wins + losses

        return {
            "trades": closed,
            "summary": {
                "total_trades": count,
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / count, 3) if count else None,
                "total_pnl": round(total_pnl, 2),
                "avg_pnl": round(total_pnl / count, 2) if count else 0,
            },
        }
    finally:
        conn.close()


@app.post("/api/portfolio/reset")
def api_portfolio_reset() -> dict[str, Any]:
    """Delete all portfolio positions (open and closed)."""
    from backend.signals import init_signal_tables, _get_conn
    init_signal_tables()
    conn = _get_conn()
    try:
        count = conn.execute("SELECT COUNT(*) FROM portfolio").fetchone()[0]
        conn.execute("DELETE FROM portfolio")
        conn.commit()
        return {"ok": True, "deleted": count}
    finally:
        conn.close()


@app.post("/api/portfolio/position")
def api_portfolio_add(payload: dict[str, Any]) -> dict[str, Any]:
    """Add a position to the portfolio."""
    from backend.signals import init_signal_tables, _get_conn
    init_signal_tables()

    ticker = payload.get("ticker")
    direction = payload.get("direction", "long")
    entry_price = payload.get("entry_price")
    shares = payload.get("shares", 0)
    stop_loss = payload.get("stop_loss")
    take_profit = payload.get("take_profit")
    signal_id = payload.get("signal_id")
    sector = payload.get("sector")

    if not ticker or not entry_price:
        return {"error": "ticker and entry_price are required"}

    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT INTO portfolio (ticker, direction, entry_price, shares, stop_loss, take_profit, signal_id, sector)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, direction, entry_price, shares, stop_loss, take_profit, signal_id, sector),
        )
        conn.commit()
        return {"ok": True, "id": cur.lastrowid}
    finally:
        conn.close()


@app.put("/api/portfolio/position/{position_id}")
def api_portfolio_close(position_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Close a portfolio position."""
    from backend.signals import init_signal_tables, _get_conn
    init_signal_tables()

    exit_price = payload.get("exit_price")
    if not exit_price:
        return {"error": "exit_price is required"}

    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM portfolio WHERE id = ?", (position_id,)).fetchone()
        if not row:
            return {"error": "Position not found"}

        cols = [d[0] for d in conn.execute("SELECT * FROM portfolio LIMIT 0").description]
        pos = dict(zip(cols, row))
        entry = pos["entry_price"]
        direction = pos["direction"]

        if direction == "long":
            pnl = (exit_price - entry) * pos["shares"]
            pnl_pct = (exit_price - entry) / entry * 100 if entry else 0
        else:
            pnl = (entry - exit_price) * pos["shares"]
            pnl_pct = (entry - exit_price) / entry * 100 if entry else 0

        conn.execute(
            "UPDATE portfolio SET status = 'closed', exit_price = ?, exit_date = datetime('now'), pnl = ?, pnl_pct = ? WHERE id = ?",
            (exit_price, round(pnl, 2), round(pnl_pct, 3), position_id),
        )
        conn.commit()
        return {"ok": True, "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 3)}
    finally:
        conn.close()


@app.post("/api/refresh")
def api_refresh(payload: RefreshRequest) -> JSONResponse:
    result = build_refresh_payload(payload.tickers, force=payload.force, window=payload.window)
    return JSONResponse(result)


@app.get("/api/nlp_status")
def api_nlp_status() -> dict[str, Any]:
    """Return current NLP model status."""
    from backend.nlp import get_nlp_status
    return get_nlp_status()


@app.get("/api/backtest")
def api_backtest(window_days: int = 30, origin: str = "all") -> dict[str, Any]:
    """Return backtest accuracy metrics."""
    from backend.backtest import compute_accuracy_metrics
    return compute_accuracy_metrics(window_days=window_days, origin=origin)


@app.get("/api/calibration/report")
def api_calibration_report(window_days: int = 30, origin: str = "live", min_samples: int = 5) -> dict[str, Any]:
    """Return calibration health report: source accuracy, category calibration, recommendations."""
    from backend.backtest import (
        compute_accuracy_metrics, compute_source_accuracy, compute_category_calibration,
    )
    metrics = compute_accuracy_metrics(window_days=window_days, origin=origin)
    source_acc = compute_source_accuracy(window_days=window_days, min_samples=min_samples)
    cat_cal = compute_category_calibration(window_days=window_days, min_samples=min_samples)

    # Recommendations
    recs = []
    live_edge = metrics.get("baseline", {}).get("edge_vs_neutral", 0)
    if live_edge < 0:
        recs.append(f"Strict mode ON: live edge is {live_edge:.1%} vs neutral baseline")
    neg_stats = metrics.get("by_direction", {}).get("negative", {})
    if neg_stats.get("total", 0) > 0 and neg_stats.get("hit_rate", 0) == 0:
        recs.append("SELL signals suppressed: 0% accuracy on negative predictions")
    for src, stats in source_acc.items():
        if stats["hit_rate"] < 0.3 and stats["total"] >= 10:
            recs.append(f"Source '{src}' underperforming: {stats['hit_rate']:.0%} hit rate ({stats['total']} samples)")
    for cat, stats in cat_cal.items():
        if stats["calibration_multiplier"] < 0.7:
            recs.append(f"Category '{cat}' dampened: {stats['calibration_multiplier']:.2f}x multiplier")
        elif stats["calibration_multiplier"] > 1.3:
            recs.append(f"Category '{cat}' boosted: {stats['calibration_multiplier']:.2f}x multiplier")

    return {
        "overall": {
            "hit_rate": metrics.get("hit_rate", 0),
            "baseline": metrics.get("baseline", {}).get("neutral_hit_rate", 0),
            "edge": live_edge,
            "total": metrics.get("total_predictions", 0),
        },
        "by_source_type": source_acc,
        "by_category": cat_cal,
        "by_signal_type": metrics.get("by_signal_type", {}),
        "by_time_horizon": metrics.get("by_time_horizon", {}),
        "recommendations": recs,
    }


@app.get("/api/signals/daily-summary")
def api_daily_summary(limit: int = 3, include_watch: bool = True) -> dict[str, Any]:
    """Return top actionable signals grouped by time horizon (1d/7d/30d)."""
    from backend.trading_signals import rank_trade_signals
    from backend.backtest import compute_accuracy_metrics

    watchlist = get_watchlist()
    payload = build_refresh_payload(watchlist, force=False, window="1d")
    stocks = payload.get("stocks", [])

    # Extract signals from stocks
    all_signals = []
    for stock in stocks:
        ts = stock.get("trading_signal") or {}
        action = ts.get("action", "IGNORE")
        if action == "IGNORE":
            continue
        if not include_watch and action == "WATCH":
            continue
        all_signals.append({
            "ticker": stock.get("ticker", ""),
            "name": stock.get("name", ""),
            "price": stock.get("price"),
            "action": action,
            "time_horizon": ts.get("time_horizon", "7d"),
            "signal_tier": ts.get("signal_tier", "D"),
            "signal_strength": ts.get("signal_strength", 0),
            "event_score": ts.get("event_score", 0),
            "tech_score": ts.get("tech_score", 0),
            "tech_confirmation_count": ts.get("tech_confirmation_count", 0),
            "entry_price": ts.get("entry_price"),
            "stop_loss": ts.get("stop_loss"),
            "take_profit": ts.get("take_profit"),
            "reasons": ts.get("reasons", []),
            "invalidation": ts.get("invalidation", ""),
        })

    # Group by horizon
    by_horizon: dict[str, list] = {"1d": [], "7d": [], "30d": []}
    for sig in all_signals:
        horizon = sig.get("time_horizon", "7d")
        if horizon in by_horizon:
            by_horizon[horizon].append(sig)

    # Sort each horizon group and take top N
    for horizon in by_horizon:
        by_horizon[horizon] = rank_trade_signals(by_horizon[horizon])[:limit]

    # Calibration context
    metrics = compute_accuracy_metrics(window_days=30, origin="live")

    return {
        "horizons": by_horizon,
        "total_signals": len(all_signals),
        "accuracy": {
            "hit_rate": metrics.get("hit_rate", 0),
            "baseline": metrics.get("baseline", {}).get("neutral_hit_rate", 0),
            "edge": metrics.get("baseline", {}).get("edge_vs_neutral", 0),
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/signals/ticker/{ticker}")
def api_signal_ticker(ticker: str, window: str = "7d") -> dict[str, Any]:
    """Return detailed signal explanation for one ticker."""
    from backend.signals import init_signal_tables
    init_signal_tables()

    ticker = ticker.upper()
    payload = build_refresh_payload([ticker], force=False, window=window)
    stocks = payload.get("stocks", [])

    # Find the matching stock
    stock_data = None
    for s in stocks:
        if s.get("ticker", "").upper() == ticker:
            stock_data = s
            break

    if not stock_data:
        return {"error": f"Ticker {ticker} not found", "ticker": ticker}

    trading_signal = stock_data.get("trading_signal") or {}

    # Extract event context
    events = stock_data.get("events", [])
    top_event = events[0] if events else {}
    event_context = {
        "headline": top_event.get("headline", ""),
        "source": top_event.get("source", ""),
        "categories": top_event.get("categories", []),
        "sentiment": top_event.get("sentiment", ""),
        "event_score": top_event.get("event_score", 0),
    }

    return {
        "ticker": ticker,
        "name": stock_data.get("name", ""),
        "price": stock_data.get("price"),
        "trading_signal": trading_signal,
        "event_context": event_context,
    }


@app.post("/api/backtest/backfill")
def api_backtest_backfill() -> dict[str, Any]:
    """Backfill predictions from cached events + Yahoo Finance history."""
    from backend.backtest import backfill_from_cache
    return backfill_from_cache()


class WebBackfillRequest(BaseModel):
    sources: list[str] = Field(default_factory=lambda: ["wayback", "archives", "newsapi"])
    from_date: str = "20260101"
    to_date: str = "20260609"
    max_articles: int = 300
    dry_run: bool = True
    min_timestamp_confidence: float = 0.8


@app.post("/api/backtest/backfill-web")
def api_backtest_backfill_web(body: WebBackfillRequest) -> dict[str, Any]:
    """Collect historical articles from web archives and import into staging.

    Pipeline: collect → filter for political relevance → stage into
    historical_events table → optionally replay through scoring pipeline.

    Sources: "wayback" (Wayback Machine CDX), "archives" (news site scrapes),
    "newsapi" (requires POLSTOCK_NEWSAPI_KEY env var).
    """
    from backend.backfill import collect_historical_articles, filter_political_articles
    from backend.backtest import import_historical_articles

    # 1. Collect from web sources
    collection = collect_historical_articles(
        sources=body.sources,
        from_date=body.from_date,
        to_date=body.to_date,
        max_articles=body.max_articles,
    )
    raw_articles = collection["articles"]

    # 2. Filter for political/economic relevance
    filtered = filter_political_articles(raw_articles, min_keyword_hits=1)

    # 3. Stage into historical_events table
    import_result = import_historical_articles(
        filtered,
        dry_run=body.dry_run,
        min_timestamp_confidence=body.min_timestamp_confidence,
    )

    return {
        "collection_stats": collection["stats"],
        "raw_articles": len(raw_articles),
        "politically_filtered": len(filtered),
        "import_result": import_result,
    }


class ReplayRequest(BaseModel):
    max_events: int = 100
    window: str = "7d"


@app.post("/api/backtest/replay-historical")
def api_replay_historical(body: ReplayRequest) -> dict[str, Any]:
    """Replay staged historical events through the scoring pipeline.

    Reads accepted articles from historical_events, runs them through
    analyze_article(), records predictions with origin='historical_backfill',
    and resolves outcomes against Yahoo Finance historical prices.
    """
    from backend.backfill import replay_historical_events
    return replay_historical_events(
        max_events=body.max_events,
        window=body.window,
    )


@app.post("/api/backtest/historical-import")
def api_historical_backfill_import(body: HistoricalBackfillRequest) -> dict[str, Any]:
    """Validate/import historical internet articles into a staging table.

    Dry-run defaults to true. This endpoint does not replay items into live
    prediction metrics; it stages only timestamp/provenance-safe records.
    """
    from backend.backtest import import_historical_articles
    return import_historical_articles(
        body.articles,
        dry_run=body.dry_run,
        min_timestamp_confidence=body.min_timestamp_confidence,
    )


@app.post("/api/backtest/resolve")
def api_backtest_resolve() -> dict[str, Any]:
    """Manually trigger outcome resolution for pending predictions."""
    from backend.backtest import resolve_pending_outcomes
    resolved = resolve_pending_outcomes()
    return {"resolved": resolved}


@app.get("/api/backtest/suggestions")
def api_backtest_suggestions(min_samples: int = 10) -> dict[str, Any]:
    """Return weight adjustment suggestions based on backtest data."""
    from backend.backtest import suggest_weight_adjustments
    return suggest_weight_adjustments(min_samples=min_samples)


@app.get("/api/backtest/indicator-analysis")
def api_indicator_analysis(min_samples: int = 5) -> dict[str, Any]:
    """Analyze per-indicator effectiveness and suggest auto-tune adjustments."""
    from backend.backtest import analyze_indicator_effectiveness
    return analyze_indicator_effectiveness(min_samples=min_samples)


@app.get("/api/weights")
def api_get_weights() -> dict[str, Any]:
    """Return current scoring weights (defaults + overrides)."""
    return {
        "weights": get_all_weights(),
        "overrides": get_overrides(),
        "defaults": dict(__import__("backend.weights", fromlist=["DEFAULTS"]).DEFAULTS),
    }


class WeightOverride(BaseModel):
    weights: dict[str, float | int]


@app.post("/api/weights")
def api_apply_weights(body: WeightOverride) -> dict[str, Any]:
    """Apply weight overrides. Partial updates supported."""
    result = apply_overrides(body.weights)
    return {"status": "ok", "applied": result, "weights": get_all_weights()}


@app.post("/api/weights/auto-tune")
def api_auto_tune(min_samples: int = 5) -> dict[str, Any]:
    """Run indicator analysis and auto-apply suggested weight adjustments."""
    from backend.backtest import analyze_indicator_effectiveness
    analysis = analyze_indicator_effectiveness(min_samples=min_samples)

    if not analysis.get("ready"):
        return {"status": "not_ready", "reason": analysis.get("reason"), "applied": {}}

    auto_tune = analysis.get("auto_tune", {})
    if not auto_tune:
        return {"status": "no_changes", "reason": "All indicators within normal range", "applied": {}}

    # Apply suggested weights
    to_apply = {k: v["suggested"] for k, v in auto_tune.items()}
    result = apply_overrides(to_apply)

    return {
        "status": "applied",
        "applied": result,
        "changes": auto_tune,
        "weights": get_all_weights(),
    }


@app.post("/api/weights/reset")
def api_reset_weights() -> dict[str, Any]:
    """Reset all weights to defaults."""
    reset_to_defaults()
    return {"status": "reset", "weights": get_all_weights()}


@app.get("/api/predictions/history")
def api_prediction_history(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List prediction history with outcomes."""
    from backend.backtest import list_predictions
    return list_predictions(status=status, limit=limit, offset=offset)


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
    """Initialize backend DB, load cached data, and warm the cache in background."""
    init_backend_db()
    # Initialize backtest tables and start outcome resolver
    try:
        from backend.backtest import init_backtest_db, start_outcome_resolver
        init_backtest_db()
        start_outcome_resolver(interval_seconds=900)  # 15 min for faster data collection
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Backtest init failed: {e}")
    # Initialize signal history and portfolio tables
    try:
        from backend.signals import init_signal_tables
        init_signal_tables()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Signal tables init failed: {e}")
    # Load persisted cache so dashboard has data immediately
    persisted = load_cache_from_db()
    if persisted:
        with CACHE_LOCK:
            CACHE.update(persisted)
    # Warm NLP models in background to avoid slow first request
    def _warm_nlp():
        try:
            from backend.nlp import _load_sentiment, _load_ner
            _load_sentiment()
            _load_ner()
        except Exception as e:
            logging.getLogger(__name__).warning(f"NLP warmup failed: {e}")

    threading.Thread(target=_warm_nlp, daemon=True).start()
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
