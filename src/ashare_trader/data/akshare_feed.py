"""AKShare primary data feed with Eastmoney/Sina fallback and retry backoff."""

from __future__ import annotations

import time
from datetime import date

import pandas as pd

from ashare_trader.data.base import DataFeed
from ashare_trader.models import Asset


class AKShareFeed(DataFeed):
    """Primary ETF/stock feed powered by AKShare."""

    def __init__(self, max_retries: int = 2, backoff_sec: float = 1.0) -> None:
        self.max_retries = max_retries
        self.backoff_sec = backoff_sec

    @property
    def name(self) -> str:
        return "akshare"

    def fetch_daily_bars(self, asset: Asset, start_date: date, end_date: date) -> pd.DataFrame:
        import akshare as ak

        start_str = start_date.strftime("%Y%m%d")
        end_str = end_date.strftime("%Y%m%d")

        last_err: Exception | None = None
        # Attempt 1: Eastmoney ETF forward-adjusted daily bars
        for attempt in range(self.max_retries):
            try:
                if asset.asset_type == "etf":
                    raw = ak.fund_etf_hist_em(
                        symbol=asset.symbol,
                        period="daily",
                        start_date=start_str,
                        end_date=end_str,
                        adjust="qfq",
                    )
                else:
                    raw = ak.stock_zh_a_hist(
                        symbol=asset.symbol,
                        period="daily",
                        start_date=start_str,
                        end_date=end_str,
                        adjust="qfq",
                    )
                df = self._normalize_em(raw)
                if not df.empty:
                    return df
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(self.backoff_sec * (attempt + 1))

        # Attempt 2: Sina ETF daily bars fallback (not qfq, only used when EM fails)
        if asset.asset_type == "etf":
            try:
                raw_sina = ak.fund_etf_hist_sina(symbol=asset.sina_code)
                df_sina = self._normalize_sina(raw_sina, start_date, end_date)
                if not df_sina.empty:
                    return df_sina
            except Exception as exc:  # noqa: BLE001
                last_err = exc

        if last_err is not None:
            raise RuntimeError(f"AKShare failed for {asset.symbol}: {last_err}") from last_err
        return pd.DataFrame()

    def fetch_intraday_snapshots(self, assets: list[Asset]) -> dict[str, dict[str, float]]:
        import akshare as ak

        target_symbols = {a.symbol for a in assets}
        snapshots: dict[str, dict[str, float]] = {}
        try:
            spot = ak.fund_etf_spot_em()
            if spot is None or spot.empty:
                return snapshots
            code_col = "代码" if "代码" in spot.columns else spot.columns[0]
            filtered = spot[spot[code_col].astype(str).isin(target_symbols)]
            for _, row in filtered.iterrows():
                sym = str(row[code_col]).zfill(6)
                price = pd.to_numeric(row.get("最新价"), errors="coerce")
                if pd.isna(price) or price <= 0:
                    continue
                snapshots[sym] = {
                    "open": float(pd.to_numeric(row.get("今开"), errors="coerce") or price),
                    "high": float(pd.to_numeric(row.get("最高"), errors="coerce") or price),
                    "low": float(pd.to_numeric(row.get("最低"), errors="coerce") or price),
                    "close": float(price),
                    "volume": float(pd.to_numeric(row.get("成交量"), errors="coerce") or 0.0),
                    "amount": float(pd.to_numeric(row.get("成交额"), errors="coerce") or 0.0),
                }
        except Exception:  # noqa: BLE001
            return {}
        return snapshots

    @staticmethod
    def _normalize_em(raw: pd.DataFrame | None) -> pd.DataFrame:
        if raw is None or raw.empty:
            return pd.DataFrame()
        col_map = {
            "日期": "trade_date",
            "开盘": "open",
            "最高": "high",
            "最低": "low",
            "收盘": "close",
            "成交量": "volume",
            "成交额": "amount",
        }
        df = raw.rename(columns=col_map)
        required = ["trade_date", "open", "high", "low", "close"]
        if not all(c in df.columns for c in required):
            return pd.DataFrame()
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col not in df.columns:
                df[col] = 0.0
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df[["trade_date", "open", "high", "low", "close", "volume", "amount"]]
        df = df.dropna(subset=["trade_date", "close"]).set_index("trade_date").sort_index()
        return df

    @staticmethod
    def _normalize_sina(raw: pd.DataFrame | None, start_date: date, end_date: date) -> pd.DataFrame:
        if raw is None or raw.empty:
            return pd.DataFrame()
        df = raw.rename(columns={"date": "trade_date"})
        if "trade_date" not in df.columns:
            return pd.DataFrame()
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.normalize()
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if "amount" not in df.columns:
            df["amount"] = df["close"] * df["volume"]
        df = df[["trade_date", "open", "high", "low", "close", "volume", "amount"]]
        df = df.dropna(subset=["trade_date", "close"]).set_index("trade_date").sort_index()
        return df.loc[(df.index >= pd.Timestamp(start_date)) & (df.index <= pd.Timestamp(end_date))]
