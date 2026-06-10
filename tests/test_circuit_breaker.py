"""Focused tests for the source circuit breaker (C2) and /api/source-health endpoint (C3)."""

from unittest.mock import patch
import time

from backend.circuit_breaker import SourceCircuitBreaker, source_breaker


# ── Circuit breaker unit tests ────────────────────────────────────────────────

class TestCircuitBreaker:
    """Tests for the SourceCircuitBreaker class."""

    def test_new_source_is_closed(self):
        """A source with no history should be closed (allow requests)."""
        breaker = SourceCircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        assert breaker.should_allow("test-source") is True
        assert breaker.is_open("test-source") is False

    def test_consecutive_failures_open_breaker(self):
        """After failure_threshold consecutive failures, the breaker opens."""
        breaker = SourceCircuitBreaker(failure_threshold=5, cooldown_seconds=900)
        for i in range(4):
            breaker.record_failure("flaky-source", error=f"fail {i}")
            assert breaker.should_allow("flaky-source"), f"should still allow after {i+1} failures"

        # 5th failure opens the breaker
        breaker.record_failure("flaky-source", error="fail 4")
        assert breaker.should_allow("flaky-source") is False
        assert breaker.is_open("flaky-source") is True

        state = breaker.get_state("flaky-source")
        assert state["state"] == "open"
        assert state["consecutive_failures"] == 5
        assert state["total_failures"] == 5

    def test_success_resets_failure_counter(self):
        """A success between failures resets the consecutive count."""
        breaker = SourceCircuitBreaker(failure_threshold=3, cooldown_seconds=60)
        breaker.record_failure("src", error="e1")
        breaker.record_failure("src", error="e2")
        breaker.record_success("src")  # resets counter

        assert breaker.should_allow("src") is True
        state = breaker.get_state("src")
        assert state["consecutive_failures"] == 0
        assert state["state"] == "closed"

    def test_open_breaker_blocks_requests(self):
        """An open breaker should block requests until cooldown expires."""
        breaker = SourceCircuitBreaker(failure_threshold=2, cooldown_seconds=9999)
        breaker.record_failure("src", error="e1")
        breaker.record_failure("src", error="e2")
        assert breaker.should_allow("src") is False
        assert breaker.is_open("src") is True

    def test_cooldown_allows_probe(self):
        """After cooldown, breaker transitions to half_open and allows a probe."""
        breaker = SourceCircuitBreaker(failure_threshold=2, cooldown_seconds=0.1)
        breaker.record_failure("src", error="e1")
        breaker.record_failure("src", error="e2")
        assert breaker.should_allow("src") is False

        # Wait for cooldown to expire
        time.sleep(0.15)

        # Should now be half-open and allow a probe
        assert breaker.should_allow("src") is True
        state = breaker.get_state("src")
        assert state["state"] == "half_open"

    def test_half_open_success_closes_breaker(self):
        """A success during half_open closes the breaker."""
        breaker = SourceCircuitBreaker(failure_threshold=2, cooldown_seconds=0.1)
        breaker._force_state("src", state="half_open")

        breaker.record_success("src")
        state = breaker.get_state("src")
        assert state["state"] == "closed"
        assert state["consecutive_failures"] == 0
        assert breaker.should_allow("src") is True

    def test_half_open_failure_reopens_breaker(self):
        """A failure during half_open reopens the breaker."""
        breaker = SourceCircuitBreaker(failure_threshold=2, cooldown_seconds=9999)
        breaker._force_state("src", state="half_open", consecutive_failures=2)

        breaker.record_failure("src", error="probe failed")
        state = breaker.get_state("src")
        assert state["state"] == "open"
        assert state["consecutive_failures"] == 3

    def test_get_state_untracked_source(self):
        """get_state for an untracked source returns clean defaults."""
        breaker = SourceCircuitBreaker()
        state = breaker.get_state("unknown-source")
        assert state["state"] == "closed"
        assert state["consecutive_failures"] == 0
        assert state["total_failures"] == 0
        assert state["total_successes"] == 0
        assert state["last_error"] == ""
        assert state["cooldown_remaining_seconds"] == 0

    def test_get_all_states(self):
        """get_all_states returns entries for all tracked sources."""
        breaker = SourceCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        breaker.record_failure("src-a", error="err")
        breaker.record_success("src-b")

        all_states = breaker.get_all_states()
        assert "src-a" in all_states
        assert "src-b" in all_states
        assert len(all_states) == 2

    def test_cooldown_remaining_is_positive_while_open(self):
        """While open and cooldown hasn't expired, remaining > 0."""
        breaker = SourceCircuitBreaker(failure_threshold=1, cooldown_seconds=60)
        breaker.record_failure("src", error="e")
        state = breaker.get_state("src")
        assert state["state"] == "open"
        assert state["cooldown_remaining_seconds"] > 0

    def test_last_error_is_recorded(self):
        """The last error message is captured in breaker state."""
        breaker = SourceCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        breaker.record_failure("src", error="connection timeout")
        breaker.record_failure("src", error="HTTP 503")
        state = breaker.get_state("src")
        assert state["last_error"] == "HTTP 503"

    def test_multiple_sources_independent(self):
        """Breaker states for different sources are independent."""
        breaker = SourceCircuitBreaker(failure_threshold=2, cooldown_seconds=60)
        breaker.record_failure("src-a", error="e1")
        breaker.record_failure("src-a", error="e2")
        assert breaker.should_allow("src-a") is False
        assert breaker.should_allow("src-b") is True


# ── fetch_news_bundle integration tests ───────────────────────────────────────

class TestFetchNewsBundleBreakerIntegration:
    """Test that fetch_news_bundle respects the circuit breaker."""

    def test_breaker_skips_open_source(self, monkeypatch):
        """A source with an open breaker is skipped with a diagnostic."""
        from backend import sources as sourcesmod

        # Create a fresh breaker and force one source open
        test_breaker = SourceCircuitBreaker(failure_threshold=5, cooldown_seconds=900)
        test_breaker._force_state("Antara Terkini", state="open", consecutive_failures=5)

        monkeypatch.setattr(sourcesmod, "source_breaker", test_breaker, raising=False)
        # Need to also patch it where it's used (inside the function via import)
        import backend.circuit_breaker as cb_mod
        monkeypatch.setattr(cb_mod, "source_breaker", test_breaker)

        # Stub fetch_source to succeed for non-blocked sources
        def fake_fetch_source(source, include_diagnostic=False):
            from backend.sources import build_source_diagnostic
            if include_diagnostic:
                return [], None, build_source_diagnostic(source, status="ok", articles=[])
            return [], None

        monkeypatch.setattr(sourcesmod, "fetch_source", fake_fetch_source)

        articles, warnings, diagnostics = sourcesmod.fetch_news_bundle()

        # The open source should appear in diagnostics with "skipped" status
        skipped = [d for d in diagnostics if d.get("status") == "skipped"]
        assert len(skipped) >= 1
        # Name resolves to canonical via source registry; just verify it's there
        assert any(d.get("warning", "").startswith("circuit_breaker_open") for d in skipped)

        # A warning about the breaker should be present
        assert any("circuit breaker open" in w.lower() for w in warnings)

    def test_successful_source_records_success(self, monkeypatch):
        """A successful fetch records a success in the breaker."""
        from backend import sources as sourcesmod
        import backend.circuit_breaker as cb_mod

        test_breaker = SourceCircuitBreaker(failure_threshold=5, cooldown_seconds=900)
        monkeypatch.setattr(cb_mod, "source_breaker", test_breaker)

        # Stub fetch_source to return success for all sources
        def fake_fetch_source(source, include_diagnostic=False):
            from backend.sources import build_source_diagnostic
            articles = [{"source": source["name"], "headline": "test", "summary": "politik test article",
                         "url": "http://example.com", "source_weight": 1.0}]
            if include_diagnostic:
                return articles, None, build_source_diagnostic(source, status="ok", articles=articles)
            return articles, None

        monkeypatch.setattr(sourcesmod, "fetch_source", fake_fetch_source)
        # Stub is_relevant_article to always pass
        monkeypatch.setattr(sourcesmod, "is_relevant_article", lambda a: True)

        sourcesmod.fetch_news_bundle()

        # All sources should have been recorded as successes
        all_states = test_breaker.get_all_states()
        for name, state in all_states.items():
            assert state["total_successes"] >= 1, f"{name} should have recorded success"

    def test_network_error_records_failure(self, monkeypatch):
        """A fetch with a network error records a failure in the breaker."""
        from backend import sources as sourcesmod
        import backend.circuit_breaker as cb_mod

        test_breaker = SourceCircuitBreaker(failure_threshold=5, cooldown_seconds=900)
        monkeypatch.setattr(cb_mod, "source_breaker", test_breaker)

        # Stub fetch_source to return a network error for all sources
        def fake_fetch_source(source, include_diagnostic=False):
            from backend.sources import build_source_diagnostic
            warning = f"{source['name']}: ConnectionTimeout error"
            if include_diagnostic:
                return [], warning, build_source_diagnostic(source, status="error", warning=warning)
            return [], warning

        monkeypatch.setattr(sourcesmod, "fetch_source", fake_fetch_source)

        sourcesmod.fetch_news_bundle()

        # All sources should have recorded failures
        all_states = test_breaker.get_all_states()
        for name, state in all_states.items():
            assert state["total_failures"] >= 1, f"{name} should have recorded failure"

    def test_backward_compatible_with_success(self, monkeypatch):
        """Successful sources work exactly as before (backward compat)."""
        from backend import sources as sourcesmod
        import backend.circuit_breaker as cb_mod

        test_breaker = SourceCircuitBreaker(failure_threshold=5, cooldown_seconds=900)
        monkeypatch.setattr(cb_mod, "source_breaker", test_breaker)

        fake_articles = [
            {"source": "Test Source", "headline": "Politik headline", "summary": "pemerintah policy test",
             "url": "http://example.com/1", "source_weight": 0.9, "published_at": __import__("datetime").datetime.now()},
        ]

        def fake_fetch_source(source, include_diagnostic=False):
            from backend.sources import build_source_diagnostic
            if include_diagnostic:
                return fake_articles, None, build_source_diagnostic(source, status="ok", articles=fake_articles)
            return fake_articles, None

        monkeypatch.setattr(sourcesmod, "fetch_source", fake_fetch_source)
        monkeypatch.setattr(sourcesmod, "is_relevant_article", lambda a: True)

        articles, warnings, diagnostics = sourcesmod.fetch_news_bundle()

        # Should return articles, warnings, diagnostics as before
        assert isinstance(articles, list)
        assert isinstance(warnings, list)
        assert isinstance(diagnostics, list)
        assert len(articles) > 0


# ── /api/source-health endpoint tests ─────────────────────────────────────────

class TestSourceHealthEndpoint:
    """Test the GET /api/source-health API endpoint."""

    def test_source_health_endpoint_returns_200(self):
        """The endpoint returns 200 with expected structure."""
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)
        resp = client.get("/api/source-health")
        assert resp.status_code == 200
        data = resp.json()
        assert "sources" in data
        assert "summary" in data
        assert "tracked_source_count" in data["summary"]
        assert "open_breaker_count" in data["summary"]
        assert "half_open_breaker_count" in data["summary"]
        assert "closed_breaker_count" in data["summary"]

    def test_source_health_endpoint_reflects_breaker_state(self):
        """After recording failures, the endpoint shows open breaker."""
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)

        # Record failures to open a breaker
        source_breaker._force_state("Test Health Source", state="open", consecutive_failures=5)

        resp = client.get("/api/source-health")
        data = resp.json()

        assert data["summary"]["open_breaker_count"] >= 1
        assert "Test Health Source" in data["sources"]
        assert data["sources"]["Test Health Source"]["state"] == "open"

    def test_source_health_endpoint_tracks_all_states(self):
        """Endpoint reports correct counts for mixed breaker states."""
        from fastapi.testclient import TestClient
        from backend.main import app

        client = TestClient(app)

        # Reset any prior state
        source_breaker._force_state("open-src", state="open", consecutive_failures=5)
        source_breaker._force_state("half-src", state="half_open", consecutive_failures=5)
        source_breaker._force_state("closed-src", state="closed", consecutive_failures=0)

        resp = client.get("/api/source-health")
        data = resp.json()

        assert data["sources"]["open-src"]["state"] == "open"
        assert data["sources"]["half-src"]["state"] == "half_open"
        assert data["sources"]["closed-src"]["state"] == "closed"
