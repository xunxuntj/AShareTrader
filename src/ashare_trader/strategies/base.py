"""Abstract strategy interface ensuring strict no-lookahead execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np
import pandas as pd

from ashare_trader.models import StrategySignal


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 14,
) -> float:
    """Compute latest Average True Range for a single asset using tail window for speed."""
    tail_n = window + 5
    h = high.dropna().iloc[-tail_n:]
    l = low.dropna().iloc[-tail_n:]
    c = close.dropna().iloc[-tail_n:]
    df = pd.DataFrame({"high": h, "low": l, "close": c}).dropna()
    if len(df) < window + 1:
        return float("nan")
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.iloc[-window:].mean()
    return float(atr) if pd.notna(atr) else float("nan")


class BaseStrategy(ABC):
    """Base class for all allocation strategies."""

    def __init__(self, name: str, safe_haven_symbol: str, params: dict[str, Any] | None = None) -> None:
        self.name = name
        self.safe_haven_symbol = safe_haven_symbol
        self.params = params or {}

    def reset_state(self) -> None:
        """Reset any stateful trackers before starting a fresh backtest."""

    @abstractmethod
    def generate_signal(
        self,
        close_df: pd.DataFrame,
        high_df: pd.DataFrame,
        low_df: pd.DataFrame,
        current_weights: dict[str, float],
    ) -> StrategySignal:
        """Compute target portfolio weights using ONLY data up to close_df.index[-1]."""

    def _eligible_symbols(self, close_df: pd.DataFrame, min_history: int) -> list[str]:
        """Return risky symbols that are listed and have at least min_history valid bars up to today."""
        eligible: list[str] = []
        latest_row = close_df.iloc[-1]
        for sym in close_df.columns:
            if sym == self.safe_haven_symbol:
                continue
            if pd.isna(latest_row[sym]) or latest_row[sym] <= 0:
                continue
            valid_count = int(close_df[sym].dropna().shape[0])
            if valid_count >= min_history:
                eligible.append(sym)
        return eligible

    @staticmethod
    def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
        clean = {k: max(0.0, float(v)) for k, v in weights.items() if v > 1e-6}
        total = sum(clean.values())
        if total <= 0:
            return {}
        if total > 1.0 + 1e-6:
            return {k: v / total for k, v in clean.items()}
        return clean

    @staticmethod
    def _safe_stop_price(price: float, atr: float, atr_mult: float, hard_stop_pct: float = 0.08) -> float:
        if np.isfinite(atr) and atr > 0:
            stop = price - atr_mult * atr
        else:
            stop = price * (1.0 - hard_stop_pct)
        return round(max(0.001, stop), 4)
