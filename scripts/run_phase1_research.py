"""Phase 1 Empirical Research Script: real ETF data sync, universe screening, IS/OOS & plateau scan."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd

from ashare_trader.config_loader import (
    DEFAULT_CANDIDATE_UNIVERSE_PATH,
    DEFAULT_STRATEGIES_PATH,
    DEFAULT_UNIVERSE_PATH,
    load_backtest_config,
    load_strategies_config,
    load_universe,
)
from ashare_trader.data.manager import DataManager
from ashare_trader.execution.signal_runner import build_default_strategies
from ashare_trader.research.universe_selector import (
    compute_asset_profiles,
    compute_correlation_matrix,
    select_low_correlation_universe,
)
from ashare_trader.research.validator import (
    evaluate_is_oos,
    parameter_plateau_scan,
    run_strategy_benchmark,
)
from ashare_trader.storage.local_store import LocalStorage
from ashare_trader.strategies.momentum_r2 import MomentumR2Strategy
from ashare_trader.strategies.trend_atr import TrendATRStrategy


def main() -> None:
    out_dir = Path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)

    candidate = load_universe(DEFAULT_CANDIDATE_UNIVERSE_PATH)
    seed_universe = load_universe(DEFAULT_UNIVERSE_PATH)
    strat_cfg = load_strategies_config(DEFAULT_STRATEGIES_PATH)
    bt_cfg = load_backtest_config(DEFAULT_STRATEGIES_PATH)

    storage = LocalStorage(data_dir="data")
    dm = DataManager(storage=storage)

    print("=== Step 1: Syncing 26 candidate ETFs + safe haven (2016-01-01 to today) ===")
    health = dm.sync_universe(candidate, start_date=date(2016, 1, 1), include_intraday=False)
    ok_cnt = sum(1 for h in health if h.status == "ok")
    deg_cnt = sum(1 for h in health if h.status == "degraded")
    fail_cnt = sum(1 for h in health if h.status == "failed")
    print(f"Sync summary: ok={ok_cnt}, degraded={deg_cnt}, failed={fail_cnt}")
    for h in health:
        if h.status != "ok":
            print(f"  [{h.status}] {h.symbol} ({h.source_used}): {h.error_msg}")

    all_syms = [a.symbol for a in candidate.all_assets]
    bars_map = storage.load_bars(all_syms, start_date="2016-01-01")
    close_df = dm.load_price_panel(all_syms, field="close", start_date="2016-01-01")
    high_df = dm.load_price_panel(all_syms, field="high", start_date="2016-01-01")
    low_df = dm.load_price_panel(all_syms, field="low", start_date="2016-01-01")
    open_df = dm.load_price_panel(all_syms, field="open", start_date="2016-01-01")

    print(f"Loaded price panel: {close_df.shape[0]} dates x {close_df.shape[1]} symbols "
          f"({close_df.index.min().date()} -> {close_df.index.max().date()})")

    # Step 2: Candidate Asset Profiles & Low-Correlation Selection
    print("\n=== Step 2: Asset Profiling & Low-Correlation Screening ===")
    profiles = compute_asset_profiles(bars_map, candidate)
    profiles.to_csv(out_dir / "asset_profiles.csv", index=False)
    print(profiles.sort_values("sharpe", ascending=False).to_string(index=False))

    risky_syms = [s for s in candidate.risky_symbols if s in close_df.columns]
    corr = compute_correlation_matrix(close_df, risky_syms)
    corr.to_csv(out_dir / "correlation_matrix.csv")

    auto_selected, selection_log = select_low_correlation_universe(
        profiles=profiles,
        corr_matrix=corr,
        min_years=5.0,
        min_median_amount_m=30.0,
        max_pair_corr=0.68,
        max_assets=8,
    )
    selection_log.to_csv(out_dir / "universe_selection_log.csv", index=False)
    print(f"\nData-driven low-corr pool ({len(auto_selected)}): {auto_selected}")

    # Define 3 competing universes for apples-to-apples empirical test
    safe_sym = candidate.safe_haven.symbol
    bm_sym = candidate.benchmark.symbol
    pools = {
        "Pool_1_DataDriven_LowCorr": [s for s in auto_selected if s in close_df.columns] + [safe_sym],
        "Pool_2_Seed_CrossAsset_8": [a.symbol for a in seed_universe.assets if a.symbol in close_df.columns] + [safe_sym],
        "Pool_3_Core5_Minimal": [
            s for s in ["510300", "159915", "510880", "513100", "518880", "511010"] if s in close_df.columns
        ] + [safe_sym],
    }

    # Step 3: Multi-Pool & Multi-Strategy Full Period + IS/OOS Comparison
    print("\n=== Step 3: Full-Period (2018-至今) & IS/OOS Comparison ===")
    summary_payload: dict[str, object] = {
        "data_range": [str(close_df.index.min().date()), str(close_df.index.max().date())],
        "sync_health": {"ok": ok_cnt, "degraded": deg_cnt, "failed": fail_cnt},
        "pools": pools,
        "full_results": {},
        "is_oos_results": {},
        "yearly_results": {},
    }

    for pool_name, syms in pools.items():
        syms_unique = list(dict.fromkeys(syms))
        c_sub = close_df[syms_unique]
        h_sub = high_df[syms_unique]
        l_sub = low_df[syms_unique]
        o_sub = open_df[syms_unique]

        def _factory():
            strats, comb = build_default_strategies(safe_sym, strat_cfg)
            return [*strats, comb]

        res_map, full_df = run_strategy_benchmark(
            strategies=_factory(),
            close_df=c_sub,
            high_df=h_sub,
            low_df=l_sub,
            open_df=o_sub,
            backtest_config=bt_cfg,
            start_date="2018-01-01",
            benchmark_symbol=bm_sym if bm_sym in c_sub.columns else None,
        )
        full_df.to_csv(out_dir / f"full_{pool_name}.csv", index=False)
        summary_payload["full_results"][pool_name] = full_df.to_dict(orient="records")

        is_oos_df = evaluate_is_oos(
            strategy_factory=_factory,
            close_df=c_sub,
            high_df=h_sub,
            low_df=l_sub,
            open_df=o_sub,
            backtest_config=bt_cfg,
            is_start="2018-01-01",
            is_end="2022-12-31",
            oos_start="2023-01-01",
            benchmark_symbol=bm_sym if bm_sym in c_sub.columns else None,
        )
        is_oos_df.to_csv(out_dir / f"is_oos_{pool_name}.csv", index=False)
        summary_payload["is_oos_results"][pool_name] = is_oos_df.to_dict(orient="records")

        # Save yearly attribution for combined and momentum_r2
        summary_payload["yearly_results"][pool_name] = {
            name: res.yearly_metrics.to_dict(orient="records") for name, res in res_map.items()
        }

        print(f"\n--- {pool_name} (2018-01-01 to latest) ---")
        cols = ["strategy", "cagr", "max_drawdown", "calmar", "sharpe", "ann_turnover"]
        print(full_df[cols].to_string(index=False))
        print(f"--- {pool_name} IS (2018-2022) vs OOS (2023-latest) ---")
        print(is_oos_df.to_string(index=False))

    # Step 4: Parameter Plateau Scan on Pool_1 & Pool_2
    print("\n=== Step 4: Parameter Plateau Scan (Overfitting Check) ===")
    target_syms = list(dict.fromkeys(pools["Pool_1_DataDriven_LowCorr"]))
    mom_plateau = parameter_plateau_scan(
        strategy_builder=lambda p: MomentumR2Strategy(safe_sym, p),
        param_grid={"lookback": [15, 20, 25, 30, 40], "top_k": [1, 2, 3]},
        close_df=close_df[target_syms],
        high_df=high_df[target_syms],
        low_df=low_df[target_syms],
        open_df=open_df[target_syms],
        backtest_config=bt_cfg,
        start_date="2018-01-01",
    )
    mom_plateau.to_csv(out_dir / "plateau_momentum_r2.csv", index=False)
    print("\nMomentum R2 Plateau (Pool_1):")
    print(mom_plateau.to_string(index=False))

    trend_plateau = parameter_plateau_scan(
        strategy_builder=lambda p: TrendATRStrategy(safe_sym, p),
        param_grid={"breakout_window": [15, 20, 30], "atr_stop_mult": [2.0, 2.5, 3.0]},
        close_df=close_df[target_syms],
        high_df=high_df[target_syms],
        low_df=low_df[target_syms],
        open_df=open_df[target_syms],
        backtest_config=bt_cfg,
        start_date="2018-01-01",
    )
    trend_plateau.to_csv(out_dir / "plateau_trend_atr.csv", index=False)
    print("\nTrend ATR Plateau (Pool_1):")
    print(trend_plateau.to_string(index=False))

    summary_payload["plateau_momentum_r2"] = mom_plateau.to_dict(orient="records")
    summary_payload["plateau_trend_atr"] = trend_plateau.to_dict(orient="records")

    with (out_dir / "phase1_summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary_payload, fh, ensure_ascii=False, indent=2, default=str)

    print("\nPhase 1 research artifacts saved to reports/.")


if __name__ == "__main__":
    main()
