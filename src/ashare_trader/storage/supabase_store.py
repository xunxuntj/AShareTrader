"""Supabase PostgreSQL storage backend via supabase-py REST client."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd

from ashare_trader.models import (
    AccountState,
    DataHealthRecord,
    Position,
    TradeInstruction,
    TradeRecord,
)
from ashare_trader.storage.base import StorageBackend


class SupabaseStorage(StorageBackend):
    """Cloud persistence backed by Supabase PostgreSQL (free tier compatible)."""

    BATCH_SIZE = 500

    def __init__(self, url: str, key: str, client: Any | None = None) -> None:
        self.url = url
        self.key = key
        if client is not None:
            self.client = client
        else:
            from supabase import create_client

            self.client = create_client(url, key)

    @property
    def backend_name(self) -> str:
        return "supabase"

    @staticmethod
    def _df_to_bar_rows(symbol: str, df: pd.DataFrame, source: str, is_final: bool) -> list[dict[str, Any]]:
        if df.empty:
            return []
        frame = df.copy()
        if "trade_date" in frame.columns:
            frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.strftime("%Y-%m-%d")
        else:
            frame["trade_date"] = pd.to_datetime(frame.index).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()
        rows: list[dict[str, Any]] = []
        for rec in frame.to_dict(orient="records"):
            if pd.isna(rec.get("close")):
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "trade_date": rec["trade_date"],
                    "open": float(rec.get("open", rec["close"])),
                    "high": float(rec.get("high", rec["close"])),
                    "low": float(rec.get("low", rec["close"])),
                    "close": float(rec["close"]),
                    "volume": float(rec.get("volume", 0.0) or 0.0),
                    "amount": float(rec.get("amount", 0.0) or 0.0),
                    "source": source,
                    "is_final": bool(is_final),
                    "updated_at": now_iso,
                }
            )
        return rows

    def upsert_bars(self, symbol: str, df: pd.DataFrame, source: str, is_final: bool = True) -> int:
        rows = self._df_to_bar_rows(symbol, df, source=source, is_final=is_final)
        if not rows:
            return 0
        for i in range(0, len(rows), self.BATCH_SIZE):
            chunk = rows[i : i + self.BATCH_SIZE]
            self.client.table("market_bars").upsert(chunk, on_conflict="symbol,trade_date").execute()
        return len(rows)

    def replace_symbol_bars(self, symbol: str, df: pd.DataFrame, source: str, is_final: bool = True) -> int:
        rows = self._df_to_bar_rows(symbol, df, source=source, is_final=is_final)
        if not rows:
            return 0
        self.client.table("market_bars").delete().eq("symbol", symbol).execute()
        for i in range(0, len(rows), self.BATCH_SIZE):
            chunk = rows[i : i + self.BATCH_SIZE]
            self.client.table("market_bars").upsert(chunk, on_conflict="symbol,trade_date").execute()
        return len(rows)

    def load_bars(
        self,
        symbols: list[str],
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> dict[str, pd.DataFrame]:
        result: dict[str, pd.DataFrame] = {}
        start_str = pd.Timestamp(start_date).strftime("%Y-%m-%d") if start_date is not None else None
        end_str = pd.Timestamp(end_date).strftime("%Y-%m-%d") if end_date is not None else None
        for symbol in symbols:
            all_rows: list[dict[str, Any]] = []
            offset = 0
            page_size = 1000
            while True:
                query = (
                    self.client.table("market_bars")
                    .select("trade_date,open,high,low,close,volume,amount,source,is_final")
                    .eq("symbol", symbol)
                    .order("trade_date", desc=False)
                    .range(offset, offset + page_size - 1)
                )
                if start_str:
                    query = query.gte("trade_date", start_str)
                if end_str:
                    query = query.lte("trade_date", end_str)
                resp = query.execute()
                batch = resp.data or []
                all_rows.extend(batch)
                if len(batch) < page_size:
                    break
                offset += page_size
            if all_rows:
                df = pd.DataFrame(all_rows)
                df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
                df = df.set_index("trade_date").sort_index()
                result[symbol] = df
        return result

    def get_latest_final_date(self, symbol: str) -> date | None:
        resp = (
            self.client.table("market_bars")
            .select("trade_date")
            .eq("symbol", symbol)
            .eq("is_final", True)
            .order("trade_date", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        return pd.Timestamp(rows[0]["trade_date"]).date()

    def log_data_health(self, records: list[DataHealthRecord]) -> None:
        if not records:
            return
        payload = [
            {
                "run_at": r.run_at.isoformat(),
                "symbol": r.symbol,
                "last_trade_date": r.last_trade_date.isoformat() if r.last_trade_date else None,
                "source_used": r.source_used,
                "status": r.status,
                "latency_ms": int(r.latency_ms),
                "error_msg": r.error_msg,
            }
            for r in records
        ]
        self.client.table("data_health_logs").insert(payload).execute()

    def load_latest_data_health(self, limit: int = 100) -> pd.DataFrame:
        resp = (
            self.client.table("data_health_logs")
            .select("*")
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return pd.DataFrame(resp.data or [])

    def save_signals(self, instructions: list[TradeInstruction]) -> None:
        if not instructions:
            return
        payload = [
            {
                "signal_date": ins.signal_date.isoformat(),
                "run_mode": ins.run_mode,
                "strategy_name": ins.strategy_name,
                "symbol": ins.symbol,
                "action": ins.action,
                "target_weight": float(ins.target_weight),
                "ref_price": float(ins.ref_price) if ins.ref_price is not None else None,
                "stop_loss_price": float(ins.stop_loss_price) if ins.stop_loss_price is not None else None,
                "reason": ins.reason,
            }
            for ins in instructions
        ]
        self.client.table("daily_signals").insert(payload).execute()

    def load_latest_signals(self, strategy_name: str | None = None, limit: int = 50) -> pd.DataFrame:
        query = self.client.table("daily_signals").select("*").order("id", desc=True).limit(limit)
        if strategy_name:
            query = query.eq("strategy_name", strategy_name)
        resp = query.execute()
        return pd.DataFrame(resp.data or [])

    def load_account(self, account_id: str = "default") -> AccountState:
        resp = (
            self.client.table("ledger_account")
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return AccountState(account_id=account_id)
        row = rows[0]
        return AccountState(
            account_id=row["account_id"],
            initial_capital=float(row["initial_capital"]),
            cash=float(row["cash"]),
            updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
        )

    def save_account(self, account: AccountState) -> None:
        self.client.table("ledger_account").upsert(
            {
                "account_id": account.account_id,
                "initial_capital": float(account.initial_capital),
                "cash": float(account.cash),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="account_id",
        ).execute()

    def load_positions(self) -> dict[str, Position]:
        resp = self.client.table("ledger_positions").select("*").gt("shares", 0).execute()
        positions: dict[str, Position] = {}
        for row in resp.data or []:
            positions[row["symbol"]] = Position(
                symbol=row["symbol"],
                shares=int(row["shares"]),
                avg_cost=float(row["avg_cost"]),
                highest_price=float(row["highest_price"]),
                stop_loss_price=float(row["stop_loss_price"]) if row.get("stop_loss_price") is not None else None,
                updated_at=datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00")),
            )
        return positions

    def upsert_position(self, position: Position) -> None:
        if position.shares <= 0:
            self.client.table("ledger_positions").delete().eq("symbol", position.symbol).execute()
            return
        self.client.table("ledger_positions").upsert(
            {
                "symbol": position.symbol,
                "shares": int(position.shares),
                "avg_cost": float(position.avg_cost),
                "highest_price": float(position.highest_price),
                "stop_loss_price": float(position.stop_loss_price) if position.stop_loss_price is not None else None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            on_conflict="symbol",
        ).execute()

    def record_trade(self, trade: TradeRecord) -> None:
        self.client.table("ledger_trades").insert(
            {
                "trade_date": trade.trade_date.isoformat(),
                "symbol": trade.symbol,
                "side": trade.side,
                "shares": int(trade.shares),
                "price": float(trade.price),
                "fee": float(trade.fee),
                "note": trade.note,
            }
        ).execute()

    def load_trades(self, limit: int = 200) -> pd.DataFrame:
        resp = (
            self.client.table("ledger_trades")
            .select("*")
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return pd.DataFrame(resp.data or [])

    def save_backtest_run(
        self,
        run_date: date,
        strategy_name: str,
        params: dict[str, Any],
        metrics: dict[str, Any],
        equity_curve: list[dict[str, Any]],
    ) -> None:
        self.client.table("backtest_runs").insert(
            {
                "run_date": run_date.isoformat(),
                "strategy_name": strategy_name,
                "params_json": params,
                "metrics_json": metrics,
                "equity_curve_json": equity_curve,
            }
        ).execute()

    def load_latest_backtest_runs(self, limit: int = 20) -> pd.DataFrame:
        resp = (
            self.client.table("backtest_runs")
            .select("*")
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return pd.DataFrame(resp.data or [])
