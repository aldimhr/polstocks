"""Tunable scoring weights for the prediction pipeline.

All indicator multipliers, thresholds, and caps are defined here.
The scoring chain reads from get_weight() instead of hardcoded values.
Auto-tune writes overrides to weights_override.json.
"""
import json
import threading
from pathlib import Path

WEIGHTS_FILE = Path(__file__).parent / "weights_override.json"

# ── Default weights (hand-tuned baseline) ──
DEFAULTS = {
    # Technical alignment (RSI + MACD + SMA combined)
    "technical_cap": 0.15,           # max ±15% from technical alignment
    "rsi_overbought_extreme": 80,    # RSI >= this → strong bearish signal
    "rsi_overbought": 70,            # RSI >= this → moderate bearish
    "rsi_oversold_extreme": 20,      # RSI <= this → strong bullish signal
    "rsi_oversold": 30,              # RSI <= this → moderate bullish

    # Event clustering
    "cluster_3plus_mult": 1.10,      # 3+ events about same ticker
    "cluster_2_mult": 1.05,          # 2 events

    # ATR volatility
    "atr_very_high_pct": 5.0,        # ATR% > this → very high volatility
    "atr_very_high_mult": 1.10,
    "atr_high_pct": 3.0,             # ATR% > this → high volatility
    "atr_high_mult": 1.06,
    "atr_low_pct": 1.0,              # ATR% < this → low volatility
    "atr_low_mult": 0.94,

    # Sector correlation
    "sector_4plus_mult": 1.08,       # 4+ stocks in sector agree
    "sector_2plus_mult": 1.04,       # 2+ stocks agree

    # Foreign market
    "foreign_aligned_mult": 1.06,    # global trend aligned with prediction
    "foreign_against_mult": 0.94,    # global trend against prediction

    # Sentiment momentum
    "momentum_strong_mult": 1.05,    # sentiment strengthening
    "momentum_weakening_mult": 0.95, # sentiment weakening

    # Currency impact
    "currency_exporter_mult": 1.05,  # favorable currency for exporters
    "currency_importer_mult": 1.03,  # favorable currency for importers
    "currency_against_mult": 0.97,   # currency against prediction

    # Volume
    "volume_3x_mult": 1.12,         # >3x average volume
    "volume_2x_mult": 1.07,         # >2x average volume
    "volume_low_mult": 0.92,        # <0.4x average volume

    # Market context (IHSG)
    "market_flat_threshold": 0.15,   # |change| < this → flat market
    "market_flat_mult": 0.82,
    "market_mild_threshold": 0.30,   # |change| < this → mild market
    "market_mild_mult": 0.90,
    "market_strong_threshold": 1.0,  # |change| > this → strong market
    "market_strong_mult": 1.04,

    # Confidence thresholds
    "high_confidence_threshold": 0.7,
    "medium_confidence_threshold": 0.4,

    # Vagueness penalty
    "vagueness_penalty_mult": 0.50,  # halve confidence for vague language

    # Source diversity
    "source_diversity_3types_mult": 1.12,
    "source_diversity_2types_mult": 1.07,
    "source_single_penalty_mult": 0.95,

    # Significance
    "significance_base": 0.35,
    "significance_multiplier": 0.55,
    "directional_sentiment_floor": 0.55,
    "indirect_relationship_multiplier": 0.70,

    # Confidence calibration: raw scores are ~0.012 but actual returns ~7.43%
    # Scale factor bridges the gap between NLP-derived magnitude and market reality.
    "calibration_scale_factor": 15.0,
    "calibration_score_abs_cap": 10.0,
    "calibration_confidence_floor": 0.15,
}

# ── Per-category weight multipliers (backtest-driven) ──
# High hit-rate categories get boosted, low ones dampened.
CATEGORY_WEIGHT_MULTIPLIERS: dict[str, float] = {
    "TRADE_POLICY": 1.15,
    "ENERGY_POLICY": 1.15,
    "REGULATION_NEW": 1.10,
    "PARLIAMENT_SESSION": 0.85,
    "MONETARY_POLICY": 0.90,
    "_DEFAULT": 1.0,
}


def get_category_multiplier(category: str) -> float:
    """Return the weight multiplier for a given event category."""
    return CATEGORY_WEIGHT_MULTIPLIERS.get(
        category, CATEGORY_WEIGHT_MULTIPLIERS["_DEFAULT"]
    )

_lock = threading.Lock()
_overrides: dict | None = None


def _load_overrides() -> dict:
    global _overrides
    if _overrides is not None:
        return _overrides
    with _lock:
        if _overrides is not None:
            return _overrides
        if WEIGHTS_FILE.exists():
            try:
                _overrides = json.loads(WEIGHTS_FILE.read_text())
            except (json.JSONDecodeError, OSError):
                _overrides = {}
        else:
            _overrides = {}
        return _overrides


def get_weight(key: str) -> float | int:
    """Get a weight value, preferring overrides over defaults."""
    overrides = _load_overrides()
    if key in overrides:
        return overrides[key]
    if key in DEFAULTS:
        return DEFAULTS[key]
    raise KeyError(f"Unknown weight key: {key}")


def get_all_weights() -> dict[str, float | int]:
    """Return all weights (defaults + overrides merged)."""
    result = dict(DEFAULTS)
    result.update(_load_overrides())
    return result


def get_overrides() -> dict[str, float | int]:
    """Return only the override values (non-default)."""
    return dict(_load_overrides())


def apply_overrides(new_overrides: dict[str, float | int]) -> dict[str, str]:
    """Apply a batch of weight overrides. Returns status per key."""
    global _overrides
    result = {}
    current = _load_overrides()

    with _lock:
        for key, value in new_overrides.items():
            if key not in DEFAULTS:
                result[key] = "unknown_key"
                continue
            default = DEFAULTS[key]
            # Validate type matches
            if isinstance(default, int) and not isinstance(value, int):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    result[key] = "invalid_type"
                    continue
            elif isinstance(default, float) and not isinstance(value, (int, float)):
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    result[key] = "invalid_type"
                    continue
            # Clamp multipliers to reasonable range
            if "mult" in key or "cap" in key or "floor" in key or "penalty" in key:
                value = max(0.5, min(1.5, value))
            current[key] = value
            result[key] = "applied"

        WEIGHTS_FILE.write_text(json.dumps(current, indent=2))
        _overrides = current

    return result


def reset_to_defaults() -> None:
    """Remove all overrides, reverting to defaults."""
    global _overrides
    with _lock:
        if WEIGHTS_FILE.exists():
            WEIGHTS_FILE.unlink()
        _overrides = {}


def reload() -> None:
    """Force reload from disk."""
    global _overrides
    with _lock:
        _overrides = None
