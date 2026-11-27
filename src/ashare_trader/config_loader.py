"""YAML configuration loader for universe and strategy settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ashare_trader.models import Asset, BacktestConfig, UniverseConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UNIVERSE_PATH = PROJECT_ROOT / "config" / "universe.yaml"
DEFAULT_CANDIDATE_UNIVERSE_PATH = PROJECT_ROOT / "config" / "candidate_universe.yaml"
DEFAULT_STRATEGIES_PATH = PROJECT_ROOT / "config" / "strategies.yaml"


def _parse_asset(raw: dict[str, Any]) -> Asset:
    return Asset(
        symbol=str(raw["symbol"]).zfill(6),
        name=str(raw["name"]),
        exchange=raw["exchange"],
        category=str(raw.get("category", "unknown")),
        asset_type=raw.get("asset_type", "etf"),
    )


def load_universe(path: str | Path | None = None) -> UniverseConfig:
    """Load an ETF/asset universe YAML file."""
    target = Path(path) if path is not None else DEFAULT_UNIVERSE_PATH
    with target.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    assets = [_parse_asset(item) for item in raw["assets"]]
    safe_haven = _parse_asset(raw["safe_haven"])
    benchmark = _parse_asset(raw["benchmark"])
    return UniverseConfig(assets=assets, safe_haven=safe_haven, benchmark=benchmark)


def load_strategies_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load strategy parameters, risk controls, combiner weights, and backtest config."""
    target = Path(path) if path is not None else DEFAULT_STRATEGIES_PATH
    with target.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_backtest_config(path: str | Path | None = None) -> BacktestConfig:
    """Extract BacktestConfig dataclass from strategies.yaml."""
    raw = load_strategies_config(path).get("backtest", {})
    return BacktestConfig(
        initial_capital=float(raw.get("initial_capital", 100000.0)),
        commission_rate=float(raw.get("commission_rate", 0.0001)),
        min_commission=float(raw.get("min_commission", 0.0)),
        stamp_duty_rate=float(raw.get("stamp_duty_rate", 0.0)),
        slippage_rate=float(raw.get("slippage_rate", 0.0005)),
        lot_size=int(raw.get("lot_size", 100)),
        execution_timing=raw.get("execution_timing", "close"),
        limit_move_threshold=float(raw.get("limit_move_threshold", 0.098)),
        rebalance_threshold=float(raw.get("rebalance_threshold", 0.03)),
    )
