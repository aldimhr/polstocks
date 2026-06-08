"""Constants, stock data, sector maps, and configuration for PolStock."""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

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




