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
[data-testid="stSidebar"], [data-testid="collapsedControl"] {display:none}
.block-container{padding-top:1rem}
.card{border-radius:14px;padding:14px 18px;margin-bottom:10px;border:1px solid #2a3142;background:#121826}
.ce{border-left:6px solid #16c784}.pe{border-left:6px solid #ea3943}.wait{border-left:6px solid #6b7280}
.big{font-size:1.25rem;font-weight:700}.muted{color:#9aa4b2;font-size:.85rem}
.lv{display:inline-block;margin-right:16px;margin-top:6px}.lv b{font-size:1.15rem}
.ok{color:#16c784}.no{color:#6b7280}.refresh{text-align:right;color:#9aa4b2;font-size:.9rem}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;background:#1f2937;font-size:.8rem;margin-left:6px}
.tops{width:100%;border-collapse:collapse;font-size:.78rem}.tops th,.tops td{padding:3px 3px;border-bottom:1px solid #2a3142;text-align:left;white-space:nowrap}.tops th{color:#9aa4b2;font-weight:600}.dn{color:#ea3943}.warn{color:#f5c542}.tm{font-size:.68rem;color:#9aa4b2}
.ts{display:inline-block;padding:2px 10px;border-radius:10px;background:#0f2a1f;color:#16c784;font-weight:600}
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

# ------------------------------------------------------------------ header
h1, h2 = st.columns([3, 1])
h1.markdown(f"## 📈 {config.APP_NAME}")
h2.markdown(f"<div class='refresh'>Last scan<br><b>{state.get('updated') or state.get('checked_at') or '—'} IST</b>"
            f"<br>{state.get('market', '')}</div>", unsafe_allow_html=True)
if h2.button("🔄 Refresh now", width="stretch"):
    fetch_json.clear()
    head_sha.clear()
    st.rerun()


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


tab_live, tab_log, tab_rules = st.tabs(["🔴 Live signals", "📊 Signal log & analysis", "📘 Strategy"])

# ------------------------------------------------------------------ live
with tab_live:
    bad = {k: v for k, v in (state.get("status") or {}).items() if not str(v).startswith("OK")}
    for k, v in bad.items():
        st.warning(f"{config.label(k)}: {v}")
    todays = store.today_signals(saved, today)
    closed = [s for s in todays if s.get("result") in analysis.CLOSED]

    def _lot(s):
        return analysis.net_pnl(s) or 0.0

    every_closed = [s for s in saved.values() if s.get("result") in analysis.CLOSED and not s.get("blocked")]
    m = st.columns(5)
    m[0].metric("Signals today", len(todays))
    m[1].metric("Won / lost", f"{sum(1 for s in closed if float(s['r']) > 0)} / "
                              f"{sum(1 for s in closed if float(s['r']) <= 0)}")
    m[2].metric("Today's R", round(sum(float(s["r"]) for s in closed), 2) if closed else 0)
    m[3].metric("Today's P&L (net)", f"₹{sum(_lot(s) for s in closed):,.0f}")
    m[4].metric("All-time P&L (net)", f"₹{sum(_lot(s) for s in every_closed):,.0f}",
                help=f"1 lot each, after brokerage, taxes and 1% slippage. {len(every_closed)} closed trades, {sum(float(s['r']) for s in every_closed):+.1f}R")
    nblk = len([s for s in store.today_signals(saved, today, include_blocked=True) if s.get("blocked")])
    st.caption(f"🛡️ Quality pre-check: only grade A signals are shown and alerted. "
               f"Blocked today: {nblk} weaker signal(s) — see the Signal log tab.")

    # ---- every running trade (index, stock, crude) with live premium and live P&L for 1 lot
    _mcx = fetch_json("mcx_paper.json", int(time.time() // 30)) or {}
    _done = {t["id"] for t in _mcx.get("trades", [])}
    run = []
    for s in todays:
        if s.get("result") == "OPEN" and s.get("opt_entry"):
            e, px, lot = float(s["opt_entry"]), s.get("ltp"), s.get("lot_size")
            run.append({"Instrument": config.label(s["index"]), "Option": s["option"], "Since": s["signal_time"],
                        "Entry ₹": e, "Live ₹": px, "SL ₹": s.get("opt_sl"), "Target ₹": s.get("opt_tp"),
                        "Lot qty": lot, "Live P&L/lot ₹": round((float(px) - e) * lot) if px and lot else None,
                        "Updated": s.get("ltp_time") or "-"})
    for k, o in (_mcx.get("opt") or {}).items():
        if k.startswith(today) and k not in _done:
            px = o.get("now")
            run.append({"Instrument": "Crude Oil (MCX)", "Option": o["sym"], "Since": o.get("time", "-"),
                        "Entry ₹": o["ltp"], "Live ₹": px, "SL ₹": o.get("sl"), "Target ₹": o.get("tp"),
                        "Lot qty": o["lot"], "Live P&L/lot ₹": round((px - o["ltp"]) * o["lot"]) if px else None,
                        "Updated": o.get("now_t") or "-"})
    st.markdown("### 🟡 Running trades (live)")
    if run:
        rdf = pd.DataFrame(run)
        st.metric("Open P&L (1 lot each, before costs)", f"₹{rdf['Live P&L/lot ₹'].fillna(0).sum():+,.0f}")
        st.dataframe(rdf.style.map(lambda v: "color:#16c784" if isinstance(v, (int, float)) and v > 0 else
                                   "color:#ea3943" if isinstance(v, (int, float)) and v < 0 else "",
                                   subset=["Live P&L/lot ₹"]), hide_index=True, width="stretch")
        st.caption("Live premium refreshes every minute (index/stock) and every 5 minutes (crude).")
    else:
        st.markdown("<div class='card wait'><div class='muted'>No trade running right now.</div></div>",
                    unsafe_allow_html=True)
    cols = st.columns(len(config.INDICES))
    for col, idx in zip(cols, config.INDICES):
        with col:
            st.markdown(f"### {config.label(idx)}")
            sigs = store.today_signals(saved, today, idx)
            for s in reversed(sigs):
                signal_card(s)
            if not any(s.get("result") == "OPEN" for s in sigs):
                checklist("Live checklist", (state.get("checks") or {}).get(idx))

    # ---- MCX Crude Oil (state written by mcx_paper.py every 5 min)
    mcx = fetch_json("mcx_paper.json", int(time.time() // 30)) or {}
    lv = mcx.get("live") or {}
    st.markdown("### 🛢 Crude Oil (MCX)")
    if lv:
        k = st.columns(5)
        k[0].metric(lv.get("symbol", "CRUDEOIL"), f"₹{lv['price']:,.0f}",
                    f"{lv['chg_pct']:+.2f}%" if lv.get("chg_pct") is not None else None)
        k[1].metric("Day high / low", f"{lv.get('day_high') or 0:,.0f} / {lv.get('day_low') or 0:,.0f}")
        k[2].metric("VWAP", f"{lv['vwap']:,.0f}" if lv.get("vwap") else "–",
                    ("Above" if lv.get("above_vwap") else "Below") if lv.get("above_vwap") is not None else None)
        k[3].metric("Trend (EMA 20/50)", lv.get("trend", "–"))
        k[4].metric("Signal window", lv.get("window", "–"))
        if lv.get("candles"):
            cd = pd.DataFrame(lv["candles"], columns=["time", "price"])
            fig = go.Figure(go.Scatter(x=cd.time, y=cd.price, mode="lines", line=dict(color="#f0b90b")))
            if lv.get("vwap"):
                fig.add_hline(y=lv["vwap"], line_dash="dot", line_color="#888", annotation_text="VWAP")
            fig.update_layout(height=220, margin=dict(l=10, r=10, t=10, b=10), template="plotly_dark")
            st.plotly_chart(fig, width="stretch")
        ct = [t for t in mcx.get("trades", []) if t["id"].startswith(today)]
        opn = [k2 for k2 in (mcx.get("opt") or {}) if k2.startswith(today) and k2 not in {t["id"] for t in ct}]
        st.caption(f"Updated {lv.get('updated')} IST · Today: {len(ct)} closed, {len(opn)} open · "
                   f"All-time: {len(mcx.get('trades', []))} trades, "
                   f"{sum(t['R'] for t in mcx.get('trades', [])):+.1f}R")
        if mcx.get("trades"):
            tdf = pd.DataFrame(mcx["trades"])
            tdf["Date"] = tdf["id"].str.slice(0, 10)
            show = {"Date": "Date", "window": "Window", "sym": "Option", "t": "Entry time", "exit_t": "Exit time",
                    "opt_entry": "Entry ₹", "opt_exit": "Exit ₹", "lot": "Lot qty", "opt_pnl_lot": "P&L/lot ₹",
                    "why": "Result", "R": "R"}
            tdf = tdf[[c for c in show if c in tdf]].rename(columns=show).iloc[::-1]
            with st.expander(f"Crude Oil trade log ({len(tdf)})"):
                st.dataframe(tdf, hide_index=True, width="stretch")
    else:
        st.caption("Waiting for first update (MCX opens 09:00 IST).")

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
