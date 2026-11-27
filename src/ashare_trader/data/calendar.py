"""A-Share trading calendar utility."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

import pandas as pd

CN_TZ = timezone(timedelta(hours=8))


def cn_today() -> date:
    """Return current date in China Standard Time (UTC+8), safe on UTC cloud runners."""
    return datetime.now(CN_TZ).date()


@lru_cache(maxsize=1)
def _fetch_sina_trade_dates() -> set[date]:
    """Fetch official China A-Share trading dates via AKShare."""
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        if df is not None and not df.empty:
            col = "trade_date" if "trade_date" in df.columns else df.columns[0]
            return set(pd.to_datetime(df[col]).dt.date.tolist())
    except Exception:  # noqa: BLE001
        pass
    return set()


def is_cn_trading_day(target_date: date | None = None) -> bool:
    """Return True if target_date is an A-Share trading day."""
    d = target_date or cn_today()
    if d.weekday() >= 5:
        return False
    cal = _fetch_sina_trade_dates()
    if cal:
        return d in cal
    return True
