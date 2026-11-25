"""MCX Crude Oil live signals (alerts only, no orders). Run every ~5 min during MCX hours.

Rules = mcx_backtest.py, Crude Oil only, windows 09:00-09:30 and 19:30-20:00 IST.
Signals use CRUDEOIL futures 5-min candles; the alert names the ATM Crude Oil Mini option
(smaller lot, less capital). Option SL/target are approximate (ATM delta ~0.5).
State: data/mcx_paper.json (alerts already sent + trade log)."""
import json
from datetime import datetime, timedelta, time as dtime

import pandas as pd

from mcx_backtest import prepare, LAST_ENTRY, SQUARE_OFF, ROLL_DAYS
from raking import config, holidays
from raking.notify import send
from raking.upstox import Upstox, UpstoxError

WINDOWS = {"Morning": (dtime(9, 0), dtime(9, 30)), "US open": (dtime(19, 30), dtime(20, 0))}
STATE = config.DATA_DIR / "mcx_paper.json"
DELTA = 0.5
MIN_SL_ATR = 0.5   # crude SL at least half an ATR from entry (tight stops were hit by normal noise)


def load():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"sent": [], "trades": []}


def near(m, sym, kinds, today):
    f = m[(m.underlying_symbol == sym) & (m.instrument_type.isin(kinds)) & (m.expiry_date >= today)]
    return f.sort_values("expiry_date")


def candles(api, today, now):
    m = api.master("MCX")
    fut = near(m, "CRUDEOIL", ["FUT"], today + timedelta(days=ROLL_DAYS)).iloc[0]
    hist = api.historical(fut.instrument_key, today - timedelta(days=6), today - timedelta(days=1))
    parts = [x for x in (hist, api.intraday(fut.instrument_key)) if x is not None and len(x)]
    d = pd.concat(parts)
    d["ts"] = pd.to_datetime(d["ts"], utc=True).dt.tz_convert(config.TZ)
    d = d.sort_values("ts").drop_duplicates("ts")
    d = d[d.ts + pd.Timedelta(minutes=5) <= now]          # completed candles only
    return prepare(d.reset_index(drop=True)), m


def events(d, today):
    """Today's simulated trades per window: list of dicts (entry and, if closed, exit)."""
    out = []
    g = d[d.day == today]
    for name, (a, b) in WINDOWS.items():
        orw = g[(g.t >= a) & (g.t < b)]
        if len(orw) < 6:
            continue
        hi, lo, n, pos, used = orw.high.max(), orw.low.min(), 0, None, set()
        rows = g[g.t >= b].reset_index(drop=True)
        for i in range(1, len(rows)):
            r, p = rows.iloc[i], rows.iloc[i - 1]
            if pos:
                hit_sl = r.low <= pos["sl"] if pos["side"] > 0 else r.high >= pos["sl"]
                hit_tg = r.high >= pos["tg"] if pos["side"] > 0 else r.low <= pos["tg"]
                if hit_sl or hit_tg or r.t >= SQUARE_OFF:
                    px = pos["sl"] if hit_sl else pos["tg"] if hit_tg else r.close
                    pos.update(exit=px, exit_t=str(r.t)[:5], why="SL" if hit_sl else "Target" if hit_tg else "Time",
                               R=round((px - pos["entry"]) * pos["side"] / abs(pos["entry"] - pos["sl"]), 2))
                    pos = None
                continue
            if n >= 2 or r.t > LAST_ENTRY or pd.isna(r.vavg) or pd.isna(r.atr):
                continue
            ok = r.volume > r.vavg
            if r.close > hi and r.close > r.vwap and r.e20 > r.e50 and ok:
                side, sl = 1, p.low
            elif r.close < lo and r.close < r.vwap and r.e20 < r.e50 and ok:
                side, sl = -1, p.high
            else:
                continue
            px = float(r.close)   # enter at the breakout candle's close, so the alert goes out right after it closes
            risk = (px - sl) * side
            if risk <= 0 or risk > 2 * r.atr or side in used:   # one entry per window per direction
                continue
            risk = max(risk, MIN_SL_ATR * float(r.atr))
            sl = px - risk * side
            used.add(side)
            n += 1
            pos = dict(id=f"{today}-{name}-{n}", window=name, side=side,
                       t=(r.ts + pd.Timedelta(minutes=5)).strftime("%H:%M"),
                       entry=px, sl=float(sl), tg=float(px + 2 * risk * side))
            out.append(pos)
    return out


def option_for(api, m, today, fut_px, side):
    kind = "CE" if side > 0 else "PE"
    for sym in ("CRUDEOILM", "CRUDEOIL"):
        o = near(m, sym, [kind], today)
        if o.empty:
            continue
        o = o[o.expiry_date == o.expiry_date.min()]
        row = o.iloc[(o.strike_price - fut_px).abs().argsort().iloc[0]]
        try:
            ltp = api.quotes([row.instrument_key]).get(row.instrument_key, {}).get("last_price")
        except UpstoxError:
            ltp = None
        return row.trading_symbol, int(row.lot_size), ltp, row.instrument_key
    return None, None, None, None


def opt_ltp(api, key):
    try:
        return api.quotes([key]).get(key, {}).get("last_price")
    except UpstoxError:
        return None


def main():
    now = pd.Timestamp.now(tz=config.TZ)
    today = now.date()
    st = load()
    st.setdefault("opt", {})
    if st.get("day") != str(today):
        st["day"], st["sent"] = str(today), []
    if holidays.gate("MCX", st, now.to_pydatetime(), send):
        STATE.write_text(json.dumps(st, indent=1))
        return
    api = Upstox()
    d, m = candles(api, today, now)
    hhmm = now.strftime("%H:%M")
    for e in events(d, today):
        up = e["side"] > 0
        risk = abs(e["entry"] - e["sl"])
        if e["id"] + ":in" not in st["sent"] and e["t"] < (now - timedelta(minutes=15)).strftime("%H:%M"):
            st["sent"] += [e["id"] + ":in", e["id"] + ":out"]   # too old to act on (e.g. after a restart): never alert late
            continue
        if e["id"] + ":in" not in st["sent"]:
            sym, lot, ltp, key = option_for(api, m, today, e["entry"], e["side"])
            lines = [f"{'🟢' if up else '🔴'} <b>NEW SIGNAL · BUY {sym or 'CRUDEOIL ' + ('CE' if up else 'PE')}</b>",
                     f"🛢 MCX Crude Oil option · {e['window']} breakout"]
            if sym and ltp:
                osl, otg = round(max(ltp - DELTA * risk, 0.5), 1), round(ltp + DELTA * 2 * risk, 1)
                st["opt"][e["id"]] = dict(sym=sym, key=key, lot=lot, ltp=ltp, sl=osl, tp=otg, side="CE" if up else "PE",
                                          time=e["t"], window=e["window"], now=ltp, now_t=hhmm)
                lines += ["", f"▶️ Entry  : <b>₹{ltp:.1f}</b>", f"🛑 SL     : <b>₹{osl:.1f}</b>",
                          f"🎯 Target : <b>₹{otg:.1f}</b>", "",
                          f"📦 1 lot = <b>{lot} qty</b>",
                          f"💰 Capital ≈ <b>₹{ltp * lot:,.0f}</b> · Risk ≈ ₹{(ltp - osl) * lot:,.0f}"
                          f" · Reward ≈ ₹{(otg - ltp) * lot:,.0f} (per lot)"]
            else:
                lines.append("Premium not available - check the option price in your broker app first.")
            lines += ["", f"📈 Future: entry {e['entry']:.0f} · SL {e['sl']:.0f} · Target {e['tg']:.0f}",
                      f"⏰ {e['t']} · exit by 23:15", "Not advice - your decision."]
            send("\n".join(lines))
            st["sent"].append(e["id"] + ":in")
        o = st["opt"].get(e["id"])
        if o and "exit" not in e:                      # running trade: live premium for the dashboard
            px = opt_ltp(api, o["key"])
            if px:
                o["now"], o["now_t"] = float(px), hhmm
        if "exit" in e and e["id"] + ":out" not in st["sent"]:
            head = {"Target": "🎯 <b>TARGET HIT ✅</b>", "SL": "🛑 <b>STOP-LOSS HIT ❌</b>"}.get(
                e["why"], "⏱️ <b>CLOSED AT 23:15</b>")
            lines = [head]
            if o:
                px = opt_ltp(api, o["key"])
                if px:
                    o["exit"] = o["now"] = float(px)
                    pts = px - o["ltp"]
                    lines += [f"<b>{o['sym']}</b>", f"Entry ₹{o['ltp']:.1f} → Exit <b>₹{px:.1f}</b>",
                              f"P&amp;L: <b>{pts:+.2f}/unit ({'+' if pts >= 0 else '-'}₹{abs(pts) * o['lot']:,.0f}"
                              f" per lot of {o['lot']} qty)</b> · {e['R']:+.1f}R"]
            if len(lines) == 1:
                lines.append(f"<b>MCX Crude {'CE' if up else 'PE'}</b> · {e['R']:+.1f}R")
            lines += [f"📈 Future {e['entry']:.0f} → {e['exit']:.0f}", f"⏰ {e['t']} → {e['exit_t']}"]
            send("\n".join(lines))
            st["sent"].append(e["id"] + ":out")
            t = {k: e[k] for k in ("id", "side", "entry", "sl", "tg", "exit", "why", "R", "t", "exit_t", "window")}
            if o and "exit" in o:
                t.update(sym=o["sym"], lot=o["lot"], opt_entry=o["ltp"], opt_exit=o["exit"], opt_sl=o.get("sl"), opt_tp=o.get("tp"),
                         opt_pnl_lot=round((o["exit"] - o["ltp"]) * o["lot"]))
            st["trades"].append(t)
    if now.time() >= dtime(23, 25) and "summary" not in st["sent"]:
        tday = [t for t in st["trades"] if t["id"].startswith(str(today))]
        tot = sum(t["R"] for t in st["trades"])
        send(f"📊 MCX Crude signals — {today}\nToday: {len(tday)} trades, {sum(t['R'] for t in tday):+.2f}R\n"
             f"Running total: {len(st['trades'])} trades, {tot:+.2f}R")
        st["sent"].append("summary")
    if len(d):
        last = d.iloc[-1]
        prev = d[d.day < today]
        pc = float(prev.iloc[-1].close) if len(prev) else None
        g = d[d.day == today]
        fut = near(m, "CRUDEOIL", ["FUT"], today + timedelta(days=ROLL_DAYS)).iloc[0]
        win = next((n for n, (a, b) in WINDOWS.items() if a <= now.time() < b), None)
        st["live"] = dict(
            updated=now.strftime("%H:%M"), symbol=fut.trading_symbol, price=float(last.close),
            prev_close=pc, chg_pct=round((last.close / pc - 1) * 100, 2) if pc else None,
            day_high=float(g.high.max()) if len(g) else None, day_low=float(g.low.min()) if len(g) else None,
            vwap=None if pd.isna(last.vwap) else round(float(last.vwap), 1),
            trend="Up" if last.e20 > last.e50 else "Down",
            above_vwap=bool(last.close > last.vwap) if not pd.isna(last.vwap) else None,
            window=win or "Waiting (09:00-09:30, 19:30-20:00)",
            candles=[[str(t.ts)[11:16], float(t.open), float(t.high), float(t.low), float(t.close)] for t in g.tail(150).itertuples()], ranges={n: [float(x.high.max()), float(x.low.min())] for n, (a, b) in WINDOWS.items() if len(x := g[(g.t >= a) & (g.t < b)]) >= 6}, events=[{k: e.get(k) for k in ("id", "window", "side", "t", "entry", "sl", "tg", "exit", "exit_t", "why", "R")} for e in events(d, today)])
    STATE.write_text(json.dumps(st, indent=1))


if __name__ == "__main__":
    main()
