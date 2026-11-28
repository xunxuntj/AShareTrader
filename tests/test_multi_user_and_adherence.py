"""Tests for multi-user auth, per-user ledger isolation, rolling shared signals, and adherence analytics."""

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ashare_trader.auth import AuthService, create_session_token, verify_session_token
from ashare_trader.execution.adherence import build_admin_team_summary, compute_signal_adherence
from ashare_trader.execution.ledger import LedgerService
from ashare_trader.execution.signal_runner import run_shared_model_signal
from ashare_trader.models import Asset, UniverseConfig
from ashare_trader.storage.local_store import LocalStorage


def test_auth_service_lifecycle_and_disable(tmp_path):
    store = LocalStorage(tmp_path)
    auth = AuthService(store)

    admin = auth.ensure_default_admin(default_password="secret123")
    assert admin.user_id == "admin" and admin.role == "admin"
    assert auth.authenticate("admin", "secret123") is not None
    assert auth.authenticate("admin", "wrong") is None

    alice = auth.create_user("Alice", "爱丽丝", "pass99", role="user", initial_capital=50000.0)
    assert alice.user_id == "alice"
    assert store.load_account("alice").initial_capital == 50000.0
    assert auth.authenticate("alice", "pass99") is not None

    auth.reset_password("alice", "newpass")
    assert auth.authenticate("alice", "pass99") is None
    assert auth.authenticate("alice", "newpass") is not None

    auth.set_active("alice", False)
    assert auth.authenticate("alice", "newpass") is None


def test_remember_me_session_token_security(tmp_path):
    store = LocalStorage(tmp_path)
    auth = AuthService(store)
    alice = auth.create_user("alice", "Alice", "pass1", initial_capital=50000.0)

    token = create_session_token(alice, ttl_days=30)
    restored = verify_session_token(token, store)
    assert restored is not None and restored.user_id == "alice"

    # Tampered token must fail
    assert verify_session_token(token + "x", store) is None

    # Password reset must immediately invalidate prior remember-me token
    auth.reset_password("alice", "pass2")
    assert verify_session_token(token, store) is None

    # Disabled user must also be rejected even with a fresh token
    alice_fresh = store.get_user("alice")
    assert alice_fresh is not None
    token2 = create_session_token(alice_fresh, ttl_days=30)
    auth.set_active("alice", False)
    assert verify_session_token(token2, store) is None


def test_per_user_ledger_isolation_and_initial_capital_guard(tmp_path):
    store = LocalStorage(tmp_path)
    auth = AuthService(store)
    auth.ensure_default_admin(default_password="pw")
    auth.create_user("alice", "Alice", "1234", initial_capital=50000.0)
    auth.create_user("bob", "Bob", "1234", initial_capital=200000.0)

    l_alice = LedgerService(store, user_id="alice")
    l_bob = LedgerService(store, user_id="bob")

    # Alice updates initial capital before first trade
    l_alice.set_initial_capital(80000.0)
    assert l_alice.get_account().cash == 80000.0
    assert l_bob.get_account().cash == 200000.0  # Untouched

    # Alice buys 510300 -> must not leak into Bob
    l_alice.record_execution(date(2025, 1, 6), "510300", "BUY", 10000, 4.0, fee=4.0, stop_loss_price=3.7)
    assert "510300" in l_alice.get_positions()
    assert "510300" not in l_bob.get_positions()

    # Once trades exist, direct set_initial_capital must raise ValueError
    with pytest.raises(ValueError):
        l_alice.set_initial_capital(90000.0)


def test_signal_adherence_matches_by_trade_date_and_computes_slippage():
    signals_df = pd.DataFrame(
        [
            {"id": 1, "signal_date": "2025-02-03", "run_mode": "eod", "symbol": "510300", "action": "BUY", "target_weight": 0.35, "ref_price": 4.00},
            {"id": 2, "signal_date": "2025-02-10", "run_mode": "eod", "symbol": "518880", "action": "BUY", "target_weight": 0.35, "ref_price": 5.00},
            {"id": 3, "signal_date": "2025-02-20", "run_mode": "eod", "symbol": "513100", "action": "BUY", "target_weight": 0.35, "ref_price": 1.50},
        ]
    )
    # User recorded trade_date=2025-02-04 (T+1) for 510300 at 4.02 (+50 bps adverse buy slippage),
    # missed 518880, and 513100 is still pending as of 2025-02-21
    trades_df = pd.DataFrame(
        [
            {"trade_date": "2025-02-04", "symbol": "510300", "side": "BUY", "shares": 8000, "price": 4.02, "created_at": "2025-02-18T10:00:00Z"},
        ]
    )
    summary, detail = compute_signal_adherence(signals_df, trades_df, as_of_date=date(2025, 2, 21), match_window_days=3)
    assert summary["executed_count"] == 1
    assert summary["missed_count"] == 1
    assert summary["pending_count"] == 1
    assert summary["execution_rate"] == 0.5  # 1 executed / (1 executed + 1 missed)
    assert abs(summary["avg_slippage_bps"] - 50.0) < 1e-6

    status_map = dict(zip(detail["symbol"], detail["status"], strict=False))
    assert status_map == {"510300": "EXECUTED", "518880": "MISSED", "513100": "PENDING"}


def test_shared_model_signal_and_admin_team_summary(tmp_path):
    store = LocalStorage(tmp_path)
    auth = AuthService(store)
    auth.ensure_default_admin(default_password="pw")
    auth.create_user("alice", "Alice", "1234", initial_capital=100000.0)

    # Seed 80 days of strong uptrend data for 510300 (daily gain > intraday high noise -> clear Donchian breakout)
    idx = pd.date_range("2025-01-02", periods=80, freq="B")
    prices = np.linspace(3.0, 5.0, 80)
    store.upsert_bars(
        "510300",
        pd.DataFrame({"open": prices, "high": prices * 1.002, "low": prices * 0.998, "close": prices, "volume": 1e6, "amount": 4e6}, index=idx),
        source="akshare",
    )
    store.upsert_bars(
        "511880",
        pd.DataFrame({"open": 100.0, "high": 100.05, "low": 99.95, "close": 100.0, "volume": 1e5, "amount": 1e7}, index=idx),
        source="akshare",
    )

    u = UniverseConfig(
        assets=[Asset("510300", "沪深300ETF", "sh", "a_broad")],
        safe_haven=Asset("511880", "银华日利", "sh", "cash"),
        benchmark=Asset("510300", "沪深300ETF", "sh", "a_broad"),
    )
    cfg = {
        "strategies": {
            "trend_atr": {"enabled": True, "breakout_window": 20, "ema_fast": 10, "ema_slow": 30, "atr_window": 14, "atr_stop_mult": 2.5, "max_single_weight": 0.35, "min_history_days": 60},
            "momentum_r2": {"enabled": True},
            "risk_parity": {"enabled": True},
        },
        "combiner": {"weights": {"trend_atr": 1.0, "momentum_r2": 0.0, "risk_parity": 0.0}},
    }

    ins, latest_px = run_shared_model_signal(store, u, cfg, run_mode="eod", persist=True)
    assert any(i.symbol == "510300" and i.target_weight > 0.30 for i in ins)

    # Admin team summary aggregates both admin and alice cleanly
    team = build_admin_team_summary(
        users=store.load_users(),
        accounts=store.load_all_accounts(),
        positions_by_user=store.load_all_positions(),
        all_trades_df=store.load_all_trades(),
        signals_df=store.load_latest_signals(),
        close_df=pd.DataFrame({"510300": prices, "511880": 100.0}, index=idx),
        latest_prices=latest_px,
        as_of_date=idx[-1].date(),
    )
    assert set(team["user_id"]) == {"admin", "alice"}
