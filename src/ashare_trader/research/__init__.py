"""Quantitative research utilities: universe screening and out-of-sample validation."""

from ashare_trader.research.universe_selector import (
    compute_asset_profiles,
    select_low_correlation_universe,
)
from ashare_trader.research.validator import (
    evaluate_is_oos,
    parameter_plateau_scan,
    run_strategy_benchmark,
)

__all__ = [
    "compute_asset_profiles",
    "select_low_correlation_universe",
    "evaluate_is_oos",
    "parameter_plateau_scan",
    "run_strategy_benchmark",
]
