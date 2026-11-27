"""Quantitative performance metrics and yearly/monthly attribution."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compute_metrics(
    equity_curve: pd.Series,
    trades: pd.DataFrame | None = None,
    risk_free_rate: float = 0.02,
    benchmark_curve: pd.Series | None = None,
) -> dict[str, Any]:
    """Compute comprehensive risk-adjusted performance metrics."""
    eq = equity_curve.dropna()
    if len(eq) < 2:
        return {}

    daily_ret = eq.pct_change().dropna()
    n_days = len(daily_ret)
    years = max(n_days / 252.0, 1e-6)

    total_return = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    cagr = float((eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0) if eq.iloc[-1] > 0 else -1.0

    running_max = eq.cummax()
    drawdown = eq / running_max - 1.0
    max_dd = float(abs(drawdown.min()))

    ann_vol = float(daily_ret.std() * np.sqrt(252.0))
    daily_rf = risk_free_rate / 252.0
    excess_ret = daily_ret - daily_rf
    sharpe = float(excess_ret.mean() / daily_ret.std() * np.sqrt(252.0)) if daily_ret.std() > 1e-12 else 0.0

    downside = excess_ret[excess_ret < 0]
    downside_std = float(downside.std() * np.sqrt(252.0)) if len(downside) > 1 else 0.0
    sortino = float((cagr - risk_free_rate) / downside_std) if downside_std > 1e-12 else 0.0
    calmar = float(cagr / max_dd) if max_dd > 1e-8 else 0.0

    # Win rate & payoff ratio on daily returns
    pos_days = daily_ret[daily_ret > 0]
    neg_days = daily_ret[daily_ret < 0]
    daily_win_rate = float(len(pos_days) / max(1, len(pos_days) + len(neg_days)))
    avg_win = float(pos_days.mean()) if len(pos_days) > 0 else 0.0
    avg_loss = float(abs(neg_days.mean())) if len(neg_days) > 0 else 0.0
    payoff_ratio = float(avg_win / avg_loss) if avg_loss > 1e-12 else 0.0

    # Turnover from trades DataFrame
    ann_turnover = 0.0
    trade_count = 0
    total_fees = 0.0
    if trades is not None and not trades.empty:
        trade_count = int(len(trades))
        total_fees = float(trades["fee"].sum()) if "fee" in trades.columns else 0.0
        avg_eq = float(eq.mean())
        if avg_eq > 0 and "notional" in trades.columns:
            ann_turnover = float(trades["notional"].abs().sum() / (2.0 * avg_eq) / years)

    metrics: dict[str, Any] = {
        "start_date": eq.index[0].strftime("%Y-%m-%d"),
        "end_date": eq.index[-1].strftime("%Y-%m-%d"),
        "trading_days": int(n_days),
        "total_return": round(total_return, 4),
        "cagr": round(cagr, 4),
        "max_drawdown": round(max_dd, 4),
        "ann_volatility": round(ann_vol, 4),
        "sharpe": round(sharpe, 3),
        "sortino": round(sortino, 3),
        "calmar": round(calmar, 3),
        "daily_win_rate": round(daily_win_rate, 4),
        "payoff_ratio": round(payoff_ratio, 3),
        "ann_turnover": round(ann_turnover, 2),
        "trade_count": trade_count,
        "total_fees": round(total_fees, 2),
    }

    if benchmark_curve is not None:
        bm = benchmark_curve.reindex(eq.index).ffill().dropna()
        if len(bm) >= 2:
            bm_total = float(bm.iloc[-1] / bm.iloc[0] - 1.0)
            bm_cagr = float((bm.iloc[-1] / bm.iloc[0]) ** (1.0 / years) - 1.0)
            bm_dd = float(abs((bm / bm.cummax() - 1.0).min()))
            metrics["benchmark_cagr"] = round(bm_cagr, 4)
            metrics["benchmark_max_dd"] = round(bm_dd, 4)
            metrics["excess_cagr"] = round(cagr - bm_cagr, 4)
            metrics["benchmark_total_return"] = round(bm_total, 4)

    return metrics


def compute_yearly_metrics(equity_curve: pd.Series, benchmark_curve: pd.Series | None = None) -> pd.DataFrame:
    """Compute calendar-year return, max drawdown, and Sharpe ratio."""
    eq = equity_curve.dropna()
    if eq.empty:
        return pd.DataFrame()
    rows: list[dict[str, Any]] = []
    for year, grp in eq.groupby(eq.index.year):
        if len(grp) < 5:
            continue
        ret = float(grp.iloc[-1] / grp.iloc[0] - 1.0)
        dd = float(abs((grp / grp.cummax() - 1.0).min()))
        dr = grp.pct_change().dropna()
        sharpe = float(dr.mean() / dr.std() * np.sqrt(252.0)) if dr.std() > 1e-12 else 0.0
        row: dict[str, Any] = {
            "year": int(year),
            "return": round(ret, 4),
            "max_drawdown": round(dd, 4),
            "sharpe": round(sharpe, 2),
        }
        if benchmark_curve is not None:
            bm_grp = benchmark_curve.reindex(grp.index).ffill().dropna()
            if len(bm_grp) >= 2:
                bm_ret = float(bm_grp.iloc[-1] / bm_grp.iloc[0] - 1.0)
                row["benchmark_return"] = round(bm_ret, 4)
                row["excess_return"] = round(ret - bm_ret, 4)
        rows.append(row)
    return pd.DataFrame(rows)


def compute_monthly_returns(equity_curve: pd.Series) -> pd.DataFrame:
    """Return Year x Month return matrix for heatmap visualization."""
    eq = equity_curve.dropna()
    if eq.empty:
        return pd.DataFrame()
    monthly_eq = eq.resample("ME").last()
    monthly_ret = monthly_eq.pct_change().dropna()
    if monthly_ret.empty:
        return pd.DataFrame()
    df = pd.DataFrame({"ret": monthly_ret})
    df["year"] = df.index.year
    df["month"] = df.index.month
    pivot = df.pivot(index="year", columns="month", values="ret")
    pivot.columns = [f"{m:02d}月" for m in pivot.columns]
    return pivot
