"""Local SQLite + Parquet storage backend for offline development and unit tests."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
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

BAR_COLUMNS = ["open", "high", "low", "close", "volume", "amount", "source", "is_final"]


class LocalStorage(StorageBackend):
    """Persists market bars in Parquet files and metadata/ledger/logs in SQLite."""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self.data_dir = Path(data_dir)
        self.market_dir = self.data_dir / "market"
        self.market_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "ashare_trader.sqlite"
        self._init_db()

    @property
    def backend_name(self) -> str:
        return f"local({self.data_dir})"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS data_health_logs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at          TEXT NOT NULL,
                    symbol          TEXT NOT NULL,
                    last_trade_date TEXT,
                    source_used     TEXT NOT NULL,
                    status          TEXT NOT NULL,
                    latency_ms      INTEGER NOT NULL DEFAULT 0,
                    error_msg       TEXT
                );
                CREATE TABLE IF NOT EXISTS daily_signals (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_date     TEXT NOT NULL,
                    run_mode        TEXT NOT NULL,
                    strategy_name   TEXT NOT NULL,
                    symbol          TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    target_weight   REAL NOT NULL DEFAULT 0,
                    ref_price       REAL,
                    stop_loss_price REAL,
                    reason          TEXT,
                    created_at      TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_account (
                    account_id      TEXT PRIMARY KEY,
                    initial_capital REAL NOT NULL DEFAULT 100000,
                    cash            REAL NOT NULL DEFAULT 100000,
                    updated_at      TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_positions (
                    symbol          TEXT PRIMARY KEY,
                    shares          INTEGER NOT NULL DEFAULT 0,
                    avg_cost        REAL NOT NULL DEFAULT 0,
                    highest_price   REAL NOT NULL DEFAULT 0,
                    stop_loss_price REAL,
                    updated_at      TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ledger_trades (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date      TEXT NOT NULL,
                    symbol          TEXT NOT NULL,
                    side            TEXT NOT NULL,
                    shares          INTEGER NOT NULL DEFAULT 0,
                    price           REAL NOT NULL DEFAULT 0,
                    fee             REAL NOT NULL DEFAULT 0,
                    note            TEXT,
                    created_at      TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_date          TEXT NOT NULL,
                    strategy_name     TEXT NOT NULL,
                    params_json       TEXT NOT NULL,
                    metrics_json      TEXT NOT NULL,
                    equity_curve_json TEXT NOT NULL,
                    created_at        TEXT NOT NULL
                );
                """
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT OR IGNORE INTO ledger_account (account_id, initial_capital, cash, updated_at)
                VALUES ('default', 100000.0, 100000.0, ?)
                """,
                (now_iso,),
            )

    def _bar_path(self, symbol: str) -> Path:
        return self.market_dir / f"{symbol}.parquet"

    @staticmethod
    def _normalize_bars(df: pd.DataFrame, source: str, is_final: bool) -> pd.DataFrame:
        if df.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
        out = df.copy()
        if "trade_date" in out.columns:
            out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.normalize()
            out = out.set_index("trade_date")
        else:
            out.index = pd.to_datetime(out.index).normalize()
            out.index.name = "trade_date"
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col not in out.columns:
                out[col] = 0.0
            out[col] = pd.to_numeric(out[col], errors="coerce")
        out["source"] = source
        out["is_final"] = bool(is_final)
        out = out[BAR_COLUMNS].dropna(subset=["close"])
        out = out[~out.index.duplicated(keep="last")].sort_index()
        return out

    def upsert_bars(self, symbol: str, df: pd.DataFrame, source: str, is_final: bool = True) -> int:
        incoming = self._normalize_bars(df, source=source, is_final=is_final)
        if incoming.empty:
            return 0
        path = self._bar_path(symbol)
        if path.exists():
            existing = pd.read_parquet(path)
            existing.index = pd.to_datetime(existing.index).normalize()
            existing.index.name = "trade_date"
            merged = pd.concat([existing, incoming])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        else:
            merged = incoming
        merged.to_parquet(path)
        return len(incoming)

    def replace_symbol_bars(self, symbol: str, df: pd.DataFrame, source: str, is_final: bool = True) -> int:
        incoming = self._normalize_bars(df, source=source, is_final=is_final)
        if incoming.empty:
            return 0
        incoming.to_parquet(self._bar_path(symbol))
        return len(incoming)

    def load_bars(
        self,
        symbols: list[str],
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> dict[str, pd.DataFrame]:
        start_ts = pd.Timestamp(start_date) if start_date is not None else None
        end_ts = pd.Timestamp(end_date) if end_date is not None else None
        result: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            path = self._bar_path(symbol)
            if not path.exists():
                continue
            df = pd.read_parquet(path)
            df.index = pd.to_datetime(df.index).normalize()
            df.index.name = "trade_date"
            df = df.sort_index()
            if start_ts is not None:
                df = df.loc[df.index >= start_ts]
            if end_ts is not None:
                df = df.loc[df.index <= end_ts]
            if not df.empty:
                result[symbol] = df
        return result

    def get_latest_final_date(self, symbol: str) -> date | None:
        path = self._bar_path(symbol)
        if not path.exists():
            return None
        df = pd.read_parquet(path)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index).normalize()
        if "is_final" in df.columns:
            final_df = df[df["is_final"].astype(bool)]
        else:
            final_df = df
        if final_df.empty:
            return None
        return final_df.index.max().date()

    def log_data_health(self, records: list[DataHealthRecord]) -> None:
        if not records:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO data_health_logs
                    (run_at, symbol, last_trade_date, source_used, status, latency_ms, error_msg)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.run_at.isoformat(),
                        r.symbol,
                        r.last_trade_date.isoformat() if r.last_trade_date else None,
                        r.source_used,
                        r.status,
                        int(r.latency_ms),
                        r.error_msg,
                    )
                    for r in records
                ],
            )

    def load_latest_data_health(self, limit: int = 100) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM data_health_logs ORDER BY id DESC LIMIT ?",
                conn,
                params=(limit,),
            )

    def save_signals(self, instructions: list[TradeInstruction]) -> None:
        if not instructions:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        # Idempotent write: replace existing rows for the same (signal_date, run_mode, strategy_name)
        keys = {(ins.signal_date.isoformat(), ins.run_mode, ins.strategy_name) for ins in instructions}
        with self._connect() as conn:
            for sig_date, run_mode, strat_name in keys:
                conn.execute(
                    "DELETE FROM daily_signals WHERE signal_date = ? AND run_mode = ? AND strategy_name = ?",
                    (sig_date, run_mode, strat_name),
                )
            conn.executemany(
                """
                INSERT INTO daily_signals
                    (signal_date, run_mode, strategy_name, symbol, action, target_weight,
                     ref_price, stop_loss_price, reason, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        ins.signal_date.isoformat(),
                        ins.run_mode,
                        ins.strategy_name,
                        ins.symbol,
                        ins.action,
                        float(ins.target_weight),
                        float(ins.ref_price) if ins.ref_price is not None else None,
                        float(ins.stop_loss_price) if ins.stop_loss_price is not None else None,
                        ins.reason,
                        now_iso,
                    )
                    for ins in instructions
                ],
            )

    def load_latest_signals(self, strategy_name: str | None = None, limit: int = 50) -> pd.DataFrame:
        with self._connect() as conn:
            if strategy_name:
                return pd.read_sql_query(
                    "SELECT * FROM daily_signals WHERE strategy_name = ? ORDER BY id DESC LIMIT ?",
                    conn,
                    params=(strategy_name, limit),
                )
            return pd.read_sql_query(
                "SELECT * FROM daily_signals ORDER BY id DESC LIMIT ?",
                conn,
                params=(limit,),
            )

    def load_account(self, account_id: str = "default") -> AccountState:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM ledger_account WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is None:
                return AccountState(account_id=account_id)
            return AccountState(
                account_id=row["account_id"],
                initial_capital=float(row["initial_capital"]),
                cash=float(row["cash"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )

    def save_account(self, account: AccountState) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ledger_account (account_id, initial_capital, cash, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    initial_capital = excluded.initial_capital,
                    cash = excluded.cash,
                    updated_at = excluded.updated_at
                """,
                (account.account_id, float(account.initial_capital), float(account.cash), now_iso),
            )

    def load_positions(self) -> dict[str, Position]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM ledger_positions WHERE shares > 0").fetchall()
        positions: dict[str, Position] = {}
        for row in rows:
            positions[row["symbol"]] = Position(
                symbol=row["symbol"],
                shares=int(row["shares"]),
                avg_cost=float(row["avg_cost"]),
                highest_price=float(row["highest_price"]),
                stop_loss_price=float(row["stop_loss_price"]) if row["stop_loss_price"] is not None else None,
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
        return positions

    def upsert_position(self, position: Position) -> None:
        with self._connect() as conn:
            if position.shares <= 0:
                conn.execute("DELETE FROM ledger_positions WHERE symbol = ?", (position.symbol,))
                return
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                INSERT INTO ledger_positions (symbol, shares, avg_cost, highest_price, stop_loss_price, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    shares = excluded.shares,
                    avg_cost = excluded.avg_cost,
                    highest_price = excluded.highest_price,
                    stop_loss_price = excluded.stop_loss_price,
                    updated_at = excluded.updated_at
                """,
                (
                    position.symbol,
                    int(position.shares),
                    float(position.avg_cost),
                    float(position.highest_price),
                    float(position.stop_loss_price) if position.stop_loss_price is not None else None,
                    now_iso,
                ),
            )

    def record_trade(self, trade: TradeRecord) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ledger_trades (trade_date, symbol, side, shares, price, fee, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade.trade_date.isoformat(),
                    trade.symbol,
                    trade.side,
                    int(trade.shares),
                    float(trade.price),
                    float(trade.fee),
                    trade.note,
                    now_iso,
                ),
            )

    def load_trades(self, limit: int = 200) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM ledger_trades ORDER BY id DESC LIMIT ?",
                conn,
                params=(limit,),
            )

    def save_backtest_run(
        self,
        run_date: date,
        strategy_name: str,
        params: dict[str, Any],
        metrics: dict[str, Any],
        equity_curve: list[dict[str, Any]],
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO backtest_runs
                    (run_date, strategy_name, params_json, metrics_json, equity_curve_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_date.isoformat(),
                    strategy_name,
                    json.dumps(params, ensure_ascii=False, default=str),
                    json.dumps(metrics, ensure_ascii=False, default=str),
                    json.dumps(equity_curve, ensure_ascii=False, default=str),
                    now_iso,
                ),
            )

    def load_latest_backtest_runs(self, limit: int = 20) -> pd.DataFrame:
        with self._connect() as conn:
            return pd.read_sql_query(
                "SELECT * FROM backtest_runs ORDER BY id DESC LIMIT ?",
                conn,
                params=(limit,),
            )
