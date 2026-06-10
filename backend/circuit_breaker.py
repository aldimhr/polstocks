"""Per-source circuit breaker to stop hammering consistently failing sources.

States:
  closed   – normal operation; every request goes through.
  open     – breaker tripped after N consecutive failures; requests are blocked.
  half_open – after a cooldown window a single probe request is allowed through.
              Success closes the breaker; failure re-opens it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


# ── Tunables ──────────────────────────────────────────────────────────────────
FAILURE_THRESHOLD = 5          # consecutive failures to open
COOLDOWN_SECONDS = 15 * 60    # 15 minutes before half-open probe


# ── State bookkeeping ────────────────────────────────────────────────────────
@dataclass
class _BreakerState:
    state: str = "closed"           # closed | open | half_open
    consecutive_failures: int = 0
    last_failure_ts: float = 0.0
    last_success_ts: float = 0.0
    opened_at: float = 0.0          # when breaker tripped
    total_failures: int = 0
    total_successes: int = 0
    last_error: str = ""


class SourceCircuitBreaker:
    """Thread-safe, in-memory circuit breaker keyed by source name."""

    def __init__(
        self,
        failure_threshold: int = FAILURE_THRESHOLD,
        cooldown_seconds: float = COOLDOWN_SECONDS,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._states: dict[str, _BreakerState] = {}
        self._lock = threading.Lock()

    # ── public query ──────────────────────────────────────────────────────
    def is_open(self, source_name: str) -> bool:
        """Return True if the source's breaker is open (should skip)."""
        with self._lock:
            st = self._states.get(source_name)
            if st is None:
                return False
            if st.state == "closed":
                return False
            if st.state == "half_open":
                return False  # allow probe
            # state == "open"
            elapsed = time.monotonic() - st.opened_at
            if elapsed >= self._cooldown_seconds:
                st.state = "half_open"
                return False  # allow probe
            return True

    def should_allow(self, source_name: str) -> bool:
        """Return True if a fetch attempt should proceed."""
        return not self.is_open(source_name)

    # ── recording outcomes ────────────────────────────────────────────────
    def record_success(self, source_name: str) -> None:
        with self._lock:
            st = self._get_or_create(source_name)
            st.consecutive_failures = 0
            st.last_success_ts = time.monotonic()
            st.total_successes += 1
            st.state = "closed"

    def record_failure(self, source_name: str, error: str = "") -> None:
        with self._lock:
            st = self._get_or_create(source_name)
            st.consecutive_failures += 1
            st.last_failure_ts = time.monotonic()
            st.total_failures += 1
            st.last_error = error
            if st.consecutive_failures >= self._failure_threshold:
                st.state = "open"
                st.opened_at = time.monotonic()

    # ── diagnostics ───────────────────────────────────────────────────────
    def get_state(self, source_name: str) -> dict[str, Any]:
        """Return a serialisable snapshot of a source's breaker state."""
        with self._lock:
            st = self._states.get(source_name)
            if st is None:
                return {
                    "source": source_name,
                    "state": "closed",
                    "consecutive_failures": 0,
                    "total_failures": 0,
                    "total_successes": 0,
                    "last_error": "",
                    "cooldown_remaining_seconds": 0,
                }
            # Check if open breaker should transition to half_open
            cooldown_remaining = 0.0
            if st.state == "open":
                elapsed = time.monotonic() - st.opened_at
                remaining = self._cooldown_seconds - elapsed
                if remaining <= 0:
                    st.state = "half_open"
                    cooldown_remaining = 0.0
                else:
                    cooldown_remaining = round(remaining, 1)
            return {
                "source": source_name,
                "state": st.state,
                "consecutive_failures": st.consecutive_failures,
                "total_failures": st.total_failures,
                "total_successes": st.total_successes,
                "last_error": st.last_error,
                "cooldown_remaining_seconds": cooldown_remaining,
            }

    def get_all_states(self) -> dict[str, dict[str, Any]]:
        """Return breaker state for every tracked source."""
        with self._lock:
            names = list(self._states.keys())
        return {name: self.get_state(name) for name in names}

    # ── testing helpers ───────────────────────────────────────────────────
    def _force_state(self, source_name: str, *, state: str, consecutive_failures: int = 0) -> None:
        """Directly set breaker state (for tests only)."""
        with self._lock:
            st = self._get_or_create(source_name)
            st.state = state
            st.consecutive_failures = consecutive_failures
            if state == "open":
                st.opened_at = time.monotonic()

    def _set_cooldown(self, seconds: float) -> None:
        """Override cooldown duration (for tests only)."""
        self._cooldown_seconds = seconds

    # ── internal ──────────────────────────────────────────────────────────
    def _get_or_create(self, source_name: str) -> _BreakerState:
        st = self._states.get(source_name)
        if st is None:
            st = _BreakerState()
            self._states[source_name] = st
        return st


# ── Module-level singleton ────────────────────────────────────────────────────
source_breaker = SourceCircuitBreaker()
