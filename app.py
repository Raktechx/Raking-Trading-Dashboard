"""Raking the Trader - online dashboard (no settings, fully automatic).

The GitHub scanner (scan_once.py) updates data/signals.json, data/live_state.json and
data/signal_log.xlsx every 5 minutes; this page only shows them, refreshing itself every 60 s.
"""
from __future__ import annotations

import json
import time

import pandas as pd
import requests
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

import importlib

from raking import analysis, config, store

# The cloud host keeps imported modules in memory between code updates; reload them so
# a new app.py never runs against an old copy of the raking package.
for _m in (config, store, analysis):
    importlib.reload(_m)
from raking.config import STRATEGY as S  # noqa: E402

st.set_page_config(page_title=config.APP_NAME, page_icon="📈", layout="wide", initial_sidebar_state="collapsed")
st_autorefresh(interval=60_000, key="auto")
st.markdown("""
<style>
:root{--bg:#0b0f17;--panel:#111827;--panel2:#0f1623;--line:#1f2a3a;--txt:#e5e7eb;--mut:#8b97a8;--up:#16c784;--dn:#ea3943;--acc:#3b82f6;--warn:#f5c542}
html,body,[data-testid="stAppViewContainer"]{background:var(--bg)}
[data-testid="stHeader"]{background:transparent}
[data-testid="stSidebar"], [data-testid="collapsedControl"] {display:none}
.block-container{padding-top:.6rem;max-width:1500px}
*{font-variant-numeric:tabular-nums}
h3{font-size:1.02rem!important;letter-spacing:.04em;text-transform:uppercase;color:#cbd5e1!important;border-bottom:1px solid var(--line);padding-bottom:6px;margin-top:1.2rem!important}
.topbar{display:flex;align-items:center;justify-content:space-between;background:linear-gradient(90deg,#0f1a2e,#0b0f17);border:1px solid var(--line);border-radius:10px;padding:10px 16px;margin-bottom:10px}
.brand{font-size:1.15rem;font-weight:800;letter-spacing:.06em;color:#fff}.brand span{color:var(--acc)}
.pill{display:inline-block;padding:3px 10px;border-radius:999px;font-size:.75rem;font-weight:700;margin-left:6px;border:1px solid var(--line);background:var(--panel2);color:var(--mut)}
.pill.on{color:var(--up);border-color:#14532d;background:#052e1a}.pill.off{color:var(--mut)}
.kpis{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px;margin-bottom:6px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.kpi .l{font-size:.68rem;color:var(--mut);text-transform:uppercase;letter-spacing:.08em}
.kpi .v{font-size:1.35rem;font-weight:800;color:var(--txt);margin-top:2px;white-space:nowrap}
.kpi .s{font-size:.72rem;color:var(--mut)}
.up{color:var(--up)!important}.dn{color:var(--dn)!important}
.card{border-radius:10px;padding:12px 16px;margin-bottom:10px;border:1px solid var(--line);background:var(--panel)}
.ce{border-left:4px solid var(--up)}.pe{border-left:4px solid var(--dn)}.wait{border-left:4px solid #475569}
.big{font-size:1.05rem;font-weight:700}.muted{color:var(--mut);font-size:.82rem}
.lv{display:inline-block;margin-right:16px;margin-top:6px}.lv b{font-size:1.05rem}
.ok{color:var(--up)}.no{color:#64748b}.warn{color:var(--warn)}.tm{font-size:.68rem;color:var(--mut)}
.tag{display:inline-block;padding:1px 8px;border-radius:6px;background:#1f2937;font-size:.75rem;margin-left:6px}
.ts{display:inline-block;padding:2px 10px;border-radius:6px;background:#0f2a1f;color:var(--up);font-weight:600}
.tops,.blot{width:100%;border-collapse:collapse;font-size:.8rem}
.tops th,.tops td,.blot th,.blot td{padding:5px 6px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
.tops th,.blot th{color:var(--mut);font-weight:600;font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;background:var(--panel2)}
.blot tr:hover td{background:#131c2b}
[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 12px}
[data-testid="stMetricLabel"]{color:var(--mut)!important;text-transform:uppercase;font-size:.7rem!important;letter-spacing:.06em}
.stTabs [data-baseweb="tab-list"]{gap:4px;border-bottom:1px solid var(--line)}
.stTabs [data-baseweb="tab"]{background:var(--panel2);border-radius:8px 8px 0 0;padding:6px 14px;font-weight:600}
.stTabs [aria-selected="true"]{background:var(--panel);color:#fff}
@media (max-width:900px){.kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.topbar{flex-direction:column;align-items:flex-start;gap:6px}}
</style>""", unsafe_allow_html=True)


# Read the newest scan straight from GitHub (the scanner commits every few minutes), so the page
# never waits for the hosting service to redeploy. Falls back to the copy bundled with the app.
RAW = "https://raw.githubusercontent.com/Raktechx/Raking-Trading-Dashboard/{}/data/"


@st.cache_data(ttl=75, show_spinner=False)
def head_sha() -> str:
    """Newest commit id. A file read by commit id skips GitHub's 5-minute file cache."""
    try:
        r = requests.get("https://api.github.com/repos/Raktechx/Raking-Trading-Dashboard/commits/main",
                         headers={"Accept": "application/vnd.github.sha"}, timeout=5)
        if r.ok and len(r.text.strip()) == 40:
            return r.text.strip()
    except requests.RequestException:
        pass
    return "main"


@st.cache_data(ttl=20, show_spinner=False)
def fetch_json(name: str, bucket: int):
    try:
        r = requests.get(RAW.format(head_sha()) + name, timeout=8)
        if r.ok:
            return r.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def load_state() -> dict:
    live = fetch_json("live_state.json", int(time.time() // 30))
    if isinstance(live, dict):
        return live
    try:
        return json.loads((config.DATA_DIR / "live_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_signals() -> dict:
    live = fetch_json("signals.json", int(time.time() // 30))
    return live if isinstance(live, dict) else store.load()


saved = load_signals()
state = load_state()
now = pd.Timestamp.now(tz=config.TZ)
today = str(now.date())




def signal_card(s: dict) -> None:
    cls = "ce" if s["side"] == "CE" else "pe"
    res = s.get("result") or "OPEN"
    badge = {"TP": "🎯 Target hit", "SL": "🛑 SL hit", "TIME": f"⏱️ Closed {S['exit_time']} ({s.get('r')}R)",
             "OPEN": "🟡 Trade open"}.get(res, res)
    qline = ""
    if s.get("grade"):
        n = s.get("score_notes") or {}
        col = {"A": "#16c784", "B": "#f5c542", "C": "#ea3943"}.get(s["grade"], "#999")
        notes = "".join(f"<div class='muted'>➕ {x}</div>" for x in n.get("plus", [])) + \
            "".join(f"<div class='muted'>➖ {x}</div>" for x in n.get("minus", []))
        qline = (f"<div style='margin-top:6px'><span class='ts' style='border:1px solid {col};color:{col}'>"
                 f"Grade {s['grade']} · {s['score']}/100</span>{notes}</div>")
    pb_tag = "<span class='tag'>↩️ Pullback</span>" if s.get("setup") == "Pullback" else ""
    exit_txt = f" · exit ₹{s['opt_exit']} at {s.get('exit_time')}" if s.get("opt_exit") else ""
    st.markdown(
        f"<div class='card {cls}'><div class='big'>BUY {s['option']}<span class='tag'>exp {s.get('expiry')}</span>"
        f"{pb_tag}</div>"
        f"<div style='margin-top:6px'><span class='ts'>⏰ {s['day']} {s['signal_time']}</span> "
        f"<span class='muted'>alert sent {str(s.get('created_at') or '')[11:19]} · {badge}{exit_txt}</span></div>"
        f"{qline}"
        f"<span class='lv'>Entry <b>₹{s.get('opt_entry')}</b></span>"
        f"<span class='lv'>SL <b style='color:#ea3943'>₹{s.get('opt_sl')}</b></span>"
        f"<span class='lv'>Target <b style='color:#16c784'>₹{s.get('opt_tp')}</b></span>"
        + (f"<span class='lv'>1 lot <b>{s['lot_size']} qty</b></span>"
           f"<span class='lv'>Capital/lot <b>₹{float(s['opt_entry']) * s['lot_size']:,.0f}</b></span>"
           if s.get('lot_size') and s.get('opt_entry') else "") +
        
        f"<div style='margin-top:8px;font-size:.88rem'>{'<br>'.join('✅ ' + r for r in s.get('reasons', []))}</div></div>",
        unsafe_allow_html=True)


def checklist(label: str, chk: dict | None) -> None:
    body = "<div class='muted'>No signal yet — waiting for all conditions together.</div>"
    if chk:
        for side in ("CE", "PE"):
            items = " · ".join(f"<span class='{'ok' if v else 'no'}'>{'●' if v else '○'} {k}</span>"
                               for k, v in chk[side].items())
            body += f"<div style='margin-top:4px'><b>{side}</b>: {items}</div>"
        body += f"<div class='muted' style='margin-top:4px'>as of {chk['time']} · price {chk['price']:.1f} · VWAP {chk['vwap']:.1f}</div>"
    st.markdown(f"<div class='card wait'><div class='big'>{label}</div>{body}</div>", unsafe_allow_html=True)


def chart(idx: str, c: dict, sigs: list[dict]) -> go.Figure:
    x = pd.to_datetime(c["bar_close"])
    fig = go.Figure()
    fig.add_candlestick(x=x, open=c["open"], high=c["high"], low=c["low"], close=c["close"], name="Price",
                        increasing_line_color="#16c784", decreasing_line_color="#ea3943")
    fig.add_scatter(x=x, y=c["vwap"], name="VWAP", line=dict(color="#f5c542", width=1.6))
    fig.add_scatter(x=x, y=c["ema_f"], name="EMA 20", line=dict(color="#4ea8ff", width=1))
    fig.add_scatter(x=x, y=c["ema_s"], name="EMA 50", line=dict(color="#c084fc", width=1))
    orb = [i for i, t in enumerate(x) if t.strftime("%H:%M") <= "09:30"]
    if orb:
        for y, nm in ((max(c["high"][i] for i in orb), "Range high"), (min(c["low"][i] for i in orb), "Range low")):
            fig.add_hline(y=y, line_dash="dot", line_color="#94a3b8", annotation_text=nm)
    for s in sigs:
        t = pd.Timestamp(f"{s['day']} {s['signal_time']}")
        fig.add_vline(x=t, line_color="#16c784" if s["side"] == "CE" else "#ea3943", line_dash="dash")
        fig.add_annotation(x=t, y=max(c["high"]), text=f"{s['side']} {s['signal_time']}", showarrow=False,
                           yshift=10, font=dict(color="#ffffff"))
    fig.update_layout(title=f"{config.label(idx)} · 5-min (future price)", height=380, template="plotly_dark",
                      margin=dict(l=10, r=10, t=40, b=10), xaxis_rangeslider_visible=False,
                      legend=dict(orientation="h", y=1.12))
    return fig


# ------------------------------------------------------------------ all books: index, stocks, crude (MCX)
mcx = fetch_json("mcx_paper.json", int(time.time() // 30)) or {}
lv = mcx.get("live") or {}
todays = store.today_signals(saved, today)


def _crude_net(t: dict) -> float:
    oe, ox, lot = t.get("opt_entry"), t.get("opt_exit"), t.get("lot")
    if oe is not None and ox is not None and lot:
        v = analysis.net_pnl({"opt_entry": oe, "opt_exit": ox, "lot_size": lot, "opt_points": round(ox - oe, 2)})
        if v is not None:
            return v
    return float(t.get("opt_pnl_lot") or 0)


book = []      # every closed trade: one row per trade, 1 lot, after costs
for s in saved.values():
    if s.get("result") in analysis.CLOSED and not s.get("blocked"):
        book.append({"day": s["day"], "time": s.get("signal_time", ""), "exit": s.get("exit_time") or "",
                     "book": "Index" if s["index"] in config.INDICES else "Stocks",
                     "inst": config.label(s["index"]), "option": s.get("option"), "side": s.get("side"),
                     "entry": s.get("opt_entry"), "out": s.get("opt_exit"), "result": s.get("result"),
                     "r": float(s.get("r") or 0), "pnl": analysis.net_pnl(s) or 0.0, "lot": s.get("lot_size")})
for t in mcx.get("trades", []):
    book.append({"day": t["id"][:10], "time": t.get("t", ""), "exit": t.get("exit_t") or "", "book": "Crude",
                 "inst": "Crude Oil", "option": t.get("sym") or ("CE" if t.get("side", 0) > 0 else "PE"),
                 "side": "CE" if t.get("side", 0) > 0 else "PE", "entry": t.get("opt_entry"),
                 "out": t.get("opt_exit"), "result": {"Target": "TP"}.get(t.get("why"), t.get("why")),
                 "r": float(t.get("R") or 0), "pnl": _crude_net(t), "lot": t.get("lot")})
bk = pd.DataFrame(book, columns=["day", "time", "exit", "book", "inst", "option", "side", "entry", "out",
                                 "result", "r", "pnl", "lot"])

run = []       # every open trade with live premium
for s in todays:
    if s.get("result") == "OPEN" and s.get("opt_entry"):
        e, px, lot = float(s["opt_entry"]), s.get("ltp"), s.get("lot_size")
        run.append({"Book": "Index" if s["index"] in config.INDICES else "Stocks", "Instrument": config.label(s["index"]),
                    "Option": s["option"], "Since": s["signal_time"], "Entry": e, "LTP": px, "SL": s.get("opt_sl"),
                    "Target": s.get("opt_tp"), "Qty": lot,
                    "P&L": round((float(px) - e) * lot) if px and lot else None, "Updated": s.get("ltp_time") or "-"})
_done = {t["id"] for t in mcx.get("trades", [])}
for k, o in (mcx.get("opt") or {}).items():
    if k.startswith(today) and k not in _done:
        px = o.get("now")
        run.append({"Book": "Crude", "Instrument": "Crude Oil", "Option": o["sym"], "Since": o.get("time", "-"),
                    "Entry": o["ltp"], "LTP": px, "SL": o.get("sl"), "Target": o.get("tp"), "Qty": o["lot"],
                    "P&L": round((px - o["ltp"]) * o["lot"]) if px else None, "Updated": o.get("now_t") or "-"})

td = bk[bk["day"] == today]
open_pnl = sum(r["P&L"] or 0 for r in run)
eq_daily = bk.groupby("day")["pnl"].sum().sort_index()
cum = eq_daily.cumsum()
max_dd = float((cum - cum.cummax()).min()) if len(cum) else 0.0
gain, loss = bk.loc[bk["pnl"] > 0, "pnl"].sum(), -bk.loc[bk["pnl"] <= 0, "pnl"].sum()
pf = gain / loss if loss > 0 else None


def _c(v: float) -> str:
    return "up" if v > 0 else "dn" if v < 0 else ""


def _rs(v: float) -> str:
    return ("+" if v > 0 else "-" if v < 0 else "") + f"₹{abs(v):,.0f}"


# ------------------------------------------------------------------ top bar
nse_on = "open" in str(state.get("market", "")).lower()
mcx_on = bool(lv.get("updated")) and (9, 0) <= (now.hour, now.minute) <= (23, 30) and now.weekday() < 5
h1, h2 = st.columns([5, 1])
h1.markdown(
    f"<div class='topbar'><div class='brand'>RAKING<span>·</span>TRADER <span style='font-size:.7rem;color:#8b97a8;"
    f"font-weight:600;letter-spacing:.1em'>OPTIONS DESK</span></div><div>"
    f"<span class='pill {'on' if nse_on else 'off'}'>● NSE {'OPEN' if nse_on else 'CLOSED'}</span>"
    f"<span class='pill {'on' if mcx_on else 'off'}'>● MCX {'OPEN' if mcx_on else 'CLOSED'}</span>"
    f"<span class='pill'>🕒 {now:%a %d %b · %H:%M} IST</span>"
    f"<span class='pill'>Scan {str(state.get('updated') or state.get('checked_at') or '—')[11:16] or '—'}</span>"
    f"<span class='pill'>MCX {lv.get('updated') or '—'}</span></div></div>", unsafe_allow_html=True)
if h2.button("🔄 Refresh", width="stretch"):
    fetch_json.clear()
    head_sha.clear()
    st.rerun()
bad = {k: v for k, v in (state.get("status") or {}).items() if not str(v).startswith("OK")}
for k, v in bad.items():
    st.warning(f"{config.label(k)}: {v}")

t_pnl = float(td["pnl"].sum())
t_win = (td["pnl"] > 0).mean() * 100 if len(td) else None
a_win = (bk["pnl"] > 0).mean() * 100 if len(bk) else None
tiles = [
    ("Day P&L (net)", _rs(t_pnl), _c(t_pnl), f"{len(td)} closed · {td['r'].sum():+.1f}R"),
    ("Open P&L", _rs(open_pnl), _c(open_pnl), f"{len(run)} running"),
    ("Day win rate", f"{t_win:.0f}%" if t_win is not None else "—", "",
     f"{int((td['pnl'] > 0).sum())}W / {int((td['pnl'] <= 0).sum())}L"),
    ("Total P&L (net)", _rs(float(bk["pnl"].sum())), _c(float(bk["pnl"].sum())), f"{len(bk)} trades · {bk['r'].sum():+.1f}R"),
    ("Win rate", f"{a_win:.0f}%" if a_win is not None else "—", "", "all books"),
    ("Profit factor", f"{pf:.2f}" if pf else "—", "", "gross win / gross loss"),
    ("Max drawdown", _rs(max_dd), "dn" if max_dd < 0 else "", "daily equity"),
]
st.markdown("<div class='kpis'>" + "".join(
    f"<div class='kpi'><div class='l'>{l}</div><div class='v {c}'>{v}</div><div class='s'>{s}</div></div>"
    for l, v, c, s in tiles) + "</div>", unsafe_allow_html=True)
st.caption("All P&L is for 1 lot per trade after brokerage, taxes and 1% slippage (crude: before costs where the "
           "premium was not recorded). Paper-trading record, not advice.")

tab_desk, tab_idx, tab_stk, tab_crude, tab_log, tab_rules = st.tabs(
    ["⚡ Desk", "📊 Indices", "🏢 Stocks", "🛢 Crude Oil", "📒 Journal & Analytics", "📘 Strategy"])

# ------------------------------------------------------------------ desk: positions, today's blotter, equity, books
with tab_desk:
    st.markdown("### Open positions")
    if run:
        rows = "".join(
            f"<tr><td>{r['Book']}</td><td><b>{r['Option']}</b></td><td>{r['Since']}</td><td>{r['Entry']:.1f}</td>"
            f"<td><b>{r['LTP'] if r['LTP'] is not None else '—'}</b></td><td class='dn'>{r['SL']}</td>"
            f"<td class='up'>{r['Target']}</td><td>{r['Qty']}</td>"
            f"<td class='{_c(r['P&L'] or 0)}'><b>{_rs(r['P&L'] or 0)}</b></td><td class='tm'>{r['Updated']}</td></tr>"
            for r in run)
        st.markdown("<table class='blot'><tr><th>Book</th><th>Contract</th><th>Since</th><th>Entry</th><th>LTP</th>"
                    "<th>SL</th><th>Target</th><th>Qty</th><th>P&L (1 lot)</th><th>Upd</th></tr>" + rows + "</table>",
                    unsafe_allow_html=True)
        st.caption("LTP refreshes every minute (index/stock) and after every 5-min candle (crude).")
    else:
        st.markdown("<div class='card wait'><div class='muted'>Flat — no open positions.</div></div>",
                    unsafe_allow_html=True)

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("### Equity curve (all books, net)")
        if len(cum):
            fig = go.Figure()
            fig.add_bar(x=eq_daily.index, y=eq_daily.values, name="Day P&L",
                        marker_color=["#16c784" if v > 0 else "#ea3943" for v in eq_daily.values], opacity=.55)
            fig.add_scatter(x=cum.index, y=cum.values, name="Cumulative", line=dict(color="#3b82f6", width=2.5))
            fig.update_layout(height=280, template="plotly_dark", paper_bgcolor="#111827", plot_bgcolor="#111827",
                              margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.12),
                              yaxis_title="₹")
            st.plotly_chart(fig, width="stretch", key="eq_all")
        else:
            st.caption("Builds up after the first closed trade.")
    with c2:
        st.markdown("### Books")
        rows = []
        for name in ("Index", "Stocks", "Crude"):
            b_all, b_td = bk[bk["book"] == name], td[td["book"] == name]
            rows.append({"Book": name, "Today": round(float(b_td["pnl"].sum())), "Trades": len(b_all),
                         "Win %": round((b_all["pnl"] > 0).mean() * 100) if len(b_all) else None,
                         "R": round(float(b_all["r"].sum()), 1), "Total ₹": round(float(b_all["pnl"].sum()))})
        bdf = pd.DataFrame(rows)
        st.dataframe(bdf.style.map(lambda v: "color:#16c784" if isinstance(v, (int, float)) and v > 0 else
                                   "color:#ea3943" if isinstance(v, (int, float)) and v < 0 else "",
                                   subset=["Today", "Total ₹"]), hide_index=True, width="stretch")

    st.markdown("### Today's executions")
    if len(td):
        ic = {"TP": "🎯", "SL": "🛑"}
        rows = "".join(
            f"<tr><td>{r.time}→{r.exit}</td><td>{r.book}</td><td><b>{r.option}</b></td>"
            f"<td class='{'up' if r.side == 'CE' else 'dn'}'>{r.side}</td>"
            f"<td>{'' if pd.isna(r.entry) or r.entry is None else r.entry}</td>"
            f"<td>{'' if pd.isna(r.out) or r.out is None else r.out}</td>"
            f"<td>{ic.get(r.result, '⏱️')} {r.result}</td><td class='{_c(r.r)}'>{r.r:+.1f}R</td>"
            f"<td class='{_c(r.pnl)}'><b>{_rs(r.pnl)}</b></td></tr>"
            for r in td.sort_values("time", ascending=False).itertuples())
        st.markdown("<table class='blot'><tr><th>Time</th><th>Book</th><th>Contract</th><th>Side</th><th>Entry</th>"
                    "<th>Exit</th><th>Result</th><th>R</th><th>Net P&L</th></tr>" + rows + "</table>",
                    unsafe_allow_html=True)
    else:
        st.markdown("<div class='card wait'><div class='muted'>No closed trade today yet.</div></div>",
                    unsafe_allow_html=True)
    nblk = len([s for s in store.today_signals(saved, today, include_blocked=True) if s.get("blocked")])
    st.caption(f"🛡️ Quality pre-check: only grade A signals are alerted. Blocked today: {nblk} (tracked in the Journal "
               "and the Excel log).")

# ------------------------------------------------------------------ indices
with tab_idx:
    cols = st.columns(len(config.INDICES))
    for col, idx in zip(cols, config.INDICES):
        with col:
            st.markdown(f"### {config.label(idx)}")
            sigs = store.today_signals(saved, today, idx)
            for s in reversed(sigs):
                signal_card(s)
            if not any(s.get("result") == "OPEN" for s in sigs):
                checklist("Live checklist", (state.get("checks") or {}).get(idx))

with tab_crude:
    # ---- MCX Crude Oil (state written by mcx_paper.py every 5 min)
    mcx = fetch_json("mcx_paper.json", int(time.time() // 30)) or {}
    lv = mcx.get("live") or {}
    st.markdown("### 🛢 Crude Oil (MCX)")
    if lv:
        win = lv.get("window", "–")
        live_win = not str(win).startswith("Waiting")
        st.markdown(f"{'🟢 **Signal window open: ' + win + '**' if live_win else '⏳ ' + win} · "
                    f"updated {lv.get('updated')} IST")
        k = st.columns(5)
        k[0].metric(lv.get("symbol", "CRUDEOIL"), f"₹{lv['price']:,.0f}",
                    f"{lv['chg_pct']:+.2f}%" if lv.get("chg_pct") is not None else None)
        k[1].metric("Day high / low", f"{lv.get('day_high') or 0:,.0f} / {lv.get('day_low') or 0:,.0f}")
        k[2].metric("VWAP", f"{lv['vwap']:,.0f}" if lv.get("vwap") else "–",
                    ("Above" if lv.get("above_vwap") else "Below") if lv.get("above_vwap") is not None else None)
        k[3].metric("Trend (EMA 20/50)", ("📈 " if lv.get("trend") == "Up" else "📉 ") + lv.get("trend", "–"))
        k[4].metric("Bias for a breakout", "CALL (CE)" if lv.get("trend") == "Up" and lv.get("above_vwap")
                    else "PUT (PE)" if lv.get("trend") == "Down" and lv.get("above_vwap") is False else "Mixed")

        trades = mcx.get("trades", [])
        ct = [t for t in trades if t["id"].startswith(today)]
        opt = mcx.get("opt") or {}
        opn = {k2: o for k2, o in opt.items() if k2.startswith(today) and k2 not in {t["id"] for t in ct}}
        pnl = lambda ts: sum(t.get("opt_pnl_lot") or 0 for t in ts)
        live_pnl = sum(((o.get("now") or o["ltp"]) - o["ltp"]) * o["lot"] for o in opn.values())
        wins = [t for t in trades if t.get("R", 0) > 0]
        s = st.columns(4)
        s[0].metric("Today closed", f"{len(ct)} · ₹{pnl(ct):+,.0f}", f"{sum(t['R'] for t in ct):+.1f}R" if ct else None)
        s[1].metric("Running now", f"{len(opn)} · ₹{live_pnl:+,.0f}", "live P&L / lot" if opn else None)
        s[2].metric("All-time P&L (1 lot)", f"₹{pnl(trades):+,.0f}", f"{sum(t['R'] for t in trades):+.1f}R")
        s[3].metric("All-time win rate", f"{len(wins) / len(trades) * 100:.0f}%" if trades else "–",
                    f"{len(trades)} trades")

        cdl = lv.get("candles") or []
        if cdl:
            if len(cdl[0]) == 5:
                cd = pd.DataFrame(cdl, columns=["time", "open", "high", "low", "close"])
                fig = go.Figure(go.Candlestick(x=cd.time, open=cd.open, high=cd.high, low=cd.low, close=cd.close,
                                               name="Crude", increasing_line_color="#26a69a",
                                               decreasing_line_color="#ef5350"))
            else:
                cd = pd.DataFrame(cdl, columns=["time", "close"])
                fig = go.Figure(go.Scatter(x=cd.time, y=cd.close, mode="lines", line=dict(color="#f0b90b")))
            if lv.get("vwap"):
                fig.add_hline(y=lv["vwap"], line_dash="dot", line_color="#9e9e9e", annotation_text="VWAP")
            for n, (hi, lo) in (lv.get("ranges") or {}).items():
                fig.add_hline(y=hi, line_dash="dash", line_color="#42a5f5", annotation_text=f"{n} high {hi:,.0f}")
                fig.add_hline(y=lo, line_dash="dash", line_color="#ab47bc", annotation_text=f"{n} low {lo:,.0f}",
                              annotation_position="bottom right")
            for e in lv.get("events") or []:
                up = e["side"] > 0
                fig.add_trace(go.Scatter(x=[e["t"]], y=[e["entry"]], mode="markers+text",
                                         marker=dict(symbol="triangle-up" if up else "triangle-down", size=14,
                                                     color="#26a69a" if up else "#ef5350"),
                                         text=["CE" if up else "PE"], textposition="top center", showlegend=False))
                if e.get("exit_t"):
                    fig.add_trace(go.Scatter(x=[e["exit_t"]], y=[e["exit"]], mode="markers+text",
                                             marker=dict(symbol="x", size=11, color="#ffffff"),
                                             text=[e.get("why")], textposition="bottom center", showlegend=False))
                else:
                    fig.add_hline(y=e["sl"], line_color="#ef5350", annotation_text="SL")
                    fig.add_hline(y=e["tg"], line_color="#26a69a", annotation_text="Target")
            fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), template="plotly_dark",
                              xaxis_rangeslider_visible=False, showlegend=False)
            st.plotly_chart(fig, width="stretch")

        for k2, o in opn.items():
            now_px = o.get("now") or o["ltp"]
            p = (now_px - o["ltp"]) * o["lot"]
            with st.container(border=True):
                st.markdown(f"🟡 **RUNNING · BUY {o['sym']}** · {o.get('window', '')} · since {o.get('time', '-')}")
                c = st.columns(5)
                c[0].metric("Entry", f"₹{o['ltp']:.1f}")
                c[1].metric("Live", f"₹{now_px:.1f}", f"at {o.get('now_t', '-')}")
                c[2].metric("SL / Target", f"₹{o.get('sl', 0):.1f} / ₹{o.get('tp', 0):.1f}")
                c[3].metric("Lot qty", f"{o['lot']}")
                c[4].metric("Live P&L / lot", f"₹{p:+,.0f}")
        for t in reversed(ct):
            icon = {"Target": "🎯", "SL": "🛑"}.get(t.get("why"), "⏱️")
            with st.container(border=True):
                st.markdown(f"{icon} **{t.get('sym') or ('CE' if t['side'] > 0 else 'PE')}** · {t.get('window', '')} · "
                            f"{t.get('t', '')} → {t.get('exit_t', '')} · **{t.get('why')}** · {t['R']:+.1f}R")
                if t.get("opt_entry") is not None:
                    st.caption(f"Entry ₹{t['opt_entry']:.1f} → Exit ₹{t.get('opt_exit', 0):.1f} · Lot {t.get('lot')} qty · "
                               f"P&L ₹{t.get('opt_pnl_lot', 0):+,.0f} per lot · Future {t['entry']:.0f} → {t['exit']:.0f}")
        if not ct and not opn:
            st.caption("No crude trade today yet. Signals only come in the 09:00-09:30 and 19:30-20:00 breakout windows.")

        if trades:
            tdf = pd.DataFrame(trades)
            tdf["Date"] = tdf["id"].str.slice(0, 10)
            show = {"Date": "Date", "window": "Window", "sym": "Option", "t": "Entry time", "exit_t": "Exit time",
                    "opt_entry": "Entry ₹", "opt_sl": "SL ₹", "opt_tp": "Target ₹", "opt_exit": "Exit ₹",
                    "lot": "Lot qty", "opt_pnl_lot": "P&L/lot ₹", "why": "Result", "R": "R"}
            tdf = tdf[[c for c in show if c in tdf]].rename(columns=show).iloc[::-1]
            with st.expander(f"Crude Oil trade log ({len(tdf)})"):
                st.dataframe(tdf, hide_index=True, width="stretch")
    else:
        st.caption("Waiting for first update (MCX opens 09:00 IST).")

with tab_stk:
    st.markdown("### Stock options")
    top = state.get("top_stocks") or []
    if top:
        st.caption(f"Top {len(top)} F&O stocks right now (ranked by strength, refreshed every 5 minutes) "
                   "- each one gets the full signal check. A signal needs every rule plus grade A:")
        today_by = {}
        for s in store.today_signals(saved, today, include_blocked=True):
            today_by[s["index"]] = s   # latest signal of the day per stock

        def _grade(sym: str) -> str:
            s = today_by.get(sym)
            if not s:
                return "<span class='no'>—</span>"
            g = s.get("grade") or "?"
            col = {"A": "ok", "B": "warn", "C": "dn"}.get(g, "no")
            what = "⛔" if s.get("blocked") else {"TP": "🎯", "SL": "🛑", "OPEN": "✅"}.get(s.get("result"), "✅")
            return f"<b class='{col}'>{g}</b> {what}<br><span class='tm'>{s.get('signal_time', '')}</span>"

        def _rules(r: dict) -> str:
            n = r.get("rules")
            cur = "<span class='no'>—</span>" if n is None else f"<span class='{'ok' if n == 3 else 'no'}'>{n}/3</span>"
            at = r.get("rules3_at")
            return cur + (f"<br><span class='tm'>3/3 {at}</span>" if at else "")

        def _vol(r: dict) -> str:
            v = r.get("vol_x")
            return "<span class='no'>—</span>" if v is None else f"<span class='{'ok' if v > 1 else 'no'}'>{v}×</span>"

        rows = "".join(
            f"<tr><td>{i + 1}</td><td><b>{r['symbol']}</b></td>"
            f"<td class='{'ok' if r['side'] == 'CE' else 'dn'}'>{'CE ▲' if r['side'] == 'CE' else 'PE ▼'}</td>"
            f"<td>{r['ltp']:,.1f}</td><td>{r['day_pct']:+.2f}%</td><td><b>{r['strength']:.0f}</b></td>"
            f"<td>{_vol(r)}</td><td>{_rules(r)}</td><td>{_grade(r['symbol'])}</td></tr>"
            for i, r in enumerate(top))
        st.markdown("<table class='tops'><tr><th>#</th><th>Stock</th><th>Side</th><th>Price</th>"
                    f"<th>Day</th><th>Str</th><th>Vol</th><th>Rules</th><th>Grade</th></tr>{rows}</table>"
                    "<div class='muted' style='margin-top:4px'>Vol = latest candle volume vs 20-candle average. "
                    "Rules = trend + range break + volume met now; time below = first time all 3 lined up today. "
                    "Then the option chain is checked and the signal gets a grade (time = signal time): "
                    "<b class='ok'>A</b> = sent to Telegram, B/C ⛔ = blocked (logged only).</div>",
                    unsafe_allow_html=True)
    else:
        st.caption("Being checked now: " + (", ".join(state.get("candidates") or []) or "—"))
    stock_sigs = [s for s in todays if s["index"] not in config.INDICES]
    for s in reversed(stock_sigs):
        signal_card(s)
    if not stock_sigs:
        st.markdown("<div class='card wait'><div class='muted'>No stock option signal yet today.</div></div>",
                    unsafe_allow_html=True)

with tab_idx:
    for idx, c in (state.get("charts") or {}).items():
        if c.get("close"):
            st.plotly_chart(chart(idx, c, store.today_signals(saved, today, idx)), width="stretch", key=f"c_{idx}")
    st.caption("Updates by itself: the scanner runs every 5 minutes (Mon–Fri 9:15–3:30) and this page reloads "
               "every 60 seconds. Signal numbers never change once shown.")

# ------------------------------------------------------------------ log & analysis
with tab_log:
    df = analysis.frame(saved)
    if df.empty:
        st.info("No signals yet. Every signal and its result will appear here, with timestamps, as the "
                "live market runs. Paper-trading results build up automatically.")
    else:
        f1, f2, f3 = st.columns(3)
        inst = f1.multiselect("Instrument", sorted(df["instrument"].unique()))
        side = f2.multiselect("Side", ["CE", "PE"])
        days = f3.date_input("Dates", value=(pd.to_datetime(df["day"]).min().date(), now.date()))
        d = df.copy()
        if inst:
            d = d[d["instrument"].isin(inst)]
        if side:
            d = d[d["side"].isin(side)]
        if isinstance(days, tuple) and len(days) == 2:
            d = d[(d["day"] >= str(days[0])) & (d["day"] <= str(days[1]))]
        dall, d = d, analysis.sent(d)
        a, b = analysis.stats(d), analysis.stats(d, "alt_r")
        m = st.columns(6)
        m[0].metric("Closed trades", a["trades"])
        m[1].metric("Win rate", f"{a['win_rate']}%" if a["win_rate"] is not None else "—",
                    help="At 1:2 reward:risk the break-even win rate is about 33%.")
        m[2].metric("Total R", a["total_r"])
        m[3].metric("Average R", a["avg_r"] if a["avg_r"] is not None else "—")
        m[4].metric("Max drawdown", f"{a['max_dd']} R")
        m[5].metric("Profit factor", a["pf"] or "—")
        st.caption(f"Exit test — Method A (current): {a['total_r']} R over {a['trades']} trades · "
                   f"Method B (option-candle SL): {b['total_r']} R over {b['trades']} trades")
        eq = analysis.equity(d)
        if not eq.empty:
            fig = go.Figure(go.Scatter(x=eq["Trade"], y=eq["Cumulative R"], mode="lines+markers",
                                       text=eq["When"], line=dict(color="#16c784")))
            fig.update_layout(height=260, template="plotly_dark", margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_title="Trade #", yaxis_title="Cumulative R")
            st.plotly_chart(fig, width="stretch", key="eq")
        st.markdown("**Quality pre-check** — sent (grade A) vs blocked signals. Blocked ones are never alerted, "
                    "but their result is still tracked to prove the filter works.")
        st.dataframe(analysis.breakdown(dall, "status"), hide_index=True, width="stretch")
        b1, b2 = st.columns(2)
        b1.markdown("**By instrument**")
        b1.dataframe(analysis.breakdown(d, "instrument"), hide_index=True, width="stretch")
        b2.markdown("**By signal hour**")
        b2.dataframe(analysis.breakdown(d, "hour"), hide_index=True, width="stretch")
        b3, b4 = st.columns(2)
        b3.markdown("**By side**")
        b3.dataframe(analysis.breakdown(d, "side"), hide_index=True, width="stretch")
        b4.markdown("**By weekday**")
        b4.dataframe(analysis.breakdown(d, "weekday"), hide_index=True, width="stretch")
        st.markdown("**All signals (newest first)**")
        st.dataframe(dall[[c for c, _ in analysis.COLUMNS]].rename(columns=dict(analysis.COLUMNS)),
                     hide_index=True, width="stretch")
    xl = store.LOG_XLSX
    if xl.exists():
        st.download_button("⬇️ Download Excel log (signals + analysis sheets)", xl.read_bytes(), "signal_log.xlsx")

# ------------------------------------------------------------------ rules
with tab_rules:
    st.markdown(f"""
**Instruments:** Nifty 50, Bank Nifty, Sensex and every NSE F&O stock — {S['candle_minutes']}-minute candles.

**BUY CE — all together:** price above VWAP and EMA20 > EMA50 · ATM call long build-up (premium and OI up) ·
put writing at ATM and 2 strikes below · after 9:30 the 9:15–9:30 high breaks with volume above the 20-candle average ·
SL previous candle low · target 1:2.
**BUY PE:** the mirror image.

**SL size:** never tighter than {S['min_risk_atr']} ATR or {S['min_risk_pct']}% of the price, and the option SL is at
least {S['min_opt_sl_pct']:.0f}% below the entry premium (target = 2× that). Options under ₹{S['min_premium']:.0f} are skipped.

**Limits (practice mode):** no daily cap - every grade A signal is sent; one open trade per instrument at a time ·
no new entry after {S['last_entry']} · open trades closed at {S['exit_time']} · alerts never older than
{S['fresh_minutes']} minutes.

**Exit test:** every trade is also scored with Method B (SL on the option's previous candle) so the paper
record shows which exit earns more.

**Quality grade (0–100):** each signal is also graded on trend strength, breakout candle, volume, OI build-up,
PCR, room to the next big OI wall, option IV, India VIX and broader-market alignment.
**Quality pre-check:** only grade A (score ≥ {S['min_quality']}) is sent to Telegram and shown as a live signal.
Lower grades, or a signal whose quality cannot be checked, are blocked. They're logged with the reason and their
result is still tracked, so the "Sent vs blocked" table shows whether the filter really improves results.

⚠️ Paper-trading tool, not investment advice.
""")
