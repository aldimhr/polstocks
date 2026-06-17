from __future__ import annotations

from backend.trading_signals import (
    classify_signal,
    compute_participation_score,
    detect_short_term_setup,
    rank_trade_signals,
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
        assert result["setup_status"] == "confirmed"
        assert result["trade_label"] == "Best Buy Now"
        assert result["signal_state"] == "ready_to_buy"
        assert result["state_label"] == "Ready to Buy"
        assert result["next_trigger"] == "Ready to execute"
        assert result["transition_trigger_price"] == 980.0
        assert result["trader_score"] >= 70
        assert result["rr_ratio"] >= 1.9
        assert result["risk_reward_label"] == "good"
        assert result["shortlist_eligible"] is True
        assert result["alert_ready"] is True
        assert any(item["status"] == "pass" for item in result["execution_checklist"])
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

    def test_breakout_without_close_above_resistance_stays_watch(self):
        stock = self._base_stock(
            impact_direction="neutral",
            impact_score=0.0,
            relationship_confidence=0.0,
            source_confidence=0.0,
            corroboration_count=0,
            volume_spike={"is_spike": True, "spike_ratio": 1.6},
            value_traded_estimate=900_000_000,
            close_above_resistance=False,
            distance_to_resistance_pct=0.02,
            return_1d=0.4,
            return_3d=0.3,
            return_5d=0.8,
            price_above_sma20=True,
            price_above_sma50=True,
        )
        result = classify_signal(stock)
        assert result["action"] == "WATCH"
        assert result["setup_status"] == "forming"
        assert result["trade_label"] == "Watch for Breakout"
        assert result["signal_state"] == "waiting_breakout"
        assert result["state_label"] == "Waiting Breakout"
        assert "close above resistance" in result["next_trigger"].lower()
        assert "980" in result["next_trigger"]
        assert result["transition_trigger_price"] == 980.0
        assert any(
            item["key"] == "breakout_close" and item["status"] == "fail"
            for item in result["execution_checklist"]
        )
        assert any(
            "resistance" in reason.lower() or "trigger" in reason.lower()
            for reason in result["reasons"]
        )

    def test_support_rebound_without_reclaim_stays_watch(self):
        stock = self._base_stock(
            impact_direction="neutral",
            impact_score=0.0,
            relationship_confidence=0.0,
            source_confidence=0.0,
            corroboration_count=0,
            rsi_value=34.0,
            macd={"histogram": 0.2},
            bollinger={"percent_b": 0.16, "squeeze": False, "bandwidth": 0.05},
            volume_spike={"is_spike": True, "spike_ratio": 1.3},
            support_resistance={"support": [990], "resistance": [1080]},
            reclaim_from_support=False,
            return_1d=-0.6,
            return_3d=-1.2,
            return_5d=-0.4,
            price_above_sma20=False,
            price_above_sma50=True,
        )
        result = classify_signal(stock)
        assert result["action"] == "WATCH"
        assert result["time_horizon"] == "14d"
        assert result["setup_status"] == "forming"
        assert result["trade_label"] == "Watch for Rebound"
        assert result["signal_state"] == "waiting_reclaim"
        assert result["state_label"] == "Waiting Reclaim"
        assert "reclaim from support" in result["next_trigger"].lower()
        assert "990" in result["next_trigger"]
        assert result["transition_trigger_price"] == 990.0
        assert any(
            item["key"] == "support_reclaim" and item["status"] == "fail"
            for item in result["execution_checklist"]
        )
        assert any(
            "rebound" in reason.lower() or "reclaim" in reason.lower()
            for reason in result["reasons"]
        )

    def test_buy_setup_becomes_late_entry_when_breakout_is_too_extended(self):
        stock = self._base_stock(
            price=1006,
            support_resistance={"support": [940], "resistance": [980]},
            distance_to_resistance_pct=-0.026,
        )
        result = classify_signal(stock)
        assert result["action"] == "BUY"
        assert result["signal_state"] == "late_entry"
        assert result["state_label"] == "Late Entry"
        assert "pullback" in result["next_trigger"].lower()
        assert result["shortlist_eligible"] is False
        assert result["alert_ready"] is False

    def test_watch_setup_can_be_marked_invalidated(self):
        stock = self._base_stock(
            rsi_value=34.0,
            macd={"histogram": 0.2},
            bollinger={"percent_b": 0.16, "squeeze": False, "bandwidth": 0.05},
            volume_spike={"is_spike": True, "spike_ratio": 1.3},
            support_resistance={"support": [990], "resistance": [1080]},
            reclaim_from_support=False,
            setup_invalidated=True,
            invalidation_reason="Support lost on failed rebound",
        )
        result = classify_signal(stock)
        assert result["signal_state"] == "invalidated"
        assert result["state_label"] == "Invalidated"
        assert "support lost" in result["next_trigger"].lower()
        assert result["alert_ready"] is False

    def test_watch_setup_can_expire_after_horizon_window(self):
        stock = self._base_stock(
            rsi_value=34.0,
            macd={"histogram": 0.2},
            bollinger={"percent_b": 0.16, "squeeze": False, "bandwidth": 0.05},
            volume_spike={"is_spike": True, "spike_ratio": 1.3},
            support_resistance={"support": [990], "resistance": [1080]},
            reclaim_from_support=False,
            setup_age_days=21,
        )
        result = classify_signal(stock)
        assert result["signal_state"] == "expired"
        assert result["state_label"] == "Expired"
        assert "expired" in result["next_trigger"].lower()
        assert result["alert_ready"] is False

    def test_buy_setup_can_be_marked_triggered_today(self):
        stock = self._base_stock(
            position_entry_price=980,
            triggered_today=True,
        )
        result = classify_signal(stock)
        assert result["signal_state"] == "triggered_today"
        assert result["state_label"] == "Triggered Today"
        assert "triggered today" in result["next_trigger"].lower()
        assert result["shortlist_eligible"] is False
        assert result["alert_ready"] is True

    def test_buy_setup_can_be_marked_active_trade(self):
        stock = self._base_stock(
            position_entry_price=980,
            days_since_entry=2,
        )
        result = classify_signal(stock)
        assert result["signal_state"] == "active_trade"
        assert result["state_label"] == "Active Trade"
        assert "manage open trade" in result["next_trigger"].lower()
        assert result["shortlist_eligible"] is False
        assert result["alert_ready"] is False

    def test_buy_setup_can_be_marked_take_profit_hit(self):
        stock = self._base_stock(
            price=1100,
            position_entry_price=980,
            days_since_entry=2,
        )
        result = classify_signal(stock)
        assert result["signal_state"] == "tp_hit"
        assert result["state_label"] == "Take Profit Hit"
        assert "take profit" in result["next_trigger"].lower()
        assert result["alert_ready"] is True

    def test_buy_setup_can_be_marked_stop_loss_hit(self):
        stock = self._base_stock(
            price=920,
            position_entry_price=980,
            days_since_entry=1,
        )
        result = classify_signal(stock)
        assert result["signal_state"] == "sl_hit"
        assert result["state_label"] == "Stop Loss Hit"
        assert "stop loss" in result["next_trigger"].lower()
        assert result["alert_ready"] is True

    def test_buy_setup_can_be_marked_failed_breakout(self):
        stock = self._base_stock(
            price=970,
            position_entry_price=980,
            days_since_entry=1,
            failed_breakout=True,
        )
        result = classify_signal(stock)
        assert result["signal_state"] == "failed_breakout"
        assert result["state_label"] == "Failed Breakout"
        assert "failed breakout" in result["next_trigger"].lower()
        assert result["alert_ready"] is True

    def test_rank_trade_signals_prefers_rr_shortlist_buy(self):
        signals = [
            {
                "ticker": "SAFE.JK",
                "action": "BUY",
                "signal_tier": "B",
                "signal_strength": 0.72,
                "time_horizon": "3d",
                "participation_score": 0.68,
                "setup_type": "breakout_continuation",
                "trader_score": 84,
                "rr_ratio": 2.0,
                "shortlist_eligible": True,
            },
            {
                "ticker": "WEAKRR.JK",
                "action": "BUY",
                "signal_tier": "A",
                "signal_strength": 0.79,
                "time_horizon": "3d",
                "participation_score": 0.70,
                "setup_type": "breakout_continuation",
                "trader_score": 88,
                "rr_ratio": 1.1,
                "shortlist_eligible": False,
            },
        ]
        ranked = rank_trade_signals(signals)
        assert ranked[0]["ticker"] == "SAFE.JK"

