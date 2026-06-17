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

    # Use higher floors so moderate factors produce actionable scores.
    # Blend: event quality (impact × confidence × source) with corroboration boost.
    event_quality = normalized_impact * max(confidence, 0.3) * max(source_conf, 0.3)
    corroboration_boost = 0.5 + 0.5 * corroboration  # range: 0.5-1.0

    score = _clamp(
        event_quality
        * corroboration_boost
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

    Core indicators (directional alignment, not extreme levels):
    1. RSI: BUY if < 55, SELL if > 45
    2. MACD histogram: BUY if > 0, SELL if < 0
    3. Bollinger %B: BUY if < 0.3, SELL if > 0.7
    4. Volume spike: confirm if is_spike

    Returns dict: confirm_count, total, score (0-1), details.
    """
    direction = str(stock.get("impact_direction", "neutral") or "neutral")
    if direction not in ("positive", "negative"):
        return {"confirm_count": 0, "total": 0, "score": 0.0, "details": []}

    is_buy = direction == "positive"
    confirmations: list[str] = []
    total = 0

    # 1. RSI — directional alignment (not extreme oversold/overbought)
    rsi = stock.get("rsi_value")
    if rsi is not None:
        total += 1
        if is_buy and rsi < 55:
            confirmations.append("RSI directional buy")
        elif not is_buy and rsi > 45:
            confirmations.append("RSI directional sell")

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

    # 3. Bollinger %B — wider band for directional confirmation
    bb = stock.get("bollinger")
    if isinstance(bb, dict) and bb.get("percent_b") is not None:
        total += 1
        pct_b = bb["percent_b"]
        if is_buy and pct_b < 0.3:
            confirmations.append("Bollinger %B near lower band")
        elif not is_buy and pct_b > 0.7:
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

    # Bollinger squeeze + directional event → 1d (breakout imminent)
    bb = stock.get("bollinger") or {}
    if bb.get("squeeze") and ev_score > 0.1:
        return "1d"

    if ev_score >= 0.5 and total >= 4 and confirm >= 3:
        return "1d"
    if ev_score >= 0.3 and total >= 4 and confirm >= 2:
        return "7d"

    return "7d"


def detect_technical_setup(
    stock: dict[str, Any],
    sector_avg_rsi: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """Detect standalone technical setups that don't need event catalyst.

    Returns a dict with action, reasons, and score if a setup is found.
    Returns None if no setup detected.
    """
    rsi = stock.get("rsi_value")
    bb = stock.get("bollinger") or {}
    bb_pct_b = bb.get("percent_b")
    macd = stock.get("macd") or {}
    hist = macd.get("histogram") if isinstance(macd, dict) else None
    vol = stock.get("volume_spike") or {}
    is_spike = vol.get("is_spike", False)
    spike_ratio = vol.get("spike_ratio", 0)

    reasons: list[str] = []
    score = 0.0

    # Oversold bounce candidate
    if rsi is not None and rsi < 35:
        reasons.append(f"RSI {rsi:.0f} oversold — bounce candidate")
        score += 0.3
    if bb_pct_b is not None and bb_pct_b < 0.2:
        reasons.append("Near lower Bollinger Band — potential bounce")
        score += 0.2

    # Momentum breakout candidate
    if rsi is not None and rsi > 60:
        reasons.append(f"RSI {rsi:.0f} bullish momentum")
        score += 0.2
    if hist is not None and hist > 0:
        reasons.append("MACD histogram positive — upward momentum")
        score += 0.15
    if is_spike:
        reasons.append(f"Volume spike {spike_ratio:.1f}x — institutional interest")
        score += 0.25

    # Bollinger near upper band
    if bb_pct_b is not None and bb_pct_b > 0.8:
        reasons.append("Near upper Bollinger Band — potential breakout or reversal")
        score += 0.15

    # Sector-relative RSI boost
    if sector_avg_rsi:
        sector_boost = compute_sector_rsi_boost(stock, sector_avg_rsi)
        if sector_boost > 0:
            score += sector_boost
            reasons.append(f"RSI below sector average — relative oversold")

    # Volume trend boost
    vol_boost = compute_volume_trend_boost(vol)
    if vol_boost > 0:
        score += vol_boost
        reasons.append(f"Volume above average — rising interest")

    # Support/resistance proximity boost
    sr_boost, sr_reasons = compute_sr_proximity_boost(stock)
    if sr_boost > 0:
        score += sr_boost
        reasons.extend(sr_reasons)

    # Bollinger squeeze detection
    squeeze_boost, squeeze_reasons = detect_bollinger_squeeze(stock)
    if squeeze_boost > 0:
        score += squeeze_boost
        reasons.extend(squeeze_reasons)

    if score >= 0.2 and reasons:
        return {
            "action": "WATCH",
            "score": min(score, 1.0),
            "reasons": reasons,
        }
    return None


def compute_participation_score(stock: dict[str, Any]) -> dict[str, Any]:
    """Estimate participation quality from volume/traded-value proxies."""
    reasons: list[str] = []
    score = 0.0

    vol = stock.get("volume_spike") or {}
    spike_ratio = float(vol.get("spike_ratio", 0) or 0)
    if spike_ratio >= 2.0:
        score += 0.65
        reasons.append(f"Volume expansion {spike_ratio:.1f}x vs average")
    elif spike_ratio >= 1.4:
        score += 0.45
        reasons.append(f"Volume above average {spike_ratio:.1f}x")
    elif spike_ratio >= 1.15:
        score += 0.20
        reasons.append(f"Participation improving {spike_ratio:.1f}x volume")

    traded_value = float(stock.get("value_traded_estimate", 0) or 0)
    if traded_value >= 2_000_000_000:
        score += 0.20
        reasons.append("High traded-value participation")
    elif traded_value >= 500_000_000:
        score += 0.10
        reasons.append("Healthy traded-value participation")

    score = min(score, 1.0)
    label = "low"
    if score >= 0.60:
        label = "high"
    elif score >= 0.35:
        label = "medium"

    return {
        "score": round(score, 4),
        "label": label,
        "spike_ratio": round(spike_ratio, 4),
        "value_traded_estimate": traded_value,
        "reasons": reasons,
    }


def detect_breakout_continuation(stock: dict[str, Any]) -> dict[str, Any] | None:
    """Detect bullish continuation setups near/through resistance."""
    price = float(stock.get("price", 0) or 0)
    if price <= 0:
        return None

    rsi = stock.get("rsi_value")
    bb = stock.get("bollinger") or {}
    pct_b = float(bb.get("percent_b", 0) or 0)
    trend = (stock.get("trend") or {}).get("trend")
    macd = stock.get("macd") or {}
    hist = macd.get("histogram") if isinstance(macd, dict) else macd
    sr = stock.get("support_resistance") or {}
    resistances = [float(level) for level in sr.get("resistance", []) if level]

    near_resistance = False
    breakout_level = None
    for level in resistances:
        if price >= level * 0.98:
            near_resistance = True
            breakout_level = level
            break

    if hist is None or hist <= 0:
        return None
    if rsi is None or not (55 <= float(rsi) <= 75):
        return None
    if not near_resistance and pct_b < 0.75:
        return None

    participation = compute_participation_score(stock)
    reasons = ["Bullish breakout continuation setup"]
    if breakout_level is not None:
        reasons.append(f"Price testing resistance {breakout_level:.0f}")
    reasons.extend(participation["reasons"])

    close_above_resistance = stock.get("close_above_resistance")
    if close_above_resistance is None:
        close_above_resistance = bool(breakout_level is not None and price > breakout_level)
    price_above_sma20 = stock.get("price_above_sma20")
    price_above_sma50 = stock.get("price_above_sma50")
    return_1d = stock.get("return_1d")
    return_3d = stock.get("return_3d")
    momentum_ok = True
    if return_1d is not None and float(return_1d) <= 0:
        momentum_ok = False
    if return_3d is not None and float(return_3d) < 0:
        momentum_ok = False

    trigger_complete = bool(
        participation["score"] >= 0.45
        and close_above_resistance
        and (price_above_sma20 is not False)
        and (price_above_sma50 is not False)
        and momentum_ok
    )
    setup_score = 0.55
    if trend == "bullish":
        setup_score += 0.10
        reasons.append("Trend structure is bullish")
    if pct_b >= 0.8:
        setup_score += 0.10
    if close_above_resistance:
        setup_score += 0.08
        reasons.append("Resistance breakout is confirmed on close")
    else:
        reasons.append("Needs confirmed close above resistance for BUY")
    setup_score += min(participation["score"], 0.25)

    return {
        "setup_type": "breakout_continuation",
        "setup_score": round(min(setup_score, 1.0), 4),
        "trigger_complete": trigger_complete,
        "preferred_horizon": "1d" if participation["score"] >= 0.60 and close_above_resistance else "3d",
        "participation": participation,
        "reasons": reasons,
    }


def detect_support_rebound(stock: dict[str, Any]) -> dict[str, Any] | None:
    """Detect bullish rebounds stabilising near support."""
    price = float(stock.get("price", 0) or 0)
    if price <= 0:
        return None

    rsi = stock.get("rsi_value")
    if rsi is None or float(rsi) > 40:
        return None

    macd = stock.get("macd") or {}
    hist = macd.get("histogram") if isinstance(macd, dict) else macd
    if hist is None or hist <= 0:
        return None

    bb = stock.get("bollinger") or {}
    pct_b = float(bb.get("percent_b", 0) or 0)
    sr = stock.get("support_resistance") or {}
    supports = [float(level) for level in sr.get("support", []) if level]
    if not supports:
        return None

    nearest_distance = min(abs(price - level) / price for level in supports)
    if nearest_distance > 0.05 and pct_b > 0.3:
        return None

    participation = compute_participation_score(stock)
    reasons = ["Support rebound setup forming"]
    reasons.append(f"Price holding near support ({nearest_distance*100:.1f}% away)")
    reasons.extend(participation["reasons"])

    reclaim_from_support = stock.get("reclaim_from_support")
    if reclaim_from_support is None:
        reclaim_from_support = nearest_distance <= 0.035 or pct_b <= 0.2
    return_1d = stock.get("return_1d")
    return_3d = stock.get("return_3d")
    short_term_recovery = True
    if return_1d is not None and float(return_1d) <= 0:
        short_term_recovery = False
    if return_3d is not None and float(return_3d) < -0.5:
        short_term_recovery = False

    setup_score = 0.50 + (0.10 if pct_b < 0.25 else 0.0) + min(participation["score"], 0.15)
    trigger_complete = bool(nearest_distance <= 0.05 and pct_b <= 0.3 and reclaim_from_support and short_term_recovery)
    if reclaim_from_support:
        reasons.append("Support reclaim is confirmed")
    else:
        reasons.append("Needs clear reclaim from support before BUY")
    if short_term_recovery:
        reasons.append("Momentum recovery confirmed")
    else:
        reasons.append("Momentum recovery still weak")

    return {
        "setup_type": "support_rebound",
        "setup_score": round(min(setup_score, 1.0), 4),
        "trigger_complete": trigger_complete,
        "preferred_horizon": "7d" if participation["score"] >= 0.20 and trigger_complete else "14d",
        "participation": participation,
        "reasons": reasons,
    }


def detect_short_term_setup(
    stock: dict[str, Any],
    sector_avg_rsi: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """Return the strongest bullish short-term setup, if any."""
    candidates = [
        detect_breakout_continuation(stock),
        detect_support_rebound(stock),
    ]
    candidates = [c for c in candidates if c]
    if not candidates:
        return None
    return max(candidates, key=lambda item: float(item.get("setup_score", 0) or 0))


def classify_signal(
    stock: dict[str, Any],
    sector_avg_rsi: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Full signal classification: action, horizon, tier, entry/SL/TP, reasons.

    This is the main entry point for the trading signal decision layer.
    Takes a stock dict from build_refresh_payload and returns a trading_signal.
    """
    price = float(stock.get("price", 0) or 0)
    atr = float(stock.get("atr_value", 0) or 0)
    if atr <= 0 and price > 0:
        atr = price * 0.02  # fallback: 2% of price

    # Step 1: Event score
    ev = compute_event_score(stock)
    ev_score = ev["score"]
    direction = ev["direction"]

    # Step 2: Technical confirmation
    tech = compute_technical_confirmation(stock)
    tech_score = tech["score"]
    confirm_count = tech["confirm_count"]
    tech_total = tech["total"]

    # Step 3: Composite signal strength
    calibration = get_calibration_multiplier(stock)
    signal_strength = round(
        ev_score * 0.55 + tech_score * 0.35 + calibration * 0.10, 4
    )
    setup = detect_short_term_setup(stock, sector_avg_rsi)
    participation = compute_participation_score(stock)
    if setup:
        signal_strength = round(max(signal_strength, float(setup.get("setup_score", 0) or 0)), 4)

    # Step 3b: Context boosts (S/R proximity, Bollinger squeeze)
    sr_boost, sr_reasons = compute_sr_proximity_boost(stock)
    squeeze_boost, squeeze_reasons = detect_bollinger_squeeze(stock)
    context_boost = sr_boost + squeeze_boost
    if context_boost > 0:
        signal_strength = round(min(signal_strength + context_boost, 1.0), 4)

    # Step 4: Determine action
    reasons: list[str] = []
    has_conflict = bool(stock.get("source_conflict", False))

    if setup:
        reasons.extend(setup.get("reasons", []))
        setup_type = str(setup.get("setup_type", "") or "")
        trigger_complete = bool(setup.get("trigger_complete", False))
        part_score = float((setup.get("participation") or {}).get("score", 0) or 0)
        if setup_type == "breakout_continuation":
            if trigger_complete and part_score >= 0.45:
                action = "BUY"
            else:
                action = "WATCH"
                reasons.append("Participation/trigger not strong enough for breakout BUY")
        elif setup_type == "support_rebound":
            if trigger_complete:
                action = "BUY"
            else:
                action = "WATCH"
                reasons.append("Rebound is forming but not fully confirmed")
        else:
            action = "WATCH"
    elif direction not in ("positive", "negative"):
        # No event direction — check standalone technical setups
        tech_setup = detect_technical_setup(stock, sector_avg_rsi)
        if tech_setup:
            action = "WATCH"
            signal_strength = max(signal_strength, tech_setup["score"])
            reasons = tech_setup["reasons"]
        elif ev_score > 0.1:
            action = "WATCH"
            reasons.append(f"Direction is {direction} — no directional conviction")
        else:
            action = "IGNORE"
            reasons.append(f"Direction is {direction}, no event or technical setup")
    elif direction == "positive":
        if sr_boost > 0 or squeeze_boost > 0:
            action = "WATCH"
            reasons.append("Positive context with early technical location setup")
        elif ev_score >= 0.20:
            action = "WATCH"
            reasons.append("Positive event/news context but no bullish technical trigger")
        else:
            action = "IGNORE"
            reasons.append("Positive event too weak without technical trigger")
    elif signal_strength < 0.30:
        action = "IGNORE"
        reasons.append(f"Signal strength {signal_strength:.2f} below 0.30 minimum")
    elif signal_strength < 0.35:
        action = "WATCH"
        reasons.append(f"Signal strength {signal_strength:.2f} below 0.35 action threshold")
    else:
        if direction == "positive":
            if confirm_count < 2:
                action = "WATCH"
                reasons.append(f"Only {confirm_count}/{tech_total} tech confirmations, need 2+ for BUY")
            else:
                action = "BUY"
        else:  # negative
            if confirm_count < 3:
                action = "WATCH"
                reasons.append(f"Only {confirm_count}/{tech_total} tech confirmations, need 3+ for SELL (strict mode)")
            else:
                action = "SELL"

    # Step 5: Horizon
    time_horizon = str(setup.get("preferred_horizon", "") or "") if setup else ""
    if not time_horizon:
        time_horizon = infer_time_horizon(stock, ev, tech)

    # Step 6: Tier
    signal_tier = "D"
    if action == "BUY" and signal_strength >= 0.70 and confirm_count >= 3 and not has_conflict:
        signal_tier = "A"
    elif action == "BUY" and signal_strength >= 0.60 and confirm_count >= 2 and not has_conflict:
        signal_tier = "B"
    elif action in ("BUY", "SELL") and signal_strength >= 0.35:
        signal_tier = "C"
    elif action == "WATCH":
        signal_tier = "C"

    # Step 7: Entry / SL / TP
    entry_price = price
    stop_loss = None
    take_profit = None
    if action == "BUY" and price > 0:
        stop_loss = round(price - 1.5 * atr, 2)
        take_profit = round(price + 3.0 * atr, 2)
    elif action == "SELL" and price > 0:
        stop_loss = round(price + 1.5 * atr, 2)
        take_profit = round(price - 3.0 * atr, 2)

    # Step 8: Reasons enrichment
    if confirm_count > 0 and not reasons:
        reasons.extend(tech["details"])
    elif confirm_count > 0:
        # Add tech details that aren't already in reasons
        for detail in tech["details"]:
            if detail not in reasons:
                reasons.append(detail)
    if has_conflict:
        reasons.append("Source conflict detected — reduced confidence")
    # Add context boost reasons (S/R proximity, squeeze)
    for r in sr_reasons + squeeze_reasons:
        if r not in reasons:
            reasons.append(r)

    # Step 9: Invalidation
    invalidation = ""
    if action == "BUY":
        invalidation = f"Close below {stop_loss} or direction reversal"
    elif action == "SELL":
        invalidation = f"Close above {stop_loss} or direction reversal"

    # Determine signal type
    if ev_score > 0 and tech_score > 0:
        signal_type = "composite"
    elif ev_score > 0:
        signal_type = "event"
    else:
        signal_type = "technical"

    return {
        "action": action,
        "time_horizon": time_horizon,
        "signal_tier": signal_tier,
        "signal_type": signal_type,
        "signal_strength": signal_strength,
        "event_score": ev_score,
        "tech_score": tech_score,
        "tech_confirmation_count": confirm_count,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "reasons": reasons,
        "invalidation": invalidation,
        "setup_type": setup.get("setup_type") if setup else None,
        "participation_score": participation.get("score", 0.0),
        "participation_label": participation.get("label", "low"),
    }


_ACTION_ORDER = {"BUY": 0, "SELL": 1, "WATCH": 2, "IGNORE": 3}
_TIER_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}
_HORIZON_ORDER = {"1d": 0, "3d": 1, "7d": 2, "14d": 3, "30d": 4}
_SETUP_ORDER = {"breakout_continuation": 0, "support_rebound": 1}


def rank_trade_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort signals by action/tier, then execution quality for short-term trades."""
    return sorted(
        signals,
        key=lambda s: (
            _ACTION_ORDER.get(s.get("action", "IGNORE"), 4),
            _TIER_ORDER.get(s.get("signal_tier", "D"), 4),
            -float(s.get("signal_strength", 0) or 0),
            -float(s.get("participation_score", 0) or 0),
            _HORIZON_ORDER.get(str(s.get("time_horizon", "30d") or "30d"), 9),
            _SETUP_ORDER.get(str(s.get("setup_type", "") or ""), 9),
        ),
    )


def get_calibration_multiplier(stock: dict[str, Any]) -> float:
    """Look up calibration multiplier from backtest data.

    Combines source type and category calibration. Returns 1.0 if
    insufficient sample size.
    """
    try:
        from backend.backtest import compute_source_accuracy, compute_category_calibration
        source_cal = compute_source_accuracy(window_days=30, min_samples=5)
        cat_cal = compute_category_calibration(window_days=30, min_samples=5)
    except Exception:
        return 1.0

    multiplier = 1.0

    # Source type calibration
    source_type = str(stock.get("source_tier", "") or stock.get("article_source_type", "") or "")
    if source_type and source_type in source_cal:
        multiplier *= source_cal[source_type]["calibration_multiplier"]

    # Category calibration — use matched_policy_channels and policy_channel
    if cat_cal:
        categories_to_check: set[str] = set()
        # From policy_channel field
        pc = stock.get("policy_channel", "")
        if pc:
            categories_to_check.add(str(pc).upper())
        # From matched_policy_channels list
        for ch in stock.get("matched_policy_channels", []):
            if isinstance(ch, dict):
                categories_to_check.add(str(ch.get("channel", "")).upper())
        # Apply best category multiplier
        cat_multipliers = []
        for cat in categories_to_check:
            if cat in cat_cal:
                cat_multipliers.append(cat_cal[cat]["calibration_multiplier"])
        if cat_multipliers:
            multiplier *= max(cat_multipliers)

    return _clamp(multiplier, 0.5, 1.5)


# ── Signal Quality Upgrade Functions ──────────────────────────────


def compute_sector_rsi_boost(
    stock: dict[str, Any],
    sector_avg_rsi: dict[str, float],
) -> float:
    """Boost strength when stock RSI is significantly below sector average.

    A stock at RSI 25 in a sector averaging RSI 45 is a stronger bounce
    candidate than one where the whole sector is oversold.

    Returns 0.0-0.15 boost value.
    """
    rsi = stock.get("rsi_value")
    sector = str(stock.get("sector", "") or "")
    if rsi is None or not sector or sector not in sector_avg_rsi:
        return 0.0

    avg = sector_avg_rsi[sector]
    delta = avg - rsi  # positive = stock is more oversold than peers

    if delta >= 20:
        return 0.15
    elif delta >= 10:
        return 0.10
    elif delta >= 5:
        return 0.05
    return 0.0


def compute_volume_trend_boost(vol: dict[str, Any]) -> float:
    """Boost when current volume is above average (rising interest).

    Uses spike_ratio (current_volume / avg_volume).
    Returns 0.0-0.10 boost.
    """
    if not vol or not isinstance(vol, dict):
        return 0.0
    spike_ratio = float(vol.get("spike_ratio", 0) or 0)
    if spike_ratio >= 2.0:
        return 0.10
    elif spike_ratio >= 1.3:
        return 0.05
    return 0.0


def apply_signal_decay(signal: dict[str, Any], days_since_signal: int) -> None:
    """Apply decay to signal strength based on age.

    WATCH signals lose 10% per day after day 1.
    BUY/SELL signals are not decayed (they have entry/SL/TP).
    Signals below 0.15 strength are auto-downgraded to IGNORE.
    """
    if signal.get("action") != "WATCH":
        return
    if days_since_signal <= 1:
        return
    decay = min(0.5, (days_since_signal - 1) * 0.10)
    signal["signal_strength"] = round(
        signal["signal_strength"] * (1 - decay), 4
    )
    if signal["signal_strength"] < 0.15:
        signal["action"] = "IGNORE"
        signal["reasons"] = signal.get("reasons", []) + [
            f"Signal stale ({days_since_signal}d) — auto-decayed"
        ]


def deduplicate_by_sector(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the strongest WATCH signal per sector.

    BUY/SELL signals are always kept (they have entry/SL/TP).
    This prevents concentrated risk from multiple signals in the same sector.
    """
    best_by_sector: dict[str, dict[str, Any]] = {}
    always_keep: list[dict[str, Any]] = []

    for sig in signals:
        if sig.get("action") in ("BUY", "SELL"):
            always_keep.append(sig)
            continue
        sector = sig.get("sector", "unknown")
        if sector not in best_by_sector or (
            sig.get("signal_strength", 0) > best_by_sector[sector].get("signal_strength", 0)
        ):
            best_by_sector[sector] = sig

    return always_keep + sorted(
        best_by_sector.values(),
        key=lambda s: s.get("signal_strength", 0),
        reverse=True,
    )


def apply_market_regime(signal: dict[str, Any], market_index: dict[str, Any]) -> None:
    """Downgrade BUY signals when IHSG is in a clear downtrend.

    Uses IHSG price relative to its 5-point SMA and daily change.
    Only affects BUY signals — WATCH and SELL are unchanged.
    """
    if signal.get("action") != "BUY":
        return
    change = float(market_index.get("change_pct", 0) or 0)
    series = market_index.get("series", [])
    if len(series) >= 5:
        sma5 = sum(series[-5:]) / len(series[-5:])
        last = series[-1]
        if last < sma5 and change < -1.0:
            signal["action"] = "WATCH"
            signal["reasons"] = signal.get("reasons", []) + [
                f"IHSG downtrend ({change:+.1f}%) — BUY suppressed"
            ]


def compute_sector_avg_rsi(stocks: list[dict[str, Any]]) -> dict[str, float]:
    """Compute average RSI per sector from a list of stock dicts.

    Returns dict: sector -> average RSI (only sectors with >= 2 stocks with RSI data).
    """
    sector_rsis: dict[str, list[float]] = {}
    for s in stocks:
        rsi = s.get("rsi_value")
        sector = str(s.get("sector", "") or "")
        if rsi is not None and sector:
            sector_rsis.setdefault(sector, []).append(float(rsi))
    return {
        sector: sum(rsis) / len(rsis)
        for sector, rsis in sector_rsis.items()
        if len(rsis) >= 2
    }


# ── Phase 6: Support/Resistance & Squeeze Detection ──────────────


def compute_sr_proximity_boost(stock: dict[str, Any]) -> tuple[float, list[str]]:
    """Boost signal strength when price is near support or resistance.

    Near support + positive direction → bounce play (BUY boost).
    Near resistance + negative direction → breakdown play (SELL boost).

    For standalone technical setups (no event), uses RSI as direction proxy:
    RSI < 40 → buy-side (near support is bullish), RSI > 60 → sell-side.

    Returns (boost 0.0-0.15, reasons list).
    """
    price = float(stock.get("price", 0) or 0)
    if price <= 0:
        return 0.0, []

    sr = stock.get("support_resistance") or {}
    supports = sr.get("support", [])
    resistances = sr.get("resistance", [])
    direction = str(stock.get("impact_direction", "neutral") or "neutral")

    # For standalone technical setups, infer direction from RSI
    if direction == "neutral":
        rsi = stock.get("rsi_value")
        if rsi is not None:
            if rsi < 40:
                direction = "positive"  # oversold → expect bounce
            elif rsi > 60:
                direction = "negative"  # overbought → expect pullback

    reasons: list[str] = []
    boost = 0.0

    # Check proximity to support levels (for BUY / positive direction)
    if direction == "positive" and supports:
        for level in supports:
            if level and level > 0:
                distance_pct = abs(price - level) / price
                if distance_pct <= 0.02:  # within 2%
                    boost = max(boost, 0.15)
                    reasons.append(f"Price {price:.0f} near support {level:.0f} ({distance_pct*100:.1f}%)")
                elif distance_pct <= 0.05:  # within 5%
                    boost = max(boost, 0.08)
                    reasons.append(f"Price {price:.0f} approaching support {level:.0f} ({distance_pct*100:.1f}%)")

    # Check proximity to resistance levels (for SELL / negative direction)
    if direction == "negative" and resistances:
        for level in resistances:
            if level and level > 0:
                distance_pct = abs(price - level) / price
                if distance_pct <= 0.02:
                    boost = max(boost, 0.15)
                    reasons.append(f"Price {price:.0f} near resistance {level:.0f} ({distance_pct*100:.1f}%)")
                elif distance_pct <= 0.05:
                    boost = max(boost, 0.08)
                    reasons.append(f"Price {price:.0f} approaching resistance {level:.0f} ({distance_pct*100:.1f}%)")

    return boost, reasons


def detect_bollinger_squeeze(stock: dict[str, Any]) -> tuple[float, list[str]]:
    """Detect Bollinger Band squeeze — low volatility preceding breakout.

    A squeeze is when bandwidth is below its recent average, indicating
    consolidation. Combined with directional event → high-conviction 1d signal.

    Returns (boost 0.0-0.20, reasons list).
    """
    bb = stock.get("bollinger") or {}
    is_squeeze = bb.get("squeeze", False)
    bandwidth = float(bb.get("bandwidth", 0) or 0)
    pct_b = float(bb.get("percent_b", 0.5) or 0.5)
    direction = str(stock.get("impact_direction", "neutral") or "neutral")

    reasons: list[str] = []
    boost = 0.0

    if is_squeeze:
        reasons.append(f"Bollinger squeeze (BW {bandwidth:.3f}) — breakout imminent")
        boost += 0.10

        # If price is near lower band + positive direction → strong BUY setup
        if direction == "positive" and pct_b < 0.3:
            boost += 0.10
            reasons.append("Squeeze near lower band + positive direction — high-conviction breakout")
        # If price is near upper band + negative direction → strong SELL setup
        elif direction == "negative" and pct_b > 0.7:
            boost += 0.10
            reasons.append("Squeeze near upper band + negative direction — breakdown setup")

    return boost, reasons
