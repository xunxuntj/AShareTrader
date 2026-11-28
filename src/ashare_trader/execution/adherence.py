"""Signal execution adherence analytics and multi-user admin team summary."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd

from ashare_trader.data.calendar import cn_today
from ashare_trader.execution.ledger import compute_live_equity_curve
from ashare_trader.models import AccountState, Position, UserAccount


def compute_signal_adherence(
    signals_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    as_of_date: date | None = None,
    match_window_days: int = 3,
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Match shared actionable signals (BUY/SELL) against user trades by user-entered trade_date.

    Matching strictly uses trade_date (NOT created_at) so late-recorded trades still count accurately.
    """
    today = as_of_date or cn_today()
    empty_summary = {
        "total_actionable": 0,
        "executed_count": 0,
        "missed_count": 0,
        "pending_count": 0,
        "execution_rate": None,
        "avg_slippage_bps": None,
    }
    if signals_df is None or signals_df.empty:
        return empty_summary, pd.DataFrame()

    sdf = signals_df.copy()
    sdf = sdf[sdf["action"].isin(["BUY", "SELL"])].copy()
    if sdf.empty:
        return empty_summary, pd.DataFrame()

    sdf["signal_date_dt"] = pd.to_datetime(sdf["signal_date"]).dt.date
    # Deduplicate same-day intraday/eod batches: prefer eod if both exist, else latest id
    if "id" in sdf.columns:
        sdf = sdf.sort_values(by=["signal_date_dt", "id"], ascending=[False, False])
    sdf = sdf.drop_duplicates(subset=["signal_date_dt", "symbol", "action"], keep="first")
    sdf = sdf.sort_values(by="signal_date_dt", ascending=False)

    # Prepare user trades strictly indexed by trade_date
    tdf = pd.DataFrame()
    if trades_df is not None and not trades_df.empty:
        tdf = trades_df[trades_df["side"].isin(["BUY", "SELL"])].copy()
        if not tdf.empty:
            tdf["trade_date_dt"] = pd.to_datetime(tdf["trade_date"]).dt.date

    rows: list[dict[str, Any]] = []
    executed_cnt = 0
    missed_cnt = 0
    pending_cnt = 0
    slippages: list[float] = []

    for _, sig in sdf.iterrows():
        sig_dt: date = sig["signal_date_dt"]
        sym = str(sig["symbol"])
        act = str(sig["action"])
        ref_px = float(sig["ref_price"]) if pd.notna(sig.get("ref_price")) else None
        win_end = sig_dt + timedelta(days=match_window_days)

        matched_trade = None
        if not tdf.empty:
            cand = tdf[
                (tdf["symbol"] == sym)
                & (tdf["side"] == act)
                & (tdf["trade_date_dt"] >= sig_dt)
                & (tdf["trade_date_dt"] <= win_end)
            ]
            if not cand.empty:
                matched_trade = cand.sort_values(by="trade_date_dt", ascending=True).iloc[0]

        if matched_trade is not None:
            status = "EXECUTED"
            executed_cnt += 1
            actual_dt = matched_trade["trade_date_dt"]
            actual_px = float(matched_trade["price"])
            actual_sh = int(matched_trade["shares"])
            slip_bps: float | None = None
            if ref_px and ref_px > 0:
                # Positive bps = adverse cost (bought higher or sold lower than ref_price)
                raw_bps = (actual_px / ref_px - 1.0) * 10000.0
                slip_bps = round(raw_bps if act == "BUY" else -raw_bps, 1)
                slippages.append(slip_bps)
        elif today <= win_end:
            status = "PENDING"
            pending_cnt += 1
            actual_dt = None
            actual_px = None
            actual_sh = None
            slip_bps = None
        else:
            status = "MISSED"
            missed_cnt += 1
            actual_dt = None
            actual_px = None
            actual_sh = None
            slip_bps = None

        rows.append(
            {
                "signal_date": sig_dt.isoformat(),
                "symbol": sym,
                "action": act,
                "target_weight": float(sig.get("target_weight", 0.0)),
                "ref_price": round(ref_px, 4) if ref_px is not None else None,
                "trade_date": actual_dt.isoformat() if actual_dt else None,
                "actual_price": round(actual_px, 4) if actual_px is not None else None,
                "actual_shares": actual_sh,
                "slippage_bps": slip_bps,
                "status": status,
            }
        )

    settled = executed_cnt + missed_cnt
    exec_rate = round(executed_cnt / settled, 4) if settled > 0 else None
    avg_slip = round(sum(slippages) / len(slippages), 1) if slippages else None

    summary = {
        "total_actionable": len(rows),
        "executed_count": executed_cnt,
        "missed_count": missed_cnt,
        "pending_count": pending_cnt,
        "execution_rate": exec_rate,
        "avg_slippage_bps": avg_slip,
    }
    return summary, pd.DataFrame(rows)


def build_admin_team_summary(
    users: list[UserAccount],
    accounts: dict[str, AccountState],
    positions_by_user: dict[str, dict[str, Position]],
    all_trades_df: pd.DataFrame,
    signals_df: pd.DataFrame,
    close_df: pd.DataFrame,
    latest_prices: dict[str, float],
    as_of_date: date | None = None,
) -> pd.DataFrame:
    """Aggregate per-user capital, equity, live return, max drawdown, and signal adherence."""
    rows: list[dict[str, Any]] = []
    for u in users:
        uid = u.user_id
        acc = accounts.get(uid, AccountState(account_id=uid))
        pos_map = positions_by_user.get(uid, {})
        mkt_val = sum(p.shares * latest_prices.get(sym, p.avg_cost) for sym, p in pos_map.items())
        total_eq = acc.cash + mkt_val
        simple_ret = (total_eq / acc.initial_capital - 1.0) if acc.initial_capital > 0 else 0.0

        u_trades = (
            all_trades_df[all_trades_df["user_id"] == uid].copy()
            if (all_trades_df is not None and not all_trades_df.empty and "user_id" in all_trades_df.columns)
            else pd.DataFrame()
        )
        live_df = compute_live_equity_curve(u_trades, close_df, initial_capital=acc.initial_capital)
        nav_ret = float(live_df["nav"].iloc[-1] - 1.0) if not live_df.empty else simple_ret
        max_dd = float(abs(live_df["drawdown"].min())) if not live_df.empty else 0.0

        adh_sum, _ = compute_signal_adherence(signals_df, u_trades, as_of_date=as_of_date)

        rows.append(
            {
                "user_id": uid,
                "display_name": u.display_name,
                "role": u.role,
                "is_active": u.is_active,
                "initial_capital": round(acc.initial_capital, 2),
                "total_equity": round(total_eq, 2),
                "cash": round(acc.cash, 2),
                "position_count": len(pos_map),
                "trade_count": len(u_trades),
                "live_return": round(nav_ret, 4),
                "max_drawdown": round(max_dd, 4),
                "execution_rate": adh_sum["execution_rate"],
                "avg_slippage_bps": adh_sum["avg_slippage_bps"],
            }
        )
    return pd.DataFrame(rows)
