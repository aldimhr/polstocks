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
}


def fake_news_fetcher():
    return [FAKE_ARTICLE], []


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


def test_root_serves_dashboard_html():
    response = client.get("/")
    assert response.status_code == 200
    dashboard_html = (appmod.PROJECT_ROOT / "dashboard.html").read_text(encoding="utf-8")
    assert response.text == dashboard_html


def test_dashboard_contains_runtime_hooks():
    dashboard_html = (appmod.PROJECT_ROOT / "dashboard.html").read_text(encoding="utf-8")
    for snippet in [
        'id="m-highest"',
        'id="m-highest-sub"',
        'id="stockBody"',
        'id="watchlistInput"',
        'id="watchlistChips"',
        'id="eventBadge"',
        'id="ihsgValue"',
        'id="ihsgChange"',
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


def test_dashboard_endpoint_returns_watchlist_and_payload(monkeypatch):
    monkeypatch.setattr(appmod, "fetch_news_bundle", fake_news_fetcher)
    monkeypatch.setattr(appmod, "fetch_stock_quotes", fake_stock_fetcher)
    monkeypatch.setattr(appmod, "fetch_market_index", fake_market_fetcher)
    response = client.get("/api/dashboard")
    assert response.status_code == 200
    data = response.json()
    assert set(["watchlist", "payload"]).issubset(data)
    assert data["watchlist"] == appmod.get_watchlist()
    assert data["payload"]["watchlist"] == appmod.get_watchlist()
    assert data["payload"]["events"]
    assert data["payload"]["stocks"]
    assert data["payload"]["market_index"]["value"] == 6847
    assert data["payload"]["market_index"]["series"]
    assert data["payload"]["sources"]


def test_refresh_builds_expected_payload_and_uses_cache():
    payload = appmod.build_refresh_payload(
        ["BBCA", "TLKM"],
        force=True,
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    assert payload["from_cache"] is False
    assert payload["events"]
    assert payload["stocks"]
    assert "Financials" in payload["sector_summary"]
    assert payload["stocks"][0]["ticker"] in {"BBCA.JK", "TLKM.JK"}

    cached = appmod.build_refresh_payload(
        ["BBCA", "TLKM"],
        force=False,
        news_fetcher=lambda: (_ for _ in ()).throw(AssertionError("news should not be fetched")),
        stock_fetcher=lambda tickers: (_ for _ in ()).throw(AssertionError("stocks should not be fetched")),
        market_fetcher=lambda: (_ for _ in ()).throw(AssertionError("market should not be fetched")),
    )
    assert cached["from_cache"] is True
    assert cached["cache_key"] == ["BBCA.JK", "TLKM.JK"]


def test_refresh_endpoint_returns_json_shape(monkeypatch):
    monkeypatch.setattr(appmod, "fetch_news_bundle", fake_news_fetcher)
    monkeypatch.setattr(appmod, "fetch_stock_quotes", fake_stock_fetcher)
    response = client.post("/api/refresh", json={"tickers": ["BBCA", "TLKM"], "force": True})
    assert response.status_code == 200
    data = response.json()
    assert set(["fetched_at", "from_cache", "events", "stocks", "sector_summary", "warnings", "watchlist"]).issubset(data)
    assert isinstance(data["events"], list)
    assert isinstance(data["stocks"], list)
    assert len(data["sector_summary"]) == len(appmod.SECTORS)


def test_empty_watchlist_request_resets_default():
    updated = client.put("/api/watchlist", json={"tickers": []})
    assert updated.status_code == 200
    assert updated.json()["tickers"] == appmod.DEFAULT_WATCHLIST
