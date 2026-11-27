"""AShareTrader Cloud Terminal — Institutional Quant Dashboard (Streamlit + Supabase)."""

from __future__ import annotations

import os
from datetime import date
from typing import Any

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

# ------------------------------------------------------------------------------
# Page Config & Anti-Slop Institutional CSS
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="AShareTrader | 量化交易终端",
    page_icon="AT",
    layout="wide",
    initial_sidebar_state="expanded",
)

CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    --bg-canvas: #F8FAFC;
    --bg-card: #FFFFFF;
    --border-subtle: #E2E8F0;
    --text-primary: #0F172A;
    --text-secondary: #475569;
    --text-muted: #94A3B8;
    --accent-brand: #1E3A8A;
    --cn-up: #C9302C;        /* A-Share convention: red = buy / positive */
    --cn-down: #15803D;      /* A-Share convention: green = sell / negative */
    --warn-amber: #B45309;
}

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
    color: var(--text-primary);
}

.stApp {
    background-color: var(--bg-canvas);
}

/* Tighten default Streamlit top whitespace and hide noisy chrome */
.block-container {
    padding-top: 1.6rem !important;
    padding-bottom: 2.5rem !important;
    max-width: 1360px;
}
#MainMenu, footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }

/* Sidebar refinement */
section[data-testid="stSidebar"] {
    background-color: #FFFFFF;
    border-right: 1px solid var(--border-subtle);
}
.brand-block {
    padding: 0.25rem 0 1rem 0;
    border-bottom: 1px solid var(--border-subtle);
    margin-bottom: 1rem;
}
.brand-title {
    font-size: 1.15rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--text-primary);
    margin: 0;
}
.brand-sub {
    font-size: 0.78rem;
    color: var(--text-secondary);
    margin-top: 0.2rem;
}

/* Page header */
.page-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    border-bottom: 1px solid var(--border-subtle);
    padding-bottom: 0.85rem;
    margin-bottom: 1.25rem;
}
.page-title {
    font-size: 1.45rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--text-primary);
    margin: 0;
}
.page-meta {
    font-size: 0.82rem;
    color: var(--text-secondary);
    font-family: 'JetBrains Mono', monospace;
}

/* KPI Cards */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.9rem;
    margin-bottom: 1.25rem;
}
.kpi-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 8px;
    padding: 0.95rem 1.1rem;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.03);
}
.kpi-label {
    font-size: 0.74rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-secondary);
    margin-bottom: 0.35rem;
}
.kpi-value {
    font-family: 'JetBrains Mono', monospace;
    font-variant-numeric: tabular-nums;
    font-size: 1.45rem;
    font-weight: 600;
    color: var(--text-primary);
    line-height: 1.2;
}
.kpi-sub {
    font-size: 0.78rem;
    color: var(--text-secondary);
    margin-top: 0.3rem;
    font-family: 'JetBrains Mono', monospace;
}
.val-up { color: var(--cn-up); }
.val-down { color: var(--cn-down); }
.val-warn { color: var(--warn-amber); }

/* Signal Action Cards */
.signal-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-left: 4px solid #64748B;
    border-radius: 6px;
    padding: 0.85rem 1rem;
    margin-bottom: 0.65rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 1rem;
}
.signal-buy { border-left-color: var(--cn-up); }
.signal-sell { border-left-color: var(--cn-down); }
.signal-hold { border-left-color: #94A3B8; }

.badge {
    display: inline-block;
    padding: 0.16rem 0.5rem;
    border-radius: 4px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.03em;
    font-family: 'JetBrains Mono', monospace;
}
.badge-buy { background: #FEF2F2; color: var(--cn-up); border: 1px solid #FECACA; }
.badge-sell { background: #F0FDF4; color: var(--cn-down); border: 1px solid #BBF7D0; }
.badge-hold { background: #F1F5F9; color: var(--text-secondary); border: 1px solid #E2E8F0; }

.mono-num {
    font-family: 'JetBrains Mono', monospace;
    font-variant-numeric: tabular-nums;
}
.section-label {
    font-size: 0.92rem;
    font-weight: 600;
    color: var(--text-primary);
    margin: 1.1rem 0 0.6rem 0;
    letter-spacing: -0.01em;
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

CHART_PALETTE = ["#1E3A8A", "#0F766E", "#B45309", "#7C3AED", "#64748B"]


def _inject_secrets_to_env() -> None:
    """Propagate Streamlit Cloud secrets into process env."""
    for key in ["SUPABASE_URL", "SUPABASE_KEY", "FEISHU_WEBHOOK_URL", "FEISHU_SECRET", "APP_PASSWORD"]:
        if key not in os.environ and key in st.secrets:
            os.environ[key] = str(st.secrets[key])


def _check_write_auth() -> bool:
    app_pwd = os.getenv("APP_PASSWORD", "").strip()
    if not app_pwd:
        return True
    if st.session_state.get("write_unlocked", False):
        return True
    with st.sidebar.expander("写操作鉴权（当前只读）"):
        pwd = st.text_input("管理口令", type="password", key="pwd_input")
        if st.button("验证解锁", use_container_width=True):
            if pwd == app_pwd:
                st.session_state["write_unlocked"] = True
                st.rerun()
            else:
                st.error("口令不正确")
    return False


_inject_secrets_to_env()
storage = create_storage()
universe = load_universe()
strat_cfg = load_strategies_config()
bt_cfg = load_backtest_config()
ledger = LedgerService(storage=storage)

all_syms = [a.symbol for a in universe.all_assets]
asset_map = universe.asset_map


@st.cache_data(ttl=300, show_spinner=False)
def _cached_price_panels(symbols_tuple: tuple[str, ...], backend_tag: str) -> dict[str, pd.DataFrame]:
    """Cache market OHLC panels for 5 minutes to avoid redundant Supabase REST calls."""
    _ = backend_tag
    dm_local = DataManager(storage=create_storage())
    syms = list(symbols_tuple)
    return {
        "close": dm_local.load_price_panel(syms, field="close"),
        "high": dm_local.load_price_panel(syms, field="high"),
        "low": dm_local.load_price_panel(syms, field="low"),
        "open": dm_local.load_price_panel(syms, field="open"),
    }


@st.cache_data(ttl=120, show_spinner=False)
def _cached_latest_signals(backend_tag: str) -> pd.DataFrame:
    _ = backend_tag
    return create_storage().load_latest_signals(limit=50)


@st.cache_data(ttl=120, show_spinner=False)
def _cached_health_logs(backend_tag: str) -> pd.DataFrame:
    _ = backend_tag
    return create_storage().load_latest_data_health(limit=120)


def _fmt_symbol(sym: str) -> str:
    a = asset_map.get(sym)
    return f"{a.name} ({sym})" if a else sym


def _style_plotly(fig: go.Figure, title: str, y_title: str) -> go.Figure:
    fig.update_layout(
        template="plotly_white",
        title={"text": title, "font": {"size": 14, "color": "#0F172A"}, "x": 0.0, "xanchor": "left"},
        yaxis_title=y_title,
        xaxis_title=None,
        hovermode="x unified",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1.0},
        margin={"l": 16, "r": 16, "t": 54, "b": 24},
        paper_bgcolor="#FFFFFF",
        plot_bgcolor="#FFFFFF",
        font={"family": "Inter, sans-serif", "size": 12, "color": "#334155"},
    )
    fig.update_xaxes(showgrid=False, linecolor="#E2E8F0")
    fig.update_yaxes(gridcolor="#F1F5F9", zerolinecolor="#CBD5E1")
    return fig


def _render_kpi_row(items: list[dict[str, str]]) -> None:
    cards_html = []
    for item in items:
        cls = item.get("value_class", "")
        cards_html.append(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">{item['label']}</div>
                <div class="kpi-value {cls}">{item['value']}</div>
                <div class="kpi-sub">{item.get('sub', '')}</div>
            </div>
            """
        )
    st.markdown(f'<div class="kpi-grid">{"".join(cards_html)}</div>', unsafe_allow_html=True)


# ------------------------------------------------------------------------------
# Sidebar Navigation & System Status
# ------------------------------------------------------------------------------
panels = _cached_price_panels(tuple(all_syms), storage.backend_name)
close_df = panels["close"]
latest_mkt_date = close_df.index.max().strftime("%Y-%m-%d") if not close_df.empty else "无数据"
latest_prices: dict[str, float] = {
    sym: float(close_df[sym].dropna().iloc[-1])
    for sym in close_df.columns
    if not close_df[sym].dropna().empty
}

st.sidebar.markdown(
    """
    <div class="brand-block">
        <div class="brand-title">AShareTrader</div>
        <div class="brand-sub">跨资产 ETF 量化信号与风控终端</div>
    </div>
    """,
    unsafe_allow_html=True,
)

page = st.sidebar.radio(
    "工作区",
    ["今日信号与风控", "多策略回测对比", "持仓与资金账本", "数据源健康监控"],
    label_visibility="collapsed",
)

st.sidebar.divider()
st.sidebar.caption(f"主策略：TrendATR (100%)")
st.sidebar.caption(f"标的池：{len(universe.assets)} 风险 ETF + 1 避险")
st.sidebar.caption(f"最新行情：{latest_mkt_date}")
st.sidebar.caption(f"存储引擎：{storage.backend_name}")

if st.sidebar.button("刷新云端缓存", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

can_write = _check_write_auth()


# ==============================================================================
# Page 1: Today's Signal & Risk Monitor
# ==============================================================================
if page == "今日信号与风控":
    st.markdown(
        f"""
        <div class="page-header">
            <div>
                <h1 class="page-title">今日交易信号与风控监控</h1>
            </div>
            <div class="page-meta">MARKET AS OF {latest_mkt_date}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    account = ledger.get_account()
    positions = ledger.get_positions()
    total_eq = ledger.total_equity(latest_prices)
    pnl = total_eq - account.initial_capital
    pnl_pct = (pnl / account.initial_capital) if account.initial_capital > 0 else 0.0

    df_sig = _cached_latest_signals(storage.backend_name)
    latest_batch = pd.DataFrame()
    if not df_sig.empty:
        latest_sig_date = df_sig["signal_date"].iloc[0]
        latest_run_mode = df_sig["run_mode"].iloc[0]
        latest_batch = df_sig[
            (df_sig["signal_date"] == latest_sig_date) & (df_sig["run_mode"] == latest_run_mode)
        ].copy()

    action_cnt = int(latest_batch["action"].isin(["BUY", "SELL"]).sum()) if not latest_batch.empty else 0
    near_stop_cnt = 0
    for sym, pos in positions.items():
        px = latest_prices.get(sym, pos.avg_cost)
        if pos.stop_loss_price and px > 0 and (px / pos.stop_loss_price - 1.0) < 0.025:
            near_stop_cnt += 1

    df_h = _cached_health_logs(storage.backend_name)
    health_latest = df_h.drop_duplicates(subset=["symbol"], keep="first") if not df_h.empty else pd.DataFrame()
    fail_cnt = int((health_latest["status"] == "failed").sum()) if not health_latest.empty else 0

    _render_kpi_row(
        [
            {
                "label": "账户总资产",
                "value": f"¥{total_eq:,.0f}",
                "sub": f"累计 {pnl:+,.0f} ({pnl_pct:+.2%})",
                "value_class": "val-up" if pnl >= 0 else "val-down",
            },
            {
                "label": "今日调仓指令",
                "value": f"{action_cnt} 笔",
                "sub": f"信号日 {latest_batch['signal_date'].iloc[0]}" if not latest_batch.empty else "等待首次计算",
            },
            {
                "label": "止损临近预警",
                "value": f"{near_stop_cnt} 只",
                "sub": f"当前持仓 {len(positions)} 只 (阈值 <2.5%)",
                "value_class": "val-warn" if near_stop_cnt > 0 else "",
            },
            {
                "label": "数据源状态",
                "value": "正常" if fail_cnt == 0 else f"{fail_cnt} 异常",
                "sub": f"最新行情 {latest_mkt_date}",
                "value_class": "val-down" if fail_cnt == 0 else "val-up",
            },
        ]
    )

    # Action bar
    act_c1, act_c2, _ = st.columns([1.3, 1.3, 4.4])
    with act_c1:
        if st.button("立即重算最新信号", type="primary", use_container_width=True):
            try:
                instructions, _ = run_daily_signal(
                    storage=storage,
                    universe=universe,
                    strat_cfg=strat_cfg,
                    run_mode="intraday",
                    lot_size=bt_cfg.lot_size,
                    rebalance_threshold=bt_cfg.rebalance_threshold,
                    persist=can_write,
                )
                st.cache_data.clear()
                st.success(f"已完成计算（{len(instructions)} 条记录）")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"信号计算失败：{exc}")
    with act_c2:
        notifier = FeishuNotifier()
        if can_write and notifier.is_configured:
            if st.button("发送飞书测试卡片", use_container_width=True):
                try:
                    ins, lp = run_daily_signal(storage, universe, strat_cfg, run_mode="intraday", persist=False)
                    card = notifier.build_signal_card(ins, ledger.get_positions(), lp, asset_map, None, "intraday")
                    notifier.send_card(card)
                    st.success("飞书测试卡片已发送")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"发送失败：{exc}")

    st.markdown('<div class="section-label">目标仓位与操作清单</div>', unsafe_allow_html=True)
    if latest_batch.empty:
        st.info("数据库暂无信号记录。点击上方“立即重算最新信号”或等待定时任务执行。")
    else:
        for _, row in latest_batch.iterrows():
            act = str(row["action"])
            sym = str(row["symbol"])
            w_tgt = float(row["target_weight"])
            ref_px = row.get("ref_price")
            stop_px = row.get("stop_loss_price")
            reason = str(row.get("reason") or "按模型目标权重执行")
            card_cls = {"BUY": "signal-buy", "SELL": "signal-sell"}.get(act, "signal-hold")
            badge_cls = {"BUY": "badge-buy", "SELL": "badge-sell"}.get(act, "badge-hold")
            badge_txt = {"BUY": "BUY 买入/加仓", "SELL": "SELL 卖出/清仓"}.get(act, "HOLD 维持")

            ref_str = f"参考价 {float(ref_px):.3f}" if pd.notna(ref_px) else "参考价 —"
            stop_str = f"止损线 {float(stop_px):.3f}" if pd.notna(stop_px) and float(stop_px) > 0 else "无止损线(避险)"

            st.markdown(
                f"""
                <div class="signal-card {card_cls}">
                    <div>
                        <span class="badge {badge_cls}">{badge_txt}</span>
                        <strong style="margin-left:0.55rem;font-size:0.96rem;">{_fmt_symbol(sym)}</strong>
                        <div style="font-size:0.82rem;color:#475569;margin-top:0.35rem;">{reason}</div>
                    </div>
                    <div style="text-align:right;white-space:nowrap;">
                        <div class="mono-num" style="font-size:1.05rem;font-weight:600;">目标 {w_tgt:.1%}</div>
                        <div class="mono-num" style="font-size:0.78rem;color:#64748B;">{ref_str} · {stop_str}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    st.markdown('<div class="section-label">当前持仓动态止损安全垫</div>', unsafe_allow_html=True)
    if not positions:
        st.info("当前账本暂无持仓记录。可在「持仓与资金账本」页录入实盘成交以启用移动止损追踪。")
    else:
        pos_rows: list[dict[str, Any]] = []
        for sym, pos in positions.items():
            px = latest_prices.get(sym, pos.avg_cost)
            pnl_r = (px / pos.avg_cost - 1.0) if pos.avg_cost > 0 else 0.0
            cushion = (px / pos.stop_loss_price - 1.0) if (pos.stop_loss_price and pos.stop_loss_price > 0) else None
            pos_rows.append(
                {
                    "标的": _fmt_symbol(sym),
                    "持仓份额": pos.shares,
                    "市值(¥)": round(pos.shares * px, 2),
                    "成本价": round(pos.avg_cost, 4),
                    "最新价": round(px, 4),
                    "浮盈亏%": round(pnl_r * 100, 2),
                    "动态止损价": round(pos.stop_loss_price, 4) if pos.stop_loss_price else None,
                    "安全垫%": round(cushion * 100, 2) if cushion is not None else None,
                }
            )
        st.dataframe(
            pd.DataFrame(pos_rows),
            use_container_width=True,
            hide_index=True,
            column_config={
                "浮盈亏%": st.column_config.NumberColumn(format="%.2f%%"),
                "安全垫%": st.column_config.ProgressColumn(min_value=0.0, max_value=15.0, format="%.2f%%"),
            },
        )


# ==============================================================================
# Page 2: Multi-Strategy Backtest Comparison
# ==============================================================================
elif page == "多策略回测对比":
    st.markdown(
        f"""
        <div class="page-header">
            <div><h1 class="page-title">多策略同口径回测对比</h1></div>
            <div class="page-meta">NO-LOOKAHEAD · 100-LOT · SLIPPAGE 5BPS</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if close_df.empty:
        st.warning("存储中暂无行情数据，请先运行数据同步。")
    else:
        def_w = strat_cfg.get("combiner", {}).get("weights", {})
        with st.container(border=True):
            c1, c2, c3, c4, c5 = st.columns([1.2, 1, 1, 1, 1])
            start_dt = c1.date_input("起始日期", value=date(2018, 1, 1))
            w_trd = c2.slider("TrendATR 权重", 0.0, 1.0, float(def_w.get("trend_atr", 1.0)), 0.05)
            w_rp = c3.slider("RiskParity 权重", 0.0, 1.0, float(def_w.get("risk_parity", 0.0)), 0.05)
            w_mom = c4.slider("MomentumR2 权重", 0.0, 1.0, float(def_w.get("momentum_r2", 0.0)), 0.05)
            run_btn = c5.button("运行回测", type="primary", use_container_width=True)

        if run_btn or "bt_results" not in st.session_state:
            if w_trd + w_rp + w_mom <= 0:
                st.error("组合总权重必须大于 0。")
            else:
                cfg_copy = {
                    **strat_cfg,
                    "combiner": {"weights": {"momentum_r2": w_mom, "trend_atr": w_trd, "risk_parity": w_rp}},
                }
                strategies, combined = build_default_strategies(universe.safe_haven.symbol, cfg_copy)
                with st.spinner("正在执行逐日无未来函数回测..."):
                    results, summary_df = run_strategy_benchmark(
                        strategies=[*strategies, combined],
                        close_df=panels["close"],
                        high_df=panels["high"],
                        low_df=panels["low"],
                        open_df=panels["open"],
                        backtest_config=bt_cfg,
                        start_date=start_dt.isoformat(),
                        benchmark_symbol=universe.benchmark.symbol,
                    )
                st.session_state["bt_results"] = results
                st.session_state["bt_summary"] = summary_df

        if "bt_results" in st.session_state:
            results = st.session_state["bt_results"]
            summary_df = st.session_state["bt_summary"].copy()

            # Format clean summary table
            display_df = pd.DataFrame(
                {
                    "策略": summary_df["strategy"],
                    "年化收益": summary_df["cagr"].map(lambda x: f"{x:.2%}"),
                    "最大回撤": summary_df["max_drawdown"].map(lambda x: f"{x:.2%}"),
                    "卡玛比": summary_df["calmar"].map(lambda x: f"{x:.3f}"),
                    "夏普比": summary_df["sharpe"].map(lambda x: f"{x:.3f}"),
                    "索提诺": summary_df["sortino"].map(lambda x: f"{x:.3f}"),
                    "年化换手": summary_df["ann_turnover"].map(lambda x: f"{x:.1f}x"),
                    "累计手续费": summary_df["total_fees"].map(lambda x: f"¥{x:,.0f}"),
                }
            )
            st.markdown('<div class="section-label">核心绩效对比表</div>', unsafe_allow_html=True)
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            # Equity & Drawdown side-by-side
            ch1, ch2 = st.columns(2)
            with ch1:
                fig_eq = go.Figure()
                for idx, (name, res) in enumerate(results.items()):
                    norm_eq = res.equity_curve / res.equity_curve.iloc[0]
                    width = 2.6 if name == "combined" else 1.5
                    fig_eq.add_trace(
                        go.Scatter(
                            x=norm_eq.index,
                            y=norm_eq.values,
                            mode="lines",
                            name=name,
                            line={"color": CHART_PALETTE[idx % len(CHART_PALETTE)], "width": width},
                        )
                    )
                bm = next(iter(results.values())).benchmark_curve
                if bm is not None:
                    norm_bm = bm / bm.iloc[0]
                    fig_eq.add_trace(
                        go.Scatter(
                            x=norm_bm.index,
                            y=norm_bm.values,
                            mode="lines",
                            name="沪深300基准",
                            line={"color": "#94A3B8", "width": 1.3, "dash": "dot"},
                        )
                    )
                st.plotly_chart(_style_plotly(fig_eq, "归一化净值曲线", "净值 (起点=1.0)"), use_container_width=True)

            with ch2:
                fig_dd = go.Figure()
                for idx, (name, res) in enumerate(results.items()):
                    dd = (res.equity_curve / res.equity_curve.cummax() - 1.0) * 100.0
                    fig_dd.add_trace(
                        go.Scatter(
                            x=dd.index,
                            y=dd.values,
                            mode="lines",
                            name=name,
                            line={"color": CHART_PALETTE[idx % len(CHART_PALETTE)], "width": 1.6},
                        )
                    )
                st.plotly_chart(_style_plotly(fig_dd, "水下动态回撤 (%)", "回撤 (%)"), use_container_width=True)

            # Yearly bar chart & Monthly heatmap for combined strategy
            comb_res = results.get("combined")
            if comb_res is not None:
                ch3, ch4 = st.columns(2)
                with ch3:
                    ym = comb_res.yearly_metrics
                    if not ym.empty:
                        fig_yr = go.Figure()
                        fig_yr.add_trace(
                            go.Bar(
                                x=ym["year"].astype(str),
                                y=ym["return"] * 100,
                                name="组合策略",
                                marker_color="#1E3A8A",
                            )
                        )
                        if "benchmark_return" in ym.columns:
                            fig_yr.add_trace(
                                go.Bar(
                                    x=ym["year"].astype(str),
                                    y=ym["benchmark_return"] * 100,
                                    name="沪深300基准",
                                    marker_color="#CBD5E1",
                                )
                            )
                        fig_yr.update_layout(barmode="group")
                        st.plotly_chart(_style_plotly(fig_yr, "逐年收益率对比 (%)", "年度收益 (%)"), use_container_width=True)
                with ch4:
                    monthly = compute_monthly_returns(comb_res.equity_curve)
                    if not monthly.empty:
                        fig_hm = px.imshow(
                            monthly * 100,
                            text_auto=".1f",
                            color_continuous_scale=["#15803D", "#F8FAFC", "#C9302C"],
                            color_continuous_midpoint=0,
                            aspect="auto",
                        )
                        st.plotly_chart(_style_plotly(fig_hm, "组合策略月度收益率热力图 (%)", "年份"), use_container_width=True)


# ==============================================================================
# Page 3: Ledger & Position Management
# ==============================================================================
elif page == "持仓与资金账本":
    st.markdown(
        """
        <div class="page-header">
            <div><h1 class="page-title">实盘 / 模拟持仓与资金账本</h1></div>
            <div class="page-meta">PERSISTED LEDGER · DYNAMIC TRAILING STOP</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    account = ledger.get_account()
    positions = ledger.get_positions()
    total_eq = ledger.total_equity(latest_prices)
    mkt_val = total_eq - account.cash
    pnl = total_eq - account.initial_capital
    pnl_pct = (pnl / account.initial_capital) if account.initial_capital > 0 else 0.0

    _render_kpi_row(
        [
            {"label": "账户总资产", "value": f"¥{total_eq:,.2f}", "sub": f"初始本金 ¥{account.initial_capital:,.0f}"},
            {"label": "持仓总市值", "value": f"¥{mkt_val:,.2f}", "sub": f"仓位占比 {(mkt_val / total_eq):.1%}" if total_eq > 0 else "0%"},
            {"label": "可用现金", "value": f"¥{account.cash:,.2f}", "sub": f"现金占比 {(account.cash / total_eq):.1%}" if total_eq > 0 else "0%"},
            {
                "label": "累计净盈亏",
                "value": f"¥{pnl:+,.2f}",
                "sub": f"收益率 {pnl_pct:+.2%}",
                "value_class": "val-up" if pnl >= 0 else "val-down",
            },
        ]
    )

    col_left, col_right = st.columns([1.3, 1])
    with col_left:
        st.markdown('<div class="section-label">录入成交 / 出入金</div>', unsafe_allow_html=True)
        if not can_write:
            st.info("当前为只读模式，请在左侧边栏输入管理口令解锁写入。")
        else:
            with st.form("trade_form", border=True):
                fc1, fc2, fc3 = st.columns(3)
                t_date = fc1.date_input("成交日期", value=date.today())
                t_side = fc2.selectbox("操作类型", ["BUY", "SELL", "DEPOSIT", "WITHDRAW"])
                t_sym = fc3.selectbox("标的（出入金可忽略）", all_syms, format_func=_fmt_symbol)
                fc4, fc5, fc6 = st.columns(3)
                t_shares = fc4.number_input("份数（出入金填 0）", min_value=0, step=100, value=1000)
                t_price = fc5.number_input("成交价 / 出入金额", min_value=0.0, step=0.01, value=1.0, format="%.4f")
                t_fee = fc6.number_input("手续费 (¥)", min_value=0.0, step=0.1, value=0.0)
                fc7, fc8 = st.columns([1, 2])
                t_stop = fc7.number_input("初始止损价（0 表示自动）", min_value=0.0, step=0.01, value=0.0, format="%.4f")
                t_note = fc8.text_input("备注")
                if st.form_submit_button("保存至数据库", type="primary", use_container_width=True):
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
                    st.cache_data.clear()
                    st.success("已持久化写入数据库")
                    st.rerun()

    with col_right:
        st.markdown('<div class="section-label">当前资产分布</div>', unsafe_allow_html=True)
        alloc = [{"资产": "可用现金", "金额": account.cash}]
        for sym, pos in positions.items():
            alloc.append({"资产": _fmt_symbol(sym), "金额": pos.shares * latest_prices.get(sym, pos.avg_cost)})
        alloc_df = pd.DataFrame(alloc)
        fig_pie = px.pie(
            alloc_df,
            names="资产",
            values="金额",
            hole=0.52,
            color_discrete_sequence=CHART_PALETTE,
        )
        fig_pie.update_layout(margin={"l": 10, "r": 10, "t": 20, "b": 10}, paper_bgcolor="#FFFFFF")
        st.plotly_chart(fig_pie, use_container_width=True)

    st.markdown('<div class="section-label">历史成交流水</div>', unsafe_allow_html=True)
    df_trades = storage.load_trades(limit=100)
    if df_trades.empty:
        st.caption("暂无成交流水记录。")
    else:
        st.dataframe(df_trades, use_container_width=True, hide_index=True)


# ==============================================================================
# Page 4: Data Source Health Monitor
# ==============================================================================
elif page == "数据源健康监控":
    st.markdown(
        """
        <div class="page-header">
            <div><h1 class="page-title">行情数据源健康状态监控</h1></div>
            <div class="page-meta">DUAL-SOURCE: AKSHARE PRIMARY · BAOSTOCK FALLBACK</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    df_health = _cached_health_logs(storage.backend_name)
    if df_health.empty:
        st.info("暂无同步日志。运行 `sync-data` 或等待云端任务执行后将在此展示明细。")
    else:
        latest_per_sym = df_health.drop_duplicates(subset=["symbol"], keep="first").copy()
        latest_per_sym["标的"] = latest_per_sym["symbol"].map(_fmt_symbol)
        ok_n = int((latest_per_sym["status"] == "ok").sum())
        deg_n = int((latest_per_sym["status"] == "degraded").sum())
        fail_n = int((latest_per_sym["status"] == "failed").sum())
        avg_lat = int(latest_per_sym["latency_ms"].mean()) if not latest_per_sym.empty else 0

        _render_kpi_row(
            [
                {"label": "主源正常", "value": f"{ok_n}", "sub": "AKShare 直接命中", "value_class": "val-down"},
                {"label": "备源降级", "value": f"{deg_n}", "sub": "Baostock 自动兜底", "value_class": "val-warn" if deg_n else ""},
                {"label": "抓取失败", "value": f"{fail_n}", "sub": "需排查接口限流", "value_class": "val-up" if fail_n else ""},
                {"label": "平均响应耗时", "value": f"{avg_lat} ms", "sub": "单标的同步延迟"},
            ]
        )

        fig_lat = go.Figure(
            go.Bar(
                x=latest_per_sym["标的"],
                y=latest_per_sym["latency_ms"],
                marker_color=[
                    "#15803D" if s == "ok" else ("#B45309" if s == "degraded" else "#C9302C")
                    for s in latest_per_sym["status"]
                ],
            )
        )
        st.plotly_chart(_style_plotly(fig_lat, "各标的最近一次同步耗时 (ms)", "耗时 (ms)"), use_container_width=True)

        st.markdown('<div class="section-label">标的同步状态明细</div>', unsafe_allow_html=True)
        st.dataframe(
            latest_per_sym[["标的", "last_trade_date", "source_used", "status", "latency_ms", "run_at", "error_msg"]],
            use_container_width=True,
            hide_index=True,
        )
