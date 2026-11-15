"""Raking the Trader - dashboard.  Start with "Start Dashboard.bat" (double-click)."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from raking import backtest, config, data, demo, live, notify, store
from raking.config import STRATEGY as S
from raking.upstox import Upstox

st.set_page_config(page_title=config.APP_NAME, page_icon="📈", layout="wide")
st.markdown("""
<style>
.block-container{padding-top:1.1rem}
.card{border-radius:14px;padding:14px 18px;margin-bottom:10px;border:1px solid #2a3142;background:#121826}
.ce{border-left:6px solid #16c784}.pe{border-left:6px solid #ea3943}.wait{border-left:6px solid #6b7280}
.big{font-size:1.3rem;font-weight:700}.muted{color:#9aa4b2;font-size:.85rem}
.lv{display:inline-block;margin-right:16px;margin-top:4px}.lv b{font-size:1.15rem}
.ok{color:#16c784}.no{color:#6b7280}.refresh{text-align:right;color:#9aa4b2;font-size:.9rem}
.tag{display:inline-block;padding:1px 8px;border-radius:10px;background:#1f2937;font-size:.8rem;margin-left:6px}
</style>""", unsafe_allow_html=True)


# ------------------------------------------------------------------ helpers
def save_env(updates: dict) -> None:
    lines = config.ENV_FILE.read_text(encoding="utf-8").splitlines() if config.ENV_FILE.exists() else []
    seen, out = set(), []
    for ln in lines:
        k = ln.split("=", 1)[0].strip()
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
        else:
            out.append(ln)
    out += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    config.ENV_FILE.write_text("\n".join(out) + "\n", encoding="utf-8")
    config.reload_env()


def chart(df: pd.DataFrame, sigs: list[dict], title: str) -> go.Figure:
    fig = go.Figure()
    fig.add_candlestick(x=df["bar_close"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
                        name="Future", increasing_line_color="#16c784", decreasing_line_color="#ea3943")
    fig.add_scatter(x=df["bar_close"], y=df["vwap"], name="VWAP", line=dict(color="#f5c542", width=1.6))
    fig.add_scatter(x=df["bar_close"], y=df["ema_f"], name="EMA 20", line=dict(color="#4ea8ff", width=1))
    fig.add_scatter(x=df["bar_close"], y=df["ema_s"], name="EMA 50", line=dict(color="#c084fc", width=1))
    orb = df[df["bar_close"].dt.strftime("%H:%M") <= "09:30"]
    if len(orb):
        for y, nm in ((orb["high"].max(), "Range high"), (orb["low"].min(), "Range low")):
            fig.add_hline(y=y, line_dash="dot", line_color="#94a3b8", annotation_text=f"{nm} {y:.1f}")
    for s in sigs:
        fig.add_hline(y=s["entry"], line_color="#ffffff", line_dash="dash", annotation_text=f"{s['side']} entry {s['entry']}")
        fig.add_hline(y=s["sl"], line_color="#ea3943", line_dash="dash", annotation_text=f"SL {s['sl']}")
        fig.add_hline(y=s["tp"], line_color="#16c784", line_dash="dash", annotation_text=f"Target {s['tp']}")
    fig.update_layout(title=title, height=400, margin=dict(l=10, r=10, t=40, b=10), template="plotly_dark",
                      xaxis_rangeslider_visible=False, legend=dict(orientation="h", y=1.1))
    return fig


def signal_card(s: dict) -> None:
    cls = "ce" if s["side"] == "CE" else "pe"
    badge = {"TP": "🎯 Target hit", "SL": "🛑 SL hit", "TIME": f"⏱️ {S['exit_time']} exit ({s.get('r')}R)",
             "OPEN": "🟡 Trade open"}.get(s.get("result"), s.get("result"))
    prem = ""
    if s.get("opt_entry"):
        prem = (f"<span class='lv'>Entry <b>₹{s['opt_entry']}</b></span>"
                f"<span class='lv'>SL <b style='color:#ea3943'>₹{s.get('opt_sl')}</b></span>"
                f"<span class='lv'>Target <b style='color:#16c784'>₹{s.get('opt_tp')}</b></span>")
    st.markdown(
        f"<div class='card {cls}'><div class='big'>BUY {s['option']}<span class='tag'>exp {s.get('expiry')}</span></div>"
        f"<div class='muted'>⏰ {s['day']} {s['signal_time']} · numbers fixed · {badge}</div>{prem}"
        f"<div class='muted' style='margin-top:6px'>Future ({s.get('future') or ''}): entry {s['entry']} · "
        f"SL {s['sl']} · target {s['tp']} · risk {s['risk_pts']} pts · 1:{S['reward_risk']:.0f}</div>"
        f"<div style='margin-top:6px;font-size:.9rem'>{'<br>'.join('✅ ' + r for r in s['reasons'])}</div></div>",
        unsafe_allow_html=True)


def checklist_card(label: str, chk: dict | None, n_today: int) -> None:
    body = f"<div class='muted'>Signals today: {n_today} · waiting for all conditions</div>"
    if chk:
        for side in ("CE", "PE"):
            items = " · ".join(f"<span class='{'ok' if v else 'no'}'>{'●' if v else '○'} {k}</span>"
                               for k, v in chk[side].items())
            body += f"<div style='margin-top:4px'><b>{side}</b> (ATM {chk.get(side + '_atm')}): {items}</div>"
        vx = f"{chk['vol_x']:.1f}x" if chk.get("vol_x") else "—"
        body += (f"<div class='muted' style='margin-top:4px'>{chk['time']} · price {chk['price']:.1f} · "
                 f"VWAP {chk['vwap']:.1f} · EMA20 {chk['ema20']:.1f} · EMA50 {chk['ema50']:.1f} · volume {vx}</div>")
    st.markdown(f"<div class='card wait'><div class='big'>{label}</div>{body}</div>", unsafe_allow_html=True)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.header("⚙️ Settings")
    atok = st.text_input("Upstox Analytics Token (1 year)", value=config.env("UPSTOX_ANALYTICS_TOKEN"), type="password",
                         help="Best option: generate it once on the Upstox Developer Apps page - valid for 1 year, no daily login.")
    tok = st.text_input("Upstox access token (daily, optional)", value=config.env("UPSTOX_ACCESS_TOKEN"), type="password",
                        help="Only needed if you don't use the Analytics Token. Expires every day at 3:30 AM.")
    tg_tok = st.text_input("Telegram bot token", value=config.env("TELEGRAM_BOT_TOKEN"), type="password")
    tg_chat = st.text_input("Telegram chat id", value=config.env("TELEGRAM_CHAT_ID"))
    if st.button("💾 Save", width="stretch"):
        save_env({"UPSTOX_ANALYTICS_TOKEN": atok.strip(), "UPSTOX_ACCESS_TOKEN": tok.strip(), "TELEGRAM_BOT_TOKEN": tg_tok.strip(),
                  "TELEGRAM_CHAT_ID": tg_chat.strip()})
        st.session_state.pop("api", None)
        st.session_state.pop("last_scan", None)
        st.success("Saved")
    if st.button("📨 Telegram test message", width="stretch"):
        st.toast("Telegram: sent ✅" if notify.send(f"✅ {config.APP_NAME} connected") else
                 "Telegram failed - check the bot token / chat id")
    auto = st.toggle("Auto refresh (every 60 sec)", value=True)
    demo_mode = st.toggle("🧪 Demo mode (simulated data)", value=False,
                          help="See the dashboard working when the market is closed or no token is set. Demo signals are kept in a separate file.")
    alerts = st.toggle("Send Telegram alerts from this dashboard too", value=True,
                       help="Turn this off if GitHub Actions is running, otherwise you get every alert twice.")

if auto:
    st_autorefresh(interval=60_000, key="auto_refresh")
if st.session_state.get("demo_flag") != demo_mode:
    st.session_state.pop("last_scan", None)
    st.session_state.pop("bt", None)
    st.session_state.demo_flag = demo_mode
if "api" not in st.session_state:
    st.session_state.api = Upstox()
api: Upstox = st.session_state.api
SPATH = store.DEMO_STORE if demo_mode else None
XPATH = store.DEMO_XLSX if demo_mode else None
BT_XLSX = config.DATA_DIR / ("demo_backtest_report.xlsx" if demo_mode else "backtest_report.xlsx")

# ------------------------------------------------------------------ header
h1, h2 = st.columns([3, 1])
h1.markdown(f"## 📈 {config.APP_NAME}")
h1.caption("Combined Strategy · VWAP + EMA20/50 · ATM option OI · Opening-range breakout + volume · Nifty 50 · Bank Nifty · Sensex · F&O stocks")
refresh_box = h2.empty()

tab_live, tab_bt, tab_paper, tab_rules = st.tabs(
    ["🔴 Combined Strategy (Live)", "🧪 Backtest", "📒 Paper Trading Log", "📘 Rules"])

# ------------------------------------------------------------------ live
with tab_live:
    is_open = live.market_open()
    slot = (pd.Timestamp.now(tz=config.TZ) - pd.Timedelta(minutes=S["candle_minutes"])).floor(f"{S['candle_minutes']}min")
    out = st.session_state.get("last_scan")
    need = out is None or st.session_state.get("scan_slot") != slot
    clicked = st.button("🔄 Scan now")
    if clicked or ((is_open or demo_mode or out is None) and need):
        if demo_mode or config.upstox_token():
            with st.spinner("Fetching data (Nifty, Bank Nifty, options)..."):
                out = live.scan(api, send_alerts=alerts, demo_mode=demo_mode)
            st.session_state.last_scan, st.session_state.scan_slot = out, slot
    t = out["time"].strftime("%d %b %Y, %H:%M:%S") if out else "—"
    refresh_box.markdown(f"<div class='refresh'>Last refresh<br><b>{t}</b><br>"
                         f"{'🟢 Market open' if is_open else '⚪ Market closed'}</div>", unsafe_allow_html=True)
    if demo_mode:
        st.info("🧪 Demo mode ON — simulated data, only for seeing how the dashboard works.")
    elif not config.upstox_token():
        st.warning("Add your Upstox Analytics Token (or daily access token) in Settings on the left and press Save.")
    if out:
        for k, v in out["status"].items():
            if not v.startswith("OK"):
                st.info(f"{config.label(k)}: {v}")
    saved = out["signals"] if out else store.load(SPATH)
    today = str(live.now_ist().date())
    cols = st.columns(len(config.INDICES))
    for col, idx in zip(cols, config.INDICES):
        with col:
            st.markdown(f"### {config.INDICES[idx]['label']}")
            sigs = store.today_signals(saved, today, idx)
            for s in reversed(sigs):
                signal_card(s)
            if not any(s.get("result") == "OPEN" for s in sigs):
                checklist_card("Live checklist", (out or {}).get("checks", {}).get(idx), len(sigs))
    stock_sigs = [x for x in store.today_signals(saved, today) if x["index"] not in config.INDICES]
    st.markdown("### Stock options")
    if stock_sigs:
        for x in reversed(stock_sigs):
            signal_card(x)
    else:
        st.caption("No stock option signal yet today. All F&O stocks are checked automatically on GitHub "
                   "(this laptop dashboard checks only the indices).")
    if out:
        for idx in config.INDICES:
            df = out["charts"].get(idx)
            if df is not None and not df.empty:
                st.plotly_chart(chart(df, store.today_signals(saved, today, idx),
                                      f"{config.INDICES[idx]['label']} future · 5 min"),
                                width="stretch", key=f"ch_{idx}")
    st.caption(f"Signals form on a closed 5-minute candle · max {S['max_trades_per_day']} trades a day · "
               f"no new entry after {S['last_entry']} · once a signal appears its numbers stay fixed. Premium SL/target "
               "are estimated from delta; the future levels are exact.")

# ------------------------------------------------------------------ backtest
with tab_bt:
    st.subheader("🧪 Backtest — the strategy on past data")
    if not demo_mode and config.upstox_token():
        try:
            plus = api.has_plus()
        except Exception:
            plus = None
        if plus:
            st.success("Upstox Plus found ✅ — a full backtest with option OI is possible.")
        elif plus is False:
            st.warning("Upstox Plus not found. The free API has no data for expired options/futures, so the backtest "
                       "will run **without the OI checks (price-only)** and only over ~2–3 months (currently listed "
                       "futures). A full 1–2 year backtest needs Upstox Plus.")
    c1, c2 = st.columns([1, 2])
    years = c1.selectbox("History length", [0.25, 0.5, 1.0, 2.0], index=2,
                         format_func=lambda y: f"{int(y * 12)} months" if y < 1 else f"{int(y)} year" + ("s" if y > 1 else ""))
    c2.caption("The first download takes time (1 year ≈ 30–60 min because of Upstox rate limits). "
               "Data is saved in `data/cache`, so later runs are instant.")
    if st.button("▶️ Run backtest", type="primary"):
        end = date.today() - timedelta(days=1)
        start = end - timedelta(days=int(365 * years))
        with st.status("Running backtest...", expanded=True) as box:
            if demo_mode:
                days = {i: demo.backtest_days(i, start, end) for i in config.INDICES}
                mode = "DEMO (simulated data)"
            else:
                api.min_gap = 1.0
                days, modes = {}, set()
                for idx in config.INDICES:
                    try:
                        days[idx], m = data.backtest_days(api, idx, start, end, progress=box.write)
                        modes.add(m)
                    except Exception as e:
                        box.write(f"❌ {idx}: {e}")
                api.min_gap = 0.25
                mode = "full (with option OI)" if modes == {"full"} else "price-only (without OI checks)"
            trades = backtest.run(days)
            summ = backtest.summary(trades, days)
            backtest.save_excel(trades, summ, BT_XLSX, mode)
            st.session_state.bt = (trades, summ, mode)
            box.update(label="Backtest complete ✅", state="complete")
    if "bt" in st.session_state:
        trades, summ, mode = st.session_state.bt
        st.markdown(f"**Mode:** {mode}")
        if mode.startswith("DEMO"):
            st.error("These numbers come from simulated demo data — they say nothing about the real market.")
        for _, s in summ.iterrows():
            name = "All indices" if s["index"] == "ALL" else config.label(s["index"])
            st.markdown(f"#### {name} · {int(s['days'] or 0)} days")
            if not s.get("trades"):
                st.write("No trades.")
                continue
            m = st.columns(6)
            m[0].metric("Trades", int(s["trades"]))
            m[1].metric("Win rate", f"{s['win_rate_pct']}%", help=f"Breakeven at 1:2 is ~{s['breakeven_win_rate_pct']}%")
            m[2].metric("Avg profit / trade", f"{s['avg_r']} R", f"{s['avg_points']} pts")
            m[3].metric("Biggest loss", f"{s['biggest_loss_r']} R", f"{s['biggest_loss_points']} pts",
                        delta_color="off")
            m[4].metric("Max drawdown", f"{s['max_drawdown_r']} R")
            m[5].metric("Total", f"{s['total_r']} R",
                        f"premium avg {s['avg_option_points']}" if pd.notna(s.get("avg_option_points")) else None)
        if not trades.empty:
            eq = trades["net_r"].cumsum()
            fig = go.Figure(go.Scatter(x=list(range(1, len(eq) + 1)), y=eq, mode="lines", line=dict(color="#16c784")))
            fig.update_layout(height=260, template="plotly_dark", margin=dict(l=10, r=10, t=10, b=10),
                              xaxis_title="Trade #", yaxis_title="Cumulative R (after costs)")
            st.plotly_chart(fig, width="stretch", key="eq")
            with st.expander("Monthly"):
                st.dataframe(backtest.monthly(trades), width="stretch", hide_index=True)
            with st.expander("All trades"):
                st.dataframe(trades, width="stretch", hide_index=True)
        if BT_XLSX.exists():
            st.download_button("⬇️ Excel report", BT_XLSX.read_bytes(), BT_XLSX.name)
        st.caption(f"{backtest.COST_R}R cost deducted from every trade · if SL and target are hit in the same candle, SL is assumed.")

# ------------------------------------------------------------------ paper trading
with tab_paper:
    st.subheader("📒 Paper Trading Log")
    st.caption("Every live signal and its result is saved here and in `data/signal_log.xlsx`. "
               "Plan: build 2–3 months of this record, and move ahead only if it is good.")
    saved = store.load(SPATH)
    if not saved:
        st.write("No live signals yet.")
    else:
        df = pd.DataFrame(saved.values()).sort_values(["day", "signal_time"], ascending=False)
        done = df[df["result"].isin(["TP", "SL", "TIME"])].copy()
        first = pd.to_datetime(df["day"]).min()
        m = st.columns(5)
        m[0].metric("Paper trading for", f"{(pd.Timestamp.today() - first).days} days")
        m[1].metric("Signals", len(df))
        if len(done):
            done["r"] = pd.to_numeric(done["r"])
            eq = done.sort_values(["day", "signal_time"])["r"].cumsum()
            m[2].metric("Win rate", f"{(done['r'] > 0).mean() * 100:.0f}%")
            m[3].metric("Total R", round(done["r"].sum(), 2))
            m[4].metric("Max drawdown", f"{round(float((eq - eq.cummax()).min()), 2)} R")
            g = done.assign(month=pd.to_datetime(done["day"]).dt.strftime("%Y-%m")).groupby("month")["r"]
            st.dataframe(pd.DataFrame({"trades": g.size(), "win %": g.apply(lambda x: round((x > 0).mean() * 100)),
                                       "total R": g.sum().round(2)}).reset_index(), hide_index=True)
        show = [c for c in ["day", "signal_time", "option", "opt_entry", "opt_sl", "opt_tp", "entry", "sl", "tp",
                            "result", "exit", "exit_time", "r", "opt_exit", "opt_points"] if c in df.columns]
        st.dataframe(df[show], width="stretch", hide_index=True)
        p = store.write_excel(saved, XPATH)
        if p.exists():
            st.download_button("⬇️ Excel log", p.read_bytes(), p.name)

# ------------------------------------------------------------------ rules
with tab_rules:
    st.subheader("📘 Combined Strategy rules")
    st.markdown(f"""
**Instruments:** Nifty 50 and Bank Nifty index options · {S['candle_minutes']}-minute candles
(price, VWAP, EMA and volume come from the **near-month future**, because the index has no volume; the ATM strike comes from the spot index).

**BUY CE — all conditions together:**
1. **Trend:** price above VWAP, and EMA {S['ema_fast']} above EMA {S['ema_slow']}
2. **OI:** long build-up in the ATM call (premium and OI both up vs {S['oi_lookback_candles']} candles earlier,
   OI ≥ {S['atm_oi_min_pct']}%) **and** put writing at ATM + 2 strikes below (put OI up ≥ {S['writing_oi_min_pct']}%, premium down)
3. **Entry:** after {S['entry_start']}, the 9:15–9:30 range high breaks with volume above the average of the previous {S['volume_avg_candles']} candles
4. **Stop loss:** previous candle low
5. **Target:** 1:{S['reward_risk']:.0f} reward:risk
6. **Limit:** max {S['max_trades_per_day']} trades a day · no new entry after {S['last_entry']} · open trade closed at {S['exit_time']}

**BUY PE:** the mirror image — price below VWAP, EMA20 below EMA50, long build-up in the ATM put + call writing at ATM
and 2 strikes above, the range low breaks with volume, SL at the previous candle high.

**Extra safety (change in `raking/config.py`):** no entry if the range broke more than {S['breakout_window']} candles ago (no late chasing) ·
skip the trade if the SL is wider than {S['max_risk_atr']} ATR · on expiry day the next expiry's options are used.

**Testing plan:** 1) 1–2 year backtest → 2) 2–3 months of paper trading → 3) subscriptions only if the record is good
(after SEBI Research Analyst registration).

⚠️ This is a research / paper-trading tool, not investment advice.
""")

