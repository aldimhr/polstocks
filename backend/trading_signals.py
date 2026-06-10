"""Trading signal decision layer.

Pure functions that convert stock payload dicts into trading signals
with action (BUY/SELL/WATCH/IGNORE), time horizon (1d/7d/30d),
confidence tier (A/B/C/D), entry/SL/TP, and reasons.

Does NOT touch the database or send alerts — that's the caller's job.
"""
from __future__ import annotations

from typing import Any


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def compute_event_score(stock: dict[str, Any]) -> dict[str, Any]:
    """Compute a normalised 0-1 event score from the stock payload.

    Returns dict with keys: score (0-1), direction, factors (debug dict).
    """
    raw_impact = abs(float(stock.get("impact_score", 0) or 0))
    normalized_impact = _clamp(raw_impact / 10.0)

    confidence = float(stock.get("relationship_confidence", 0) or 0)
    source_conf = float(stock.get("source_confidence", 0) or 0)
    corroboration = min(float(stock.get("corroboration_count", 0) or 0) / 3.0, 1.0)
    recency = float(stock.get("recency_weight", 1.0) or 1.0)
    conflict = bool(stock.get("source_conflict", False))
    conflict_penalty = 0.7 if conflict else 1.0

    direction = str(stock.get("impact_direction", "neutral") or "neutral")

    score = _clamp(
        normalized_impact
        * max(confidence, 0.1)
        * max(source_conf, 0.1)
        * max(corroboration, 0.1)
        * recency
        * conflict_penalty
    )

    return {
        "score": round(score, 4),
        "direction": direction,
        "factors": {
            "normalized_impact": round(normalized_impact, 4),
            "confidence": round(confidence, 4),
            "source_confidence": round(source_conf, 4),
            "corroboration": round(corroboration, 4),
            "recency": round(recency, 4),
            "conflict_penalty": conflict_penalty,
        },
    }


def compute_technical_confirmation(stock: dict[str, Any]) -> dict[str, Any]:
    """Count how many core technical indicators agree with the stock's direction.

    Core indicators:
    1. RSI: BUY if < 40, SELL if > 60
    2. MACD histogram: BUY if > 0, SELL if < 0
    3. Bollinger %B: BUY if < 0.2, SELL if > 0.8
    4. Volume spike: confirm if is_spike AND direction aligns

    Returns dict: confirm_count, total, score (0-1), details.
    """
    direction = str(stock.get("impact_direction", "neutral") or "neutral")
    if direction not in ("positive", "negative"):
        return {"confirm_count": 0, "total": 0, "score": 0.0, "details": []}

    is_buy = direction == "positive"
    confirmations: list[str] = []
    total = 0

    # 1. RSI
    rsi = stock.get("rsi_value")
    if rsi is not None:
        total += 1
        if is_buy and rsi < 40:
            confirmations.append("RSI oversold")
        elif not is_buy and rsi > 60:
            confirmations.append("RSI overbought")

    # 2. MACD histogram
    macd = stock.get("macd")
    hist = None
    if isinstance(macd, dict):
        hist = macd.get("histogram")
    elif isinstance(macd, (int, float)):
        hist = macd
    if hist is not None:
        total += 1
        if is_buy and hist > 0:
            confirmations.append("MACD histogram positive")
        elif not is_buy and hist < 0:
            confirmations.append("MACD histogram negative")

    # 3. Bollinger %B
    bb = stock.get("bollinger")
    if isinstance(bb, dict) and bb.get("percent_b") is not None:
        total += 1
        pct_b = bb["percent_b"]
        if is_buy and pct_b < 0.2:
            confirmations.append("Bollinger %B near lower band")
        elif not is_buy and pct_b > 0.8:
            confirmations.append("Bollinger %B near upper band")

    # 4. Volume spike
    vol = stock.get("volume_spike")
    if isinstance(vol, dict) and vol.get("is_spike") is not None:
        total += 1
        if vol.get("is_spike"):
            confirmations.append("Volume spike")

    score = len(confirmations) / total if total > 0 else 0.0
    return {
        "confirm_count": len(confirmations),
        "total": total,
        "score": round(score, 4),
        "details": confirmations,
    }


def infer_time_horizon(
    stock: dict[str, Any],
    event_score: dict[str, Any],
    tech: dict[str, Any],
) -> str:
    """Determine signal time horizon: '1d', '7d', or '30d'.

    Rules:
    - event_stage == "breaking" → 1d
    - event_stage == "established" → 30d
    - event_score >= 0.5 AND tech confirm >= 3/4 → 1d (strong setup, act fast)
    - event_score >= 0.3 AND tech confirm >= 2/4 → 7d (developing swing)
    - else → 7d default
    """
    stage = str(stock.get("event_stage", "") or "")
    ev_score = float(event_score.get("score", 0) or 0)
    confirm = int(tech.get("confirm_count", 0) or 0)
    total = int(tech.get("total", 0) or 0)

    if stage == "breaking":
        return "1d"
    if stage == "established":
        return "30d"

    if ev_score >= 0.5 and total >= 4 and confirm >= 3:
        return "1d"
    if ev_score >= 0.3 and total >= 4 and confirm >= 2:
        return "7d"

    return "7d"
