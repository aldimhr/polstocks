"""Tests for C4 (API key protection) and C5 (refresh rate limiting).

C4: Write endpoints require X-API-Key when the API_KEY env var is set.
C5: POST /api/refresh returns 429 when the per-IP limit is exceeded.
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import main as appmod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client() -> TestClient:
    """Create a fresh TestClient wired to the real app."""
    return TestClient(appmod.app)


def _set_api_key(key: str | None):
    """Monkeypatch the module-level _API_KEY."""
    appmod._API_KEY = key or ""


def _clear_refresh_rate_store():
    """Reset the per-IP refresh rate limiter store."""
    with appmod._refresh_rate_lock:
        appmod._refresh_rate_store.clear()


# ---------------------------------------------------------------------------
# C4 – API key protection
# ---------------------------------------------------------------------------

class TestAPIKeyProtection:
    """Write endpoints return 401 when API_KEY is set and missing from request."""

    def setup_method(self):
        self.client = _make_client()
        _set_api_key("")

    def teardown_method(self):
        _set_api_key("")

    # -- PUT /api/watchlist ------------------------------------------

    def test_put_watchlist_401_when_key_required(self):
        _set_api_key("secret-key")
        resp = self.client.put("/api/watchlist", json={"tickers": ["BBCA.JK"]})
        assert resp.status_code == 401
        assert "API key" in resp.json()["detail"]

    def test_put_watchlist_ok_with_correct_key(self):
        _set_api_key("secret-key")
        resp = self.client.put(
            "/api/watchlist",
            json={"tickers": ["BBCA.JK"]},
            headers={"X-API-Key": "secret-key"},
        )
        assert resp.status_code == 200
        assert "BBCA.JK" in resp.json()["tickers"]

    def test_put_watchlist_ok_with_bearer_auth(self):
        _set_api_key("secret-key")
        resp = self.client.put(
            "/api/watchlist",
            json={"tickers": ["BBCA.JK"]},
            headers={"Authorization": "Bearer secret-key"},
        )
        assert resp.status_code == 200

    def test_put_watchlist_ok_when_no_key_configured(self):
        """Backward compat: no API_KEY set → no auth required."""
        _set_api_key("")
        resp = self.client.put("/api/watchlist", json={"tickers": ["BBCA.JK"]})
        assert resp.status_code == 200

    # -- POST /api/watchlist/pin/{ticker} ----------------------------

    def test_pin_ticker_401_when_key_required(self):
        _set_api_key("secret-key")
        resp = self.client.post("/api/watchlist/pin/BBCA.JK")
        assert resp.status_code == 401

    def test_pin_ticker_ok_with_correct_key(self):
        _set_api_key("secret-key")
        resp = self.client.post(
            "/api/watchlist/pin/BBCA.JK",
            headers={"X-API-Key": "secret-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    # -- DELETE /api/watchlist/pin/{ticker} --------------------------

    def test_unpin_ticker_401_when_key_required(self):
        _set_api_key("secret-key")
        resp = self.client.delete("/api/watchlist/pin/BBCA.JK")
        assert resp.status_code == 401

    def test_unpin_ticker_ok_with_correct_key(self):
        _set_api_key("secret-key")
        resp = self.client.delete(
            "/api/watchlist/pin/BBCA.JK",
            headers={"X-API-Key": "secret-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    # -- POST /api/portfolio/reset -----------------------------------

    def test_portfolio_reset_401_when_key_required(self):
        _set_api_key("secret-key")
        resp = self.client.post("/api/portfolio/reset")
        assert resp.status_code == 401

    def test_portfolio_reset_ok_with_correct_key(self):
        _set_api_key("secret-key")
        resp = self.client.post(
            "/api/portfolio/reset",
            headers={"X-API-Key": "secret-key"},
        )
        assert resp.status_code == 200

    # -- POST /api/portfolio/position --------------------------------

    def test_portfolio_add_401_when_key_required(self):
        _set_api_key("secret-key")
        resp = self.client.post(
            "/api/portfolio/position",
            json={"ticker": "BBCA.JK", "entry_price": 9000},
        )
        assert resp.status_code == 401

    def test_portfolio_add_ok_with_correct_key(self):
        _set_api_key("secret-key")
        resp = self.client.post(
            "/api/portfolio/position",
            json={"ticker": "BBCA.JK", "entry_price": 9000, "shares": 100},
            headers={"X-API-Key": "secret-key"},
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    # -- PUT /api/portfolio/position/{id} ----------------------------

    def test_portfolio_close_401_when_key_required(self):
        _set_api_key("secret-key")
        resp = self.client.put(
            "/api/portfolio/position/1",
            json={"exit_price": 9500},
        )
        assert resp.status_code == 401

    # -- POST /api/refresh -------------------------------------------

    def test_refresh_401_when_key_required(self):
        _set_api_key("secret-key")
        resp = self.client.post("/api/refresh", json={})
        assert resp.status_code == 401

    def test_refresh_ok_with_correct_key(self):
        """Refresh succeeds with valid key (hits rate limit dep too but under limit)."""
        _set_api_key("secret-key")
        _clear_refresh_rate_store()
        orig = appmod.build_refresh_payload
        appmod.build_refresh_payload = _mock_build_refresh_payload
        resp = self.client.post(
            "/api/refresh",
            json={},
            headers={"X-API-Key": "secret-key"},
        )
        appmod.build_refresh_payload = orig
        assert resp.status_code == 200

    # -- GET endpoints are unaffected --------------------------------

    def test_get_watchlist_not_protected(self):
        _set_api_key("secret-key")
        resp = self.client.get("/api/watchlist")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# C5 – Refresh rate limiting
# ---------------------------------------------------------------------------

class TestRefreshRateLimit:
    """POST /api/refresh returns 429 when the per-IP limit is exceeded."""

    def setup_method(self):
        self.client = _make_client()
        _set_api_key("")
        _clear_refresh_rate_store()
        self._orig_build = appmod.build_refresh_payload
        appmod.build_refresh_payload = _mock_build_refresh_payload

    def teardown_method(self):
        _set_api_key("")
        _clear_refresh_rate_store()
        appmod.build_refresh_payload = self._orig_build

    def test_refresh_rate_limit_returns_429(self):
        """Exceed the per-IP limit and assert 429."""
        max_requests = appmod._REFRESH_RATE_MAX
        # Exhaust the limit
        for _ in range(max_requests):
            resp = self.client.post("/api/refresh", json={})
            assert resp.status_code == 200

        # Next one should be rate-limited
        resp = self.client.post("/api/refresh", json={})
        assert resp.status_code == 429
        assert "rate limit" in resp.json()["detail"].lower()
        assert "Retry-After" in resp.headers

    def test_refresh_rate_limit_window_resets(self):
        """After the window expires, the client is allowed again."""
        max_requests = appmod._REFRESH_RATE_MAX

        # Exhaust the limit
        for _ in range(max_requests):
            self.client.post("/api/refresh", json={})

        # Simulate window expiry by manipulating the store
        with appmod._refresh_rate_lock:
            appmod._refresh_rate_store.clear()

        resp = self.client.post("/api/refresh", json={})
        assert resp.status_code == 200
def _mock_build_refresh_payload(*args, **kwargs):
    """Fast stub so /api/refresh doesn't hit the network."""
    return {"events": [], "stocks": [], "timestamp": "2026-01-01T00:00:00+07:00"}
