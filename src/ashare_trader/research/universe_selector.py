"""Candidate ETF universe profiling, correlation clustering, and low-redundancy selection."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ashare_trader.models import UniverseConfig


def compute_asset_profiles(
    bars_map: dict[str, pd.DataFrame],
    universe: UniverseConfig,
) -> pd.DataFrame:
    """Compute listing start date, history years, median daily amount (RMB M), CAGR, vol, Sharpe."""
    rows: list[dict[str, Any]] = []
    for asset in universe.assets:
        df = bars_map.get(asset.symbol)
        if df is None or df.empty or len(df) < 60:
            rows.append(
                {
                    "symbol": asset.symbol,
                    "name": asset.name,
                    "category": asset.category,
                    "first_date": None,
                    "last_date": None,
                    "years": 0.0,
                    "median_amount_m": 0.0,
                    "cagr": np.nan,
                    "ann_vol": np.nan,
                    "sharpe": np.nan,
                    "max_dd": np.nan,
                }
            )
            continue

        close = df["close"].astype(float).dropna()
        rets = close.pct_change().dropna()
        years = len(rets) / 252.0
        cagr = float((close.iloc[-1] / close.iloc[0]) ** (1.0 / max(years, 1e-6)) - 1.0)
        ann_vol = float(rets.std() * np.sqrt(252.0))
        sharpe = float((rets.mean() * 252.0 - 0.02) / ann_vol) if ann_vol > 1e-8 else 0.0
        max_dd = float(abs((close / close.cummax() - 1.0).min()))

        amt_recent = df["amount"].astype(float).tail(60) if "amount" in df.columns else pd.Series([0.0])
        median_amt_m = float(amt_recent.median() / 1e6)

        rows.append(
            {
                "symbol": asset.symbol,
                "name": asset.name,
                "category": asset.category,
                "first_date": close.index[0].strftime("%Y-%m-%d"),
                "last_date": close.index[-1].strftime("%Y-%m-%d"),
                "years": round(years, 2),
                "median_amount_m": round(median_amt_m, 1),
                "cagr": round(cagr, 4),
                "ann_vol": round(ann_vol, 4),
                "sharpe": round(sharpe, 3),
                "max_dd": round(max_dd, 4),
            }
        )
    return pd.DataFrame(rows)


def compute_correlation_matrix(
    close_df: pd.DataFrame,
    symbols: list[str],
    min_periods: int = 120,
) -> pd.DataFrame:
    """Compute pairwise daily return correlation matrix over overlapping trading days."""
    valid_syms = [s for s in symbols if s in close_df.columns]
    rets = close_df[valid_syms].pct_change()
    return rets.corr(min_periods=min_periods)


def select_low_correlation_universe(
    profiles: pd.DataFrame,
    corr_matrix: pd.DataFrame,
    min_years: float = 4.0,
    min_median_amount_m: float = 20.0,
    max_pair_corr: float = 0.72,
    max_assets: int = 10,
) -> tuple[list[str], pd.DataFrame]:
    """Greedy diversity-first selection: picks top-Sharpe liquid assets with pairwise corr <= threshold."""
    qualified = profiles[
        (profiles["years"] >= min_years)
        & (profiles["median_amount_m"] >= min_median_amount_m)
        & profiles["sharpe"].notna()
    ].copy()
    # Sort by Sharpe descending (with history length tie-breaker)
    qualified = qualified.sort_values(by=["sharpe", "years"], ascending=[False, False])

    selected: list[str] = []
    reasons: list[dict[str, Any]] = []

    for _, row in qualified.iterrows():
        sym = str(row["symbol"])
        if len(selected) >= max_assets:
            break
        if not selected:
            selected.append(sym)
            reasons.append({**row.to_dict(), "max_corr_to_selected": 0.0, "decision": "SELECTED"})
            continue

        corrs = [
            abs(float(corr_matrix.loc[sym, s]))
            for s in selected
            if sym in corr_matrix.index and s in corr_matrix.columns and pd.notna(corr_matrix.loc[sym, s])
        ]
        max_c = max(corrs) if corrs else 0.0
        if max_c <= max_pair_corr:
            selected.append(sym)
            reasons.append({**row.to_dict(), "max_corr_to_selected": round(max_c, 3), "decision": "SELECTED"})
        else:
            reasons.append({**row.to_dict(), "max_corr_to_selected": round(max_c, 3), "decision": "DROPPED_HIGH_CORR"})

    return selected, pd.DataFrame(reasons)
