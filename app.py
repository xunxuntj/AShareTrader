"""AShareTrader Cloud Terminal — Impeccable Dark Quant UI (Streamlit + Supabase)."""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

# Ensure src/ is on sys.path for Streamlit Community Cloud (which only installs requirements.txt)
_SRC_DIR = str(Path(__file__).resolve().parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

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
from ashare_trader.execution.ledger import LedgerService, compute_live_equity_curve
from ashare_trader.execution.signal_runner import run_daily_signal
from ashare_trader.notify.feishu import FeishuNotifier
from ashare_trader.storage.factory import create_storage

# ------------------------------------------------------------------------------
# Page Config & Impeccable Dark Terminal Design System
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="AShareTrader · 量化交易终端",
    page_icon="AT",
    layout="wide",
    initial_sidebar_state="expanded",
)

IMPECCABLE_CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');

:root {
    --bg-canvas: #0D0E11;          /* Warm charcoal, never pure #000 */
    --bg-surface: #14161B;         /* Elevated surface */
    --bg-surface-2: #1B1E25;       /* Subtle interactive surface */
    --hairline: #242830;           /* 1px crisp structural divider */
    --hairline-strong: #323743;
    --ink-primary: #ECEEF2;        /* High-contrast warm off-white */
    --ink-secondary: #98A0AE;      /* Muted metadata */
    --ink-tertiary: #656D7C;
    --accent-gold: #D4A359;        /* Restraint financial amber accent */
    --cn-up: #E05A5A;              /* A-Share positive / buy */
    --cn-down: #36B37E;            /* A-Share negative / sell / healthy */
    --warn: #E5A93C;
}

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
    color: var(--ink-primary);
    -webkit-font-smoothing: antialiased;
}

.stApp {
    background-color: var(--bg-canvas);
}

.block-container {
    padding-top: 1.8rem !important;
    padding-bottom: 3rem !important;
    max-width: 1320px;
}
#MainMenu, footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }

/* Sidebar */
section[data-testid="stSidebar"] {
    background-color: var(--bg-surface);
    border-right: 1px solid var(--hairline);
}
.brand-block {
    padding: 0.15rem 0 1.1rem 0;
    border-bottom: 1px solid var(--hairline);
    margin-bottom: 1rem;
}
.brand-kicker {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.68rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--accent-gold);
    margin-bottom: 0.25rem;
}
.brand-title {
    font-size: 1.25rem;
    font-weight: 700;
    letter-spacing: -0.025em;
    color: var(--ink-primary);
    line-height: 1.2;
}
.brand-sub {
    font-size: 0.78rem;
    color: var(--ink-secondary);
    margin-top: 0.28rem;
}
.sys-meta {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.75rem;
    color: var(--ink-secondary);
    line-height: 1.75;
}
.sys-meta span {
    color: var(--ink-primary);
}

/* Editorial Page Header (strong modular type hierarchy) */
.page-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 0.75rem;
    padding-bottom: 1rem;
    border-bottom: 1px solid var(--hairline);
    margin-bottom: 1.4rem;
}
.page-title {
    font-size: 1.7rem;
    font-weight: 600;
    letter-spacing: -0.03em;
    color: var(--ink-primary);
    margin: 0;
    line-height: 1.2;
}
.page-meta {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.76rem;
    letter-spacing: 0.04em;
    color: var(--ink-secondary);
}

/* Unified Metrics Strip (anti-slop alternative to 4 repetitive hero cards) */
.metrics-strip {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    background: var(--bg-surface);
    border: 1px solid var(--hairline);
    border-radius: 10px;
    margin-bottom: 1.5rem;
    overflow: hidden;
}
@media (max-width: 960px) {
    .metrics-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.metric-cell {
    padding: 1.05rem 1.25rem;
    border-right: 1px solid var(--hairline);
}
.metric-cell:last-child {
    border-right: none;
}
.metric-label {
    font-size: 0.72rem;
    font-weight: 500;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--ink-secondary);
    margin-bottom: 0.45rem;
}
.metric-value {
    font-family: 'IBM Plex Mono', monospace;
    font-variant-numeric: tabular-nums;
    font-size: 1.48rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--ink-primary);
    white-space: nowrap;
    line-height: 1.2;
}
.metric-sub {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.76rem;
    color: var(--ink-secondary);
    margin-top: 0.35rem;
}
.val-up { color: var(--cn-up); }
.val-down { color: var(--cn-down); }
.val-warn { color: var(--warn); }
.val-gold { color: var(--accent-gold); }

/* Order Blotter (clean hairline rows, zero side-tab cliché) */
.blotter {
    background: var(--bg-surface);
    border: 1px solid var(--hairline);
    border-radius: 10px;
    overflow: hidden;
    margin-bottom: 1.25rem;
}
.blotter-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.75rem;
    padding: 1rem 1.25rem;
    border-bottom: 1px solid var(--hairline);
    transition: background-color 140ms ease;
}
.blotter-row:last-child { border-bottom: none; }
.blotter-row:hover { background-color: var(--bg-surface-2); }

.pill {
    display: inline-block;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    padding: 0.16rem 0.5rem;
    border-radius: 4px;
    vertical-align: middle;
}
.pill-buy { background: rgba(224, 90, 90, 0.14); color: #F28B8B; }
.pill-sell { background: rgba(54, 179, 126, 0.14); color: #67D4A6; }
.pill-hold { background: rgba(152, 160, 174, 0.14); color: #C2C8D4; }

.section-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin: 1.5rem 0 0.75rem 0;
}
.section-title {
    font-size: 0.96rem;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ink-primary);
}
.section-note {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.74rem;
    color: var(--ink-tertiary);
}
.mono {
    font-family: 'IBM Plex Mono', monospace;
    font-variant-numeric: tabular-nums;
}
</style>"""
st.markdown(IMPECCABLE_CSS, unsafe_allow_html=True)

CHART_PALETTE = ["#D4A359", "#36B37E", "#60A5FA", "#C084FC", "#7E8796"]


def _inject_secrets_to_env() -> None:
    try:
        for key in ["SUPABASE_URL", "SUPABASE_KEY", "FEISHU_WEBHOOK_URL", "FEISHU_SECRET", "APP_PASSWORD"]:
            if key not in os.environ and key in st.secrets:
                os.environ[key] = str(st.secrets[key])
    except Exception:  # noqa: BLE001
        pass


def _check_write_auth() -> bool:
    app_pwd = os.getenv("APP_PASSWORD", "").strip()
    if not app_pwd:
        return True
    if st.session_state.get("write_unlocked", False):
        return True
    with st.sidebar.expander("写操作鉴权 · 只读"):
        pwd = st.text_input("管理口令", type="password", key="pwd_input")
        if st.button("解锁写入", use_container_width=True):
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
def _cached_close_panel(symbols_tuple: tuple[str, ...], backend_tag: str) -> pd.DataFrame:
    _ = backend_tag
    dm_local = DataManager(storage=create_storage())
    return dm_local.load_price_panel(list(symbols_tuple), field="close")


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


def _style_plotly(fig: go.Figure, title: str, y_title: str, height: int = 360) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        height=height,
        title={"text": title, "font": {"size": 14, "color": "#ECEEF2", "family": "IBM Plex Sans"}, "x": 0.0, "xanchor": "left", "y": 0.96},
        yaxis_title=y_title,
        xaxis_title=None,
        hovermode="x unified",
        legend={
            "orientation": "h",
            "yanchor": "top",
            "y": -0.16,
            "xanchor": "center",
            "x": 0.5,
            "font": {"color": "#98A0AE", "size": 11, "family": "IBM Plex Mono"},
        },
        margin={"l": 16, "r": 16, "t": 48, "b": 60},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"family": "IBM Plex Sans, sans-serif", "size": 12, "color": "#98A0AE"},
    )
    fig.update_xaxes(showgrid=False, linecolor="#242830", tickfont={"color": "#98A0AE", "family": "IBM Plex Mono"})
    fig.update_yaxes(gridcolor="#1F232B", zerolinecolor="#323743", tickfont={"color": "#98A0AE", "family": "IBM Plex Mono"})
    return fig


def _render_metrics_strip(items: list[dict[str, str]]) -> None:
    """Render zero-indentation unified metrics strip."""
    parts = ['<div class="metrics-strip">']
    for item in items:
        cls = item.get("value_class", "")
        parts.append(
            f'<div class="metric-cell">'
            f'<div class="metric-label">{item["label"]}</div>'
            f'<div class="metric-value {cls}">{item["value"]}</div>'
            f'<div class="metric-sub">{item.get("sub", "")}</div>'
            f'</div>'
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def _render_page_header(title: str, meta: str) -> None:
    st.markdown(
        f'<div class="page-header"><h1 class="page-title">{title}</h1><div class="page-meta">{meta}</div></div>',
        unsafe_allow_html=True,
    )


def _render_section_head(title: str, note: str = "") -> None:
    st.markdown(
        f'<div class="section-head"><div class="section-title">{title}</div><div class="section-note">{note}</div></div>',
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------------------
# Sidebar Navigation & Telemetry
# ------------------------------------------------------------------------------
close_df = _cached_close_panel(tuple(all_syms), storage.backend_name)
latest_mkt_date = close_df.index.max().strftime("%Y-%m-%d") if not close_df.empty else "—"
latest_prices: dict[str, float] = {
    sym: float(close_df[sym].dropna().iloc[-1])
    for sym in close_df.columns
    if not close_df[sym].dropna().empty
}

st.sidebar.markdown(
    '<div class="brand-block">'
    '<div class="brand-kicker">QUANT TERMINAL</div>'
    '<div class="brand-title">AShareTrader</div>'
    '<div class="brand-sub">跨资产 ETF 趋势与风控系统</div>'
    '</div>',
    unsafe_allow_html=True,
)

page = st.sidebar.radio(
    "导航",
    ["交易终端", "系统状态"],
    label_visibility="collapsed",
)

st.sidebar.markdown(
    f'<div style="border-top:1px solid #242830;margin:1rem 0;padding-top:0.9rem;" class="sys-meta">'
    f'主策略 <span>TrendATR 100%</span><br>'
    f'标的池 <span>{len(universe.assets)} 风险 + 1 避险</span><br>'
    f'最新日 <span>{latest_mkt_date}</span><br>'
    f'数据库 <span>{storage.backend_name}</span>'
    f'</div>',
    unsafe_allow_html=True,
)

if st.sidebar.button("刷新云端数据", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

can_write = _check_write_auth()


# ==============================================================================
# Page 1: Unified Trading Terminal (Signals + Live Performance + Ledger)
# ==============================================================================
if page == "交易终端":
    _render_page_header("交易终端", f"AS OF {latest_mkt_date} · TREND-ATR 100%")

    account = ledger.get_account()
    positions = ledger.get_positions()
    total_eq = ledger.total_equity(latest_prices)
    mkt_val = total_eq - account.cash
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
    near_stop_cnt = sum(
        1
        for sym, pos in positions.items()
        if pos.stop_loss_price
        and latest_prices.get(sym, pos.avg_cost) > 0
        and (latest_prices.get(sym, pos.avg_cost) / pos.stop_loss_price - 1.0) < 0.025
    )

    _render_metrics_strip(
        [
            {
                "label": "账户总资产",
                "value": f"¥{total_eq:,.0f}",
                "sub": f"累计 {pnl:+,.0f} ({pnl_pct:+.2%})",
                "value_class": "val-up" if pnl > 0 else ("val-down" if pnl < 0 else ""),
            },
            {
                "label": "仓位 / 可用现金",
                "value": f"{(mkt_val / total_eq):.0%}" if total_eq > 0 else "0%",
                "sub": f"现金 ¥{account.cash:,.0f}",
            },
            {
                "label": "今日调仓指令",
                "value": f"{action_cnt} 笔",
                "sub": f"信号日 {latest_batch['signal_date'].iloc[0]}" if not latest_batch.empty else "等待计算",
                "value_class": "val-gold" if action_cnt > 0 else "",
            },
            {
                "label": "止损预警",
                "value": f"{near_stop_cnt} 只",
                "sub": f"持仓 {len(positions)} 只 · 阈值 <2.5%",
                "value_class": "val-warn" if near_stop_cnt > 0 else "",
            },
        ]
    )

    _render_section_head(
        "执行指令清单",
        f"MODE: {latest_batch['run_mode'].iloc[0].upper()}" if not latest_batch.empty else "NO SIGNAL",
    )
    if latest_batch.empty:
        st.info("暂无落库信号，点击下方「重算信号」即可立即生成。")
    else:
        rows_html = ['<div class="blotter">']
        for _, row in latest_batch.iterrows():
            act = str(row["action"])
            sym = str(row["symbol"])
            w_tgt = float(row["target_weight"])
            ref_px = row.get("ref_price")
            stop_px = row.get("stop_loss_price")
            reason = str(row.get("reason") or "按模型目标权重执行")
            pill_cls = {"BUY": "pill-buy", "SELL": "pill-sell"}.get(act, "pill-hold")
            pill_txt = {"BUY": "BUY 买入", "SELL": "SELL 卖出"}.get(act, "HOLD 持有")
            ref_str = f"参考价 {float(ref_px):.3f}" if pd.notna(ref_px) else "参考价 —"
            stop_str = f"止损线 {float(stop_px):.3f}" if pd.notna(stop_px) and float(stop_px) > 0 else "避险底仓"

            rows_html.append(
                f'<div class="blotter-row">'
                f'<div style="min-width:220px;flex:1;">'
                f'<span class="pill {pill_cls}">{pill_txt}</span>'
                f'<span style="margin-left:0.65rem;font-weight:600;font-size:0.98rem;color:#ECEEF2;">{_fmt_symbol(sym)}</span>'
                f'<div style="font-size:0.82rem;color:#98A0AE;margin-top:0.35rem;">{reason}</div>'
                f'</div>'
                f'<div style="text-align:right;white-space:nowrap;">'
                f'<div class="mono" style="font-size:1.05rem;font-weight:600;color:#ECEEF2;">目标 {w_tgt:.1%}</div>'
                f'<div class="mono" style="font-size:0.76rem;color:#98A0AE;margin-top:0.2rem;">{ref_str} · {stop_str}</div>'
                f'</div>'
                f'</div>'
            )
        rows_html.append("</div>")
        st.markdown("".join(rows_html), unsafe_allow_html=True)

    # Compact action bar beneath blotter
    act_c1, act_c2, _ = st.columns([1.4, 1.6, 4.0])
    with act_c1:
        if st.button("重算信号", type="primary", use_container_width=True):
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
                st.success(f"已完成计算（{len(instructions)} 条）")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"计算失败：{exc}")
    with act_c2:
        notifier = FeishuNotifier()
        if can_write and notifier.is_configured:
            if st.button("推送测试飞书", use_container_width=True):
                try:
                    ins, lp = run_daily_signal(storage, universe, strat_cfg, run_mode="intraday", persist=False)
                    card = notifier.build_signal_card(ins, ledger.get_positions(), lp, asset_map, None, "intraday")
                    notifier.send_card(card)
                    st.success("测试卡片已发送")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"发送失败：{exc}")

    _render_section_head("实盘策略表现", "LIVE NAV VS CSI 300")
    df_live_trades = storage.load_trades(limit=500)
    live_eq_df = compute_live_equity_curve(df_live_trades, close_df, initial_capital=account.initial_capital)
    if live_eq_df.empty or len(live_eq_df) < 2:
        st.caption("录入首笔实盘成交并经历至少 2 个交易日后，此处将自动绘制你的实盘净值曲线、最大回撤与同期沪深 300 基准对比。")
    else:
        live_ret = float(live_eq_df["nav"].iloc[-1] - 1.0)
        live_dd = float(abs(live_eq_df["drawdown"].min()))
        bm_series = (
            close_df[universe.benchmark.symbol].reindex(live_eq_df.index).ffill().dropna()
            if universe.benchmark.symbol in close_df.columns
            else pd.Series(dtype=float)
        )
        bm_nav = (bm_series / bm_series.iloc[0]) if not bm_series.empty else pd.Series(dtype=float)
        bm_ret = float(bm_nav.iloc[-1] - 1.0) if not bm_nav.empty else 0.0
        excess = live_ret - bm_ret

        st.markdown(
            f'<div class="sys-meta" style="margin-bottom:0.6rem;">'
            f'实盘累计 <span class="{"val-up" if live_ret > 0 else ("val-down" if live_ret < 0 else "")}">{live_ret:+.2%}</span> · '
            f'同期沪深300 <span>{bm_ret:+.2%}</span> · '
            f'超额收益 <span class="{"val-up" if excess > 0 else ("val-down" if excess < 0 else "")}">{excess:+.2%}</span> · '
            f'实盘最大回撤 <span>{live_dd:.2%}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        fig_live = go.Figure()
        fig_live.add_trace(
            go.Scatter(
                x=live_eq_df.index,
                y=live_eq_df["nav"].values,
                mode="lines",
                name="实盘净值",
                line={"color": "#D4A359", "width": 2.4},
            )
        )
        if not bm_nav.empty:
            fig_live.add_trace(
                go.Scatter(
                    x=bm_nav.index,
                    y=bm_nav.values,
                    mode="lines",
                    name="沪深300基准",
                    line={"color": "#656D7C", "width": 1.4, "dash": "dot"},
                )
            )
        st.plotly_chart(_style_plotly(fig_live, "实盘净值走势 vs 沪深300", "净值 (起点=1.0)", height=320), use_container_width=True)

    _render_section_head("持仓与资金账本", "POSITIONS · LEDGER · TRAILING STOP")
    tab_pos, tab_entry, tab_hist = st.tabs(["持仓与动态止损", "录入成交 / 出入金", "历史流水"])

    with tab_pos:
        col_tbl, col_pie = st.columns([1.65, 1])
        with col_tbl:
            if not positions:
                st.caption("当前为空仓状态。在右侧「录入成交 / 出入金」记入实际成交后，此处将实时追踪每只 ETF 的动态止损安全垫。")
            else:
                pos_rows: list[dict[str, Any]] = []
                for sym, pos in positions.items():
                    px = latest_prices.get(sym, pos.avg_cost)
                    pnl_r = (px / pos.avg_cost - 1.0) if pos.avg_cost > 0 else 0.0
                    cushion = (px / pos.stop_loss_price - 1.0) if (pos.stop_loss_price and pos.stop_loss_price > 0) else None
                    pos_rows.append(
                        {
                            "标的": _fmt_symbol(sym),
                            "份额": pos.shares,
                            "市值(¥)": round(pos.shares * px, 2),
                            "成本价": round(pos.avg_cost, 4),
                            "最新价": round(px, 4),
                            "浮盈亏%": round(pnl_r * 100, 2),
                            "止损线": round(pos.stop_loss_price, 4) if pos.stop_loss_price else None,
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

        with col_pie:
            alloc = [{"资产": "可用现金", "金额": account.cash}]
            for sym, pos in positions.items():
                alloc.append({"资产": _fmt_symbol(sym), "金额": pos.shares * latest_prices.get(sym, pos.avg_cost)})
            alloc_df = pd.DataFrame(alloc)
            fig_pie = px.pie(
                alloc_df,
                names="资产",
                values="金额",
                hole=0.58,
                color_discrete_sequence=CHART_PALETTE,
            )
            fig_pie.update_traces(textfont={"color": "#ECEEF2", "family": "IBM Plex Mono"})
            fig_pie.update_layout(
                template="plotly_dark",
                height=280,
                margin={"l": 10, "r": 10, "t": 16, "b": 16},
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                legend={"font": {"color": "#98A0AE", "size": 11}},
            )
            st.plotly_chart(fig_pie, use_container_width=True)

    with tab_entry:
        if not can_write:
            st.info("当前为只读模式，请在左侧栏「写操作鉴权」输入管理口令后解锁。")
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
                t_stop = fc7.number_input("初始止损价（0 自动）", min_value=0.0, step=0.01, value=0.0, format="%.4f")
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

    with tab_hist:
        if df_live_trades.empty:
            st.caption("暂无成交流水记录。")
        else:
            st.dataframe(df_live_trades, use_container_width=True, hide_index=True)


# ==============================================================================
# Page 2: System Status & Data Source Health
# ==============================================================================
elif page == "系统状态":
    _render_page_header("系统与数据源状态", "AKSHARE PRIMARY · BAOSTOCK FALLBACK")

    df_health = _cached_health_logs(storage.backend_name)
    if df_health.empty:
        st.info("暂无同步日志。")
    else:
        latest_per_sym = df_health.drop_duplicates(subset=["symbol"], keep="first").copy()
        latest_per_sym["标的"] = latest_per_sym["symbol"].map(_fmt_symbol)
        ok_n = int((latest_per_sym["status"] == "ok").sum())
        deg_n = int((latest_per_sym["status"] == "degraded").sum())
        fail_n = int((latest_per_sym["status"] == "failed").sum())
        avg_lat = int(latest_per_sym["latency_ms"].mean()) if not latest_per_sym.empty else 0

        _render_metrics_strip(
            [
                {"label": "主源正常", "value": f"{ok_n}", "sub": "AKShare 直接命中", "value_class": "val-down"},
                {"label": "备源降级", "value": f"{deg_n}", "sub": "Baostock 兜底", "value_class": "val-warn" if deg_n else ""},
                {"label": "抓取失败", "value": f"{fail_n}", "sub": "需排查限流", "value_class": "val-up" if fail_n else ""},
                {"label": "平均耗时", "value": f"{avg_lat} ms", "sub": "单标的同步延迟"},
            ]
        )

        # Horizontal bar chart: full Chinese ETF names remain crisp and legible
        sorted_h = latest_per_sym.sort_values("latency_ms", ascending=True)
        fig_lat = go.Figure(
            go.Bar(
                y=sorted_h["标的"],
                x=sorted_h["latency_ms"],
                orientation="h",
                marker_color=[
                    "#36B37E" if s == "ok" else ("#E5A93C" if s == "degraded" else "#E05A5A")
                    for s in sorted_h["status"]
                ],
                text=sorted_h["latency_ms"].map(lambda v: f"{int(v)} ms"),
                textposition="outside",
                textfont={"color": "#98A0AE", "family": "IBM Plex Mono", "size": 11},
            )
        )
        fig_lat = _style_plotly(fig_lat, "各标的最近一次同步耗时 (ms)", "", height=360)
        fig_lat.update_layout(showlegend=False, margin={"l": 16, "r": 56, "t": 46, "b": 24})
        st.plotly_chart(fig_lat, use_container_width=True)

        _render_section_head("同步状态明细", f"TOTAL {len(latest_per_sym)} ASSETS")
        st.dataframe(
            latest_per_sym[["标的", "last_trade_date", "source_used", "status", "latency_ms", "error_msg"]],
            use_container_width=True,
            hide_index=True,
        )
