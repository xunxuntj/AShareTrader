"""Strategy B: Multi-asset Donchian/EMA trend breakout with ATR inverse-vol sizing and trailing stop."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ashare_trader.models import StrategySignal
from ashare_trader.strategies.base import BaseStrategy, compute_atr


class TrendATRStrategy(BaseStrategy):
    """Independent trend-following per asset with ATR trailing stops."""

    def __init__(self, safe_haven_symbol: str, params: dict[str, Any] | None = None) -> None:
        defaults = {
            "breakout_window": 20,
            "ema_fast": 10,
            "ema_slow": 30,
            "atr_window": 14,
            "atr_stop_mult": 2.5,
            "max_single_weight": 0.35,
            "min_history_days": 60,
        }
        merged = {**defaults, **(params or {})}
        super().__init__(name="trend_atr", safe_haven_symbol=safe_haven_symbol, params=merged)
        self._active_stops: dict[str, float] = {}

    def reset_state(self) -> None:
        self._active_stops.clear()

    def seed_stop_losses(self, existing_stops: dict[str, float]) -> None:
        """Restore persisted trailing stops so stateless daily cloud runs never lower a stop."""
        for sym, stop_px in existing_stops.items():
            if stop_px is not None and stop_px > 0:
                prev = self._active_stops.get(sym, 0.0)
                self._active_stops[sym] = max(prev, float(stop_px))

    def generate_signal(
        self,
        close_df: pd.DataFrame,
        high_df: pd.DataFrame,
        low_df: pd.DataFrame,
        current_weights: dict[str, float],
    ) -> StrategySignal:
        sig_date = close_df.index[-1].date()
        bo_win = int(self.params["breakout_window"])
        ema_fast_n = int(self.params["ema_fast"])
        ema_slow_n = int(self.params["ema_slow"])
        atr_win = int(self.params["atr_window"])
        atr_mult = float(self.params["atr_stop_mult"])
        max_w = float(self.params["max_single_weight"])
        min_hist = max(int(self.params["min_history_days"]), ema_slow_n + 10, bo_win + 5)

        eligible = self._eligible_symbols(close_df, min_history=min_hist)
        active_inv_vol: dict[str, float] = {}
        stop_losses: dict[str, float] = {}
        reasons: dict[str, str] = {}
        scores: dict[str, float] = {}

        tail_len = max(ema_slow_n * 5, bo_win + 10, atr_win + 10)
        for sym in eligible:
            c = close_df[sym].dropna().iloc[-tail_len:]
            h = high_df[sym].reindex(c.index).ffill()
            l = low_df[sym].reindex(c.index).ffill()
            px = float(c.iloc[-1])
            ema_f = float(c.ewm(span=ema_fast_n, adjust=False).mean().iloc[-1])
            ema_s = float(c.ewm(span=ema_slow_n, adjust=False).mean().iloc[-1])
            prev_high = float(h.iloc[-bo_win - 1 : -1].max())
            atr = compute_atr(h, l, c, window=atr_win)
            atr_pct = (atr / px) if (np.isfinite(atr) and px > 0) else 0.02

            is_held = current_weights.get(sym, 0.0) > 0.02
            scores[sym] = round((ema_f / ema_s - 1.0) * 100.0, 2) if ema_s > 0 else 0.0

            if is_held:
                prev_stop = self._active_stops.get(sym, self._safe_stop_price(px, atr, atr_mult))
                new_stop = max(prev_stop, self._safe_stop_price(px, atr, atr_mult))
                if px < new_stop or ema_f < ema_s:
                    # Trigger trailing stop or trend reversal exit
                    self._active_stops.pop(sym, None)
                    continue
                self._active_stops[sym] = new_stop
                active_inv_vol[sym] = 1.0 / max(atr_pct, 0.005)
                stop_losses[sym] = new_stop
                reasons[sym] = f"趋势延续(EMA{ema_fast_n}>{ema_slow_n})，移动止损={new_stop:.3f}"
            else:
                if px > prev_high and ema_f > ema_s:
                    init_stop = self._safe_stop_price(px, atr, atr_mult)
                    self._active_stops[sym] = init_stop
                    active_inv_vol[sym] = 1.0 / max(atr_pct, 0.005)
                    stop_losses[sym] = init_stop
                    reasons[sym] = f"突破{bo_win}日新高且EMA多头，初始止损={init_stop:.3f}"

        # Clean stale stops for symbols no longer active
        for sym in list(self._active_stops.keys()):
            if sym not in active_inv_vol:
                self._active_stops.pop(sym, None)

        if not active_inv_vol:
            return StrategySignal(
                signal_date=sig_date,
                strategy_name=self.name,
                target_weights={self.safe_haven_symbol: 1.0},
                stop_losses={},
                reasons={self.safe_haven_symbol: "无突破或趋势持仓，停泊避险底仓"},
                scores=scores,
            )

        total_inv = sum(active_inv_vol.values())
        raw_weights = {sym: min(max_w, val / total_inv) for sym, val in active_inv_vol.items()}
        risky_sum = sum(raw_weights.values())
        if risky_sum < 1.0 - 1e-4:
            raw_weights[self.safe_haven_symbol] = 1.0 - risky_sum
            reasons[self.safe_haven_symbol] = f"单标的权重上限保护，剩余{(1.0 - risky_sum):.1%}停泊避险"
        elif risky_sum > 1.0:
            raw_weights = {sym: w / risky_sum for sym, w in raw_weights.items()}

        return StrategySignal(
            signal_date=sig_date,
            strategy_name=self.name,
            target_weights=self._normalize_weights(raw_weights),
            stop_losses=stop_losses,
            reasons=reasons,
            scores=scores,
        )
