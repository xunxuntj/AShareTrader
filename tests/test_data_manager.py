"""Tests for DataManager dual-source fallback and forward-adjustment drift refresh."""

from datetime import date

import pandas as pd

from ashare_trader.data.base import DataFeed
from ashare_trader.data.manager import DataManager
from ashare_trader.models import Asset, UniverseConfig
from ashare_trader.storage.local_store import LocalStorage


class _MockFeed(DataFeed):
    def __init__(self, feed_name: str, data: pd.DataFrame | None = None, fail: bool = False) -> None:
        self._name = feed_name
        self._data = data if data is not None else pd.DataFrame()
        self._fail = fail
        self.calls: list[tuple[date, date]] = []

    @property
    def name(self) -> str:
        return self._name

    def fetch_daily_bars(self, asset: Asset, start_date: date, end_date: date) -> pd.DataFrame:
        self.calls.append((start_date, end_date))
        if self._fail:
            raise RuntimeError("simulated network error")
        return self._data.loc[(self._data.index >= pd.Timestamp(start_date)) & (self._data.index <= pd.Timestamp(end_date))]


def _make_universe() -> UniverseConfig:
    a = Asset("510300", "沪深300ETF", "sh", "a_broad", "etf")
    cash = Asset("511880", "银华日利", "sh", "cash", "etf")
    return UniverseConfig(assets=[a], safe_haven=cash, benchmark=a)


def _sample_df(multiplier: float = 1.0) -> pd.DataFrame:
    idx = pd.date_range("2024-01-02", periods=20, freq="B")
    vals = [3.0 * multiplier + i * 0.01 for i in range(len(idx))]
    return pd.DataFrame(
        {"open": vals, "high": vals, "low": vals, "close": vals, "volume": 1000.0, "amount": 3000.0},
        index=idx,
    )


def test_primary_failure_falls_back_to_backup(tmp_path):
    store = LocalStorage(data_dir=tmp_path)
    primary = _MockFeed("akshare", fail=True)
    backup = _MockFeed("baostock", data=_sample_df(1.0))
    dm = DataManager(storage=store, primary_feed=primary, backup_feed=backup)

    records = dm.sync_universe(_make_universe(), start_date=date(2024, 1, 1), end_date=date(2024, 2, 1))
    assert all(r.status == "degraded" and r.source_used == "baostock" for r in records)
    loaded = store.load_bars(["510300"])["510300"]
    assert len(loaded) == 20


def test_dividend_adjustment_drift_triggers_full_refresh(tmp_path):
    store = LocalStorage(data_dir=tmp_path)
    old_df = _sample_df(1.0).iloc[:15]
    store.upsert_bars("510300", old_df, source="akshare", is_final=True)

    # Simulate dividend forward adjustment (-5% shift across entire history)
    adjusted_df = _sample_df(0.95)
    primary = _MockFeed("akshare", data=adjusted_df)
    backup = _MockFeed("baostock", data=adjusted_df)
    dm = DataManager(storage=store, primary_feed=primary, backup_feed=backup)

    dm.sync_universe(_make_universe(), start_date=date(2024, 1, 1), end_date=date(2024, 2, 1))

    # First call was incremental overlap, second call must be full refresh from 2024-01-01
    assert any(call[0] == date(2024, 1, 1) for call in primary.calls)
    loaded = store.load_bars(["510300"])["510300"]
    # Earliest historical bar should now reflect the 0.95 adjusted price
    assert abs(float(loaded.iloc[0]["close"]) - 3.0 * 0.95) < 1e-6
