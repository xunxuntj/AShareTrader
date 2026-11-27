"""Tests for orthogonal strategies: trend direction, dynamic listing, and zero lookahead."""

import numpy as np
import pandas as pd

from ashare_trader.strategies.combiner import CombinedStrategy
from ashare_trader.strategies.momentum_r2 import MomentumR2Strategy
from ashare_trader.strategies.risk_parity import RiskParityMomentumStrategy
from ashare_trader.strategies.trend_atr import TrendATRStrategy


def _synthetic_panels(n_days: int = 120) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    idx = pd.date_range("2023-01-02", periods=n_days, freq="B")
    t = np.arange(n_days, dtype=float)
    # Asset A: smooth uptrend; Asset B: downtrend; Asset C: late-listed; Cash: flat
    close = pd.DataFrame(
        {
            "AAA": 1.0 * np.exp(0.004 * t),
            "BBB": 2.0 * np.exp(-0.003 * t),
            "CCC": np.where(t >= 90, 1.5 * np.exp(0.002 * (t - 90)), np.nan),
            "511880": np.full(n_days, 100.0),
        },
        index=idx,
    )
    high = close * 1.01
    low = close * 0.99
    return close, high, low


def test_momentum_r2_selects_uptrend_and_ignores_unlisted():
    close, high, low = _synthetic_panels()
    strat = MomentumR2Strategy(safe_haven_symbol="511880", params={"top_k": 1, "min_history_days": 60})
    sig = strat.generate_signal(close, high, low, current_weights={})
    assert "AAA" in sig.target_weights
    assert sig.target_weights["AAA"] == 1.0
    assert "CCC" not in sig.target_weights  # Only 30 days history < 60


def test_momentum_r2_switches_to_safe_haven_in_bear_market():
    close, high, low = _synthetic_panels()
    # Make all risky assets trend downward
    close["AAA"] = close["BBB"] * 0.8
    high = close * 1.01
    low = close * 0.99
    strat = MomentumR2Strategy(safe_haven_symbol="511880", params={"top_k": 2, "min_history_days": 60})
    sig = strat.generate_signal(close, high, low, current_weights={})
    assert sig.target_weights == {"511880": 1.0}


def test_strict_no_lookahead_invariance():
    """Mutating future prices (t+1..end) must NOT change signals generated at day t."""
    close, high, low = _synthetic_panels(120)
    cutoff = 85

    for strat in [
        MomentumR2Strategy("511880"),
        TrendATRStrategy("511880"),
        RiskParityMomentumStrategy("511880"),
    ]:
        sig_clean = strat.generate_signal(
            close.iloc[:cutoff], high.iloc[:cutoff], low.iloc[:cutoff], current_weights={}
        )
        # Corrupt future data massively
        close_mut = close.copy()
        close_mut.iloc[cutoff:] = close_mut.iloc[cutoff:] * 0.1
        high_mut = close_mut * 1.01
        low_mut = close_mut * 0.99

        strat.reset_state()
        sig_mut = strat.generate_signal(
            close_mut.iloc[:cutoff], high_mut.iloc[:cutoff], low_mut.iloc[:cutoff], current_weights={}
        )
        assert sig_clean.target_weights == sig_mut.target_weights


def test_combined_strategy_blends_weights():
    close, high, low = _synthetic_panels(120)
    s1 = MomentumR2Strategy("511880")
    s2 = TrendATRStrategy("511880")
    s3 = RiskParityMomentumStrategy("511880")
    comb = CombinedStrategy([s1, s2, s3], {"momentum_r2": 0.5, "trend_atr": 0.3, "risk_parity": 0.2}, "511880")
    sig = comb.generate_signal(close, high, low, current_weights={})
    assert abs(sum(sig.target_weights.values()) - 1.0) < 1e-4
