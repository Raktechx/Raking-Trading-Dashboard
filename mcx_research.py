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
