"""AShareTrader Cloud Terminal — Multi-User Dark Quant Dashboard (Streamlit + Supabase)."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Ensure src/ is on sys.path for Streamlit Community Cloud (which only installs requirements.txt)
_SRC_DIR = str(Path(__file__).resolve().parent / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import extra_streamlit_components as stx
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ashare_trader.auth import AuthService, create_session_token, verify_session_token
from ashare_trader.config_loader import (
    load_backtest_config,
    load_strategies_config,
    load_universe,
)
from ashare_trader.data.calendar import cn_today
from ashare_trader.data.manager import DataManager
from ashare_trader.execution.adherence import build_admin_team_summary, compute_signal_adherence
from ashare_trader.execution.ledger import LedgerService, compute_live_equity_curve
from ashare_trader.execution.signal_runner import (
    compute_entry_guidance,
    compute_suggested_shares,
    run_shared_model_signal,
)
from ashare_trader.models import UserAccount
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
    --bg-canvas: #0D0E11;
    --bg-surface: #14161B;
    --bg-surface-2: #1B1E25;
    --hairline: #242830;
    --hairline-strong: #323743;
    --ink-primary: #ECEEF2;
    --ink-secondary: #98A0AE;
    --ink-tertiary: #656D7C;
    --accent-gold: #D4A359;
    --cn-up: #E05A5A;
    --cn-down: #36B37E;
    --warn: #E5A93C;
}

html, body, [class*="css"] {
    font-family: 'IBM Plex Sans', -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
    color: var(--ink-primary);
    -webkit-font-smoothing: antialiased;
}

.stApp { background-color: var(--bg-canvas); }

.block-container {
    padding-top: 1.8rem !important;
    padding-bottom: 3rem !important;
    max-width: 1320px;
}
#MainMenu, footer { visibility: hidden; }
header[data-testid="stHeader"] { background: transparent; }

section[data-testid="stSidebar"] {
    background-color: var(--bg-surface);
    border-right: 1px solid var(--hairline);
}
.brand-block {
    padding: 0.15rem 0 1.05rem 0;
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
.sys-meta span { color: var(--ink-primary); }

.user-chip {
    background: var(--bg-surface-2);
    border: 1px solid var(--hairline);
    border-radius: 8px;
    padding: 0.65rem 0.85rem;
    margin-bottom: 0.85rem;
}
.user-chip-name {
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--ink-primary);
}
.user-chip-role {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.68rem;
    letter-spacing: 0.08em;
    color: var(--accent-gold);
    margin-top: 0.15rem;
}

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
.metric-cell:last-child { border-right: none; }
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


_inject_secrets_to_env()
storage = create_storage()
auth_service = AuthService(storage)
auth_service.ensure_default_admin()

COOKIE_NAME = "ashare_session"
cookie_manager = stx.CookieManager(key="ashare_cookie_mgr")


def _read_remember_token() -> str | None:
    """Read signed remember-me cookie: fast server-side st.context.cookies first, then CookieManager."""
    if st.session_state.get("just_logged_out", False):
        return None
    try:
        ctx_cookies = getattr(st.context, "cookies", None)
        if ctx_cookies and COOKIE_NAME in ctx_cookies:
            val = ctx_cookies.get(COOKIE_NAME)
            if val:
                return str(val)
    except Exception:  # noqa: BLE001
        pass
    try:
        val = cookie_manager.get(COOKIE_NAME)
        if val:
            return str(val)
    except Exception:  # noqa: BLE001
        pass
    return None


# ------------------------------------------------------------------------------
# Login Gate (with 30-Day Remember-Me Signed Cookie)
# ------------------------------------------------------------------------------
def _render_login_gate() -> UserAccount:
    current = st.session_state.get("auth_user")
    if isinstance(current, UserAccount):
        return current

    # Try auto-login via signed cookie
    token = _read_remember_token()
    if token:
        restored = verify_session_token(token, storage)
        if restored is not None:
            st.session_state["auth_user"] = restored
            return restored

    _, center, _ = st.columns([1.2, 1.4, 1.2])
    with center:
        st.markdown(
            '<div style="margin-top:3.5rem;margin-bottom:1.2rem;">'
            '<div class="brand-kicker">QUANT TERMINAL · SIGN IN</div>'
            '<div style="font-size:1.85rem;font-weight:700;letter-spacing:-0.03em;color:#ECEEF2;">AShareTrader</div>'
            '<div style="font-size:0.86rem;color:#98A0AE;margin-top:0.3rem;">跨资产 ETF 趋势信号与个人实盘账本</div>'
            '</div>',
            unsafe_allow_html=True,
        )
        with st.form("login_form", border=True):
            uid = st.text_input("用户名", placeholder="例如 admin")
            pwd = st.text_input("密码", type="password")
            remember = st.checkbox("记住我（30 天免登录，飞书点击直达）", value=True)
            submitted = st.form_submit_button("登录终端", type="primary", use_container_width=True)
            if submitted:
                user = auth_service.authenticate(uid, pwd)
                if user is not None:
                    st.session_state["auth_user"] = user
                    st.session_state["just_logged_out"] = False
                    if remember:
                        st.session_state["pending_remember_token"] = create_session_token(user, ttl_days=30)
                    st.rerun()
                else:
                    st.error("用户名或密码错误，或账号已停用。")
    st.stop()


auth_user = _render_login_gate()
is_admin = auth_user.role == "admin"

# Persist remember-me cookie on the rendered post-login page so frontend component commits reliably
_pending_tok = st.session_state.pop("pending_remember_token", None)
if _pending_tok:
    try:
        cookie_manager.set(
            COOKIE_NAME,
            _pending_tok,
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
            key="set_auth_cookie",
        )
    except Exception:  # noqa: BLE001
        pass

universe = load_universe()
strat_cfg = load_strategies_config()
bt_cfg = load_backtest_config()
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
    return create_storage().load_latest_signals(limit=200)


@st.cache_data(ttl=120, show_spinner=False)
def _cached_health_logs(backend_tag: str) -> pd.DataFrame:
    _ = backend_tag
    return create_storage().load_latest_data_health(limit=120)


@st.cache_data(ttl=120, show_spinner=False)
def _cached_user_trades(user_id: str, backend_tag: str) -> pd.DataFrame:
    _ = backend_tag
    return create_storage().load_trades(user_id=user_id, limit=500)


def _fmt_symbol(sym: str) -> str:
    a = asset_map.get(sym)
    return f"{a.name} ({sym})" if a else sym


def _style_plotly(fig: go.Figure, title: str, y_title: str, height: int = 340) -> go.Figure:
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
# Sidebar Navigation, User Identity & Admin Perspective Switcher
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

role_tag = "ADMIN · 管理员" if is_admin else "MEMBER · 交易员"
st.sidebar.markdown(
    f'<div class="user-chip">'
    f'<div class="user-chip-name">{auth_user.display_name} ({auth_user.user_id})</div>'
    f'<div class="user-chip-role">{role_tag}</div>'
    f'</div>',
    unsafe_allow_html=True,
)

nav_options = ["交易终端", "团队总览与管理", "系统状态"] if is_admin else ["交易终端", "系统状态"]
page = st.sidebar.radio("导航", nav_options, label_visibility="collapsed")

# Admin can inspect any member's trading terminal in read-only observation mode
viewing_user_id = auth_user.user_id
viewing_user_label = f"{auth_user.display_name} ({auth_user.user_id})"
all_users = storage.load_users() if is_admin else [auth_user]
if is_admin and page == "交易终端" and len(all_users) > 1:
    user_labels = {u.user_id: f"{u.display_name} ({u.user_id})" for u in all_users}
    selected_uid = st.sidebar.selectbox(
        "查看用户视角",
        list(user_labels.keys()),
        index=list(user_labels.keys()).index(auth_user.user_id) if auth_user.user_id in user_labels else 0,
        format_func=lambda k: user_labels[k],
    )
    viewing_user_id = selected_uid
    viewing_user_label = user_labels[selected_uid]

is_self_view = viewing_user_id == auth_user.user_id

st.sidebar.markdown(
    f'<div style="border-top:1px solid #242830;margin:1rem 0;padding-top:0.9rem;" class="sys-meta">'
    f'主策略 <span>TrendATR 100%</span><br>'
    f'标的池 <span>{len(universe.assets)} 风险 + 1 避险</span><br>'
    f'最新日 <span>{latest_mkt_date}</span><br>'
    f'数据库 <span>{storage.backend_name}</span>'
    f'</div>',
    unsafe_allow_html=True,
)

c_ref, c_out = st.sidebar.columns(2)
if c_ref.button("刷新缓存", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
if c_out.button("退出登录", use_container_width=True):
    st.session_state.pop("auth_user", None)
    st.session_state["just_logged_out"] = True
    try:
        cookie_manager.delete(COOKIE_NAME, key="del_auth_cookie")
    except Exception:  # noqa: BLE001
        pass
    st.rerun()


# ==============================================================================
# Page 1: Unified Trading Terminal (Shared Signals + Per-User Ledger & Analytics)
# ==============================================================================
if page == "交易终端":
    header_meta = f"USER: {viewing_user_id.upper()} · AS OF {latest_mkt_date} · TREND-ATR"
    _render_page_header("交易终端", header_meta)
    if not is_self_view:
        st.info(f"当前正在以管理员视角查看 **{viewing_user_label}** 的账户数据（只读观察模式）。")

    ledger = LedgerService(storage=storage, user_id=viewing_user_id)
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

    df_user_trades = _cached_user_trades(viewing_user_id, storage.backend_name)
    adh_summary, adh_detail = compute_signal_adherence(df_sig, df_user_trades, as_of_date=cn_today())
    exec_rate = adh_summary["execution_rate"]
    exec_rate_str = f"{exec_rate:.0%}" if exec_rate is not None else "—"

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
                "label": "信号执行率",
                "value": exec_rate_str,
                "sub": f"已执行 {adh_summary['executed_count']} / 漏单 {adh_summary['missed_count']} / 待办 {adh_summary['pending_count']}",
                "value_class": "val-gold" if exec_rate is not None else "",
            },
            {
                "label": "止损预警",
                "value": f"{near_stop_cnt} 只",
                "sub": f"持仓 {len(positions)} 只 · 阈值 <2.5%",
                "value_class": "val-warn" if near_stop_cnt > 0 else "",
            },
        ]
    )

    # --- Shared Signals Personalized by Current User's Equity & Holdings ---
    _render_section_head(
        "今日执行指令清单（已按本人资金换算）",
        f"SIGNAL {latest_batch['signal_date'].iloc[0]} · {latest_batch['run_mode'].iloc[0].upper()}"
        if not latest_batch.empty
        else "NO SIGNAL",
    )
    if latest_batch.empty:
        st.info("暂无公共策略信号。")
    else:
        target_map: dict[str, dict[str, Any]] = {}
        for _, row in latest_batch.iterrows():
            target_map[str(row["symbol"])] = row.to_dict()

        # Combine shared targets with user's actual positions so user gets exact BUY/SELL/HOLD
        involved_syms = sorted(set(target_map.keys()) | set(positions.keys()))
        rows_html = ['<div class="blotter">']
        for sym in involved_syms:
            meta = target_map.get(sym, {})
            w_tgt = float(meta.get("target_weight", 0.0))
            ref_px_raw = meta.get("ref_price")
            ref_px = float(ref_px_raw) if pd.notna(ref_px_raw) else latest_prices.get(sym)
            stop_px = meta.get("stop_loss_price")
            reason = str(meta.get("reason") or ("策略已移出目标池，建议清仓" if w_tgt <= 1e-4 else "按模型目标权重执行"))

            cur_sh = positions[sym].shares if sym in positions else 0
            cur_w = (cur_sh * ref_px / total_eq) if (ref_px and total_eq > 0) else 0.0
            diff_w = w_tgt - cur_w

            if w_tgt <= 1e-4 and cur_sh > 0:
                act = "SELL"
                sugg_sh = cur_sh
            elif diff_w > bt_cfg.rebalance_threshold:
                sugg_sh = compute_suggested_shares("BUY", w_tgt, cur_sh, total_eq, ref_px, bt_cfg.lot_size)
                act = "BUY" if sugg_sh > 0 else "HOLD"
            elif diff_w < -bt_cfg.rebalance_threshold:
                sugg_sh = compute_suggested_shares("SELL", w_tgt, cur_sh, total_eq, ref_px, bt_cfg.lot_size)
                act = "SELL" if sugg_sh > 0 else "HOLD"
            else:
                act = "HOLD"
                sugg_sh = 0

            is_safe = sym == universe.safe_haven.symbol
            stop_val = float(stop_px) if pd.notna(stop_px) and float(stop_px) > 0 else None
            # For an empty/underweight user viewing a BUY diff on an ongoing trend, use shared action for mid-trend guidance
            shared_act = str(meta.get("action") or act)
            guide = compute_entry_guidance(
                action=shared_act if (act == "BUY" and shared_act == "HOLD") else act,
                ref_price=ref_px,
                stop_loss_price=stop_val,
                is_safe_haven=is_safe,
            )

            pill_cls = {"BUY": "pill-buy", "SELL": "pill-sell"}.get(act, "pill-hold")
            pill_txt = f"{act} {guide['action_label']}"
            ref_str = f"参考价 {ref_px:.3f}" if ref_px is not None else "参考价 —"

            if act in ("BUY", "SELL") and sugg_sh > 0 and ref_px:
                est_amt = sugg_sh * ref_px
                top_right = (
                    f'<span class="val-gold">建议 {sugg_sh:,} 份</span>'
                    f'<span style="color:#98A0AE;font-weight:400;font-size:0.84rem;"> (约 ¥{est_amt:,.0f})</span>'
                    f' · 目标仓位 {w_tgt:.1%}'
                )
            else:
                top_right = f'当前 {cur_w:.1%} → 目标 {w_tgt:.1%}'

            rows_html.append(
                f'<div class="blotter-row">'
                f'<div style="min-width:240px;flex:1;">'
                f'<span class="pill {pill_cls}">{pill_txt}</span>'
                f'<span style="margin-left:0.65rem;font-weight:600;font-size:0.98rem;color:#ECEEF2;">{_fmt_symbol(sym)}</span>'
                f'<div class="mono" style="font-size:0.8rem;color:#D4A359;margin-top:0.35rem;">{guide["guidance"]}</div>'
                f'<div style="font-size:0.78rem;color:#98A0AE;margin-top:0.2rem;">策略理由：{reason}</div>'
                f'</div>'
                f'<div style="text-align:right;white-space:nowrap;">'
                f'<div class="mono" style="font-size:1.02rem;font-weight:600;color:#ECEEF2;">{top_right}</div>'
                f'<div class="mono" style="font-size:0.76rem;color:#98A0AE;margin-top:0.2rem;">{ref_str}</div>'
                f'</div>'
                f'</div>'
            )
        rows_html.append("</div>")
        st.markdown("".join(rows_html), unsafe_allow_html=True)

    # Admin-only shared signal trigger bar
    if is_admin:
        act_c1, act_c2, _ = st.columns([1.6, 1.6, 3.8])
        with act_c1:
            if st.button("重算公共信号", type="primary", use_container_width=True):
                try:
                    instructions, _ = run_shared_model_signal(
                        storage=storage,
                        universe=universe,
                        strat_cfg=strat_cfg,
                        run_mode="intraday",
                        lot_size=bt_cfg.lot_size,
                        rebalance_threshold=bt_cfg.rebalance_threshold,
                        ref_capital=bt_cfg.initial_capital,
                        persist=True,
                    )
                    st.cache_data.clear()
                    st.success(f"公共信号已刷新（{len(instructions)} 条）")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"计算失败：{exc}")
        with act_c2:
            notifier = FeishuNotifier()
            if notifier.is_configured and st.button("群发测试飞书", use_container_width=True):
                try:
                    ins, lp = run_shared_model_signal(
                        storage=storage,
                        universe=universe,
                        strat_cfg=strat_cfg,
                        run_mode="intraday",
                        persist=False,
                    )
                    card = notifier.build_signal_card(ins, {}, lp, asset_map, None, "intraday")
                    notifier.send_card(card)
                    st.success("群飞书测试卡片已发送")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"发送失败：{exc}")

    # --- Per-User Live Strategy Performance ---
    _render_section_head("实盘策略表现", f"{viewing_user_id.upper()} LIVE NAV VS CSI 300")
    live_eq_df = compute_live_equity_curve(df_user_trades, close_df, initial_capital=account.initial_capital)
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
        st.plotly_chart(_style_plotly(fig_live, "实盘净值走势 vs 沪深300", "净值 (起点=1.0)", height=310), use_container_width=True)

    # --- Per-User Ledger, Signal Adherence & Trade Entry ---
    _render_section_head("持仓、信号执行与账本", "POSITIONS · ADHERENCE · LEDGER")
    tab_pos, tab_adh, tab_entry, tab_hist = st.tabs(
        ["持仓与动态止损", "信号执行统计", "录入成交与本金设置", "历史流水"]
    )

    with tab_pos:
        col_tbl, col_pie = st.columns([1.65, 1])
        with col_tbl:
            if not positions:
                st.caption("当前为空仓状态。可在「录入成交与本金设置」中设定初始本金并登记成交。")
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
            fig_pie = px.pie(
                pd.DataFrame(alloc),
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

    with tab_adh:
        slip_txt = f"{adh_summary['avg_slippage_bps']:+.1f} bps" if adh_summary["avg_slippage_bps"] is not None else "—"
        st.markdown(
            f'<div class="sys-meta" style="margin-bottom:0.8rem;">'
            f'匹配规则：按录入的真实交易日（信号日 ~ +3 自然日，支持补录） · '
            f'执行率 <span class="val-gold">{exec_rate_str}</span> · '
            f'平均滑点 <span>{slip_txt}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if adh_detail.empty:
            st.caption("暂无可统计的历史调仓信号。")
        else:
            disp_adh = adh_detail.copy()
            disp_adh["标的"] = disp_adh["symbol"].map(_fmt_symbol)
            disp_adh["状态"] = disp_adh["status"].map(
                {"EXECUTED": "已执行", "MISSED": "未执行", "PENDING": "待执行"}
            )
            st.dataframe(
                disp_adh[
                    ["signal_date", "标的", "action", "ref_price", "trade_date", "actual_price", "actual_shares", "slippage_bps", "状态"]
                ].rename(
                    columns={
                        "signal_date": "信号日",
                        "action": "方向",
                        "ref_price": "参考价",
                        "trade_date": "成交日",
                        "actual_price": "实际成交价",
                        "actual_shares": "成交份数",
                        "slippage_bps": "滑点(bps)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    with tab_entry:
        if not is_self_view:
            st.info("查看他人账户时处于只读模式，切换回本人视角后可录入交易。")
        else:
            # Initial capital setup (editable directly before first trade)
            if df_user_trades.empty:
                with st.form("init_cap_form", border=True):
                    st.caption("首次使用请先设定你的实盘初始本金（录入首笔成交后需改用下方出入金调整）")
                    ic1, ic2 = st.columns([2, 1])
                    new_cap = ic1.number_input(
                        "初始本金 (¥)",
                        min_value=1000.0,
                        step=10000.0,
                        value=float(account.initial_capital),
                    )
                    if ic2.form_submit_button("保存初始本金", use_container_width=True):
                        try:
                            ledger.set_initial_capital(float(new_cap))
                            st.cache_data.clear()
                            st.success(f"初始本金已设为 ¥{new_cap:,.0f}")
                            st.rerun()
                        except Exception as exc:  # noqa: BLE001
                            st.error(str(exc))

            with st.form("trade_form", border=True):
                fc1, fc2, fc3 = st.columns(3)
                t_date = fc1.date_input("真实成交日期（支持补录）", value=cn_today())
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
                    if t_side in ("BUY", "SELL") and (t_shares <= 0 or t_price <= 0):
                        st.error("买入/卖出时，份数和成交价必须大于 0。")
                    elif t_side in ("DEPOSIT", "WITHDRAW") and t_price <= 0:
                        st.error("出入金金额必须大于 0。")
                    else:
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
                        st.success("已持久化写入本人账本")
                        st.rerun()

    with tab_hist:
        if df_user_trades.empty:
            st.caption("暂无成交流水记录。")
        else:
            st.dataframe(df_user_trades, use_container_width=True, hide_index=True)


# ==============================================================================
# Page 2 (Admin Only): Team Analytics & User Management
# ==============================================================================
elif page == "团队总览与管理" and is_admin:
    _render_page_header("团队绩效与用户管理", "ADMIN CONSOLE · ALL MEMBERS")

    users_list = storage.load_users()
    all_accounts = storage.load_all_accounts()
    all_positions = storage.load_all_positions()
    all_trades_df = storage.load_all_trades(limit=2000)
    df_sig = _cached_latest_signals(storage.backend_name)

    team_df = build_admin_team_summary(
        users=users_list,
        accounts=all_accounts,
        positions_by_user=all_positions,
        all_trades_df=all_trades_df,
        signals_df=df_sig,
        close_df=close_df,
        latest_prices=latest_prices,
        as_of_date=cn_today(),
    )

    active_n = int(team_df["is_active"].sum()) if not team_df.empty else 0
    total_aum = float(team_df["total_equity"].sum()) if not team_df.empty else 0.0
    avg_ret = float(team_df["live_return"].mean()) if not team_df.empty else 0.0
    valid_rates = team_df["execution_rate"].dropna() if not team_df.empty else pd.Series(dtype=float)
    avg_exec = f"{valid_rates.mean():.0%}" if not valid_rates.empty else "—"

    _render_metrics_strip(
        [
            {"label": "活跃用户数", "value": f"{active_n} / {len(team_df)}", "sub": "支持随时增停成员"},
            {"label": "团队合计规模", "value": f"¥{total_aum:,.0f}", "sub": "全部成员总资产"},
            {
                "label": "平均实盘收益率",
                "value": f"{avg_ret:+.2%}",
                "sub": "全员等权均值",
                "value_class": "val-up" if avg_ret > 0 else ("val-down" if avg_ret < 0 else ""),
            },
            {"label": "平均信号执行率", "value": avg_exec, "sub": "按真实交易日匹配", "value_class": "val-gold"},
        ]
    )

    tab_overview, tab_create, tab_manage = st.tabs(["全员绩效与执行排行", "新增用户", "密码重置与启停"])

    with tab_overview:
        if team_df.empty:
            st.info("暂无用户数据。")
        else:
            show_team = pd.DataFrame(
                {
                    "用户": team_df["display_name"] + " (" + team_df["user_id"] + ")",
                    "角色": team_df["role"],
                    "状态": team_df["is_active"].map(lambda x: "启用" if x else "停用"),
                    "初始本金": team_df["initial_capital"].map(lambda x: f"¥{x:,.0f}"),
                    "当前总资产": team_df["total_equity"].map(lambda x: f"¥{x:,.0f}"),
                    "实盘收益率": team_df["live_return"].map(lambda x: f"{x:+.2%}"),
                    "最大回撤": team_df["max_drawdown"].map(lambda x: f"{x:.2%}"),
                    "持仓数": team_df["position_count"],
                    "成交笔数": team_df["trade_count"],
                    "信号执行率": team_df["execution_rate"].map(lambda x: f"{x:.0%}" if pd.notna(x) else "—"),
                    "平均滑点": team_df["avg_slippage_bps"].map(lambda x: f"{x:+.1f} bps" if pd.notna(x) else "—"),
                }
            )
            st.dataframe(show_team, use_container_width=True, hide_index=True)

            if len(team_df) >= 2:
                sorted_t = team_df.sort_values("live_return", ascending=True)
                fig_team = go.Figure(
                    go.Bar(
                        y=sorted_t["display_name"] + " (" + sorted_t["user_id"] + ")",
                        x=sorted_t["live_return"] * 100.0,
                        orientation="h",
                        marker_color=["#E05A5A" if r >= 0 else "#36B37E" for r in sorted_t["live_return"]],
                        text=sorted_t["live_return"].map(lambda r: f"{r:+.2%}"),
                        textposition="outside",
                        textfont={"color": "#98A0AE", "family": "IBM Plex Mono", "size": 11},
                    )
                )
                fig_team = _style_plotly(fig_team, "全员实盘收益率对比 (%)", "", height=max(240, 46 * len(team_df)))
                fig_team.update_layout(showlegend=False, margin={"l": 16, "r": 64, "t": 46, "b": 24})
                st.plotly_chart(fig_team, use_container_width=True)

    with tab_create:
        with st.form("create_user_form", border=True):
            u1, u2, u3 = st.columns(3)
            new_uid = u1.text_input("登录用户名（英文/数字）", placeholder="如 alice")
            new_name = u2.text_input("显示昵称", placeholder="如 张三")
            new_role = u3.selectbox("角色", ["user", "admin"])
            u4, u5 = st.columns(2)
            new_pwd = u4.text_input("初始密码（至少4位）", type="password")
            new_cap = u5.number_input("初始本金 (¥)", min_value=1000.0, step=10000.0, value=100000.0)
            if st.form_submit_button("创建用户", type="primary", use_container_width=True):
                try:
                    auth_service.create_user(
                        user_id=new_uid,
                        display_name=new_name,
                        password=new_pwd,
                        role=new_role,
                        initial_capital=float(new_cap),
                    )
                    st.cache_data.clear()
                    st.success(f"用户 `{new_uid}` 已创建，初始本金 ¥{new_cap:,.0f}")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))

    with tab_manage:
        if not users_list:
            st.caption("暂无用户。")
        else:
            u_map = {u.user_id: u for u in users_list}
            target_uid = st.selectbox(
                "选择要管理的用户",
                list(u_map.keys()),
                format_func=lambda k: f"{u_map[k].display_name} ({k}) · {'启用' if u_map[k].is_active else '停用'}",
            )
            mc1, mc2 = st.columns(2)
            with mc1:
                with st.form("reset_pwd_form", border=True):
                    rpwd = st.text_input("新密码（至少4位）", type="password")
                    if st.form_submit_button("重置密码", use_container_width=True):
                        try:
                            auth_service.reset_password(target_uid, rpwd)
                            st.success(f"已重置 `{target_uid}` 的登录密码")
                        except Exception as exc:  # noqa: BLE001
                            st.error(str(exc))
            with mc2:
                with st.form("toggle_active_form", border=True):
                    cur_active = u_map[target_uid].is_active
                    st.caption(f"当前状态：{'启用中' if cur_active else '已停用'}")
                    btn_label = "停用该账号" if cur_active else "重新启用该账号"
                    if st.form_submit_button(btn_label, use_container_width=True):
                        if target_uid == auth_user.user_id and cur_active:
                            st.error("不能停用当前登录的管理员自己。")
                        else:
                            auth_service.set_active(target_uid, not cur_active)
                            st.cache_data.clear()
                            st.success(f"已更新 `{target_uid}` 状态")
                            st.rerun()


# ==============================================================================
# Page 3: System Status & Data Source Health
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
