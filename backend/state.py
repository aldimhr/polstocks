"""Mutable global state: locks, caches, and runtime data."""

from __future__ import annotations

import threading
from typing import Any

from backend.config import DEFAULT_WATCHLIST

WATCHLIST_LOCK = threading.Lock()
CACHE_LOCK = threading.Lock()
WATCHLIST_STATE: list[str] = list(DEFAULT_WATCHLIST)
CACHE: dict[str, Any] = {}
COMPANY_KNOWLEDGE: dict[str, dict[str, Any]] = {}
POLICY_SIGNAL_RULES: dict[str, Any] = {}
MARKET_VALIDATION_CONFIG: dict[str, Any] = {}
SOURCE_REGISTRY: dict[str, Any] = {}
