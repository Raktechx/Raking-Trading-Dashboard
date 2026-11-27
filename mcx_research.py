"""Crude Oil strategy research: tests many rule variants on ~1 year of CRUDEOIL futures 5-min candles.
Train = first 2/3 of days, Test = last 1/3 (unseen). A variant is only worth using if it is profitable in BOTH.
Read-only Upstox data, no orders. Results in R after 0.05R costs; entry at the signal candle close (as live)."""
import itertools
import sys
from datetime import date, time as dtime, timedelta

import pandas as pd

from mcx_backtest import continuous, prepare, SQUARE_OFF
from raking.upstox import Upstox

WINDOWS = {"AM 09:00": (dtime(9, 0), dtime(9, 30)), "EU 13:30": (dtime(13, 30), dtime(14, 0)),
           "PM 17:00": (dtime(17, 0), dtime(17, 30)), "US 19:30": (dtime(19, 30), dtime(20, 0))}
STOPS = ["prev", "prev_min05", "atr1", "range_mid"]
TARGETS = [1.5, 2.0, 3.0, "trail"]
FILTERS = ["base", "htf", "narrow", "vol15", "htf+narrow"]
COST = 0.05


def add_htf(d):
    d = d.copy()
    d["h20"], d["h50"] = d.close.ewm(span=60).mean(), d.close.ewm(span=150).mean()   # 15-min EMA20/50
    return d


def run(d, win, stop, target, filt, last_entry=dtime(22, 0)):
    a, b = win
    out = []
    for day, g in d.groupby("day"):
        orw = g[(g.t >= a) & (g.t < b)]
        if len(orw) < 6:
            continue
        hi, lo, pos, used = orw.high.max(), orw.low.min(), None, set()
        rows = g[g.t >= b].reset_index(drop=True)
        for i in range(1, len(rows)):
            r, p = rows.iloc[i], rows.iloc[i - 1]
            if pos:
                side, entry, sl, tg, risk = pos
                if target == "trail" and i > 1:   # trail SL to EMA20 once +1R
                    if (r.close - entry) * side >= risk:
                        sl = max(sl, r.e20) if side > 0 else min(sl, r.e20)
                hit_sl = r.low <= sl if side > 0 else r.high >= sl
                hit_tg = tg is not None and (r.high >= tg if side > 0 else r.low <= tg)
                if hit_sl or hit_tg or r.t >= SQUARE_OFF:
                    px = sl if hit_sl else tg if hit_tg else r.close
                    out.append((day, side, (px - entry) * side / risk - COST))
                    pos = None
                else:
                    pos = (side, entry, sl, tg, risk)
                continue
            if r.t > last_entry or pd.isna(r.vavg) or pd.isna(r.atr) or len(used) == 2:
                continue
            vm = 1.5 if "vol15" in filt else 1.0
            ok = r.volume > vm * r.vavg
            if "narrow" in filt and (hi - lo) > 1.5 * r.atr * 2:     # skip wide opening ranges
                continue
            if r.close > hi and r.close > r.vwap and r.e20 > r.e50 and ok and 1 not in used:
                side = 1
            elif r.close < lo and r.close < r.vwap and r.e20 < r.e50 and ok and -1 not in used:
                side = -1
            else:
                continue
            if "htf" in filt and (r.h20 - r.h50) * side <= 0:
                continue
            px = float(r.close)
            if stop == "prev":
                sl = p.low if side > 0 else p.high
            elif stop == "prev_min05":
                sl = p.low if side > 0 else p.high
                sl = px - max((px - sl) * side, 0.5 * r.atr) * side
            elif stop == "atr1":
                sl = px - r.atr * side
            else:
                sl = (hi + lo) / 2
            risk = (px - sl) * side
            if risk <= 0 or risk > 2.5 * r.atr:
                continue
            tg = None if target == "trail" else px + target * risk * side
            used.add(side)
            pos = (side, px, sl, tg, risk)
        if pos:
            side, entry, sl, tg, risk = pos
            out.append((day, side, (rows.iloc[-1].close - entry) * side / risk - COST))
    return pd.DataFrame(out, columns=["day", "side", "R"])


def md(df):
    try:
        return df.to_markdown(index=False)
    except ImportError:
        return "```\n" + df.to_string(index=False) + "\n```"


def stats(t):
    if t.empty:
        return 0, 0.0, 0.0, 0.0, 0.0
    r = t.R
    pf = r[r > 0].sum() / -r[r < 0].sum() if (r < 0).any() else 9.99
    eq = r.cumsum()
    return len(r), 100 * (r > 0).mean(), r.sum(), pf, (eq - eq.cummax()).min()


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    end = date.today()
    raw, _ = continuous(Upstox(), "CRUDEOIL", end - timedelta(days=days), end)
    d = add_htf(prepare(raw))
    alld = sorted(d.day.unique())
    cut = alld[len(alld) * 2 // 3]
    print(f"\n## Crude Oil research: {alld[0]} to {alld[-1]} ({len(alld)} days), test from {cut}\n")
    rows = []
    for (wn, w), st, tg, fl in itertools.product(WINDOWS.items(), STOPS, TARGETS, FILTERS):
        t = run(d, w, st, tg, fl)
        tr, te = t[t.day < cut], t[t.day >= cut]
        n, wr, tot, pf, dd = stats(t)
        rows.append(dict(window=wn, stop=st, target=str(tg), filter=fl, trades=n, win=round(wr), R=round(tot, 1),
                         PF=round(pf, 2), maxDD=round(dd, 1), train_R=round(stats(tr)[2], 1),
                         test_R=round(stats(te)[2], 1), test_PF=round(stats(te)[3], 2), test_n=stats(te)[0]))
    res = pd.DataFrame(rows)
    cur = res[(res.window.isin(["AM 09:00", "US 19:30"])) & (res.stop == "prev_min05") & (res.target == "2.0")
              & (res.filter == "base")]
    print("### Current live rules\n")
    print(md(cur))
    good = res[(res.trades >= 40) & (res.train_R > 0) & (res.test_R > 0)].sort_values("R", ascending=False)
    print("\n### Best variants profitable in BOTH train and test (top 25)\n")
    print(md(good.head(25)))
    print("\n### Per window, best robust variant\n")
    print(md(good.groupby("window").head(1)))
    print(f"\n{len(good)} of {len(res)} variants were profitable in both periods.")
    # ---- 27 Nov: crude v2 ideas on the live setup (US 19:30 range, SL 1 ATR, trail EMA20 after +1R)
    def run2(d, retest=False, cut=dtime(22, 0), no_wed=False, half=False, htf=False, mode="orb", dirn=False):
        a, b = WINDOWS["US 19:30"]
        out = []
        for day, g in d.groupby("day"):
            if no_wed and pd.Timestamp(day).weekday() == 2:
                continue
            orw = g[(g.t >= a) & (g.t < b)]
            if len(orw) < 6:
                continue
            hi, lo, pos, used, armed = orw.high.max(), orw.low.min(), None, set(), 0
            wret = float(orw.close.iloc[-1] - orw.open.iloc[0])   # first half-hour move after the US open
            rows = g[g.t >= b].reset_index(drop=True)
            if mode != "orb":   # intraday momentum: one trade in the direction of a half-hour move, held to close
                if mode == "eia":
                    if pd.Timestamp(day).weekday() != 2:
                        continue
                    ea = dtime(21, 0) if pd.Timestamp(day).month in (11, 12, 1, 2) or (pd.Timestamp(day).month == 3 and pd.Timestamp(day).day < 10) else dtime(20, 0)
                    ew = g[(g.t >= ea) & (g.t < (pd.Timestamp.combine(day, ea) + pd.Timedelta(minutes=30)).time())]
                    if len(ew) < 6:
                        continue
                    wret = float(ew.close.iloc[-1] - ew.open.iloc[0])
                    rows = g[g.t >= ew.t.iloc[-1]].reset_index(drop=True)
                if len(rows) < 2 or wret == 0 or pd.isna(rows.iloc[0].atr):
                    continue
                side, r0 = (1 if wret > 0 else -1), rows.iloc[0]
                if htf and (r0.h20 - r0.h50) * side <= 0:
                    continue
                entry, risk = float(r0.close), float(r0.atr)
                if abs(wret) < 0.5 * risk:   # skip quiet opens
                    continue
                pos = (side, entry, entry - risk * side, risk, 0)
                rows = rows.iloc[1:].reset_index(drop=True)
                rows = pd.concat([rows.iloc[:1], rows]).reset_index(drop=True)
            for i in range(1, len(rows)):
                r = rows.iloc[i]
                if pos:
                    side, entry, sl, risk, bank = pos
                    if (r.close - entry) * side >= risk:
                        sl = max(sl, r.e20) if side > 0 else min(sl, r.e20)
                    if half and bank == 0 and ((r.high - entry) if side > 0 else (entry - r.low)) >= 1.5 * risk:
                        bank, sl = 0.75, (max(sl, entry) if side > 0 else min(sl, entry))
                    hit = r.low <= sl if side > 0 else r.high >= sl
                    if hit or r.t >= SQUARE_OFF:
                        px = sl if hit else r.close
                        rr = (px - entry) * side / risk
                        out.append((day, side, (bank + 0.5 * rr if bank else rr) - COST))
                        pos = None
                    else:
                        pos = (side, entry, sl, risk, bank)
                    continue
                if mode != "orb" or r.t > cut or pd.isna(r.vavg) or pd.isna(r.atr) or len(used) == 2:
                    continue
                up = r.close > r.vwap and r.e20 > r.e50 and (not htf or r.h20 > r.h50)
                dn = r.close < r.vwap and r.e20 < r.e50 and (not htf or r.h20 < r.h50)
                side = 0
                if not retest:
                    if r.close > hi and up and r.volume > r.vavg and 1 not in used:
                        side = 1
                    elif r.close < lo and dn and r.volume > r.vavg and -1 not in used:
                        side = -1
                else:
                    if r.close > hi and r.volume > r.vavg:
                        armed = 1
                    elif r.close < lo and r.volume > r.vavg:
                        armed = -1
                    elif armed == 1 and r.low <= hi + 0.2 * r.atr and r.close > hi and up and 1 not in used:
                        side = 1
                    elif armed == -1 and r.high >= lo - 0.2 * r.atr and r.close < lo and dn and -1 not in used:
                        side = -1
                if not side or (dirn and side * wret <= 0):
                    continue
                px, risk = float(r.close), float(r.atr)
                if risk <= 0:
                    continue
                used.add(side)
                armed = 0
                pos = (side, px, px - risk * side, risk, 0)
            if pos:
                side, entry, sl, risk, bank = pos
                rr = (rows.iloc[-1].close - entry) * side / risk
                out.append((day, side, (bank + 0.5 * rr if bank else rr) - COST))
        return pd.DataFrame(out, columns=["day", "side", "R"])

    rows2 = []
    combos = [("orb", rt, ct, nw, hf, hx, dn) for rt, ct, nw, hf, hx, dn in itertools.product(
        [False, True], [dtime(22, 0), dtime(21, 0)], [False, True], [False, True], [False, True], [False, True])]
    combos += [(m, False, dtime(22, 0), False, hf, hx, False) for m in ("momo", "eia")
               for hf in (False, True) for hx in (False, True)]
    for mo, rt, ct, nw, hf, hx, dn in combos:
        t = run2(d, rt, ct, nw, hf, hx, mo, dn)
        tr, te = t[t.day < cut], t[t.day >= cut]
        n, wr, tot, pf, dd = stats(t)
        rows2.append(dict(mode=mo, dir_of_open=dn, retest=rt, last_entry=str(ct)[:5], skip_wed=nw, half_at_1_5R=hf, htf=hx, trades=n,
                          win=round(wr), R=round(tot, 1), PF=round(pf, 2), maxDD=round(dd, 1),
                          train_R=round(stats(tr)[2], 1), test_R=round(stats(te)[2], 1),
                          test_PF=round(stats(te)[3], 2), test_n=stats(te)[0]))
    r2 = pd.DataFrame(rows2).sort_values("R", ascending=False)
    print("")
    print("### V2 crude ideas (US 19:30, SL 1 ATR, trail). Current live = mode orb with every other option False, 22:00")
    print("")
    print(md(r2))
