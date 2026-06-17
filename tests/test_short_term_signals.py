from __future__ import annotations

from backend.trading_signals import (
    classify_signal,
    compute_participation_score,
    detect_short_term_setup,
)


class TestShortTermSignalScorer:
    def _base_stock(self, **overrides):
        base = {
            "ticker": "TEST.JK",
            "price": 1000,
            "impact_score": 0.0,
            "impact_direction": "neutral",
            "relationship_confidence": 0.0,
            "source_confidence": 0.0,
            "corroboration_count": 0,
            "recency_weight": 1.0,
            "source_conflict": False,
            "event_stage": "developing",
            "rsi_value": 60.0,
            "macd": {"histogram": 0.5},
            "bollinger": {"percent_b": 0.82, "squeeze": False, "bandwidth": 0.05},
            "volume_spike": {"is_spike": True, "spike_ratio": 2.2},
            "atr_value": 40.0,
            "support_resistance": {"support": [940], "resistance": [980]},
            "trend": {"trend": "bullish"},
        }
        base.update(overrides)
        return base

    def test_compute_participation_score_flags_high_participation(self):
        stock = self._base_stock(value_traded_estimate=3_500_000_000)
        result = compute_participation_score(stock)
        assert result["score"] >= 0.6
        assert result["label"] == "high"
        assert any("volume" in reason.lower() for reason in result["reasons"])

    def test_breakout_with_participation_becomes_buy_without_event_bias(self):
        stock = self._base_stock(
            impact_direction="neutral",
            impact_score=0.0,
            relationship_confidence=0.0,
            source_confidence=0.0,
            corroboration_count=0,
        )
        setup = detect_short_term_setup(stock)
        assert setup is not None
        assert setup["setup_type"] == "breakout_continuation"
        result = classify_signal(stock)
        assert result["action"] == "BUY"
        assert result["time_horizon"] in ("1d", "3d")
        assert result["entry_price"] == 1000
        assert result["stop_loss"] is not None
        assert any("breakout" in reason.lower() for reason in result["reasons"])

    def test_breakout_without_participation_stays_watch(self):
        stock = self._base_stock(
            volume_spike={"is_spike": False, "spike_ratio": 1.05},
            bollinger={"percent_b": 0.82, "squeeze": False, "bandwidth": 0.05},
        )
        result = classify_signal(stock)
        assert result["action"] == "WATCH"
        assert any(
            "participation" in reason.lower() or "volume" in reason.lower()
            for reason in result["reasons"]
        )

    def test_support_rebound_with_recovery_is_buy(self):
        stock = self._base_stock(
            impact_direction="neutral",
            rsi_value=34.0,
            macd={"histogram": 0.25},
            bollinger={"percent_b": 0.18, "squeeze": False, "bandwidth": 0.05},
            volume_spike={"is_spike": True, "spike_ratio": 1.6},
            support_resistance={"support": [990], "resistance": [1080]},
        )
        setup = detect_short_term_setup(stock)
        assert setup is not None
        assert setup["setup_type"] == "support_rebound"
        result = classify_signal(stock)
        assert result["action"] == "BUY"
        assert result["time_horizon"] in ("7d", "14d")
        assert any("support" in reason.lower() or "rebound" in reason.lower() for reason in result["reasons"])

    def test_strong_event_without_bullish_trigger_is_watch(self):
        stock = self._base_stock(
            impact_score=8.0,
            impact_direction="positive",
            relationship_confidence=0.9,
            source_confidence=0.9,
            corroboration_count=3,
            rsi_value=49.0,
            macd={"histogram": -0.05},
            bollinger={"percent_b": 0.55, "squeeze": False, "bandwidth": 0.08},
            volume_spike={"is_spike": False, "spike_ratio": 1.0},
            support_resistance={"support": [900], "resistance": [1100]},
        )
        setup = detect_short_term_setup(stock)
        assert setup is None
        result = classify_signal(stock)
        assert result["action"] == "WATCH"
        assert any(
            "technical" in reason.lower() or "trigger" in reason.lower()
            for reason in result["reasons"]
        )
