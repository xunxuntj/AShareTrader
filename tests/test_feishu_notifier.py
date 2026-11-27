"""Tests for Feishu webhook signing and interactive card payload generation."""

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from ashare_trader.models import Asset, DataHealthRecord, Position, TradeInstruction
from ashare_trader.notify.feishu import FeishuNotifier


def test_feishu_hmac_signature_deterministic():
    sig1 = FeishuNotifier.generate_sign("1700000000", "test_secret")
    sig2 = FeishuNotifier.generate_sign("1700000000", "test_secret")
    assert sig1 == sig2
    assert len(sig1) > 20


def test_feishu_card_structure_and_send():
    notifier = FeishuNotifier(webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/mock", secret="sec")
    instructions = [
        TradeInstruction(
            signal_date=date(2024, 6, 1),
            run_mode="intraday",
            strategy_name="combined",
            symbol="510300",
            action="BUY",
            target_weight=0.5,
            ref_price=3.6,
            stop_loss_price=3.35,
            suggested_shares=13800,
            reason="R2动量领跑",
        )
    ]
    positions = {"518880": Position("518880", 5000, 5.2, 5.5, 5.3)}
    assets = {
        "510300": Asset("510300", "沪深300ETF", "sh", "a_broad"),
        "518880": Asset("518880", "黄金ETF", "sh", "commodity"),
    }
    health = [
        DataHealthRecord(datetime.now(timezone.utc), "510300", date(2024, 6, 1), "akshare", "ok", 80),
    ]
    card = notifier.build_signal_card(instructions, positions, {"518880": 5.45}, assets, health, "intraday")
    assert card["msg_type"] == "interactive"
    assert card["card"]["header"]["template"] == "orange"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": 0, "msg": "success"}
    mock_resp.raise_for_status.return_value = None
    with patch("ashare_trader.notify.feishu.requests.post", return_value=mock_resp) as mock_post:
        res = notifier.send_card(card)
        assert res["code"] == 0
        sent_json = mock_post.call_args.kwargs["json"]
        assert "timestamp" in sent_json and "sign" in sent_json
