"""Feishu custom bot webhook notifier with HMAC-SHA256 signing and clean structured card layout."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any

import requests

from ashare_trader.execution.signal_runner import compute_entry_guidance
from ashare_trader.models import Asset, DataHealthRecord, Position, TradeInstruction

DEFAULT_DASHBOARD_URL = "https://asharerider.streamlit.app/"


class FeishuNotifier:
    """Sends structured, high-readability interactive cards to a Feishu group custom bot webhook."""

    def __init__(
        self,
        webhook_url: str | None = None,
        secret: str | None = None,
        dashboard_url: str | None = None,
    ) -> None:
        self.webhook_url = (webhook_url or os.getenv("FEISHU_WEBHOOK_URL", "")).strip()
        self.secret = (secret if secret is not None else os.getenv("FEISHU_SECRET", "")).strip()
        self.dashboard_url = (dashboard_url or os.getenv("DASHBOARD_URL", DEFAULT_DASHBOARD_URL)).strip()

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
        """Build a clean, structured Feishu interactive card using native column_set and semantic colors."""
        signal_date = instructions[0].signal_date.isoformat() if instructions else "N/A"
        mode_label = "尾盘实时信号 · 14:35" if run_mode == "intraday" else "收盘归档确认 · 16:30"

        has_action = any(ins.action in ("BUY", "SELL") for ins in instructions)
        failed_health = [h for h in (health_records or []) if h.status == "failed"]
        header_color = "red" if failed_health else ("orange" if has_action else "indigo")

        # Compute summary allocation (risky vs safe-haven)
        safe_weight = 0.0
        risky_weight = 0.0
        for ins in instructions:
            asset = asset_map.get(ins.symbol)
            if asset and asset.category == "cash":
                safe_weight += ins.target_weight
            else:
                risky_weight += ins.target_weight

        if health_records:
            ok_cnt = sum(1 for h in health_records if h.status == "ok")
            deg_cnt = sum(1 for h in health_records if h.status == "degraded")
            fail_cnt = len(failed_health)
            if fail_cnt > 0:
                health_val = f"<font color='red'>{fail_cnt} 异常</font>"
            elif deg_cnt > 0:
                health_val = f"<font color='orange'>备源 {deg_cnt}</font>"
            else:
                health_val = f"<font color='green'>正常 ({ok_cnt})</font>"
        else:
            health_val = "<font color='green'>正常</font>"

        elements: list[dict[str, Any]] = [
            # 1. Top 3-column KPI overview bar
            {
                "tag": "column_set",
                "flex_mode": "trisect",
                "background_style": "grey",
                "horizontal_spacing": "default",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "center",
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": f"<font color='grey'>风险资产仓位</font>\n**{risky_weight:.0%}**",
                            }
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "center",
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": f"<font color='grey'>避险底仓</font>\n**{safe_weight:.0%}**",
                            }
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "center",
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": f"<font color='grey'>行情数据</font>\n**{health_val}**",
                            }
                        ],
                    },
                ],
            },
            {"tag": "hr"},
        ]

        # 2. Per-asset structured blocks (risky assets first, safe-haven last)
        sorted_ins = sorted(
            instructions,
            key=lambda x: (
                1 if (asset_map.get(x.symbol) and asset_map[x.symbol].category == "cash") else 0,
                -x.target_weight,
            ),
        )

        if not sorted_ins:
            elements.append({"tag": "markdown", "content": "今日无调仓指令，维持现有组合。"})
        else:
            for idx, ins in enumerate(sorted_ins):
                asset = asset_map.get(ins.symbol)
                name = asset.name if asset else ins.symbol
                is_safe = bool(asset and asset.category == "cash")
                guide = compute_entry_guidance(ins.action, ins.ref_price, ins.stop_loss_price, is_safe_haven=is_safe)

                color = {"BUY": "red", "SELL": "green", "HOLD": "blue"}.get(ins.action, "grey")
                if is_safe and ins.action == "HOLD":
                    color = "grey"

                ref_str = f"{ins.ref_price:.3f}" if ins.ref_price else "—"
                sh_str = f"每10万约 **{ins.suggested_shares:,}** 份" if ins.suggested_shares > 0 else "—"

                # Row A: Asset name + Action badge (left) | Target Weight + per-100k shares (right)
                elements.append(
                    {
                        "tag": "column_set",
                        "flex_mode": "none",
                        "background_style": "default",
                        "columns": [
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 3,
                                "vertical_align": "center",
                                "elements": [
                                    {
                                        "tag": "markdown",
                                        "content": (
                                            f"**{name}** <font color='grey'>{ins.symbol}</font>\n"
                                            f"<font color='{color}'>**[{guide['action_label']}]**</font>"
                                        ),
                                    }
                                ],
                            },
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 2,
                                "vertical_align": "center",
                                "elements": [
                                    {
                                        "tag": "markdown",
                                        "text_align": "right",
                                        "content": f"目标仓位 **{ins.target_weight:.1%}**\n<font color='grey'>{sh_str}</font>",
                                    }
                                ],
                            },
                        ],
                    }
                )

                # Row B: Clean guidance & reference price box
                elements.append(
                    {
                        "tag": "markdown",
                        "content": (
                            f"参考价 `{ref_str}` · {guide['guidance']}\n"
                            f"<font color='grey'>策略理由：{ins.reason or '按模型权重执行'}</font>"
                        ),
                    }
                )

                if idx < len(sorted_ins) - 1:
                    elements.append({"tag": "hr"})

        # 3. Optional personal position stop-loss section (omitted in shared group broadcast when positions={})
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
                stop_lines.append(
                    f"• **{label}** {pos.shares:,}份 | 现价 `{px:.3f}` ({pnl_pct:+.1%}) | "
                    f"止损 `{pos.stop_loss_price:.3f}` (安全垫 `{cushion:+.1%}`)"
                )
            else:
                stop_lines.append(f"• **{label}** {pos.shares:,}份 | 现价 `{px:.3f}` ({pnl_pct:+.1%})")

        if stop_lines:
            elements.append({"tag": "hr"})
            elements.append({"tag": "markdown", "content": "**持仓止损警戒**\n" + "\n".join(stop_lines)})

        # 4. Action button to open Web Terminal + clean footer note
        elements.append({"tag": "hr"})
        if self.dashboard_url:
            elements.append(
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "打开 Web 终端查看个人精确份数 & 登记成交"},
                            "type": "primary",
                            "url": self.dashboard_url,
                        }
                    ],
                }
            )

        elements.append(
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "AShareTrader · TrendATR 跨资产模型 · 群内按10万基准换算，实际请按个人资金执行",
                    }
                ],
            }
        )

        return {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {"tag": "plain_text", "content": "AShareTrader 每日策略信号"},
                    "subtitle": {"tag": "plain_text", "content": f"{signal_date} · {mode_label}"},
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
