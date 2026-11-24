"""BTCUSD futures backtest on Delta Exchange India public candles (no API key needed).
Compares 3 strategies on 1 year of 15m data, after costs. Prints a summary only."""
import time, json, urllib.request
import pandas as pd, numpy as np

BASE = "https://api.india.delta.exchange/v2/history/candles"
FEE = 0.0005 + 0.0002   # taker fee + slippage, each side
DAYS = 365

def fetch(res="15m", sym="BTCUSD"):
    step = {"15m": 900, "1h": 3600}[res]
    end = int(time.time()); start = end - DAYS * 86400
    rows, t = [], start
    while t < end:
        t2 = min(t + step * 1900, end)
        url = f"{BASE}?resolution={res}&symbol={sym}&start={t}&end={t2}"
        r = json.load(urllib.request.urlopen(url, timeout=30))
        rows += r.get("result", [])
        t = t2; time.sleep(0.3)
    df = pd.DataFrame(rows).drop_duplicates("time").sort_values("time")
    df["dt"] = pd.to_datetime(df["time"], unit="s")
    return df.set_index("dt")[["open", "high", "low", "close", "volume"]].astype(float)

def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def atr(d, n=14):
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(),
                    (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def simulate(d, sig, sl, tp, trail=None, max_bars=96):
    """sig: +1/-1 entry on next open. sl/tp: price arrays. Returns net % and R per trade."""
    out, i, n = [], 0, len(d)
    o, h, l, c = d.open.values, d.high.values, d.low.values, d.close.values
    while i < n - 2:
        s = sig[i]
        if s == 0 or np.isnan(sl[i]): i += 1; continue
        e = o[i + 1]; stop = sl[i]; tgt = tp[i] if tp is not None else None
        risk = abs(e - stop)
        if risk / e < 0.0015 or risk / e > 0.05: i += 1; continue
        j = i + 1; ex = None
        while j < min(n, i + 1 + max_bars):
            if s > 0:
                if l[j] <= stop: ex = stop; break
                if tgt and h[j] >= tgt: ex = tgt; break
                if trail is not None: stop = max(stop, c[j] - trail[j])
            else:
                if h[j] >= stop: ex = stop; break
                if tgt and l[j] <= tgt: ex = tgt; break
                if trail is not None: stop = min(stop, c[j] + trail[j])
            j += 1
        if ex is None: ex = c[min(j, n - 1)]
        gross = s * (ex - e) / e
        net = gross - 2 * FEE
        out.append((d.index[i + 1], net, net * e / risk))
        i = j + 1
    return pd.DataFrame(out, columns=["t", "ret", "r"])

def strat_vwap(d):
    day = d.index.date
    tp_ = (d.high + d.low + d.close) / 3
    vwap = (tp_ * d.volume).groupby(day).cumsum() / d.volume.groupby(day).cumsum()
    e20, e50 = ema(d.close, 20), ema(d.close, 50)
    hi, lo = d.high.rolling(8).max().shift(), d.low.rolling(8).min().shift()
    vol = d.volume > d.volume.rolling(20).mean().shift() * 1.5
    long = (d.close > hi) & (d.close > vwap) & (e20 > e50) & vol
    short = (d.close < lo) & (d.close < vwap) & (e20 < e50) & vol
    sig = np.where(long, 1, np.where(short, -1, 0))
    sl = np.where(long, d.low.values, np.where(short, d.high.values, np.nan))
    e = d.close.values
    tp = np.where(long, e + 2 * (e - sl), np.where(short, e - 2 * (sl - e), np.nan))
    return simulate(d, sig, sl, tp)

def strat_trend(d):
    a = atr(d); e20, e50 = ema(d.close, 20), ema(d.close, 50)
    e200h = ema(d.close.resample("1h").last(), 200).reindex(d.index, method="ffill")
    up = (e20 > e50) & (d.close > e200h); dn = (e20 < e50) & (d.close < e200h)
    long = up & (d.low <= e20) & (d.close > e20)
    short = dn & (d.high >= e20) & (d.close < e20)
    sig = np.where(long, 1, np.where(short, -1, 0)); e = d.close.values
    sl = np.where(long, e - 1.5 * a, np.where(short, e + 1.5 * a, np.nan))
    tp = np.where(long, e + 3 * a, np.where(short, e - 3 * a, np.nan))
    return simulate(d, sig, sl, tp)

def strat_donchian(d):
    a = atr(d); hi, lo = d.high.rolling(48).max().shift(), d.low.rolling(48).min().shift()
    long, short = d.close > hi, d.close < lo
    sig = np.where(long, 1, np.where(short, -1, 0)); e = d.close.values
    sl = np.where(long, e - 2 * a, np.where(short, e + 2 * a, np.nan))
    return simulate(d, sig, sl, None, trail=(2.5 * a).values, max_bars=400)

def report(name, t):
    if t.empty: return f"{name}: no trades"
    eq = (1 + t.ret).cumprod(); dd = (eq / eq.cummax() - 1).min()
    w = t.r[t.r > 0].sum(); ls = -t.r[t.r < 0].sum()
    m = t.set_index("t").ret.resample("ME").sum()
    return (f"{name}: trades {len(t)}, win {100*(t.ret>0).mean():.0f}%, total {t.r.sum():+.1f}R, "
            f"PF {w/ls if ls else 0:.2f}, return {100*(eq.iloc[-1]-1):+.1f}% (1x, after costs), "
            f"max DD {100*dd:.1f}%, green months {(m>0).sum()}/{len(m)}")

if __name__ == "__main__":
    d = fetch()
    print(f"BTCUSD 15m candles: {len(d)}  {d.index[0]:%d %b %Y} -> {d.index[-1]:%d %b %Y}")
    print(f"Buy & hold: {100*(d.close.iloc[-1]/d.close.iloc[0]-1):+.1f}%")
    for name, f in [("A VWAP+EMA breakout 1:2", strat_vwap),
                    ("B Trend pullback (1h EMA200)", strat_trend),
                    ("C Donchian breakout + trail", strat_donchian)]:
        print(report(name, f(d)))
