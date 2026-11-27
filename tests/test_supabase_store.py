"""Mocked unit tests for SupabaseStorage REST batching and idempotency."""

from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from ashare_trader.models import AccountState, Position, TradeInstruction
from ashare_trader.storage.supabase_store import SupabaseStorage


def _make_mock_client():
    client = MagicMock()
    table_mock = MagicMock()
    # Chainable query builder returning itself
    for method in ["select", "eq", "gt", "gte", "lte", "order", "limit", "range", "upsert", "insert", "delete"]:
        getattr(table_mock, method).return_value = table_mock
    resp = MagicMock()
    resp.data = []
    table_mock.execute.return_value = resp
    client.table.return_value = table_mock
    return client, table_mock, resp


def test_supabase_upsert_bars_batches_correctly():
    client, table_mock, _ = _make_mock_client()
    store = SupabaseStorage(url="https://mock.supabase.co", key="mock-key", client=client)
    idx = pd.date_range("2020-01-01", periods=650, freq="B")
    df = pd.DataFrame({"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 100.0, "amount": 105.0}, index=idx)

    written = store.upsert_bars("510300", df, source="akshare", is_final=True)
    assert written == 650
    # 650 rows with BATCH_SIZE=500 -> 2 upsert calls
    assert table_mock.upsert.call_count == 2


def test_supabase_save_signals_deletes_prior_batch_first():
    client, table_mock, _ = _make_mock_client()
    store = SupabaseStorage(url="https://mock.supabase.co", key="mock-key", client=client)
    ins = [
        TradeInstruction(
            signal_date=date(2025, 5, 10),
            run_mode="intraday",
            strategy_name="combined",
            symbol="510300",
            action="BUY",
            target_weight=0.35,
            ref_price=3.8,
            stop_loss_price=3.5,
            reason="breakout",
        )
    ]
    store.save_signals(ins)
    assert table_mock.delete.call_count == 1
    assert table_mock.insert.call_count == 1


def test_supabase_position_zero_shares_triggers_delete():
    client, table_mock, _ = _make_mock_client()
    store = SupabaseStorage(url="https://mock.supabase.co", key="mock-key", client=client)
    store.upsert_position(Position(symbol="510300", shares=0, avg_cost=3.5, highest_price=3.8))
    assert table_mock.delete.call_count == 1
    store.save_account(AccountState(account_id="default", initial_capital=100000.0, cash=95000.0))
    assert table_mock.upsert.call_count == 1
