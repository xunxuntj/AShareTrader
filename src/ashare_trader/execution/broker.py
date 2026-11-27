"""Broker abstraction (ManualPaperBroker for Phase 1, QMTBroker extension point)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ashare_trader.models import TradeInstruction


class Broker(ABC):
    """Abstract execution broker interface."""

    @abstractmethod
    def submit_instructions(self, instructions: list[TradeInstruction]) -> list[str]:
        """Submit or record trade instructions. Returns human-readable execution summaries."""


class ManualPaperBroker(Broker):
    """Formats actionable manual order instructions for user execution in broker app."""

    def submit_instructions(self, instructions: list[TradeInstruction]) -> list[str]:
        lines: list[str] = []
        for ins in instructions:
            if ins.action == "HOLD":
                lines.append(f"[HOLD] {ins.symbol} 目标仓位 {ins.target_weight:.1%}")
            else:
                lines.append(
                    f"[{ins.action}] {ins.symbol} 建议 {ins.suggested_shares} 份 "
                    f"(目标仓位 {ins.target_weight:.1%}, 参考价 {ins.ref_price or 0:.3f}, "
                    f"止损 {ins.stop_loss_price or 0:.3f}) - {ins.reason}"
                )
        return lines
