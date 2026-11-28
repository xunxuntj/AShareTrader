"""Feishu custom bot webhook notifier with optional HMAC-SHA256 signing and interactive card formatting."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any

import requests

from ashare_trader.models import Asset, DataHealthRecord, Position, TradeInstruction


class FeishuNotifier:
    """Sends rich interactive cards to a Feishu group custom bot webhook."""

    def __init__(self, webhook_url: str | None = None, secret: str | None = None) -> None:
        self.webhook_url = (webhook_url or os.getenv("FEISHU_WEBHOOK_URL", "")).strip()
        self.secret = (secret if secret is not None else os.getenv("FEISHU_SECRET", "")).strip()

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url)

    @staticmethod
    def generate_sign(timestamp: str, secret: str) -> str:
        """Compute Feishu webhook HMAC-SHA256 base64 signature."""
        string_to_sign = f"{timestamp}\n{secret}"
        hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        return base64.b64encode(hmac_code).decode("utf-8")

    def build_signal_card(
        self,
        instructions: list[TradeInstruction],
        positions: dict[str, Position],
        latest_prices: dict[str, float],
        asset_map: dict[str, Asset],
        health_records: list[DataHealthRecord] | None = None,
        run_mode: str = "intraday",
    ) -> dict[str, Any]:
        """Build Feishu interactive card JSON payload."""
        signal_date = instructions[0].signal_date.isoformat() if instructions else "N/A"
        mode_label = "尾盘实时信号 (14:35)" if run_mode == "intraday" else "收盘归档确认 (16:30)"

        has_action = any(ins.action in ("BUY", "SELL") for ins in instructions)
        failed_health = [h for h in (health_records or []) if h.status == "failed"]
        header_color = "red" if failed_health else ("orange" if has_action else "blue")

        # Section 1: Trading Instructions (shared model baseline, per-100k reference shares)
        action_lines: list[str] = []
        for ins in instructions:
            asset = asset_map.get(ins.symbol)
            label = f"{asset.name}({ins.symbol})" if asset else ins.symbol
            badge = {"BUY": "🟢 买入/加仓", "SELL": "🔴 卖出/减仓", "HOLD": "⚪ 持有"}.get(ins.action, ins.action)
            px_txt = f"参考价 {ins.ref_price:.3f}" if ins.ref_price else ""
            stop_txt = f" | 止损线 {ins.stop_loss_price:.3f}" if ins.stop_loss_price else ""
            sh_txt = f" | 每10万参考 {ins.suggested_shares} 份" if ins.suggested_shares > 0 else ""
            action_lines.append(
                f"- **{badge}** {label} → 目标仓位 `{ins.target_weight:.1%}` {px_txt}{sh_txt}{stop_txt}\n"
                f"  理由：{ins.reason or '按模型权重执行'}"
            )
        action_md = "\n".join(action_lines) if action_lines else "今日无调仓指令，维持现有组合。"
        action_md += "\n\n*提示：以上为公共策略理论信号（按10万基准换算参考份数），请登录 Web 终端查看本人账户精确买卖份数并登记成交。*"

        # Section 2: Current Position Stop-Loss Monitor
        stop_lines: list[str] = []
        for sym, pos in positions.items():
            if pos.shares <= 0:
                continue
            asset = asset_map.get(sym)
            label = f"{asset.name}({sym})" if asset else sym
            px = latest_prices.get(sym, pos.avg_cost)
            pnl_pct = (px / pos.avg_cost - 1.0) if pos.avg_cost > 0 else 0.0
            if pos.stop_loss_price and px > 0:
                cushion = px / pos.stop_loss_price - 1.0
                warn = " ⚠️接近止损" if cushion < 0.02 else ""
                stop_lines.append(
                    f"- {label}: 持仓 {pos.shares} 份 | 现价 `{px:.3f}` ({pnl_pct:+.1%}) | "
                    f"止损 `{pos.stop_loss_price:.3f}` (安全垫 `{cushion:+.1%}`){warn}"
                )
            else:
                stop_lines.append(f"- {label}: 持仓 {pos.shares} 份 | 现价 `{px:.3f}` ({pnl_pct:+.1%})")
        stop_md = "\n".join(stop_lines) if stop_lines else "当前账本暂无实盘持仓记录。"

        # Section 3: Data Health Summary
        if health_records:
            ok_cnt = sum(1 for h in health_records if h.status == "ok")
            deg_cnt = sum(1 for h in health_records if h.status == "degraded")
            fail_cnt = len(failed_health)
            health_md = f"数据同步：✅ 正常 {ok_cnt} | 🟡 降级备源 {deg_cnt} | ❌ 失败 {fail_cnt}"
            if failed_health:
                bad_syms = ", ".join(h.symbol for h in failed_health[:5])
                health_md += f"\n异常标的：{bad_syms}"
        else:
            health_md = "数据源健康检查：未附带本次同步明细"

        elements: list[dict[str, Any]] = [
            {"tag": "markdown", "content": f"**📌 今日交易信号**\n{action_md}"},
        ]
        if stop_lines:
            elements.append({"tag": "hr"})
            elements.append({"tag": "markdown", "content": f"**🛡️ 持仓与止损警戒线**\n{stop_md}"})
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown", "content": f"**📡 系统状态**\n{health_md}"})

        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"AShareTrader | {signal_date} {mode_label}"},
                    "template": header_color,
                },
                "elements": elements,
            },
        }

    def send_card(self, card_payload: dict[str, Any], timeout: int = 10) -> dict[str, Any]:
        """POST interactive card payload to Feishu webhook."""
        if not self.is_configured:
            raise ValueError("FEISHU_WEBHOOK_URL is not configured.")
        body = dict(card_payload)
        if self.secret:
            ts = str(int(time.time()))
            body["timestamp"] = ts
            body["sign"] = self.generate_sign(ts, self.secret)
        resp = requests.post(self.webhook_url, json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code", 0) != 0 and data.get("StatusCode", 0) != 0:
            raise RuntimeError(f"Feishu webhook error: {data}")
        return data
