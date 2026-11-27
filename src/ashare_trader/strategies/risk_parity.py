"""Strategy C: Inverse-volatility Risk Parity with momentum tilt and volatility targeting."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ashare_trader.models import StrategySignal
from ashare_trader.strategies.base import BaseStrategy, compute_atr


class RiskParityMomentumStrategy(BaseStrategy):
    """Allocates inversely proportional to realized volatility with mild momentum tilt."""

    def __init__(self, safe_haven_symbol: str, params: dict[str, Any] | None = None) -> None:
        defaults = {
            "vol_window": 60,
            "mom_window": 20,
            "tilt_high": 1.25,
            "tilt_low": 0.75,
            "target_vol": 0.10,
            "max_leverage": 1.0,
            "rebalance_interval_days": 10,
            "atr_window": 14,
            "atr_stop_mult": 3.0,
            "min_history_days": 65,
        }
        merged = {**defaults, **(params or {})}
        super().__init__(name="risk_parity", safe_haven_symbol=safe_haven_symbol, params=merged)
        self._days_since_rebalance = 999

    def reset_state(self) -> None:
        self._days_since_rebalance = 999

    def generate_signal(
        self,
        close_df: pd.DataFrame,
        high_df: pd.DataFrame,
        low_df: pd.DataFrame,
        current_weights: dict[str, float],
    ) -> StrategySignal:
        sig_date = close_df.index[-1].date()
        vol_win = int(self.params["vol_window"])
        mom_win = int(self.params["mom_window"])
        tilt_hi = float(self.params["tilt_high"])
        tilt_lo = float(self.params["tilt_low"])
        target_vol = float(self.params["target_vol"])
        max_lev = float(self.params["max_leverage"])
        rebal_days = int(self.params["rebalance_interval_days"])
        min_hist = max(int(self.params["min_history_days"]), vol_win + 2, mom_win + 2)

        self._days_since_rebalance += 1
        if current_weights and self._days_since_rebalance < rebal_days:
            # Hold existing allocation between scheduled rebalance dates
            return StrategySignal(
                signal_date=sig_date,
                strategy_name=self.name,
                target_weights=self._normalize_weights(current_weights),
                stop_losses={},
                reasons={sym: "定期再平衡窗口内维持仓位" for sym in current_weights},
                scores={},
            )

        eligible = self._eligible_symbols(close_df, min_history=min_hist)
        if not eligible:
            return StrategySignal(
                signal_date=sig_date,
                strategy_name=self.name,
                target_weights={self.safe_haven_symbol: 1.0},
                stop_losses={},
                reasons={self.safe_haven_symbol: "有效历史不足，暂泊避险"},
                scores={},
            )

        vols: dict[str, float] = {}
        moms: dict[str, float] = {}
        for sym in eligible:
            c = close_df[sym].dropna()
            rets = c.pct_change().dropna().iloc[-vol_win:]
            ann_vol = float(rets.std() * np.sqrt(252.0))
            if not np.isfinite(ann_vol) or ann_vol <= 1e-4:
                continue
            vols[sym] = ann_vol
            moms[sym] = float(c.iloc[-1] / c.iloc[-mom_win - 1] - 1.0)

        if not vols:
            return StrategySignal(
                signal_date=sig_date,
                strategy_name=self.name,
                target_weights={self.safe_haven_symbol: 1.0},
                stop_losses={},
                reasons={self.safe_haven_symbol: "波动率计算异常，停泊避险"},
                scores={},
            )

        median_mom = float(np.median(list(moms.values())))
        raw_w: dict[str, float] = {}
        for sym, v in vols.items():
            tilt = tilt_hi if moms[sym] >= median_mom else tilt_lo
            raw_w[sym] = (1.0 / v) * tilt

        w_sum = sum(raw_w.values())
        base_weights = {sym: w / w_sum for sym, w in raw_w.items()}

        # Estimate portfolio realized volatility using covariance matrix
        ret_matrix = close_df[list(base_weights.keys())].pct_change().dropna().iloc[-vol_win:]
        if len(ret_matrix) >= 20:
            w_vec = np.array([base_weights[s] for s in ret_matrix.columns], dtype=float)
            cov = ret_matrix.cov().to_numpy(dtype=float) * 252.0
            port_vol = float(np.sqrt(max(1e-8, w_vec @ cov @ w_vec)))
            scale = min(max_lev, target_vol / port_vol)
        else:
            scale = 1.0

        final_weights = {sym: w * scale for sym, w in base_weights.items()}
        if scale < 1.0 - 1e-4:
            final_weights[self.safe_haven_symbol] = 1.0 - scale

        stop_losses: dict[str, float] = {}
        atr_win = int(self.params["atr_window"])
        atr_mult = float(self.params["atr_stop_mult"])
        reasons: dict[str, str] = {}
        for sym in base_weights:
            px = float(close_df[sym].dropna().iloc[-1])
            atr = compute_atr(high_df[sym], low_df[sym], close_df[sym], window=atr_win)
            stop_losses[sym] = self._safe_stop_price(px, atr, atr_mult)
            reasons[sym] = f"风险平价(波动={vols[sym]:.1%}, 20日动量={moms[sym]:+.1%}, 缩放={scale:.0%})"
        if self.safe_haven_symbol in final_weights:
            reasons[self.safe_haven_symbol] = f"波动率目标({target_vol:.0%})控仓留存避险"

        self._days_since_rebalance = 0
        return StrategySignal(
            signal_date=sig_date,
            strategy_name=self.name,
            target_weights=self._normalize_weights(final_weights),
            stop_losses=stop_losses,
            reasons=reasons,
            scores={sym: round(moms[sym] * 100, 2) for sym in base_weights},
        )
