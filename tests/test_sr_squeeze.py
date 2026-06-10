"""B3 + B4 regression tests: support/resistance proximity and Bollinger squeeze.

All tests are pure (no DB, no network) and exercise:
  - compute_sr_proximity_boost: price near support/edge cases
  - detect_bollinger_squeeze: squeeze flag + %B alignment
  - classify_signal: integration — boosted strength/tier/horizon
"""
from __future__ import annotations

import pytest

from backend.trading_signals import (
    classify_signal,
    compute_sr_proximity_boost,
    detect_bollinger_squeeze,
    infer_time_horizon,
)


# ── Shared helpers ──────────────────────────────────────────────

def _base_positive_stock(**overrides):
    """Strong positive stock that produces a baseline BUY signal."""
    base = {
        "ticker": "TEST.JK",
        "price": 1000,
        "impact_score": 7.0,
        "impact_direction": "positive",
        "relationship_confidence": 0.8,
        "source_confidence": 0.8,
        "corroboration_count": 3,
        "recency_weight": 1.0,
        "source_conflict": False,
        "event_stage": "developing",
        "rsi_value": 35.0,
        "macd": {"histogram": 0.5},
        "bollinger": {"percent_b": 0.15, "squeeze": False, "bandwidth": 0.05},
        "volume_spike": {"is_spike": True, "spike_ratio": 2.0},
        "atr_value": 50.0,
        "support_resistance": {"support": [], "resistance": []},
    }
    base.update(overrides)
    return base


def _base_negative_stock(**overrides):
    """Strong negative stock that produces a baseline SELL signal."""
    base = {
        "ticker": "TEST.JK",
        "price": 1000,
        "impact_score": 7.0,
        "impact_direction": "negative",
        "relationship_confidence": 0.8,
        "source_confidence": 0.8,
        "corroboration_count": 3,
        "recency_weight": 1.0,
        "source_conflict": False,
        "event_stage": "developing",
        "rsi_value": 75.0,
        "macd": {"histogram": -0.5},
        "bollinger": {"percent_b": 0.85, "squeeze": False, "bandwidth": 0.05},
        "volume_spike": {"is_spike": True, "spike_ratio": 2.0},
        "atr_value": 50.0,
        "support_resistance": {"support": [], "resistance": []},
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════
# B3: compute_sr_proximity_boost
# ═══════════════════════════════════════════════════════════════

class TestComputeSRProximityBoost:
    """Pure unit tests for the S/R proximity helper."""

    def test_no_price_returns_zero(self):
        boost, reasons = compute_sr_proximity_boost({"price": 0})
        assert boost == 0.0
        assert reasons == []

    def test_no_support_resistance_returns_zero(self):
        boost, reasons = compute_sr_proximity_boost({"price": 1000})
        assert boost == 0.0
        assert reasons == []

    def test_price_near_support_positive_direction_max_boost(self):
        """Price within 2% of support + positive direction → 0.15 boost."""
        stock = {
            "price": 1010,
            "impact_direction": "positive",
            "support_resistance": {"support": [1000], "resistance": []},
        }
        boost, reasons = compute_sr_proximity_boost(stock)
        assert boost == 0.15
        assert any("support" in r.lower() for r in reasons)

    def test_price_near_support_within_5pct_moderate_boost(self):
        """Price within 5% of support + positive direction → 0.08 boost."""
        stock = {
            "price": 1040,
            "impact_direction": "positive",
            "support_resistance": {"support": [1000], "resistance": []},
        }
        boost, reasons = compute_sr_proximity_boost(stock)
        assert boost == 0.08

    def test_price_far_from_support_no_boost(self):
        stock = {
            "price": 1200,
            "impact_direction": "positive",
            "support_resistance": {"support": [1000], "resistance": []},
        }
        boost, reasons = compute_sr_proximity_boost(stock)
        assert boost == 0.0

    def test_price_near_resistance_negative_direction_max_boost(self):
        """Price within 2% of resistance + negative direction → 0.15 boost."""
        stock = {
            "price": 990,
            "impact_direction": "negative",
            "support_resistance": {"support": [], "resistance": [1000]},
        }
        boost, reasons = compute_sr_proximity_boost(stock)
        assert boost == 0.15
        assert any("resistance" in r.lower() for r in reasons)

    def test_price_near_support_negative_direction_no_boost(self):
        """Near support but direction is negative → no S/R boost."""
        stock = {
            "price": 1005,
            "impact_direction": "negative",
            "support_resistance": {"support": [1000], "resistance": []},
        }
        boost, reasons = compute_sr_proximity_boost(stock)
        assert boost == 0.0

    def test_price_near_resistance_positive_direction_no_boost(self):
        """Near resistance but direction is positive → no S/R boost."""
        stock = {
            "price": 995,
            "impact_direction": "positive",
            "support_resistance": {"support": [], "resistance": [1000]},
        }
        boost, reasons = compute_sr_proximity_boost(stock)
        assert boost == 0.0

    def test_neutral_direction_infers_buy_from_rsi(self):
        """Neutral direction + low RSI → treat as positive (bounce candidate)."""
        stock = {
            "price": 1005,
            "impact_direction": "neutral",
            "rsi_value": 30.0,
            "support_resistance": {"support": [1000], "resistance": []},
        }
        boost, reasons = compute_sr_proximity_boost(stock)
        assert boost == 0.15

    def test_neutral_direction_infers_sell_from_rsi(self):
        """Neutral direction + high RSI → treat as negative (pullback expected)."""
        stock = {
            "price": 995,
            "impact_direction": "neutral",
            "rsi_value": 70.0,
            "support_resistance": {"support": [], "resistance": [1000]},
        }
        boost, reasons = compute_sr_proximity_boost(stock)
        assert boost == 0.15

    def test_neutral_direction_mid_rsi_no_boost(self):
        """Neutral direction + mid RSI → no direction inference."""
        stock = {
            "price": 1005,
            "impact_direction": "neutral",
            "rsi_value": 50.0,
            "support_resistance": {"support": [1000], "resistance": []},
        }
        boost, reasons = compute_sr_proximity_boost(stock)
        assert boost == 0.0


# ═══════════════════════════════════════════════════════════════
# B4: detect_bollinger_squeeze
# ═══════════════════════════════════════════════════════════════

class TestDetectBollingerSqueeze:
    """Pure unit tests for the Bollinger squeeze helper."""

    def test_no_squeeze_returns_zero(self):
        stock = {"bollinger": {"squeeze": False, "bandwidth": 0.05, "percent_b": 0.5}}
        boost, reasons = detect_bollinger_squeeze(stock)
        assert boost == 0.0
        assert reasons == []

    def test_no_bollinger_returns_zero(self):
        boost, reasons = detect_bollinger_squeeze({})
        assert boost == 0.0

    def test_squeeze_alone_gives_base_boost(self):
        """Squeeze without directional alignment → 0.10 base boost."""
        stock = {
            "impact_direction": "neutral",
            "bollinger": {"squeeze": True, "bandwidth": 0.01, "percent_b": 0.5},
        }
        boost, reasons = detect_bollinger_squeeze(stock)
        assert boost == 0.10
        assert any("squeeze" in r.lower() for r in reasons)

    def test_squeeze_near_lower_band_positive_direction(self):
        """Squeeze + low %B + positive direction → 0.20 total boost."""
        stock = {
            "impact_direction": "positive",
            "bollinger": {"squeeze": True, "bandwidth": 0.01, "percent_b": 0.15},
        }
        boost, reasons = detect_bollinger_squeeze(stock)
        assert boost == 0.20
        assert any("breakout" in r.lower() for r in reasons)

    def test_squeeze_near_upper_band_negative_direction(self):
        """Squeeze + high %B + negative direction → 0.20 total boost."""
        stock = {
            "impact_direction": "negative",
            "bollinger": {"squeeze": True, "bandwidth": 0.01, "percent_b": 0.85},
        }
        boost, reasons = detect_bollinger_squeeze(stock)
        assert boost == 0.20
        assert any("breakdown" in r.lower() for r in reasons)

    def test_squeeze_wrong_alignment_stays_at_base(self):
        """Squeeze + low %B but negative direction → only 0.10 base."""
        stock = {
            "impact_direction": "negative",
            "bollinger": {"squeeze": True, "bandwidth": 0.01, "percent_b": 0.15},
        }
        boost, reasons = detect_bollinger_squeeze(stock)
        assert boost == 0.10


# ═══════════════════════════════════════════════════════════════
# B3+B4 Integration: classify_signal strength/tier/horizon influence
# ═══════════════════════════════════════════════════════════════

class TestSRAndSqueezeIntegration:
    """Integration tests proving B3+B4 boosts propagate into classify_signal."""

    def test_near_support_positive_increases_strength_vs_baseline(self):
        """Stock near support (positive dir) should have higher signal_strength
        than the same stock with no support levels."""
        stock_no_sr = _base_positive_stock(
            support_resistance={"support": [], "resistance": []},
        )
        stock_near_support = _base_positive_stock(
            support_resistance={"support": [990], "resistance": []},
        )
        baseline = classify_signal(stock_no_sr)
        boosted = classify_signal(stock_near_support)
        assert boosted["signal_strength"] > baseline["signal_strength"]
        assert any("support" in r.lower() for r in boosted["reasons"])

    def test_near_resistance_negative_boosts_sell_strength(self):
        """Stock near resistance (negative dir) should have higher signal_strength."""
        stock_no_sr = _base_negative_stock(
            support_resistance={"support": [], "resistance": []},
        )
        stock_near_resistance = _base_negative_stock(
            support_resistance={"support": [], "resistance": [1010]},
        )
        baseline = classify_signal(stock_no_sr)
        boosted = classify_signal(stock_near_resistance)
        assert boosted["signal_strength"] > baseline["signal_strength"]
        assert any("resistance" in r.lower() for r in boosted["reasons"])

    def test_bollinger_squeeze_promotes_strength(self):
        """Stock with squeeze=True should have higher signal_strength."""
        stock_no_squeeze = _base_positive_stock(
            bollinger={"percent_b": 0.15, "squeeze": False, "bandwidth": 0.05},
        )
        stock_squeezed = _base_positive_stock(
            bollinger={"percent_b": 0.15, "squeeze": True, "bandwidth": 0.01},
        )
        baseline = classify_signal(stock_no_squeeze)
        squeezed = classify_signal(stock_squeezed)
        assert squeezed["signal_strength"] > baseline["signal_strength"]
        assert any("squeeze" in r.lower() for r in squeezed["reasons"])

    def test_squeeze_with_event_promotes_1d_horizon(self):
        """Squeeze + non-trivial event score → infer_time_horizon returns '1d'."""
        stock = {
            "event_stage": "developing",
            "bollinger": {"squeeze": True, "bandwidth": 0.01, "percent_b": 0.5},
        }
        event = {"score": 0.3}
        tech = {"confirm_count": 2, "total": 4}
        assert infer_time_horizon(stock, event, tech) == "1d"

    def test_combined_sr_and_squeeze_max_boosts_strength(self):
        """Both S/R proximity + squeeze should compound into signal_strength."""
        stock = _base_positive_stock(
            support_resistance={"support": [990], "resistance": []},
            bollinger={"percent_b": 0.15, "squeeze": True, "bandwidth": 0.01},
        )
        result = classify_signal(stock)
        # Should be higher than baseline (which already has some strength from
        # event + tech). With both boosts, we expect strong conviction.
        assert result["action"] == "BUY"
        assert result["signal_strength"] >= 0.50

    def test_sr_boost_promotes_marginal_signal_to_watch(self):
        """A marginal IGNORE signal can be promoted to WATCH by S/R boost."""
        stock_no_sr = {
            "ticker": "WEAK.JK",
            "price": 1000,
            "impact_score": 5.0,
            "impact_direction": "positive",
            "relationship_confidence": 0.6,
            "source_confidence": 0.6,
            "corroboration_count": 2,
            "recency_weight": 0.8,
            "source_conflict": False,
            "event_stage": "developing",
            "support_resistance": {"support": [], "resistance": []},
        }
        baseline = classify_signal(stock_no_sr)
        assert baseline["action"] == "IGNORE"

        # Same stock near support → should be promoted to WATCH
        stock_near_support = dict(stock_no_sr)
        stock_near_support["support_resistance"] = {"support": [990], "resistance": []}
        boosted = classify_signal(stock_near_support)
        assert boosted["action"] == "WATCH"
        assert any("support" in r.lower() for r in boosted["reasons"])

    def test_no_false_boost_when_direction_mismatched(self):
        """Near support but negative direction should NOT add S/R boost."""
        stock = _base_negative_stock(
            support_resistance={"support": [990], "resistance": []},
        )
        stock_no_sr = _base_negative_stock(
            support_resistance={"support": [], "resistance": []},
        )
        result_mismatch = classify_signal(stock)
        result_clean = classify_signal(stock_no_sr)
        # No support boost should be added (direction mismatch)
        assert result_mismatch["signal_strength"] == result_clean["signal_strength"]
