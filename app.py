"""AShareTrader Cloud Dashboard (Streamlit Community Cloud + Supabase)."""

from __future__ import annotations

import os
from datetime import date

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ashare_trader.config_loader import (
    load_backtest_config,
    load_strategies_config,
    load_universe,
)
from ashare_trader.data.manager import DataManager
from ashare_trader.engine.metrics import compute_monthly_returns
from ashare_trader.execution.ledger import LedgerService
from ashare_trader.execution.signal_runner import build_default_strategies, run_daily_signal
from ashare_trader.notify.feishu import FeishuNotifier
from ashare_trader.research.validator import run_strategy_benchmark
from ashare_trader.storage.factory import create_storage

st.set_page_config(page_title="AShareTrader 量化看板", page_icon="📈", layout="wide")


def _inject_secrets_to_env() -> None:
    """Propagate Streamlit Cloud secrets into process env so factory & notifier pick them up."""
    for key in ["SUPABASE_URL", "SUPABASE_KEY", "FEISHU_WEBHOOK_URL", "FEISHU_SECRET", "APP_PASSWORD"]:
        if key not in os.environ and key in st.secrets:
            os.environ[key] = str(st.secrets[key])


def _check_write_auth() -> bool:
    app_pwd = os.getenv("APP_PASSWORD", "").strip()
    if not app_pwd:
        return True
    if st.session_state.get("write_unlocked", False):
        return True
    with st.sidebar.expander("🔒 写操作解锁（录成交/发飞书）"):
        pwd = st.text_input("管理口令", type="password", key="pwd_input")
        if st.button("解锁"):
            if pwd == app_pwd:
                st.session_state["write_unlocked"] = True
                st.success("已解锁写操作")
                st.rerun()
            else:
                st.error("口令错误")
    return False


_inject_secrets_to_env()
storage = create_storage()
universe = load_universe()
strat_cfg = load_strategies_config()
bt_cfg = load_backtest_config()
dm = DataManager(storage=storage)
ledger = LedgerService(storage=storage)

st.sidebar.title("📈 AShareTrader")
st.sidebar.caption(f"存储引擎：`{storage.backend_name}`")
page = st.sidebar.radio(
    "功能导航",
    ["📌 今日信号与风控", "📊 多策略回测对比", "💼 持仓与资金账本", "📡 数据源健康监控"],
)
can_write = _check_write_auth()

all_syms = [a.symbol for a in universe.all_assets]
asset_map = universe.asset_map


def _fmt_symbol(sym: str) -> str:
    a = asset_map.get(sym)
    return f"{a.name} ({sym})" if a else sym


# ==============================================================================
# Page 1: Today's Signal & Risk Monitor
# ==============================================================================
if page == "📌 今日信号与风控":
    st.title("📌 今日交易信号与止损警戒")

    col_a, col_b = st.columns([3, 1])
    with col_b:
        if st.button("🔄 立即重算最新信号", use_container_width=True):
            try:
                instructions, latest_prices = run_daily_signal(
                    storage=storage,
                    universe=universe,
                    strat_cfg=strat_cfg,
                    run_mode="intraday",
                    persist=can_write,
                )
                st.session_state["live_instructions"] = instructions
                st.session_state["live_prices"] = latest_prices
                st.success(f"信号计算完成（{len(instructions)} 条）")
            except Exception as exc:  # noqa: BLE001
                st.error(f"计算失败：{exc}")

    df_sig = storage.load_latest_signals(limit=30)
    if df_sig.empty:
        st.info("暂无已落库的信号记录。请先执行 `sync-data` 与 `daily-run`，或点击右上角“立即重算最新信号”。")
    else:
        latest_date = df_sig["signal_date"].iloc[0]
        latest_df = df_sig[df_sig["signal_date"] == latest_date].copy()
        latest_df["标的"] = latest_df["symbol"].map(_fmt_symbol)
        latest_df["目标仓位"] = (latest_df["target_weight"] * 100).map(lambda x: f"{x:.1f}%")
        st.subheader(f"最新信号日期：{latest_date}（模式：`{latest_df['run_mode'].iloc[0]}`）")
        st.dataframe(
            latest_df[["标的", "action", "目标仓位", "ref_price", "stop_loss_price", "reason"]],
            use_container_width=True,
            hide_index=True,
        )

    st.divider()
    st.subheader("🛡️ 当前持仓动态止损安全垫")
    positions = ledger.get_positions()
    close_df = dm.load_price_panel(all_syms, field="close")
    latest_prices = {
        sym: float(close_df[sym].dropna().iloc[-1])
        for sym in close_df.columns
        if not close_df[sym].dropna().empty
    }

    if not positions:
        st.info("当前账本无持仓记录，请在「💼 持仓与资金账本」页面录入实盘成交。")
    else:
        rows = []
        for sym, pos in positions.items():
            px = latest_prices.get(sym, pos.avg_cost)
            pnl_pct = (px / pos.avg_cost - 1.0) if pos.avg_cost > 0 else 0.0
            cushion = (px / pos.stop_loss_price - 1.0) if (pos.stop_loss_price and pos.stop_loss_price > 0) else None
            rows.append(
                {
                    "标的": _fmt_symbol(sym),
                    "持仓份额": pos.shares,
                    "成本价": round(pos.avg_cost, 4),
                    "最新价": round(px, 4),
                    "浮盈亏%": f"{pnl_pct:+.2%}",
                    "持仓最高价": round(pos.highest_price, 4),
                    "动态止损价": round(pos.stop_loss_price, 4) if pos.stop_loss_price else "-",
                    "距止损安全垫": f"{cushion:+.2%}" if cushion is not None else "-",
                }
            )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    notifier = FeishuNotifier()
    if can_write and notifier.is_configured:
        if st.button("📨 发送测试飞书信号卡片"):
            try:
                ins, lp = run_daily_signal(storage, universe, strat_cfg, run_mode="intraday", persist=False)
                card = notifier.build_signal_card(ins, ledger.get_positions(), lp, asset_map, None, "intraday")
                notifier.send_card(card)
                st.success("飞书测试卡片发送成功！")
            except Exception as exc:  # noqa: BLE001
                st.error(f"发送失败：{exc}")


# ==============================================================================
# Page 2: Multi-Strategy Backtest Comparison
# ==============================================================================
elif page == "📊 多策略回测对比":
    st.title("📊 多策略同口径回测对比")
    close_df = dm.load_price_panel(all_syms, field="close")
    high_df = dm.load_price_panel(all_syms, field="high")
    low_df = dm.load_price_panel(all_syms, field="low")
    open_df = dm.load_price_panel(all_syms, field="open")

    if close_df.empty:
        st.warning("存储中暂无行情数据，请先同步历史行情。")
    else:
        c1, c2, c3, c4 = st.columns(4)
        start_dt = c1.date_input("回测起始日", value=date(2019, 1, 1))
        def_w = strat_cfg.get("combiner", {}).get("weights", {})
        w_mom = c2.slider("R²动量权重", 0.0, 1.0, float(def_w.get("momentum_r2", 0.0)), 0.05)
        w_trd = c3.slider("趋势ATR权重", 0.0, 1.0, float(def_w.get("trend_atr", 1.0)), 0.05)
        w_rp = c4.slider("风险平价权重", 0.0, 1.0, float(def_w.get("risk_parity", 0.0)), 0.05)

        if st.button("🚀 运行同口径回测", type="primary"):
            cfg_copy = {**strat_cfg, "combiner": {"weights": {"momentum_r2": w_mom, "trend_atr": w_trd, "risk_parity": w_rp}}}
            strategies, combined = build_default_strategies(universe.safe_haven.symbol, cfg_copy)
            with st.spinner("正在执行无未来函数逐日回测..."):
                results, summary_df = run_strategy_benchmark(
                    strategies=[*strategies, combined],
                    close_df=close_df,
                    high_df=high_df,
                    low_df=low_df,
                    open_df=open_df,
                    backtest_config=bt_cfg,
                    start_date=start_dt.isoformat(),
                    benchmark_symbol=universe.benchmark.symbol,
                )

            st.subheader("绩效指标对比")
            show_cols = ["strategy", "cagr", "max_drawdown", "calmar", "sharpe", "sortino", "ann_turnover", "total_fees"]
            st.dataframe(summary_df[[c for c in show_cols if c in summary_df.columns]], use_container_width=True, hide_index=True)

            # Equity curve chart
            fig_eq = go.Figure()
            for name, res in results.items():
                norm_eq = res.equity_curve / res.equity_curve.iloc[0]
                fig_eq.add_trace(go.Scatter(x=norm_eq.index, y=norm_eq.values, mode="lines", name=name))
            bm = next(iter(results.values())).benchmark_curve
            if bm is not None:
                norm_bm = bm / bm.iloc[0]
                fig_eq.add_trace(
                    go.Scatter(x=norm_bm.index, y=norm_bm.values, mode="lines", name="沪深300基准", line={"dash": "dot"})
                )
            fig_eq.update_layout(title="归一化净值曲线对比", yaxis_title="净值", hovermode="x unified")
            st.plotly_chart(fig_eq, use_container_width=True)

            # Underwater drawdown chart
            fig_dd = go.Figure()
            for name, res in results.items():
                dd = res.equity_curve / res.equity_curve.cummax() - 1.0
                fig_dd.add_trace(go.Scatter(x=dd.index, y=dd.values * 100, mode="lines", name=name))
            fig_dd.update_layout(title="动态回撤曲线 (%)", yaxis_title="回撤 %", hovermode="x unified")
            st.plotly_chart(fig_dd, use_container_width=True)

            # Monthly heatmap for combined strategy
            comb_res = results.get("combined")
            if comb_res is not None:
                monthly = compute_monthly_returns(comb_res.equity_curve)
                if not monthly.empty:
                    fig_hm = px.imshow(
                        monthly * 100,
                        text_auto=".1f",
                        color_continuous_scale="RdYlGn",
                        color_continuous_midpoint=0,
                        title="组合策略月度收益率热力图 (%)",
                    )
                    st.plotly_chart(fig_hm, use_container_width=True)


# ==============================================================================
# Page 3: Ledger & Position Management
# ==============================================================================
elif page == "💼 持仓与资金账本":
    st.title("💼 实盘 / 模拟持仓与资金账本")
    account = ledger.get_account()
    positions = ledger.get_positions()
    close_df = dm.load_price_panel(all_syms, field="close")
    latest_prices = {
        sym: float(close_df[sym].dropna().iloc[-1])
        for sym in close_df.columns
        if not close_df[sym].dropna().empty
    }
    total_eq = ledger.total_equity(latest_prices)
    pnl = total_eq - account.initial_capital

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("账户总资产", f"¥{total_eq:,.2f}")
    m2.metric("可用现金", f"¥{account.cash:,.2f}")
    m3.metric("累计盈亏", f"¥{pnl:+,.2f}", f"{(pnl / account.initial_capital):+.2%}" if account.initial_capital > 0 else "0%")
    m4.metric("持仓标的数", len(positions))

    st.subheader("录入实际成交 / 出入金")
    if not can_write:
        st.info("当前为只读模式，请在左侧栏输入管理口令后解锁账本写入。")
    else:
        with st.form("trade_form"):
            fc1, fc2, fc3 = st.columns(3)
            t_date = fc1.date_input("成交日期", value=date.today())
            t_side = fc2.selectbox("操作类型", ["BUY", "SELL", "DEPOSIT", "WITHDRAW"])
            t_sym = fc3.selectbox("标的（出入金可任选）", all_syms, format_func=_fmt_symbol)
            fc4, fc5, fc6 = st.columns(3)
            t_shares = fc4.number_input("股数/份数（出入金填0）", min_value=0, step=100, value=1000)
            t_price = fc5.number_input("成交价（出入金填金额）", min_value=0.0, step=0.01, value=1.0, format="%.4f")
            t_fee = fc6.number_input("手续费", min_value=0.0, step=0.1, value=0.0)
            t_stop = st.number_input("初始止损价（可选，0表示不设置）", min_value=0.0, step=0.01, value=0.0, format="%.4f")
            t_note = st.text_input("备注")
            submitted = st.form_submit_button("💾 保存到数据库")
            if submitted:
                ledger.record_execution(
                    trade_date=t_date,
                    symbol=t_sym if t_side in ("BUY", "SELL") else "CASH",
                    side=t_side,
                    shares=int(t_shares),
                    price=float(t_price),
                    fee=float(t_fee),
                    stop_loss_price=float(t_stop) if t_stop > 0 else None,
                    note=t_note,
                )
                st.success("记录已持久化写入数据库！")
                st.rerun()

    st.subheader("历史成交流水")
    df_trades = storage.load_trades(limit=100)
    if df_trades.empty:
        st.caption("暂无流水记录。")
    else:
        st.dataframe(df_trades, use_container_width=True, hide_index=True)


# ==============================================================================
# Page 4: Data Source Health Monitor
# ==============================================================================
elif page == "📡 数据源健康监控":
    st.title("📡 行情数据源健康状态监控")
    df_health = storage.load_latest_data_health(limit=100)
    if df_health.empty:
        st.info("暂无同步日志。")
    else:
        latest_per_sym = df_health.drop_duplicates(subset=["symbol"], keep="first").copy()
        latest_per_sym["标的"] = latest_per_sym["symbol"].map(_fmt_symbol)
        ok_n = int((latest_per_sym["status"] == "ok").sum())
        deg_n = int((latest_per_sym["status"] == "degraded").sum())
        fail_n = int((latest_per_sym["status"] == "failed").sum())
        c1, c2, c3 = st.columns(3)
        c1.metric("✅ 主源正常", ok_n)
        c2.metric("🟡 降级备源", deg_n)
        c3.metric("❌ 抓取失败", fail_n)

        st.dataframe(
            latest_per_sym[["标的", "last_trade_date", "source_used", "status", "latency_ms", "run_at", "error_msg"]],
            use_container_width=True,
            hide_index=True,
        )
