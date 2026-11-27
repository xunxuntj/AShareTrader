"""Out-of-sample (IS/OOS) validation, strategy comparison, and parameter plateau scanning."""

from __future__ import annotations

from itertools import product
from typing import Any, Callable

import pandas as pd

from ashare_trader.engine.backtest import BacktestEngine
from ashare_trader.models import BacktestConfig, BacktestResult
from ashare_trader.strategies.base import BaseStrategy


def run_strategy_benchmark(
    strategies: list[BaseStrategy],
    close_df: pd.DataFrame,
    high_df: pd.DataFrame,
    low_df: pd.DataFrame,
    open_df: pd.DataFrame,
    backtest_config: BacktestConfig,
    start_date: str,
    end_date: str | None = None,
    benchmark_symbol: str | None = None,
    risk_config: dict[str, Any] | None = None,
    use_risk_overlay: bool = False,
) -> tuple[dict[str, BacktestResult], pd.DataFrame]:
    """Run all strategies on identical data and friction assumptions and return summary table."""
    engine = BacktestEngine(
        config=backtest_config,
        risk_config=risk_config,
        use_risk_overlay=use_risk_overlay,
    )
    results: dict[str, BacktestResult] = {}
    rows: list[dict[str, Any]] = []

    for strat in strategies:
        res = engine.run(
            strategy=strat,
            close_df=close_df,
            high_df=high_df,
            low_df=low_df,
            open_df=open_df,
            start_date=start_date,
            end_date=end_date,
            benchmark_symbol=benchmark_symbol,
        )
        results[strat.name] = res
        rows.append({"strategy": strat.name, **res.metrics})

    return results, pd.DataFrame(rows)


def evaluate_is_oos(
    strategy_factory: Callable[[], list[BaseStrategy]],
    close_df: pd.DataFrame,
    high_df: pd.DataFrame,
    low_df: pd.DataFrame,
    open_df: pd.DataFrame,
    backtest_config: BacktestConfig,
    is_start: str,
    is_end: str,
    oos_start: str,
    oos_end: str | None = None,
    benchmark_symbol: str | None = None,
) -> pd.DataFrame:
    """Compare In-Sample vs Out-of-Sample metrics to detect overfitting decay."""
    _, is_df = run_strategy_benchmark(
        strategies=strategy_factory(),
        close_df=close_df,
        high_df=high_df,
        low_df=low_df,
        open_df=open_df,
        backtest_config=backtest_config,
        start_date=is_start,
        end_date=is_end,
        benchmark_symbol=benchmark_symbol,
    )
    _, oos_df = run_strategy_benchmark(
        strategies=strategy_factory(),
        close_df=close_df,
        high_df=high_df,
        low_df=low_df,
        open_df=open_df,
        backtest_config=backtest_config,
        start_date=oos_start,
        end_date=oos_end,
        benchmark_symbol=benchmark_symbol,
    )

    merged = is_df[["strategy", "cagr", "max_drawdown", "calmar", "sharpe", "ann_turnover"]].merge(
        oos_df[["strategy", "cagr", "max_drawdown", "calmar", "sharpe", "ann_turnover"]],
        on="strategy",
        suffixes=("_IS", "_OOS"),
    )
    merged["sharpe_retention"] = (merged["sharpe_OOS"] / merged["sharpe_IS"].replace(0, pd.NA)).round(2)
    return merged


def parameter_plateau_scan(
    strategy_builder: Callable[[dict[str, Any]], BaseStrategy],
    param_grid: dict[str, list[Any]],
    close_df: pd.DataFrame,
    high_df: pd.DataFrame,
    low_df: pd.DataFrame,
    open_df: pd.DataFrame,
    backtest_config: BacktestConfig,
    start_date: str,
    end_date: str | None = None,
) -> pd.DataFrame:
    """Scan a parameter grid and return performance surface to verify parameter stability."""
    engine = BacktestEngine(config=backtest_config)
    keys = list(param_grid.keys())
    rows: list[dict[str, Any]] = []

    for values in product(*param_grid.values()):
        params = dict(zip(keys, values, strict=True))
        strat = strategy_builder(params)
        res = engine.run(
            strategy=strat,
            close_df=close_df,
            high_df=high_df,
            low_df=low_df,
            open_df=open_df,
            start_date=start_date,
            end_date=end_date,
        )
        rows.append(
            {
                **params,
                "cagr": res.metrics.get("cagr"),
                "max_drawdown": res.metrics.get("max_drawdown"),
                "calmar": res.metrics.get("calmar"),
                "sharpe": res.metrics.get("sharpe"),
                "ann_turnover": res.metrics.get("ann_turnover"),
            }
        )
    return pd.DataFrame(rows)
