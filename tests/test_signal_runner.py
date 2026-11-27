"""Regression tests for stateless daily cloud signal pipeline (cold-start & idempotency)."""

from datetime import date

import numpy as np
import pandas as pd

from ashare_trader.execution.ledger import LedgerService
from ashare_trader.execution.signal_runner import run_daily_signal
from ashare_trader.models import Asset, UniverseConfig
from ashare_trader.storage.local_store import LocalStorage


def _build_universe() -> UniverseConfig:
    a = Asset("510300", "沪深300ETF", "sh", "a_broad", "etf")
    cash = Asset("511880", "银华日利", "sh", "cash", "etf")
    return UniverseConfig(assets=[a], safe_haven=cash, benchmark=a)


def _seed_market_data(store: LocalStorage, prices_a: list[float]) -> None:
    idx = pd.date_range("2024-01-02", periods=len(prices_a), freq="B")
    df_a = pd.DataFrame(
        {
            "open": prices_a,
            "high": [p * 1.01 for p in prices_a],
            "low": [p * 0.99 for p in prices_a],
            "close": prices_a,
            "volume": 1e6,
            "amount": 1e7,
        },
        index=idx,
    )
    df_c = pd.DataFrame(
        {"open": 100.0, "high": 100.05, "low": 99.95, "close": 100.0, "volume": 1e5, "amount": 1e7},
        index=idx,
    )
    store.upsert_bars("510300", df_a, source="akshare", is_final=True)
    store.upsert_bars("511880", df_c, source="akshare", is_final=True)


def _trend_only_cfg() -> dict:
    return {
        "strategies": {
            "momentum_r2": {"enabled": True},
            "trend_atr": {
                "enabled": True,
                "breakout_window": 20,
                "ema_fast": 10,
                "ema_slow": 30,
                "atr_window": 14,
                "atr_stop_mult": 2.5,
                "max_single_weight": 0.35,
                "min_history_days": 60,
            },
            "risk_parity": {"enabled": True},
        },
        "combiner": {"weights": {"momentum_r2": 0.0, "trend_atr": 1.0, "risk_parity": 0.0}},
    }


def test_cold_start_preserves_held_trend_without_new_high(tmp_path):
    """P0 regression: held asset that pauses below 20d high (but above stop & EMA) must NOT be sold on cold start."""
    store = LocalStorage(tmp_path)
    # 70-day uptrend peaking at 2.00 on day 68, pulling back slightly to 1.97 on day 70 (still EMA10>EMA30 & above stop)
    prices = list(np.linspace(1.0, 2.0, 68)) + [1.99, 1.97]
    _seed_market_data(store, prices)

    ledger = LedgerService(store)
    ledger.record_execution(
        trade_date=date(2024, 3, 1),
        symbol="510300",
        side="BUY",
        shares=17000,
        price=1.80,
        stop_loss_price=1.85,
    )

    instructions, _ = run_daily_signal(store, _build_universe(), _trend_only_cfg(), run_mode="intraday", persist=True)
    ins_map = {i.symbol: i for i in instructions}
    assert "510300" in ins_map
    assert ins_map["510300"].target_weight > 0.30
    assert ins_map["510300"].action != "SELL"


def test_cold_start_enforces_persisted_trailing_stop_breach(tmp_path):
    """P0 regression: if price drops below persisted ledger stop_loss_price, cold-start MUST trigger SELL exit."""
    store = LocalStorage(tmp_path)
    prices = list(np.linspace(1.0, 2.0, 69)) + [1.90]
    _seed_market_data(store, prices)

    ledger = LedgerService(store)
    # Persisted trailing stop was ratcheted up to 1.94 earlier; current price 1.90 breaches it
    ledger.record_execution(
        trade_date=date(2024, 3, 1),
        symbol="510300",
        side="BUY",
        shares=17000,
        price=1.80,
        stop_loss_price=1.94,
    )

    instructions, _ = run_daily_signal(store, _build_universe(), _trend_only_cfg(), run_mode="intraday", persist=True)
    ins_map = {i.symbol: i for i in instructions}
    assert ins_map["510300"].action == "SELL"
    assert ins_map["510300"].target_weight == 0.0


def test_save_signals_is_idempotent_for_same_day_and_mode(tmp_path):
    """P1 regression: re-running daily_signal on the same day must not create duplicate rows."""
    store = LocalStorage(tmp_path)
    prices = list(np.linspace(1.0, 2.0, 70))
    _seed_market_data(store, prices)

    run_daily_signal(store, _build_universe(), _trend_only_cfg(), run_mode="intraday", persist=True)
    first_count = len(store.load_latest_signals(limit=100))
    run_daily_signal(store, _build_universe(), _trend_only_cfg(), run_mode="intraday", persist=True)
    second_count = len(store.load_latest_signals(limit=100))

    assert first_count > 0
    assert second_count == first_count


def test_sub_lot_rebalance_downgrades_to_hold(tmp_path):
    """P1 regression: never emit a BUY/SELL 0-share instruction when cash delta is below 1 lot."""
    store = LocalStorage(tmp_path)
    # Bear market -> target is 100% 511880 (price 100.0, 1 lot = 10,000 RMB)
    prices = list(np.linspace(2.0, 1.0, 70))
    _seed_market_data(store, prices)

    # Hold 900 shares of 511880 (90k) + 10k cash -> wait: set cash to 5000 so total=95k, diff=5.2% (>3%) = 5000 < 10000 (1 lot)
    ledger = LedgerService(store)
    ledger.record_execution(
        trade_date=date(2024, 3, 1),
        symbol="511880",
        side="BUY",
        shares=900,
        price=100.0,
    )
    ledger.record_execution(
        trade_date=date(2024, 3, 2),
        symbol="CASH",
        side="WITHDRAW",
        shares=0,
        price=5000.0,
    )

    instructions, _ = run_daily_signal(store, _build_universe(), _trend_only_cfg(), run_mode="intraday", persist=False)
    ins_map = {i.symbol: i for i in instructions}
    assert ins_map["511880"].action == "HOLD"
    assert ins_map["511880"].suggested_shares == 0
