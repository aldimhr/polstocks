"""Historical data backfill — fetch past articles from web archives and feeds.

Three collection strategies:
1. Wayback Machine CDX API — archived snapshots of RSS feeds
2. Archive page scraping — search pages from Indonesian news sites
3. NewsAPI.org (optional, needs POLSTOCK_NEWSAPI_KEY env var)

Collected articles feed into the existing `import_historical_articles` staging
pipeline, then get replayed through the analysis/scoring pipeline as
`prediction_origin='historical_backfill'`.
"""

from __future__ import annotations

import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests

from backend.utils import strip_tags

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────

WAYBACK_CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_BASE = "https://web.archive.org/web"
REQUEST_TIMEOUT = 15
RATE_LIMIT_DELAY = 1.0  # seconds between Wayback requests

# RSS feeds to look up in Wayback Machine (same as live NEWS_SOURCES RSS subset)
WAYBACK_RSS_FEEDS = [
    {"name": "Antara Terkini", "url": "https://www.antaranews.com/rss/terkini.xml"},
    {"name": "Antara Top News", "url": "https://www.antaranews.com/rss/top-news.xml"},
    {"name": "Antara Ekonomi Bursa", "url": "https://www.antaranews.com/rss/ekonomi-bursa.xml"},
    {"name": "Antara Politik", "url": "https://www.antaranews.com/rss/politik"},
    {"name": "CNBC Indonesia", "url": "https://www.cnbcindonesia.com/rss"},
    {"name": "CNBC Market", "url": "https://www.cnbcindonesia.com/market/rss"},
    {"name": "CNN Indonesia Nasional", "url": "https://www.cnnindonesia.com/nasional/rss"},
    {"name": "CNN Indonesia Ekonomi", "url": "https://www.cnnindonesia.com/ekonomi/rss"},
    {"name": "Detik Finance", "url": "https://finance.detik.com/rss"},
    {"name": "Tempo", "url": "https://www.tempo.co/rss"},
]

# Archive page URLs with date-based pagination for scrape fallback
ARCHIVE_PAGES = [
    {
        "name": "Antara Ekonomi Bursa Archive",
        "base_url": "https://www.antaranews.com/ekonomi-bursa",
        "kind": "html_list",
    },
    {
        "name": "CNBC Indonesia Market Archive",
        "base_url": "https://www.cnbcindonesia.com/market",
        "kind": "html_list",
    },
]

# Political keywords for filtering (same set used in scoring)
_POLITICAL_KEYWORDS = [
    "ihsg", "saham", "bursa", "idx", "bei", "ojk", "kemenkeu",
    "menteri", "presiden", "dpr", "parlemen", "mpr", "komisi",
    "bumn", "kadin", "ekspor", "impor", "tarif", "bea cukai",
    "pajak", "subsidi", "apbn", "inflasi", "bi rate", "suku bunga",
    "rupiah", "defisit", "utang", "investasi", "fdi",
    "tambang", "batu bara", "sawit", "cpo", "nikel", "timah",
    "energi", "listrik", "bbm", "gas", "minyak",
    "pangan", "beras", "gula", "pupuk",
    "perdagangan", "perdagangan internasional", "perang dagang",
    "monopoli", "kartel", "kompetisi", "anti monopoli",
]


# ── Wayback Machine CDX API ──────────────────────────────────────

def _cdq_snapshots(
    url: str,
    from_date: str = "20260101",
    to_date: str = "20260609",
    limit: int = 20,
) -> list[dict[str, str]]:
    """Query CDX API for archived snapshots of a URL.

    Returns list of {"timestamp": "20260515120000", "original": url, "mimetype": "..."}.
    """
    params = {
        "url": url,
        "output": "json",
        "fl": "timestamp,original,mimetype,statuscode",
        "from": from_date,
        "to": to_date,
        "limit": str(limit),
        "filter": "statuscode:200",
        "collapse": "digest",  # unique content only
    }
    try:
        resp = requests.get(WAYBACK_CDX_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json()
        if not rows or len(rows) < 2:
            return []
        header = rows[0]
        return [dict(zip(header, row)) for row in rows[1:]]
    except Exception as exc:
        logger.warning("CDX query failed for %s: %s", url, exc)
        return []


def _fetch_wayback_snapshot(timestamp: str, original_url: str) -> str | None:
    """Fetch the content of a Wayback Machine snapshot."""
    wayback_url = f"{WAYBACK_BASE}/{timestamp}id_/{original_url}"
    try:
        resp = requests.get(wayback_url, timeout=REQUEST_TIMEOUT * 2)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.warning("Wayback fetch failed %s: %s", wayback_url[:80], exc)
        return None


def _parse_rss_xml(xml_text: str, source_name: str) -> list[dict[str, Any]]:
    """Parse RSS XML into article dicts compatible with import_historical_articles."""
    articles: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return articles

    # Handle RSS 2.0 and Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")
    if not items:
        items = root.findall(".//atom:entry", ns)

    for item in items:
        title = ""
        link = ""
        description = ""
        pub_date = ""

        # RSS 2.0
        title_el = item.find("title")
        if title_el is not None and title_el.text:
            title = strip_tags(title_el.text).strip()

        link_el = item.find("link")
        if link_el is not None:
            link = (link_el.text or link_el.get("href", "")).strip()

        desc_el = item.find("description")
        if desc_el is not None and desc_el.text:
            description = strip_tags(desc_el.text).strip()[:500]

        for date_tag in ["pubDate", "dc:date", "published", "updated", "date"]:
            date_el = item.find(date_tag)
            if date_el is None:
                date_el = item.find(f"{{{ns.get('atom', '')}}}{date_tag}")
            if date_el is not None and date_el.text:
                pub_date = date_el.text.strip()
                break

        # Convert RFC 2822 dates (e.g., "Thu, 28 May 2026 01:21:20 +0700") to ISO 8601
        if pub_date and re.match(r"\w{3},\s+\d{1,2}\s+\w{3}\s+\d{4}", pub_date):
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(pub_date)
                pub_date = dt.isoformat(timespec="seconds")
            except Exception:
                pass  # keep original, will fail import validation

        # Fallback: extract date from URL (many Indonesian news sites use /2026/01/15/ or /20260115/)
        if not pub_date and link:
            url_date = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", link)
            if url_date:
                pub_date = f"{url_date.group(1)}-{url_date.group(2)}-{url_date.group(3)}T00:00:00+07:00"
            else:
                url_date_compact = re.search(r"/(\d{8})\d{4,}", link)
                if url_date_compact:
                    d = url_date_compact.group(1)
                    pub_date = f"{d[:4]}-{d[4:6]}-{d[6:8]}T00:00:00+07:00"

        if title:
            articles.append({
                "source": source_name,
                "headline": title,
                "url": link,
                "summary": description,
                "published_at": pub_date,
                "provenance": {
                    "collection_method": "wayback_machine",
                    "source_name": source_name,
                },
            })

    return articles


def collect_from_wayback(
    *,
    from_date: str = "20260101",
    to_date: str = "20260609",
    max_snapshots_per_feed: int = 10,
    max_articles: int = 500,
) -> list[dict[str, Any]]:
    """Collect historical articles from Wayback Machine archived RSS feeds."""
    all_articles: list[dict[str, Any]] = []
    feeds_checked = 0
    feeds_with_data = 0

    for feed in WAYBACK_RSS_FEEDS:
        if len(all_articles) >= max_articles:
            break

        feeds_checked += 1
        snapshots = _cdq_snapshots(
            feed["url"],
            from_date=from_date,
            to_date=to_date,
            limit=max_snapshots_per_feed,
        )

        if not snapshots:
            continue

        feeds_with_data += 1
        for snap in snapshots[:max_snapshots_per_feed]:
            if len(all_articles) >= max_articles:
                break

            time.sleep(RATE_LIMIT_DELAY)
            content = _fetch_wayback_snapshot(snap["timestamp"], snap["original"])
            if not content:
                continue

            articles = _parse_rss_xml(content, feed["name"])
            # Attach wayback timestamp for provenance and use as date fallback
            wb_ts = snap["timestamp"]  # "20260101005921"
            wb_iso = f"{wb_ts[:4]}-{wb_ts[4:6]}-{wb_ts[6:8]}T{wb_ts[8:10]}:{wb_ts[10:12]}:{wb_ts[12:14]}+00:00"
            for art in articles:
                art.setdefault("provenance", {})["wayback_timestamp"] = wb_ts
                art["provenance"]["collection_method"] = "wayback_machine"
                # If no date was found in the RSS item or URL, use Wayback timestamp
                if not art.get("published_at"):
                    art["published_at"] = wb_iso
                    art["provenance"]["timestamp_reason"] = "wayback_snapshot_time"
            all_articles.extend(articles)

    logger.info(
        "Wayback collection: %d feeds checked, %d with data, %d articles",
        feeds_checked, feeds_with_data, len(all_articles),
    )
    return all_articles[:max_articles]


# ── Archive Page Scraping ────────────────────────────────────────

def _scrape_antara_archive(from_date: str, to_date: str) -> list[dict[str, Any]]:
    """Scrape Antara news archive pages for economic/stock articles."""
    articles: list[dict[str, Any]] = []
    sections = ["ekonomi-bursa", "ekonomi", "politik"]

    for section in sections:
        archive_url = f"https://www.antaranews.com/{section}"
        try:
            resp = requests.get(archive_url, timeout=REQUEST_TIMEOUT, headers={
                "User-Agent": "Mozilla/5.0 (compatible; PolStockBot/1.0)"
            })
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            logger.warning("Antara archive fetch failed (%s): %s", section, exc)
            continue

        # Extract article links with titles
        link_pattern = re.compile(
            r'<a[^>]+href="(https?://www\.antaranews\.com/[^"]*?\d{6,}[^"]*)"[^>]*>'
            r'([^<]+)</a>',
            re.IGNORECASE,
        )
        for match in link_pattern.finditer(html):
            url, title = match.group(1), strip_tags(match.group(2)).strip()
            if len(title) > 15 and any(kw in title.lower() for kw in _POLITICAL_KEYWORDS[:10]):
                articles.append({
                    "source": f"Antara {section.title()}",
                    "headline": title,
                    "url": url,
                    "summary": "",
                    "published_at": "",  # Will need date extraction
                    "provenance": {
                        "collection_method": "archive_scrape",
                        "section": section,
                    },
                })

    return articles


def _scrape_cnbc_archive() -> list[dict[str, Any]]:
    """Scrape CNBC Indonesia market archive."""
    articles: list[dict[str, Any]] = []
    sections = ["market", "news"]

    for section in sections:
        archive_url = f"https://www.cnbcindonesia.com/{section}"
        try:
            resp = requests.get(archive_url, timeout=REQUEST_TIMEOUT, headers={
                "User-Agent": "Mozilla/5.0 (compatible; PolStockBot/1.0)"
            })
            resp.raise_for_status()
            html = resp.text
        except Exception as exc:
            logger.warning("CNBC archive fetch failed (%s): %s", section, exc)
            continue

        link_pattern = re.compile(
            r'<a[^>]+href="(https?://www\.cnbcindonesia\.com/[^"]*?/\d{8,}[^"]*)"[^>]*>',
            re.IGNORECASE,
        )

        urls_found = link_pattern.findall(html)
        for url in urls_found[:30]:
            articles.append({
                "source": f"CNBC Indonesia {section.title()}",
                "headline": "",  # Will extract from page
                "url": url,
                "summary": "",
                "published_at": "",
                "provenance": {
                    "collection_method": "archive_scrape",
                    "section": section,
                },
            })

    return articles


def collect_from_archives() -> list[dict[str, Any]]:
    """Collect articles from news site archive pages."""
    articles: list[dict[str, Any]] = []
    articles.extend(_scrape_antara_archive("20260101", "20260609"))
    articles.extend(_scrape_cnbc_archive())
    logger.info("Archive scrape: %d articles collected", len(articles))
    return articles


# ── NewsAPI.org (optional) ───────────────────────────────────────

def collect_from_newsapi(
    *,
    query: str = "IHSG OR saham OR bursa OR ekonomi Indonesia",
    from_date: str = "2026-01-01",
    to_date: str = "2026-06-09",
    page_size: int = 100,
    max_pages: int = 3,
) -> list[dict[str, Any]]:
    """Collect articles from NewsAPI.org (requires POLSTOCK_NEWSAPI_KEY env var)."""
    api_key = os.environ.get("POLSTOCK_NEWSAPI_KEY", "")
    if not api_key:
        logger.info("NewsAPI skipped: POLSTOCK_NEWSAPI_KEY not set")
        return []

    articles: list[dict[str, Any]] = []
    endpoint = "https://newsapi.org/v2/everything"

    for page in range(1, max_pages + 1):
        params = {
            "q": query,
            "from": from_date,
            "to": to_date,
            "language": "id",
            "sortBy": "publishedAt",
            "pageSize": str(page_size),
            "page": str(page),
        }
        headers = {"X-Api-Key": api_key}
        try:
            resp = requests.get(endpoint, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("NewsAPI request failed (page %d): %s", page, exc)
            break

        items = data.get("articles", [])
        if not items:
            break

        for item in items:
            articles.append({
                "source": item.get("source", {}).get("name", "NewsAPI"),
                "headline": item.get("title", ""),
                "url": item.get("url", ""),
                "summary": item.get("description", "") or "",
                "published_at": item.get("publishedAt", ""),
                "provenance": {
                    "collection_method": "newsapi",
                    "source_id": item.get("source", {}).get("id", ""),
                },
            })

        if len(items) < page_size:
            break
        time.sleep(0.5)

    logger.info("NewsAPI collection: %d articles", len(articles))
    return articles


# ── Top-Level Orchestrator ────────────────────────────────────────

def collect_historical_articles(
    *,
    sources: list[str] | None = None,
    from_date: str = "2026-01-01",
    to_date: str = "2026-06-09",
    max_articles: int = 500,
) -> dict[str, Any]:
    """Collect historical articles from all enabled sources.

    Args:
        sources: List of source names to use. None = all available.
            Options: "wayback", "archives", "newsapi"
        from_date: Start date (YYYYMMDD for wayback, YYYY-MM-DD for newsapi)
        to_date: End date
        max_articles: Maximum total articles to collect

    Returns:
        {"articles": [...], "stats": {...}}
    """
    enabled = set(sources) if sources else {"wayback", "archives", "newsapi"}
    all_articles: list[dict[str, Any]] = []
    stats: dict[str, Any] = {"sources_tried": [], "articles_per_source": {}}

    # Normalize dates for wayback (YYYYMMDD) vs newsapi (YYYY-MM-DD)
    wb_from = from_date.replace("-", "")
    wb_to = to_date.replace("-", "")
    api_from = f"{from_date[:4]}-{from_date[4:6]}-{from_date[6:8]}" if len(from_date) == 8 else from_date
    api_to = f"{to_date[:4]}-{to_date[4:6]}-{to_date[6:8]}" if len(to_date) == 8 else to_date

    if "wayback" in enabled:
        stats["sources_tried"].append("wayback")
        wb_articles = collect_from_wayback(
            from_date=wb_from, to_date=wb_to,
            max_articles=max_articles - len(all_articles),
        )
        stats["articles_per_source"]["wayback"] = len(wb_articles)
        all_articles.extend(wb_articles)

    if "archives" in enabled and len(all_articles) < max_articles:
        stats["sources_tried"].append("archives")
        arch_articles = collect_from_archives()
        stats["articles_per_source"]["archives"] = len(arch_articles)
        all_articles.extend(arch_articles)

    if "newsapi" in enabled and len(all_articles) < max_articles:
        stats["sources_tried"].append("newsapi")
        na_articles = collect_from_newsapi(
            from_date=api_from, to_date=api_to,
            max_pages=3,
        )
        stats["articles_per_source"]["newsapi"] = len(na_articles)
        all_articles.extend(na_articles)

    # Deduplicate by URL
    seen_urls: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for art in all_articles:
        url = (art.get("url") or "").strip()
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        deduped.append(art)

    stats["total_collected"] = len(all_articles)
    stats["total_deduped"] = len(deduped)

    return {"articles": deduped[:max_articles], "stats": stats}


def filter_political_articles(
    articles: list[dict[str, Any]],
    *,
    min_keyword_hits: int = 1,
) -> list[dict[str, Any]]:
    """Pre-filter articles for political/economic relevance before import."""
    filtered: list[dict[str, Any]] = []
    for art in articles:
        text = f"{art.get('headline', '')} {art.get('summary', '')}".lower()
        hits = sum(1 for kw in _POLITICAL_KEYWORDS if kw in text)
        if hits >= min_keyword_hits:
            art["_political_keyword_hits"] = hits
            filtered.append(art)
    return filtered
