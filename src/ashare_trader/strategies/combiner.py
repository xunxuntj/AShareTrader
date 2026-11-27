"""Portfolio combiner blending multiple orthogonal strategies by weight."""

from __future__ import annotations

import pandas as pd

from ashare_trader.models import StrategySignal
from ashare_trader.strategies.base import BaseStrategy


class CombinedStrategy(BaseStrategy):
    """Combines multiple sub-strategies into a single weighted target allocation."""

    def __init__(
        self,
        strategies: list[BaseStrategy],
        strategy_weights: dict[str, float],
        safe_haven_symbol: str,
        name: str = "combined",
    ) -> None:
        super().__init__(name=name, safe_haven_symbol=safe_haven_symbol, params={"weights": strategy_weights})
        self.strategies = strategies
        total = sum(strategy_weights.get(s.name, 0.0) for s in strategies)
        if total <= 0:
            raise ValueError("Total strategy weight in CombinedStrategy must be positive.")
        self.strategy_weights = {s.name: strategy_weights.get(s.name, 0.0) / total for s in strategies}
        self._sub_current_weights: dict[str, dict[str, float]] = {s.name: {} for s in strategies}

    def reset_state(self) -> None:
        for strat in self.strategies:
            strat.reset_state()
        self._sub_current_weights = {s.name: {} for s in self.strategies}

    def generate_signal(
        self,
        close_df: pd.DataFrame,
        high_df: pd.DataFrame,
        low_df: pd.DataFrame,
        current_weights: dict[str, float],
    ) -> StrategySignal:
        sig_date = close_df.index[-1].date()
        blended_weights: dict[str, float] = {}
        blended_stops: dict[str, float] = {}
        blended_reasons: dict[str, list[str]] = {}
        blended_scores: dict[str, float] = {}

        for strat in self.strategies:
            sw = self.strategy_weights.get(strat.name, 0.0)
            if sw <= 0:
                continue
            sub_sig = strat.generate_signal(
                close_df=close_df,
                high_df=high_df,
                low_df=low_df,
                current_weights=self._sub_current_weights.get(strat.name, {}),
            )
            self._sub_current_weights[strat.name] = dict(sub_sig.target_weights)

            for sym, w in sub_sig.target_weights.items():
                blended_weights[sym] = blended_weights.get(sym, 0.0) + w * sw
            for sym, stop_px in sub_sig.stop_losses.items():
                # Take the tighter (higher) stop-loss across active sub-strategies
                if sym not in blended_stops or stop_px > blended_stops[sym]:
                    blended_stops[sym] = stop_px
            for sym, reason in sub_sig.reasons.items():
                if reason:
                    blended_reasons.setdefault(sym, []).append(f"[{strat.name}] {reason}")
            for sym, sc in sub_sig.scores.items():
                blended_scores[sym] = round(blended_scores.get(sym, 0.0) + sc * sw, 3)

        norm_weights = self._normalize_weights(blended_weights)
        return StrategySignal(
            signal_date=sig_date,
            strategy_name=self.name,
            target_weights=norm_weights,
            stop_losses={sym: px for sym, px in blended_stops.items() if sym in norm_weights},
            reasons={sym: " | ".join(r) for sym, r in blended_reasons.items() if sym in norm_weights},
            scores=blended_scores,
        )
