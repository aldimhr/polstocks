"""NLP scoring, policy matching, and article analysis."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.config import (
    CATEGORY_TO_SECTORS,
    POLICY_THEMES,
    DEFAULT_EVENT_WINDOW,
)
from backend.weights import get_weight, get_category_multiplier
from backend.sources import (
    analyze_sentiment, classify_categories, detect_event_stage,
    detect_negation_or_reversal, detect_policy_themes, extract_entities,
    score_political_relevance, sector_matches, source_quality_metrics_for_article,
    source_type_rank,
)
from backend.utils import (
    now_wib, clamp, normalize_match_text,
    collect_phrase_hits, normalize_event_window, event_window_delta, article_text,
)

def evidence_quality_score(article: dict[str, Any], themes: list[dict[str, Any]], direct_alias_hit: bool, company_evidence: list[dict[str, Any]] | None = None) -> float:
    article_source_rank = source_type_rank(article.get("source_type"))
    company_rank = max((float(item.get("quality_rank") or source_type_rank(item.get("source_type"))) for item in (company_evidence or [])), default=0.0)
    source_quality = clamp(float(article.get("source_quality_score", 0.0) or 0.0), 0.0, 1.0)
    source_freshness = clamp(float(article.get("source_freshness_score", source_quality) or 0.0), 0.0, 1.0)
    base = 0.6 + 0.32 * article_source_rank + 0.55 * source_quality + 0.3 * source_freshness + 0.2 * max(float(article.get("source_weight") or 0.0), 0.4)
    if article.get("url"):
        base += 0.2
    if len(themes) >= 2:
        base += 0.25
    if direct_alias_hit:
        base += 0.35
    if company_rank:
        base += min(0.9, 0.18 * company_rank)
    return min(5.0, base)


def recency_weight_for_article(article: dict[str, Any], window: str = DEFAULT_EVENT_WINDOW) -> tuple[float, float]:
    recency_hours = 0.0
    published_at = article.get("published_at") or now_wib()
    if isinstance(published_at, datetime):
        recency_hours = max(0.0, (now_wib() - published_at).total_seconds() / 3600.0)
    window_hours = max(event_window_delta(window).total_seconds() / 3600.0, 1.0)
    recency_weight = max(0.2, 1.0 - recency_hours / window_hours)
    return recency_hours, recency_weight


def infer_article_policy_signal(text: str) -> dict[str, list[str]]:
    supportive_terms = [
        "dorong",
        "stimulus",
        "percepat",
        "subsidi",
        "insentif",
        "relaksasi",
        "permudah",
        "turunkan bunga",
        "dukungan",
        "tambahan anggaran",
        "berlaku",
        "sahkan",
    ]
    restrictive_terms = [
        "larang",
        "batasi",
        "pembatasan",
        "kuota",
        "quota",
        "tarif",
        "bea masuk",
        "moratorium",
        "tekan",
        "perketat",
    ]
    relief_terms = [
        "batalkan",
        "cabut",
        "hapus",
        "longgarkan",
        "relaksasi",
        "buka kembali",
    ]
    return {
        "supportive_hits": collect_phrase_hits(text, supportive_terms),
        "restrictive_hits": collect_phrase_hits(text, restrictive_terms),
        "relief_hits": collect_phrase_hits(text, relief_terms),
    }


def match_policy_channels(text: str, knowledge: dict[str, Any], themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not knowledge:
        return []
    theme_names = {str(theme.get("name", "")).strip() for theme in themes if str(theme.get("name", "")).strip()}
    if not theme_names:
        return []
    article_signal = infer_article_policy_signal(text)
    business_lines = [str(item).strip() for item in knowledge.get("business_lines", []) if str(item).strip()]
    exposures = knowledge.get("exposure_factors", {}) if isinstance(knowledge.get("exposure_factors"), dict) else {}
    revenue_exposure = {str(item).strip() for item in exposures.get("revenue_exposure", []) if str(item).strip()}
    input_cost_exposure = {str(item).strip() for item in exposures.get("input_cost_exposure", []) if str(item).strip()}
    matched: list[dict[str, Any]] = []
    for detail in knowledge.get("policy_channel_details", []):
        if not isinstance(detail, dict):
            continue
        channel = str(detail.get("channel", "")).strip()
        if not channel:
            continue
        theme_hits = sorted(theme_names & set(knowledge.get("policy_exposures", [])))
        detail_text = " ".join([channel, *[str(item) for item in detail.get("keywords", [])]])
        detail_theme_hits = []
        for theme_name in theme_hits:
            theme_keywords = POLICY_THEMES.get(theme_name, {}).get("keywords", [])
            if collect_phrase_hits(detail_text, theme_keywords) or normalize_match_text(theme_name) in normalize_match_text(detail_text):
                detail_theme_hits.append(theme_name)
        if not detail_theme_hits:
            detail_theme_hits = theme_hits
        theme_keywords = [
            keyword
            for theme_name in detail_theme_hits
            for keyword in POLICY_THEMES.get(theme_name, {}).get("keywords", [])
        ]
        revenue_hits = sorted(set(detail_theme_hits) & revenue_exposure)
        input_cost_hits = sorted(set(detail_theme_hits) & input_cost_exposure)
        keyword_hits = collect_phrase_hits(text, detail.get("keywords", []))
        theme_keyword_hits = collect_phrase_hits(text, theme_keywords)
        business_line_hits = collect_phrase_hits(text, business_lines)
        if not detail_theme_hits or (not keyword_hits and not theme_keyword_hits and not business_line_hits and not revenue_hits and not input_cost_hits):
            continue
        direction_map = detail.get("direction_map", {}) if isinstance(detail.get("direction_map"), dict) else {}
        positive_direction_hits = collect_phrase_hits(text, direction_map.get("positive", []))
        negative_direction_hits = collect_phrase_hits(text, direction_map.get("negative", []))
        confidence = clamp(
            0.2
            + 0.3 * float(detail.get("confidence", 0.5))
            + 0.12 * len(detail_theme_hits)
            + 0.1 * len(keyword_hits)
            + 0.09 * len(theme_keyword_hits)
            + 0.1 * len(business_line_hits)
            + 0.08 * len(revenue_hits)
            + 0.05 * len(input_cost_hits),
            0.0,
            1.0,
        )
        matched.append(
            {
                "channel": channel,
                "channel_confidence": round(confidence, 3),
                "matched_themes": detail_theme_hits,
                "keyword_hits": keyword_hits,
                "theme_keyword_hits": theme_keyword_hits,
                "business_line_hits": business_line_hits,
                "revenue_exposure_hits": revenue_hits,
                "input_cost_exposure_hits": input_cost_hits,
                "positive_direction_hits": positive_direction_hits,
                "negative_direction_hits": negative_direction_hits,
                "article_signal": article_signal,
            }
        )
    matched.sort(
        key=lambda item: (
            item["channel_confidence"],
            len(item["keyword_hits"]) + len(item.get("theme_keyword_hits", [])),
            len(item["business_line_hits"]),
        ),
        reverse=True,
    )
    return matched[:4]


def score_company_exposure(knowledge: dict[str, Any], matched_channels: list[dict[str, Any]], direct_alias_hit: bool) -> dict[str, Any]:
    exposure_factors = knowledge.get("exposure_factors", {}) if isinstance(knowledge.get("exposure_factors"), dict) else {}
    if direct_alias_hit:
        return {
            "company_exposure": 5.0,
            "channel_confidence": round(max((float(item.get("channel_confidence", 0.0)) for item in matched_channels), default=1.0), 3),
            "exposure_factors": exposure_factors,
            "exposure_rationale": "Direct company mention in the source creates a first-order linkage.",
        }
    if not matched_channels:
        return {
            "company_exposure": 0.0,
            "channel_confidence": 0.0,
            "exposure_factors": exposure_factors,
            "exposure_rationale": "No matched company-specific policy channel.",
        }
    avg_channel_confidence = sum(float(item.get("channel_confidence", 0.0)) for item in matched_channels) / len(matched_channels)
    financing_bonus = {"low": 0.15, "medium": 0.35, "high": 0.55}.get(str(exposure_factors.get("financing_sensitivity", "unknown")), 0.0)
    regulatory_bonus = {"low": 0.1, "medium": 0.2, "high": 0.35}.get(str(exposure_factors.get("regulatory_dependency", "unknown")), 0.0)
    trade_bonus = {"low": 0.05, "medium": 0.15, "high": 0.3}.get(str(exposure_factors.get("export_import_dependency", "unknown")), 0.0)
    revenue_bonus = 0.18 * sum(len(item.get("revenue_exposure_hits", [])) for item in matched_channels)
    cost_penalty = 0.08 * sum(len(item.get("input_cost_exposure_hits", [])) for item in matched_channels)
    exposure = clamp(2.45 + 1.55 * avg_channel_confidence + financing_bonus + regulatory_bonus + trade_bonus + revenue_bonus - cost_penalty, 0.0, 5.0)
    return {
        "company_exposure": round(exposure, 2),
        "channel_confidence": round(avg_channel_confidence, 3),
        "exposure_factors": exposure_factors,
        "exposure_rationale": f"Matched {len(matched_channels)} company-specific policy channel(s) with avg confidence {avg_channel_confidence:.2f}.",
    }


def expected_direction_for_company(themes: list[dict[str, Any]], matched_channels: list[dict[str, Any]], knowledge: dict[str, Any]) -> dict[str, Any]:
    theme_names = {str(theme.get("name", "")).strip() for theme in themes if str(theme.get("name", "")).strip()}
    exposure_factors = knowledge.get("exposure_factors", {}) if isinstance(knowledge.get("exposure_factors"), dict) else {}
    positive_score = 0.0
    negative_score = 0.0
    rationale_parts: list[str] = []
    article_signal = matched_channels[0].get("article_signal", {}) if matched_channels else {"supportive_hits": [], "restrictive_hits": [], "relief_hits": []}
    supportive_hits = list(article_signal.get("supportive_hits", []))
    restrictive_hits = list(article_signal.get("restrictive_hits", []))
    relief_hits = list(article_signal.get("relief_hits", []))

    for channel in matched_channels:
        channel_themes = set(channel.get("matched_themes", [])) or theme_names
        positive_direction_hits = channel.get("positive_direction_hits", [])
        negative_direction_hits = channel.get("negative_direction_hits", [])
        positive_score += 0.9 * len(positive_direction_hits)
        negative_score += 0.9 * len(negative_direction_hits)
        if channel.get("keyword_hits"):
            rationale_parts.append(f"{channel['channel']} via {', '.join(channel['keyword_hits'][:3])}")

        if channel_themes & {"HOUSING", "BANKING_LIQUIDITY", "INFRASTRUCTURE", "DIGITAL_PUBLIC", "DOWNSTREAMING", "FOOD_SECURITY", "DEFENSE_PROCUREMENT"}:
            positive_score += 0.9 * len(supportive_hits)
            positive_score += 0.8 * len(relief_hits)
            negative_score += 0.7 * len(restrictive_hits)
        if "TRADE_RESTRICTION" in channel_themes:
            if restrictive_hits and relief_hits:
                positive_score += 1.7
                rationale_parts.append("restriction rollback improves trade realization")
            elif restrictive_hits:
                negative_score += 1.7
                rationale_parts.append("trade restriction pressures export/import volumes")
            elif supportive_hits:
                positive_score += 0.6
        if "ENERGY_TRANSITION" in channel_themes:
            if restrictive_hits and relief_hits:
                positive_score += 1.0
            elif restrictive_hits:
                negative_score += 1.0
            elif supportive_hits:
                positive_score += 0.7

    if "TRADE_RESTRICTION" in theme_names and str(exposure_factors.get("export_import_dependency", "unknown")) == "high":
        if restrictive_hits and not relief_hits:
            negative_score += 0.8
        elif restrictive_hits and relief_hits:
            positive_score += 0.8
    if theme_names & {"BANKING_LIQUIDITY", "HOUSING"} and str(exposure_factors.get("financing_sensitivity", "unknown")) in {"medium", "high"}:
        positive_score += 0.5 * len(supportive_hits)
        negative_score += 0.4 * len(restrictive_hits)

    delta = positive_score - negative_score
    if delta >= 0.75:
        impact_direction = "positive"
    elif delta <= -0.75:
        impact_direction = "negative"
    elif positive_score > 0.0 and negative_score > 0.0:
        impact_direction = "mixed"
    else:
        impact_direction = "neutral"

    # Force negative to neutral: backtest shows 0/16 accuracy for negative predictions.
    # Re-enable when historical backfill proves negative signals work.
    if impact_direction == "negative":
        impact_direction = "neutral"
        rationale_parts.append("negative direction suppressed: insufficient backtest accuracy")

    if not rationale_parts:
        rationale_parts.append("direction inferred from matched policy themes and company exposures")
    return {
        "impact_direction": impact_direction,
        "direction_rationale": "; ".join(rationale_parts[:3]),
        "positive_score": round(positive_score, 2),
        "negative_score": round(negative_score, 2),
    }


def relationship_type_for_link(direct_alias_hit: bool, matched_channels: list[dict[str, Any]]) -> str | None:
    if direct_alias_hit:
        return "direct"
    if matched_channels:
        return "indirect"
    return None


def relationship_confidence_label(confidence: float, coverage_warning: str = "") -> str:
    warning = str(coverage_warning or "").strip()
    if warning == "stale_coverage" and confidence < 0.7:
        return "predicted_only"
    if confidence >= 0.8:
        return "high_confidence"
    if confidence >= 0.65:
        return "confirmed"
    if confidence >= 0.4:
        return "low_confidence"
    if confidence >= 0.2:
        return "predicted_only"
    return "insufficient_data"


def analyze_article(article: dict[str, Any], watchlist: list[str], window: str = DEFAULT_EVENT_WINDOW) -> dict[str, Any]:
    text = article_text(article)
    # Original-case text for NER (article_text lowercases, which breaks NER)
    ner_text = " ".join(p for p in [article.get("headline", ""), article.get("summary", ""), article.get("source", "")] if p)
    relevance = score_political_relevance(article)
    stage = detect_event_stage(text)
    reversal = detect_negation_or_reversal(text)
    sentiment, sentiment_score, sentiment_confidence = analyze_sentiment(text)
    categories = classify_categories(text)
    entities = extract_entities(ner_text)
    sector_hits = sector_matches(text)
    for category in categories:
        sector_hits.update(CATEGORY_TO_SECTORS.get(category, []))
    themes = detect_policy_themes(text)
    for theme in themes:
        sector_hits.update(theme["sectors"])

    article_quality = source_quality_metrics_for_article(article)
    article_context = {**article, **article_quality, "relevance_label": relevance.get("relevance_label", "not_political"), "sentiment": sentiment, "sentiment_score": sentiment_score}

    from backend.events import build_stock_relationships
    stock_relationships = build_stock_relationships(
        article=article_context,
        watchlist=watchlist,
        categories=categories or ["PARLIAMENT_SESSION"],
        sector_hits=sector_hits,
        themes=themes,
        sentiment_confidence=sentiment_confidence,
        window=window,
    )
    impacted_tickers = [item["ticker"] for item in stock_relationships]

    stage_weight = {
        "proposal": 0.68,
        "debate": 0.8,
        "approved": 1.08,
        "effective": 1.15,
        "enforced": 1.05,
        "delayed": 0.66,
        "revoked": 0.72,
        "unspecified": 0.85,
    }.get(stage.get("event_stage", "unspecified"), 0.85)
    confidence = clamp(
        (
            0.1
            + 0.25 * float(relevance.get("relevance_score", 0.0))
            + 0.08 * len(categories)
            + 0.08 * len(sector_hits)
            + 0.08 * len(entities)
            + 0.1 * len(themes)
            + 0.16 * sentiment_confidence
            + 0.12 * float(stage.get("event_stage_confidence", 0.0))
        ) * stage_weight,
        0.0,
        1.0,
    )
    if article.get("source_weight"):
        confidence = clamp(confidence * float(article["source_weight"]), 0.0, 1.0)
    confidence = clamp(confidence * (0.55 + 0.45 * float(article_context.get("source_quality_score", 0.5))), 0.0, 1.0)

    _, recency_weight = recency_weight_for_article(article, window)
    avg_relevance = sum(link["relevance_score"] for link in stock_relationships) / len(stock_relationships) if stock_relationships else 0.0

    # Apply per-category multiplier (use max across matched categories)
    cat_mult = max((get_category_multiplier(c) for c in (categories or ["_DEFAULT"])), default=1.0)
    return {
        **article_context,
        "sentiment": sentiment,
        "sentiment_score": round(sentiment_score, 3),
        "sentiment_confidence": round(sentiment_confidence, 3),
        "relevance_score": relevance.get("relevance_score", 0.0),
        "relevance_label": relevance.get("relevance_label", "not_political"),
        "relevance_signals": relevance.get("relevance_signals", {}),
        "relevance_penalties": relevance.get("relevance_penalties", {}),
        "event_stage": stage.get("event_stage", "unspecified"),
        "event_stage_confidence": stage.get("event_stage_confidence", 0.0),
        "event_stage_signals": stage.get("event_stage_signals", []),
        "is_reversal": reversal.get("is_reversal", False),
        "is_tentative": reversal.get("is_tentative", False),
        "reversal_hits": reversal.get("reversal_hits", []),
        "negation_hits": reversal.get("negation_hits", []),
        "categories": categories or ["PARLIAMENT_SESSION"],
        "entities": entities,
        "policy_themes": [theme["name"] for theme in themes],
        "policy_channels": [theme["channel"] for theme in themes],
        "impacted_sectors": sorted(sector_hits),
        "impacted_tickers": impacted_tickers,
        "stock_relationships": stock_relationships,
        "confidence": round(confidence, 3),
        "recency_weight": round(recency_weight, 3),
        "window": normalize_event_window(window),
        "significance": round(
            (get_weight("significance_base") + abs(sentiment_score) + avg_relevance / 5.0)
            * float(relevance.get("relevance_score", 0.0))
            * confidence
            * recency_weight
            * (0.55 + 0.45 * float(article_context.get("source_quality_score", 0.5)))
            * get_weight("significance_multiplier")
            * cat_mult,
            3,
        ),
    }


def compute_ticker_score(article: dict[str, Any], ticker: str) -> float:
    relationship = next((item for item in article.get("stock_relationships", []) if item.get("ticker") == ticker), None)
    if not relationship:
        return 0.0
    sentiment_score = float(article.get("sentiment_score", 0.0))
    relevance_factor = float(relationship.get("relevance_score", 0.0)) / 5.0
    confidence = float(
        relationship.get(
            "relationship_confidence",
            relationship.get("confidence", article.get("confidence", 0.5)),
        )
    )
    source_confidence = float(relationship.get("source_confidence", article.get("source_quality_score", 0.5)))
    evidence_strength = float(relationship.get("evidence_strength", confidence))
    relationship_multiplier = {
        "direct": 1.0,
        "indirect": float(get_weight("indirect_relationship_multiplier")),
    }.get(relationship.get("relationship_type"), 0.5)
    confidence_multiplier = clamp(0.45 + 0.55 * max(0.0, source_confidence), 0.25, 1.0)
    evidence_multiplier = clamp(0.5 + 0.5 * max(0.0, evidence_strength), 0.25, 1.0)
    from backend.validation import validation_outcome_multiplier
    validation_multiplier = validation_outcome_multiplier(
        str(relationship.get("validation_status", article.get("validation_status", "unvalidated"))),
        float(relationship.get("validation_score", article.get("validation_score", 0.0)) or 0.0),
    )
    direction = str(relationship.get("impact_direction", "neutral"))
    directional_floor = float(get_weight("directional_sentiment_floor"))
    if direction == "positive":
        directional_sentiment = max(abs(sentiment_score), directional_floor)
    elif direction == "negative":
        directional_sentiment = -max(abs(sentiment_score), directional_floor)
    elif direction == "mixed":
        directional_sentiment = 0.35 * sentiment_score
    else:
        directional_sentiment = 0.0
    # Apply per-category multiplier
    article_cats = article.get("categories", [])
    cat_mult = max((get_category_multiplier(c) for c in (article_cats or ["_DEFAULT"])), default=1.0)
    raw = directional_sentiment * relevance_factor * confidence * relationship_multiplier * confidence_multiplier * evidence_multiplier * validation_multiplier * cat_mult
    return clamp(raw, -1.0, 1.0)


