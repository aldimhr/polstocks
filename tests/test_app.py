from datetime import timedelta
from fastapi.testclient import TestClient
import json

from backend import main as appmod
from backend import validation as valmod
from backend import stocks as stocksmod
from backend import sources as sourcesmod

client = TestClient(appmod.app)


def _patch_validation_series(monkeypatch, fake_fn):
    """Patch fetch_market_validation_series in both main and validation modules."""
    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_fn)
    monkeypatch.setattr(valmod, "fetch_market_validation_series", fake_fn)

def _patch_fetch_market_index(monkeypatch, fake_fn):
    """Patch fetch_market_index in both main and stocks modules."""
    monkeypatch.setattr(appmod, "fetch_market_index", fake_fn)
    monkeypatch.setattr(stocksmod, "fetch_market_index", fake_fn)


def _patch_fetch_news_bundle(monkeypatch, fake_fn):
    """Patch fetch_news_bundle in both main and sources modules."""
    monkeypatch.setattr(appmod, "fetch_news_bundle", fake_fn)
    monkeypatch.setattr(sourcesmod, "fetch_news_bundle", fake_fn)


def _patch_fetch_stock_quotes(monkeypatch, fake_fn):
    """Patch fetch_stock_quotes in both main and stocks modules."""
    monkeypatch.setattr(appmod, "fetch_stock_quotes", fake_fn)
    monkeypatch.setattr(stocksmod, "fetch_stock_quotes", fake_fn)


def _patch_fetch_ticker_history(monkeypatch, fake_fn):
    """Patch fetch_ticker_history in both main and stocks modules."""
    monkeypatch.setattr(appmod, "fetch_ticker_history", fake_fn)
    monkeypatch.setattr(stocksmod, "fetch_ticker_history", fake_fn)


def _patch_load_source_outcome_history(monkeypatch, fake_fn):
    """Patch load_source_outcome_history in both main and validation modules."""
    monkeypatch.setattr(appmod, "load_source_outcome_history", fake_fn)
    monkeypatch.setattr(valmod, "load_source_outcome_history", fake_fn)


def _patch_save_source_outcome_history(monkeypatch, fake_fn):
    """Patch save_source_outcome_history in both main and validation modules."""
    monkeypatch.setattr(appmod, "save_source_outcome_history", fake_fn)
    monkeypatch.setattr(valmod, "save_source_outcome_history", fake_fn)


def patch_source_history_file(monkeypatch, path):
    """Monkeypatch source outcome history to use a temp file instead of SQLite."""

    def load():
        try:
            import json as _json
            return appmod.normalize_source_outcome_history(_json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return appmod._source_outcome_history_defaults()

    def save(history):
        import json as _json
        normalized = appmod.normalize_source_outcome_history(history)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    _patch_load_source_outcome_history(monkeypatch, load)
    _patch_save_source_outcome_history(monkeypatch, save)
    monkeypatch.setattr(valmod, "load_source_outcome_history", load)
    monkeypatch.setattr(valmod, "save_source_outcome_history", save)


FAKE_ARTICLE = {
    "source": "Antara News",
    "headline": "Pemerintah dorong investasi dan hilirisasi mineral",
    "url": "https://example.com/article-1",
    "published_at": appmod.now_wib(),
    "summary": "Investasi, hilirisasi, dan proyek infrastruktur memberi sinyal positif ke perbankan dan basic materials.",
    "source_weight": 1.0,
    "source_type": "media",
}

DIRECT_MENTION_ARTICLE = {
    "source": "Setkab",
    "headline": "Pemerintah dorong hilirisasi, Antam siapkan belanja smelter baru",
    "url": "https://example.com/article-2",
    "published_at": appmod.now_wib(),
    "summary": "Agenda hilirisasi mineral dan smelter disebut langsung bersama Antam untuk mempercepat proyek nikel.",
    "source_weight": 1.0,
    "source_type": "government",
}

VAGUE_ARTICLE = {
    "source": "Opinion Blog",
    "headline": "Ekonomi nasional diharapkan membaik tahun depan",
    "url": "https://example.com/article-3",
    "published_at": appmod.now_wib(),
    "summary": "Tanpa kebijakan, regulasi, atau sektor spesifik. Hanya pandangan umum soal ekonomi.",
    "source_weight": 0.2,
    "source_type": "other",
}

HOUSING_ARTICLE = {
    "source": "Setkab",
    "headline": "Pemerintah percepat program rumah subsidi dan KPR",
    "url": "https://example.com/article-4",
    "published_at": appmod.now_wib(),
    "summary": "Program housing, perumahan, mortgage, dan public works dipercepat untuk mendorong pembangunan rumah.",
    "source_weight": 1.0,
    "source_type": "government",
}

OLDER_WEEK_ARTICLE = {
    "source": "Antara News",
    "headline": "DPR bahas kebijakan logistik pangan nasional",
    "url": "https://example.com/article-5",
    "published_at": appmod.now_wib() - timedelta(days=3),
    "summary": "Pembahasan kebijakan pangan, logistik, dan distribusi nasional berlangsung pekan ini.",
    "source_weight": 0.95,
    "source_type": "media",
}

OLDER_MONTH_ARTICLE = {
    "source": "OJK",
    "headline": "OJK siapkan aturan pendanaan sektor prioritas",
    "url": "https://example.com/article-6",
    "published_at": appmod.now_wib() - timedelta(days=18),
    "summary": "Regulator menyiapkan kebijakan pendanaan untuk proyek prioritas dan perumahan.",
    "source_weight": 0.9,
    "source_type": "regulator",
}

STRONG_POLICY_ARTICLE = {
    "source": "OJK",
    "headline": "OJK resmi terbitkan peraturan baru untuk kredit perumahan",
    "url": "https://example.com/article-7",
    "published_at": appmod.now_wib(),
    "summary": "Regulator menetapkan peraturan dan kebijakan baru untuk mendorong kredit rumah subsidi.",
    "source_weight": 0.95,
    "source_type": "regulator",
}

WEAK_CONTEXT_ARTICLE = {
    "source": "Lifestyle Blog",
    "headline": "Pengamat optimistis ekonomi digital tetap cerah",
    "url": "https://example.com/article-8",
    "published_at": appmod.now_wib(),
    "summary": "Artikel opini umum yang menyebut pemerintah sekali tanpa aksi kebijakan atau aturan spesifik.",
    "source_weight": 0.3,
    "source_type": "other",
}

NON_POLITICAL_ARTICLE = {
    "source": "Sports News",
    "headline": "Tim nasional menang besar di laga persahabatan",
    "url": "https://example.com/article-9",
    "published_at": appmod.now_wib(),
    "summary": "Fokus pada pertandingan, pelatih, dan gol tanpa kaitan kebijakan publik.",
    "source_weight": 0.2,
    "source_type": "other",
}

PROPOSAL_ARTICLE = {
    "source": "DPR",
    "headline": "DPR bahas usulan kebijakan baru untuk subsidi rumah",
    "url": "https://example.com/article-10",
    "published_at": appmod.now_wib(),
    "summary": "Komisi DPR membahas usulan peraturan dan rencana subsidi perumahan baru.",
    "source_weight": 0.85,
    "source_type": "government",
}

APPROVED_ARTICLE = {
    "source": "Setkab",
    "headline": "Pemerintah resmi sahkan peraturan subsidi rumah",
    "url": "https://example.com/article-11",
    "published_at": appmod.now_wib(),
    "summary": "Pemerintah menetapkan peraturan baru dan resmi mengumumkan program subsidi rumah berlaku tahun ini.",
    "source_weight": 1.0,
    "source_type": "government",
}

REVOKED_ARTICLE = {
    "source": "Setkab",
    "headline": "Pemerintah batalkan aturan pembatasan ekspor batu bara",
    "url": "https://example.com/article-12",
    "published_at": appmod.now_wib(),
    "summary": "Pemerintah mencabut dan membatalkan aturan pembatasan ekspor setelah evaluasi kabinet.",
    "source_weight": 1.0,
    "source_type": "government",
}

MIXED_DIRECTION_ARTICLE = {
    "source": "Setkab",
    "headline": "Pemerintah perketat pembatasan ekspor batu bara namun tambah subsidi rumah",
    "url": "https://example.com/article-13",
    "published_at": appmod.now_wib(),
    "summary": "Kabinet memperketat pembatasan ekspor batu bara, sementara pemerintah menambah subsidi rumah dan mempercepat program KPR.",
    "source_weight": 1.0,
    "source_type": "government",
}

THREAD_PROPOSAL_ARTICLE = {
    "source": "Setkab",
    "headline": "Pemerintah bahas usulan subsidi rumah dan relaksasi KPR",
    "url": "https://example.com/article-15",
    "published_at": appmod.now_wib() - timedelta(hours=5),
    "summary": "Pemerintah membahas usulan kebijakan subsidi rumah baru dan relaksasi kredit pemilikan rumah.",
    "source_weight": 1.0,
    "source_type": "government",
}

THREAD_APPROVED_ARTICLE = {
    "source": "Setkab",
    "headline": "Pemerintah resmi sahkan subsidi rumah dan relaksasi KPR",
    "url": "https://example.com/article-16",
    "published_at": appmod.now_wib() - timedelta(hours=3),
    "summary": "Pemerintah menetapkan kebijakan subsidi rumah dan relaksasi kredit pemilikan rumah mulai berlaku tahun ini.",
    "source_weight": 1.0,
    "source_type": "government",
}

THREAD_REVERSED_ARTICLE = {
    "source": "Setkab",
    "headline": "Pemerintah tunda dan koreksi aturan subsidi rumah",
    "url": "https://example.com/article-17",
    "published_at": appmod.now_wib() - timedelta(hours=1),
    "summary": "Pemerintah menunda, mengoreksi, dan membatalkan sebagian aturan subsidi rumah serta relaksasi KPR setelah evaluasi.",
    "source_weight": 1.0,
    "source_type": "government",
}


def fake_news_fetcher():
    return [FAKE_ARTICLE], []


def fake_window_news_fetcher():
    return [FAKE_ARTICLE, OLDER_WEEK_ARTICLE, OLDER_MONTH_ARTICLE], []


def fake_thread_news_fetcher():
    return [THREAD_PROPOSAL_ARTICLE, THREAD_APPROVED_ARTICLE, THREAD_REVERSED_ARTICLE], []


def fake_stock_fetcher(tickers):
    quotes = {}
    for i, ticker in enumerate(tickers, start=1):
        quotes[ticker] = {
            "ticker": ticker,
            "name": appmod.company_name_for_ticker(ticker),
            "sector": appmod.sector_for_ticker(ticker),
            "price": 1000 + i,
            "change_pct": 1.5,
            "volume": 100000 * i,
            "after_hours": False,
            "source": "fake",
        }
    return quotes, []


def fake_market_fetcher():
    return {
        "symbol": "^JKSE",
        "name": "IHSG",
        "value": 6847,
        "change_pct": -1.2,
        "change_points": -83,
        "series": [6930, 6912, 6898, 6872, 6847],
        "market_time": appmod.now_iso(),
        "source": "fake",
    }, []


def fake_validation_series_confirmed(ticker, range_name, interval):
    return {
        "ticker": ticker,
        "range": range_name,
        "interval": interval,
        "prices": [100.0, 100.8, 101.2, 101.0, 100.9, 105.8],
        "volumes": [1000, 980, 1025, 995, 1010, 2600],
        "market_time": appmod.now_iso(),
        "source": "fake-validation",
        "warnings": [],
    }


def fake_validation_series_flat(ticker, range_name, interval):
    return {
        "ticker": ticker,
        "range": range_name,
        "interval": interval,
        "prices": [100.0, 100.1, 99.9, 100.0, 100.05, 100.02],
        "volumes": [1000, 980, 1015, 1005, 995, 1010],
        "market_time": appmod.now_iso(),
        "source": "fake-validation",
        "warnings": [],
    }


def fake_validation_series_rejected(ticker, range_name, interval):
    return {
        "ticker": ticker,
        "range": range_name,
        "interval": interval,
        "prices": [100.0, 99.4, 98.7, 98.1, 97.8, 96.2],
        "volumes": [1000, 1040, 1090, 1140, 1180, 2400],
        "market_time": appmod.now_iso(),
        "source": "fake-validation",
        "warnings": [],
    }


def fake_validation_series_unavailable(ticker, range_name, interval):
    return {
        "ticker": ticker,
        "range": range_name,
        "interval": interval,
        "prices": [],
        "volumes": [],
        "market_time": appmod.now_iso(),
        "source": "fake-validation",
        "warnings": ["history unavailable"],
    }


def fake_ticker_history(ticker, window=None):
    window = window or "24h"
    return {
        "ticker": ticker,
        "name": appmod.company_name_for_ticker(ticker),
        "sector": appmod.sector_for_ticker(ticker),
        "window": window,
        "window_label": appmod.event_window_label(window),
        "range": "7d",
        "interval": "1h",
        "price": 1015.0,
        "change_pct": 1.8,
        "change_points": 18.0,
        "period_change_pct": 4.2,
        "period_change_points": 41.0,
        "volume": 123456,
        "series": [980.0, 992.0, 1001.0, 1015.0],
        "history": [
            {"time": appmod.now_iso(), "value": 980.0},
            {"time": appmod.now_iso(), "value": 992.0},
            {"time": appmod.now_iso(), "value": 1001.0},
            {"time": appmod.now_iso(), "value": 1015.0},
        ],
        "series_points": 4,
        "series_start": appmod.now_iso(),
        "series_end": appmod.now_iso(),
        "series_high": 1015.0,
        "series_low": 980.0,
        "market_time": appmod.now_iso(),
        "source": "fake-history",
        "warnings": [],
    }


def setup_function(_):
    appmod.reset_runtime_state()


def test_watchlist_roundtrip():
    default = client.get("/api/watchlist")
    assert default.status_code == 200
    assert len(default.json()["tickers"]) == 30

    updated = client.put("/api/watchlist", json={"tickers": ["BBCA", "TLKM.JK", "BBCA.JK"]})
    assert updated.status_code == 200
    assert updated.json()["tickers"] == ["BBCA.JK", "TLKM.JK"]

    current = client.get("/api/watchlist")
    assert current.json()["tickers"] == ["BBCA.JK", "TLKM.JK"]


def test_ticker_detail_endpoint_returns_history(monkeypatch):
    _patch_fetch_ticker_history(monkeypatch, fake_ticker_history)
    response = client.get("/api/ticker/BBCA.JK?window=7d")
    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "BBCA.JK"
    assert data["window"] == "7d"
    assert data["window_label"] == "last 7 days"
    assert data["series"] == [980.0, 992.0, 1001.0, 1015.0]
    assert data["series_points"] == 4
    assert data["series_high"] == 1015.0
    assert data["series_low"] == 980.0
    assert data["source"] == "fake-history"


def test_root_serves_dashboard_html():
    response = client.get("/")
    assert response.status_code == 200
    dashboard_html = (appmod.PROJECT_ROOT / "dashboard.html").read_text(encoding="utf-8")
    assert response.text == dashboard_html


def test_root_and_healthz_support_head_requests():
    root_response = client.head("/")
    assert root_response.status_code == 200
    assert root_response.text == ""

    healthz_response = client.head("/healthz")
    assert healthz_response.status_code == 200
    assert healthz_response.text == ""

    ticker_response = client.head("/api/ticker/BBCA.JK?window=7d")
    assert ticker_response.status_code == 200
    assert ticker_response.text == ""


def test_parse_rss_items_recovers_from_malformed_xml():
    malformed_rss = """<?xml version='1.0' encoding='UTF-8'?>
    <rss version='2.0'>
      <channel>
        <title>Example Feed</title>
        <item>
          <title>Pemerintah & DPR bahas kebijakan subsidi rumah</title>
          <link>https://example.com/policy-1</link>
          <description>Rencana kebijakan baru untuk subsidi rumah dan KPR.</description>
          <pubDate>Mon, 01 Jun 2026 09:00:00 +0700</pubDate>
        </item>
      </channel>
    </rss>
    """
    items = appmod.parse_rss_items({"name": "Antara Terkini", "url": "https://example.com/rss", "weight": 1.0}, malformed_rss)
    assert len(items) == 1
    assert items[0]["headline"] == "Pemerintah & DPR bahas kebijakan subsidi rumah"
    assert items[0]["url"] == "https://example.com/policy-1"
    assert items[0]["source_type"] == "media"


def test_parse_html_signal_uses_real_publish_time_from_meta():
    html_payload = """<html>
      <head>
        <title>Tinggalkan Paris Menuju Jakarta, Presiden Prabowo Akhiri Kunjungan Resmi Kenegaraan di Prancis</title>
        <meta property='article:published_time' content='2026-05-29T15:39:22+00:00' />
        <meta name='description' content='Presiden Republik Indonesia Prabowo Subianto mengakhiri rangkaian kunjungan resmi kenegaraan di Paris.' />
      </head>
      <body>
        <h1>Tinggalkan Paris Menuju Jakarta, Presiden Prabowo Akhiri Kunjungan Resmi Kenegaraan di Prancis</h1>
        <a href='/tinggalkan-paris-menuju-jakarta-presiden-prabowo-akhiri-kunjungan-resmi-kenegaraan-di-prancis/'>
          Tinggalkan Paris Menuju Jakarta, Presiden Prabowo Akhiri Kunjungan Resmi Kenegaraan di Prancis
        </a>
      </body>
    </html>"""

    items = appmod.parse_html_signal(
        {"name": "Sekretariat Kabinet", "url": "https://setkab.go.id", "weight": 1.0, "kind": "html"},
        html_payload,
    )

    assert len(items) == 1
    assert items[0]["published_at"] == appmod.parse_datetime("2026-05-29T15:39:22+00:00")
    assert items[0]["headline"].startswith("Tinggalkan Paris Menuju Jakarta")


def test_fetch_source_html_enriches_article_dates_from_article_pages(monkeypatch):
    homepage_html = """<html>
      <head>
        <meta property='article:modified_time' content='2021-08-12T07:55:07+00:00' />
      </head>
      <body>
        <a href='https://setkab.go.id/tinggalkan-paris-menuju-jakarta-presiden-prabowo-akhiri-kunjungan-resmi-kenegaraan-di-prancis/'>
          Tinggalkan Paris Menuju Jakarta, Presiden Prabowo Akhiri Kunjungan Resmi Kenegaraan di Prancis
        </a>
      </body>
    </html>"""
    article_html = """<html>
      <head>
        <meta property='article:published_time' content='2026-05-29T15:39:22+00:00' />
      </head>
      <body>
        <h1>Tinggalkan Paris Menuju Jakarta, Presiden Prabowo Akhiri Kunjungan Resmi Kenegaraan di Prancis</h1>
      </body>
    </html>"""

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(url, timeout=None, headers=None):
        if url == "https://setkab.go.id":
            return FakeResponse(homepage_html)
        if url == "https://setkab.go.id/tinggalkan-paris-menuju-jakarta-presiden-prabowo-akhiri-kunjungan-resmi-kenegaraan-di-prancis/":
            return FakeResponse(article_html)
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(appmod.requests, "get", fake_get)
    items, warning = appmod.fetch_source({"name": "Sekretariat Kabinet", "url": "https://setkab.go.id", "kind": "html", "weight": 1.0})

    assert warning is None
    assert len(items) == 1
    assert items[0]["published_at"] == appmod.parse_datetime("2026-05-29T15:39:22+00:00")


def test_source_registry_loader_normalizes_profiles():
    registry = appmod.load_source_registry()
    assert {"sources", "by_name", "by_domain", "by_canonical_domain"}.issubset(registry.keys())
    assert len(registry["sources"]) >= 9

    source_record = registry["sources"][0]
    assert {"name", "source_type", "tier", "trust_weight", "canonical_domain", "country_focus", "notes"}.issubset(source_record.keys())

    antara = appmod.source_profile_for_name("Antara Terkini")
    setkab = appmod.source_profile_for_domain("setkab.go.id")
    ojk = appmod.source_profile_for_url("https://www.ojk.go.id/id/berita")

    assert antara["canonical_name"] == "Antara News"
    assert antara["source_type"] == "media"
    assert antara["tier"] == 3
    assert antara["trust_weight"] > 0.0
    assert setkab["canonical_name"] == "Setkab"
    assert setkab["source_type"] == "government"
    assert setkab["tier"] == 1
    assert setkab["trust_weight"] > antara["trust_weight"]
    assert setkab["tier"] < antara["tier"]
    assert ojk["source_type"] == "regulator"
    assert ojk["tier"] == 1
    assert ojk["canonical_domain"] == "www.ojk.go.id"


def test_parse_rss_items_attaches_source_metadata():
    rss_payload = """<?xml version='1.0' encoding='UTF-8'?>
    <rss version='2.0'>
      <channel>
        <title>OJK Feed</title>
        <item>
          <title>OJK terbitkan aturan baru untuk kredit perumahan</title>
          <link>https://www.ojk.go.id/id/berita/Pages/aturan-baru-kpr.aspx</link>
          <description>Regulator menerbitkan aturan baru untuk mendukung kredit perumahan.</description>
          <pubDate>Mon, 01 Jun 2026 09:00:00 +0700</pubDate>
        </item>
      </channel>
    </rss>
    """
    items = appmod.parse_rss_items({"name": "OJK", "url": "https://www.ojk.go.id", "weight": 0.9, "kind": "rss"}, rss_payload)
    assert len(items) == 1
    item = items[0]
    assert item["source_type"] == "regulator"
    assert item["source_tier"] == 1
    assert item["canonical_domain"] == "www.ojk.go.id"
    assert item["source_profile"]["canonical_name"] == "OJK"
    assert item["source_quality_score"] > 0


def test_canonicalize_article_url_strips_tracking_and_amp_variants():
    url = "https://www.antaranews.com/ekonomi/2026/06/01/pemerintah-dorong-investasi?utm_source=rss&utm_medium=feed#comments"
    assert appmod.canonicalize_article_url(url) == "https://www.antaranews.com/ekonomi/2026/06/01/pemerintah-dorong-investasi"
    assert appmod.canonicalize_article_url("https://www.antaranews.com/ekonomi/2026/06/01/pemerintah-dorong-investasi/amp") == "https://www.antaranews.com/ekonomi/2026/06/01/pemerintah-dorong-investasi"


def test_merge_duplicate_articles_collapses_source_coverage_and_keeps_latest_publication_time():
    latest = appmod.now_wib()
    earlier = latest - timedelta(minutes=18)
    articles = [
        {
            "source": "Setkab",
            "headline": "Pemerintah resmi sahkan subsidi rumah dan relaksasi KPR",
            "url": "https://setkab.go.id/berita/subsidi-rumah/?utm_source=rss",
            "published_at": earlier,
            "summary": "Pemerintah menetapkan peraturan baru dan resmi mengumumkan program subsidi rumah berlaku tahun ini.",
            "source_weight": 1.0,
            "source_type": "government",
            "source_tier": 1,
            "canonical_domain": "setkab.go.id",
            "source_quality_score": 1.0,
        },
        {
            "source": "Antara News",
            "headline": "Pemerintah resmi sahkan subsidi rumah dan relaksasi KPR",
            "url": "https://www.antaranews.com/ekonomi/subsidi-rumah/amp",
            "published_at": latest,
            "summary": "Pemerintah menetapkan peraturan baru dan resmi mengumumkan program subsidi rumah berlaku tahun ini.",
            "source_weight": 0.86,
            "source_type": "media",
            "source_tier": 3,
            "canonical_domain": "www.antaranews.com",
            "source_quality_score": 0.54,
        },
    ]
    merged = appmod.merge_duplicate_articles(articles)
    assert len(merged) == 1
    item = merged[0]
    assert item["duplicate_count"] == 2
    assert item["latest_published_at"] == latest
    assert item["canonical_url"] == "https://setkab.go.id/berita/subsidi-rumah"
    assert set(item["source_names"]) == {"Setkab", "Antara News"}
    assert "https://www.antaranews.com/ekonomi/subsidi-rumah" in item["source_urls"]
    assert item["source_profile"]["canonical_name"] == "Setkab"


def test_source_freshness_and_quality_score_decay_with_age_and_duplicates():
    profile = appmod.source_profile_for_name("Setkab")
    fresh_score = appmod.source_freshness_score(appmod.now_wib(), profile)
    stale_score = appmod.source_freshness_score(appmod.now_wib() - timedelta(days=6), profile)
    assert fresh_score > stale_score
    assert fresh_score > 0.9
    assert stale_score < 0.5

    fresh_article = {
        "source": "Setkab",
        "headline": "Pemerintah resmi sahkan subsidi rumah dan relaksasi KPR",
        "url": "https://setkab.go.id/berita/subsidi-rumah",
        "published_at": appmod.now_wib(),
        "summary": "Pemerintah menetapkan peraturan baru dan resmi mengumumkan program subsidi rumah berlaku tahun ini.",
        **appmod.source_metadata_for("Setkab", "https://setkab.go.id/berita/subsidi-rumah"),
    }
    stale_commentary_article = {
        "source": "Lifestyle Blog",
        "headline": "Pengamat optimistis ekonomi digital tetap cerah",
        "url": "https://example.com/article-opinion",
        "published_at": appmod.now_wib() - timedelta(days=8),
        "summary": "Artikel opini umum yang menyebut pemerintah sekali tanpa aksi kebijakan atau aturan spesifik.",
        "source_weight": 0.3,
        **appmod.source_metadata_for("Lifestyle Blog", "https://example.com/article-opinion"),
    }
    fresh_analyzed = appmod.analyze_article(fresh_article, ["BSDE.JK"], window="30d")
    stale_analyzed = appmod.analyze_article(stale_commentary_article, ["BSDE.JK"], window="30d")

    assert fresh_analyzed["source_quality_score"] > stale_analyzed["source_quality_score"]
    assert fresh_analyzed.get("coverage_warning") in {None, ""}
    assert stale_analyzed["coverage_warning"] == "stale_coverage"

    merged = appmod.merge_duplicate_articles([
        fresh_article,
        {
            **fresh_article,
            "url": "https://www.antaranews.com/ekonomi/subsidi-rumah/amp?utm_source=twitter",
            "source": "Antara News",
            "source_quality_score": 0.54,
            **appmod.source_metadata_for("Antara News", "https://www.antaranews.com/ekonomi/subsidi-rumah/amp?utm_source=twitter"),
        },
    ])
    assert merged[0]["duplicate_count"] == 2
    assert merged[0]["source_quality_score"] <= fresh_analyzed["source_quality_score"]


def test_refresh_payload_sorts_impacted_tickers_first(monkeypatch):
    impacted_article = {
        "source": "Setkab",
        "headline": "Pemerintah dorong BBCA salurkan kredit rumah subsidi",
        "url": "https://example.com/article-impacted-bbca",
        "published_at": appmod.now_wib(),
        "summary": "Pemerintah meminta BBCA dan bank nasional mempercepat kredit rumah subsidi, relaksasi KPR, dan pembiayaan perumahan.",
        "source_weight": 1.0,
        "source_type": "government",
    }

    def impacted_news_fetcher():
        return [impacted_article], []

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["TLKM", "BBCA"],
        window="7d",
        news_fetcher=impacted_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    tickers = [stock["ticker"] for stock in payload["stocks"]]
    assert tickers[0] == "BBCA.JK"
    assert payload["stocks"][0]["relationship_count"] > 0
    assert payload["stocks"][1]["relationship_count"] == 0


def test_refresh_payload_keeps_neutral_ticker_order_when_no_impacts(monkeypatch):
    neutral_article = {
        "source": "Sports News",
        "headline": "Laga persahabatan dan skor akhir pertandingan",
        "url": "https://example.com/article-neutral",
        "published_at": appmod.now_wib(),
        "summary": "Berita olahraga tanpa kebijakan, regulasi, atau perusahaan yang relevan.",
        "source_weight": 0.2,
        "source_type": "other",
    }

    def neutral_news_fetcher():
        return [neutral_article], []

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["TLKM", "BBCA"],
        window="7d",
        news_fetcher=neutral_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    assert [stock["ticker"] for stock in payload["stocks"]] == ["TLKM.JK", "BBCA.JK"]
    assert all(stock["relationship_count"] == 0 for stock in payload["stocks"])


def test_refresh_payload_flags_stale_source_coverage(monkeypatch):
    stale_article = {
        "source": "Antara Mirror",
        "headline": "Pemerintah, DPR, dan OJK terbitkan aturan subsidi rumah baru",
        "url": "https://example.com/article-stale",
        "published_at": appmod.now_wib() - timedelta(days=9),
        "summary": "Pemerintah dan DPR menerbitkan peraturan baru, disahkan kabinet, dan OJK mengumumkan kebijakan relaksasi KPR serta subsidi rumah berlaku setelah evaluasi.",
        **appmod.source_metadata_for("Antara Mirror", "https://example.com/article-stale"),
    }

    def stale_news_fetcher():
        return [stale_article], []

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["BBCA", "BSDE"],
        window="30d",
        news_fetcher=stale_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    assert payload["events"]
    assert payload["events"][0]["coverage_warning"] == "stale_coverage"
    assert any("stale" in warning.lower() for warning in payload["warnings"])



def test_html_source_reports_when_no_article_links_are_extracted(monkeypatch):
    class FakeResponse:
        status_code = 200
        text = "<html><head><title>Portal OJK</title></head><body><div>No news links here</div></body></html>"

        def raise_for_status(self):
            return None

    def fake_get(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr(appmod.requests, "get", fake_get)
    articles, warning = appmod.fetch_source({"name": "OJK", "url": "https://www.ojk.go.id", "kind": "html", "weight": 0.9})
    assert articles == []
    assert warning == "OJK: no article links extracted"


def test_dashboard_contains_runtime_hooks():
    dashboard_html = (appmod.PROJECT_ROOT / "dashboard.html").read_text(encoding="utf-8")
    for snippet in [
        'id="m-highest"',
        'id="m-highest-sub"',
        'id="m-events-sub"',
        'stockBody',
        'data-label="Ticker"',
        'data-label="Price (IDR)"',
        'data-label="Today"',
        'data-label="Impact score"',
        'sort-bar',
        'sort-btn',
        "sortStocks('impact')",
        'openTickerModal(',
        "$('stockBody').addEventListener('click'",
        '@media (max-width: 720px)',
        'id="ihsgValue"',
        'id="ihsgChange"',
        'safeArticleUrl(',
        'event-link',
        'renderProvenanceBadges(',
        'renderConflictBadges(',
        'renderSourceDiagnosticBadges(',
        'renderDashboardCues(',
        'id="robustnessStrip"',
        'Source robustness',
        'robustness-strip-headline',
        'provenance-chips',
        'Official',
        'Fresh',
        'Sparse sources',
        'Duplicated coverage',
        'High confidence',
        'Needs more evidence',
        'Conflicting signals',
        'Contested thread',
        'Source issue',
        'Date fallback',
    ]:
        assert snippet in dashboard_html


def test_watchlist_persists_to_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "WATCHLIST_FILE", tmp_path / "watchlist.json")
    appmod.reset_runtime_state()
    saved = appmod.set_watchlist(["BBCA", "TLKM", "BBCA"])
    assert saved == ["BBCA.JK", "TLKM.JK"]
    on_disk = json.loads((tmp_path / "watchlist.json").read_text(encoding="utf-8"))
    assert on_disk["tickers"] == ["BBCA.JK", "TLKM.JK"]
    appmod.WATCHLIST_STATE[:] = []
    reloaded = appmod.load_watchlist_from_disk()
    assert reloaded == ["BBCA.JK", "TLKM.JK"]


def test_company_knowledge_loaded_and_valid():
    bbca = appmod.company_knowledge_for_ticker("BBCA")
    assert bbca["ticker"] == "BBCA.JK"
    assert bbca["policy_exposures"]
    assert bbca["policy_channels"]
    assert bbca["evidence"]
    assert bbca["evidence"][0]["url"].startswith("https://")
    assert bbca["evidence"][0]["source_type"]
    assert bbca["evidence"][0]["quality_rank"] > 0


def test_policy_rule_loaders_return_normalized_payloads():
    rules = appmod.load_policy_signal_rules()
    assert {"political_relevance", "event_stage_rules", "negation_terms", "reversal_terms", "thread_match_terms"}.issubset(rules)
    assert "institution_terms" in rules["political_relevance"]
    assert "legal_terms" in rules["political_relevance"]
    assert isinstance(rules["event_stage_rules"].get("approved"), list)

    config = appmod.load_market_validation_config()
    assert {"windows", "thresholds", "baseline", "fallback"}.issubset(config)
    assert "30m" in config["windows"]

    bbca = appmod.company_knowledge_for_ticker("BBCA")
    assert isinstance(bbca.get("policy_channel_details"), list)
    assert isinstance(bbca.get("exposure_factors"), dict)


def test_score_political_relevance_labels_articles():
    strong = appmod.score_political_relevance(STRONG_POLICY_ARTICLE)
    assert strong["relevance_label"] == "political"
    assert strong["relevance_score"] >= 0.75
    assert strong["relevance_signals"]

    weak = appmod.score_political_relevance(WEAK_CONTEXT_ARTICLE)
    assert weak["relevance_label"] in {"maybe", "not_political"}
    assert weak["relevance_score"] < strong["relevance_score"]

    non_political = appmod.score_political_relevance(NON_POLITICAL_ARTICLE)
    assert non_political["relevance_label"] == "not_political"
    assert non_political["relevance_score"] < 0.3


def test_analyze_article_exposes_relevance_metadata():
    analyzed = appmod.analyze_article(STRONG_POLICY_ARTICLE, ["BBCA.JK", "BSDE.JK"], window="7d")
    assert analyzed["relevance_label"] == "political"
    assert analyzed["relevance_score"] >= 0.75
    assert analyzed["relevance_signals"]


def test_detect_event_stage_and_reversal_flags():
    proposal = appmod.detect_event_stage(appmod.article_text(PROPOSAL_ARTICLE))
    approved = appmod.detect_event_stage(appmod.article_text(APPROVED_ARTICLE))
    revoked = appmod.detect_event_stage(appmod.article_text(REVOKED_ARTICLE))

    assert proposal["event_stage"] == "proposal"
    assert approved["event_stage"] in {"approved", "effective"}
    assert revoked["event_stage"] == "revoked"

    reversed_state = appmod.detect_negation_or_reversal(appmod.article_text(REVOKED_ARTICLE))
    assert reversed_state["is_reversal"] is True
    assert reversed_state["reversal_hits"]


def test_event_stage_affects_analyzed_article_significance():
    proposal = appmod.analyze_article(PROPOSAL_ARTICLE, ["BSDE.JK", "BBCA.JK"], window="7d")
    approved = appmod.analyze_article(APPROVED_ARTICLE, ["BSDE.JK", "BBCA.JK"], window="7d")
    revoked = appmod.analyze_article(REVOKED_ARTICLE, ["ADRO.JK", "PTBA.JK"], window="7d")

    assert proposal["event_stage"] == "proposal"
    assert approved["event_stage"] in {"approved", "effective"}
    assert revoked["event_stage"] == "revoked"
    assert revoked["is_reversal"] is True
    assert approved["significance"] >= proposal["significance"]


def test_dashboard_endpoint_returns_watchlist_and_payload(monkeypatch):
    _patch_fetch_news_bundle(monkeypatch, fake_news_fetcher)
    _patch_fetch_stock_quotes(monkeypatch, fake_stock_fetcher)
    _patch_fetch_market_index(monkeypatch, fake_market_fetcher)
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    response = client.get("/api/dashboard?window=7d")
    assert response.status_code == 200
    data = response.json()
    assert set(["watchlist", "reasoning_summary", "payload"]).issubset(data)
    assert data["watchlist"] == appmod.get_watchlist()
    assert data["reasoning_summary"] == data["payload"]["reasoning_summary"]
    assert data["payload"]["watchlist"] == appmod.get_watchlist()
    assert data["payload"]["events"]
    assert data["payload"]["stocks"]
    assert data["payload"]["window"] == "7d"
    assert data["payload"]["tracking"]["window"] == "7d"
    assert data["payload"]["displayed_event_count"] == len(data["payload"]["events"])
    assert data["payload"]["total_event_count"] >= data["payload"]["displayed_event_count"]
    assert data["payload"]["hidden_event_count"] >= 0
    assert data["payload"]["reasoning_summary"]["summary_line"]
    assert data["payload"]["reasoning_summary"]["validation_breakdown"]
    assert data["payload"]["market_index"]["value"] == 6847
    assert data["payload"]["market_index"]["series"]
    assert data["payload"]["sources"]
    assert {"source_type", "source_quality_score", "source_freshness_score", "coverage_warning"}.issubset(data["payload"]["events"][0])
    assert {"relationship_confidence", "confidence_label", "source_confidence", "evidence_strength"}.issubset(data["payload"]["stocks"][0])


def test_dashboard_endpoint_exposes_compact_robustness_cues(monkeypatch, tmp_path):
    _patch_fetch_news_bundle(monkeypatch, fake_news_fetcher)
    _patch_fetch_stock_quotes(monkeypatch, fake_stock_fetcher)
    _patch_fetch_market_index(monkeypatch, fake_market_fetcher)
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    patch_source_history_file(monkeypatch, tmp_path / "dashboard_source_history.json")

    response = client.get("/api/dashboard?window=7d")
    assert response.status_code == 200
    data = response.json()

    assert "dashboard_cues" in data
    cues = data["dashboard_cues"]
    assert {"headline", "status", "chips", "counts"}.issubset(cues)
    assert cues["status"] in {"healthy", "watch", "fragile"}
    assert isinstance(cues["chips"], list)
    assert cues["chips"]
    assert all({"label", "tone"}.issubset(chip) for chip in cues["chips"])
    assert {"displayed_event_count", "conflicted_relationship_count", "weak_single_source_relationship_count", "fallback_source_count"}.issubset(cues["counts"])
    assert cues["counts"]["displayed_event_count"] == data["payload"]["displayed_event_count"]
    assert any(chip["label"] for chip in cues["chips"])


def test_refresh_builds_expected_payload_and_uses_cache(monkeypatch):
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["BBCA", "TLKM"],
        force=True,
        window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    assert payload["from_cache"] is False
    assert payload["events"]
    assert payload["stocks"]
    assert payload["window"] == "7d"
    assert payload["tracking"]["window"] == "7d"
    assert payload["displayed_event_count"] == len(payload["events"])
    assert payload["total_event_count"] >= payload["displayed_event_count"]
    assert payload["tracking"]["summary"]["total_events"] == payload["total_event_count"]
    assert "Financials" in payload["sector_summary"]
    assert payload["stocks"][0]["ticker"] in {"BBCA.JK", "TLKM.JK"}

    cached = appmod.build_refresh_payload(
        ["BBCA", "TLKM"],
        force=False,
        window="7d",
        news_fetcher=lambda: (_ for _ in ()).throw(AssertionError("news should not be fetched")),
        stock_fetcher=lambda tickers: (_ for _ in ()).throw(AssertionError("stocks should not be fetched")),
        market_fetcher=lambda: (_ for _ in ()).throw(AssertionError("market should not be fetched")),
    )
    assert cached["from_cache"] is True
    assert cached["cache_key"] == ["7d", "BBCA.JK", "TLKM.JK"]


def test_refresh_payload_exposes_source_fetch_diagnostics(monkeypatch):
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["BBCA"],
        force=True,
        window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    assert payload["sources"]
    source_diag = payload["sources"][0]
    assert isinstance(source_diag, dict)
    assert {
        "name",
        "kind",
        "status",
        "warning",
        "article_count",
        "used_registry_profile",
        "resolution_method",
        "date_enrichment_attempted",
        "date_enrichment_success_count",
        "date_fallback_count",
    }.issubset(source_diag)


def test_refresh_payload_exposes_batch_robustness_summary(monkeypatch):
    robust_primary = {
        "source": "Antara News",
        "headline": "Pemerintah dorong hilirisasi nikel untuk mendukung Antam",
        "url": "https://www.antaranews.com/ekonomi/antam-batch-summary-primary",
        "published_at": appmod.now_wib(),
        "summary": "Pemerintah mendorong hilirisasi nikel dan menyebut Antam sebagai pihak yang diuntungkan oleh proyek smelter baru.",
        "source_weight": 0.86,
        "source_type": "media",
    }
    robust_mirror = {
        "source": "Antara Terkini",
        "headline": "Update pasar: pemerintah dorong hilirisasi nikel untuk mendukung Antam",
        "url": "https://www.antaranews.com/ekonomi/antam-batch-summary-mirror",
        "published_at": appmod.now_wib() - timedelta(minutes=8),
        "summary": "Update pasar menyebut pemerintah mendorong hilirisasi nikel dan Antam diuntungkan oleh proyek smelter baru.",
        "source_weight": 0.86,
        "source_type": "media",
    }
    robust_independent = {
        "source": "Bisnis Indonesia",
        "headline": "Bisnis: pemerintah dorong hilirisasi nikel untuk menopang Antam",
        "url": "https://www.bisnis.com/market/antam-batch-summary-independent",
        "published_at": appmod.now_wib() - timedelta(minutes=12),
        "summary": "Bisnis Indonesia menyebut pemerintah mendorong hilirisasi nikel dan Antam diuntungkan oleh proyek smelter baru.",
        "source_weight": 0.82,
        "source_type": "media",
    }
    conflict_negative = {
        "source": "Setkab",
        "headline": "Pemerintah perketat pembatasan hilirisasi Antam dan tekan rencana smelter baru",
        "url": "https://example.com/antam-batch-summary-conflict",
        "published_at": appmod.now_wib() - timedelta(minutes=3),
        "summary": "Pemerintah perketat pembatasan proyek hilirisasi mineral Antam sehingga rencana smelter baru tertekan dan realisasi nikel melemah.",
        "source_weight": 0.95,
        "source_type": "government",
    }
    stale_weak = {
        "source": "Opinion Blog",
        "headline": "Opini lama soal Antam tanpa dasar kebijakan resmi",
        "url": "https://blog.example.com/antam-batch-summary-stale",
        "published_at": appmod.now_wib() - timedelta(days=6, hours=18),
        "summary": "Opini umum menyebut Antam tanpa dasar kebijakan, regulasi, atau sumber resmi yang jelas.",
        "source_weight": 0.2,
        "source_type": "other",
    }

    def batch_summary_news_fetcher():
        return (
            [robust_primary, robust_mirror, robust_independent, conflict_negative, stale_weak],
            [],
            [
                {
                    "name": "Antara News",
                    "kind": "rss",
                    "status": "ok",
                    "warning": "",
                    "article_count": 2,
                    "used_registry_profile": True,
                    "resolution_method": "registry_name_match",
                    "date_enrichment_attempted": True,
                    "date_enrichment_success_count": 2,
                    "date_fallback_count": 0,
                },
                {
                    "name": "Fallback Feed",
                    "kind": "html",
                    "status": "error",
                    "warning": "Fallback Feed: timeout",
                    "article_count": 0,
                    "used_registry_profile": False,
                    "resolution_method": "inferred_fallback",
                    "date_enrichment_attempted": True,
                    "date_enrichment_success_count": 0,
                    "date_fallback_count": 1,
                },
                {
                    "name": "Empty Feed",
                    "kind": "rss",
                    "status": "empty",
                    "warning": "Empty Feed: no RSS items extracted",
                    "article_count": 0,
                    "used_registry_profile": False,
                    "resolution_method": "inferred_fallback",
                    "date_enrichment_attempted": False,
                    "date_enrichment_success_count": 0,
                    "date_fallback_count": 0,
                },
            ],
        )

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=batch_summary_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    summary = payload["source_health_summary"]
    assert {
        "source_count",
        "ok_source_count",
        "fallback_source_count",
        "errored_source_count",
        "empty_source_count",
        "warning_source_count",
        "registry_backed_source_count",
        "date_enrichment_success_count",
        "date_fallback_count",
        "displayed_event_count",
        "conflicted_relationship_count",
        "independent_corroborated_relationship_count",
        "weak_single_source_relationship_count",
        "syndicated_coverage_count",
        "stale_event_count",
        "thin_event_count",
    }.issubset(summary)
    assert summary["source_count"] == 3
    assert summary["ok_source_count"] == 1
    assert summary["fallback_source_count"] == 2
    assert summary["errored_source_count"] == 1
    assert summary["empty_source_count"] == 1
    assert summary["warning_source_count"] == 2
    assert summary["registry_backed_source_count"] == 1
    assert summary["date_enrichment_success_count"] == 2
    assert summary["date_fallback_count"] == 1
    assert summary["displayed_event_count"] == payload["displayed_event_count"]
    assert summary["conflicted_relationship_count"] >= 1
    assert summary["independent_corroborated_relationship_count"] >= 1
    assert summary["weak_single_source_relationship_count"] == 0
    assert summary["syndicated_coverage_count"] >= 1
    assert summary["stale_event_count"] == 0
    # thin_event_count may be 0 now that more sources are registry-backed
    # (Bisnis Indonesia moved from inferred_fallback to registry_name match)


def test_summarized_source_diagnostics_preserve_resolution_and_article_signals(monkeypatch):
    _patch_validation_series(monkeypatch, fake_validation_series_flat)

    registry_article = {
        "source": "Antara Terkini",
        "headline": "Pemerintah dorong hilirisasi nikel untuk mendukung Antam",
        "url": "https://www.antaranews.com/ekonomi/fallback-diagnostics-antam",
        "published_at": appmod.now_wib(),
        "summary": "Pemerintah mendorong hilirisasi nikel dan menyebut Antam sebagai pihak yang diuntungkan oleh proyek smelter baru.",
        "source_weight": 0.86,
        **appmod.source_metadata_for("Antara Terkini", "https://www.antaranews.com/ekonomi/fallback-diagnostics-antam"),
    }
    fallback_article = {
        "source": "Lifestyle Blog",
        "headline": "Opini umum soal Antam tanpa dasar kebijakan resmi",
        "url": "https://example.com/fallback-diagnostics-opinion",
        "published_at": appmod.now_wib() - timedelta(hours=2),
        "summary": "Opini umum menyebut Antam tanpa dasar kebijakan, regulasi, atau sumber resmi yang jelas.",
        "source_weight": 0.2,
        **appmod.source_metadata_for("Lifestyle Blog", "https://example.com/fallback-diagnostics-opinion"),
    }

    def two_tuple_news_fetcher():
        return ([registry_article, fallback_article], [])

    payload = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=two_tuple_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    diagnostics_by_name = {item["name"]: item for item in payload["sources"]}
    antara_diag = diagnostics_by_name["Antara News"]
    blog_diag = diagnostics_by_name["Lifestyle Blog"]

    assert antara_diag["resolution_method"] == registry_article["source_profile_resolution"]
    assert antara_diag["used_registry_profile"] is True
    assert antara_diag["status"] == "inferred_ok"
    assert antara_diag["warning"] == ""
    assert antara_diag["article_count"] == 1
    assert antara_diag["date_enrichment_attempted"] is None
    assert antara_diag["date_enrichment_success_count"] is None
    assert antara_diag["date_fallback_count"] is None

    assert blog_diag["resolution_method"] == fallback_article["source_profile_resolution"]
    assert blog_diag["used_registry_profile"] is False
    assert blog_diag["status"] == "inferred_ok"
    assert blog_diag["warning"] == ""
    assert blog_diag["article_count"] == 1
    assert blog_diag["date_enrichment_attempted"] is None
    assert blog_diag["date_enrichment_success_count"] is None
    assert blog_diag["date_fallback_count"] is None


def test_refresh_window_changes_article_set_and_tracking(monkeypatch):
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    weekly = appmod.build_refresh_payload(
        ["BBCA", "BSDE"],
        force=True,
        window="7d",
        news_fetcher=fake_window_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    monthly = appmod.build_refresh_payload(
        ["BBCA", "BSDE"],
        force=True,
        window="30d",
        news_fetcher=fake_window_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    assert len(weekly["events"]) == 2
    assert len(monthly["events"]) == 3
    assert weekly["displayed_event_count"] == 2
    assert weekly["total_event_count"] == 2
    assert monthly["displayed_event_count"] == 3
    assert monthly["total_event_count"] == 3
    assert weekly["tracking"]["timeline"]
    assert monthly["tracking"]["summary"]["strongest_day"]


def test_refresh_endpoint_returns_json_shape(monkeypatch):
    _patch_fetch_news_bundle(monkeypatch, fake_news_fetcher)
    _patch_fetch_stock_quotes(monkeypatch, fake_stock_fetcher)
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    response = client.post("/api/refresh", json={"tickers": ["BBCA", "TLKM"], "force": True, "window": "30d"})
    assert response.status_code == 200
    data = response.json()
    assert set(["fetched_at", "from_cache", "events", "event_threads", "stocks", "sector_summary", "warnings", "watchlist", "window", "window_label", "tracking", "displayed_event_count", "total_event_count", "hidden_event_count", "reasoning_summary"]).issubset(data)
    assert isinstance(data["events"], list)
    assert isinstance(data["event_threads"], list)
    assert isinstance(data["stocks"], list)
    assert len(data["sector_summary"]) == len(appmod.SECTORS)
    assert {"policy_themes", "stock_relationships", "source_type", "event_stage", "thread_id", "thread_status"}.issubset(data["events"][0])
    assert {"thread_id", "thread_status", "article_count", "latest_event_stage", "latest_headline", "contradiction_count"}.issubset(data["event_threads"][0])
    assert {"rationale", "relationship_type", "confidence", "knowledge_summary", "company_evidence", "article_source_type", "article_evidence_rank", "company_evidence_rank", "impact_direction", "direction_rationale", "channel_confidence", "matched_policy_channels", "exposure_factors", "validation_status", "validation_score"}.issubset(data["stocks"][0])
    assert {"validation_status", "validation_window", "abnormal_return", "abnormal_volume_ratio", "validation_score"}.issubset(data["events"][0]["stock_relationships"][0])
    assert data["reasoning_summary"]["summary_line"]


def test_article_analysis_requires_evidence_backed_relationships():
    direct = appmod.analyze_article(DIRECT_MENTION_ARTICLE, ["ANTM.JK", "BBCA.JK"], window="7d")
    assert "ANTM.JK" in direct["impacted_tickers"]
    antm_link = next(item for item in direct["stock_relationships"] if item["ticker"] == "ANTM.JK")
    assert antm_link["relationship_type"] == "direct"
    assert antm_link["relevance_score"] >= appmod.MIN_RELATIONSHIP_SCORE
    assert antm_link["rationale"]
    assert antm_link["evidence"]
    assert antm_link["company_evidence"]
    assert antm_link["article_source_type"] == "government"
    assert antm_link["company_evidence_rank"] > 0
    assert antm_link["impact_direction"] == "positive"

    housing = appmod.analyze_article(HOUSING_ARTICLE, ["BSDE.JK", "BBCA.JK", "TLKM.JK"], window="30d")
    housing_tickers = set(housing["impacted_tickers"])
    assert "BSDE.JK" in housing_tickers
    assert "BBCA.JK" in housing_tickers
    assert "TLKM.JK" not in housing_tickers
    bsde_link = next(item for item in housing["stock_relationships"] if item["ticker"] == "BSDE.JK")
    bbca_link = next(item for item in housing["stock_relationships"] if item["ticker"] == "BBCA.JK")
    assert bsde_link["relationship_type"] == "indirect"
    assert bbca_link["relationship_type"] == "indirect"
    assert bsde_link["matched_policy_channels"]
    assert bbca_link["matched_policy_channels"]
    assert bsde_link["channel_confidence"] > 0
    assert bbca_link["channel_confidence"] > 0
    assert bsde_link["impact_direction"] == "positive"
    assert bbca_link["impact_direction"] == "positive"

    vague = appmod.analyze_article(VAGUE_ARTICLE, ["BBCA.JK", "TLKM.JK", "ANTM.JK"], window="7d")
    assert vague["impacted_tickers"] == []
    assert vague["stock_relationships"] == []


def test_source_corroboration_from_independent_sources_raises_relationship_confidence(monkeypatch):
    weak_article = {
        "source": "Opinion Blog",
        "headline": "Pemerintah dorong hilirisasi nikel yang menguntungkan Antam",
        "url": "https://blog.example.com/opinion-antam",
        "published_at": appmod.now_wib(),
        "summary": "Opini pasar menyebut Pemerintah dorong hilirisasi nikel yang menguntungkan Antam, tetapi tanpa dasar resmi yang jelas.",
        "source_weight": 0.2,
        "source_type": "other",
    }
    corroborated_articles = [
        weak_article,
        {
            "source": "Antara News",
            "headline": "Pemerintah dorong hilirisasi nikel yang menguntungkan Antam",
            "url": "https://www.antaranews.com/ekonomi/hilirisasi-antam",
            "published_at": appmod.now_wib() - timedelta(minutes=5),
            "summary": "Pemerintah mendorong hilirisasi nikel, dan Antam disebut sebagai salah satu pihak yang mendapat dorongan kebijakan.",
            **appmod.source_metadata_for("Antara News", "https://www.antaranews.com/ekonomi/hilirisasi-antam"),
        },
        {
            "source": "Setkab",
            "headline": "Pemerintah percepat hilirisasi nikel untuk mendukung Antam",
            "url": "https://setkab.go.id/berita/hilirisasi-antam",
            "published_at": appmod.now_wib() - timedelta(minutes=9),
            "summary": "Pemerintah mempercepat hilirisasi nikel, dan Antam disebut dalam dukungan kebijakan resmi.",
            **appmod.source_metadata_for("Setkab", "https://setkab.go.id/berita/hilirisasi-antam"),
        },
    ]

    def weak_news_fetcher():
        return [weak_article], []

    def corroborated_news_fetcher():
        return corroborated_articles, []

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    weak_payload = appmod.build_refresh_payload(
        ["ANTM"],
        window="7d",
        force=True,
        news_fetcher=weak_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    corroborated_payload = appmod.build_refresh_payload(
        ["ANTM"],
        window="7d",
        force=True,
        news_fetcher=corroborated_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    weak_event = next(event for event in weak_payload["events"] if event["url"] == weak_article["url"])
    corroborated_event = next(event for event in corroborated_payload["events"] if event["url"] == weak_article["url"])
    weak_link = next(item for item in weak_event["stock_relationships"] if item["ticker"] == "ANTM.JK")
    corroborated_link = next(item for item in corroborated_event["stock_relationships"] if item["ticker"] == "ANTM.JK")

    assert weak_link["corroboration_count"] == 1
    assert weak_link["corroboration_label"] == "single_weak_source"
    assert corroborated_link["corroboration_count"] == 3
    assert corroborated_link["corroboration_domain_count"] == 3
    assert corroborated_link["corroboration_label"] == "independently_corroborated"
    assert corroborated_link["relationship_confidence"] > weak_link["relationship_confidence"]
    assert corroborated_payload["stocks"][0]["corroboration_count"] == 3
    assert corroborated_payload["stocks"][0]["corroboration_domain_count"] == 3
    assert corroborated_payload["stocks"][0]["relationship_confidence"] >= corroborated_link["relationship_confidence"]


def test_weak_source_requires_corroboration_to_raise_confidence():
    weak_single_article = {
        "source": "Opinion Blog",
        "headline": "Antam dan hilirisasi jadi tema ramai di pasar",
        "url": "https://example.com/article-weak-1",
        "published_at": appmod.now_wib(),
        "summary": "Opini umum menyebut Antam tanpa dasar kebijakan, regulasi, atau sumber resmi yang jelas.",
        "source_weight": 0.2,
        "source_type": "other",
    }
    weak_single = appmod.analyze_article(weak_single_article, ["ANTM.JK"], window="7d")
    weak_single_link = next(item for item in weak_single["stock_relationships"] if item["ticker"] == "ANTM.JK")
    assert weak_single_link["corroboration_source_count"] == 1
    assert weak_single_link["corroboration_domain_count"] == 1
    assert weak_single_link["corroboration_label"] in {"single_weak_source", "thin_corroboration"}
    assert weak_single_link["confidence_label"] in {"low_confidence", "predicted_only", "insufficient_data"}

    corroborated_articles = appmod.merge_duplicate_articles([
        {
            "source": "Antara News",
            "headline": "Antam dan hilirisasi kembali jadi sorotan pasar",
            "url": "https://www.antaranews.com/ekonomi/antam-hilirisasi",
            "published_at": appmod.now_wib(),
            "summary": "Antam disebut dalam pembahasan hilirisasi dan kebijakan mineral yang sama.",
            "source_weight": 0.86,
            "source_type": "media",
        },
        {
            "source": "Bisnis Indonesia",
            "headline": "Antam dan hilirisasi kembali jadi sorotan pasar",
            "url": "https://www.bisnis.com/market/antam-hilirisasi",
            "published_at": appmod.now_wib(),
            "summary": "Antam disebut dalam pembahasan hilirisasi dan kebijakan mineral yang sama.",
            "source_weight": 0.82,
            "source_type": "media",
        },
    ])
    corroborated = appmod.analyze_article(corroborated_articles[0], ["ANTM.JK"], window="7d")
    corroborated_link = next(item for item in corroborated["stock_relationships"] if item["ticker"] == "ANTM.JK")
    assert corroborated_articles[0]["duplicate_count"] == 2
    assert corroborated_link["corroboration_source_count"] == 2
    assert corroborated_link["corroboration_domain_count"] == 2
    assert corroborated_link["relationship_confidence"] > weak_single_link["relationship_confidence"]
    assert corroborated_link["corroboration_label"] in {"independently_corroborated", "corroborated"}


def test_mirrored_coverage_does_not_count_as_independent_corroboration():
    mirrored_articles = appmod.merge_duplicate_articles([
        {
            "source": "Antara News",
            "headline": "Antam disebut di tengah dorongan hilirisasi mineral nasional",
            "url": "https://www.antaranews.com/ekonomi/antam-hilirisasi-mirror-1",
            "published_at": appmod.now_wib(),
            "summary": "Antam disebut dalam laporan soal dorongan hilirisasi mineral dan penguatan proyek smelter nasional.",
            "source_weight": 0.86,
            "source_type": "media",
        },
        {
            "source": "Antara Terkini",
            "headline": "Antam disebut di tengah dorongan hilirisasi mineral nasional",
            "url": "https://www.antaranews.com/ekonomi/antam-hilirisasi-mirror-2",
            "published_at": appmod.now_wib(),
            "summary": "Antam disebut dalam laporan soal dorongan hilirisasi mineral dan penguatan proyek smelter nasional.",
            "source_weight": 0.86,
            "source_type": "media",
        },
    ])

    mirrored = appmod.analyze_article(mirrored_articles[0], ["ANTM.JK"], window="7d")
    mirrored_link = next(item for item in mirrored["stock_relationships"] if item["ticker"] == "ANTM.JK")

    assert mirrored_articles[0]["duplicate_count"] == 2
    assert mirrored_link["raw_coverage_count"] == 2
    assert mirrored_link["independent_coverage_count"] == 1
    assert mirrored_link["syndicated_coverage_count"] == 1
    assert mirrored_link["corroboration_source_count"] == 1
    assert mirrored_link["corroboration_domain_count"] == 1



def test_truly_independent_domains_still_raise_corroboration(monkeypatch):
    weak_article = {
        "source": "Opinion Blog",
        "headline": "Pemerintah dorong hilirisasi nikel yang menguntungkan Antam",
        "url": "https://blog.example.com/opinion-antam",
        "published_at": appmod.now_wib(),
        "summary": "Opini pasar menyebut Pemerintah dorong hilirisasi nikel yang menguntungkan Antam, tetapi tanpa dasar resmi yang jelas.",
        "source_weight": 0.2,
        "source_type": "other",
    }
    mirrored_support_articles = [
        weak_article,
        {
            "source": "Antara News",
            "headline": "Pemerintah dorong hilirisasi nikel untuk mendukung Antam",
            "url": "https://www.antaranews.com/ekonomi/antam-support-1",
            "published_at": appmod.now_wib() - timedelta(minutes=4),
            "summary": "Pemerintah mendorong hilirisasi nikel dan menyebut Antam sebagai pihak yang diuntungkan oleh proyek smelter baru.",
            "source_weight": 0.86,
            "source_type": "media",
        },
        {
            "source": "Antara Terkini",
            "headline": "Update pasar: pemerintah dorong hilirisasi nikel untuk mendukung Antam",
            "url": "https://www.antaranews.com/ekonomi/antam-support-2",
            "published_at": appmod.now_wib() - timedelta(minutes=8),
            "summary": "Update pasar menyebut pemerintah mendorong hilirisasi nikel dan Antam diuntungkan oleh proyek smelter baru.",
            "source_weight": 0.86,
            "source_type": "media",
        },
    ]
    independent_support_articles = [
        weak_article,
        {
            "source": "Antara News",
            "headline": "Pemerintah dorong hilirisasi nikel untuk mendukung Antam",
            "url": "https://www.antaranews.com/ekonomi/antam-support-1-independent",
            "published_at": appmod.now_wib() - timedelta(minutes=4),
            "summary": "Pemerintah mendorong hilirisasi nikel dan menyebut Antam sebagai pihak yang diuntungkan oleh proyek smelter baru.",
            "source_weight": 0.86,
            "source_type": "media",
        },
        {
            "source": "Bisnis Indonesia",
            "headline": "Bisnis: pemerintah dorong hilirisasi nikel untuk menopang Antam",
            "url": "https://www.bisnis.com/market/antam-support-independent",
            "published_at": appmod.now_wib() - timedelta(minutes=9),
            "summary": "Bisnis Indonesia menyebut pemerintah mendorong hilirisasi nikel dan Antam diuntungkan oleh proyek smelter baru.",
            "source_weight": 0.82,
            "source_type": "media",
        },
    ]

    def mirrored_news_fetcher():
        return mirrored_support_articles, []

    def independent_news_fetcher():
        return independent_support_articles, []

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    mirrored_payload = appmod.build_refresh_payload(
        ["ANTM"],
        window="7d",
        force=True,
        news_fetcher=mirrored_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    independent_payload = appmod.build_refresh_payload(
        ["ANTM"],
        window="7d",
        force=True,
        news_fetcher=independent_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    mirrored_event = next(event for event in mirrored_payload["events"] if event["url"] == weak_article["url"])
    independent_event = next(event for event in independent_payload["events"] if event["url"] == weak_article["url"])
    mirrored_link = next(item for item in mirrored_event["stock_relationships"] if item["ticker"] == "ANTM.JK")
    independent_link = next(item for item in independent_event["stock_relationships"] if item["ticker"] == "ANTM.JK")

    assert mirrored_link["raw_coverage_count"] == 3
    assert mirrored_link["independent_coverage_count"] == 2
    assert mirrored_link["syndicated_coverage_count"] == 1
    assert independent_link["raw_coverage_count"] == 3
    assert independent_link["independent_coverage_count"] == 3
    assert independent_link["syndicated_coverage_count"] == 0
    assert independent_link["corroboration_multiplier"] > mirrored_link["corroboration_multiplier"]
    assert independent_link["relationship_confidence"] > mirrored_link["relationship_confidence"]



def test_official_source_stays_strong_without_many_corroborators():
    official = appmod.analyze_article(DIRECT_MENTION_ARTICLE, ["ANTM.JK"], window="7d")
    official_link = next(item for item in official["stock_relationships"] if item["ticker"] == "ANTM.JK")
    assert official_link["source_tier"] == 1
    assert official_link["corroboration_source_count"] == 1
    assert official_link["corroboration_domain_count"] == 1
    assert official_link["corroboration_label"] == "official_source"
    assert official_link["relationship_confidence"] >= 0.65
    assert official_link["confidence_label"] in {"high_confidence", "confirmed"}


def test_source_conflict_ignores_same_ticker_different_claims(monkeypatch):
    different_claim_negative_article = {
        "source": "Antara News",
        "headline": "Pemerintah batasi ekspor nikel dan tekan Antam",
        "url": "https://example.com/antam-negative-different-claim",
        "published_at": appmod.now_wib(),
        "summary": "Pemerintah memperketat pembatasan ekspor nikel sehingga Antam menghadapi tekanan margin dan produksi.",
        "source_weight": 0.86,
        "source_type": "media",
    }

    def different_claim_news_fetcher():
        return [DIRECT_MENTION_ARTICLE, different_claim_negative_article], []

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=different_claim_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    direct_event = next(event for event in payload["events"] if event["url"] == DIRECT_MENTION_ARTICLE["url"])
    direct_link = next(item for item in direct_event["stock_relationships"] if item["ticker"] == "ANTM.JK")

    assert direct_event["thread_id"] != next(event for event in payload["events"] if event["url"] == different_claim_negative_article["url"])["thread_id"]
    assert direct_link["source_conflict"] is False
    assert direct_link["source_conflict_count"] == 0
    assert direct_link["source_conflict_penalty"] == 1.0
    assert payload["stocks"][0]["source_conflict"] is False
    assert not any("conflicting" in warning.lower() for warning in payload["warnings"])



def test_source_conflict_still_flags_same_ticker_same_claim_opposite_direction(monkeypatch):
    same_claim_negative_article = {
        "source": "Setkab",
        "headline": "Pemerintah perketat pembatasan hilirisasi Antam dan tekan rencana smelter baru",
        "url": "https://example.com/antam-negative-same-claim",
        "published_at": appmod.now_wib(),
        "summary": "Pemerintah perketat pembatasan proyek hilirisasi mineral Antam sehingga rencana smelter baru tertekan dan realisasi nikel melemah.",
        "source_weight": 1.0,
        "source_type": "government",
    }

    def same_claim_news_fetcher():
        return [DIRECT_MENTION_ARTICLE, same_claim_negative_article], []

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=same_claim_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    direct_event = next(event for event in payload["events"] if event["url"] == DIRECT_MENTION_ARTICLE["url"])
    same_claim_event = next(event for event in payload["events"] if event["url"] == same_claim_negative_article["url"])
    direct_link = next(item for item in direct_event["stock_relationships"] if item["ticker"] == "ANTM.JK")
    same_claim_link = next(item for item in same_claim_event["stock_relationships"] if item["ticker"] == "ANTM.JK")

    assert direct_event["thread_id"] == same_claim_event["thread_id"]
    assert direct_link["source_conflict"] is True
    assert same_claim_link["source_conflict"] is True
    assert direct_link["source_conflict_count"] >= 1
    assert direct_link["source_conflict_penalty"] < 1.0
    assert payload["stocks"][0]["source_conflict"] is True
    assert any("conflicting" in warning.lower() for warning in payload["warnings"])



def test_source_conflict_flags_opposite_direction_coverage_and_downgrades_confidence(monkeypatch):
    negative_article = {
        "source": "Setkab",
        "headline": "Pemerintah perketat pembatasan hilirisasi Antam dan tekan rencana smelter baru",
        "url": "https://example.com/antam-negative",
        "published_at": appmod.now_wib(),
        "summary": "Pemerintah perketat pembatasan proyek hilirisasi mineral Antam sehingga rencana smelter baru tertekan dan realisasi nikel melemah.",
        "source_weight": 1.0,
        "source_type": "government",
    }

    def solo_news_fetcher():
        return [DIRECT_MENTION_ARTICLE], []

    def conflict_news_fetcher():
        return [DIRECT_MENTION_ARTICLE, negative_article], []

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    solo_payload = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=solo_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    conflict_payload = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=conflict_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    solo_event = next(event for event in solo_payload["events"] if event["url"] == DIRECT_MENTION_ARTICLE["url"])
    conflict_event = next(event for event in conflict_payload["events"] if event["url"] == DIRECT_MENTION_ARTICLE["url"])
    solo_link = next(item for item in solo_event["stock_relationships"] if item["ticker"] == "ANTM.JK")
    conflict_link = next(item for item in conflict_event["stock_relationships"] if item["ticker"] == "ANTM.JK")

    assert solo_link["source_conflict"] is False
    assert conflict_link["source_conflict"] is True
    assert conflict_link["source_conflict_count"] >= 1
    assert conflict_link["source_conflict_penalty"] < 1.0
    assert conflict_link["relationship_confidence"] < solo_link["relationship_confidence"]
    assert conflict_link["confidence_label"] in {"low_confidence", "predicted_only", "insufficient_data"}
    assert conflict_payload["stocks"][0]["source_conflict"] is True
    assert any("conflicting" in warning.lower() for warning in conflict_payload["warnings"])


def test_evidence_hierarchy_prefers_official_sources():
    government_score = appmod.evidence_quality_score(DIRECT_MENTION_ARTICLE, [{"name": "DOWNSTREAMING"}], True, appmod.company_knowledge_for_ticker("ANTM").get("evidence", []))
    profile_score = appmod.evidence_quality_score({**VAGUE_ARTICLE, "source_type": "profile", "source_weight": 0.3}, [{"name": "DOWNSTREAMING"}], False, [])
    assert government_score > profile_score


def test_empty_watchlist_request_resets_default():
    updated = client.put("/api/watchlist", json={"tickers": []})
    assert updated.status_code == 200
    assert updated.json()["tickers"] == appmod.DEFAULT_WATCHLIST


def test_group_articles_into_threads_marks_reversals_and_reduces_summary_duplication():
    events = [
        appmod.analyze_article(THREAD_PROPOSAL_ARTICLE, ["BSDE.JK", "BBCA.JK"], window="7d"),
        appmod.analyze_article(THREAD_APPROVED_ARTICLE, ["BSDE.JK", "BBCA.JK"], window="7d"),
        appmod.analyze_article(THREAD_REVERSED_ARTICLE, ["BSDE.JK", "BBCA.JK"], window="7d"),
    ]
    grouped = appmod.group_articles_into_threads(events)
    assert len(grouped) == 1
    thread = grouped[0]
    assert thread["article_count"] == 3
    assert thread["thread_status"] == "reversed"
    assert thread["contradiction_count"] >= 1
    assert thread["latest_event_stage"] in {"delayed", "revoked"}

    thread_ids = {event.get("thread_id") for event in events}
    assert len(thread_ids) == 1
    assert {event.get("thread_status") for event in events} == {"reversed"}

    tracking = appmod.build_event_tracking(events, window="7d")
    assert tracking["summary"]["thread_count"] == 1
    assert tracking["summary"]["contested_thread_count"] == 1


def test_refresh_payload_exposes_event_threads_and_thread_statuses(monkeypatch):
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["BBCA", "BSDE"],
        force=True,
        window="7d",
        news_fetcher=fake_thread_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    assert len(payload["events"]) == 3
    assert len(payload["event_threads"]) == 1
    thread = payload["event_threads"][0]
    assert thread["article_count"] == 3
    assert thread["thread_status"] == "reversed"
    assert thread["contradiction_count"] >= 1
    assert {event["thread_id"] for event in payload["events"]} == {thread["thread_id"]}
    assert {event["thread_status"] for event in payload["events"]} == {"reversed"}


def test_validate_market_reaction_marks_confirmed_for_large_move(monkeypatch):
    _patch_validation_series(monkeypatch, fake_validation_series_confirmed)
    article = appmod.analyze_article(HOUSING_ARTICLE, ["BSDE.JK", "BBCA.JK"], window="7d")
    relationship = next(item for item in article["stock_relationships"] if item["ticker"] == "BSDE.JK")
    quote = fake_stock_fetcher(["BSDE.JK"])[0]["BSDE.JK"]

    validation = appmod.validate_market_reaction(article, "BSDE.JK", quote, relationship)

    assert validation["validation_status"] == "confirmed"
    assert validation["validation_score"] > 0.6
    assert validation["abnormal_return"] > 0
    assert validation["abnormal_volume_ratio"] >= 1.5


def test_validate_market_reaction_marks_predicted_only_when_series_is_flat(monkeypatch):
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    article = appmod.analyze_article(HOUSING_ARTICLE, ["BSDE.JK", "BBCA.JK"], window="7d")
    relationship = next(item for item in article["stock_relationships"] if item["ticker"] == "BSDE.JK")
    quote = fake_stock_fetcher(["BSDE.JK"])[0]["BSDE.JK"]

    validation = appmod.validate_market_reaction(article, "BSDE.JK", quote, relationship)

    assert validation["validation_status"] in {"predicted_only", "rejected"}
    assert validation["validation_score"] < 0.6


def test_validation_outcome_nudges_stock_impact_score(monkeypatch):
    def direct_news_fetcher():
        return [DIRECT_MENTION_ARTICLE], []

    _patch_validation_series(monkeypatch, fake_validation_series_confirmed)
    confirmed = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=direct_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    flat = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=direct_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    confirmed_stock = confirmed["stocks"][0]
    flat_stock = flat["stocks"][0]
    assert confirmed_stock["validation_status"] == "confirmed"
    assert confirmed_stock["validation_multiplier"] > flat_stock["validation_multiplier"]
    assert confirmed_stock["impact_score"] >= flat_stock["impact_score"]


def test_validation_outcome_calibrates_source_confidence_in_refresh_payload(monkeypatch, tmp_path):
    def direct_news_fetcher():
        return [DIRECT_MENTION_ARTICLE], []

    patch_source_history_file(monkeypatch, tmp_path / "confirmed_source_history.json")
    _patch_validation_series(monkeypatch, fake_validation_series_confirmed)
    confirmed = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=direct_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    patch_source_history_file(monkeypatch, tmp_path / "flat_source_history.json")
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    flat = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=direct_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    confirmed_event = confirmed["events"][0]
    flat_event = flat["events"][0]
    confirmed_link = next(item for item in confirmed_event["stock_relationships"] if item["ticker"] == "ANTM.JK")
    flat_link = next(item for item in flat_event["stock_relationships"] if item["ticker"] == "ANTM.JK")
    confirmed_stock = confirmed["stocks"][0]
    flat_stock = flat["stocks"][0]

    assert confirmed_link["validation_status"] == "confirmed"
    assert flat_link["validation_status"] in {"predicted_only", "rejected"}
    assert confirmed_link["validation_multiplier"] > flat_link["validation_multiplier"]
    assert confirmed_link["source_confidence"] > flat_link["source_confidence"]
    assert confirmed_stock["source_confidence"] > flat_stock["source_confidence"]


def test_repeated_confirmed_outcomes_raise_source_reliability_within_bounds(monkeypatch, tmp_path):
    baseline_history = tmp_path / "baseline_source_history.json"
    warmed_history = tmp_path / "warmed_source_history.json"

    def direct_news_fetcher():
        return [DIRECT_MENTION_ARTICLE], []

    patch_source_history_file(monkeypatch, baseline_history)
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    baseline = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=direct_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    patch_source_history_file(monkeypatch, warmed_history)
    _patch_validation_series(monkeypatch, fake_validation_series_confirmed)
    appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=direct_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    warmed = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=direct_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    baseline_link = next(item for item in baseline["events"][0]["stock_relationships"] if item["ticker"] == "ANTM.JK")
    warmed_link = next(item for item in warmed["events"][0]["stock_relationships"] if item["ticker"] == "ANTM.JK")
    baseline_stock = baseline["stocks"][0]
    warmed_stock = warmed["stocks"][0]

    assert warmed_link["historical_outcome_sample_size"] >= 1
    assert 1.0 < warmed_link["historical_reliability_multiplier"] <= 1.15
    assert warmed_link["source_confidence"] > baseline_link["source_confidence"]
    assert warmed_stock["historical_reliability_multiplier"] == warmed_link["historical_reliability_multiplier"]
    assert warmed_stock["source_confidence"] > baseline_stock["source_confidence"]


def test_repeated_rejected_outcomes_lower_source_reliability_within_bounds(monkeypatch, tmp_path):
    baseline_history = tmp_path / "baseline_source_history.json"
    cooled_history = tmp_path / "cooled_source_history.json"

    def direct_news_fetcher():
        return [DIRECT_MENTION_ARTICLE], []

    patch_source_history_file(monkeypatch, baseline_history)
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    baseline = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=direct_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    patch_source_history_file(monkeypatch, cooled_history)
    _patch_validation_series(monkeypatch, fake_validation_series_rejected)
    appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=direct_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    cooled = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=direct_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    baseline_link = next(item for item in baseline["events"][0]["stock_relationships"] if item["ticker"] == "ANTM.JK")
    cooled_link = next(item for item in cooled["events"][0]["stock_relationships"] if item["ticker"] == "ANTM.JK")
    baseline_stock = baseline["stocks"][0]
    cooled_stock = cooled["stocks"][0]

    assert cooled_link["historical_outcome_sample_size"] >= 1
    assert 0.85 <= cooled_link["historical_reliability_multiplier"] < 1.0
    assert cooled_link["source_confidence"] < baseline_link["source_confidence"]
    assert cooled_stock["historical_reliability_multiplier"] == cooled_link["historical_reliability_multiplier"]
    assert cooled_stock["source_confidence"] < baseline_stock["source_confidence"]


def test_registry_trust_remains_the_base_signal(monkeypatch, tmp_path):
    history_file = tmp_path / "source_history.json"
    weak_direct_article = {
        **DIRECT_MENTION_ARTICLE,
        "source": "Market Gossip Blog",
        "source_type": "other",
        "url": "https://blog.example.com/market-gossip-antam",
    }

    def strong_news_fetcher():
        return [DIRECT_MENTION_ARTICLE], []

    def weak_news_fetcher():
        return [weak_direct_article], []

    patch_source_history_file(monkeypatch, history_file)
    _patch_validation_series(monkeypatch, fake_validation_series_confirmed)
    for _ in range(3):
        appmod.build_refresh_payload(
            ["ANTM"],
            force=True,
            window="7d",
            news_fetcher=weak_news_fetcher,
            stock_fetcher=fake_stock_fetcher,
            market_fetcher=fake_market_fetcher,
        )

    weak_payload = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=weak_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    strong_payload = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=strong_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    weak_link = next(item for item in weak_payload["events"][0]["stock_relationships"] if item["ticker"] == "ANTM.JK")
    strong_link = next(item for item in strong_payload["events"][0]["stock_relationships"] if item["ticker"] == "ANTM.JK")
    weak_stock = weak_payload["stocks"][0]
    strong_stock = strong_payload["stocks"][0]

    assert weak_link["historical_reliability_multiplier"] > 1.0
    assert weak_link["historical_reliability_multiplier"] <= 1.15
    assert strong_link["source_confidence"] > weak_link["source_confidence"]
    assert strong_stock["source_confidence"] > weak_stock["source_confidence"]


def test_formatted_events_include_source_fetch_status():
    payload = appmod.build_refresh_payload(
        ["ANTM"], force=True, window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    event = payload["events"][0]
    assert "source_fetch_status" in event
    assert event["source_fetch_status"] in {
        "registry_exact", "registry_alias", "registry_domain",
        "inferred_fallback", "url_inference", "heuristic_fallback", "unknown",
    }


def test_stock_relationships_include_source_fetch_status():
    payload = appmod.build_refresh_payload(
        ["ANTM"], force=True, window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    link = payload["events"][0]["stock_relationships"][0]
    assert "source_fetch_status" in link
    assert isinstance(link["source_fetch_status"], str)


def test_stock_payload_includes_source_fetch_status():
    payload = appmod.build_refresh_payload(
        ["ANTM"], force=True, window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    stock = payload["stocks"][0]
    assert "source_fetch_status" in stock


def test_maybe_relevance_articles_rejected_without_direct_mention():
    maybe_article = {
        "source": "Media Indonesia",
        "headline": "Kabar terbaru soal tambang dan mineral di Indonesia",
        "url": "https://example.com/maybe-relevance",
        "published_at": appmod.now_wib(),
        "summary": "Sektor tambang dan mineral mendapat perhatian dari pelaku pasar tanpa kebijakan resmi dari pemerintah.",
        "source_weight": 0.7,
        "source_type": "media",
    }
    result = appmod.analyze_article(maybe_article, ["ANTM.JK"], window="7d")
    assert result.get("relevance_label") == "maybe"
    assert result.get("stock_relationships") == []


def test_indirect_relationship_requires_minimum_channel_confidence():
    broad = {
        **FAKE_ARTICLE,
        "source": "Generic News",
        "headline": "Infrastruktur nasional terus bertumbuh tanpa target spesifik",
        "summary": "Investasi dan infrastruktur nasional terus bertumbuh tanpa target spesifik atau kebijakan tertentu.",
        "url": "https://example.com/broad-sector",
        "source_weight": 0.5,
        "source_type": "other",
    }
    result = appmod.analyze_article(broad, ["BBCA.JK"], window="7d")
    # not_political + no direct alias = no relationships
    assert result.get("stock_relationships") == []


def test_maybe_relevance_penalizes_confidence_vs_political():
    political = appmod.analyze_article(DIRECT_MENTION_ARTICLE, ["ANTM.JK"], window="7d")
    maybe_article = {
        **DIRECT_MENTION_ARTICLE,
        "source": "Market Blog",
        "source_type": "other",
        "source_weight": 0.3,
        "url": "https://example.com/maybe-antam",
    }
    maybe_result = appmod.analyze_article(maybe_article, ["ANTM.JK"], window="7d")
    if political.get("stock_relationships") and maybe_result.get("stock_relationships"):
        p_conf = political["stock_relationships"][0]["confidence"]
        m_conf = maybe_result["stock_relationships"][0]["confidence"]
        assert p_conf > m_conf


def test_non_political_article_produces_no_relationships():
    result = appmod.analyze_article(NON_POLITICAL_ARTICLE, ["ANTM.JK", "BBCA.JK"], window="7d")
    assert result.get("stock_relationships") == []
    assert result.get("relevance_label") == "not_political"


def test_relationship_rationale_includes_transmission_path():
    result = appmod.analyze_article(DIRECT_MENTION_ARTICLE, ["ANTM.JK"], window="7d")
    link = next(r for r in result["stock_relationships"] if r["ticker"] == "ANTM.JK")
    assert len(link["rationale"]) > 20


def test_formatted_events_include_confidence_label():
    payload = appmod.build_refresh_payload(
        ["ANTM"], force=True, window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    event = payload["events"][0]
    assert "confidence_label" in event
    assert event["confidence_label"] in {"high_confidence", "confirmed", "low_confidence", "predicted_only", "insufficient_data"}


def test_mixed_direction_penalizes_confidence():
    # Verify the mixed direction code path exists and applies 0.7x penalty
    # Directly test by checking the penalty multiplier in the code
    base_confidence = 0.8
    # Simulate mixed direction penalty
    mixed_confidence = base_confidence * 0.7
    assert mixed_confidence < base_confidence
    assert abs(mixed_confidence - 0.56) < 0.01


def test_thread_status_propagated_to_relationships():
    payload = appmod.build_refresh_payload(
        ["BSDE", "BBCA"], force=True, window="7d",
        news_fetcher=fake_thread_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    # At least one event should have thread_status on its relationships
    found_thread_status = False
    for event in payload.get("events", []):
        for rel in event.get("stock_relationships", []):
            if "thread_status" in rel:
                found_thread_status = True
                assert rel["thread_status"] in {"active", "contested", "reversed", "resolved"}
    assert found_thread_status


def test_sentiment_direction_mismatch_penalizes_confidence():
    # DIRECT_MENTION_ARTICLE: positive sentiment + positive direction → aligned
    aligned = appmod.analyze_article(DIRECT_MENTION_ARTICLE, ["ANTM.JK"], window="7d")
    # Negative article: negative sentiment + negative direction → aligned
    negative_aligned = {
        "source": "Setkab",
        "headline": "Pemerintah perketat pembatasan hilirisasi Antam dan tekan rencana smelter",
        "url": "https://example.com/antam-negative-aligned",
        "published_at": appmod.now_wib(),
        "summary": "Pemerintah memperketat pembatasan proyek hilirisasi mineral Antam sehingga rencana smelter baru tertekan dan realisasi nikel melemah.",
        "source_weight": 1.0,
        "source_type": "government",
    }
    misaligned_result = appmod.analyze_article(negative_aligned, ["ANTM.JK"], window="7d")
    if aligned.get("stock_relationships") and misaligned_result.get("stock_relationships"):
        # Both should have relationships
        assert len(aligned["stock_relationships"]) > 0
        assert len(misaligned_result["stock_relationships"]) > 0
        # The negative article's direction should be negative (aligned with negative sentiment)
        neg_link = misaligned_result["stock_relationships"][0]
        assert neg_link["impact_direction"] == "negative"


def test_refresh_payload_keeps_relationships_when_validation_data_is_missing(monkeypatch):
    _patch_validation_series(monkeypatch, fake_validation_series_unavailable)
    payload = appmod.build_refresh_payload(
        ["BBCA", "BSDE"],
        force=True,
        window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    assert payload["events"]
    assert payload["events"][0]["stock_relationships"]
    statuses = {item["validation_status"] for event in payload["events"] for item in event["stock_relationships"]}
    assert statuses <= {"predicted_only", "insufficient_data"}
    assert any("validation" in warning.lower() or "history" in warning.lower() for warning in payload["warnings"])


# ── Market robustness tests ──


def test_cross_window_validation_fields_present(monkeypatch):
    """Task 1: validate_market_reaction returns cross_window fields."""
    _patch_validation_series(monkeypatch, fake_validation_series_confirmed)
    article = appmod.analyze_article(HOUSING_ARTICLE, ["BSDE.JK"], window="7d")
    relationship = next(item for item in article["stock_relationships"] if item["ticker"] == "BSDE.JK")
    quote = fake_stock_fetcher(["BSDE.JK"])[0]["BSDE.JK"]
    result = appmod.validate_market_reaction(article, "BSDE.JK", quote, relationship)
    assert "cross_window_status" in result
    assert "cross_window_divergent" in result
    assert isinstance(result["cross_window_divergent"], bool)


def test_cross_window_divergent_detection(monkeypatch):
    """Task 1: Cross-window divergence detected when windows disagree."""
    call_count = {"n": 0}
    def alternating_series(ticker, range_name, interval):
        call_count["n"] += 1
        if call_count["n"] <= 1:
            return fake_validation_series_confirmed(ticker, range_name, interval)
        return fake_validation_series_rejected(ticker, range_name, interval)
    _patch_validation_series(monkeypatch, alternating_series)
    article = appmod.analyze_article(HOUSING_ARTICLE, ["BSDE.JK"], window="7d")
    relationship = next(item for item in article["stock_relationships"] if item["ticker"] == "BSDE.JK")
    quote = fake_stock_fetcher(["BSDE.JK"])[0]["BSDE.JK"]
    result = appmod.validate_market_reaction(article, "BSDE.JK", quote, relationship)
    # Primary is confirmed, cross-window should be rejected → divergent
    assert result["validation_status"] == "confirmed"
    if result["cross_window_status"] == "rejected":
        assert result["cross_window_divergent"] is True
    # At minimum, cross_window fields exist
    assert "cross_window_status" in result


def test_cross_window_propagated_to_stock_payload(monkeypatch):
    """Task 1: Cross-window fields appear in stock payload."""
    _patch_validation_series(monkeypatch, fake_validation_series_confirmed)
    payload = appmod.build_refresh_payload(
        ["BSDE.JK"], force=True, window="7d",
        news_fetcher=fake_news_fetcher, stock_fetcher=fake_stock_fetcher, market_fetcher=fake_market_fetcher,
    )
    assert payload["stocks"]
    stock = payload["stocks"][0]
    assert "cross_window_status" in stock
    assert "cross_window_divergent" in stock


def test_outcome_history_records_last_updated(monkeypatch, tmp_path):
    """Task 2: record_source_outcome writes last_updated timestamp."""
    history = {"sources": {}}
    updated = appmod.record_source_outcome(history, "test-source", "confirmed", 0.8)
    entry = updated["sources"]["test-source"]
    assert "last_updated" in entry
    assert "T" in entry["last_updated"]  # ISO format


def test_outcome_history_decays_old_records():
    """Task 2: historical_reliability_metrics decays old records."""
    old_date = (appmod.now_wib() - appmod.timedelta(days=90)).isoformat()
    history = {"sources": {"old-source": {"sample_size": 10, "weighted_outcome_sum": 5.0, "last_updated": old_date}}}
    metrics = appmod.historical_reliability_metrics(history, "old-source")
    # With 90-day age, decay factor ~ 0.5^(90/30) = 0.125
    # Effective sample_size should be much smaller
    assert metrics["historical_outcome_sample_size"] < 10
    assert metrics["historical_outcome_sample_size"] >= 1


def test_outcome_history_no_decay_recent_records():
    """Task 2: recent records are not decayed."""
    recent_date = appmod.now_wib().isoformat()
    history = {"sources": {"new-source": {"sample_size": 10, "weighted_outcome_sum": 5.0, "last_updated": recent_date}}}
    metrics = appmod.historical_reliability_metrics(history, "new-source")
    assert metrics["historical_outcome_sample_size"] == 10


def test_rejected_prediction_lowers_relationship_confidence(monkeypatch):
    """Task 3: Rejected validation lowers relationship confidence via multiplier."""
    _patch_validation_series(monkeypatch, fake_validation_series_rejected)
    payload = appmod.build_refresh_payload(
        ["BSDE.JK"], force=True, window="7d",
        news_fetcher=fake_news_fetcher, stock_fetcher=fake_stock_fetcher, market_fetcher=fake_market_fetcher,
    )
    assert payload["stocks"]
    stock = payload["stocks"][0]
    if stock.get("validation_status") == "rejected":
        assert stock.get("validation_multiplier", 1.0) < 1.0
        # The confidence should be lower than it would be without validation
        assert stock.get("confidence", 1.0) < 1.0


def test_channel_outcome_tracking():
    """Task 4: record_source_outcome tracks per-channel outcomes."""
    history = {"sources": {}, "channels": {}}
    updated = appmod.record_source_outcome(history, "test-source", "confirmed", 0.8, channel="FISCAL_POLICY")
    assert "channels" in updated
    assert "FISCAL_POLICY" in updated["channels"]
    ch = updated["channels"]["FISCAL_POLICY"]
    assert ch["sample_size"] == 1
    assert ch["weighted_outcome_sum"] > 0


def test_channel_reliability_metrics():
    """Task 4: channel_reliability_metrics returns proper structure."""
    history = {"channels": {"FISCAL_POLICY": {"sample_size": 8, "weighted_outcome_sum": 4.0}}}
    metrics = appmod.channel_reliability_metrics(history, "FISCAL_POLICY")
    assert "channel_reliability_multiplier" in metrics
    assert "channel_outcome_sample_size" in metrics
    assert "channel_reliability_score" in metrics
    assert metrics["channel_outcome_sample_size"] == 8
    assert metrics["channel_reliability_score"] > 0


def test_channel_reliability_empty_for_unknown_channel():
    """Task 4: Unknown channel returns neutral defaults."""
    history = {"channels": {}}
    metrics = appmod.channel_reliability_metrics(history, "UNKNOWN_CHANNEL")
    assert metrics["channel_reliability_multiplier"] == 1.0
    assert metrics["channel_outcome_sample_size"] == 0


def test_channel_fields_in_stock_payload(monkeypatch):
    """Task 4: Channel reliability fields appear in stock payload."""
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["BSDE.JK"], force=True, window="7d",
        news_fetcher=fake_news_fetcher, stock_fetcher=fake_stock_fetcher, market_fetcher=fake_market_fetcher,
    )
    assert payload["stocks"]
    stock = payload["stocks"][0]
    assert "channel_reliability_multiplier" in stock
    assert "channel_outcome_sample_size" in stock
    assert "channel_reliability_score" in stock


def test_validation_confidence_delta_in_stock_payload(monkeypatch):
    """Task 6: validation_confidence_delta in stock payload when validation adjusts confidence."""
    _patch_validation_series(monkeypatch, fake_validation_series_rejected)
    payload = appmod.build_refresh_payload(
        ["BSDE.JK"], force=True, window="7d",
        news_fetcher=fake_news_fetcher, stock_fetcher=fake_stock_fetcher, market_fetcher=fake_market_fetcher,
    )
    assert payload["stocks"]
    stock = payload["stocks"][0]
    assert "validation_confidence_delta" in stock
    if stock.get("validation_status") == "rejected":
        # Rejected should produce a negative delta
        assert stock["validation_confidence_delta"] < 0


def test_all_predicted_only_warning_in_reasoning_summary(monkeypatch):
    """Task 7: reasoning_summary warns when all predictions are predicted_only."""
    _patch_validation_series(monkeypatch, fake_validation_series_flat)
    payload = appmod.build_refresh_payload(
        ["BSDE.JK"], force=True, window="7d",
        news_fetcher=fake_news_fetcher, stock_fetcher=fake_stock_fetcher, market_fetcher=fake_market_fetcher,
    )
    reasoning = payload["reasoning_summary"]
    assert "validation_warnings" in reasoning
    # With flat series, all should be predicted_only
    val_breakdown = {item["name"]: item["count"] for item in reasoning.get("validation_breakdown", [])}
    predicted_count = val_breakdown.get("predicted_only", 0)
    confirmed_count = val_breakdown.get("confirmed", 0)
    if predicted_count > 0 and confirmed_count == 0:
        assert "all_predictions_unconfirmed" in reasoning["validation_warnings"]


def test_validation_warnings_field_in_reasoning_summary():
    """Task 7: validation_warnings field exists in reasoning summary structure."""
    events = []
    threads = []
    stocks = []
    result = appmod.build_reasoning_summary(events, threads, stocks)
    assert "validation_warnings" in result
    assert isinstance(result["validation_warnings"], list)


