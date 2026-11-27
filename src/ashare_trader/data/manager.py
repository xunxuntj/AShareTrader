"""Dual-source incremental data manager with qfq adjustment drift detection."""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from ashare_trader.data.akshare_feed import AKShareFeed
from ashare_trader.data.baostock_feed import BaostockFeed
from ashare_trader.data.base import DataFeed
from ashare_trader.data.calendar import cn_today
from ashare_trader.models import Asset, DataHealthRecord, UniverseConfig
from ashare_trader.storage.base import StorageBackend


class DataManager:
    """Orchestrates primary/backup feeds, qfq drift checks, and health logging."""

    DEFAULT_HISTORY_START = date(2016, 1, 1)
    OVERLAP_DAYS = 10  # Calendar days (~5 trading days) for dividend drift check
    DRIFT_THRESHOLD = 0.001  # 0.1% close price discrepancy triggers full refresh

    def __init__(
        self,
        storage: StorageBackend,
        primary_feed: DataFeed | None = None,
        backup_feed: DataFeed | None = None,
    ) -> None:
        self.storage = storage
        self.primary_feed = primary_feed or AKShareFeed()
        self.backup_feed = backup_feed or BaostockFeed()

    def sync_universe(
        self,
        universe: UniverseConfig,
        start_date: date | None = None,
        end_date: date | None = None,
        include_intraday: bool = False,
        force_full: bool = False,
    ) -> list[DataHealthRecord]:
        """Synchronize all assets in universe and persist data health telemetry."""
        history_start = start_date or self.DEFAULT_HISTORY_START
        target_end = end_date or cn_today()
        run_at = datetime.now(timezone.utc)
        health_records: list[DataHealthRecord] = []

        intraday_map: dict[str, dict[str, float]] = {}
        if include_intraday:
            intraday_map = self.primary_feed.fetch_intraday_snapshots(universe.all_assets)

        try:
            for asset in universe.all_assets:
                record = self._sync_single_asset(
                    asset=asset,
                    history_start=history_start,
                    target_end=target_end,
                    include_intraday=include_intraday,
                    intraday_quote=intraday_map.get(asset.symbol),
                    force_full=force_full,
                    run_at=run_at,
                )
                health_records.append(record)
        finally:
            if isinstance(self.backup_feed, BaostockFeed):
                self.backup_feed.close()

        self.storage.log_data_health(health_records)
        return health_records

    def _sync_single_asset(
        self,
        asset: Asset,
        history_start: date,
        target_end: date,
        include_intraday: bool,
        intraday_quote: dict[str, float] | None,
        force_full: bool,
        run_at: datetime,
    ) -> DataHealthRecord:
        t0 = time.perf_counter()
        latest_final = None if force_full else self.storage.get_latest_final_date(asset.symbol)

        # During intraday runs (14:35), cap finalized historical bars at yesterday so unclosed bars never mark is_final=True
        hist_end = target_end - timedelta(days=1) if include_intraday else target_end
        fetch_start = (
            max(history_start, latest_final - timedelta(days=self.OVERLAP_DAYS))
            if latest_final is not None
            else history_start
        )

        # Fetch historical daily bars with primary -> backup fallback
        df, source_used, status, err_msg = self._fetch_with_fallback(asset, fetch_start, hist_end)

        if not df.empty:
            if latest_final is not None and not force_full and self._has_adjustment_drift(asset.symbol, df):
                # Dividend/split detected: perform full qfq refresh from history_start
                full_df, full_src, full_status, full_err = self._fetch_with_fallback(
                    asset, history_start, hist_end
                )
                if not full_df.empty:
                    self.storage.replace_symbol_bars(asset.symbol, full_df, source=full_src, is_final=True)
                    df = full_df
                    source_used = full_src
                    status = full_status
                    err_msg = full_err
            elif force_full:
                self.storage.replace_symbol_bars(asset.symbol, df, source=source_used, is_final=True)
            else:
                self.storage.upsert_bars(asset.symbol, df, source=source_used, is_final=True)

        # Optionally append intraday snapshot marked is_final=False
        if intraday_quote is not None:
            today_ts = pd.Timestamp(target_end)
            snap_df = pd.DataFrame([intraday_quote], index=pd.DatetimeIndex([today_ts], name="trade_date"))
            self.storage.upsert_bars(asset.symbol, snap_df, source=f"{self.primary_feed.name}_spot", is_final=False)
            if status == "failed":
                status = "degraded"
                source_used = f"{self.primary_feed.name}_spot"

        loaded = self.storage.load_bars([asset.symbol]).get(asset.symbol)
        last_trade_date = loaded.index.max().date() if loaded is not None and not loaded.empty else None
        latency_ms = int((time.perf_counter() - t0) * 1000)

        return DataHealthRecord(
            run_at=run_at,
            symbol=asset.symbol,
            last_trade_date=last_trade_date,
            source_used=source_used,
            status=status,
            latency_ms=latency_ms,
            error_msg=err_msg,
        )

    def _fetch_with_fallback(
        self,
        asset: Asset,
        start_date: date,
        end_date: date,
    ) -> tuple[pd.DataFrame, str, str, str | None]:
        primary_err: str | None = None
        try:
            df = self.primary_feed.fetch_daily_bars(asset, start_date, end_date)
            if not df.empty:
                return df, self.primary_feed.name, "ok", None
            primary_err = f"{self.primary_feed.name} returned empty dataframe"
        except Exception as exc:  # noqa: BLE001
            primary_err = str(exc)

        try:
            df_backup = self.backup_feed.fetch_daily_bars(asset, start_date, end_date)
            if not df_backup.empty:
                return (
                    df_backup,
                    self.backup_feed.name,
                    "degraded",
                    f"Primary failed ({primary_err}); fell back to {self.backup_feed.name}",
                )
            backup_err = f"{self.backup_feed.name} returned empty dataframe"
        except Exception as exc:  # noqa: BLE001
            backup_err = str(exc)

        return pd.DataFrame(), "cache", "failed", f"Primary: {primary_err} | Backup: {backup_err}"

    def _has_adjustment_drift(self, symbol: str, incoming_df: pd.DataFrame) -> bool:
        """Compare overlapping historical close prices to detect forward-adjustment drift."""
        existing = self.storage.load_bars([symbol]).get(symbol)
        if existing is None or existing.empty:
            return False
        if "is_final" in existing.columns:
            existing = existing[existing["is_final"].astype(bool)]
        common_idx = existing.index.intersection(incoming_df.index)
        if common_idx.empty:
            return False
        old_close = existing.loc[common_idx, "close"].astype(float)
        new_close = incoming_df.loc[common_idx, "close"].astype(float)
        rel_diff = (old_close - new_close).abs() / old_close.replace(0, pd.NA)
        max_diff = rel_diff.max(skipna=True)
        return bool(pd.notna(max_diff) and max_diff > self.DRIFT_THRESHOLD)

    def load_price_panel(
        self,
        symbols: list[str],
        field: str = "close",
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> pd.DataFrame:
        """Return aligned Date x Symbol DataFrame for a given OHLCV field (preserves NaNs for dynamic listing)."""
        bars_map = self.storage.load_bars(symbols, start_date=start_date, end_date=end_date)
        series_map = {sym: df[field].astype(float) for sym, df in bars_map.items() if field in df.columns}
        if not series_map:
            return pd.DataFrame()
        panel = pd.DataFrame(series_map).sort_index()
        panel.index.name = "trade_date"
        return panel
