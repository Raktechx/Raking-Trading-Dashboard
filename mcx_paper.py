"""MCX Crude Oil paper alerts (no orders). Run every ~5 min during MCX hours.

Rules = mcx_backtest.py, Crude Oil only, windows 09:00-09:30 and 19:30-20:00 IST.
Signals use CRUDEOIL futures 5-min candles; the alert names the ATM Crude Oil Mini option
(smaller lot, less capital). Option SL/target are approximate (ATM delta ~0.5).
State: data/mcx_paper.json (alerts already sent + trade log)."""
import json
from datetime import datetime, timedelta, time as dtime

import pandas as pd

from mcx_backtest import prepare, LAST_ENTRY, SQUARE_OFF, ROLL_DAYS
from raking import config
from raking.notify import send
from raking.upstox import Upstox, UpstoxError

WINDOWS = {"Morning": (dtime(9, 0), dtime(9, 30)), "US open": (dtime(19, 30), dtime(20, 0))}
STATE = config.DATA_DIR / "mcx_paper.json"
DELTA = 0.5


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
        hi, lo, n, pos = orw.high.max(), orw.low.min(), 0, None
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
            if i == len(rows) - 1 or n >= 2 or r.t > LAST_ENTRY or pd.isna(r.vavg) or pd.isna(r.atr):
                continue
            ok = r.volume > r.vavg
            if r.close > hi and r.close > r.vwap and r.e20 > r.e50 and ok:
                side, sl = 1, p.low
            elif r.close < lo and r.close < r.vwap and r.e20 < r.e50 and ok:
                side, sl = -1, p.high
            else:
                continue
            nx = rows.iloc[i + 1]
            risk = (nx.open - sl) * side
            if risk <= 0 or risk > 2 * r.atr:
                continue
            n += 1
            pos = dict(id=f"{today}-{name}-{n}", window=name, side=side, t=str(nx.t)[:5],
                       entry=float(nx.open), sl=float(sl), tg=float(nx.open + 2 * risk * side))
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
        return row.trading_symbol, int(row.lot_size), ltp
    return None, None, None


def main():
    now = pd.Timestamp.now(tz=config.TZ)
    today = now.date()
    st = load()
    if st.get("day") != str(today):
        st["day"], st["sent"] = str(today), []
    api = Upstox()
    d, m = candles(api, today, now)
    for e in events(d, today):
        side_txt = "CE (bullish)" if e["side"] > 0 else "PE (bearish)"
        risk = abs(e["entry"] - e["sl"])
        if e["id"] + ":in" not in st["sent"]:
            sym, lot, ltp = option_for(api, m, today, e["entry"], e["side"])
            opt = ""
            if sym and ltp:
                opt = (f"\nOption: {sym} (lot {lot})\nPremium ≈ ₹{ltp:.1f} · SL ≈ ₹{max(ltp - DELTA * risk, 0.5):.1f}"
                       f" · Target ≈ ₹{ltp + DELTA * 2 * risk:.1f}")
            elif sym:
                opt = f"\nOption: {sym} (lot {lot})"
            send(f"🛢 MCX Crude {side_txt} — PAPER\n{e['window']} breakout at {e['t']} IST\n"
                 f"Future: entry {e['entry']:.0f} · SL {e['sl']:.0f} · Target {e['tg']:.0f}{opt}\n"
                 f"Exit by 23:15 IST. Paper only.")
            st["sent"].append(e["id"] + ":in")
        if "exit" in e and e["id"] + ":out" not in st["sent"]:
            icon = "✅" if e["R"] > 0 else "❌"
            send(f"{icon} MCX Crude {e['window']} trade closed ({e['why']}) at {e['exit_t']} IST\n"
                 f"Future {e['entry']:.0f} → {e['exit']:.0f} = {e['R']:+.2f}R (paper)")
            st["sent"].append(e["id"] + ":out")
            st["trades"].append({k: e[k] for k in ("id", "side", "entry", "sl", "tg", "exit", "why", "R")})
    if now.time() >= dtime(23, 25) and "summary" not in st["sent"]:
        tday = [t for t in st["trades"] if t["id"].startswith(str(today))]
        tot = sum(t["R"] for t in st["trades"])
        send(f"📊 MCX Crude paper — {today}\nToday: {len(tday)} trades, {sum(t['R'] for t in tday):+.2f}R\n"
             f"Running total: {len(st['trades'])} trades, {tot:+.2f}R")
        st["sent"].append("summary")
    STATE.write_text(json.dumps(st, indent=1))


if __name__ == "__main__":
    main()
