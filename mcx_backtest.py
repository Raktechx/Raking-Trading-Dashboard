"""MCX intraday backtest - Crude Oil + Natural Gas (Upstox data, read-only token, no orders).

Same combined logic as the index strategy, run on MCX futures 5-min candles:
  LONG (CE): close > session VWAP, EMA20 > EMA50, close breaks opening-range high, volume > 20-bar avg
  SHORT (PE): mirror image
  SL = previous candle low/high (skip if wider than 2 ATR), target = 2R, max 2 trades/day, exit 23:15 IST.
Three opening-range windows are compared: 09:00-09:30, 17:00-17:30, 19:30-20:00 (US open).
Results are in R (risk units) on the futures price; option-buying P&L is roughly half the points (ATM delta ~0.5).
Uses Upstox Plus expired contracts if the account has them (about 1 year); otherwise listed contracts only."""
import sys
from datetime import date, time as dtime, timedelta

import numpy as np
import pandas as pd

from raking.upstox import Upstox, UpstoxError

COMMODITIES = {"CRUDEOIL": "Crude Oil", "NATURALGAS": "Natural Gas"}
WINDOWS = {"Morning 09:00": (dtime(9, 0), dtime(9, 30)),
           "Evening 17:00": (dtime(17, 0), dtime(17, 30)),
           "US open 19:30": (dtime(19, 30), dtime(20, 0))}
LAST_ENTRY, SQUARE_OFF = dtime(22, 30), dtime(23, 15)
ROLL_DAYS = 3


def contracts(api, sym, start, end):
    """[(expiry, kind, key)] covering start..end, nearest first."""
    m = api.master("MCX")
    f = m[(m["instrument_type"] == "FUT") & (m["underlying_symbol"] == sym)].sort_values("expiry_date")
    if f.empty:
        near = sorted({s for s in m["underlying_symbol"].dropna().unique() if sym[:5] in s})
        raise UpstoxError(f"{sym}: not found; similar symbols: {near}")
    out = [(r.expiry_date, "listed", r.instrument_key) for r in f.itertuples()]
    plus = False
    try:
        u = f["underlying_key"].iloc[0]
        exps = api._get("/v2/expired-instruments/expiries", {"instrument_key": u}).get("data") or []
        for e in sorted(pd.to_datetime(x).date() for x in exps):
            if start <= e and e < out[0][0]:
                rows = api._get("/v2/expired-instruments/future/contract",
                                {"instrument_key": u, "expiry_date": f"{e:%Y-%m-%d}"}).get("data") or []
                if rows:
                    out.append((e, "expired", rows[0]["instrument_key"]))
                    plus = True
    except UpstoxError as err:
        print(f"<!-- {sym}: expired data not available ({type(err).__name__}) -->")
    return sorted(out), plus


def continuous(api, sym, start, end):
    cons, plus = contracts(api, sym, start, end)
    parts, prev = [], start
    for exp, kind, key in cons:
        s, t = prev, min(end, exp - timedelta(days=ROLL_DAYS))
        if t < s:
            continue
        try:
            df = api.historical(key, s, t) if kind == "listed" else api.expired_candles(key, s, t)
        except UpstoxError as err:
            print(f"<!-- skip {sym} {exp}: {err} -->")
            df = pd.DataFrame()
        if not df.empty:
            parts.append(df)
            prev = t + timedelta(days=1)
        if t >= end:
            break
    if not parts:
        return pd.DataFrame(), plus
    d = pd.concat(parts).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    return d, plus


def prepare(d):
    d = d.copy()
    d["day"], d["t"] = d["ts"].dt.date, d["ts"].dt.time
    tp = (d.high + d.low + d.close) / 3
    pv = (tp * d.volume).groupby(d.day).cumsum()
    vv = d.volume.groupby(d.day).cumsum().replace(0, np.nan)
    d["vwap"] = (pv / vv).fillna(d.close)
    d["e20"], d["e50"] = d.close.ewm(span=20).mean(), d.close.ewm(span=50).mean()
    d["vavg"] = d.volume.rolling(20).mean().shift()
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(), (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(14).mean()
    return d


def run(d, win):
    a, b = win
    trades = []
    for day, g in d.groupby("day"):
        orw = g[(g.t >= a) & (g.t < b)]
        if len(orw) < 3:
            continue
        hi, lo, n, pos = orw.high.max(), orw.low.min(), 0, None
        rows = g[g.t >= b].reset_index(drop=True)
        for i in range(1, len(rows) - 1):
            r, p, nx = rows.iloc[i], rows.iloc[i - 1], rows.iloc[i + 1]
            if pos:
                side, entry, sl, tg = pos
                hit_sl = r.low <= sl if side > 0 else r.high >= sl
                hit_tg = r.high >= tg if side > 0 else r.low <= tg
                if hit_sl or hit_tg or r.t >= SQUARE_OFF:
                    px = sl if hit_sl else tg if hit_tg else r.close
                    trades.append((day, side, (px - entry) * side / abs(entry - sl)))
                    pos = None
                continue
            if n >= 2 or r.t > LAST_ENTRY or pd.isna(r.vavg) or pd.isna(r.atr):
                continue
            vol_ok = r.volume > r.vavg
            if r.close > hi and r.close > r.vwap and r.e20 > r.e50 and vol_ok:
                side, sl = 1, p.low
            elif r.close < lo and r.close < r.vwap and r.e20 < r.e50 and vol_ok:
                side, sl = -1, p.high
            else:
                continue
            entry = nx.open
            risk = (entry - sl) * side
            if risk <= 0 or risk > 2 * r.atr:
                continue
            pos, n = (side, entry, sl, entry + 2 * risk * side), n + 1
        if pos:
            side, entry, sl, _ = pos
            trades.append((day, side, (rows.iloc[-1].close - entry) * side / abs(entry - sl)))
    return pd.DataFrame(trades, columns=["day", "side", "R"])


def line(name, t):
    if t.empty:
        return f"| {name} | 0 | - | - | - | - | - |"
    r = t.R - 0.05                       # ~0.05R for fees + slippage
    eq = r.cumsum()
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else float("inf")
    months = " ".join(f"{m}:{v:+.1f}" for m, v in r.groupby(pd.to_datetime(t.day).dt.strftime("%b%y")).sum().items())
    return (f"| {name} | {len(r)} | {100*(r > 0).mean():.0f}% | {r.mean():+.2f}R | {r.sum():+.1f}R | "
            f"{pf:.2f} | {(eq - eq.cummax()).min():.1f}R | {months} |")


if __name__ == "__main__":
    end = date.today()
    start = end - timedelta(days=int(sys.argv[1]) if len(sys.argv) > 1 else 365)
    api = Upstox()
    print("## MCX intraday backtest (futures 5-min, R per trade after ~0.05R costs)\n")
    print("| Commodity / window | Trades | Win % | Avg | Total | Profit factor | Max DD | Monthly R |")
    print("|---|---|---|---|---|---|---|---|")
    for sym, label in COMMODITIES.items():
        try:
            raw, plus = continuous(api, sym, start, end)
        except UpstoxError as err:
            print(f"| {label} | data error: {err} |"); continue
        if raw.empty:
            print(f"| {label} | no data |"); continue
        d = prepare(raw)
        span = f"{d.day.min():%d %b %Y} → {d.day.max():%d %b %Y}, {d.day.nunique()} days, {'Plus' if plus else 'listed only'}"
        for w, win in WINDOWS.items():
            t = run(d, win)
            print(line(f"{label} · {w}", t))
            for s, nm in ((1, "CE"), (-1, "PE")):
                print(line(f"  ↳ {nm} only", t[t.side == s]))
        print(f"| _{label} data: {span}_ | | | | | | | |")
    print("\nR = profit in units of risk (SL distance). +1R = won what you risked. "
          "A strategy needs avg > +0.10R and profit factor > 1.3 over 100+ trades to be worth trading.")
