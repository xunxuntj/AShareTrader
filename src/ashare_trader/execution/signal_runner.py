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


def compute_entry_guidance(
    action: str,
    ref_price: float | None,
    stop_loss_price: float | None,
    is_safe_haven: bool = False,
) -> dict[str, str]:
    """Compute human-actionable entry price range and empty-account guidance for Feishu & Web UI."""
    if is_safe_haven or (stop_loss_price is None or stop_loss_price <= 0):
        if action == "SELL":
            return {"action_label": "卖出/减仓", "guidance": "避险底仓调出，按现价附近卖出换仓"}
        return {
            "action_label": "买入/避险停泊" if action == "BUY" else "持有 / 空仓可买",
            "guidance": "避险货币底仓，无需止损，现价附近随时可买",
        }

    if ref_price is None or ref_price <= 0:
        return {"action_label": action, "guidance": f"止损线 {stop_loss_price:.3f}"}

    if action == "SELL":
        return {"action_label": "卖出/清仓", "guidance": f"趋势破位或触发止损，参考价 {ref_price:.3f} 附近离场"}

    risk_pct = max(0.0, (ref_price - stop_loss_price) / ref_price)
    low_px = max(stop_loss_price * 1.005, ref_price * 0.985)
    high_px = min(stop_loss_price * 1.075, ref_price * 1.01)

    if action == "BUY":
        buy_low = max(stop_loss_price * 1.005, ref_price * 0.99)
        buy_high = ref_price * 1.01
        return {
            "action_label": "买入/加仓",
            "guidance": f"建议入场区间 {buy_low:.3f} ~ {buy_high:.3f} | 止损 {stop_loss_price:.3f} (距止损 {risk_pct:.1%})",
        }

    # action == "HOLD": provide clear guidance for empty-account users joining mid-trend
    if risk_pct <= 0.065:
        return {
            "action_label": "持有 / 空仓可买",
            "guidance": f"建议入场区间 {low_px:.3f} ~ {max(low_px, high_px):.3f} | 止损 {stop_loss_price:.3f} (距止损 {risk_pct:.1%})",
        }
    if risk_pct <= 0.095:
        return {
            "action_label": "持有 / 空仓半仓",
            "guidance": f"可回踩 {low_px:.3f} ~ {ref_price:.3f} 轻仓试水 | 止损 {stop_loss_price:.3f} (距止损 {risk_pct:.1%})",
        }
    wait_px = stop_loss_price * 1.07
    return {
        "action_label": "持有 / 空仓勿追",
        "guidance": f"偏离止损较远，空仓建议等回踩 ≤ {wait_px:.3f} | 止损 {stop_loss_price:.3f} (距止损 {risk_pct:.1%})",
    }


def compute_suggested_shares(
    action: str,
    target_weight: float,
    current_shares: int,
    total_equity: float,
    ref_price: float | None,
    lot_size: int = 100,
) -> int:
    """Compute actionable A-Share lot-rounded shares consistent across Feishu and Web UI."""
    if ref_price is None or ref_price <= 0 or total_equity <= 0:
        return 0
    if action == "SELL" and target_weight <= 1e-4:
        return int(current_shares)
    cur_weight = (current_shares * ref_price) / total_equity
    diff_w = target_weight - cur_weight
    if action == "BUY" and diff_w > 0:
        return int((total_equity * diff_w / ref_price) // lot_size) * lot_size
    if action == "SELL" and diff_w < 0:
        qty = int((total_equity * abs(diff_w) / ref_price) // lot_size) * lot_size
        return min(int(current_shares), qty) if current_shares > 0 else qty
    return 0


def run_shared_model_signal(
    storage: StorageBackend,
    universe: UniverseConfig,
    strat_cfg: dict[str, Any],
    run_mode: str = "intraday",
    lot_size: int = 100,
    rebalance_threshold: float = 0.03,
    ref_capital: float = 100000.0,
    rolling_days: int = 250,
    persist: bool = True,
) -> tuple[list[TradeInstruction], dict[str, float]]:
    """Stateless rolling model signal (Plan 1): rolls strategy over recent ~1Y history to get zero-drift
    theoretical yesterday vs today weights, trailing stops, and per-100k reference shares."""
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

    _, combined = build_default_strategies(universe.safe_haven.symbol, strat_cfg)
    combined.reset_state()

    n_rows = len(close_df)
    start_idx = max(60, n_rows - rolling_days)
    if start_idx >= n_rows:
        start_idx = max(0, n_rows - 1)

    prev_weights: dict[str, float] = {}
    latest_signal = None
    for idx in range(start_idx, n_rows):
        sig = combined.generate_signal(
            close_df=close_df.iloc[: idx + 1],
            high_df=high_df.iloc[: idx + 1],
            low_df=low_df.iloc[: idx + 1],
            current_weights=prev_weights,
        )
        if idx < n_rows - 1:
            prev_weights = dict(sig.target_weights)
        latest_signal = sig

    assert latest_signal is not None
    instructions: list[TradeInstruction] = []
    involved = sorted(set(prev_weights.keys()) | set(latest_signal.target_weights.keys()))
    for sym in involved:
        prev_w = prev_weights.get(sym, 0.0)
        tgt_w = latest_signal.target_weights.get(sym, 0.0)
        px = latest_prices.get(sym)
        diff_w = tgt_w - prev_w

        if tgt_w <= 1e-4 and prev_w > 1e-4:
            action = "SELL"
            sugg_shares = int((ref_capital * prev_w / px) // lot_size) * lot_size if px and px > 0 else 0
        elif diff_w > rebalance_threshold:
            sugg_shares = int((ref_capital * diff_w / px) // lot_size) * lot_size if px and px > 0 else 0
            action = "BUY" if sugg_shares > 0 else "HOLD"
        elif diff_w < -rebalance_threshold:
            sugg_shares = int((ref_capital * abs(diff_w) / px) // lot_size) * lot_size if px and px > 0 else 0
            action = "SELL" if sugg_shares > 0 else "HOLD"
        else:
            action = "HOLD"
            sugg_shares = int((ref_capital * tgt_w / px) // lot_size) * lot_size if px and px > 0 else 0

        instructions.append(
            TradeInstruction(
                signal_date=latest_signal.signal_date,
                run_mode=run_mode,  # type: ignore[arg-type]
                strategy_name=latest_signal.strategy_name,
                symbol=sym,
                action=action,  # type: ignore[arg-type]
                target_weight=round(tgt_w, 4),
                ref_price=round(px, 4) if px is not None else None,
                stop_loss_price=latest_signal.stop_losses.get(sym),
                suggested_shares=sugg_shares,
                reason=latest_signal.reasons.get(sym, "退出持仓"),
            )
        )

    if persist and instructions:
        storage.save_signals(instructions)

    return instructions, latest_prices


def run_daily_signal(
    storage: StorageBackend,
    universe: UniverseConfig,
    strat_cfg: dict[str, Any],
    run_mode: str = "intraday",
    lot_size: int = 100,
    rebalance_threshold: float = 0.03,
    persist: bool = True,
    user_id: str = "admin",
) -> tuple[list[TradeInstruction], dict[str, float]]:
    """Compute latest target weights diffed against a specific user's ledger positions."""
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

    ledger = LedgerService(storage, user_id=user_id)
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
            sugg_shares = int((total_eq * diff_w / px) // lot_size) * lot_size if px and px > 0 else 0
            action = "BUY" if sugg_shares > 0 else "HOLD"
        elif diff_w < -rebalance_threshold:
            sugg_shares = int((total_eq * abs(diff_w) / px) // lot_size) * lot_size if px and px > 0 else 0
            action = "SELL" if sugg_shares > 0 else "HOLD"
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
