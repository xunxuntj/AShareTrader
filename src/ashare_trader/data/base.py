"""Abstract DataFeed interface for historical and intraday market data."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from ashare_trader.models import Asset


class DataFeed(ABC):
    """Contract for market data providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Identifier of this data feed."""

    @abstractmethod
    def fetch_daily_bars(
        self,
        asset: Asset,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Fetch forward-adjusted daily OHLCV bars indexed by DatetimeIndex (trade_date).

        Expected columns: open, high, low, close, volume, amount.
        """

    def fetch_intraday_snapshots(self, assets: list[Asset]) -> dict[str, dict[str, float]]:
        """Optional: fetch current intraday OHLCV snapshot for given assets."""
        return {}
