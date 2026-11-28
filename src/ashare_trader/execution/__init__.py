"""Execution layer: ledger service, adherence analytics, abstract broker, and signal runners."""

from ashare_trader.execution.adherence import build_admin_team_summary, compute_signal_adherence
from ashare_trader.execution.broker import Broker, ManualPaperBroker
from ashare_trader.execution.ledger import LedgerService, compute_live_equity_curve
from ashare_trader.execution.signal_runner import (
    build_default_strategies,
    compute_suggested_shares,
    run_daily_signal,
    run_shared_model_signal,
)

__all__ = [
    "Broker",
    "ManualPaperBroker",
    "LedgerService",
    "compute_live_equity_curve",
    "compute_signal_adherence",
    "build_admin_team_summary",
    "build_default_strategies",
    "compute_suggested_shares",
    "run_daily_signal",
    "run_shared_model_signal",
]
