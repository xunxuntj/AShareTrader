"""Baostock backup data feed for forward-adjusted historical daily bars."""

from __future__ import annotations

from datetime import date

import pandas as pd

from ashare_trader.data.base import DataFeed
from ashare_trader.models import Asset


class BaostockFeed(DataFeed):
    """Backup A-Share historical feed powered by Baostock (adjustflag='2' qfq)."""

    def __init__(self) -> None:
        self._logged_in = False

    @property
    def name(self) -> str:
        return "baostock"

    def _ensure_login(self) -> None:
        if self._logged_in:
            return
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"Baostock login failed: {lg.error_msg}")
        self._logged_in = True

    def close(self) -> None:
        if not self._logged_in:
            return
        import baostock as bs

        bs.logout()
        self._logged_in = False

    def fetch_daily_bars(self, asset: Asset, start_date: date, end_date: date) -> pd.DataFrame:
        import baostock as bs

        self._ensure_login()
        rs = bs.query_history_k_data_plus(
            asset.baostock_code,
            "date,open,high,low,close,volume,amount",
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            frequency="d",
            adjustflag="2",  # Forward adjusted (qfq)
        )
        if rs.error_code != "0":
            raise RuntimeError(f"Baostock query failed for {asset.symbol}: {rs.error_msg}")

        rows: list[list[str]] = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=rs.fields).rename(columns={"date": "trade_date"})
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["trade_date", "close"]).set_index("trade_date").sort_index()
        return df[["open", "high", "low", "close", "volume", "amount"]]
