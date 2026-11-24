"""BTCUSD walk-forward research (Delta Exchange India public data, no API key).
Collects max daily history + funding rate, tests a grid of rule combinations, and picks
the best rule using ONLY past data (rolling 2y train -> next 6m test). Reports out-of-sample results."""
import time, json, urllib.request, itertools
import pandas as pd, numpy as np

BASE = "https://api.india.delta.exchange/v2/history/candles"
COST = 0.0007          # per side (fee + slippage)
TRAIN, TEST = 730, 182 # days

def fetch(sym, res="1d", start=1546300800):   # from 2019-01-01
    step = {"1d": 86400, "1h": 3600}[res]; end = int(time.time()); rows, t = [], start
    while t < end:
        t2 = min(t + step * 1900, end)
        try: rows += json.load(urllib.request.urlopen(
                f"{BASE}?resolution={res}&symbol={sym}&start={t}&end={t2}", timeout=30)).get("result", [])
        except Exception as e: print(f"fetch {sym} err: {type(e).__name__}")
        t = t2; time.sleep(0.3)
    if not rows: return None
    df = pd.DataFrame(rows).drop_duplicates("time").sort_values("time")
    df.index = pd.to_datetime(df["time"], unit="s").normalize()
    return df[["open", "high", "low", "close", "volume"]].astype(float)

def ret_of(d, pos):
    pos = pos.fillna(0).clip(-1, 1)
    return pos.shift().fillna(0) * d.close.pct_change().fillna(0) - pos.diff().abs().fillna(0) * COST

def latch(entry, exit_):
    p, cur = [], 0
    for en, ex in zip(entry, exit_):
        if cur and ex: cur = 0
        elif not cur and en: cur = 1
        p.append(cur)
    return pd.Series(p, index=entry.index, dtype=float)

def candidates(d, fund):
    c = d.close; C = {}
    for n in (5, 10, 20, 30, 45, 60, 90, 120):
        m = c.pct_change(n)
        C[f"TSMOM{n} long"] = (m > 0).astype(float)
        C[f"TSMOM{n} L/S"] = np.sign(m)
    for f, s in ((10, 30), (20, 50), (20, 100), (50, 200)):
        C[f"MA{f}/{s} long"] = (c.rolling(f).mean() > c.rolling(s).mean()).astype(float)
    for e, x in ((20, 10), (30, 15), (55, 20)):
        C[f"Donch{e}/{x} long"] = latch(c > c.rolling(e).max().shift(), c < c.rolling(x).min().shift())
    vol = c.pct_change().rolling(30).std() * np.sqrt(365)
    for n in (20, 60):
        base = (c.pct_change(n) > 0).astype(float)
        C[f"TSMOM{n} volTarget"] = (base * (0.5 / vol)).clip(0, 1)
        C[f"TSMOM{n} lowVol"] = base * (vol < vol.rolling(365, min_periods=90).median())
        if fund is not None:   # skip longs when funding is extreme (crowded longs)
            hot = fund > fund.rolling(90, min_periods=30).quantile(0.9)
            C[f"TSMOM{n} fundFilter"] = base * (~hot)
    C["Buy & hold"] = pd.Series(1.0, index=d.index)
    return C

def stats(r):
    if len(r) < 30 or r.std() == 0: return 0, 0, 0
    eq = (1 + r).cumprod(); yrs = len(r) / 365
    return eq.iloc[-1] ** (1 / yrs) - 1, r.mean() / r.std() * np.sqrt(365), (eq / eq.cummax() - 1).min()

if __name__ == "__main__":
    d = fetch("BTCUSD")
    fr = fetch("FUNDING:BTCUSD")
    fund = fr.close.reindex(d.index).ffill() if fr is not None else None
    print(f"BTCUSD daily: {len(d)} days {d.index[0]:%d %b %Y} -> {d.index[-1]:%d %b %Y}; "
          f"funding data: {'yes' if fund is not None else 'not available'}\n")
    C = candidates(d, fund); R = {k: ret_of(d, v) for k, v in C.items()}
    # walk-forward: each window pick best Sharpe on train (excluding buy&hold), trade it on test
    oos, picks, i = [], [], TRAIN
    while i < len(d):
        tr = slice(d.index[i - TRAIN], d.index[i - 1]); te = d.index[i:i + TEST]
        best = max((k for k in R if k != "Buy & hold"), key=lambda k: stats(R[k][tr])[1])
        oos.append(R[best].loc[te]); picks.append(f"{te[0]:%b %Y}: {best}"); i += TEST
    wf = pd.concat(oos); idx = wf.index
    print("## Walk-forward (rules chosen only on past data)")
    for name, r in (("Walk-forward pick", wf), ("Buy & hold", R["Buy & hold"].loc[idx])):
        c_, s, dd = stats(r)
        yrs = " | ".join(f"{y}: {100*((1+r[r.index.year==y]).prod()-1):+.0f}%" for y in sorted(set(idx.year)))
        print(f"- **{name}**: CAGR {100*c_:+.0f}%, Sharpe {s:.2f}, max DD {100*dd:.0f}%  ({yrs})")
    print("\nPicks per window: " + "; ".join(picks))
    print("\n## Each rule over the same out-of-sample period (top 10 by Sharpe)\n")
    print("| Rule | CAGR | Sharpe | Max DD |\n|---|---|---|---|")
    rows = sorted(((k, *stats(R[k].loc[idx])) for k in R), key=lambda x: -x[2])
    for k, c_, s, dd in rows[:10]:
        print(f"| {k} | {100*c_:+.0f}% | {s:.2f} | {100*dd:.0f}% |")
    print("\n1x, after 0.07%/side costs. Only the walk-forward line is a fair estimate; the top-10 table is hindsight.")
