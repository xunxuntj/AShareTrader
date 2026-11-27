"""Strict no-lookahead backtest engine with A-Share ETF friction and T+1 constraints."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from ashare_trader.engine.metrics import compute_metrics, compute_yearly_metrics
from ashare_trader.engine.risk import RiskManager
from ashare_trader.models import BacktestConfig, BacktestResult
from ashare_trader.strategies.base import BaseStrategy


class BacktestEngine:
    """Event-stepped daily backtester enforcing strict no-lookahead and A-Share rules."""

    def __init__(
        self,
        config: BacktestConfig | None = None,
        risk_config: dict[str, Any] | None = None,
        use_risk_overlay: bool = False,
    ) -> None:
        self.config = config or BacktestConfig()
        self.risk_config = risk_config or {}
        self.use_risk_overlay = use_risk_overlay

    def run(
        self,
        strategy: BaseStrategy,
        close_df: pd.DataFrame,
        high_df: pd.DataFrame | None = None,
        low_df: pd.DataFrame | None = None,
        open_df: pd.DataFrame | None = None,
        start_date: str | pd.Timestamp | None = None,
        end_date: str | pd.Timestamp | None = None,
        benchmark_symbol: str | None = None,
    ) -> BacktestResult:
        """Run backtest over [start_date, end_date] while giving strategy full pre-start warm-up."""
        strategy.reset_state()
        close_df = close_df.sort_index()
        high_df = (high_df if high_df is not None else close_df).reindex_like(close_df)
        low_df = (low_df if low_df is not None else close_df).reindex_like(close_df)
        open_df = (open_df if open_df is not None else close_df).reindex_like(close_df)

        all_dates = close_df.index
        if start_date is not None:
            start_ts = pd.Timestamp(start_date)
            eval_mask = all_dates >= start_ts
        else:
            eval_mask = np.ones(len(all_dates), dtype=bool)
        if end_date is not None:
            end_ts = pd.Timestamp(end_date)
            eval_mask &= all_dates <= end_ts

        eval_indices = np.where(eval_mask)[0]
        if len(eval_indices) < 2:
            raise ValueError("Insufficient dates in backtest range.")

        cash = float(self.config.initial_capital)
        holdings: dict[str, int] = {}
        buy_dates: dict[str, pd.Timestamp] = {}  # For T+1 enforcement
        risk_mgr = RiskManager(strategy.safe_haven_symbol, self.risk_config) if self.use_risk_overlay else None

        equity_records: list[tuple[pd.Timestamp, float]] = []
        weights_records: list[dict[str, float]] = []
        weights_dates: list[pd.Timestamp] = []
        trades_list: list[dict[str, Any]] = []

        pending_target: dict[str, float] | None = None
        prev_close_row = close_df.iloc[max(0, eval_indices[0] - 1)]

        for pos_idx in eval_indices:
            dt = all_dates[pos_idx]
            close_row = close_df.iloc[pos_idx]
            open_row = open_df.iloc[pos_idx]

            # If execution_timing == "next_open", execute yesterday's pending target at today's open
            if self.config.execution_timing == "next_open" and pending_target is not None:
                cash, holdings, buy_dates, new_trades = self._rebalance(
                    dt=dt,
                    target_weights=pending_target,
                    exec_prices=open_row,
                    prev_close=prev_close_row,
                    cash=cash,
                    holdings=holdings,
                    buy_dates=buy_dates,
                )
                trades_list.extend(new_trades)
                pending_target = None

            # Mark-to-market at today's close
            equity = self._calc_equity(cash, holdings, close_row, prev_close_row)
            current_weights = self._calc_weights(holdings, close_row, prev_close_row, equity)

            # Generate signal strictly using data slice [:pos_idx + 1] (zero lookahead)
            c_slice = close_df.iloc[: pos_idx + 1]
            h_slice = high_df.iloc[: pos_idx + 1]
            l_slice = low_df.iloc[: pos_idx + 1]

            signal = strategy.generate_signal(
                close_df=c_slice,
                high_df=h_slice,
                low_df=l_slice,
                current_weights=current_weights,
            )
            target_weights = signal.target_weights

            if risk_mgr is not None:
                latest_px_map = {
                    sym: float(close_row[sym])
                    for sym in close_row.index
                    if pd.notna(close_row[sym]) and close_row[sym] > 0
                }
                target_weights, _ = risk_mgr.apply(
                    target_weights=target_weights,
                    current_equity=equity,
                    latest_prices=latest_px_map,
                    stop_losses=signal.stop_losses,
                )

            if self.config.execution_timing == "close":
                cash, holdings, buy_dates, new_trades = self._rebalance(
                    dt=dt,
                    target_weights=target_weights,
                    exec_prices=close_row,
                    prev_close=prev_close_row,
                    cash=cash,
                    holdings=holdings,
                    buy_dates=buy_dates,
                )
                trades_list.extend(new_trades)
                equity = self._calc_equity(cash, holdings, close_row, prev_close_row)
                current_weights = self._calc_weights(holdings, close_row, prev_close_row, equity)
            else:
                pending_target = target_weights

            equity_records.append((dt, equity))
            weights_records.append(current_weights)
            weights_dates.append(dt)
            prev_close_row = close_row.combine_first(prev_close_row)

        equity_curve = pd.Series(
            [v for _, v in equity_records],
            index=pd.DatetimeIndex([d for d, _ in equity_records], name="trade_date"),
            name=strategy.name,
        )
        daily_returns = equity_curve.pct_change().fillna(0.0)
        weights_history = pd.DataFrame(weights_records, index=pd.DatetimeIndex(weights_dates, name="trade_date")).fillna(0.0)
        trades_df = pd.DataFrame(trades_list)

        benchmark_curve = None
        if benchmark_symbol and benchmark_symbol in close_df.columns:
            bm_series = close_df[benchmark_symbol].reindex(equity_curve.index).ffill().dropna()
            if not bm_series.empty:
                benchmark_curve = bm_series / bm_series.iloc[0] * self.config.initial_capital

        metrics = compute_metrics(equity_curve, trades=trades_df, benchmark_curve=benchmark_curve)
        yearly = compute_yearly_metrics(equity_curve, benchmark_curve=benchmark_curve)

        return BacktestResult(
            strategy_name=strategy.name,
            equity_curve=equity_curve,
            daily_returns=daily_returns,
            weights_history=weights_history,
            trades=trades_df,
            metrics=metrics,
            yearly_metrics=yearly,
            benchmark_curve=benchmark_curve,
        )

    @staticmethod
    def _calc_equity(
        cash: float,
        holdings: dict[str, int],
        prices: pd.Series,
        fallback_prices: pd.Series,
    ) -> float:
        total = cash
        for sym, sh in holdings.items():
            if sh <= 0:
                continue
            px = prices.get(sym, np.nan)
            if pd.isna(px) or px <= 0:
                px = fallback_prices.get(sym, np.nan)
            if pd.notna(px) and px > 0:
                total += sh * float(px)
        return float(total)

    @staticmethod
    def _calc_weights(
        holdings: dict[str, int],
        prices: pd.Series,
        fallback_prices: pd.Series,
        equity: float,
    ) -> dict[str, float]:
        if equity <= 0:
            return {}
        weights: dict[str, float] = {}
        for sym, sh in holdings.items():
            if sh <= 0:
                continue
            px = prices.get(sym, np.nan)
            if pd.isna(px) or px <= 0:
                px = fallback_prices.get(sym, np.nan)
            if pd.notna(px) and px > 0:
                weights[sym] = float(sh * float(px) / equity)
        return weights

    def _rebalance(
        self,
        dt: pd.Timestamp,
        target_weights: dict[str, float],
        exec_prices: pd.Series,
        prev_close: pd.Series,
        cash: float,
        holdings: dict[str, int],
        buy_dates: dict[str, pd.Timestamp],
    ) -> tuple[float, dict[str, int], dict[str, pd.Timestamp], list[dict[str, Any]]]:
        equity = self._calc_equity(cash, holdings, exec_prices, prev_close)
        if equity <= 0:
            return cash, holdings, buy_dates, []

        lot = self.config.lot_size
        slip = self.config.slippage_rate
        comm_rate = self.config.commission_rate
        min_comm = self.config.min_commission
        stamp = self.config.stamp_duty_rate
        limit_th = self.config.limit_move_threshold
        rebal_th = self.config.rebalance_threshold

        all_syms = set(holdings.keys()) | set(target_weights.keys())
        sell_orders: list[tuple[str, int, float]] = []
        buy_orders: list[tuple[str, int, float]] = []

        for sym in all_syms:
            px = exec_prices.get(sym, np.nan)
            if pd.isna(px) or px <= 0:
                continue  # Suspended or unlisted
            px = float(px)
            prev_px = prev_close.get(sym, np.nan)
            daily_chg = (px / float(prev_px) - 1.0) if (pd.notna(prev_px) and prev_px > 0) else 0.0

            cur_shares = holdings.get(sym, 0)
            cur_weight = (cur_shares * px) / equity
            tgt_weight = float(target_weights.get(sym, 0.0))

            # Skip minor drift unless exiting to 0
            if tgt_weight > 0 and abs(tgt_weight - cur_weight) < rebal_th:
                continue

            tgt_shares = int((equity * tgt_weight / px) // lot) * lot
            diff = tgt_shares - cur_shares
            if diff < 0:
                # Enforce T+1: cannot sell shares bought on the same day
                if buy_dates.get(sym) == dt:
                    continue
                # Limit-down check
                if daily_chg <= -limit_th:
                    continue
                sell_orders.append((sym, -diff, px))
            elif diff > 0:
                # Limit-up check
                if daily_chg >= limit_th:
                    continue
                buy_orders.append((sym, diff, px))

        trades: list[dict[str, Any]] = []

        # Execute sells first to free cash
        for sym, qty, raw_px in sell_orders:
            fill_px = raw_px * (1.0 - slip)
            notional = qty * fill_px
            fee = max(min_comm, notional * comm_rate) + notional * stamp
            cash += notional - fee
            holdings[sym] = holdings.get(sym, 0) - qty
            if holdings[sym] <= 0:
                holdings.pop(sym, None)
                buy_dates.pop(sym, None)
            trades.append(
                {
                    "trade_date": dt,
                    "symbol": sym,
                    "side": "SELL",
                    "shares": qty,
                    "price": round(fill_px, 4),
                    "notional": round(notional, 2),
                    "fee": round(fee, 2),
                }
            )

        # Execute buys constrained by available cash
        for sym, qty, raw_px in buy_orders:
            fill_px = raw_px * (1.0 + slip)
            max_affordable = int((cash / (fill_px * (1.0 + comm_rate))) // lot) * lot
            exec_qty = min(qty, max_affordable)
            if exec_qty <= 0:
                continue
            notional = exec_qty * fill_px
            fee = max(min_comm, notional * comm_rate)
            if notional + fee > cash:
                exec_qty = max(0, exec_qty - lot)
                if exec_qty <= 0:
                    continue
                notional = exec_qty * fill_px
                fee = max(min_comm, notional * comm_rate)
            cash -= notional + fee
            holdings[sym] = holdings.get(sym, 0) + exec_qty
            buy_dates[sym] = dt
            trades.append(
                {
                    "trade_date": dt,
                    "symbol": sym,
                    "side": "BUY",
                    "shares": exec_qty,
                    "price": round(fill_px, 4),
                    "notional": round(notional, 2),
                    "fee": round(fee, 2),
                }
            )

        return cash, holdings, buy_dates, trades
