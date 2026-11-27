"""Portfolio-level and position-level risk control overlay."""

from __future__ import annotations

from typing import Any


class RiskManager:
    """Applies portfolio drawdown de-risking and single-position trailing stop checks."""

    def __init__(self, safe_haven_symbol: str, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.safe_haven_symbol = safe_haven_symbol
        self.portfolio_dd_reduce = float(cfg.get("portfolio_dd_reduce", 0.12))
        self.portfolio_dd_recover = float(cfg.get("portfolio_dd_recover", 0.06))
        self._peak_equity: float = 0.0
        self._derisked: bool = False

    def reset(self) -> None:
        self._peak_equity = 0.0
        self._derisked = False

    def apply(
        self,
        target_weights: dict[str, float],
        current_equity: float,
        latest_prices: dict[str, float],
        stop_losses: dict[str, float],
    ) -> tuple[dict[str, float], list[str]]:
        """Adjust target weights based on portfolio drawdown and stop-loss breaches."""
        notes: list[str] = []
        adjusted = dict(target_weights)

        # 1. Single-asset stop-loss enforcement
        stopped_weight = 0.0
        for sym in list(adjusted.keys()):
            if sym == self.safe_haven_symbol:
                continue
            stop_px = stop_losses.get(sym)
            px = latest_prices.get(sym)
            if stop_px is not None and px is not None and px < stop_px:
                stopped_weight += adjusted.pop(sym)
                notes.append(f"{sym} 跌破止损线 {stop_px:.3f} (现价 {px:.3f})，移出转避险")
        if stopped_weight > 0:
            adjusted[self.safe_haven_symbol] = adjusted.get(self.safe_haven_symbol, 0.0) + stopped_weight

        # 2. Portfolio-level drawdown circuit breaker (hysteresis)
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
        dd = (self._peak_equity - current_equity) / self._peak_equity if self._peak_equity > 0 else 0.0

        if not self._derisked and dd >= self.portfolio_dd_reduce:
            self._derisked = True
            notes.append(f"组合回撤达 {dd:.1%} >= {self.portfolio_dd_reduce:.0%}，触发风险仓位减半保护")
        elif self._derisked and dd <= self.portfolio_dd_recover:
            self._derisked = False
            notes.append(f"组合回撤修复至 {dd:.1%} <= {self.portfolio_dd_recover:.0%}，恢复正常仓位")

        if self._derisked:
            risky_cut = 0.0
            for sym in list(adjusted.keys()):
                if sym == self.safe_haven_symbol:
                    continue
                cut = adjusted[sym] * 0.5
                adjusted[sym] -= cut
                risky_cut += cut
            if risky_cut > 0:
                adjusted[self.safe_haven_symbol] = adjusted.get(self.safe_haven_symbol, 0.0) + risky_cut

        return adjusted, notes
