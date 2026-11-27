"""Tests for BacktestEngine A-Share constraints, costs, lot rounding, and metrics."""

import numpy as np
import pandas as pd

from ashare_trader.engine.backtest import BacktestEngine
from ashare_trader.models import BacktestConfig, StrategySignal
from ashare_trader.strategies.base import BaseStrategy


class _FlipFlopStrategy(BaseStrategy):
    """Alternates 100% AAA and 100% BBB every N days to test friction and T+1."""

    def __init__(self, flip_every: int = 5) -> None:
        super().__init__(name="flip", safe_haven_symbol="511880")
        self.flip_every = flip_every
        self._step = 0

    def reset_state(self) -> None:
        self._step = 0

    def generate_signal(self, close_df, high_df, low_df, current_weights):
        self._step += 1
        sym = "AAA" if (self._step // self.flip_every) % 2 == 0 else "BBB"
        return StrategySignal(
            signal_date=close_df.index[-1].date(),
            strategy_name=self.name,
            target_weights={sym: 1.0},
        )


def test_backtest_engine_lot_size_fees_and_metrics():
    idx = pd.date_range("2023-01-02", periods=60, freq="B")
    close = pd.DataFrame(
        {
            "AAA": np.linspace(1.0, 1.3, 60),
            "BBB": np.linspace(2.0, 2.2, 60),
            "511880": np.full(60, 100.0),
        },
        index=idx,
    )
    cfg = BacktestConfig(
        initial_capital=100000.0,
        commission_rate=0.0001,
        slippage_rate=0.0005,
        lot_size=100,
        execution_timing="close",
    )
    engine = BacktestEngine(config=cfg)
    res = engine.run(
        strategy=_FlipFlopStrategy(flip_every=10),
        close_df=close,
        start_date="2023-01-02",
        benchmark_symbol="AAA",
    )

    assert len(res.equity_curve) == 60
    assert not res.trades.empty
    # Every executed order must be a multiple of 100 shares
    assert (res.trades["shares"] % 100 == 0).all()
    assert res.metrics["total_fees"] > 0
    assert "cagr" in res.metrics and "calmar" in res.metrics


def test_backtest_engine_blocks_limit_up_buy():
    idx = pd.date_range("2023-01-02", periods=5, freq="B")
    # Day 1 -> Day 2 jumps +10% (limit-up)
    close = pd.DataFrame(
        {
            "AAA": [1.0, 1.10, 1.10, 1.10, 1.10],
            "511880": [100.0] * 5,
        },
        index=idx,
    )

    class _BuyOnDay2(BaseStrategy):
        def __init__(self):
            super().__init__("buy2", "511880")
            self.n = 0

        def generate_signal(self, close_df, high_df, low_df, current_weights):
            self.n += 1
            w = {"AAA": 1.0} if self.n >= 2 else {}
            return StrategySignal(close_df.index[-1].date(), self.name, w)

    engine = BacktestEngine(config=BacktestConfig(limit_move_threshold=0.098))
    res = engine.run(_BuyOnDay2(), close_df=close, start_date="2023-01-02")
    # Should NOT buy on day 2 (2023-01-03), should fill on day 3 (2023-01-04)
    first_buy_date = res.trades.iloc[0]["trade_date"]
    assert first_buy_date == pd.Timestamp("2023-01-04")
