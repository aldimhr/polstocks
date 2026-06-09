"""Source fetching, parsing, quality assessment, and corroboration."""

from __future__ import annotations

import html
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit
import xml.etree.ElementTree as ET

import requests

from backend.config import (
    COMPANY_KNOWLEDGE_FILE, POLICY_SIGNAL_RULES_FILE,
    MARKET_VALIDATION_CONFIG_FILE, SOURCE_REGISTRY_FILE, WATCHLIST_FILE,
    SOURCE_TIMEOUT_SECONDS, REQUEST_HEADERS,
    SOURCE_TYPE_RANKS, POLITICAL_SIGNAL_KEYWORDS, DEFAULT_WATCHLIST,
    SECTOR_KEYWORDS, CATEGORY_RULES, POLICY_THEMES, NEWS_SOURCES, DEFAULT_EVENT_WINDOW,
)
from backend.state import (
    WATCHLIST_LOCK, WATCHLIST_STATE, COMPANY_KNOWLEDGE,
    POLICY_SIGNAL_RULES, MARKET_VALIDATION_CONFIG, SOURCE_REGISTRY,
)
from backend.utils import (
    now_wib, now_iso, normalize_ticker, strip_tags, safe_text,
    parse_datetime, extract_html_published_at, clamp, normalize_match_text,
    text_similarity, is_stale_article,
    article_text,
)
from backend.nlp import analyze_sentiment_ml, extract_entities_ml

def score_political_relevance(article: dict[str, Any]) -> dict[str, Any]:
    text = article_text(article)
    rules = POLICY_SIGNAL_RULES or load_policy_signal_rules()
    relevance_rules = rules.get("political_relevance", {}) if isinstance(rules, dict) else {}
    institution_terms = relevance_rules.get("institution_terms", [])
    legal_terms = relevance_rules.get("legal_terms", [])
    action_terms = relevance_rules.get("action_terms", [])
    weak_context_terms = relevance_rules.get("weak_context_terms", [])
    non_political_terms = relevance_rules.get("non_political_terms", [])

    institution_hits = [term for term in institution_terms if term in text]
    legal_hits = [term for term in legal_terms if term in text]
    action_hits = [term for term in action_terms if term in text]
    weak_hits = [term for term in weak_context_terms if term in text]
    non_political_hits = [term for term in non_political_terms if term in text]
    keyword_hits = [keyword for keyword in POLITICAL_SIGNAL_KEYWORDS if keyword in text]

    score = 0.0
    score += min(0.45, 0.14 * len(institution_hits))
    score += min(0.3, 0.12 * len(legal_hits))
    score += min(0.2, 0.08 * len(action_hits))
    if article.get("source_type") in {"government", "regulator"}:
        score += 0.12
    if keyword_hits:
        score += min(0.15, 0.03 * len(keyword_hits))
    score -= min(0.2, 0.06 * len(weak_hits))
    score -= min(0.45, 0.18 * len(non_political_hits))
    score = clamp(score, 0.0, 1.0)

    if score >= 0.6:
        label = "political"
    elif score >= 0.3:
        label = "maybe"
    else:
        label = "not_political"

    return {
        "relevance_score": round(score, 3),
        "relevance_label": label,
        "relevance_signals": {
            "institutions": institution_hits[:5],
            "legal": legal_hits[:5],
            "actions": action_hits[:5],
            "keyword_hits": keyword_hits[:8],
        },
        "relevance_penalties": {
            "weak_context": weak_hits[:5],
            "non_political": non_political_hits[:5],
        },
    }


def detect_event_stage(text: str) -> dict[str, Any]:
    text = str(text or "").lower()
    rules = (POLICY_SIGNAL_RULES or load_policy_signal_rules()).get("event_stage_rules", {})
    priority = ["revoked", "delayed", "effective", "approved", "enforced", "proposal", "debate"]
    hits_map = {stage: [term for term in rules.get(stage, []) if term in text] for stage in priority}
    for stage in priority:
        hits = hits_map.get(stage, [])
        if hits:
            confidence = clamp(0.45 + 0.12 * len(hits), 0.0, 1.0)
            return {"event_stage": stage, "event_stage_confidence": round(confidence, 3), "event_stage_signals": hits[:5]}
    return {"event_stage": "unspecified", "event_stage_confidence": 0.25, "event_stage_signals": []}


def detect_negation_or_reversal(text: str) -> dict[str, Any]:
    text = str(text or "").lower()
    rules = POLICY_SIGNAL_RULES or load_policy_signal_rules()
    negation_hits = [term for term in rules.get("negation_terms", []) if term in text]
    reversal_hits = [term for term in rules.get("reversal_terms", []) if term in text]
    return {
        "negation_hits": negation_hits[:5],
        "reversal_hits": reversal_hits[:5],
        "is_reversal": bool(reversal_hits),
        "is_tentative": any(term in text for term in ["wacana", "usulan", "rencana", "berencana", "kajian"]),
    }


def is_relevant_article(article: dict[str, Any]) -> bool:
    relevance = score_political_relevance(article)
    return relevance.get("relevance_label") == "political"


def source_weight(source_name: str) -> float:
    for source in NEWS_SOURCES:
        if source["name"].lower() == source_name.lower():
            return float(source["weight"])
    return 0.7


def infer_source_type(source_name: str = "", url: str = "") -> str:
    source_name = (source_name or "").lower()
    url = (url or "").lower()
    if any(token in source_name or token in url for token in ["ojk", "kpk", "bank indonesia", "bi.go.id"]):
        return "regulator"
    if "idx.co.id" in url or any(token in source_name or token in url for token in ["investor", "/ir/", "annualreport", "sustainability-report", "corporate action"]):
        return "company"
    if any(token in source_name or token in url for token in ["setkab", "sekretariat kabinet", "presiden.go.id", ".go.id"]):
        return "government"
    if "finance.yahoo.com" in url or "profile" in url:
        return "profile"
    if any(token in source_name or token in url for token in ["antara", "cnbc", "cnn", "detik", "kompas", "tempo", "beritasatu"]):
        return "media"
    return "other"


def normalize_domain(value: str) -> str:
    value = (value or "").strip().lower()
    if not value:
        return ""
    if "://" in value:
        value = urlsplit(value).netloc or value
    value = value.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if value.startswith("www."):
        value = value[4:]
    return value


def canonicalize_article_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = re.sub(r"/amp/?$", "", parsed.path or "", flags=re.I)
    path = re.sub(r"/{2,}", "/", path)
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    if path == "/":
        path = ""
    return urlunsplit((scheme, netloc, path, "", ""))


def canonical_source_key(article: dict[str, Any]) -> str:
    profile = article.get("source_profile", {}) if isinstance(article.get("source_profile", {}), dict) else {}
    candidates = [
        str(profile.get("duplicate_grouping") or "").strip().lower(),
        normalize_domain(str(profile.get("canonical_domain") or "")),
        normalize_domain(str(article.get("canonical_domain") or "")),
        normalize_domain(canonicalize_article_url(str(article.get("url") or ""))),
        normalize_match_text(article.get("source", "")),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return "unknown"


def claim_signature(article: dict[str, Any]) -> str:
    headline = normalize_match_text(article.get("headline", ""))
    summary = normalize_match_text(article.get("summary", ""))
    entities = normalize_match_text(" ".join(str(item) for item in article.get("entities", []) if str(item).strip()))
    text_bits = [bit for bit in [headline, summary, entities] if bit]
    if not text_bits:
        return canonical_source_key(article)
    signature = "::".join(text_bits[:3])
    return signature[:320]


def _article_merge_priority(article: dict[str, Any]) -> tuple[float, int, float, datetime]:
    published_at = article.get("published_at") if isinstance(article.get("published_at"), datetime) else now_wib()
    try:
        quality_score = float(article.get("source_quality_score", 0.0))
    except Exception:
        quality_score = 0.0
    try:
        tier = int(article.get("source_tier", 4) or 4)
    except Exception:
        tier = 4
    try:
        source_weight = float(article.get("source_weight", 0.0))
    except Exception:
        source_weight = 0.0
    return (quality_score, -tier, source_weight, published_at)


def merge_duplicate_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: list[list[dict[str, Any]]] = []
    for article in articles:
        candidate = dict(article)
        metadata = source_metadata_for(str(candidate.get("source") or ""), str(candidate.get("url") or ""))
        candidate.setdefault("source_profile", metadata.get("source_profile", {}))
        candidate.setdefault("source_type", metadata.get("source_type", "other"))
        candidate.setdefault("source_tier", metadata.get("source_tier", 4))
        candidate.setdefault("canonical_domain", metadata.get("canonical_domain", ""))
        candidate.setdefault("source_quality_score", metadata.get("source_quality_score", 0.0))
        candidate["canonical_url"] = canonicalize_article_url(str(candidate.get("url") or ""))
        candidate["claim_signature"] = claim_signature(candidate)
        matched_group = None
        for group in groups:
            exemplar = group[0]
            if candidate["canonical_url"] and candidate["canonical_url"] == exemplar.get("canonical_url"):
                matched_group = group
                break
            if candidate["claim_signature"] == exemplar.get("claim_signature"):
                matched_group = group
                break
            if text_similarity(candidate.get("headline", ""), exemplar.get("headline", "")) >= 0.92:
                if text_similarity(candidate.get("summary", ""), exemplar.get("summary", "")) >= 0.84 or candidate["canonical_url"] == exemplar.get("canonical_url"):
                    matched_group = group
                    break
        if matched_group is None:
            groups.append([candidate])
        else:
            matched_group.append(candidate)

    merged_articles: list[dict[str, Any]] = []
    for group in groups:
        group.sort(key=_article_merge_priority, reverse=True)
        canonical = dict(group[0])
        canonical_url = canonical.get("canonical_url") or canonicalize_article_url(str(canonical.get("url") or ""))
        source_names: list[str] = []
        source_urls: list[str] = []
        source_types: list[str] = []
        for article in group:
            source_name = str(article.get("source") or "").strip()
            if source_name and source_name not in source_names:
                source_names.append(source_name)
            normalized_url = canonicalize_article_url(str(article.get("url") or ""))
            if normalized_url and normalized_url not in source_urls:
                source_urls.append(normalized_url)
            source_type = str(article.get("source_type") or "").strip()
            if source_type and source_type not in source_types:
                source_types.append(source_type)

        latest_published_at = max(
            [article.get("published_at") for article in group if isinstance(article.get("published_at"), datetime)],
            default=canonical.get("published_at") or now_wib(),
        )
        alternate_urls = [url for url in source_urls if url != canonical_url]

        canonical.update(
            {
                "url": canonical_url or canonical.get("url", ""),
                "canonical_url": canonical_url,
                "duplicate_group_id": canonical.get("claim_signature") or claim_signature(canonical),
                "duplicate_count": len(group),
                "source_names": source_names,
                "source_urls": source_urls,
                "source_types": source_types,
                "alternate_urls": alternate_urls,
                "latest_published_at": latest_published_at,
            }
        )
        canonical.update(source_quality_metrics_for_article(canonical))
        merged_articles.append(canonical)

    merged_articles.sort(key=lambda article: (article.get("published_at") or now_wib(), _article_merge_priority(article)), reverse=True)
    return merged_articles


def _source_registry_defaults() -> dict[str, Any]:
    return {"sources": [], "by_name": {}, "by_domain": {}, "by_canonical_domain": {}}


def normalize_source_registry(raw: Any) -> dict[str, Any]:
    records = raw.get("sources", []) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        return _source_registry_defaults()

    normalized_sources: list[dict[str, Any]] = []
    by_name: dict[str, dict[str, Any]] = {}
    by_domain: dict[str, dict[str, Any]] = {}

    for record in records:
        if not isinstance(record, dict):
            continue
        canonical_name = str(record.get("name", "")).strip()
        if not canonical_name:
            continue

        aliases = [str(item).strip() for item in record.get("aliases", []) if str(item).strip()]
        domains = [normalize_domain(item) for item in record.get("domains", []) if normalize_domain(item)]
        raw_canonical_domain = str(record.get("canonical_domain", "")).strip().lower()
        canonical_domain = normalize_domain(raw_canonical_domain)
        if canonical_domain and canonical_domain not in domains:
            domains.append(canonical_domain)
        if not raw_canonical_domain and domains:
            raw_canonical_domain = domains[0]
        display_canonical_domain = raw_canonical_domain or canonical_domain

        source_type = str(record.get("source_type") or infer_source_type(canonical_name, canonical_domain)).strip().lower() or "other"
        try:
            tier = int(record.get("tier", 4))
        except Exception:
            tier = 4
        tier = max(1, min(4, tier))
        try:
            trust_weight = float(record.get("trust_weight", 0.5))
        except Exception:
            trust_weight = 0.5
        trust_weight = clamp(trust_weight, 0.0, 1.0)

        country_focus = str(record.get("country_focus", "mixed")).strip().lower() or "mixed"
        notes = str(record.get("notes", "")).strip()
        duplicate_grouping = str(record.get("duplicate_grouping") or canonical_domain or canonical_name).strip().lower()
        if not duplicate_grouping:
            duplicate_grouping = normalize_match_text(canonical_name)

        profile = {
            **record,
            "name": canonical_name,
            "canonical_name": canonical_name,
            "aliases": aliases,
            "domains": domains,
            "canonical_domain": display_canonical_domain,
            "source_type": source_type,
            "tier": tier,
            "trust_weight": trust_weight,
            "country_focus": country_focus,
            "notes": notes,
            "duplicate_grouping": duplicate_grouping,
        }

        normalized_sources.append(profile)

        name_keys = {normalize_match_text(canonical_name), normalize_match_text(profile.get("canonical_name", canonical_name))}
        name_keys.update(normalize_match_text(alias) for alias in aliases)
        for key in {key for key in name_keys if key}:
            by_name[key] = profile

        for domain in domains:
            by_domain[domain] = profile

    return {
        "sources": normalized_sources,
        "by_name": by_name,
        "by_domain": by_domain,
        "by_canonical_domain": dict(by_domain),
    }


def load_source_registry() -> dict[str, Any]:
    if not SOURCE_REGISTRY_FILE.exists():
        return _source_registry_defaults()
    try:
        raw = json.loads(SOURCE_REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return _source_registry_defaults()
    return normalize_source_registry(raw)


def _fallback_source_profile(name: str = "", url: str = "") -> dict[str, Any]:
    parsed = urlsplit(url or "")
    domain = normalize_domain(parsed.netloc or (url if "." in url and "/" not in url else ""))
    source_type = infer_source_type(name, url)
    canonical_name = name.strip() or domain or "Unknown source"
    return {
        "name": canonical_name,
        "canonical_name": canonical_name,
        "aliases": [],
        "domains": [domain] if domain else [],
        "canonical_domain": domain,
        "source_type": source_type,
        "tier": 4,
        "trust_weight": 0.5,
        "country_focus": "mixed",
        "notes": "Fallback profile inferred from source name or URL.",
        "duplicate_grouping": normalize_match_text(canonical_name) or domain or "unknown",
    }


def source_profile_for_domain(domain: str) -> dict[str, Any]:
    normalized_domain = normalize_domain(domain)
    if not normalized_domain:
        return _fallback_source_profile(url=domain)
    registry = SOURCE_REGISTRY or load_source_registry()
    profile = registry.get("by_domain", {}).get(normalized_domain)
    if profile:
        return dict(profile)
    return _fallback_source_profile(url=normalized_domain)


def source_profile_for_name(name: str) -> dict[str, Any]:
    normalized_name = normalize_match_text(name)
    registry = SOURCE_REGISTRY or load_source_registry()
    profile = registry.get("by_name", {}).get(normalized_name)
    if profile:
        return dict(profile)
    if name and "://" in name:
        return source_profile_for_url(name)
    return _fallback_source_profile(name=name)


def source_profile_for_url(url: str) -> dict[str, Any]:
    parsed = urlsplit(url or "")
    domain = parsed.netloc or (url if "." in url and "/" not in url else "")
    if domain:
        registry = SOURCE_REGISTRY or load_source_registry()
        profile = registry.get("by_domain", {}).get(normalize_domain(domain))
        if profile:
            return dict(profile)
    return _fallback_source_profile(url=url)


def source_profile_resolution(source_name: str = "", url: str = "") -> tuple[dict[str, Any], str]:
    registry = SOURCE_REGISTRY or load_source_registry()
    normalized_name = normalize_match_text(source_name)
    if normalized_name:
        profile = registry.get("by_name", {}).get(normalized_name)
        if profile:
            return dict(profile), "registry_name"

    parsed = urlsplit(url or "")
    domain = parsed.netloc or (url if "." in url and "/" not in url else "")
    normalized_domain = normalize_domain(domain)
    if normalized_domain:
        profile = registry.get("by_domain", {}).get(normalized_domain)
        if profile:
            return dict(profile), "registry_domain"

    if source_name and "://" in source_name:
        return _fallback_source_profile(url=source_name), "inferred_fallback"
    if source_name or url:
        return _fallback_source_profile(name=source_name, url=url), "inferred_fallback"
    return _fallback_source_profile(), "inferred_fallback"


def source_quality_score_for_profile(profile: dict[str, Any]) -> float:
    try:
        tier = int(profile.get("tier", 4))
    except Exception:
        tier = 4
    tier = max(1, min(4, tier))
    try:
        trust_weight = float(profile.get("trust_weight", 0.5))
    except Exception:
        trust_weight = 0.5
    tier_factor = clamp((5 - tier) / 4.0, 0.25, 1.0)
    return round(clamp(trust_weight, 0.0, 1.0) * tier_factor, 3)


def source_freshness_score(published_at: datetime | None, source_profile: dict[str, Any] | None = None) -> float:
    if not isinstance(published_at, datetime):
        return 0.5
    profile = source_profile or {}
    source_type = str(profile.get("source_type") or "other")
    age_hours = max(0.0, (now_wib() - published_at).total_seconds() / 3600.0)
    half_life = profile.get("freshness_half_life_hours", 0.0)
    try:
        half_life = float(half_life)
    except Exception:
        half_life = 0.0
    if half_life <= 0:
        half_life = {
            "government": 120.0,
            "regulator": 96.0,
            "company": 84.0,
            "media": 48.0,
            "profile": 36.0,
            "other": 24.0,
        }.get(source_type, 36.0)
    decay = 0.5 ** (age_hours / max(half_life, 1.0))
    floor = {
        "government": 0.35,
        "regulator": 0.3,
        "company": 0.25,
        "media": 0.15,
        "profile": 0.12,
        "other": 0.1,
    }.get(source_type, 0.15)
    if age_hours <= 0:
        return 1.0
    return round(clamp(decay, floor, 1.0), 3)


def source_quality_metrics_for_article(article: dict[str, Any]) -> dict[str, Any]:
    profile = article.get("source_profile", {}) if isinstance(article.get("source_profile", {}), dict) else {}
    if not profile:
        profile = source_metadata_for(str(article.get("source") or ""), str(article.get("url") or "")).get("source_profile", {})
    published_at = article.get("latest_published_at") if isinstance(article.get("latest_published_at"), datetime) else article.get("published_at")
    source_type = str(article.get("source_type") or profile.get("source_type") or infer_source_type(str(article.get("source") or ""), str(article.get("url") or "")))
    base_quality = float(article.get("source_quality_score", source_quality_score_for_profile(profile)) or 0.0)
    freshness = source_freshness_score(published_at if isinstance(published_at, datetime) else None, profile)
    try:
        duplicate_count = max(1, int(article.get("duplicate_count", 1) or 1))
    except Exception:
        duplicate_count = 1
    duplicate_penalty = 1.0 / (1.0 + 0.22 * max(0, duplicate_count - 1))
    relevance_score = float(article.get("relevance_score", 0.0) or 0.0)
    relevance_label = str(article.get("relevance_label", "") or "")
    event_stage = str(article.get("event_stage", "") or "")
    direct_language_bonus = 1.0
    if source_type in {"government", "regulator", "company"} and relevance_score >= 0.5 and relevance_label != "not_political":
        direct_language_bonus += 0.08
    if event_stage in {"approved", "effective", "enforced", "revoked"}:
        direct_language_bonus += 0.05
    if source_type in {"media", "profile", "other"} and relevance_label in {"maybe", "not_political"}:
        direct_language_bonus -= 0.08
    source_quality = clamp(base_quality * (0.55 + 0.45 * freshness) * duplicate_penalty * direct_language_bonus, 0.0, 1.0)
    age_hours = 0.0
    if isinstance(published_at, datetime):
        age_hours = max(0.0, (now_wib() - published_at).total_seconds() / 3600.0)
    coverage_warning = ""
    if freshness < 0.28:
        coverage_warning = "stale_coverage"
    elif duplicate_count > 1 and source_quality < 0.65:
        coverage_warning = "duplicated_coverage"
    elif source_quality < 0.35:
        coverage_warning = "thin_source_coverage"
    return {
        "source_age_hours": round(age_hours, 1),
        "source_freshness_score": round(freshness, 3),
        "source_quality_score": round(source_quality, 3),
        "source_tier": int(article.get("source_tier", profile.get("tier", 4)) or 4),
        "coverage_warning": coverage_warning,
    }


def corroboration_family_key(profile: dict[str, Any], source_name: str = "", url: str = "") -> str:
    candidates = [
        normalize_match_text(str(profile.get("syndication_group") or "")),
        normalize_match_text(str(profile.get("duplicate_grouping") or "")),
        normalize_domain(str(profile.get("canonical_domain") or "")),
        normalize_domain(canonicalize_article_url(url)),
        normalize_match_text(source_name),
    ]
    for candidate in candidates:
        if candidate:
            return candidate
    return "unknown"


def corroboration_domain_key(profile: dict[str, Any], source_name: str = "", url: str = "") -> str:
    syndication_group = normalize_match_text(str(profile.get("syndication_group") or ""))
    if syndication_group:
        return syndication_group
    domain = normalize_domain(str(profile.get("canonical_domain") or "")) or normalize_domain(canonicalize_article_url(url))
    if domain:
        return domain
    return corroboration_family_key(profile, source_name, url)


def corroboration_coverage_items(article: dict[str, Any]) -> list[dict[str, Any]]:
    profile = article.get("source_profile", {}) if isinstance(article.get("source_profile", {}), dict) else {}
    source_type = str(article.get("source_type") or profile.get("source_type") or infer_source_type(str(article.get("source") or ""), str(article.get("url") or "")))
    try:
        duplicate_count = max(1, int(article.get("duplicate_count", 1) or 1))
    except Exception:
        duplicate_count = 1

    source_names = [str(item).strip() for item in article.get("source_names", []) if str(item).strip()] if isinstance(article.get("source_names", []), list) else []
    source_urls = [str(item).strip() for item in article.get("source_urls", []) if str(item).strip()] if isinstance(article.get("source_urls", []), list) else []
    source_types = [str(item).strip().lower() for item in article.get("source_types", []) if str(item).strip()] if isinstance(article.get("source_types", []), list) else []
    if not source_names and str(article.get("source") or "").strip():
        source_names = [str(article.get("source") or "").strip()]
    if not source_urls and str(article.get("url") or "").strip():
        source_urls = [canonicalize_article_url(str(article.get("url") or "")) or str(article.get("url") or "").strip()]
    if not source_types and source_type:
        source_types = [source_type]

    item_count = max(duplicate_count, len(source_names), len(source_urls), len(source_types), 1)
    items: list[dict[str, Any]] = []
    default_quality = float(article.get("source_quality_score", source_quality_score_for_profile(profile)) or 0.0)
    for idx in range(item_count):
        item_source_name = source_names[idx] if idx < len(source_names) else (source_names[-1] if source_names else str(article.get("source") or "").strip())
        item_url = source_urls[idx] if idx < len(source_urls) else (source_urls[-1] if source_urls else str(article.get("url") or "").strip())
        metadata = source_metadata_for(item_source_name, item_url)
        item_profile = metadata.get("source_profile", {}) if isinstance(metadata.get("source_profile", {}), dict) else {}
        item_source_type = source_types[idx] if idx < len(source_types) else str(metadata.get("source_type") or source_type or "other")
        try:
            item_source_tier = int(metadata.get("source_tier", item_profile.get("tier", article.get("source_tier", 4))) or 4)
        except Exception:
            item_source_tier = 4
        item_quality = float(metadata.get("source_quality_score", default_quality) or default_quality)
        family_key = corroboration_family_key(item_profile, item_source_name, item_url)
        items.append(
            {
                "source_name": item_source_name,
                "url": item_url,
                "source_type": item_source_type,
                "source_tier": item_source_tier,
                "source_quality_score": item_quality,
                "family_key": family_key,
                "domain_key": corroboration_domain_key(item_profile, item_source_name, item_url),
            }
        )
    return items


def source_corroboration_metrics_for_article(article: dict[str, Any]) -> dict[str, Any]:
    profile = article.get("source_profile", {}) if isinstance(article.get("source_profile", {}), dict) else {}
    if not profile:
        profile = source_metadata_for(str(article.get("source") or ""), str(article.get("url") or "")).get("source_profile", {})
    source_type = str(article.get("source_type") or profile.get("source_type") or infer_source_type(str(article.get("source") or ""), str(article.get("url") or "")))
    try:
        source_tier = int(article.get("source_tier", profile.get("tier", 4)) or 4)
    except Exception:
        source_tier = 4

    coverage_items = corroboration_coverage_items(article)
    raw_coverage_count = max(1, len(coverage_items))
    unique_families = {str(item.get("family_key") or "").strip() for item in coverage_items if str(item.get("family_key") or "").strip()}
    unique_domains = {str(item.get("domain_key") or "").strip() for item in coverage_items if str(item.get("domain_key") or "").strip()}
    independent_source_count = max(1, len(unique_families))
    independent_domain_count = max(1, len(unique_domains))
    syndicated_coverage_count = max(0, raw_coverage_count - independent_source_count)
    source_type_count = max(1, len({str(item.get("source_type") or "").strip().lower() for item in coverage_items if str(item.get("source_type") or "").strip()}))
    source_quality = float(article.get("source_quality_score", source_quality_score_for_profile(profile)) or 0.0)
    official_source = source_type in {"government", "regulator", "company"} or source_tier <= 2

    corroboration_agreement_score = clamp(
        0.45 + 0.2 * max(0, independent_source_count - 1) + 0.2 * max(0, independent_domain_count - 1) + 0.15 * max(0, source_type_count - 1),
        0.0,
        1.0,
    )
    corroboration_multiplier = clamp(
        1.0
        + 0.12 * max(0, independent_source_count - 1)
        + 0.14 * max(0, independent_domain_count - 1)
        + 0.05 * max(0, source_type_count - 1)
        + (0.05 if official_source else 0.0),
        1.0,
        1.35,
    )

    if official_source and independent_source_count <= 1:
        corroboration_label = "official_source"
    elif independent_domain_count > 1 and independent_source_count > 1:
        corroboration_label = "independently_corroborated"
    elif independent_source_count > 1:
        corroboration_label = "corroborated"
    elif source_quality < 0.4 or source_tier >= 4:
        corroboration_label = "single_weak_source"
    else:
        corroboration_label = "single_source"

    return {
        "source_tier": source_tier,
        "raw_coverage_count": raw_coverage_count,
        "independent_coverage_count": independent_source_count,
        "syndicated_coverage_count": syndicated_coverage_count,
        "independent_domain_count": independent_domain_count,
        "corroboration_source_count": independent_source_count,
        "corroboration_domain_count": independent_domain_count,
        "corroboration_source_type_count": source_type_count,
        "corroboration_agreement_score": round(corroboration_agreement_score, 3),
        "corroboration_multiplier": round(corroboration_multiplier, 3),
        "corroboration_label": corroboration_label,
    }


def source_metadata_for(source_name: str = "", url: str = "") -> dict[str, Any]:
    profile, resolution_method = source_profile_resolution(source_name, url)
    if profile.get("canonical_domain") and not url:
        profile = source_profile_for_domain(str(profile.get("canonical_domain", "")))
    return {
        "source_profile": profile,
        "source_type": profile.get("source_type", infer_source_type(source_name, url)),
        "source_tier": int(profile.get("tier", 4) or 4),
        "canonical_domain": profile.get("canonical_domain", ""),
        "source_quality_score": source_quality_score_for_profile(profile),
        "source_profile_resolution": resolution_method,
        "used_registry_profile": resolution_method.startswith("registry_"),
    }


def source_type_rank(source_type: str | None) -> float:
    return float(SOURCE_TYPE_RANKS.get(str(source_type or "other"), SOURCE_TYPE_RANKS["other"]))


def normalize_watchlist_values(raw: Any) -> list[str]:
    values: list[str] = []
    if isinstance(raw, dict):
        raw = raw.get("tickers", [])
    if isinstance(raw, list):
        seen: set[str] = set()
        for ticker in raw:
            normalized_ticker = normalize_ticker(str(ticker))
            if normalized_ticker and normalized_ticker not in seen:
                seen.add(normalized_ticker)
                values.append(normalized_ticker)
    return values


def normalize_company_knowledge(raw: Any) -> dict[str, dict[str, Any]]:
    records = raw.get("companies", []) if isinstance(raw, dict) else raw
    if not isinstance(records, list):
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        ticker = normalize_ticker(str(record.get("ticker", "")))
        policy_exposures = [str(item).strip() for item in record.get("policy_exposures", []) if str(item).strip()]
        policy_channels = [str(item).strip() for item in record.get("policy_channels", []) if str(item).strip()]
        evidence = []
        for item in record.get("evidence", []):
            if not isinstance(item, dict) or not str(item.get("url", "")).startswith(("http://", "https://")):
                continue
            source_type = str(item.get("source_type") or infer_source_type(str(item.get("label", "")), str(item.get("url", ""))))
            evidence.append(
                {
                    **item,
                    "source_type": source_type,
                    "source_date": str(item.get("source_date") or "").strip() or None,
                    "quality_rank": round(source_type_rank(source_type), 2),
                }
            )
        if not ticker or not policy_exposures or not policy_channels or not evidence:
            continue
        policy_channel_details = []
        for item in record.get("policy_channel_details", []):
            if not isinstance(item, dict):
                continue
            channel = str(item.get("channel", "")).strip()
            if not channel:
                continue
            keywords = [normalize_match_text(keyword) for keyword in item.get("keywords", []) if normalize_match_text(keyword)]
            direction_map_raw = item.get("direction_map", {}) if isinstance(item.get("direction_map"), dict) else {}
            direction_map = {
                str(key).strip().lower(): [normalize_match_text(token) for token in value if normalize_match_text(token)]
                for key, value in direction_map_raw.items()
                if isinstance(value, list)
            }
            policy_channel_details.append(
                {
                    "channel": channel,
                    "keywords": keywords,
                    "confidence": clamp(float(item.get("confidence", 0.5)), 0.0, 1.0),
                    "direction_map": direction_map,
                }
            )
        exposure_factors_raw = record.get("exposure_factors", {}) if isinstance(record.get("exposure_factors"), dict) else {}
        exposure_factors = {
            "revenue_exposure": [str(item).strip() for item in exposure_factors_raw.get("revenue_exposure", []) if str(item).strip()],
            "input_cost_exposure": [str(item).strip() for item in exposure_factors_raw.get("input_cost_exposure", []) if str(item).strip()],
            "financing_sensitivity": str(exposure_factors_raw.get("financing_sensitivity", "unknown")).strip().lower() or "unknown",
            "regulatory_dependency": str(exposure_factors_raw.get("regulatory_dependency", "unknown")).strip().lower() or "unknown",
            "export_import_dependency": str(exposure_factors_raw.get("export_import_dependency", "unknown")).strip().lower() or "unknown",
        }
        market_validation_proxy_raw = record.get("market_validation_proxy", {}) if isinstance(record.get("market_validation_proxy"), dict) else {}
        market_validation_proxy = {
            "symbol": str(market_validation_proxy_raw.get("symbol", ticker)).strip() or ticker,
            "kind": str(market_validation_proxy_raw.get("kind", "ticker")).strip() or "ticker",
        }
        normalized[ticker] = {
            **record,
            "ticker": ticker,
            "policy_exposures": policy_exposures,
            "policy_channels": policy_channels,
            "business_lines": [str(item).strip() for item in record.get("business_lines", []) if str(item).strip()],
            "aliases": [str(item).strip().lower() for item in record.get("aliases", []) if str(item).strip()],
            "evidence": evidence,
            "policy_channel_details": policy_channel_details,
            "exposure_factors": exposure_factors,
            "market_validation_proxy": market_validation_proxy,
        }
    return normalized


def load_company_knowledge_from_disk() -> dict[str, dict[str, Any]]:
    if not COMPANY_KNOWLEDGE_FILE.exists():
        return {}
    try:
        raw = json.loads(COMPANY_KNOWLEDGE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return normalize_company_knowledge(raw)


def company_knowledge_for_ticker(ticker: str) -> dict[str, Any]:
    return COMPANY_KNOWLEDGE.get(normalize_ticker(ticker), {})


def normalize_policy_signal_rules(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    political_relevance = raw.get("political_relevance", {}) if isinstance(raw.get("political_relevance"), dict) else {}
    event_stage_rules = raw.get("event_stage_rules", {}) if isinstance(raw.get("event_stage_rules"), dict) else {}
    return {
        "political_relevance": {
            "institution_terms": [str(item).strip().lower() for item in political_relevance.get("institution_terms", []) if str(item).strip()],
            "legal_terms": [str(item).strip().lower() for item in political_relevance.get("legal_terms", []) if str(item).strip()],
            "action_terms": [str(item).strip().lower() for item in political_relevance.get("action_terms", []) if str(item).strip()],
            "weak_context_terms": [str(item).strip().lower() for item in political_relevance.get("weak_context_terms", []) if str(item).strip()],
            "non_political_terms": [str(item).strip().lower() for item in political_relevance.get("non_political_terms", []) if str(item).strip()],
        },
        "event_stage_rules": {
            str(name).strip().lower(): [str(item).strip().lower() for item in values if str(item).strip()]
            for name, values in event_stage_rules.items()
            if isinstance(values, list)
        },
        "negation_terms": [str(item).strip().lower() for item in raw.get("negation_terms", []) if str(item).strip()],
        "reversal_terms": [str(item).strip().lower() for item in raw.get("reversal_terms", []) if str(item).strip()],
        "thread_match_terms": [str(item).strip().lower() for item in raw.get("thread_match_terms", []) if str(item).strip()],
    }


def load_policy_signal_rules() -> dict[str, Any]:
    if not POLICY_SIGNAL_RULES_FILE.exists():
        return normalize_policy_signal_rules({})
    try:
        raw = json.loads(POLICY_SIGNAL_RULES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return normalize_policy_signal_rules({})
    return normalize_policy_signal_rules(raw)


def normalize_market_validation_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    windows_raw = raw.get("windows", {}) if isinstance(raw.get("windows"), dict) else {}
    thresholds_raw = raw.get("thresholds", {}) if isinstance(raw.get("thresholds"), dict) else {}
    baseline_raw = raw.get("baseline", {}) if isinstance(raw.get("baseline"), dict) else {}
    fallback_raw = raw.get("fallback", {}) if isinstance(raw.get("fallback"), dict) else {}
    return {
        "windows": {
            str(name).strip(): {
                "range": str(config.get("range", "")).strip(),
                "interval": str(config.get("interval", "")).strip(),
            }
            for name, config in windows_raw.items()
            if isinstance(config, dict)
        },
        "thresholds": {
            "price_sigma": float(thresholds_raw.get("price_sigma", 2.0) or 2.0),
            "volume_ratio": float(thresholds_raw.get("volume_ratio", 1.5) or 1.5),
        },
        "baseline": {
            "lookback_periods": int(baseline_raw.get("lookback_periods", 20) or 20),
            "min_points": int(baseline_raw.get("min_points", 5) or 5),
        },
        "fallback": {
            "status": str(fallback_raw.get("status", "predicted_only")).strip() or "predicted_only",
            "reason": str(fallback_raw.get("reason", "market history unavailable")).strip() or "market history unavailable",
        },
    }


def load_market_validation_config() -> dict[str, Any]:
    if not MARKET_VALIDATION_CONFIG_FILE.exists():
        return normalize_market_validation_config({})
    try:
        raw = json.loads(MARKET_VALIDATION_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return normalize_market_validation_config({})
    return normalize_market_validation_config(raw)


def load_watchlist_from_disk() -> list[str]:
    if not WATCHLIST_FILE.exists():
        return list(DEFAULT_WATCHLIST)
    try:
        raw = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return list(DEFAULT_WATCHLIST)
    values = normalize_watchlist_values(raw)
    return values or list(DEFAULT_WATCHLIST)


def save_watchlist_to_disk(tickers: list[str]) -> None:
    WATCHLIST_FILE.write_text(
        json.dumps({"tickers": tickers, "updated_at": now_iso()}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_watchlist() -> list[str]:
    with WATCHLIST_LOCK:
        return list(WATCHLIST_STATE)


def set_watchlist(tickers: list[str]) -> list[str]:
    normalized = normalize_watchlist_values(tickers) or list(DEFAULT_WATCHLIST)
    with WATCHLIST_LOCK:
        WATCHLIST_STATE[:] = normalized
    try:
        save_watchlist_to_disk(normalized)
    except Exception:
        pass
    return list(normalized)


# Load persisted watchlist after helper definitions are available.
with WATCHLIST_LOCK:
    WATCHLIST_STATE[:] = load_watchlist_from_disk()
COMPANY_KNOWLEDGE.update(load_company_knowledge_from_disk())
POLICY_SIGNAL_RULES.update(load_policy_signal_rules())
MARKET_VALIDATION_CONFIG.update(load_market_validation_config())
SOURCE_REGISTRY.update(load_source_registry())


def _extract_loose_xml_text(block: str, tag_name: str) -> str:
    tag = re.escape(tag_name)
    match = re.search(rf"<(?:[\w.-]+:)?{tag}\b[^>]*>(.*?)</(?:[\w.-]+:)?{tag}>", block, flags=re.I | re.S)
    return match.group(1).strip() if match else ""


def parse_rss_items(source: dict[str, Any], xml_text: str) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    source_metadata = source_metadata_for(source.get("name", ""), source.get("url", ""))

    def add_item(title: str, link: str, summary: str, published_raw: str) -> None:
        if not title:
            return
        published_at = parse_datetime(published_raw)
        articles.append(
            {
                "source": source["name"],
                "headline": strip_tags(title),
                "url": html.unescape(link) if link else source["url"],
                "published_at": published_at or now_wib(),
                "summary": strip_tags(summary),
                "source_weight": float(source["weight"]),
                **source_metadata,
            }
        )

    parsed = False
    try:
        root = ET.fromstring(xml_text)
        items = list(root.findall(".//item"))
        if not items:
            items = list(root.findall(".//{*}item"))
        for item in items[:80]:
            title = safe_text(item, "title")
            link = safe_text(item, "link")
            summary = safe_text(item, "description") or safe_text(item, "encoded")
            published_at = safe_text(item, "pubDate") or safe_text(item, "date")
            add_item(title, link, summary, published_at)
        parsed = bool(items)
    except Exception:
        parsed = False

    if articles or parsed:
        return articles

    # Fallback for malformed RSS/Atom payloads where strict XML parsing fails.
    for item_match in re.finditer(r"<item\b[^>]*>(.*?)</item>", xml_text, flags=re.I | re.S):
        item_block = item_match.group(1)
        title = _extract_loose_xml_text(item_block, "title")
        link = _extract_loose_xml_text(item_block, "link")
        summary = _extract_loose_xml_text(item_block, "description") or _extract_loose_xml_text(item_block, "encoded")
        published_raw = _extract_loose_xml_text(item_block, "pubDate") or _extract_loose_xml_text(item_block, "date")
        add_item(title, link, summary, published_raw)
        if len(articles) >= 80:
            break

    return articles


def parse_html_signal(source: dict[str, Any], html_text: str) -> list[dict[str, Any]]:
    page_title = ""
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.I | re.S)
    if match:
        page_title = strip_tags(match.group(1))
    for tag in ("h1", "h2", "h3"):
        match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", html_text, flags=re.I | re.S)
        if match:
            break
    description = ""
    match = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html_text, flags=re.I)
    if match:
        description = strip_tags(match.group(1))

    source_published_at = extract_html_published_at(html_text) or now_wib()

    base_url = source["url"].rstrip("/")
    domain_match = re.match(r"https?://([^/]+)", base_url)
    domain = domain_match.group(1) if domain_match else ""
    candidates: list[dict[str, Any]] = []
    source_metadata = source_metadata_for(source.get("name", ""), source.get("url", ""))

    anchor_pattern = re.compile(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', flags=re.I | re.S)
    for href, inner_html in anchor_pattern.findall(html_text):
        title = strip_tags(inner_html)
        if len(title) < 28:
            continue
        href = html.unescape(href.strip())
        if href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        if href.startswith("/"):
            href = f"{base_url}{href}"
        elif href.startswith("//"):
            href = f"https:{href}"
        elif href.startswith("http"):
            if domain and domain not in href:
                continue
        else:
            continue
        item = {
            "source": source["name"],
            "headline": title,
            "url": href,
            "published_at": source_published_at,
            "summary": description or page_title or title,
            "source_weight": float(source["weight"]),
            **source_metadata,
        }
        if is_relevant_article(item):
            candidates.append(item)

    deduped: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for item in candidates:
        if item["url"] in seen_urls:
            continue
        if any(text_similarity(item["headline"], existing["headline"]) > 0.92 for existing in deduped):
            continue
        seen_urls.add(item["url"])
        deduped.append(item)
        if len(deduped) >= 8:
            break

    return deduped


def enrich_html_article_dates(articles: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    stats = {
        "date_enrichment_attempted": 0,
        "date_enrichment_success_count": 0,
        "date_fallback_count": 0,
    }
    for article in articles:
        url = str(article.get("url") or "").strip()
        if not url:
            stats["date_fallback_count"] += 1
            continue
        stats["date_enrichment_attempted"] += 1
        try:
            original_published_at = article.get("published_at") if isinstance(article.get("published_at"), datetime) else None
            response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
            response.raise_for_status()
            published_at = extract_html_published_at(response.text)
            if published_at:
                article["published_at"] = published_at
                if not original_published_at or published_at != original_published_at:
                    stats["date_enrichment_success_count"] += 1
                continue
        except Exception:
            pass
        stats["date_fallback_count"] += 1
    return articles, stats


def build_source_diagnostic(
    source: dict[str, Any],
    *,
    status: str,
    articles: list[dict[str, Any]] | None = None,
    warning: str | None = None,
    date_stats: dict[str, int] | None = None,
) -> dict[str, Any]:
    metadata = source_metadata_for(str(source.get("name") or ""), str(source.get("url") or ""))
    resolution_method = str(metadata.get("source_profile_resolution", "inferred_fallback") or "inferred_fallback")
    canonical_name = str(metadata.get("source_profile", {}).get("canonical_name") or source.get("name") or "Unknown source").strip() or "Unknown source"
    article_count = len(articles or [])
    stats = date_stats or {}
    return {
        "name": canonical_name,
        "kind": str(source.get("kind") or "unknown"),
        "status": status,
        "warning": str(warning or ""),
        "article_count": article_count,
        "used_registry_profile": bool(metadata.get("used_registry_profile")),
        "resolution_method": resolution_method,
        "date_enrichment_attempted": bool(stats.get("date_enrichment_attempted", 0)),
        "date_enrichment_success_count": int(stats.get("date_enrichment_success_count", 0) or 0),
        "date_fallback_count": int(stats.get("date_fallback_count", 0) or 0),
    }


def summarize_source_diagnostics_from_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for article in articles:
        source_name = str(article.get("source") or "Unknown source").strip() or "Unknown source"
        grouped.setdefault(source_name, []).append(article)

    diagnostics: list[dict[str, Any]] = []
    for source_name, group in sorted(grouped.items()):
        first = group[0]
        metadata = source_metadata_for(source_name, str(first.get("url") or ""))
        resolution_method = str(first.get("source_profile_resolution") or metadata.get("source_profile_resolution", "inferred_fallback") or "inferred_fallback")
        source_profile = first.get("source_profile", {}) if isinstance(first.get("source_profile", {}), dict) else metadata.get("source_profile", {})
        diagnostics.append(
            {
                "name": str(source_profile.get("canonical_name") or source_name).strip() or source_name,
                "kind": str(first.get("source_kind") or "provided"),
                "status": "inferred_ok" if group else "empty",
                "warning": "",
                "article_count": len(group),
                "used_registry_profile": bool(first.get("used_registry_profile", metadata.get("used_registry_profile", False))),
                "resolution_method": resolution_method,
                "date_enrichment_attempted": None,
                "date_enrichment_success_count": None,
                "date_fallback_count": None,
            }
        )
    return diagnostics


def build_source_health_summary(sources: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_sources = sources if isinstance(sources, list) else []
    normalized_events = events if isinstance(events, list) else []
    relationships = [
        relationship
        for event in normalized_events
        for relationship in (event.get("stock_relationships", []) if isinstance(event.get("stock_relationships", []), list) else [])
        if isinstance(relationship, dict)
    ]

    ok_source_count = sum(1 for source in normalized_sources if str(source.get("status") or "").strip().lower() == "ok")
    errored_source_count = sum(1 for source in normalized_sources if str(source.get("status") or "").strip().lower() == "error")
    empty_source_count = sum(1 for source in normalized_sources if str(source.get("status") or "").strip().lower() == "empty")
    warning_source_count = sum(1 for source in normalized_sources if str(source.get("warning") or "").strip())
    registry_backed_source_count = sum(1 for source in normalized_sources if bool(source.get("used_registry_profile")))
    fallback_source_count = sum(
        1
        for source in normalized_sources
        if not bool(source.get("used_registry_profile"))
        or str(source.get("resolution_method") or "").strip().lower() in {"inferred_fallback", "url_inference", "heuristic_fallback", "unknown"}
    )
    date_enrichment_success_count = sum(int(source.get("date_enrichment_success_count", 0) or 0) for source in normalized_sources)
    date_fallback_count = sum(int(source.get("date_fallback_count", 0) or 0) for source in normalized_sources)

    def event_warning_count(warning: str) -> int:
        return sum(1 for event in normalized_events if str(event.get("coverage_warning") or "").strip() == warning)

    return {
        "source_count": len(normalized_sources),
        "ok_source_count": ok_source_count,
        "fallback_source_count": fallback_source_count,
        "errored_source_count": errored_source_count,
        "empty_source_count": empty_source_count,
        "warning_source_count": warning_source_count,
        "registry_backed_source_count": registry_backed_source_count,
        "date_enrichment_success_count": date_enrichment_success_count,
        "date_fallback_count": date_fallback_count,
        "displayed_event_count": len(normalized_events),
        "relationship_count": len(relationships),
        "conflicted_relationship_count": sum(1 for relationship in relationships if bool(relationship.get("source_conflict"))),
        "independent_corroborated_relationship_count": sum(1 for relationship in relationships if str(relationship.get("corroboration_label") or "") == "independently_corroborated"),
        "weak_single_source_relationship_count": sum(1 for relationship in relationships if str(relationship.get("corroboration_label") or "") == "single_weak_source"),
        "syndicated_coverage_count": sum(int(relationship.get("syndicated_coverage_count", 0) or 0) for relationship in relationships),
        "stale_event_count": event_warning_count("stale_coverage"),
        "thin_event_count": event_warning_count("thin_source_coverage"),
        "duplicated_event_count": event_warning_count("duplicated_coverage"),
    }


def unpack_news_fetch_result(result: Any) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    if not isinstance(result, tuple):
        return [], ["News fetcher returned an invalid result shape."], []

    if len(result) == 3:
        articles, warnings, diagnostics = result
    elif len(result) == 2:
        articles, warnings = result
        diagnostics = summarize_source_diagnostics_from_articles(articles if isinstance(articles, list) else [])
    else:
        return [], ["News fetcher returned an unsupported tuple shape."], []

    normalized_articles = articles if isinstance(articles, list) else []
    normalized_warnings = warnings if isinstance(warnings, list) else [str(warnings)] if warnings else []
    normalized_diagnostics = diagnostics if isinstance(diagnostics, list) else []
    return normalized_articles, normalized_warnings, normalized_diagnostics


def fetch_source(
    source: dict[str, Any],
    include_diagnostic: bool = False,
) -> tuple[list[dict[str, Any]], str | None] | tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    try:
        response = requests.get(source["url"], timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
        response.raise_for_status()
        if source["kind"] == "rss":
            articles = parse_rss_items(source, response.text)
            if not articles:
                warning = f"{source['name']}: no RSS items extracted"
                if include_diagnostic:
                    return [], warning, build_source_diagnostic(source, status="empty", warning=warning)
                return [], warning
            if include_diagnostic:
                return articles, None, build_source_diagnostic(source, status="ok", articles=articles)
            return articles, None
        articles = parse_html_signal(source, response.text)
        if not articles:
            warning = f"{source['name']}: no article links extracted"
            if include_diagnostic:
                return [], warning, build_source_diagnostic(source, status="empty", warning=warning)
            return [], warning
        enriched_articles, date_stats = enrich_html_article_dates(articles)
        if include_diagnostic:
            return enriched_articles, None, build_source_diagnostic(source, status="ok", articles=enriched_articles, date_stats=date_stats)
        return enriched_articles, None
    except Exception as exc:  # pragma: no cover - network failures are expected in some environments
        warning = f"{source['name']}: {exc}"
        if include_diagnostic:
            return [], warning, build_source_diagnostic(source, status="error", warning=warning)
        return [], warning


def fetch_news_bundle() -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    articles: list[dict[str, Any]] = []
    warnings: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(NEWS_SOURCES))) as pool:
        futures = {pool.submit(fetch_source, source, True): source for source in NEWS_SOURCES}
        for future in as_completed(futures):
            source_articles, warning, source_diagnostic = future.result()
            if warning:
                warnings.append(warning)
            diagnostics.append(source_diagnostic)
            articles.extend(source_articles)

    articles = [article for article in articles if is_relevant_article(article)]
    if not articles:
        warnings.append("No live news articles available right now.")
    diagnostics.sort(key=lambda item: str(item.get("name") or ""))
    return articles, warnings, diagnostics


def dedupe_articles(articles: list[dict[str, Any]], window: str = DEFAULT_EVENT_WINDOW) -> list[dict[str, Any]]:
    filtered = [article for article in articles if not is_stale_article(article.get("published_at"), window)]
    filtered.sort(key=lambda article: article.get("published_at") or now_wib(), reverse=True)
    return merge_duplicate_articles(filtered)


def analyze_sentiment(text: str) -> tuple[str, float, float]:
    """Sentiment analysis using IndoBERT (with keyword fallback)."""
    return analyze_sentiment_ml(text[:512])


def classify_categories(text: str) -> list[str]:
    hits = []
    for category, keywords in CATEGORY_RULES.items():
        if any(keyword in text for keyword in keywords):
            hits.append(category)
    return hits[:4]


def extract_entities(text: str) -> list[str]:
    """Extract named entities using IndoBERT NER (with regex fallback)."""
    return extract_entities_ml(text[:512])


def sector_matches(text: str) -> set[str]:
    matches: set[str] = set()
    for sector, keywords in SECTOR_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            matches.add(sector)
    return matches


def detect_policy_themes(text: str) -> list[dict[str, Any]]:
    themes: list[dict[str, Any]] = []
    for name, config in POLICY_THEMES.items():
        hit_count = sum(1 for keyword in config["keywords"] if keyword in text)
        if hit_count:
            themes.append(
                {
                    "name": name,
                    "keyword_hits": hit_count,
                    "sectors": list(config["sectors"]),
                    "channel": config["channel"],
                    "exposure_type": config["exposure_type"],
                }
            )
    themes.sort(key=lambda item: item["keyword_hits"], reverse=True)
    return themes[:4]


def policy_specificity_score(categories: list[str], themes: list[dict[str, Any]], text: str) -> float:
    score = 1.0
    score += min(1.5, 0.55 * len(categories))
    score += min(1.0, 0.35 * len(themes))
    if any(keyword in text for keyword in ["perpres", "perppu", "ruu", "uu ", "apbn", "anggaran", "tarif", "kuota", "izin"]):
        score += 0.8
    return min(5.0, score)


