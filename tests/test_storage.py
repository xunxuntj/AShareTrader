"""Tests for LocalStorage and LedgerService."""

from datetime import date, datetime, timezone

import pandas as pd

from ashare_trader.execution.ledger import LedgerService, compute_live_equity_curve
from ashare_trader.models import DataHealthRecord, TradeInstruction
from ashare_trader.storage.local_store import LocalStorage


def test_local_storage_bars_and_is_final(tmp_path):
    store = LocalStorage(data_dir=tmp_path)
    idx = pd.date_range("2024-01-02", periods=3, freq="B")
    df = pd.DataFrame(
        {"open": [1.0, 1.1, 1.2], "high": [1.05, 1.15, 1.25], "low": [0.98, 1.08, 1.18], "close": [1.02, 1.12, 1.22]},
        index=idx,
    )
    assert store.upsert_bars("510300", df, source="akshare", is_final=True) == 3
    assert store.get_latest_final_date("510300") == date(2024, 1, 4)

    # Append intraday non-final bar; latest final date must remain 2024-01-04
    intraday = pd.DataFrame(
        {"open": [1.22], "high": [1.26], "low": [1.21], "close": [1.25]},
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-05")]),
    )
    store.upsert_bars("510300", intraday, source="akshare_spot", is_final=False)
    assert store.get_latest_final_date("510300") == date(2024, 1, 4)

    # Overwrite with final EOD bar
    eod = pd.DataFrame(
        {"open": [1.22], "high": [1.27], "low": [1.21], "close": [1.26]},
        index=pd.DatetimeIndex([pd.Timestamp("2024-01-05")]),
    )
    store.upsert_bars("510300", eod, source="akshare", is_final=True)
    assert store.get_latest_final_date("510300") == date(2024, 1, 5)
    loaded = store.load_bars(["510300"])["510300"]
    assert float(loaded.loc["2024-01-05", "close"]) == 1.26


def test_ledger_cash_positions_and_trailing_stop(tmp_path):
    store = LocalStorage(data_dir=tmp_path)
    ledger = LedgerService(store)
    assert ledger.get_account().cash == 100000.0

    ledger.record_execution(
        trade_date=date(2024, 2, 1),
        symbol="510300",
        side="BUY",
        shares=10000,
        price=3.5,
        fee=3.5,
        stop_loss_price=3.2,
    )
    assert abs(ledger.get_account().cash - (100000.0 - 35003.5)) < 1e-6
    pos = ledger.get_positions()["510300"]
    assert pos.shares == 10000
    assert pos.stop_loss_price == 3.2

    # Price rallies -> trailing stop ratchets up, never down
    ledger.refresh_trailing_stops({"510300": 3.8}, {"510300": 3.45})
    pos = ledger.get_positions()["510300"]
    assert pos.highest_price == 3.8
    assert pos.stop_loss_price == 3.45

    ledger.refresh_trailing_stops({"510300": 3.7}, {"510300": 3.30})
    pos = ledger.get_positions()["510300"]
    assert pos.stop_loss_price == 3.45  # Must not lower stop

    # Partial sell updates cash
    ledger.record_execution(
        trade_date=date(2024, 2, 10),
        symbol="510300",
        side="SELL",
        shares=10000,
        price=3.8,
        fee=3.8,
    )
    assert "510300" not in ledger.get_positions()
    assert abs(ledger.get_account().cash - (100000.0 + 3000.0 - 7.3)) < 1e-6


def test_health_and_signals_persistence(tmp_path):
    store = LocalStorage(data_dir=tmp_path)
    store.log_data_health(
        [
            DataHealthRecord(
                run_at=datetime.now(timezone.utc),
                symbol="510300",
                last_trade_date=date(2024, 3, 1),
                source_used="akshare",
                status="ok",
                latency_ms=120,
            )
        ]
    )
    df_h = store.load_latest_data_health()
    assert len(df_h) == 1
    assert df_h.iloc[0]["symbol"] == "510300"

    store.save_signals(
        [
            TradeInstruction(
                signal_date=date(2024, 3, 1),
                run_mode="intraday",
                strategy_name="combined",
                symbol="510300",
                action="BUY",
                target_weight=0.5,
                ref_price=3.5,
                stop_loss_price=3.2,
                reason="test",
            )
        ]
    )
    df_s = store.load_latest_signals()
    assert len(df_s) == 1
    assert df_s.iloc[0]["action"] == "BUY"

    # Backtest run persistence should be idempotent per strategy_name
    store.save_backtest_run(date(2024, 3, 1), "trend_atr", {"w": 20}, {"cagr": 0.12}, [{"date": "2024-03-01", "equity": 100000}])
    store.save_backtest_run(date(2024, 3, 2), "trend_atr", {"w": 20}, {"cagr": 0.13}, [{"date": "2024-03-02", "equity": 101000}])
    df_bt = store.load_latest_backtest_runs()
    assert len(df_bt) == 1
    assert df_bt.iloc[0]["run_date"] == "2024-03-02"


def test_compute_live_equity_curve_tracks_nav_and_drawdown(tmp_path):
    store = LocalStorage(tmp_path)
    ledger = LedgerService(store)
    ledger.record_execution(date(2024, 2, 1), "510300", "BUY", 10000, 3.5, fee=3.5)
    trades_df = store.load_trades()

    idx = pd.date_range("2024-02-01", periods=4, freq="B")
    close_df = pd.DataFrame({"510300": [3.5, 3.7, 3.6, 3.8]}, index=idx)
    eq_df = compute_live_equity_curve(trades_df, close_df, initial_capital=100000.0)

    assert len(eq_df) == 4
    assert eq_df["nav"].iloc[-1] > 1.02  # +3000 profit on 100k -> ~+3%
    assert eq_df["drawdown"].min() < 0.0  # Pullback from 3.7 to 3.6 recorded


def test_compute_live_equity_curve_handles_weekend_deposit(tmp_path):
    store = LocalStorage(tmp_path)
    ledger = LedgerService(store)
    # 2024-02-03 is Saturday -> should be applied on Monday 2024-02-05 without distorting TWR NAV
    ledger.record_execution(date(2024, 2, 1), "510300", "BUY", 10000, 3.5, fee=0.0)
    ledger.record_execution(date(2024, 2, 3), "CASH", "DEPOSIT", 0, 50000.0, fee=0.0)
    trades_df = store.load_trades()

    idx = pd.DatetimeIndex(["2024-02-01", "2024-02-02", "2024-02-05"])
    close_df = pd.DataFrame({"510300": [3.5, 3.5, 3.5]}, index=idx)
    eq_df = compute_live_equity_curve(trades_df, close_df, initial_capital=150000.0)

    assert eq_df.loc["2024-02-05", "equity"] == 150000.0
    assert abs(eq_df.loc["2024-02-05", "nav"] - 1.0) < 1e-6
