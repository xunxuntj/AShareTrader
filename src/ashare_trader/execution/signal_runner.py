"""Daily signal pipeline connecting strategy suite, ledger diffing, and storage."""

from __future__ import annotations

from typing import Any

from ashare_trader.data.manager import DataManager
from ashare_trader.execution.ledger import LedgerService
from ashare_trader.models import TradeInstruction, UniverseConfig
from ashare_trader.storage.base import StorageBackend
from ashare_trader.strategies.base import BaseStrategy
from ashare_trader.strategies.combiner import CombinedStrategy
from ashare_trader.strategies.momentum_r2 import MomentumR2Strategy
from ashare_trader.strategies.risk_parity import RiskParityMomentumStrategy
from ashare_trader.strategies.trend_atr import TrendATRStrategy


def build_default_strategies(
    safe_haven_symbol: str,
    strat_cfg: dict[str, Any],
) -> tuple[list[BaseStrategy], CombinedStrategy]:
    """Instantiate the 3 orthogonal strategies plus the weighted CombinedStrategy."""
    s_cfg = strat_cfg.get("strategies", {})
    strategies: list[BaseStrategy] = []
    if s_cfg.get("momentum_r2", {}).get("enabled", True):
        strategies.append(MomentumR2Strategy(safe_haven_symbol, s_cfg.get("momentum_r2", {})))
    if s_cfg.get("trend_atr", {}).get("enabled", True):
        strategies.append(TrendATRStrategy(safe_haven_symbol, s_cfg.get("trend_atr", {})))
    if s_cfg.get("risk_parity", {}).get("enabled", True):
        strategies.append(RiskParityMomentumStrategy(safe_haven_symbol, s_cfg.get("risk_parity", {})))

    comb_weights = strat_cfg.get("combiner", {}).get(
        "weights",
        {"momentum_r2": 0.40, "trend_atr": 0.35, "risk_parity": 0.25},
    )
    combined = CombinedStrategy(
        strategies=strategies,
        strategy_weights=comb_weights,
        safe_haven_symbol=safe_haven_symbol,
        name="combined",
    )
    return strategies, combined


def run_daily_signal(
    storage: StorageBackend,
    universe: UniverseConfig,
    strat_cfg: dict[str, Any],
    run_mode: str = "intraday",
    lot_size: int = 100,
    rebalance_threshold: float = 0.03,
    persist: bool = True,
) -> tuple[list[TradeInstruction], dict[str, float]]:
    """Compute latest target weights, diff against ledger positions, and optionally persist."""
    dm = DataManager(storage=storage)
    all_syms = [a.symbol for a in universe.all_assets]
    close_df = dm.load_price_panel(all_syms, field="close")
    high_df = dm.load_price_panel(all_syms, field="high")
    low_df = dm.load_price_panel(all_syms, field="low")

    if close_df.empty:
        raise RuntimeError("No market data found in storage. Run sync-data first.")

    latest_prices: dict[str, float] = {}
    for sym in close_df.columns:
        s = close_df[sym].dropna()
        if not s.empty:
            latest_prices[sym] = float(s.iloc[-1])

    ledger = LedgerService(storage)
    positions = ledger.get_positions()
    total_eq = ledger.total_equity(latest_prices)
    current_weights = {
        sym: (pos.shares * latest_prices.get(sym, pos.avg_cost)) / total_eq
        for sym, pos in positions.items()
        if total_eq > 0 and pos.shares > 0
    }

    _, combined = build_default_strategies(universe.safe_haven.symbol, strat_cfg)
    # Hydrate persisted trailing stops from ledger so cold-start runs respect prior high-water stops
    persisted_stops = {
        sym: pos.stop_loss_price
        for sym, pos in positions.items()
        if pos.shares > 0 and pos.stop_loss_price is not None and pos.stop_loss_price > 0
    }
    combined.seed_stop_losses(persisted_stops)
    signal = combined.generate_signal(
        close_df=close_df,
        high_df=high_df,
        low_df=low_df,
        current_weights=current_weights,
    )

    if persist:
        ledger.refresh_trailing_stops(latest_prices, signal.stop_losses)

    instructions: list[TradeInstruction] = []
    involved = sorted(set(current_weights.keys()) | set(signal.target_weights.keys()))
    for sym in involved:
        cur_w = current_weights.get(sym, 0.0)
        tgt_w = signal.target_weights.get(sym, 0.0)
        px = latest_prices.get(sym)
        diff_w = tgt_w - cur_w

        if tgt_w <= 1e-4 and cur_w > 1e-4:
            action = "SELL"
            sugg_shares = positions[sym].shares if sym in positions else 0
        elif diff_w > rebalance_threshold:
            action = "BUY"
            sugg_shares = int((total_eq * diff_w / px) // lot_size) * lot_size if px and px > 0 else 0
        elif diff_w < -rebalance_threshold:
            action = "SELL"
            sugg_shares = int((total_eq * abs(diff_w) / px) // lot_size) * lot_size if px and px > 0 else 0
        else:
            action = "HOLD"
            sugg_shares = 0

        instructions.append(
            TradeInstruction(
                signal_date=signal.signal_date,
                run_mode=run_mode,  # type: ignore[arg-type]
                strategy_name=signal.strategy_name,
                symbol=sym,
                action=action,  # type: ignore[arg-type]
                target_weight=round(tgt_w, 4),
                ref_price=round(px, 4) if px is not None else None,
                stop_loss_price=signal.stop_losses.get(sym),
                suggested_shares=sugg_shares,
                reason=signal.reasons.get(sym, ""),
            )
        )

    if persist and instructions:
        storage.save_signals(instructions)

    return instructions, latest_prices
