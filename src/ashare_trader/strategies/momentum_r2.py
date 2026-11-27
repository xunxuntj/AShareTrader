"""Strategy A: R2-weighted log-linear momentum rotation with trend filter and switch buffer."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ashare_trader.models import StrategySignal
from ashare_trader.strategies.base import BaseStrategy, compute_atr


class MomentumR2Strategy(BaseStrategy):
    """Scores each ETF by (annualized log-linear regression slope * R^2)."""

    def __init__(self, safe_haven_symbol: str, params: dict[str, Any] | None = None) -> None:
        defaults = {
            "lookback": 25,
            "ma_filter": 20,
            "top_k": 2,
            "min_score": 0.0,
            "switch_buffer": 0.15,
            "atr_window": 14,
            "atr_stop_mult": 2.5,
            "min_history_days": 60,
        }
        merged = {**defaults, **(params or {})}
        super().__init__(name="momentum_r2", safe_haven_symbol=safe_haven_symbol, params=merged)

    @staticmethod
    def _calc_r2_momentum(series: pd.Series, lookback: int) -> tuple[float, float, float]:
        """Return (score, annualized_return, r2) using log-price linear regression."""
        clean = series.dropna()
        if len(clean) < lookback:
            return float("nan"), float("nan"), float("nan")
        y = np.log(clean.iloc[-lookback:].to_numpy(dtype=float))
        x = np.arange(lookback, dtype=float)
        x_mean = x.mean()
        y_mean = y.mean()
        x_diff = x - x_mean
        y_diff = y - y_mean
        denom = np.sum(x_diff * x_diff)
        if denom <= 0:
            return 0.0, 0.0, 0.0
        slope = float(np.sum(x_diff * y_diff) / denom)
        y_pred = slope * x_diff + y_mean
        ss_res = float(np.sum((y - y_pred) ** 2))
        ss_tot = float(np.sum(y_diff * y_diff))
        r2 = max(0.0, 1.0 - ss_res / ss_tot) if ss_tot > 1e-12 else 0.0
        ann_ret = float(np.exp(slope * 252.0) - 1.0)
        score = ann_ret * r2
        return score, ann_ret, r2

    def generate_signal(
        self,
        close_df: pd.DataFrame,
        high_df: pd.DataFrame,
        low_df: pd.DataFrame,
        current_weights: dict[str, float],
    ) -> StrategySignal:
        sig_date = close_df.index[-1].date()
        lookback = int(self.params["lookback"])
        ma_filter = int(self.params["ma_filter"])
        top_k = int(self.params["top_k"])
        min_score = float(self.params["min_score"])
        switch_buffer = float(self.params["switch_buffer"])
        min_hist = max(int(self.params["min_history_days"]), lookback + 5, ma_filter + 5)

        eligible = self._eligible_symbols(close_df, min_history=min_hist)
        scores: dict[str, float] = {}
        passed: dict[str, float] = {}
        reasons: dict[str, str] = {}

        for sym in eligible:
            series = close_df[sym].dropna()
            score, ann_ret, r2 = self._calc_r2_momentum(series, lookback)
            if not np.isfinite(score):
                continue
            scores[sym] = round(score, 4)
            ma_val = float(series.iloc[-ma_filter:].mean())
            latest_px = float(series.iloc[-1])
            if score > min_score and latest_px >= ma_val:
                passed[sym] = score
                reasons[sym] = f"R2动量={score:.2f} (年化={ann_ret:.1%}, R²={r2:.2f}, 站上MA{ma_filter})"

        if not passed:
            return StrategySignal(
                signal_date=sig_date,
                strategy_name=self.name,
                target_weights={self.safe_haven_symbol: 1.0},
                stop_losses={},
                reasons={self.safe_haven_symbol: "全池动量转弱或跌破均线，切换避险底仓"},
                scores=scores,
            )

        ranked = sorted(passed.items(), key=lambda kv: kv[1], reverse=True)
        # Apply switch buffer: retain currently held qualifying assets unless challenger beats by buffer
        held_risky = [
            sym
            for sym, w in current_weights.items()
            if sym != self.safe_haven_symbol and w > 0.05 and sym in passed
        ]
        selected: list[str] = []
        for sym in held_risky:
            selected.append(sym)

        for cand, cand_score in ranked:
            if cand in selected:
                continue
            if len(selected) < top_k:
                selected.append(cand)
            else:
                # Replace weakest held asset only if challenger exceeds it by switch_buffer
                weakest = min(selected, key=lambda s: passed[s])
                weakest_score = passed[weakest]
                threshold = weakest_score * (1.0 + switch_buffer) if weakest_score > 0 else weakest_score + 0.05
                if cand_score > threshold:
                    selected.remove(weakest)
                    selected.append(cand)

        selected = selected[:top_k]
        weight_each = 1.0 / len(selected)
        target_weights = {sym: weight_each for sym in selected}

        stop_losses: dict[str, float] = {}
        atr_win = int(self.params["atr_window"])
        atr_mult = float(self.params["atr_stop_mult"])
        for sym in selected:
            px = float(close_df[sym].dropna().iloc[-1])
            atr = compute_atr(high_df[sym], low_df[sym], close_df[sym], window=atr_win)
            stop_losses[sym] = self._safe_stop_price(px, atr, atr_mult)

        return StrategySignal(
            signal_date=sig_date,
            strategy_name=self.name,
            target_weights=self._normalize_weights(target_weights),
            stop_losses=stop_losses,
            reasons={sym: reasons.get(sym, "") for sym in selected},
            scores=scores,
        )
