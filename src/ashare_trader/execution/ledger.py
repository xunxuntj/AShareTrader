"""Account cash, positions, trade logging, and dynamic trailing stop tracker."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ashare_trader.models import AccountState, Position, TradeRecord
from ashare_trader.storage.base import StorageBackend


def compute_live_equity_curve(
    trades_df: pd.DataFrame,
    close_df: pd.DataFrame,
    initial_capital: float = 100000.0,
) -> pd.DataFrame:
    """Reconstruct daily live account equity curve from confirmed ledger_trades and market closes.

    Returns a DataFrame indexed by trade_date with columns: equity, nav, drawdown.
    """
    if trades_df is None or trades_df.empty or close_df is None or close_df.empty:
        return pd.DataFrame(columns=["equity", "nav", "drawdown"])

    tdf = trades_df.copy()
    tdf["trade_date"] = pd.to_datetime(tdf["trade_date"]).dt.normalize()
    tdf = tdf.sort_values(by=["trade_date", "id"] if "id" in tdf.columns else ["trade_date"])

    first_dt = tdf["trade_date"].min()
    prices = close_df.sort_index().ffill()
    eval_dates = prices.index[prices.index >= first_dt]
    if eval_dates.empty:
        return pd.DataFrame(columns=["equity", "nav", "drawdown"])

    # Compute initial starting cash before the first logged DEPOSIT/WITHDRAW adjustment
    cash = float(initial_capital)
    # Subtract net deposits already folded into initial_capital so replay doesn't double-count
    dep_sum = float(tdf.loc[tdf["side"] == "DEPOSIT", "price"].sum())
    cash = max(0.0, cash - dep_sum)
    nav_base = max(1.0, cash if cash > 0 else float(initial_capital))

    holdings: dict[str, int] = {}
    trade_rows = list(tdf.itertuples(index=False))
    trade_idx = 0
    n_trades = len(trade_rows)

    records: list[dict[str, float]] = []
    out_dates: list[pd.Timestamp] = []
    cum_nav = 1.0
    prev_equity: float | None = None

    for dt in eval_dates:
        # Consume all trades/cashflows on or before trading day dt (handles weekend deposits cleanly)
        net_flow = 0.0
        while trade_idx < n_trades and trade_rows[trade_idx].trade_date <= dt:
            tr = trade_rows[trade_idx]
            trade_idx += 1
            side = str(tr.side)
            sym = str(tr.symbol)
            sh = int(tr.shares)
            px = float(tr.price)
            fee = float(getattr(tr, "fee", 0.0) or 0.0)
            if side == "DEPOSIT":
                cash += px
                net_flow += px
            elif side == "WITHDRAW":
                cash = max(0.0, cash - px)
                net_flow -= px
            elif side == "BUY":
                cash = max(0.0, cash - (sh * px + fee))
                holdings[sym] = holdings.get(sym, 0) + sh
            elif side == "SELL":
                cash += sh * px - fee
                rem = max(0, holdings.get(sym, 0) - sh)
                if rem > 0:
                    holdings[sym] = rem
                else:
                    holdings.pop(sym, None)

        row_px = prices.loc[dt]
        mkt_val = 0.0
        for sym, sh in holdings.items():
            if sym in row_px.index and pd.notna(row_px[sym]) and row_px[sym] > 0:
                mkt_val += sh * float(row_px[sym])
        equity = cash + mkt_val

        # Time-weighted NAV step (excludes external DEPOSIT/WITHDRAW distortion)
        if prev_equity is not None and (prev_equity + net_flow) > 0:
            daily_ret = (equity - net_flow) / prev_equity - 1.0 if prev_equity > 0 else 0.0
            cum_nav *= 1.0 + daily_ret
        elif nav_base > 0:
            cum_nav = equity / (nav_base + net_flow) if (nav_base + net_flow) > 0 else 1.0

        prev_equity = equity
        out_dates.append(dt)
        records.append({"equity": round(equity, 2), "nav": round(cum_nav, 6)})

    res = pd.DataFrame(records, index=pd.DatetimeIndex(out_dates, name="trade_date"))
    res["drawdown"] = res["nav"] / res["nav"].cummax() - 1.0
    return res


class LedgerService:
    """High-level ledger operations backed by StorageBackend (Supabase or local)."""

    def __init__(self, storage: StorageBackend, account_id: str = "default") -> None:
        self.storage = storage
        self.account_id = account_id

    def get_account(self) -> AccountState:
        return self.storage.load_account(self.account_id)

    def get_positions(self) -> dict[str, Position]:
        return self.storage.load_positions()

    def total_equity(self, latest_prices: dict[str, float]) -> float:
        account = self.get_account()
        positions = self.get_positions()
        mkt_val = 0.0
        for sym, pos in positions.items():
            px = latest_prices.get(sym, pos.avg_cost)
            mkt_val += pos.shares * px
        return account.cash + mkt_val

    def record_execution(
        self,
        trade_date: date,
        symbol: str,
        side: str,
        shares: int,
        price: float,
        fee: float = 0.0,
        stop_loss_price: float | None = None,
        note: str = "",
    ) -> None:
        """Record a confirmed BUY/SELL/DEPOSIT/WITHDRAW and update cash & position."""
        account = self.get_account()
        positions = self.get_positions()

        if side == "DEPOSIT":
            account.cash += price
            account.initial_capital += price
            self.storage.save_account(account)
        elif side == "WITHDRAW":
            account.cash = max(0.0, account.cash - price)
            self.storage.save_account(account)
        elif side == "BUY":
            cost = shares * price + fee
            account.cash = max(0.0, account.cash - cost)
            self.storage.save_account(account)
            prev = positions.get(symbol)
            if prev and prev.shares > 0:
                new_shares = prev.shares + shares
                new_avg = (prev.shares * prev.avg_cost + shares * price) / new_shares
                new_high = max(prev.highest_price, price)
                new_stop = stop_loss_price if stop_loss_price is not None else prev.stop_loss_price
            else:
                new_shares = shares
                new_avg = price
                new_high = price
                new_stop = stop_loss_price
            self.storage.upsert_position(
                Position(
                    symbol=symbol,
                    shares=new_shares,
                    avg_cost=round(new_avg, 4),
                    highest_price=round(new_high, 4),
                    stop_loss_price=round(new_stop, 4) if new_stop is not None else None,
                )
            )
        elif side == "SELL":
            proceeds = shares * price - fee
            account.cash += proceeds
            self.storage.save_account(account)
            prev = positions.get(symbol)
            if prev:
                rem = max(0, prev.shares - shares)
                self.storage.upsert_position(
                    Position(
                        symbol=symbol,
                        shares=rem,
                        avg_cost=prev.avg_cost,
                        highest_price=prev.highest_price,
                        stop_loss_price=prev.stop_loss_price,
                    )
                )
        else:
            raise ValueError(f"Unsupported side: {side}")

        self.storage.record_trade(
            TradeRecord(
                trade_date=trade_date,
                symbol=symbol,
                side=side,  # type: ignore[arg-type]
                shares=shares,
                price=price,
                fee=fee,
                note=note,
            )
        )

    def refresh_trailing_stops(
        self,
        latest_prices: dict[str, float],
        strategy_stops: dict[str, float],
    ) -> dict[str, Position]:
        """Ratchet up highest_price and stop_loss_price on existing holdings."""
        positions = self.get_positions()
        for sym, pos in positions.items():
            px = latest_prices.get(sym)
            if px is None or px <= 0:
                continue
            changed = False
            if px > pos.highest_price:
                pos.highest_price = round(px, 4)
                changed = True
            cand_stop = strategy_stops.get(sym)
            if cand_stop is not None and (pos.stop_loss_price is None or cand_stop > pos.stop_loss_price):
                pos.stop_loss_price = round(cand_stop, 4)
                changed = True
            if changed:
                self.storage.upsert_position(pos)
        return self.get_positions()
