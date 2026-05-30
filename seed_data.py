#!/usr/bin/env python3
"""Seed the politics->stock mapper with public Indonesian sources.

Sources used:
- Setkab homepage headlines (scraped live)
- Setkab article: "Inilah Kementerian Negara Kabinet Merah Putih"

The output is written to data.json.
"""

from __future__ import annotations

import html
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
DATA_FILE = BASE / "data.json"
HEADERS = {"User-Agent": "Mozilla/5.0"}
NOW = datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def fetch(url: str) -> str:
    return requests.get(url, headers=HEADERS, timeout=30).text


def clean(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def scrape_setkab_homepage():
    html_text = fetch("https://setkab.go.id/")
    items = []
    seen = set()
    # Grab article cards on the homepage (h1/h2 titles inside anchors), in page order.
    for href, inner in re.findall(r'<a href="([^"]+)"[^>]*>(.*?)</a>', html_text, flags=re.S | re.I):
        if "setkab.go.id" not in href:
            continue
        title_match = re.search(r'<h[12][^>]*>(.*?)</h[12]>', inner, flags=re.S | re.I)
        if not title_match:
            continue
        title = clean(title_match.group(1))
        if not title or title in seen:
            continue
        seen.add(title)
        items.append({"title": title, "url": html.unescape(href)})
    return items


def scrape_cabinet_article():
    url = "https://setkab.go.id/inilah-kementerian-negara-kabinet-merah-putih/"
    html_text = fetch(url)
    paras = [clean(p) for p in re.findall(r"<p>(.*?)</p>", html_text, flags=re.S | re.I)]
    return {"url": url, "paragraphs": [p for p in paras if p]}


def build_data():
    home = scrape_setkab_homepage()
    cabinet = scrape_cabinet_article()

    # People / circles
    people = [
        {
            "id": new_id("person"),
            "name": "Prabowo Subianto",
            "role": "President of Indonesia",
            "circle": "Kabinet Merah Putih",
            "notes": "Central political signal for agenda and sector priorities.",
            "created_at": NOW,
        },
        {
            "id": new_id("person"),
            "name": "Gibran Rakabuming Raka",
            "role": "Vice President of Indonesia",
            "circle": "Kabinet Merah Putih",
            "notes": "Useful for monitoring youth, digital, and regional policy messaging.",
            "created_at": NOW,
        },
        {
            "id": new_id("person"),
            "name": "Rosan Roeslani",
            "role": "Minister of Investment and downstream policy",
            "circle": "economic team",
            "notes": "Appears in the scraped Setkab homepage as a current public signal around investment and trade.",
            "created_at": NOW,
        },
    ]

    # Cabinet structure from scraped article text.
    cabinet_roles = [
        ("Menteri Koordinator Bidang Pembangunan Kewilayahan", "regional development and infrastructure", "physical development / transport / housing"),
        ("Menteri Koordinator Bidang Pemberdayaan Masyarakat", "community empowerment", "SMEs / social policy / migrant workers / creative economy"),
        ("Menteri Koordinator Bidang Pangan", "food coordination", "agriculture / forestry / fisheries / nutrition"),
    ]
    for name, role, circle in cabinet_roles:
        people.append(
            {
                "id": new_id("person"),
                "name": name,
                "role": role,
                "circle": circle,
                "notes": "Scraped from Setkab article 'Inilah Kementerian Negara Kabinet Merah Putih'.",
                "created_at": NOW,
            }
        )

    # Policies / agenda signals
    policy_seed = [
        {
            "title": home[0]["title"],
            "theme": "state visit / diplomacy / defense",
            "description": "Prabowo ends the official state visit in France; useful as a signal for defense, trade, and foreign-investment sentiment.",
            "url": home[0]["url"],
        },
        {
            "title": home[1]["title"],
            "theme": "investment / trade",
            "description": "Business council Indonesia-France as a channel for investment and two-way trade.",
            "url": home[1]["url"],
        },
        {
            "title": home[3]["title"],
            "theme": "defense / trade agreement",
            "description": "Defense cooperation and IEU-CEPA discussions; higher-level signal for industrial, export, and procurement themes.",
            "url": home[3]["url"],
        },
        {
            "title": home[4]["title"],
            "theme": "defense / strategic cooperation",
            "description": "Rafale and wider strategic cooperation headline, another defense procurement signal.",
            "url": home[4]["url"],
        },
        {
            "title": home[5]["title"],
            "theme": "foreign policy / macro uncertainty",
            "description": "Broader partnership framing, useful as a soft signal for exporters and market-sensitive sectors.",
            "url": home[5]["url"],
        },
        {
            "title": home[10]["title"],
            "theme": "cabinet structure",
            "description": "This article exposes the current sectoral coordination map and the ministries attached to each cluster.",
            "url": "https://setkab.go.id/inilah-kementerian-negara-kabinet-merah-putih/",
        },
        {
            "title": "Food coordination cluster",
            "theme": "food security",
            "description": cabinet["paragraphs"][2],
            "url": cabinet["url"],
        },
        {
            "title": "Regional development coordination cluster",
            "theme": "infrastructure / housing / transport",
            "description": cabinet["paragraphs"][0],
            "url": cabinet["url"],
        },
        {
            "title": "Community empowerment coordination cluster",
            "theme": "SMEs / social policy / creative economy",
            "description": cabinet["paragraphs"][1],
            "url": cabinet["url"],
        },
        {
            "title": home[15]["title"],
            "theme": "tourism / consumer / transport",
            "description": "Tourism as an economic and social engine; useful for airlines, hotels, travel, and consumer names.",
            "url": home[15]["url"],
        },
        {
            "title": home[16]["title"],
            "theme": "talent / education / productivity",
            "description": "Corporate university and integrated competency building; a public productivity signal.",
            "url": home[16]["url"],
        },
        {
            "title": home[17]["title"],
            "theme": "border regions / infrastructure / inclusion",
            "description": "Protection and inclusion for border and indigenous communities, often tied to infrastructure and telecom needs.",
            "url": home[17]["url"],
        },
        {
            "title": home[18]["title"],
            "theme": "public-sector efficiency / digitalization",
            "description": "Debottlenecking in the public sector; often a signal for process and digitization reforms.",
            "url": home[18]["url"],
        },
    ]

    policies = []
    for p in policy_seed:
        policies.append(
            {
                "id": new_id("policy"),
                "title": p["title"],
                "theme": p["theme"],
                "description": p["description"],
                "date": "2026-05-30",
                "source_url": p["url"],
                "created_at": NOW,
            }
        )

    stocks = [
        # Banking / consumption / policy transmission
        ("BBCA", "Bank Central Asia", "banking", "liquid core name; baseline policy transmission beneficiary"),
        ("BBRI", "Bank Rakyat Indonesia", "banking", "SME / community empowerment / credit transmission"),
        ("BMRI", "Bank Mandiri", "banking", "state-linked policy / infrastructure financing"),
        ("BBNI", "Bank Negara Indonesia", "banking", "trade and corporate financing"),
        ("BBTN", "Bank Tabungan Negara", "banking", "housing / public development sensitivity"),
        # Infrastructure / construction / materials
        ("ADHI", "Adhi Karya", "infrastructure", "state project sensitivity"),
        ("PTPP", "PP (Persero) Tbk", "infrastructure", "state project sensitivity"),
        ("WIKA", "Wijaya Karya", "infrastructure", "state project sensitivity"),
        ("WSKT", "Waskita Karya", "infrastructure", "high policy beta / project execution"),
        ("WTON", "Wijaya Karya Beton", "materials", "construction cycle exposure"),
        ("JSMR", "Jasa Marga", "transport infrastructure", "roads / logistics / regional development"),
        ("SMGR", "Semen Indonesia", "materials", "construction / housing / infrastructure"),
        ("INTP", "Indocement", "materials", "construction / housing / infrastructure"),
        # Food / agriculture
        ("INDF", "Indofood Sukses Makmur", "consumer staples", "food security and domestic demand"),
        ("ICBP", "Indofood CBP", "consumer staples", "food security and domestic demand"),
        ("CPIN", "Charoen Pokphand Indonesia", "poultry", "food security / protein supply"),
        ("JPFA", "Japfa Comfeed Indonesia", "poultry", "food security / feed / protein supply"),
        ("AALI", "Astra Agro Lestari", "plantation", "agri / commodity / food supply"),
        ("SMAR", "Sinar Mas Agro Resources & Technology", "plantation", "agri / commodity / food supply"),
        ("LSIP", "London Sumatra Indonesia", "plantation", "agri / food supply"),
        # Mining / downstream / energy
        ("ADRO", "Adaro Energy Indonesia", "energy", "coal / export / commodity policy"),
        ("PTBA", "Bukit Asam", "energy", "state-linked coal / energy policy"),
        ("ANTM", "Aneka Tambang", "metals", "downstreaming / metals / nickel"),
        ("INCO", "Vale Indonesia", "metals", "nickel / downstreaming / EV chain"),
        ("TINS", "Timah", "metals", "mining policy / commodity"),
        ("MDKA", "Merdeka Copper Gold", "metals", "resource downstreaming"),
        ("MBMA", "Merdeka Battery Materials", "metals", "battery chain / downstreaming"),
        ("PGEO", "Pertamina Geothermal Energy", "renewables", "energy transition / public policy"),
        ("MEDC", "Medco Energi", "energy", "energy policy / gas / transition"),
        # Telecom / digital / process efficiency
        ("TLKM", "Telkom Indonesia", "telecom", "digital government / public-sector efficiency"),
        ("ISAT", "Indosat", "telecom", "digitalization / inclusive connectivity"),
        ("EXCL", "XL Axiata", "telecom", "regional inclusion / border connectivity"),
        # Travel / tourism
        ("GIAA", "Garuda Indonesia", "transport", "tourism / mobility / state travel"),
        ("PANR", "Panorama Sentrawisata", "tourism", "tourism / consumer recovery"),
        ("MAPI", "Mitra Adiperkasa", "retail", "consumer / household spending / SME spillovers"),
        ("ACES", "Ace Hardware Indonesia", "retail", "SME / household / consumer spending"),
        ("UNVR", "Unilever Indonesia", "consumer staples", "household spending / mass market demand"),
        # Conglomerates / trade / broad exposure
        ("ASII", "Astra International", "conglomerate", "trade / consumer / industrial exposure"),
        ("UNTR", "United Tractors", "heavy equipment", "infrastructure / mining machinery / capex cycle"),
    ]

    stocks = [
        {
            "id": new_id("stock"),
            "ticker": t,
            "name": n,
            "sector": s,
            "current_condition": c,
            "notes": "Seeded from public-policy mapping heuristics.",
            "created_at": NOW,
        }
        for t, n, s, c in stocks
    ]

    # Heuristic mappings from agenda -> stocks.
    def link(policy_title: str, tickers: list[str], actor: str, impact="positive", strength=4, confidence=3, rationale=""):
        policy = next(p for p in policies if p["title"] == policy_title)
        for ticker in tickers:
            stock = next(s for s in stocks if s["ticker"] == ticker)
            links.append(
                {
                    "id": new_id("link"),
                    "policy_id": policy["id"],
                    "ticker": stock["ticker"],
                    "actor": actor,
                    "market_condition": stock["current_condition"],
                    "impact": impact,
                    "strength": strength,
                    "confidence": confidence,
                    "rationale": rationale or f"{actor} -> {policy_title} -> {ticker}",
                    "created_at": NOW,
                }
            )

    links = []
    # France / defense / investment / trade package
    link(
        home[0]["title"],
        ["ASII", "UNTR", "BBCA", "BMRI", "BBRI", "ADRO", "ANTM", "MEDC"],
        "Prabowo Subianto",
        rationale="State visit and strategic diplomacy can lift risk appetite for exporters, industrials, and financing proxies.",
    )
    link(
        home[1]["title"],
        ["BBCA", "BBRI", "BMRI", "BBNI", "ADHI", "PTPP", "WIKA", "SMGR", "INTP"],
        "Rosan Roeslani",
        rationale="Investment and two-way trade usually benefit banks, contractors, and materials names.",
    )
    link(
        home[3]["title"],
        ["ASII", "UNTR", "BBCA", "BBRI", "ANTM", "INCO", "MDKA", "MBMA", "PGEO"],
        "Prabowo Subianto",
        rationale="Defense cooperation plus trade talks raise attention to industrial supply chains and commodity-linked names.",
    )
    link(
        home[4]["title"],
        ["ASII", "UNTR", "ANTM", "INCO", "MDKA", "MBMA"],
        "Prabowo Subianto",
        rationale="Rafale and strategic synergy are classic defense/industrial headlines; listed exposure is indirect but relevant.",
    )
    link(
        home[5]["title"],
        ["BBCA", "BMRI", "BBRI", "ASII", "UNTR", "ANTM", "INCO", "MEDC"],
        "Prabowo Subianto",
        rationale="Macro uncertainty generally favors large liquid names and exporters over small domestics.",
    )

    # Cabinet structure clusters
    link(
        "Regional development coordination cluster",
        ["ADHI", "PTPP", "WIKA", "WSKT", "WTON", "JSMR", "SMGR", "INTP"],
        "Menteri Koordinator Bidang Pembangunan Kewilayahan",
        rationale="Regional development, housing, transport, and public works are direct beneficiaries for contractors and materials.",
        strength=5,
    )
    link(
        "Food coordination cluster",
        ["INDF", "ICBP", "CPIN", "JPFA", "AALI", "SMAR", "LSIP"],
        "Menteri Koordinator Bidang Pangan",
        rationale="Food security maps to staples, poultry, and plantation supply chains.",
        strength=5,
    )
    link(
        "Community empowerment coordination cluster",
        ["BBRI", "BBTN", "BMRI", "ASII", "UNTR", "MAPI", "ACES", "UNVR"],
        "Menteri Koordinator Bidang Pemberdayaan Masyarakat",
        rationale="SME and household-focused policy tends to support banks and consumer names.",
        strength=4,
    )

    # Tourism / competency / public-sector efficiency
    link(
        home[15]["title"],
        ["GIAA", "PANR", "ASII", "UNTR", "BBRI"],
        "Kabinet Merah Putih",
        rationale="Tourism recovery lifts mobility, travel services, and broad domestic demand.",
    )
    link(
        home[16]["title"],
        ["TLKM", "ISAT", "EXCL", "BBCA"],
        "Kabinet Merah Putih",
        rationale="Corporate university and integrated competency building suggest productivity and digital transformation spending.",
        impact="positive",
        strength=3,
    )
    link(
        home[17]["title"],
        ["TLKM", "ISAT", "EXCL", "JSMR", "BBRI"],
        "Kabinet Merah Putih",
        rationale="Border and indigenous-community inclusion usually needs connectivity, road access, and financial inclusion.",
        impact="positive",
        strength=4,
    )
    link(
        home[18]["title"],
        ["TLKM", "ISAT", "EXCL", "BBCA", "BMRI"],
        "Kabinet Merah Putih",
        rationale="Public-sector debottlenecking points toward process reform and digitization; telecoms and banks are logical proxies.",
        impact="positive",
        strength=4,
    )

    notes = [
        {
            "id": new_id("note"),
            "title": "Public-source seed",
            "body": "Seeded from live scraping of Setkab homepage headlines and the Setkab article about Kabinet Merah Putih ministry clusters.",
            "tags": "source, setkab, scraping",
            "created_at": NOW,
        },
        {
            "id": new_id("note"),
            "title": "How to read the watchlist",
            "body": "The score is heuristic only. It reflects policy relevance, timing, and implementation confidence; it is not financial advice.",
            "tags": "methodology, scoring",
            "created_at": NOW,
        },
    ]

    return {
        "version": 1,
        "updated_at": NOW,
        "people": people,
        "policies": policies,
        "stocks": stocks,
        "links": links,
        "notes": notes,
    }


if __name__ == "__main__":
    data = build_data()
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {DATA_FILE}")
    print(f"People: {len(data['people'])}, policies: {len(data['policies'])}, stocks: {len(data['stocks'])}, links: {len(data['links'])}")
