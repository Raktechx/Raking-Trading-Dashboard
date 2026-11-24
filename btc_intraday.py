"""BTCUSD intraday + swing research (public Bitstamp hourly data, no API key).
Every intraday rule trades only WITH the daily trend (Carver-style ensemble regime).
Rules: hourly Donchian breakout, EMA cross, Larry Williams volatility breakout (flat by day end,
popular with Korean/Chinese crypto traders), Bollinger dip-buy in uptrend, US-session long.
Fair tests: fixed rules + walk-forward pick (2y train -> 6m test). Costs 0.07%/side."""
import time, json, urllib.request
import pandas as pd, numpy as np

COST, YR = 0.0007, 8760
UA = {"User-Agent": "Mozilla/5.0"}

def fetch_hourly(start=1514764800):              # 2018-01-01
    rows, t, end, fails = [], start, int(time.time()), 0
    while t < end and fails < 5:
        try:
            data = json.load(urllib.request.urlopen(urllib.request.Request(
                f"https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=3600&limit=1000&start={t}", headers=UA),
                timeout=30))["data"]["ohlc"]
        except Exception as e:
            fails += 1; print(f"fetch err: {type(e).__name__}"); time.sleep(3); continue
        if not data: break
        rows += data; nt = int(data[-1]["timestamp"]) + 3600
        if nt <= t: break
        t = nt; time.sleep(0.25)
    df = pd.DataFrame(rows).drop_duplicates("timestamp")
    df.index = pd.to_datetime(df["timestamp"].astype(int), unit="s")
    df = df.sort_index()[["open", "high", "low", "close", "volume"]].astype(float)
    return df[df.close > 0]

def latch(entry, exit_):
    p, cur, out = 0, 0, np.zeros(len(entry))
    for i, (en, ex) in enumerate(zip(entry, exit_)):
        if cur and ex: cur = 0
        elif not cur and en: cur = 1
        out[i] = cur
    return out

def rules(h):
    c, hi, lo = h.close, h.high, h.low
    day = h.index.floor("D")
    D = h.resample("D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    sig = pd.concat([np.sign(D.close.pct_change(n)) for n in (10, 20, 45, 90, 180)], axis=1).mean(axis=1)
    dvol = D.close.pct_change().rolling(30).std() * np.sqrt(365)
    swing_d = (sig.clip(0, 1) * (0.6 / dvol)).clip(0, 1)
    reg = sig.shift(1).reindex(day).fillna(0).values          # yesterday's completed daily trend
    up, dn = reg > 0, reg < 0
    last = np.r_[day[1:] != day[:-1], True]                   # last hour of each day -> flat
    S = lambda a: pd.Series(np.asarray(a, dtype=float), index=h.index)
    R = {}
    R["SWING daily ensemble volTgt"] = S(swing_d.shift(1).reindex(day).fillna(0).values)
    for n, m in ((24, 12), (48, 24), (96, 48)):
        L = latch((c > hi.rolling(n).max().shift()).values & up, (c < lo.rolling(m).min().shift()).values | ~up)
        Sh = latch((c < lo.rolling(n).min().shift()).values & dn, (c > hi.rolling(m).max().shift()).values | ~dn)
        R[f"Donchian{n}/{m}h long"] = S(L); R[f"Donchian{n}/{m}h L/S"] = S(L - Sh)
    for f, s in ((12, 48), (24, 96)):
        ef, es = c.ewm(span=f).mean(), c.ewm(span=s).mean()
        R[f"EMA{f}/{s}h long"] = S((ef > es).values & up)
        R[f"EMA{f}/{s}h L/S"] = S(((ef > es).values & up) * 1 - ((ef < es).values & dn) * 1)
    rng = (D.high - D.low).shift(1)
    for k in (0.3, 0.5, 0.7):
        tl = (D.open + k * rng).reindex(day).values; ts = (D.open - k * rng).reindex(day).values
        lg = pd.Series((c.values > tl) & up, index=h.index).groupby(day).cummax().values & ~last
        sh = pd.Series((c.values < ts) & dn, index=h.index).groupby(day).cummax().values & ~last
        R[f"VolBreak k{k} long"] = S(lg); R[f"VolBreak k{k} L/S"] = S(lg * 1 - sh * 1)
    mid, sd = c.rolling(48).mean(), c.rolling(48).std()
    R["BB dip-buy in uptrend"] = S(latch((c < mid - 2 * sd).values & up, (c > mid).values | ~up))
    R["US session long in uptrend"] = S(up & np.isin(h.index.hour, range(13, 21)))
    R["COMBO swing 50% + VolBreak0.5 L/S 50%"] = 0.5 * R["SWING daily ensemble volTgt"] + 0.5 * R["VolBreak k0.5 L/S"]
    R["Buy & hold"] = S(np.ones(len(h)))
    return R

def ret_of(h, pos):
    return pos.shift().fillna(0) * h.close.pct_change().fillna(0) - pos.diff().abs().fillna(0) * COST

def stats(r):
    if len(r) < 500 or r.std() == 0: return 0, 0, 0
    eq = (1 + r).cumprod()
    return eq.iloc[-1] ** (YR / len(r)) - 1, r.mean() / r.std() * np.sqrt(YR), (eq / eq.cummax() - 1).min()

def trade_stats(pos, r):
    seg = (pos.ne(pos.shift())).cumsum()
    first = pos.groupby(seg).first()
    tr = (r.groupby(seg).sum() - first.abs() * COST)[first != 0]   # include the exit cost
    yrs = len(r) / YR
    return (len(tr) / yrs if yrs else 0), (100 * (tr > 0).mean() if len(tr) else 0), (100 * tr.mean() if len(tr) else 0)

def line(name, pos, r):
    c_, s, dd = stats(r); n, w, a = trade_stats(pos, r)
    yrs = " ".join(f"{str(y)[2:]}:{100*((1+r[r.index.year==y]).prod()-1):+.0f}" for y in sorted(set(r.index.year)))
    return f"| {name} | {100*c_:+.0f}% | {s:.2f} | {100*dd:.0f}% | {n:.0f} | {w:.0f}% | {a:+.2f}% | {yrs} |"

if __name__ == "__main__":
    h = fetch_hourly()
    print(f"Bitstamp BTC/USD hourly: {len(h)} bars {h.index[0]:%d %b %Y} -> {h.index[-1]:%d %b %Y}\n")
    P = rules(h); R = {k: ret_of(h, v) for k, v in P.items()}
    TR, TE = 730 * 24, 182 * 24
    pool = [k for k in R if not k.startswith(("Buy", "SWING", "COMBO"))]
    oos, wpos, picks, i = [], [], [], TR
    while i < len(h):
        tr = h.index[i - TR:i]; te = h.index[i:i + TE]
        best = max(pool, key=lambda k: stats(R[k].loc[tr])[1])
        oos.append(R[best].loc[te]); wpos.append(P[best].loc[te]); picks.append(f"{te[0]:%b%y}:{best}"); i += TE
    wf, wp = pd.concat(oos), pd.concat(wpos); idx = wf.index
    hdr = "| Strategy | CAGR | Sharpe | Max DD | Trades/yr | Win % | Avg trade | Yearly % |\n|---|---|---|---|---|---|---|---|"
    print(f"## Fair test {idx[0]:%b %Y} -> {idx[-1]:%b %Y} (out-of-sample)\n\n{hdr}")
    print(line("Walk-forward intraday pick", wp, wf))
    for k in ("SWING daily ensemble volTgt", "COMBO swing 50% + VolBreak0.5 L/S 50%", "Buy & hold"):
        print(line(k, P[k].loc[idx], R[k].loc[idx]))
    print("\nPicks: " + "; ".join(picks))
    print(f"\n## Every rule, same period (sorted by Sharpe; fixed rules, but choosing the top one is hindsight)\n\n{hdr}")
    for k in sorted(R, key=lambda k: -stats(R[k].loc[idx])[1]):
        print(line(k, P[k].loc[idx], R[k].loc[idx]))
    print("\n1x, no leverage, after 0.07%/side fee+slippage, signals act on the next hourly bar.")
