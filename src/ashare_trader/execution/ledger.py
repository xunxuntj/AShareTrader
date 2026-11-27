"""Account cash, positions, trade logging, and dynamic trailing stop tracker."""

from __future__ import annotations

from datetime import date

from ashare_trader.models import AccountState, Position, TradeRecord
from ashare_trader.storage.base import StorageBackend


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
