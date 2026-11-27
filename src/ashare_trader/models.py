"""Core domain models shared across data, strategy, backtest, and execution layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

import pandas as pd


@dataclass(frozen=True)
class Asset:
    """Instrument metadata (asset-class agnostic for future stock extension)."""

    symbol: str
    name: str
    exchange: Literal["sh", "sz"]
    category: str
    asset_type: Literal["etf", "stock", "bond"] = "etf"

    @property
    def baostock_code(self) -> str:
        return f"{self.exchange}.{self.symbol}"

    @property
    def sina_code(self) -> str:
        return f"{self.exchange}{self.symbol}"


@dataclass
class UniverseConfig:
    """Universe definition including safe-haven parking asset and benchmark."""

    assets: list[Asset]
    safe_haven: Asset
    benchmark: Asset

    @property
    def all_assets(self) -> list[Asset]:
        seen: dict[str, Asset] = {}
        for asset in [*self.assets, self.safe_haven, self.benchmark]:
            seen[asset.symbol] = asset
        return list(seen.values())

    @property
    def asset_map(self) -> dict[str, Asset]:
        return {a.symbol: a for a in self.all_assets}

    @property
    def risky_symbols(self) -> list[str]:
        return [a.symbol for a in self.assets if a.symbol != self.safe_haven.symbol]


@dataclass
class DataHealthRecord:
    """Data fetch health telemetry per symbol."""

    run_at: datetime
    symbol: str
    last_trade_date: date | None
    source_used: str
    status: Literal["ok", "degraded", "failed"]
    latency_ms: int
    error_msg: str | None = None


@dataclass
class StrategySignal:
    """Output of a strategy for a single date."""

    signal_date: date
    strategy_name: str
    target_weights: dict[str, float]
    stop_losses: dict[str, float] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)


@dataclass
class TradeInstruction:
    """Actionable signal item persisted to daily_signals and sent to Feishu."""

    signal_date: date
    run_mode: Literal["intraday", "eod"]
    strategy_name: str
    symbol: str
    action: Literal["BUY", "SELL", "HOLD"]
    target_weight: float
    ref_price: float | None = None
    stop_loss_price: float | None = None
    suggested_shares: int = 0
    reason: str = ""


@dataclass
class Position:
    """Live or simulated position tracked in ledger_positions."""

    symbol: str
    shares: int
    avg_cost: float
    highest_price: float
    stop_loss_price: float | None = None
    updated_at: datetime | None = None


@dataclass
class AccountState:
    """Cash and initial capital state in ledger_account."""

    account_id: str = "default"
    initial_capital: float = 100000.0
    cash: float = 100000.0
    updated_at: datetime | None = None


@dataclass
class TradeRecord:
    """User-confirmed trade or cashflow record in ledger_trades."""

    trade_date: date
    symbol: str
    side: Literal["BUY", "SELL", "DEPOSIT", "WITHDRAW"]
    shares: int
    price: float
    fee: float = 0.0
    note: str = ""


@dataclass
class BacktestConfig:
    """Execution friction and constraints for realistic A-Share backtesting."""

    initial_capital: float = 100000.0
    commission_rate: float = 0.0001
    min_commission: float = 0.0
    stamp_duty_rate: float = 0.0
    slippage_rate: float = 0.0005
    lot_size: int = 100
    execution_timing: Literal["close", "next_open"] = "close"
    limit_move_threshold: float = 0.098
    rebalance_threshold: float = 0.03


@dataclass
class BacktestResult:
    """Complete output of a single strategy backtest."""

    strategy_name: str
    equity_curve: pd.Series
    daily_returns: pd.Series
    weights_history: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict[str, Any]
    yearly_metrics: pd.DataFrame
    benchmark_curve: pd.Series | None = None
