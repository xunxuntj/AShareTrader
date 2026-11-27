"""Unified CLI entry point for data sync, backtest, research, and daily cloud runs."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from ashare_trader.config_loader import (
    DEFAULT_CANDIDATE_UNIVERSE_PATH,
    DEFAULT_STRATEGIES_PATH,
    DEFAULT_UNIVERSE_PATH,
    load_backtest_config,
    load_strategies_config,
    load_universe,
)
from ashare_trader.data.calendar import is_cn_trading_day
from ashare_trader.data.manager import DataManager
from ashare_trader.execution.broker import ManualPaperBroker
from ashare_trader.execution.ledger import LedgerService
from ashare_trader.execution.signal_runner import build_default_strategies, run_daily_signal
from ashare_trader.notify.feishu import FeishuNotifier
from ashare_trader.research.validator import run_strategy_benchmark
from ashare_trader.storage.factory import create_storage


def cmd_sync_data(args: argparse.Namespace) -> None:
    universe_path = DEFAULT_CANDIDATE_UNIVERSE_PATH if args.candidate else args.universe
    universe = load_universe(universe_path)
    storage = create_storage(force_local=args.local)
    dm = DataManager(storage=storage)
    print(f"[sync-data] Backend={storage.backend_name} | Assets={len(universe.all_assets)}")
    records = dm.sync_universe(
        universe=universe,
        start_date=date.fromisoformat(args.start),
        include_intraday=args.intraday,
        force_full=args.full,
    )
    ok = sum(1 for r in records if r.status == "ok")
    deg = sum(1 for r in records if r.status == "degraded")
    fail = sum(1 for r in records if r.status == "failed")
    print(f"[sync-data] Done: ok={ok}, degraded={deg}, failed={fail}")
    for r in records:
        if r.status != "ok":
            print(f"  - {r.symbol}: {r.status} ({r.source_used}) {r.error_msg}")


def cmd_backtest(args: argparse.Namespace) -> None:
    universe = load_universe(args.universe)
    strat_cfg = load_strategies_config(args.strategies)
    bt_cfg = load_backtest_config(args.strategies)
    storage = create_storage(force_local=args.local)
    dm = DataManager(storage=storage)

    all_syms = [a.symbol for a in universe.all_assets]
    close_df = dm.load_price_panel(all_syms, field="close")
    high_df = dm.load_price_panel(all_syms, field="high")
    low_df = dm.load_price_panel(all_syms, field="low")
    open_df = dm.load_price_panel(all_syms, field="open")

    strategies, combined = build_default_strategies(universe.safe_haven.symbol, strat_cfg)
    _, summary = run_strategy_benchmark(
        strategies=[*strategies, combined],
        close_df=close_df,
        high_df=high_df,
        low_df=low_df,
        open_df=open_df,
        backtest_config=bt_cfg,
        start_date=args.start,
        end_date=args.end,
        benchmark_symbol=universe.benchmark.symbol,
        risk_config=strat_cfg.get("risk", {}),
        use_risk_overlay=args.risk_overlay,
    )
    cols = ["strategy", "cagr", "max_drawdown", "calmar", "sharpe", "sortino", "ann_turnover", "benchmark_cagr"]
    present = [c for c in cols if c in summary.columns]
    print(summary[present].to_string(index=False))


def cmd_daily_run(args: argparse.Namespace) -> None:
    if not args.force and not is_cn_trading_day():
        print("[daily-run] Today is not an A-Share trading day. Exiting cleanly.")
        return

    universe = load_universe(args.universe)
    strat_cfg = load_strategies_config(args.strategies)
    bt_cfg = load_backtest_config(args.strategies)
    storage = create_storage(force_local=args.local)
    dm = DataManager(storage=storage)

    health = dm.sync_universe(
        universe=universe,
        include_intraday=(args.mode == "intraday"),
    )

    instructions, latest_prices = run_daily_signal(
        storage=storage,
        universe=universe,
        strat_cfg=strat_cfg,
        run_mode=args.mode,
        lot_size=bt_cfg.lot_size,
        rebalance_threshold=bt_cfg.rebalance_threshold,
        persist=not args.dry_run,
    )

    broker = ManualPaperBroker()
    for line in broker.submit_instructions(instructions):
        print(line)

    # Save latest backtest snapshot on EOD run
    if args.mode == "eod" and not args.dry_run:
        all_syms = [a.symbol for a in universe.all_assets]
        close_df = dm.load_price_panel(all_syms, field="close")
        high_df = dm.load_price_panel(all_syms, field="high")
        low_df = dm.load_price_panel(all_syms, field="low")
        open_df = dm.load_price_panel(all_syms, field="open")
        strategies, combined = build_default_strategies(universe.safe_haven.symbol, strat_cfg)
        results, _ = run_strategy_benchmark(
            strategies=[*strategies, combined],
            close_df=close_df,
            high_df=high_df,
            low_df=low_df,
            open_df=open_df,
            backtest_config=bt_cfg,
            start_date="2019-01-01",
            benchmark_symbol=universe.benchmark.symbol,
        )
        today = date.today()
        for name, res in results.items():
            eq_points = [
                {"date": dt.strftime("%Y-%m-%d"), "equity": round(float(val), 2)}
                for dt, val in res.equity_curve.items()
            ]
            storage.save_backtest_run(
                run_date=today,
                strategy_name=name,
                params=strat_cfg.get("strategies", {}).get(name, {}),
                metrics=res.metrics,
                equity_curve=eq_points,
            )

    # Feishu notification policy: always notify on intraday; notify on EOD only if forced or errors exist
    notifier = FeishuNotifier()
    has_failure = any(h.status == "failed" for h in health)
    should_notify = args.send_feishu and (args.mode == "intraday" or has_failure or args.force_notify)
    if should_notify and notifier.is_configured and not args.dry_run:
        positions = LedgerService(storage).get_positions()
        card = notifier.build_signal_card(
            instructions=instructions,
            positions=positions,
            latest_prices=latest_prices,
            asset_map=universe.asset_map,
            health_records=health,
            run_mode=args.mode,
        )
        notifier.send_card(card)
        print("[daily-run] Feishu card sent successfully.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ashare-trader", description="AShareTrader CLI")
    parser.add_argument("--universe", type=Path, default=DEFAULT_UNIVERSE_PATH)
    parser.add_argument("--strategies", type=Path, default=DEFAULT_STRATEGIES_PATH)
    parser.add_argument("--local", action="store_true", help="Force local SQLite+Parquet storage")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sync = sub.add_parser("sync-data", help="Sync historical (and optional intraday) bars")
    p_sync.add_argument("--candidate", action="store_true", help="Sync 25-ETF candidate research universe")
    p_sync.add_argument("--start", default="2016-01-01")
    p_sync.add_argument("--intraday", action="store_true")
    p_sync.add_argument("--full", action="store_true")
    p_sync.set_defaults(func=cmd_sync_data)

    p_bt = sub.add_parser("backtest", help="Run multi-strategy backtest comparison")
    p_bt.add_argument("--start", default="2018-01-01")
    p_bt.add_argument("--end", default=None)
    p_bt.add_argument("--risk-overlay", action="store_true")
    p_bt.set_defaults(func=cmd_backtest)

    p_daily = sub.add_parser("daily-run", help="Run scheduled data sync, signal generation, and Feishu alert")
    p_daily.add_argument("--mode", choices=["intraday", "eod"], default="intraday")
    p_daily.add_argument("--send-feishu", action="store_true")
    p_daily.add_argument("--force-notify", action="store_true")
    p_daily.add_argument("--force", action="store_true", help="Bypass trading calendar check")
    p_daily.add_argument("--dry-run", action="store_true")
    p_daily.set_defaults(func=cmd_daily_run)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
