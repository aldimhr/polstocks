"""Market validation, source conflicts, and historical reliability."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from backend.config import (
    WIB,
    SOURCE_TIMEOUT_SECONDS,
    REQUEST_HEADERS,
    SOURCE_OUTCOME_HISTORY_FILE, SOURCE_REGISTRY_FILE,
    DEFAULT_EVENT_WINDOW, EVENT_WINDOWS,
)
from backend.state import MARKET_VALIDATION_CONFIG
from backend.scoring import relationship_confidence_label
from backend.sources import (
    canonical_source_key, canonicalize_article_url,
    normalize_domain, company_knowledge_for_ticker,
    corroboration_coverage_items, load_market_validation_config,
)
from backend.utils import (
    now_wib, now_iso, normalize_ticker, strip_tags, safe_text,
    parse_datetime, extract_html_published_at, clamp, normalize_match_text,
    collect_phrase_hits, normalize_event_window, event_window_config,
    event_window_delta, event_window_label, text_similarity,
    is_stale_article, within_trading_hours, sector_for_ticker,
    company_name_for_ticker, article_text, normalize_ticker,
)

def article_source_domain(article: dict[str, Any]) -> str:
    profile = article.get("source_profile", {}) if isinstance(article.get("source_profile", {}), dict) else {}
    candidates = [
        str(article.get("canonical_domain") or "").strip(),
        str(profile.get("canonical_domain") or "").strip(),
        canonicalize_article_url(str(article.get("url") or "")),
    ]
    for candidate in candidates:
        normalized = normalize_domain(candidate)
        if normalized:
            return normalized
    return ""


def corroboration_group_key(article: dict[str, Any], relationship: dict[str, Any]) -> tuple[str, str, str]:
    ticker = normalize_ticker(str(relationship.get("ticker") or ""))
    direction = str(relationship.get("impact_direction") or "neutral").strip().lower() or "neutral"
    policy_channel = str(relationship.get("policy_channel") or "").strip().lower() or "__any__"
    return ticker, direction, policy_channel


def corroboration_multiplier_for_group(supports: list[dict[str, Any]]) -> tuple[float, int, int, int, int]:
    coverage_items = [coverage for item in supports for coverage in item.get("coverage_items", [])]
    raw_coverage_count = max(1, len(coverage_items))
    unique_family_records: dict[str, dict[str, Any]] = {}
    for coverage in coverage_items:
        family_key = str(coverage.get("family_key") or "").strip()
        if not family_key:
            continue
        existing = unique_family_records.get(family_key)
        if existing is None or float(coverage.get("source_quality_score", 0.0) or 0.0) > float(existing.get("source_quality_score", 0.0) or 0.0):
            unique_family_records[family_key] = coverage

    independent_source_count = max(1, len(unique_family_records))
    independent_domain_count = max(1, len({str(item.get("domain_key") or "").strip() for item in unique_family_records.values() if str(item.get("domain_key") or "").strip()}))
    syndicated_coverage_count = max(0, raw_coverage_count - independent_source_count)
    official_count = sum(1 for item in unique_family_records.values() if int(item.get("source_tier", 4) or 4) <= 1)
    avg_quality = (
        sum(float(item.get("source_quality_score", 0.0) or 0.0) for item in unique_family_records.values()) / independent_source_count
        if independent_source_count
        else 0.0
    )

    if independent_source_count <= 1:
        multiplier = 0.66 + (0.18 if official_count else 0.0) + 0.10 * avg_quality + 0.04 * min(independent_domain_count, 2)
    else:
        multiplier = 0.70 + 0.12 * min(independent_source_count, 4) + 0.09 * min(independent_domain_count, 3) + 0.08 * avg_quality + 0.12 * min(official_count, 2)
        if independent_source_count >= 2 and independent_domain_count >= 2:
            multiplier += 0.03
    return clamp(multiplier, 0.55, 1.25), raw_coverage_count, independent_domain_count, independent_source_count, syndicated_coverage_count


def apply_corroboration_to_events(events: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for event in events:
        source_key = canonical_source_key(event)
        source_domain = article_source_domain(event)
        source_tier = int(event.get("source_tier", 4) or 4)
        source_quality_score = float(event.get("source_quality_score", 0.0) or 0.0)
        coverage_items = corroboration_coverage_items(event)
        for relationship in event.get("stock_relationships", []):
            key = corroboration_group_key(event, relationship)
            if not key[0]:
                continue
            groups.setdefault(key, []).append(
                {
                    "event": event,
                    "relationship": relationship,
                    "source_key": source_key,
                    "domain": source_domain,
                    "source_tier": source_tier,
                    "source_quality_score": source_quality_score,
                    "coverage_items": coverage_items,
                }
            )

    for supports in groups.values():
        multiplier, raw_coverage_count, domain_count, source_count, syndicated_coverage_count = corroboration_multiplier_for_group(supports)
        corroboration_score = clamp((multiplier - 0.55) / 0.70, 0.0, 1.0)
        for item in supports:
            relationship = item["relationship"]
            relationship_confidence = clamp(float(relationship.get("relationship_confidence", relationship.get("confidence", 0.0)) or 0.0) * multiplier, 0.0, 1.0)
            evidence_strength = clamp(float(relationship.get("evidence_strength", 0.0) or 0.0) * max(1.0, min(multiplier, 1.15)), 0.0, 1.0)
            relationship.update(
                {
                    "corroboration_count": raw_coverage_count,
                    "raw_coverage_count": raw_coverage_count,
                    "independent_coverage_count": source_count,
                    "syndicated_coverage_count": syndicated_coverage_count,
                    "independent_domain_count": domain_count,
                    "corroboration_domain_count": domain_count,
                    "corroboration_source_count": source_count,
                    "corroboration_multiplier": round(multiplier, 3),
                    "corroboration_score": round(corroboration_score, 3),
                    "corroboration_agreement_score": round(corroboration_score, 3),
                    "corroboration_label": (
                        "official_source"
                        if source_count <= 1 and any(int(item.get("source_tier", 4) or 4) <= 1 for item in supports)
                        else "independently_corroborated"
                        if source_count >= 2 and domain_count >= 2
                        else "corroborated"
                        if source_count > 1
                        else "single_weak_source"
                        if any(int(item.get("source_tier", 4) or 4) >= 4 for item in supports)
                        else "single_source"
                    ),
                    "relationship_confidence": round(relationship_confidence, 3),
                    "confidence": round(relationship_confidence, 3),
                    "evidence_strength": round(evidence_strength, 3),
                    "confidence_label": relationship_confidence_label(relationship_confidence, str(relationship.get("coverage_warning", ""))),
                }
            )


def _source_outcome_history_defaults() -> dict[str, Any]:
    return {"sources": {}}


def normalize_source_outcome_history(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return _source_outcome_history_defaults()
    normalized_sources: dict[str, dict[str, Any]] = {}
    for key, value in raw.get("sources", {}).items() if isinstance(raw.get("sources", {}), dict) else {}:
        normalized_key = str(key or "").strip().lower()
        if not normalized_key or not isinstance(value, dict):
            continue
        try:
            sample_size = max(0, int(value.get("sample_size", 0) or 0))
        except Exception:
            sample_size = 0
        try:
            weighted_outcome_sum = float(value.get("weighted_outcome_sum", 0.0) or 0.0)
        except Exception:
            weighted_outcome_sum = 0.0
        normalized_sources[normalized_key] = {
            "sample_size": sample_size,
            "weighted_outcome_sum": weighted_outcome_sum,
        }
    return {"sources": normalized_sources}


def load_source_outcome_history() -> dict[str, Any]:
    try:
        raw = json.loads(SOURCE_OUTCOME_HISTORY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _source_outcome_history_defaults()
    except Exception:
        return _source_outcome_history_defaults()
    return normalize_source_outcome_history(raw)


def save_source_outcome_history(history: dict[str, Any]) -> None:
    normalized = normalize_source_outcome_history(history)
    SOURCE_OUTCOME_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SOURCE_OUTCOME_HISTORY_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_reliability_history_key(source_name: str = "", url: str = "", source_profile: dict[str, Any] | None = None) -> str:
    profile = source_profile if isinstance(source_profile, dict) else {}
    parsed = urlsplit(url or "")
    candidates = [
        normalize_domain(str(profile.get("canonical_domain") or "")),
        normalize_domain(parsed.netloc or (url if "." in url and "/" not in url else "")),
        normalize_match_text(str(profile.get("canonical_name") or "")),
        normalize_match_text(source_name),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return "unknown"


def source_outcome_weight(validation_status: str, validation_score: float) -> float:
    status = str(validation_status or "unvalidated").strip().lower() or "unvalidated"
    try:
        score = clamp(float(validation_score or 0.0), 0.0, 1.0)
    except Exception:
        score = 0.0
    if status == "confirmed":
        return 0.6 + 0.4 * score
    if status == "rejected":
        return -(0.6 + 0.4 * score)
    if status == "predicted_only":
        return 0.1 * score
    return 0.0


def historical_reliability_metrics(history: dict[str, Any], history_key: str) -> dict[str, Any]:
    sources = history.get("sources", {}) if isinstance(history, dict) else {}
    entry = sources.get(str(history_key or "").strip().lower(), {}) if isinstance(sources, dict) else {}
    try:
        sample_size = max(0, int(entry.get("sample_size", 0) or 0))
    except Exception:
        sample_size = 0
    try:
        weighted_outcome_sum = float(entry.get("weighted_outcome_sum", 0.0) or 0.0)
    except Exception:
        weighted_outcome_sum = 0.0
    # Time-decay: apply 30-day half-life to stale outcome history
    last_updated_raw = entry.get("last_updated")
    if last_updated_raw and sample_size > 0:
        try:
            last_updated = datetime.fromisoformat(str(last_updated_raw))
            age_days = max(0.0, (now_wib() - last_updated).total_seconds() / 86400.0)
            decay = 0.5 ** (age_days / 30.0)
            if decay < 0.95:  # only apply if >1.5 days old
                weighted_outcome_sum *= decay
                sample_size = max(1, int(sample_size * decay))
        except Exception:
            pass
    reliability_score = clamp(weighted_outcome_sum / sample_size, -1.0, 1.0) if sample_size else 0.0
    stability = clamp(sample_size / 5.0, 0.0, 1.0)
    multiplier = clamp(1.0 + 0.1 * reliability_score * stability, 0.85, 1.15)
    return {
        "historical_reliability_multiplier": round(multiplier, 3),
        "historical_outcome_sample_size": sample_size,
        "historical_reliability_score": round(reliability_score, 3),
    }


def channel_reliability_metrics(history: dict[str, Any], channel: str) -> dict[str, Any]:
    """Get reliability metrics for a specific policy channel from outcome history."""
    channels = history.get("channels", {}) if isinstance(history, dict) else {}
    channel_key = str(channel or "").strip().upper()
    entry = channels.get(channel_key, {}) if isinstance(channels, dict) and channel_key else {}
    if not entry:
        return {"channel_reliability_multiplier": 1.0, "channel_outcome_sample_size": 0, "channel_reliability_score": 0.0}
    try:
        sample_size = max(0, int(entry.get("sample_size", 0) or 0))
    except Exception:
        sample_size = 0
    try:
        weighted_outcome_sum = float(entry.get("weighted_outcome_sum", 0.0) or 0.0)
    except Exception:
        weighted_outcome_sum = 0.0
    # Apply same time-decay as source outcomes
    last_updated_raw = entry.get("last_updated")
    if last_updated_raw and sample_size > 0:
        try:
            last_updated = datetime.fromisoformat(str(last_updated_raw))
            age_days = max(0.0, (now_wib() - last_updated).total_seconds() / 86400.0)
            decay = 0.5 ** (age_days / 30.0)
            if decay < 0.95:
                weighted_outcome_sum *= decay
                sample_size = max(1, int(sample_size * decay))
        except Exception:
            pass
    reliability_score = clamp(weighted_outcome_sum / sample_size, -1.0, 1.0) if sample_size else 0.0
    stability = clamp(sample_size / 5.0, 0.0, 1.0)
    multiplier = clamp(1.0 + 0.08 * reliability_score * stability, 0.88, 1.12)
    return {
        "channel_reliability_multiplier": round(multiplier, 3),
        "channel_outcome_sample_size": sample_size,
        "channel_reliability_score": round(reliability_score, 3),
    }


def record_source_outcome(history: dict[str, Any], history_key: str, validation_status: str, validation_score: float, *, channel: str = "") -> dict[str, Any]:
    normalized = normalize_source_outcome_history(history)
    key = str(history_key or "").strip().lower()
    if not key or key == "unknown":
        return normalized
    weight = source_outcome_weight(validation_status, validation_score)
    if abs(weight) <= 1e-9:
        return normalized
    entry = normalized.setdefault("sources", {}).setdefault(key, {"sample_size": 0, "weighted_outcome_sum": 0.0})
    sample_size = max(0, int(entry.get("sample_size", 0) or 0))
    weighted_outcome_sum = float(entry.get("weighted_outcome_sum", 0.0) or 0.0)
    if sample_size >= 20:
        sample_size = 19
        weighted_outcome_sum *= 0.95
    entry["sample_size"] = sample_size + 1
    entry["weighted_outcome_sum"] = round(clamp(weighted_outcome_sum + weight, -20.0, 20.0), 4)
    entry["last_updated"] = now_iso()
    # Per-channel outcome tracking
    channel_key = str(channel or "").strip().upper()
    if channel_key:
        channels = normalized.setdefault("channels", {})
        ch_entry = channels.setdefault(channel_key, {"sample_size": 0, "weighted_outcome_sum": 0.0})
        ch_sample = max(0, int(ch_entry.get("sample_size", 0) or 0))
        ch_outcome = float(ch_entry.get("weighted_outcome_sum", 0.0) or 0.0)
        if ch_sample >= 20:
            ch_sample = 19
            ch_outcome *= 0.95
        ch_entry["sample_size"] = ch_sample + 1
        ch_entry["weighted_outcome_sum"] = round(clamp(ch_outcome + weight, -20.0, 20.0), 4)
        ch_entry["last_updated"] = now_iso()
    return normalized


def validation_outcome_multiplier(validation_status: str, validation_score: float) -> float:
    status = str(validation_status or "unvalidated").strip().lower() or "unvalidated"
    try:
        score = clamp(float(validation_score or 0.0), 0.0, 1.0)
    except Exception:
        score = 0.0
    base = {
        "confirmed": 1.08,
        "predicted_only": 0.98,
        "insufficient_data": 0.94,
        "rejected": 0.86,
        "unvalidated": 1.0,
    }.get(status, 1.0)
    if status == "confirmed":
        base += 0.04 * score
    elif status == "predicted_only":
        base += 0.02 * score
    elif status == "insufficient_data":
        base -= 0.02 * (1.0 - score)
    elif status == "rejected":
        base -= 0.06 * score
    return round(clamp(base, 0.8, 1.15), 3)


def calibrate_source_confidence_from_validation(
    source_confidence: float,
    validation_status: str,
    validation_score: float,
    historical_reliability_multiplier: float = 1.0,
) -> float:
    try:
        base_confidence = clamp(float(source_confidence or 0.0), 0.0, 1.0)
    except Exception:
        base_confidence = 0.0
    try:
        historical_multiplier = clamp(float(historical_reliability_multiplier or 1.0), 0.85, 1.15)
    except Exception:
        historical_multiplier = 1.0
    multiplier = validation_outcome_multiplier(validation_status, validation_score)
    return round(clamp(base_confidence * historical_multiplier * multiplier, 0.0, 1.0), 3)


def source_conflict_scope_key(event: dict[str, Any], ticker: str) -> str:
    def normalized(value: Any) -> str:
        text = str(value or "").strip().lower()
        return text

    scope_parts = [normalize_ticker(ticker)]
    for value in [event.get("thread_id"), event.get("duplicate_group_id"), event.get("claim_signature")]:
        token = normalized(value)
        if token:
            scope_parts.append(token)
            return "::".join(scope_parts)
    return scope_parts[0]


def apply_source_conflicts_to_events(events: list[dict[str, Any]]) -> None:
    groups: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        source_key = canonical_source_key(event)
        source_domain = article_source_domain(event)
        source_tier = int(event.get("source_tier", 4) or 4)
        for relationship in event.get("stock_relationships", []):
            direction = str(relationship.get("impact_direction") or "neutral").strip().lower() or "neutral"
            if direction not in {"positive", "negative"}:
                continue
            ticker = normalize_ticker(str(relationship.get("ticker") or ""))
            if not ticker:
                continue
            groups.setdefault(source_conflict_scope_key(event, ticker), []).append(
                {
                    "event": event,
                    "relationship": relationship,
                    "direction": direction,
                    "source_key": source_key,
                    "domain": source_domain,
                    "source_tier": source_tier,
                }
            )

    for supports in groups.values():
        positive = [item for item in supports if item["direction"] == "positive"]
        negative = [item for item in supports if item["direction"] == "negative"]
        if not positive or not negative:
            continue
        total_count = len(positive) + len(negative)
        opposing_label = {"positive": len(negative), "negative": len(positive)}
        conflict_score = clamp(min(len(positive), len(negative)) / max(total_count, 1), 0.0, 1.0)
        for item in supports:
            relationship = item["relationship"]
            opposing_count = opposing_label.get(item["direction"], 0)
            penalty = clamp(1.0 - (0.15 * opposing_count) - (0.05 * max(0, total_count - 2)), 0.65, 1.0)
            relationship_confidence = clamp(float(relationship.get("relationship_confidence", relationship.get("confidence", 0.0)) or 0.0) * penalty, 0.0, 1.0)
            evidence_strength = clamp(float(relationship.get("evidence_strength", 0.0) or 0.0) * penalty, 0.0, 1.0)
            current_warning = str(relationship.get("coverage_warning", "")).strip()
            new_warning = current_warning or "source_conflict"
            relationship.update(
                {
                    "source_conflict": True,
                    "source_conflict_count": opposing_count,
                    "source_conflict_total_count": total_count,
                    "source_conflict_score": round(conflict_score, 3),
                    "source_conflict_penalty": round(penalty, 3),
                    "source_conflict_label": "conflicted",
                    "coverage_warning": new_warning,
                    "relationship_confidence": round(relationship_confidence, 3),
                    "confidence": round(relationship_confidence, 3),
                    "evidence_strength": round(evidence_strength, 3),
                    "confidence_label": relationship_confidence_label(relationship_confidence, new_warning),
                }
            )


def fetch_market_validation_series(ticker: str, range_name: str, interval: str) -> dict[str, Any]:
    normalized_ticker = normalize_ticker(ticker)
    knowledge = company_knowledge_for_ticker(normalized_ticker)
    proxy = knowledge.get("market_validation_proxy", {}) if isinstance(knowledge.get("market_validation_proxy"), dict) else {}
    symbol = str(proxy.get("symbol", normalized_ticker)).strip() or normalized_ticker
    warnings: list[str] = []
    if not symbol:
        return {
            "ticker": normalized_ticker,
            "symbol": normalized_ticker,
            "range": range_name,
            "interval": interval,
            "prices": [],
            "volumes": [],
            "market_time": now_iso(),
            "source": "unavailable",
            "warnings": ["validation symbol unavailable"],
        }
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?range={quote(range_name)}&interval={quote(interval)}&includePrePost=false&events=div,splits"
    try:
        response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
        response.raise_for_status()
        payload = response.json().get("chart", {}).get("result", [])
        if not payload:
            raise ValueError("empty validation history")
        result = payload[0]
        quote_data = result.get("indicators", {}).get("quote", [{}])[0]
        closes = [float(value) for value in quote_data.get("close", []) if value is not None]
        volumes = [float(value) for value in quote_data.get("volume", []) if value is not None]
        market_time = result.get("meta", {}).get("regularMarketTime")
        return {
            "ticker": normalized_ticker,
            "symbol": symbol,
            "range": range_name,
            "interval": interval,
            "prices": closes,
            "volumes": volumes,
            "market_time": datetime.fromtimestamp(market_time, tz=WIB).isoformat(timespec="seconds") if market_time else now_iso(),
            "source": "yahoo-finance",
            "warnings": warnings,
        }
    except Exception as exc:  # pragma: no cover - network failures are environment-dependent
        warnings.append(f"validation history unavailable for {symbol}: {exc}")
        return {
            "ticker": normalized_ticker,
            "symbol": symbol,
            "range": range_name,
            "interval": interval,
            "prices": [],
            "volumes": [],
            "market_time": now_iso(),
            "source": "unavailable",
            "warnings": warnings,
        }


def validation_window_for_article(article: dict[str, Any]) -> str:
    forced = str(article.get("_force_window", "") or "").strip()
    if forced in EVENT_WINDOWS:
        return forced
    published_at = article.get("published_at")
    if isinstance(published_at, datetime) and (now_wib() - published_at) <= timedelta(days=1):
        return "30m"
    return "1d"


def _alternate_validation_window(primary_window: str) -> str | None:
    """Return the other validation window for cross-checking, or None if not applicable."""
    mapping = {"30m": "1d", "1d": "30m"}
    return mapping.get(str(primary_window or "").strip())


def sample_stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(max(variance, 0.0))


def validate_market_reaction(
    article: dict[str, Any],
    ticker: str,
    quote: dict[str, Any] | None,
    relationship: dict[str, Any],
    fetcher: Callable[[str, str, str], dict[str, Any]] | None = None,
    series_cache: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    config = MARKET_VALIDATION_CONFIG or load_market_validation_config()
    windows = config.get("windows", {}) if isinstance(config, dict) else {}
    baseline_cfg = config.get("baseline", {}) if isinstance(config, dict) else {}
    thresholds = config.get("thresholds", {}) if isinstance(config, dict) else {}
    fallback = config.get("fallback", {}) if isinstance(config, dict) else {}

    validation_window = validation_window_for_article(article)
    window_cfg = windows.get(validation_window) or windows.get("1d") or {"range": "1mo", "interval": "1d"}
    range_name = str(window_cfg.get("range", "1mo"))
    interval = str(window_cfg.get("interval", "1d"))
    fetcher = fetcher or fetch_market_validation_series

    cache_key = (normalize_ticker(ticker), validation_window)
    if series_cache is not None and cache_key in series_cache:
        series = series_cache[cache_key]
    else:
        series = fetcher(ticker, range_name, interval)
        if series_cache is not None:
            series_cache[cache_key] = series

    prices = [float(value) for value in series.get("prices", []) if value is not None]
    volumes = [float(value) for value in series.get("volumes", []) if value is not None]
    warnings = [str(item) for item in series.get("warnings", []) if str(item).strip()]
    lookback_periods = max(int(baseline_cfg.get("lookback_periods", 20) or 20), 3)
    min_points = max(int(baseline_cfg.get("min_points", 5) or 5), 3)

    fallback_status = str(fallback.get("status", "predicted_only") or "predicted_only")
    fallback_reason = str(fallback.get("reason", "market history unavailable") or "market history unavailable")
    base_result = {
        "validation_status": fallback_status,
        "validation_window": validation_window,
        "abnormal_return": 0.0,
        "abnormal_volume_ratio": 0.0,
        "validation_score": 0.0,
        "validation_reason": fallback_reason,
        "validation_warnings": warnings,
        "validation_series_source": series.get("source", "unavailable"),
    }

    if not quote or not relationship:
        return {
            **base_result,
            "validation_status": "unvalidated",
            "validation_reason": "missing quote or relationship",
        }

    if len(prices) < min_points or len(volumes) < min_points:
        return {
            **base_result,
            "validation_status": "insufficient_data" if warnings else fallback_status,
            "validation_reason": warnings[0] if warnings else fallback_reason,
        }

    recent_prices = prices[-(lookback_periods + 1):]
    recent_volumes = volumes[-(lookback_periods + 1):]
    returns = []
    for previous, current in zip(recent_prices[:-1], recent_prices[1:]):
        if previous not in (None, 0):
            returns.append((float(current) - float(previous)) / float(previous))
    if len(returns) < max(2, min_points - 1):
        return {
            **base_result,
            "validation_status": "insufficient_data",
            "validation_reason": "not enough return history for baseline",
        }

    baseline_returns = returns[:-1] or returns
    observed_return = returns[-1]
    baseline_volumes = recent_volumes[:-1] or recent_volumes
    observed_volume = recent_volumes[-1]
    mean_return = sum(baseline_returns) / len(baseline_returns)
    sigma_return = sample_stddev(baseline_returns)
    return_z = abs(observed_return - mean_return) / sigma_return if sigma_return > 1e-9 else abs(observed_return - mean_return) * 100.0
    avg_volume = sum(baseline_volumes) / len(baseline_volumes) if baseline_volumes else 0.0
    volume_ratio = (observed_volume / avg_volume) if avg_volume > 0 else 0.0

    expected_direction = str(relationship.get("impact_direction", "neutral"))
    aligned = True
    if expected_direction == "positive":
        aligned = observed_return > 0
    elif expected_direction == "negative":
        aligned = observed_return < 0

    price_sigma_threshold = float(thresholds.get("price_sigma", 2.0) or 2.0)
    volume_ratio_threshold = float(thresholds.get("volume_ratio", 1.5) or 1.5)
    signal_strength = min(1.0, return_z / max(price_sigma_threshold, 0.1))
    volume_strength = min(1.0, volume_ratio / max(volume_ratio_threshold, 0.1)) if volume_ratio_threshold > 0 else 1.0
    validation_score = clamp(0.65 * signal_strength + 0.35 * volume_strength, 0.0, 1.0)

    if aligned and return_z >= price_sigma_threshold and volume_ratio >= volume_ratio_threshold:
        status = "confirmed"
        reason = "price and volume move align with predicted direction"
    elif not aligned and abs(observed_return) > 0.002:
        status = "rejected"
        reason = "market move conflicts with predicted direction"
    else:
        status = "predicted_only"
        reason = "market move is too weak or noisy to confirm the prediction"

    # Cross-window validation: check alternate window for divergence
    alt_window = _alternate_validation_window(validation_window)
    cross_window_status = None
    cross_window_divergent = False
    if alt_window:
        alt_config = windows.get(alt_window)
        if alt_config:
            alt_range = str(alt_config.get("range", "1mo"))
            alt_interval = str(alt_config.get("interval", "1d"))
            alt_cache_key = (normalize_ticker(ticker), alt_window)
            if series_cache is not None and alt_cache_key in series_cache:
                alt_series = series_cache[alt_cache_key]
            else:
                alt_series = fetcher(ticker, alt_range, alt_interval)
                if series_cache is not None:
                    series_cache[alt_cache_key] = alt_series
            alt_prices = [float(v) for v in alt_series.get("prices", []) if v is not None]
            alt_volumes = [float(v) for v in alt_series.get("volumes", []) if v is not None]
            alt_warnings = [str(w) for w in alt_series.get("warnings", []) if str(w).strip()]
            if len(alt_prices) >= min_points and len(alt_volumes) >= min_points:
                alt_returns = []
                for prev, cur in zip(alt_prices[:-1], alt_prices[1:]):
                    if prev not in (None, 0):
                        alt_returns.append((cur - prev) / prev)
                if len(alt_returns) >= max(2, min_points - 1):
                    alt_baseline = alt_returns[:-1] or alt_returns
                    alt_observed = alt_returns[-1]
                    alt_mean = sum(alt_baseline) / len(alt_baseline)
                    alt_sigma = sample_stddev(alt_baseline)
                    alt_z = abs(alt_observed - alt_mean) / alt_sigma if alt_sigma > 1e-9 else abs(alt_observed - alt_mean) * 100.0
                    alt_baseline_vols = alt_volumes[:-1] or alt_volumes
                    alt_avg_vol = sum(alt_baseline_vols) / len(alt_baseline_vols) if alt_baseline_vols else 0.0
                    alt_vol_ratio = (alt_volumes[-1] / alt_avg_vol) if alt_avg_vol > 0 else 0.0
                    alt_aligned = True
                    if expected_direction == "positive":
                        alt_aligned = alt_observed > 0
                    elif expected_direction == "negative":
                        alt_aligned = alt_observed < 0
                    if alt_aligned and alt_z >= price_sigma_threshold and alt_vol_ratio >= volume_ratio_threshold:
                        cross_window_status = "confirmed"
                    elif not alt_aligned and abs(alt_observed) > 0.002:
                        cross_window_status = "rejected"
                    else:
                        cross_window_status = "predicted_only"
                    status_set = {status, cross_window_status}
                    if "confirmed" in status_set and "rejected" in status_set:
                        cross_window_divergent = True
                        warnings.append(f"cross-window divergence: {validation_window}={status}, {alt_window}={cross_window_status}")
            elif alt_warnings:
                cross_window_status = "insufficient_data"

    return {
        "validation_status": status,
        "validation_window": validation_window,
        "abnormal_return": round(observed_return - mean_return, 4),
        "abnormal_volume_ratio": round(volume_ratio, 3),
        "validation_score": round(validation_score, 3),
        "validation_reason": reason,
        "validation_warnings": warnings,
        "validation_series_source": series.get("source", "unavailable"),
        "cross_window_status": cross_window_status,
        "cross_window_divergent": cross_window_divergent,
    }


