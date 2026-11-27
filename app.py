"""AShareTrader Cloud Terminal — Dark Institutional Quant Dashboard (Streamlit + Supabase)."""

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
from ashare_trader.engine.metrics import compute_monthly_returns
from ashare_trader.execution.ledger import LedgerService
from ashare_trader.execution.signal_runner import build_default_strategies, run_daily_signal
from ashare_trader.notify.feishu import FeishuNotifier
from ashare_trader.research.validator import run_strategy_benchmark
from ashare_trader.storage.factory import create_storage

# ------------------------------------------------------------------------------
# Page Config & Dark Institutional Terminal CSS
# ------------------------------------------------------------------------------
st.set_page_config(
    page_title="AShareTrader | 量化交易终端",
    page_icon="AT",
    layout="wide",
    initial_sidebar_state="expanded",
)

DARK_CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');

:root {
    --bg-canvas: #0B0F19;
    --bg-card: #111827;
    --bg-card-hover: #162033;
    --border-subtle: #1F2937;
    --border-strong: #334155;
    --text-primary: #F8FAFC;
    --text-secondary: #94A3B8;
    --text-muted: #64748B;
    --accent-blue: #3B82F6;
    --cn-up: #F87171;        /* A-Share dark mode: vibrant red = buy / positive */
    --cn-down: #34D399;      /* A-Share dark mode: emerald green = sell / negative */
    --warn-amber: #FBBF24;
}

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
    color: var(--text-primary);
}

.stApp {
    background-color: var(--bg-canvas);
}

.block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 2.5rem !important;
    max-width: 1400px;
}
#MainMenu, footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }

section[data-testid="stSidebar"] {
    background-color: #0F172A;
    border-right: 1px solid var(--border-subtle);
}

.brand-block {
    padding: 0.2rem 0 0.9rem 0;
    border-bottom: 1px solid var(--border-subtle);
    margin-bottom: 0.9rem;
}
.brand-title {
    font-size: 1.18rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: var(--text-primary);
}
.brand-sub {
    font-size: 0.78rem;
    color: var(--text-secondary);
    margin-top: 0.25rem;
}

.page-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    flex-wrap: wrap;
    gap: 0.5rem;
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
    font-size: 0.78rem;
    color: var(--text-secondary);
    font-family: 'JetBrains Mono', monospace;
    background: #111827;
    border: 1px solid var(--border-subtle);
    padding: 0.25rem 0.6rem;
    border-radius: 6px;
}

.kpi-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.9rem;
    margin-bottom: 1.25rem;
}
@media (max-width: 960px) {
    .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.kpi-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-radius: 10px;
    padding: 1rem 1.15rem;
}
.kpi-label {
    font-size: 0.74rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-secondary);
    margin-bottom: 0.4rem;
}
.kpi-value {
    font-family: 'JetBrains Mono', monospace;
    font-variant-numeric: tabular-nums;
    font-size: 1.42rem;
    font-weight: 600;
    color: var(--text-primary);
    white-space: nowrap;
    line-height: 1.25;
}
.kpi-sub {
    font-size: 0.78rem;
    color: var(--text-secondary);
    margin-top: 0.35rem;
    font-family: 'JetBrains Mono', monospace;
}
.val-up { color: var(--cn-up); }
.val-down { color: var(--cn-down); }
.val-warn { color: var(--warn-amber); }

.signal-card {
    background: var(--bg-card);
    border: 1px solid var(--border-subtle);
    border-left: 4px solid #64748B;
    border-radius: 8px;
    padding: 0.95rem 1.15rem;
    margin-bottom: 0.7rem;
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.75rem;
}
.signal-buy { border-left-color: var(--cn-up); }
.signal-sell { border-left-color: var(--cn-down); }
.signal-hold { border-left-color: #64748B; }

.badge {
    display: inline-block;
    padding: 0.18rem 0.55rem;
    border-radius: 5px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.03em;
    font-family: 'JetBrains Mono', monospace;
}
.badge-buy { background: rgba(248, 113, 113, 0.15); color: #FCA5A5; border: 1px solid rgba(248, 113, 113, 0.35); }
.badge-sell { background: rgba(52, 211, 153, 0.15); color: #6EE7B7; border: 1px solid rgba(52, 211, 153, 0.35); }
.badge-hold { background: rgba(148, 163, 184, 0.15); color: #CBD5E1; border: 1px solid rgba(148, 163, 184, 0.3); }

.mono-num {
    font-family: 'JetBrains Mono', monospace;
    font-variant-numeric: tabular-nums;
}
.section-label {
    font-size: 0.95rem;
    font-weight: 600;
    color: var(--text-primary);
    margin: 1.25rem 0 0.65rem 0;
    letter-spacing: -0.01em;
}
</style>"""
st.markdown(DARK_CSS, unsafe_allow_html=True)

CHART_PALETTE = ["#3B82F6", "#34D399", "#FBBF24", "#A78BFA", "#94A3B8"]


def _inject_secrets_to_env() -> None:
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


@st.cache_data(ttl=300, show_spinner=False)
def _cached_backtest_snapshots(backend_tag: str) -> pd.DataFrame:
    _ = backend_tag
    return create_storage().load_latest_backtest_runs(limit=20)


def _fmt_symbol(sym: str) -> str:
    a = asset_map.get(sym)
    return f"{a.name} ({sym})" if a else sym


def _style_plotly(fig: go.Figure, title: str, y_title: str, height: int = 360) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        height=height,
        title={"text": title, "font": {"size": 14, "color": "#F8FAFC"}, "x": 0.0, "xanchor": "left", "y": 0.96},
        yaxis_title=y_title,
        xaxis_title=None,
        hovermode="x unified",
        legend={
            "orientation": "h",
            "yanchor": "top",
            "y": -0.16,
            "xanchor": "center",
            "x": 0.5,
            "font": {"color": "#CBD5E1", "size": 11},
        },
        margin={"l": 16, "r": 16, "t": 46, "b": 60},
        paper_bgcolor="#111827",
        plot_bgcolor="#111827",
        font={"family": "Inter, sans-serif", "size": 12, "color": "#CBD5E1"},
    )
    fig.update_xaxes(showgrid=False, linecolor="#1F2937", tickfont={"color": "#94A3B8"})
    fig.update_yaxes(gridcolor="#1F2937", zerolinecolor="#334155", tickfont={"color": "#94A3B8"})
    return fig


def _render_kpi_row(items: list[dict[str, str]]) -> None:
    """Render zero-indentation HTML KPI grid (immune to CommonMark 4-space code block parsing)."""
    parts = ['<div class="kpi-grid">']
    for item in items:
        cls = item.get("value_class", "")
        parts.append(
            f'<div class="kpi-card">'
            f'<div class="kpi-label">{item["label"]}</div>'
            f'<div class="kpi-value {cls}">{item["value"]}</div>'
            f'<div class="kpi-sub">{item.get("sub", "")}</div>'
            f'</div>'
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def _render_page_header(title: str, meta: str) -> None:
    html = (
        f'<div class="page-header">'
        f'<h1 class="page-title">{title}</h1>'
        f'<div class="page-meta">{meta}</div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


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
    '<div class="brand-block">'
    '<div class="brand-title">AShareTrader</div>'
    '<div class="brand-sub">跨资产 ETF 量化信号与风控终端</div>'
    '</div>',
    unsafe_allow_html=True,
)

page = st.sidebar.radio(
    "工作区",
    ["今日信号与风控", "多策略回测对比", "持仓与资金账本", "数据源健康监控"],
    label_visibility="collapsed",
)

st.sidebar.divider()
st.sidebar.caption("主策略：TrendATR (100%)")
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
    _render_page_header("今日交易信号与风控监控", f"MARKET AS OF {latest_mkt_date}")

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
                "value_class": "val-up" if pnl > 0 else ("val-down" if pnl < 0 else ""),
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

    act_c1, act_c2, _ = st.columns([1.8, 1.8, 2.4])
    with act_c1:
        if st.button("重算最新信号", type="primary", use_container_width=True):
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
        st.info("数据库暂无信号记录。点击上方「立即重算最新信号」或等待定时任务执行。")
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
            stop_str = f"止损线 {float(stop_px):.3f}" if pd.notna(stop_px) and float(stop_px) > 0 else "避险底仓"

            card_html = (
                f'<div class="signal-card {card_cls}">'
                f'<div>'
                f'<span class="badge {badge_cls}">{badge_txt}</span>'
                f'<strong style="margin-left:0.6rem;font-size:0.98rem;color:#F8FAFC;">{_fmt_symbol(sym)}</strong>'
                f'<div style="font-size:0.82rem;color:#94A3B8;margin-top:0.35rem;">{reason}</div>'
                f'</div>'
                f'<div style="text-align:right;white-space:nowrap;">'
                f'<div class="mono-num" style="font-size:1.08rem;font-weight:600;color:#F8FAFC;">目标 {w_tgt:.1%}</div>'
                f'<div class="mono-num" style="font-size:0.78rem;color:#94A3B8;margin-top:0.2rem;">{ref_str} · {stop_str}</div>'
                f'</div>'
                f'</div>'
            )
            st.markdown(card_html, unsafe_allow_html=True)

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
# Page 2: Multi-Strategy Backtest Comparison (Instant Snapshot + On-Demand Custom)
# ==============================================================================
elif page == "多策略回测对比":
    _render_page_header("多策略同口径回测对比", "NO-LOOKAHEAD · 100-LOT · SLIPPAGE 5BPS")

    def_w = strat_cfg.get("combiner", {}).get("weights", {})
    with st.container(border=True):
        r1_c1, r1_c2, _ = st.columns([1.8, 1.6, 1.6])
        start_dt = r1_c1.date_input("回测起始日", value=date(2019, 1, 1))
        run_btn = r1_c2.button("运行回测", type="primary", use_container_width=True)

        r2_c1, r2_c2, r2_c3 = st.columns(3)
        w_trd = r2_c1.slider("TrendATR 权重", 0.0, 1.0, float(def_w.get("trend_atr", 1.0)), 0.05)
        w_rp = r2_c2.slider("RiskParity 权重", 0.0, 1.0, float(def_w.get("risk_parity", 0.0)), 0.05)
        w_mom = r2_c3.slider("MomentumR2 权重", 0.0, 1.0, float(def_w.get("momentum_r2", 0.0)), 0.05)

    # On-demand custom run (only when user clicks button)
    if run_btn:
        if close_df.empty:
            st.warning("存储中暂无行情数据，请先同步行情。")
        elif w_trd + w_rp + w_mom <= 0:
            st.error("组合总权重必须大于 0。")
        else:
            cfg_copy = {
                **strat_cfg,
                "combiner": {"weights": {"momentum_r2": w_mom, "trend_atr": w_trd, "risk_parity": w_rp}},
            }
            strategies, combined = build_default_strategies(universe.safe_haven.symbol, cfg_copy)
            with st.spinner("正在执行逐日无未来函数回测（约 15~30 秒）..."):
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
            st.session_state["custom_bt"] = (results, summary_df)

    # Render custom results if available, otherwise instant-load Supabase precomputed snapshots!
    if "custom_bt" in st.session_state:
        results, summary_df = st.session_state["custom_bt"]
        st.caption("当前展示：自定义参数实时回测结果")
        eq_curves = {name: res.equity_curve for name, res in results.items()}
        bm_curve = next(iter(results.values())).benchmark_curve
        yearly_df = results["combined"].yearly_metrics if "combined" in results else pd.DataFrame()
    else:
        snap_df = _cached_backtest_snapshots(storage.backend_name)
        eq_curves = {}
        bm_curve = None
        yearly_df = pd.DataFrame()
        summary_rows: list[dict[str, Any]] = []
        if not snap_df.empty:
            latest_run_date = snap_df["run_date"].iloc[0]
            latest_snaps = snap_df[snap_df["run_date"] == latest_run_date].drop_duplicates(subset=["strategy_name"])
            st.caption(f"当前展示：云端预计算快照（更新日期 {latest_run_date}）· 如需改权重请调整上方滑块并点击「运行自定义回测」")
            for _, row in latest_snaps.iterrows():
                sname = str(row["strategy_name"])
                m = row["metrics_json"] if isinstance(row["metrics_json"], dict) else json.loads(row["metrics_json"])
                summary_rows.append({"strategy": sname, **m})
                pts = row["equity_curve_json"] if isinstance(row["equity_curve_json"], list) else json.loads(row["equity_curve_json"])
                if pts:
                    s_eq = pd.Series(
                        [float(p["equity"]) for p in pts],
                        index=pd.DatetimeIndex([pd.Timestamp(p["date"]) for p in pts]),
                        name=sname,
                    )
                    eq_curves[sname] = s_eq
            if eq_curves and universe.benchmark.symbol in close_df.columns:
                ref_idx = next(iter(eq_curves.values())).index
                bm_s = close_df[universe.benchmark.symbol].reindex(ref_idx).ffill().dropna()
                if not bm_s.empty:
                    bm_curve = bm_s / bm_s.iloc[0] * bt_cfg.initial_capital
        summary_df = pd.DataFrame(summary_rows)

    if summary_df.empty:
        st.info("暂无云端回测快照。点击上方「运行自定义回测」即可生成。")
    else:
        display_df = pd.DataFrame(
            {
                "策略": summary_df["strategy"],
                "年化收益": summary_df["cagr"].map(lambda x: f"{float(x):.2%}"),
                "最大回撤": summary_df["max_drawdown"].map(lambda x: f"{float(x):.2%}"),
                "卡玛比": summary_df["calmar"].map(lambda x: f"{float(x):.3f}"),
                "夏普比": summary_df["sharpe"].map(lambda x: f"{float(x):.3f}"),
                "索提诺": summary_df["sortino"].map(lambda x: f"{float(x):.3f}"),
                "年化换手": summary_df["ann_turnover"].map(lambda x: f"{float(x):.1f}x"),
                "累计手续费": summary_df["total_fees"].map(lambda x: f"¥{float(x):,.0f}"),
            }
        )
        st.markdown('<div class="section-label">核心绩效对比表</div>', unsafe_allow_html=True)
        st.dataframe(display_df, use_container_width=True, hide_index=True)

        if eq_curves:
            fig_eq = go.Figure()
            for idx, (name, s_eq) in enumerate(eq_curves.items()):
                norm_eq = s_eq / s_eq.iloc[0]
                fig_eq.add_trace(
                    go.Scatter(
                        x=norm_eq.index,
                        y=norm_eq.values,
                        mode="lines",
                        name=name,
                        line={"color": CHART_PALETTE[idx % len(CHART_PALETTE)], "width": 2.6 if name == "combined" else 1.5},
                    )
                )
            if bm_curve is not None:
                norm_bm = bm_curve / bm_curve.iloc[0]
                fig_eq.add_trace(
                    go.Scatter(
                        x=norm_bm.index,
                        y=norm_bm.values,
                        mode="lines",
                        name="沪深300基准",
                        line={"color": "#64748B", "width": 1.4, "dash": "dot"},
                    )
                )
            st.plotly_chart(_style_plotly(fig_eq, "归一化净值曲线", "净值 (起点=1.0)", height=360), use_container_width=True)

            fig_dd = go.Figure()
            for idx, (name, s_eq) in enumerate(eq_curves.items()):
                dd = (s_eq / s_eq.cummax() - 1.0) * 100.0
                fig_dd.add_trace(
                    go.Scatter(
                        x=dd.index,
                        y=dd.values,
                        mode="lines",
                        name=name,
                        line={"color": CHART_PALETTE[idx % len(CHART_PALETTE)], "width": 1.6},
                    )
                )
            st.plotly_chart(_style_plotly(fig_dd, "水下动态回撤 (%)", "回撤 (%)", height=320), use_container_width=True)

            comb_eq = eq_curves["combined"] if "combined" in eq_curves else eq_curves.get("trend_atr")
            if comb_eq is not None and not comb_eq.empty:
                monthly = compute_monthly_returns(comb_eq)
                if not monthly.empty:
                    fig_hm = px.imshow(
                        monthly * 100,
                        text_auto=".1f",
                        color_continuous_scale=["#065F46", "#1E293B", "#B91C1C"],
                        color_continuous_midpoint=0,
                        aspect="auto",
                    )
                    st.plotly_chart(_style_plotly(fig_hm, "实盘组合月度收益率热力图 (%)", "年份", height=360), use_container_width=True)


# ==============================================================================
# Page 3: Ledger & Position Management
# ==============================================================================
elif page == "持仓与资金账本":
    _render_page_header("实盘 / 模拟持仓与资金账本", "PERSISTED LEDGER · DYNAMIC TRAILING STOP")

    account = ledger.get_account()
    positions = ledger.get_positions()
    total_eq = ledger.total_equity(latest_prices)
    mkt_val = total_eq - account.cash
    pnl = total_eq - account.initial_capital
    pnl_pct = (pnl / account.initial_capital) if account.initial_capital > 0 else 0.0

    _render_kpi_row(
        [
            {"label": "账户总资产", "value": f"¥{total_eq:,.0f}", "sub": f"初始本金 ¥{account.initial_capital:,.0f}"},
            {"label": "持仓总市值", "value": f"¥{mkt_val:,.0f}", "sub": f"仓位占比 {(mkt_val / total_eq):.1%}" if total_eq > 0 else "0%"},
            {"label": "可用现金", "value": f"¥{account.cash:,.0f}", "sub": f"现金占比 {(account.cash / total_eq):.1%}" if total_eq > 0 else "0%"},
            {
                "label": "累计净盈亏",
                "value": f"¥{pnl:+,.0f}",
                "sub": f"收益率 {pnl_pct:+.2%}",
                "value_class": "val-up" if pnl > 0 else ("val-down" if pnl < 0 else ""),
            },
        ]
    )

    col_left, col_right = st.columns([1.35, 1])
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
            hole=0.55,
            color_discrete_sequence=CHART_PALETTE,
        )
        fig_pie.update_traces(textfont={"color": "#F8FAFC"})
        fig_pie.update_layout(
            template="plotly_dark",
            height=300,
            margin={"l": 12, "r": 12, "t": 16, "b": 16},
            paper_bgcolor="#111827",
            legend={"font": {"color": "#CBD5E1"}},
        )
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
    _render_page_header("数据源健康监控", "DUAL-SOURCE: AKSHARE PRIMARY · BAOSTOCK FALLBACK")

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
                    "#34D399" if s == "ok" else ("#FBBF24" if s == "degraded" else "#F87171")
                    for s in latest_per_sym["status"]
                ],
            )
        )
        st.plotly_chart(_style_plotly(fig_lat, "各标的最近一次同步耗时 (ms)", "耗时 (ms)"), use_container_width=True)

        st.markdown('<div class="section-label">标的同步状态明细</div>', unsafe_allow_html=True)
        st.dataframe(
            latest_per_sym[["标的", "last_trade_date", "source_used", "status", "latency_ms", "error_msg"]],
            use_container_width=True,
            hide_index=True,
        )
