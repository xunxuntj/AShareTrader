"""Tests for RiskManager drawdown circuit breaker and research universe/IS-OOS utilities."""

import numpy as np
import pandas as pd

from ashare_trader.engine.risk import RiskManager
from ashare_trader.research.universe_selector import select_low_correlation_universe


def test_risk_manager_stop_loss_and_drawdown_hysteresis():
    rm = RiskManager(safe_haven_symbol="511880", config={"portfolio_dd_reduce": 0.12, "portfolio_dd_recover": 0.06})

    # Establish peak equity = 100k, no stop breach
    w, notes = rm.apply({"510300": 0.6, "518880": 0.4}, 100000.0, {"510300": 4.0, "518880": 5.0}, {"510300": 3.8})
    assert w["510300"] == 0.6 and not notes

    # 1. Single-asset stop breach: 510300 drops to 3.7 < 3.8
    w, notes = rm.apply({"510300": 0.6, "518880": 0.4}, 96000.0, {"510300": 3.7, "518880": 5.0}, {"510300": 3.8})
    assert "510300" not in w
    assert abs(w["511880"] - 0.6) < 1e-6
    assert any("跌破止损线" in n for n in notes)

    # 2. Portfolio drawdown hits 13% (>= 12%) -> cuts risky exposure by 50%
    w, notes = rm.apply({"518880": 0.8, "511880": 0.2}, 87000.0, {"518880": 4.5}, {})
    assert abs(w["518880"] - 0.4) < 1e-6
    assert abs(w["511880"] - 0.6) < 1e-6

    # 3. Equity recovers to 92k (dd=8%, still > 6%) -> stays derisked
    w, _ = rm.apply({"518880": 0.8, "511880": 0.2}, 92000.0, {"518880": 4.8}, {})
    assert abs(w["518880"] - 0.4) < 1e-6

    # 4. Equity recovers to 95k (dd=5% <= 6%) -> restores full exposure
    w, notes = rm.apply({"518880": 0.8, "511880": 0.2}, 95000.0, {"518880": 5.0}, {})
    assert abs(w["518880"] - 0.8) < 1e-6
    assert any("恢复正常仓位" in n for n in notes)


def test_select_low_correlation_universe_drops_redundant_assets():
    profiles = pd.DataFrame(
        [
            {"symbol": "A", "name": "A", "category": "eq", "years": 8.0, "median_amount_m": 100.0, "sharpe": 1.2},
            {"symbol": "B", "name": "B", "category": "eq", "years": 8.0, "median_amount_m": 100.0, "sharpe": 1.1},
            {"symbol": "C", "name": "C", "category": "gold", "years": 8.0, "median_amount_m": 100.0, "sharpe": 0.9},
            {"symbol": "D", "name": "D", "category": "new", "years": 1.0, "median_amount_m": 100.0, "sharpe": 2.0},
        ]
    )
    corr = pd.DataFrame(
        [[1.0, 0.92, 0.10], [0.92, 1.0, 0.12], [0.10, 0.12, 1.0]],
        index=["A", "B", "C"],
        columns=["A", "B", "C"],
    )
    selected, log = select_low_correlation_universe(
        profiles, corr, min_years=4.0, min_median_amount_m=20.0, max_pair_corr=0.70, max_assets=5
    )
    # D excluded by history < 4y; B excluded by high corr 0.92 to A; A and C selected
    assert selected == ["A", "C"]
    assert log.loc[log["symbol"] == "B", "decision"].iloc[0] == "DROPPED_HIGH_CORR"
