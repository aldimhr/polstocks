"""Event building, threading, tracking, and dashboard cues."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any

from backend.config import (
    WIB,
    MIN_EVIDENCE_QUALITY,
    STOCK_MASTER, TICKER_EXPOSURE_PROFILES, POLICY_THEMES,
    MIN_RELATIONSHIP_SCORE, DEFAULT_EVENT_WINDOW, EVENT_WINDOWS,
    SECTORS, DEFAULT_WATCHLIST, CACHE_TTL_SECONDS,
)
from backend.state import CACHE, CACHE_LOCK, POLICY_SIGNAL_RULES
from backend.sources import (
    unpack_news_fetch_result, fetch_news_bundle, dedupe_articles,
    build_source_health_summary, summarize_source_diagnostics_from_articles,
    source_corroboration_metrics_for_article, source_type_rank,
    infer_source_type, company_knowledge_for_ticker,
    get_watchlist, load_policy_signal_rules, policy_specificity_score,
)
from backend.scoring import (
    analyze_article, compute_ticker_score, evidence_quality_score,
    expected_direction_for_company, match_policy_channels,
    recency_weight_for_article, relationship_confidence_label,
    relationship_type_for_link, score_company_exposure,
)
from backend.stocks import (
    THREAD_CATEGORY_FAMILIES, EVENT_STAGE_ORDER, THREAD_STATUS_RANK,
    compute_sector_summary, fetch_market_index, fetch_stock_quotes,
    sort_stocks_by_impact,
)
from backend.validation import (
    apply_corroboration_to_events, apply_source_conflicts_to_events,
    calibrate_source_confidence_from_validation, channel_reliability_metrics,
    historical_reliability_metrics, load_source_outcome_history,
    record_source_outcome, save_source_outcome_history,
    source_reliability_history_key, validate_market_reaction,
    validation_outcome_multiplier,
)
from backend.utils import (
    now_wib, now_iso, normalize_ticker, strip_tags, safe_text,
    parse_datetime, extract_html_published_at, clamp, normalize_match_text,
    collect_phrase_hits, normalize_event_window, event_window_config,
    event_window_delta, event_window_label, text_similarity,
    is_stale_article, within_trading_hours, sector_for_ticker,
    company_name_for_ticker, article_text, normalize_ticker,
)

def build_stock_relationships(
    article: dict[str, Any],
    watchlist: list[str],
    categories: list[str],
    sector_hits: set[str],
    themes: list[dict[str, Any]],
    sentiment_confidence: float,
    window: str = DEFAULT_EVENT_WINDOW,
) -> list[dict[str, Any]]:
    text = article_text(article)
    relationships: list[dict[str, Any]] = []
    recency_hours, _ = recency_weight_for_article(article, window)
    for ticker in watchlist:
        info = STOCK_MASTER.get(ticker)
        if not info:
            continue

        knowledge = company_knowledge_for_ticker(ticker)
        knowledge_alias_hits = [alias for alias in knowledge.get("aliases", []) if alias in text]
        alias_hits = [alias for alias in info["aliases"] if alias in text] + knowledge_alias_hits
        direct_alias_hit = bool(alias_hits)
        if not knowledge and not direct_alias_hit:
            continue
        relevance_label = str(article.get("relevance_label", "") or "")
        source_type = str(article.get("source_type", "") or "")
        if relevance_label == "not_political" and not direct_alias_hit and source_type == "other":
            continue
        if relevance_label == "maybe" and not direct_alias_hit and source_type not in {"government", "regulator", "company"}:
            continue

        profile = TICKER_EXPOSURE_PROFILES.get(ticker, {"themes": [], "keywords": []})
        profile_theme_names = set(profile.get("themes", []))
        knowledge_theme_names = set(knowledge.get("policy_exposures", []))
        matched_themes = [theme for theme in themes if theme["name"] in (profile_theme_names | knowledge_theme_names)]
        matched_channels = match_policy_channels(text, knowledge, matched_themes or themes)
        relationship_type = relationship_type_for_link(direct_alias_hit, matched_channels)
        if not relationship_type:
            continue

        exposure = score_company_exposure(knowledge, matched_channels, direct_alias_hit)
        transmission_clarity = 5.0 if direct_alias_hit else clamp(2.5 + 2.0 * float(exposure.get("channel_confidence", 0.0)), 0.0, 5.0)
        company_exposure = float(exposure.get("company_exposure", 0.0))
        if transmission_clarity <= 0.0 or company_exposure <= 0.0:
            continue

        specificity = policy_specificity_score(categories, themes, text)
        timing = max(1.0, min(5.0, 5.0 - recency_hours / max(6.0, event_window_delta(window).total_seconds() / 21600.0)))
        evidence_quality = evidence_quality_score(article, matched_themes or themes, direct_alias_hit, knowledge.get("evidence", []))
        direction = expected_direction_for_company(matched_themes or themes, matched_channels, knowledge)
        # Vagueness penalty — downgrade generic government optimism to neutral
        _VAGUE_PHRASES = [
            "tegaskan komitmen", "yakin fundamental", "perkuat pengawasan",
            "dukung penegakan hukum", "komitmen perang", "tetap kuat",
            "menegaskan kembali", "optimis terhadap", "berkomitmen untuk",
            "tegaskan kembali", "perkuat komitmen", "menegaskan pentingnya",
        ]
        if direction.get("impact_direction") == "positive":
            headline_lower = str(article.get("headline", "") or "").lower()
            combined = f"{headline_lower} {text}"
            vague_count = sum(1 for p in _VAGUE_PHRASES if p in combined)
            if vague_count > 0:
                direction = {
                    **direction,
                    "impact_direction": "neutral",
                    "direction_rationale": f"downgraded from positive: generic government rhetoric ({vague_count} vague phrases detected)",
                }
        source_quality = clamp(float(article.get("source_quality_score", 0.0) or 0.0), 0.0, 1.0)
        source_freshness = clamp(float(article.get("source_freshness_score", 1.0) or 0.0), 0.0, 1.0)
        corroboration = source_corroboration_metrics_for_article(article)
        source_tier = int(corroboration.get("source_tier", article.get("source_tier", 4)) or 4)
        try:
            duplicate_count = max(1, int(article.get("duplicate_count", 1) or 1))
        except Exception:
            duplicate_count = 1
        redundancy_factor = 1.0 / (1.0 + 0.18 * max(0, duplicate_count - 1))
        source_confidence = clamp(0.35 + 0.65 * (source_quality * source_freshness), 0.0, 1.0)
        if source_tier <= 2:
            source_confidence = clamp(source_confidence + 0.05, 0.0, 1.0)

        score = (
            0.24 * specificity
            + 0.26 * transmission_clarity
            + 0.24 * company_exposure
            + 0.14 * timing
            + 0.12 * evidence_quality
        )
        confidence = clamp((score / 5.0) * (0.7 + 0.3 * sentiment_confidence), 0.0, 1.0)
        # Mixed direction penalty — ambiguous signals get lower confidence
        impact_direction = direction.get("impact_direction", "neutral")
        if impact_direction == "mixed":
            confidence *= 0.7
        # Sentiment-direction alignment — mismatch penalizes confidence
        article_sentiment = str(article.get("sentiment", "neutral") or "neutral").strip().lower()
        if article_sentiment == "positive" and impact_direction == "negative":
            confidence *= 0.85
        elif article_sentiment == "negative" and impact_direction == "positive":
            confidence *= 0.85
        # Neutral sentiment + directional prediction = weak signal (vague rhetoric)
        elif article_sentiment == "neutral" and impact_direction in ("positive", "negative"):
            confidence *= 0.65
        # Low sentiment confidence with directional prediction = uncertain
        if sentiment_confidence < 0.4 and impact_direction in ("positive", "negative"):
            confidence *= 0.80
        relationship_confidence = clamp(confidence * source_confidence * redundancy_factor * float(corroboration.get("corroboration_multiplier", 1.0)), 0.0, 1.0)

        # Source diversity reward — multiple source types reporting the same event is a stronger signal
        source_type_count = int(corroboration.get("corroboration_source_type_count", 1) or 1)
        if source_type_count >= 3:
            relationship_confidence = clamp(relationship_confidence * 1.12, 0.0, 1.0)  # 3+ types: +12%
        elif source_type_count >= 2:
            relationship_confidence = clamp(relationship_confidence * 1.07, 0.0, 1.0)  # 2 types: +7%
        elif impact_direction in ("positive", "negative"):
            # Single source type + directional prediction = less trustworthy
            relationship_confidence = clamp(relationship_confidence * 0.95, 0.0, 1.0)  # -5%

        evidence_strength = clamp((evidence_quality / 5.0) * source_confidence * redundancy_factor * float(corroboration.get("corroboration_multiplier", 1.0)), 0.0, 1.0)
        confidence_label = relationship_confidence_label(relationship_confidence, str(article.get("coverage_warning", "")))

        # Confidence floor filter — only downgrade when sentiment AND direction are both weak
        # This targets the specific false-positive pattern: neutral sentiment + low-confidence positive direction
        CONFIDENCE_FLOOR_FOR_DIRECTION = 0.25
        if relationship_confidence < CONFIDENCE_FLOOR_FOR_DIRECTION and impact_direction in ("positive", "negative"):
            if article_sentiment == "neutral":
                impact_direction = "neutral"
                direction = {**direction, "impact_direction": "neutral", "direction_rationale": f"downgraded: confidence {relationship_confidence:.3f} < {CONFIDENCE_FLOOR_FOR_DIRECTION} with neutral sentiment"}

        if evidence_quality < MIN_EVIDENCE_QUALITY or score < MIN_RELATIONSHIP_SCORE:
            continue

        primary_theme = (matched_themes or themes or [{"channel": "company-specific transmission path", "exposure_type": "company"}])[0]
        policy_channel = matched_channels[0]["channel"] if matched_channels else (knowledge.get("policy_channels") or [primary_theme["channel"]])[0]
        summary = knowledge.get("summary") or ""
        article_source_type = str(article.get("source_type") or infer_source_type(article.get("source", ""), article.get("url", "")))
        article_evidence_rank = round(source_type_rank(article_source_type), 2)
        company_evidence_rank = round(max((float(item.get("quality_rank") or source_type_rank(item.get("source_type"))) for item in knowledge.get("evidence", [])), default=0.0), 2)
        evidence_label = f"{article_source_type} article"
        if direct_alias_hit:
            channel_hint = f" via {policy_channel}" if policy_channel else ""
            rationale = f"{company_name_for_ticker(ticker)} mentioned directly{channel_hint}"
        else:
            channel_names = [ch["channel"] for ch in matched_channels[:2]]
            rationale = f"Linked through policy channel: {', '.join(channel_names)}" if channel_names else f"{ticker} linked through sector/theme overlap"
        evidence = []
        if direct_alias_hit:
            evidence.append("company/entity mentioned in article")
        if matched_themes:
            evidence.append(f"matched policy theme: {matched_themes[0]['name'].replace('_', ' ').title()}")
        if policy_channel:
            evidence.append(f"policy channel: {policy_channel}")
        if direction.get("direction_rationale"):
            evidence.append(f"direction: {direction['direction_rationale']}")
        evidence.append(f"article source tier: {article_source_type} ({article_evidence_rank:.2f})")
        for item in knowledge.get("evidence", [])[:2]:
            evidence.append(f"{item.get('label', 'source')} [{item.get('source_type', 'other')}]: {item.get('url', '')}")

        relationships.append(
            {
                "ticker": ticker,
                "company_name": company_name_for_ticker(ticker),
                "sector": info["sector"],
                "relationship_type": relationship_type,
                "policy_specificity": round(specificity, 2),
                "transmission_clarity": round(transmission_clarity, 2),
                "company_exposure": round(company_exposure, 2),
                "timing": round(timing, 2),
                "evidence_quality": round(evidence_quality, 2),
                "article_source_type": article_source_type,
                "article_evidence_rank": article_evidence_rank,
                "company_evidence_rank": company_evidence_rank,
                "evidence_label": evidence_label,
                "relevance_score": round(score, 2),
                "confidence": round(relationship_confidence, 3),
                "relationship_confidence": round(relationship_confidence, 3),
                "source_confidence": round(source_confidence, 3),
                "evidence_strength": round(evidence_strength, 3),
                "confidence_label": confidence_label,
                "rationale": rationale,
                "policy_channel": policy_channel,
                "matched_policy_channels": matched_channels,
                "channel_confidence": round(float(exposure.get("channel_confidence", 0.0)), 3),
                "impact_direction": direction.get("impact_direction", "neutral"),
                "direction_rationale": direction.get("direction_rationale", ""),
                "exposure_type": primary_theme["exposure_type"],
                "exposure_factors": exposure.get("exposure_factors", {}),
                "knowledge_summary": summary,
                "company_evidence": knowledge.get("evidence", []),
                "evidence": evidence[:7],
                "source_tier": source_tier,
                "raw_coverage_count": corroboration.get("raw_coverage_count", 1),
                "independent_coverage_count": corroboration.get("independent_coverage_count", corroboration.get("corroboration_source_count", 1)),
                "syndicated_coverage_count": corroboration.get("syndicated_coverage_count", 0),
                "independent_domain_count": corroboration.get("independent_domain_count", corroboration.get("corroboration_domain_count", 1)),
                "corroboration_source_count": corroboration.get("corroboration_source_count", 1),
                "corroboration_domain_count": corroboration.get("corroboration_domain_count", 1),
                "corroboration_source_type_count": corroboration.get("corroboration_source_type_count", 1),
                "corroboration_agreement_score": corroboration.get("corroboration_agreement_score", 0.0),
                "corroboration_multiplier": corroboration.get("corroboration_multiplier", 1.0),
                "corroboration_label": corroboration.get("corroboration_label", "single_source"),
                "source_conflict": False,
                "source_conflict_count": 0,
                "source_conflict_total_count": 0,
                "source_conflict_score": 0.0,
                "source_conflict_penalty": 1.0,
                "source_conflict_label": "aligned",
                "source_fetch_status": str(article.get("source_profile_resolution", "unknown") or "unknown"),
            }
        )

    relationships.sort(
        key=lambda item: (item["relevance_score"], item["confidence"], item["relationship_type"] == "direct"),
        reverse=True,
    )
    return relationships[:8]


def normalize_thread_token(value: Any, fallback: str = "general") -> str:
    token = normalize_match_text(value)
    return token.replace(" ", "-") if token else fallback


def thread_category_family(article: dict[str, Any]) -> str:
    categories = article.get("categories", []) if isinstance(article.get("categories", []), list) else []
    for category in categories:
        normalized = str(category or "").strip().upper()
        if normalized:
            return THREAD_CATEGORY_FAMILIES.get(normalized, normalized)
    return "GENERAL"


def thread_institution_label(article: dict[str, Any]) -> str:
    relevance_signals = article.get("relevance_signals", {}) if isinstance(article.get("relevance_signals"), dict) else {}
    institutions = relevance_signals.get("institutions", []) if isinstance(relevance_signals.get("institutions", []), list) else []
    if institutions:
        return str(institutions[0])
    source = str(article.get("source", "")).strip()
    if source:
        return source
    return str(article.get("source_type") or "general")


def thread_entity_label(article: dict[str, Any]) -> str:
    for relationship in article.get("stock_relationships", []):
        if relationship.get("relationship_type") == "direct" and relationship.get("company_name"):
            return str(relationship.get("company_name"))
    impacted_tickers = article.get("impacted_tickers", []) if isinstance(article.get("impacted_tickers", []), list) else []
    if impacted_tickers:
        return str(impacted_tickers[0])
    entities = article.get("entities", []) if isinstance(article.get("entities", []), list) else []
    if entities:
        return str(entities[0])
    return "market"


def thread_focus_label(article: dict[str, Any]) -> str:
    rules = POLICY_SIGNAL_RULES or load_policy_signal_rules()
    focus_terms = rules.get("thread_match_terms", []) if isinstance(rules, dict) else []
    hits = collect_phrase_hits(article_text(article), focus_terms)
    if hits:
        return str(hits[0])
    channels = article.get("policy_channels", []) if isinstance(article.get("policy_channels", []), list) else []
    if channels:
        return str(channels[0])
    return "general"


def build_event_thread_key(article: dict[str, Any]) -> str:
    top_theme = (article.get("policy_themes") or ["general"])[0]
    institution = thread_institution_label(article)
    entity = thread_entity_label(article)
    category_family = thread_category_family(article)
    return "::".join(
        [
            normalize_thread_token(top_theme),
            normalize_thread_token(institution),
            normalize_thread_token(entity),
            normalize_thread_token(category_family.lower()),
        ]
    )


def event_primary_direction(event: dict[str, Any]) -> str:
    directions = [str(item.get("impact_direction", "neutral")) for item in event.get("stock_relationships", []) if item.get("impact_direction")]
    if "negative" in directions and "positive" in directions:
        return "mixed"
    if directions:
        return directions[0]
    sentiment_score = float(event.get("sentiment_score", 0.0))
    if sentiment_score >= 0.2:
        return "positive"
    if sentiment_score <= -0.2:
        return "negative"
    return "neutral"


def summarize_thread_status(thread_events: list[dict[str, Any]]) -> tuple[str, int, str]:
    contradiction_count = 0
    contradiction_reasons: list[str] = []
    seen_positive_progress = False
    seen_negative_progress = False
    previous_direction = None
    previous_stage_rank = None
    for event in thread_events:
        stage = str(event.get("event_stage") or "unspecified")
        stage_rank = EVENT_STAGE_ORDER.get(stage, EVENT_STAGE_ORDER["unspecified"])
        direction = event_primary_direction(event)
        if stage in {"approved", "effective", "enforced"}:
            seen_positive_progress = True
        if stage in {"delayed", "revoked"} or bool(event.get("is_reversal")):
            seen_negative_progress = True
            if seen_positive_progress or previous_stage_rank not in {None, 0, 1}:
                contradiction_count += 1
                contradiction_reasons.append(f"latest coverage weakens earlier thread via {stage}")
        if previous_stage_rank is not None and stage_rank < previous_stage_rank and stage in {"delayed", "revoked", "proposal", "debate"}:
            contradiction_count += 1
            contradiction_reasons.append(f"event stage moved backward to {stage}")
        if previous_direction and direction in {"positive", "negative"} and previous_direction in {"positive", "negative"} and direction != previous_direction:
            contradiction_count += 1
            contradiction_reasons.append(f"impact direction flipped from {previous_direction} to {direction}")
        previous_direction = direction if direction != "mixed" else previous_direction
        previous_stage_rank = stage_rank

    latest_event = thread_events[-1]
    latest_stage = str(latest_event.get("event_stage") or "unspecified")
    if latest_stage in {"delayed", "revoked"} or bool(latest_event.get("is_reversal")):
        status = "reversed"
    elif contradiction_count > 0 or (seen_positive_progress and seen_negative_progress):
        status = "contested"
    elif len(thread_events) >= 2 and latest_stage in {"approved", "effective", "enforced"}:
        status = "confirmed"
    else:
        status = "active"
    summary = contradiction_reasons[0] if contradiction_reasons else ""
    return status, contradiction_count, summary


def group_articles_into_threads(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        key = build_event_thread_key(event)
        grouped.setdefault(key, []).append(event)

    thread_summaries: list[dict[str, Any]] = []
    for index, (thread_key, thread_events) in enumerate(grouped.items(), start=1):
        thread_events.sort(key=lambda item: item.get("published_at") or now_wib())
        latest_event = thread_events[-1]
        top_theme = (latest_event.get("policy_themes") or ["general"])[0]
        institution = thread_institution_label(latest_event)
        entity = thread_entity_label(latest_event)
        category_family = thread_category_family(latest_event)
        focus = thread_focus_label(latest_event)
        thread_id = f"thr_{normalize_thread_token(thread_key, fallback=str(index))[:72]}"
        thread_status, contradiction_count, contradiction_summary = summarize_thread_status(thread_events)
        latest_stage = str(latest_event.get("event_stage") or "unspecified")
        headline = str(latest_event.get("headline") or "")
        published_at = latest_event.get("published_at")
        latest_published_at = published_at if isinstance(published_at, datetime) else now_wib()
        summary = {
            "thread_id": thread_id,
            "thread_key": thread_key,
            "thread_status": thread_status,
            "article_count": len(thread_events),
            "latest_event_stage": latest_stage,
            "latest_headline": headline,
            "latest_published_at": latest_published_at,
            "contradiction_count": contradiction_count,
            "contradiction_summary": contradiction_summary,
            "top_theme": top_theme,
            "institution": institution,
            "entity": entity,
            "category": category_family,
            "focus": focus,
        }
        thread_summaries.append(summary)
        for event in thread_events:
            event["thread_id"] = thread_id
            event["thread_status"] = thread_status
            event["thread_key"] = thread_key
            event["thread_contradiction_count"] = contradiction_count
            event["thread_latest_event_stage"] = latest_stage

    thread_summaries.sort(
        key=lambda item: (
            THREAD_STATUS_RANK.get(str(item.get("thread_status")), 0),
            int(item.get("contradiction_count", 0)),
            item.get("latest_published_at") or now_wib(),
            int(item.get("article_count", 0)),
        ),
        reverse=True,
    )
    return thread_summaries


def build_event_tracking(events: list[dict[str, Any]], window: str = DEFAULT_EVENT_WINDOW) -> dict[str, Any]:
    normalized_window = normalize_event_window(window)
    event_threads = group_articles_into_threads(events)
    buckets: dict[str, dict[str, Any]] = {}
    theme_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    for event in events:
        published_at = event.get("published_at")
        if isinstance(published_at, datetime):
            bucket_key = published_at.astimezone(WIB).date().isoformat()
        else:
            bucket_key = str(published_at or now_iso())[:10]
        bucket = buckets.setdefault(bucket_key, {"date": bucket_key, "event_count": 0, "total_significance": 0.0, "top_headline": event.get("headline", "")})
        bucket["event_count"] += 1
        bucket["total_significance"] += float(event.get("significance", 0.0))
        if float(event.get("significance", 0.0)) >= float(bucket.get("max_significance", -1.0)):
            bucket["max_significance"] = float(event.get("significance", 0.0))
            bucket["top_headline"] = event.get("headline", "")
        source = str(event.get("source", "")).strip()
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1
        for theme in event.get("policy_themes", []):
            if theme:
                theme_counts[theme] = theme_counts.get(theme, 0) + 1
        for category in event.get("categories", []):
            if category:
                category_counts[category] = category_counts.get(category, 0) + 1

    timeline = []
    for day in sorted(buckets):
        bucket = buckets[day]
        count = max(int(bucket["event_count"]), 1)
        timeline.append(
            {
                "date": day,
                "event_count": bucket["event_count"],
                "avg_significance": round(bucket["total_significance"] / count, 3),
                "max_significance": round(float(bucket.get("max_significance", 0.0)), 3),
                "top_headline": bucket.get("top_headline", ""),
            }
        )

    top_sources = [
        {"name": name, "count": count}
        for name, count in sorted(source_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[:5]
    ]
    top_themes = [
        {"name": name, "count": count}
        for name, count in sorted(theme_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[:5]
    ]
    top_categories = [
        {"name": name, "count": count}
        for name, count in sorted(category_counts.items(), key=lambda item: (item[1], item[0]), reverse=True)[:5]
    ]
    strongest_day = max(timeline, key=lambda item: (item["event_count"], item["max_significance"]), default=None)
    total_events = len(events)
    avg_significance = round(sum(float(event.get("significance", 0.0)) for event in events) / total_events, 3) if total_events else 0.0
    contested_thread_count = sum(1 for thread in event_threads if thread.get("thread_status") in {"contested", "reversed"})
    reversed_thread_count = sum(1 for thread in event_threads if thread.get("thread_status") == "reversed")
    return {
        "window": normalized_window,
        "window_label": event_window_label(normalized_window),
        "timeline": timeline,
        "top_sources": top_sources,
        "top_themes": top_themes,
        "top_categories": top_categories,
        "summary": {
            "total_events": total_events,
            "thread_count": len(event_threads),
            "contested_thread_count": contested_thread_count,
            "reversed_thread_count": reversed_thread_count,
            "avg_significance": avg_significance,
            "strongest_day": strongest_day,
            "strongest_theme": top_themes[0] if top_themes else None,
        },
    }


def build_reasoning_summary(events: list[dict[str, Any]], event_threads: list[dict[str, Any]], stocks: list[dict[str, Any]]) -> dict[str, Any]:
    def bump(counts: dict[str, int], key: str, *, fallback: str = "unknown") -> None:
        normalized = str(key or fallback).strip() or fallback
        counts[normalized] = counts.get(normalized, 0) + 1

    relevance_counts: dict[str, int] = {}
    stage_counts: dict[str, int] = {}
    thread_counts: dict[str, int] = {}
    validation_counts: dict[str, int] = {}
    direction_counts: dict[str, int] = {}

    for event in events:
        bump(relevance_counts, event.get("relevance_label"), fallback="not_political")
        bump(stage_counts, event.get("event_stage"), fallback="unspecified")
        for relationship in event.get("stock_relationships", []):
            bump(validation_counts, relationship.get("validation_status"), fallback="unvalidated")
            bump(direction_counts, relationship.get("impact_direction"), fallback="neutral")

    for thread in event_threads:
        bump(thread_counts, thread.get("thread_status"), fallback="active")

    def to_buckets(counts: dict[str, int], *, order: list[str] | None = None) -> list[dict[str, Any]]:
        ordered_keys = order or []
        seen = set()
        buckets: list[dict[str, Any]] = []
        for key in ordered_keys:
            if key in counts:
                buckets.append({"name": key, "count": counts[key]})
                seen.add(key)
        for name, count in sorted(counts.items(), key=lambda item: (item[1], item[0]), reverse=True):
            if name in seen:
                continue
            buckets.append({"name": name, "count": count})
        return buckets[:5]

    confirmed = validation_counts.get("confirmed", 0)
    predicted = validation_counts.get("predicted_only", 0)
    insufficient = validation_counts.get("insufficient_data", 0)
    contested = thread_counts.get("contested", 0)
    reversed_threads = thread_counts.get("reversed", 0)
    total_validated = confirmed + predicted + int(validation_counts.get("rejected", 0))
    validation_warnings_list: list[str] = []
    if predicted > 0 and confirmed == 0 and total_validated == predicted:
        validation_warnings_list.append("all_predictions_unconfirmed")
    rejected = validation_counts.get("rejected", 0)
    if rejected > 0 and rejected >= confirmed:
        validation_warnings_list.append("more_rejected_than_confirmed")
    summary_bits = [
        f"{confirmed} confirmed links" if confirmed else None,
        f"{predicted} predicted-only links" if predicted else None,
        f"{insufficient} insufficient-data links" if insufficient else None,
        f"{contested} contested threads" if contested else None,
        f"{reversed_threads} reversed threads" if reversed_threads else None,
        f"⚠ all predictions unconfirmed" if "all_predictions_unconfirmed" in validation_warnings_list else None,
        f"⚠ more rejected than confirmed" if "more_rejected_than_confirmed" in validation_warnings_list else None,
    ]
    summary_line = " · ".join(bit for bit in summary_bits if bit) or "No reasoning summary yet"

    return {
        "summary_line": summary_line,
        "relevance_breakdown": to_buckets(relevance_counts, order=["political", "maybe", "not_political"]),
        "stage_breakdown": to_buckets(stage_counts, order=["proposal", "debate", "approved", "effective", "enforced", "delayed", "revoked", "unspecified"]),
        "thread_breakdown": to_buckets(thread_counts, order=["active", "confirmed", "contested", "reversed"]),
        "validation_breakdown": to_buckets(validation_counts, order=["confirmed", "predicted_only", "rejected", "insufficient_data", "unvalidated"]),
        "direction_breakdown": to_buckets(direction_counts, order=["positive", "negative", "neutral", "mixed"]),
        "validation_warnings": validation_warnings_list,
    }


def build_dashboard_cues(payload: dict[str, Any]) -> dict[str, Any]:
    normalized_payload = payload if isinstance(payload, dict) else {}
    source_health = normalized_payload.get("source_health_summary", {}) if isinstance(normalized_payload.get("source_health_summary", {}), dict) else {}
    reasoning = normalized_payload.get("reasoning_summary", {}) if isinstance(normalized_payload.get("reasoning_summary", {}), dict) else {}
    stocks = normalized_payload.get("stocks", []) if isinstance(normalized_payload.get("stocks", []), list) else []
    events = normalized_payload.get("events", []) if isinstance(normalized_payload.get("events", []), list) else []

    conflicted_relationship_count = int(source_health.get("conflicted_relationship_count", 0) or 0)
    weak_single_source_relationship_count = int(source_health.get("weak_single_source_relationship_count", 0) or 0)
    fallback_source_count = int(source_health.get("fallback_source_count", 0) or 0)
    stale_event_count = int(source_health.get("stale_event_count", 0) or 0)
    thin_event_count = int(source_health.get("thin_event_count", 0) or 0)
    duplicated_event_count = int(source_health.get("duplicated_event_count", 0) or 0)
    displayed_event_count = int(source_health.get("displayed_event_count", normalized_payload.get("displayed_event_count", 0) or 0) or 0)
    relationship_count = int(source_health.get("relationship_count", 0) or 0)
    source_count = int(source_health.get("source_count", 0) or 0)

    historical_sample_count = sum(
        1
        for stock in stocks
        if isinstance(stock, dict) and int(stock.get("historical_outcome_sample_size", 0) or 0) > 0
    )
    contested_thread_count = sum(
        1
        for event in events
        if isinstance(event, dict) and str(event.get("thread_status") or "").strip().lower() in {"contested", "reversed"}
    )
    confirmed_count = next(
        (int(item.get("count", 0) or 0) for item in reasoning.get("validation_breakdown", []) if str(item.get("name") or "") == "confirmed"),
        0,
    ) if isinstance(reasoning.get("validation_breakdown", []), list) else 0
    predicted_count = next(
        (int(item.get("count", 0) or 0) for item in reasoning.get("validation_breakdown", []) if str(item.get("name") or "") == "predicted_only"),
        0,
    ) if isinstance(reasoning.get("validation_breakdown", []), list) else 0

    status = "healthy"
    if conflicted_relationship_count > 0 or contested_thread_count > 0:
        status = "fragile"
    elif weak_single_source_relationship_count > 0 or fallback_source_count > 0 or stale_event_count > 0 or thin_event_count > 0 or duplicated_event_count > 0 or predicted_count > confirmed_count:
        status = "watch"

    chips: list[dict[str, Any]] = []
    if conflicted_relationship_count > 0:
        chips.append({"label": f"{conflicted_relationship_count} conflicting signal{'s' if conflicted_relationship_count != 1 else ''}", "tone": "neg"})
    if contested_thread_count > 0:
        chips.append({"label": f"{contested_thread_count} contested thread{'s' if contested_thread_count != 1 else ''}", "tone": "warn"})
    if weak_single_source_relationship_count > 0:
        chips.append({"label": f"{weak_single_source_relationship_count} weak single-source link{'s' if weak_single_source_relationship_count != 1 else ''}", "tone": "warn"})
    if fallback_source_count > 0:
        chips.append({"label": f"{fallback_source_count} fallback source profile{'s' if fallback_source_count != 1 else ''}", "tone": "warn"})
    if stale_event_count > 0:
        chips.append({"label": f"{stale_event_count} stale event{'s' if stale_event_count != 1 else ''}", "tone": "warn"})
    if historical_sample_count > 0:
        chips.append({"label": f"{historical_sample_count} source histor{'y' if historical_sample_count == 1 else 'ies'} calibrated", "tone": "info"})
    if confirmed_count > 0:
        chips.append({"label": f"{confirmed_count} confirmed link{'s' if confirmed_count != 1 else ''}", "tone": "pos"})
    if not chips:
        chips.append({"label": "No major robustness alerts", "tone": "muted"})

    if status == "fragile":
        headline = "Robustness signals need caution: conflicts or contested threads are affecting the batch."
    elif status == "watch":
        headline = "Source mix is usable but still leaning on thin, fallback, or predicted-only evidence."
    else:
        headline = "Coverage looks healthy: stronger source support with no major robustness alerts in the current batch."

    return {
        "headline": headline,
        "status": status,
        "chips": chips[:5],
        "counts": {
            "displayed_event_count": displayed_event_count,
            "relationship_count": relationship_count,
            "source_count": source_count,
            "conflicted_relationship_count": conflicted_relationship_count,
            "weak_single_source_relationship_count": weak_single_source_relationship_count,
            "fallback_source_count": fallback_source_count,
            "contested_thread_count": contested_thread_count,
            "historical_sample_count": historical_sample_count,
            "stale_event_count": stale_event_count,
            "thin_event_count": thin_event_count,
            "duplicated_event_count": duplicated_event_count,
        },
    }


def _background_refresh(
    cache_key: tuple,
    tickers: list[str],
    window: str,
    news_fetcher: Callable | None,
    stock_fetcher: Callable | None,
    market_fetcher: Callable | None,
) -> None:
    """Rebuild cache in background (stale-while-revalidate)."""
    try:
        build_refresh_payload(
            tickers, force=True, window=window,
            news_fetcher=news_fetcher, stock_fetcher=stock_fetcher, market_fetcher=market_fetcher,
        )
    except Exception:
        pass  # keep stale cache alive


def build_refresh_payload(
    tickers: list[str],
    force: bool = False,
    window: str = DEFAULT_EVENT_WINDOW,
    news_fetcher: Callable[[], tuple[list[dict[str, Any]], list[str]]] | None = None,
    stock_fetcher: Callable[[list[str]], tuple[dict[str, dict[str, Any]], list[str]]] | None = None,
    market_fetcher: Callable[[], tuple[dict[str, Any], list[str]]] | None = None,
) -> dict[str, Any]:
    normalized_window = normalize_event_window(window)
    requested = [normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)]
    if not requested:
        requested = get_watchlist()
    cache_key = (normalized_window, *sorted(requested))

    with CACHE_LOCK:
        cached = CACHE.get(cache_key)
        if cached:
            age = (now_wib() - cached["cached_at"]).total_seconds()
            if age <= CACHE_TTL_SECONDS and not force:
                payload = json.loads(json.dumps(cached["payload"], default=str))
                payload["from_cache"] = True
                payload["cache_key"] = list(cache_key)
                payload["window"] = normalized_window
                payload["window_label"] = event_window_label(normalized_window)
                return payload
            # Stale refresh: return stale data immediately,
            # refresh in background so the user never sees a 502.
            # Skip this when force=True — caller wants a fresh computation.
            if cached["payload"] and not force:
                payload = json.loads(json.dumps(cached["payload"], default=str))
                payload["from_cache"] = True
                payload["stale"] = True
                payload["cache_key"] = list(cache_key)
                payload["window"] = normalized_window
                payload["window_label"] = event_window_label(normalized_window)
                _bg_key = cache_key
                _bg_tickers = list(requested)
                _bg_window = normalized_window
                _bg_nf = news_fetcher
                _bg_sf = stock_fetcher
                _bg_mf = market_fetcher
                threading.Thread(
                    target=_background_refresh,
                    args=(_bg_key, _bg_tickers, _bg_window, _bg_nf, _bg_sf, _bg_mf),
                    daemon=True,
                ).start()
                return payload

    news_fetcher = news_fetcher or fetch_news_bundle
    stock_fetcher = stock_fetcher or fetch_stock_quotes
    market_fetcher = market_fetcher or fetch_market_index

    live_articles, news_warnings, source_diagnostics = unpack_news_fetch_result(news_fetcher())
    articles = dedupe_articles(live_articles, normalized_window)
    watchlist = list(dict.fromkeys(requested))
    analyzed_articles = [analyze_article(article, watchlist, normalized_window) for article in articles]
    analyzed_articles.sort(key=lambda article: (article.get("significance", 0.0), article.get("published_at") or now_wib()), reverse=True)
    meaningful_events = [article for article in analyzed_articles if float(article.get("significance", 0.0)) > 0.015]
    ranked_events = meaningful_events or analyzed_articles
    event_threads = group_articles_into_threads(ranked_events)
    # Propagate thread_status to individual relationships
    for event in ranked_events:
        thread_status = str(event.get("thread_status", "active") or "active")
        for relationship in event.get("stock_relationships", []):
            relationship.setdefault("thread_status", thread_status)
    events = ranked_events[:10]
    apply_corroboration_to_events(events)
    apply_source_conflicts_to_events(events)

    quotes, stock_warnings = stock_fetcher(watchlist)
    market_index, market_warnings = market_fetcher()
    validation_warnings: list[str] = []
    validation_cache: dict[tuple[str, str], dict[str, Any]] = {}
    source_outcome_history = load_source_outcome_history()
    updated_source_outcome_history = source_outcome_history
    for event in events:
        history_key = source_reliability_history_key(
            str(event.get("source") or ""),
            str(event.get("url") or ""),
            event.get("source_profile", {}) if isinstance(event.get("source_profile", {}), dict) else {},
        )
        history_metrics = historical_reliability_metrics(source_outcome_history, history_key)
        for relationship in event.get("stock_relationships", []):
            ticker = normalize_ticker(relationship.get("ticker", ""))
            validation = validate_market_reaction(
                event,
                ticker,
                quotes.get(ticker),
                relationship,
                series_cache=validation_cache,
            )
            validation_status = str(validation.get("validation_status", "unvalidated"))
            validation_score = float(validation.get("validation_score", 0.0) or 0.0)
            validation["validation_multiplier"] = validation_outcome_multiplier(
                validation_status,
                validation_score,
            )
            relationship.update(validation)
            relationship.update(history_metrics)
            # Apply channel reliability metrics
            primary_channel_for_metrics = str(relationship.get("policy_channel", ""))
            ch_metrics = channel_reliability_metrics(updated_source_outcome_history, primary_channel_for_metrics)
            relationship.update(ch_metrics)
            # Apply validation multiplier directly to relationship confidence
            raw_confidence = float(relationship.get("confidence", 0.0) or 0.0)
            val_mult = float(validation.get("validation_multiplier", 1.0) or 1.0)
            if val_mult != 1.0:
                adjusted = clamp(raw_confidence * val_mult, 0.0, 1.0)
                relationship["confidence"] = round(adjusted, 3)
                relationship["relationship_confidence"] = round(adjusted, 3)
                relationship["confidence_label"] = relationship_confidence_label(adjusted, str(relationship.get("coverage_warning", "")))
                relationship["validation_confidence_delta"] = round(adjusted - raw_confidence, 3)
            relationship["source_confidence"] = calibrate_source_confidence_from_validation(
                relationship.get("source_confidence", event.get("source_quality_score", 0.5)),
                validation_status,
                validation_score,
                historical_reliability_multiplier=float(history_metrics.get("historical_reliability_multiplier", 1.0) or 1.0),
            )
            primary_channel = ""
            matched_channels = relationship.get("matched_policy_channels", [])
            if isinstance(matched_channels, list) and matched_channels:
                primary_channel = str(matched_channels[0].get("channel", "")) if isinstance(matched_channels[0], dict) else ""
            if not primary_channel:
                primary_channel = str(relationship.get("policy_channel", ""))
            updated_source_outcome_history = record_source_outcome(
                updated_source_outcome_history,
                history_key,
                validation_status,
                validation_score,
                channel=primary_channel,
            )
            for warning in validation.get("validation_warnings", []):
                if warning:
                    validation_warnings.append(f"{ticker} validation: {warning}")
    if updated_source_outcome_history != source_outcome_history:
        save_source_outcome_history(updated_source_outcome_history)
    stocks: list[dict[str, Any]] = []
    for ticker in watchlist:
        quote = quotes.get(ticker)
        related_links = []
        for idx, event in enumerate(events):
            link = next((item for item in event.get("stock_relationships", []) if item.get("ticker") == ticker), None)
            if link:
                related_links.append((f"evt_{idx+1:03d}", link))
        related_ids = [event_id for event_id, _ in related_links]
        score_inputs = [compute_ticker_score(event, ticker) for event in events]
        recency_weights = [float(event.get("recency_weight", 1.0)) for event in events]
        weighted_total = sum(score * weight for score, weight in zip(score_inputs, recency_weights))
        total_weight = sum(recency_weights) or 1.0
        impact_score = clamp(weighted_total / total_weight, -1.0, 1.0)
        strongest_link = max(related_links, key=lambda item: item[1].get("relevance_score", 0.0), default=None)
        knowledge = company_knowledge_for_ticker(ticker)
        stocks.append(
            {
                "ticker": ticker,
                "name": (quote or {}).get("name") or company_name_for_ticker(ticker),
                "sector": (quote or {}).get("sector") or sector_for_ticker(ticker),
                "price": (quote or {}).get("price"),
                "change_pct": (quote or {}).get("change_pct"),
                "volume": (quote or {}).get("volume"),
                "after_hours": bool((quote or {}).get("after_hours")),
                "impact_score": round(impact_score, 3),
                "related_event_ids": related_ids,
                "relationship_count": len(related_links),
                "relationship_type": strongest_link[1].get("relationship_type") if strongest_link else None,
                "relevance_score": strongest_link[1].get("relevance_score") if strongest_link else None,
                "confidence": strongest_link[1].get("confidence") if strongest_link else 0.0,
                "relationship_confidence": strongest_link[1].get("relationship_confidence") if strongest_link else 0.0,
                "confidence_label": strongest_link[1].get("confidence_label") if strongest_link else "insufficient_data",
                "source_confidence": strongest_link[1].get("source_confidence") if strongest_link else 0.0,
                "evidence_strength": strongest_link[1].get("evidence_strength") if strongest_link else 0.0,
                "rationale": strongest_link[1].get("rationale") if strongest_link else "No evidence-backed political link in current batch.",
                "policy_channel": strongest_link[1].get("policy_channel") if strongest_link else None,
                "matched_policy_channels": strongest_link[1].get("matched_policy_channels") if strongest_link else [],
                "channel_confidence": strongest_link[1].get("channel_confidence") if strongest_link else 0.0,
                "impact_direction": strongest_link[1].get("impact_direction") if strongest_link else "neutral",
                "direction_rationale": strongest_link[1].get("direction_rationale") if strongest_link else "",
                "exposure_factors": strongest_link[1].get("exposure_factors") if strongest_link else knowledge.get("exposure_factors", {}),
                "knowledge_summary": strongest_link[1].get("knowledge_summary") if strongest_link else knowledge.get("summary", ""),
                "company_evidence": strongest_link[1].get("company_evidence") if strongest_link else knowledge.get("evidence", []),
                "article_source_type": strongest_link[1].get("article_source_type") if strongest_link else None,
                "article_evidence_rank": strongest_link[1].get("article_evidence_rank") if strongest_link else None,
                "company_evidence_rank": strongest_link[1].get("company_evidence_rank") if strongest_link else max((item.get("quality_rank", 0.0) for item in knowledge.get("evidence", [])), default=0.0),
                "evidence_label": strongest_link[1].get("evidence_label") if strongest_link else None,
                "source_tier": strongest_link[1].get("source_tier") if strongest_link else None,
                "corroboration_source_count": strongest_link[1].get("corroboration_source_count") if strongest_link else 0,
                "corroboration_domain_count": strongest_link[1].get("corroboration_domain_count") if strongest_link else 0,
                "corroboration_source_type_count": strongest_link[1].get("corroboration_source_type_count") if strongest_link else 0,
                "corroboration_agreement_score": strongest_link[1].get("corroboration_agreement_score") if strongest_link else 0.0,
                "corroboration_multiplier": strongest_link[1].get("corroboration_multiplier") if strongest_link else 1.0,
                "corroboration_label": strongest_link[1].get("corroboration_label") if strongest_link else "single_source",
                "corroboration_count": strongest_link[1].get("corroboration_count") if strongest_link else 0,
                "corroboration_score": strongest_link[1].get("corroboration_score") if strongest_link else 0.0,
                "validation_status": strongest_link[1].get("validation_status") if strongest_link else "unvalidated",
                "validation_window": strongest_link[1].get("validation_window") if strongest_link else None,
                "abnormal_return": strongest_link[1].get("abnormal_return") if strongest_link else 0.0,
                "abnormal_volume_ratio": strongest_link[1].get("abnormal_volume_ratio") if strongest_link else 0.0,
                "validation_score": strongest_link[1].get("validation_score") if strongest_link else 0.0,
                "validation_multiplier": strongest_link[1].get("validation_multiplier") if strongest_link else 1.0,
                "historical_reliability_multiplier": strongest_link[1].get("historical_reliability_multiplier") if strongest_link else 1.0,
                "historical_outcome_sample_size": strongest_link[1].get("historical_outcome_sample_size") if strongest_link else 0,
                "historical_reliability_score": strongest_link[1].get("historical_reliability_score") if strongest_link else 0.0,
                "validation_reason": strongest_link[1].get("validation_reason") if strongest_link else "",
                "cross_window_status": strongest_link[1].get("cross_window_status") if strongest_link else None,
                "cross_window_divergent": strongest_link[1].get("cross_window_divergent", False) if strongest_link else False,
                "channel_reliability_multiplier": strongest_link[1].get("channel_reliability_multiplier", 1.0) if strongest_link else 1.0,
                "channel_outcome_sample_size": strongest_link[1].get("channel_outcome_sample_size", 0) if strongest_link else 0,
                "channel_reliability_score": strongest_link[1].get("channel_reliability_score", 0.0) if strongest_link else 0.0,
                "validation_confidence_delta": strongest_link[1].get("validation_confidence_delta", 0.0) if strongest_link else 0.0,
                "source_conflict": strongest_link[1].get("source_conflict") if strongest_link else False,
                "source_conflict_count": strongest_link[1].get("source_conflict_count") if strongest_link else 0,
                "source_conflict_total_count": strongest_link[1].get("source_conflict_total_count") if strongest_link else 0,
                "source_conflict_score": strongest_link[1].get("source_conflict_score") if strongest_link else 0.0,
                "source_conflict_penalty": strongest_link[1].get("source_conflict_penalty") if strongest_link else 1.0,
                "source_conflict_label": strongest_link[1].get("source_conflict_label") if strongest_link else "aligned",
                "source_fetch_status": strongest_link[1].get("source_fetch_status", "unknown") if strongest_link else "unknown",
                "source": (quote or {}).get("source", "unavailable"),
            }
        )
    stocks = sort_stocks_by_impact(stocks)

    event_id_map = {f"evt_{idx+1:03d}": event for idx, event in enumerate(events)}
    formatted_events = []
    for event_id, event in event_id_map.items():
        formatted_events.append(
            {
                "id": event_id,
                "headline": event.get("headline", ""),
                "source": event.get("source", ""),
                "source_type": event.get("source_type") or infer_source_type(event.get("source", ""), event.get("url", "")),
                "url": event.get("url", ""),
                "published_at": event.get("published_at").isoformat(timespec="seconds") if isinstance(event.get("published_at"), datetime) else str(event.get("published_at")),
                "categories": event.get("categories", []),
                "sentiment": event.get("sentiment", "neutral"),
                "sentiment_score": event.get("sentiment_score", 0.0),
                "impacted_sectors": event.get("impacted_sectors", []),
                "impacted_tickers": event.get("impacted_tickers", []),
                "policy_themes": event.get("policy_themes", []),
                "policy_channels": event.get("policy_channels", []),
                "stock_relationships": event.get("stock_relationships", []),
                "event_stage": event.get("event_stage", "unspecified"),
                "thread_id": event.get("thread_id"),
                "thread_status": event.get("thread_status", "active"),
                "thread_contradiction_count": event.get("thread_contradiction_count", 0),
                "confidence": event.get("confidence", 0.0),
                "confidence_label": event.get("confidence_label", relationship_confidence_label(float(event.get("confidence", 0.0) or 0.0))),
                "window": normalized_window,
                "significance": event.get("significance", 0.0),
                "source_age_hours": event.get("source_age_hours", 0.0),
                "source_freshness_score": event.get("source_freshness_score", 0.0),
                "source_quality_score": event.get("source_quality_score", 0.0),
                "coverage_warning": event.get("coverage_warning", ""),
                "source_fetch_status": str(event.get("source_profile_resolution", "unknown") or "unknown"),
            }
        )

    sector_summary = compute_sector_summary(stocks)
    tracking = build_event_tracking(ranked_events, normalized_window)
    reasoning_summary = build_reasoning_summary(events, event_threads, stocks)
    warnings = news_warnings + stock_warnings + market_warnings + validation_warnings
    coverage_warnings = sorted({str(event.get("coverage_warning", "")).strip() for event in events if str(event.get("coverage_warning", "")).strip()})
    if "stale_coverage" in coverage_warnings:
        warnings.append("Some article coverage is stale; fresher evidence would improve confidence.")
    if "thin_source_coverage" in coverage_warnings:
        warnings.append("Some article coverage is thin; the current thread may need more independent sources.")
    if "duplicated_coverage" in coverage_warnings:
        warnings.append("Some article coverage is duplicated across mirrored sources.")
    if any(bool(relationship.get("source_conflict")) for event in events for relationship in event.get("stock_relationships", [])):
        warnings.append("Some article coverage is conflicting across sources.")
    if not articles:
        warnings.append("No live articles available.")
    if not quotes:
        warnings.append("No live stock quotes available.")

    sources = source_diagnostics or summarize_source_diagnostics_from_articles(articles)
    source_health_summary = build_source_health_summary(sources, formatted_events)
    payload = {
        "fetched_at": now_iso(),
        "from_cache": False,
        "cache_key": list(cache_key),
        "window": normalized_window,
        "window_label": event_window_label(normalized_window),
        "watchlist": watchlist,
        "events": formatted_events,
        "event_threads": [
            {
                **thread,
                "latest_published_at": thread.get("latest_published_at").isoformat(timespec="seconds") if isinstance(thread.get("latest_published_at"), datetime) else str(thread.get("latest_published_at") or ""),
            }
            for thread in event_threads
        ],
        "displayed_event_count": len(formatted_events),
        "total_event_count": len(ranked_events),
        "hidden_event_count": max(0, len(ranked_events) - len(formatted_events)),
        "reasoning_summary": reasoning_summary,
        "stocks": stocks,
        "sector_summary": sector_summary,
        "tracking": tracking,
        "market_index": market_index,
        "sources": sources,
        "source_health_summary": source_health_summary,
        "warnings": warnings,
    }

    with CACHE_LOCK:
        CACHE[cache_key] = {"cached_at": now_wib(), "payload": payload}

    return payload


