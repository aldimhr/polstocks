from datetime import timedelta
from fastapi.testclient import TestClient
import json

from backend import main as appmod

client = TestClient(appmod.app)


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
    monkeypatch.setattr(appmod, "fetch_ticker_history", fake_ticker_history)
    response = client.get("/api/ticker/BBCA.JK?window=7d")
    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "BBCA.JK"
    assert data["window"] == "7d"
    assert data["window_label"] == "7 hari terakhir"
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

    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_validation_series_flat)
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
        'data-label="Harga (IDR)"',
        'data-label="Hari ini"',
        'data-label="Impact score"',
        'openTickerModal(',
        "$('stockBody').addEventListener('click'",
        '@media (max-width: 720px)',
        'id="ihsgValue"',
        'id="ihsgChange"',
        'safeArticleUrl(',
        'event-link',
        'renderProvenanceBadges(',
        'provenance-chips',
        'Official',
        'Fresh',
        'Sparse sources',
        'Duplicated coverage',
        'High confidence',
        'Needs more evidence',
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
    monkeypatch.setattr(appmod, "fetch_news_bundle", fake_news_fetcher)
    monkeypatch.setattr(appmod, "fetch_stock_quotes", fake_stock_fetcher)
    monkeypatch.setattr(appmod, "fetch_market_index", fake_market_fetcher)
    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_validation_series_flat)
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


def test_refresh_builds_expected_payload_and_uses_cache(monkeypatch):
    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_validation_series_flat)
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


def test_refresh_window_changes_article_set_and_tracking(monkeypatch):
    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_validation_series_flat)
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
    monkeypatch.setattr(appmod, "fetch_news_bundle", fake_news_fetcher)
    monkeypatch.setattr(appmod, "fetch_stock_quotes", fake_stock_fetcher)
    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_validation_series_flat)
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


def test_source_quality_downgrades_relationship_confidence_and_labels_weak_coverage():
    strong = appmod.analyze_article(DIRECT_MENTION_ARTICLE, ["ANTM.JK"], window="7d")
    strong_link = next(item for item in strong["stock_relationships"] if item["ticker"] == "ANTM.JK")
    assert strong_link["relationship_type"] == "direct"
    assert strong_link["relationship_confidence"] > 0
    assert strong_link["confidence_label"] in {"high_confidence", "confirmed"}

    weak_direct_article = {
        "source": "Opinion Blog",
        "headline": "Antam dan hilirisasi jadi tema ramai di pasar",
        "url": "https://example.com/article-14",
        "published_at": appmod.now_wib(),
        "summary": "Opini umum menyebut Antam tanpa dasar kebijakan, regulasi, atau sumber resmi yang jelas.",
        "source_weight": 0.2,
        "source_type": "other",
    }
    weak = appmod.analyze_article(weak_direct_article, ["ANTM.JK"], window="7d")
    weak_link = next(item for item in weak["stock_relationships"] if item["ticker"] == "ANTM.JK")
    assert weak_link["relationship_type"] == "direct"
    assert weak_link["relationship_confidence"] < strong_link["relationship_confidence"]
    assert weak_link["confidence_label"] in {"low_confidence", "predicted_only", "insufficient_data"}
    assert weak_link["source_confidence"] <= strong_link["source_confidence"]
    assert weak_link["evidence_strength"] <= strong_link["evidence_strength"]


def test_transmission_path_scoring_blocks_sector_only_spillover_and_supports_directionality():
    trade_only_article = {
        "source": "Setkab",
        "headline": "Pemerintah perketat pembatasan ekspor batu bara",
        "url": "https://example.com/article-14",
        "published_at": appmod.now_wib(),
        "summary": "Kabinet memperketat pembatasan ekspor batu bara untuk menjaga pasokan domestik.",
        "source_weight": 1.0,
        "source_type": "government",
    }
    trade_only = appmod.analyze_article(trade_only_article, ["ADRO.JK", "BBCA.JK"], window="7d")
    trade_links = {item["ticker"]: item for item in trade_only["stock_relationships"]}
    assert set(trade_links) == {"ADRO.JK"}
    assert trade_links["ADRO.JK"]["impact_direction"] == "negative"
    assert trade_links["ADRO.JK"]["matched_policy_channels"]

    mixed = appmod.analyze_article(MIXED_DIRECTION_ARTICLE, ["ADRO.JK", "BBCA.JK"], window="7d")
    mixed_links = {item["ticker"]: item for item in mixed["stock_relationships"]}
    assert mixed_links["BBCA.JK"]["impact_direction"] == "positive"
    assert mixed_links["ADRO.JK"]["impact_direction"] == "negative"
    assert mixed_links["BBCA.JK"]["direction_rationale"]
    assert mixed_links["ADRO.JK"]["direction_rationale"]


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
    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_validation_series_flat)
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
    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_validation_series_confirmed)
    article = appmod.analyze_article(HOUSING_ARTICLE, ["BSDE.JK", "BBCA.JK"], window="7d")
    relationship = next(item for item in article["stock_relationships"] if item["ticker"] == "BSDE.JK")
    quote = fake_stock_fetcher(["BSDE.JK"])[0]["BSDE.JK"]

    validation = appmod.validate_market_reaction(article, "BSDE.JK", quote, relationship)

    assert validation["validation_status"] == "confirmed"
    assert validation["validation_score"] > 0.6
    assert validation["abnormal_return"] > 0
    assert validation["abnormal_volume_ratio"] >= 1.5


def test_validate_market_reaction_marks_predicted_only_when_series_is_flat(monkeypatch):
    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_validation_series_flat)
    article = appmod.analyze_article(HOUSING_ARTICLE, ["BSDE.JK", "BBCA.JK"], window="7d")
    relationship = next(item for item in article["stock_relationships"] if item["ticker"] == "BSDE.JK")
    quote = fake_stock_fetcher(["BSDE.JK"])[0]["BSDE.JK"]

    validation = appmod.validate_market_reaction(article, "BSDE.JK", quote, relationship)

    assert validation["validation_status"] in {"predicted_only", "rejected"}
    assert validation["validation_score"] < 0.6


def test_refresh_payload_keeps_relationships_when_validation_data_is_missing(monkeypatch):
    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_validation_series_unavailable)
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
