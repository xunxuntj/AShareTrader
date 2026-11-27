"""Abstract storage interface unifying Supabase PostgreSQL and local SQLite/Parquet."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import pandas as pd

from ashare_trader.models import (
    AccountState,
    DataHealthRecord,
    Position,
    TradeInstruction,
    TradeRecord,
)


class StorageBackend(ABC):
    """Unified persistence contract for market data, signals, ledger, and health logs."""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """Human-readable name of active storage backend."""

    # --- Market Bars ---
    @abstractmethod
    def upsert_bars(self, symbol: str, df: pd.DataFrame, source: str, is_final: bool = True) -> int:
        """Upsert OHLCV bars for a symbol. Returns count of rows written."""

    @abstractmethod
    def replace_symbol_bars(self, symbol: str, df: pd.DataFrame, source: str, is_final: bool = True) -> int:
        """Full overwrite of bars for a symbol (used after dividend/adjustment drift detection)."""

    @abstractmethod
    def load_bars(
        self,
        symbols: list[str],
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Load OHLCV DataFrames keyed by symbol, indexed by DatetimeIndex (trade_date)."""

    @abstractmethod
    def get_latest_final_date(self, symbol: str) -> date | None:
        """Return the latest finalized trade_date stored for symbol."""

    # --- Data Health Logs ---
    @abstractmethod
    def log_data_health(self, records: list[DataHealthRecord]) -> None:
        """Persist a batch of data fetch health records."""

    @abstractmethod
    def load_latest_data_health(self, limit: int = 100) -> pd.DataFrame:
        """Load recent data health logs ordered by run_at descending."""

    # --- Daily Signals ---
    @abstractmethod
    def save_signals(self, instructions: list[TradeInstruction]) -> None:
        """Persist daily trade instructions."""

    @abstractmethod
    def load_latest_signals(self, strategy_name: str | None = None, limit: int = 50) -> pd.DataFrame:
        """Load latest persisted signals."""

    # --- Ledger (Account, Positions, Trades) ---
    @abstractmethod
    def load_account(self, account_id: str = "default") -> AccountState:
        """Load cash & initial capital state."""

    @abstractmethod
    def save_account(self, account: AccountState) -> None:
        """Upsert account cash & initial capital."""

    @abstractmethod
    def load_positions(self) -> dict[str, Position]:
        """Load all non-zero current holdings."""

    @abstractmethod
    def upsert_position(self, position: Position) -> None:
        """Upsert or remove (if shares <= 0) a position."""

    @abstractmethod
    def record_trade(self, trade: TradeRecord) -> None:
        """Append a confirmed trade or cashflow record."""

    @abstractmethod
    def load_trades(self, limit: int = 200) -> pd.DataFrame:
        """Load recent trade history."""

    # --- Backtest Snapshots ---
    @abstractmethod
    def save_backtest_run(
        self,
        run_date: date,
        strategy_name: str,
        params: dict[str, Any],
        metrics: dict[str, Any],
        equity_curve: list[dict[str, Any]],
    ) -> None:
        """Persist latest backtest summary and equity curve."""

    @abstractmethod
    def load_latest_backtest_runs(self, limit: int = 20) -> pd.DataFrame:
        """Load recent backtest snapshots."""
