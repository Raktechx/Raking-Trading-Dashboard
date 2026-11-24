"""BTCUSD research backtest v2 - Delta Exchange India public 1h candles (no API key).
Tests research-backed strategies, after costs, year by year (robustness check)."""
import time, json, urllib.request
import pandas as pd, numpy as np

BASE = "https://api.india.delta.exchange/v2/history/candles"
COST = 0.0007          # per side: 0.05% taker fee + 0.02% slippage
YEARS = 3

def fetch(sym="BTCUSD"):
    end = int(time.time()); t = end - YEARS * 365 * 86400; rows = []
    while t < end:
        t2 = min(t + 3600 * 1900, end)
        url = f"{BASE}?resolution=1h&symbol={sym}&start={t}&end={t2}"
        try: rows += json.load(urllib.request.urlopen(url, timeout=30)).get("result", [])
        except Exception as e: print("fetch err", e)
        t = t2; time.sleep(0.3)
    df = pd.DataFrame(rows).drop_duplicates("time").sort_values("time")
    df.index = pd.to_datetime(df["time"], unit="s")
    return df[["open", "high", "low", "close", "volume"]].astype(float)

def run(h, pos):
    """pos: hourly target position (-1..1) decided at bar close, held next bar."""
    pos = pos.fillna(0).clip(-1, 1)
    r = h.close.pct_change().fillna(0)
    return pos.shift().fillna(0) * r - pos.diff().abs().fillna(0) * COST

def daily_to_hourly(h, s):
    return s.shift(1).reindex(h.index, method="ffill")   # use yesterday's signal only

def strategies(h):
    d = h.close.resample("1D").last()
    dr = d.pct_change()
    vol = dr.rolling(30).std() * np.sqrt(365)
    S = {}
    # 1 Time-series momentum (Moskowitz; Han et al. - strongest crypto evidence)
    for n in (20, 60):
        S[f"TSMOM {n}d long/short"] = daily_to_hourly(h, np.sign(d.pct_change(n)))
        S[f"TSMOM {n}d long-only"] = daily_to_hourly(h, (d.pct_change(n) > 0).astype(float))
    # 2 Multi-horizon trend + volatility targeting (40% annual vol)
    sig = (np.sign(d.pct_change(10)) + np.sign(d.pct_change(30)) + np.sign(d.pct_change(90))) / 3
    S["Trend blend + vol target"] = daily_to_hourly(h, (sig * (0.4 / vol)).clip(-1, 1))
    # 3 Turtle / Donchian daily 20-in 10-out, long/short
    hi20, lo20 = d.rolling(20).max().shift(), d.rolling(20).min().shift()
    hi10, lo10 = d.rolling(10).max().shift(), d.rolling(10).min().shift()
    p, cur = [], 0
    for c, a, b, x, y in zip(d, hi20, lo20, hi10, lo10):
        if cur == 0: cur = 1 if c > a else (-1 if c < b else 0)
        elif cur == 1 and c < y: cur = 0
        elif cur == -1 and c > x: cur = 0
        p.append(cur)
    S["Turtle 20/10 daily"] = daily_to_hourly(h, pd.Series(p, index=d.index))
    # 4 Seasonality: long 21:00-23:00 UTC (Padysak & Vojtko 2022)
    hr = pd.Series(h.index.hour, index=h.index)
    S["Seasonality 21-23 UTC"] = hr.isin([21, 22]).astype(float)
    # 5 Seasonality only in uptrend (price > 10-day MA at 00 UTC)
    up = daily_to_hourly(h, (d > d.rolling(10).mean()).astype(float))
    S["Seasonality + uptrend"] = hr.isin([21, 22]).astype(float) * up
    # 6 Combo: trend blend (vol-targeted) + seasonality overlay
    S["Combo trend + season"] = (S["Trend blend + vol target"] * 0.7 + S["Seasonality + uptrend"] * 0.3)
    S["Buy & hold"] = pd.Series(1.0, index=h.index)
    return S

def stats(ret):
    eq = (1 + ret).cumprod(); yrs = len(ret) / 8760
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else 0
    sharpe = ret.mean() / ret.std() * np.sqrt(8760) if ret.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return cagr, sharpe, dd

if __name__ == "__main__":
    h = fetch()
    print(f"BTCUSD 1h candles: {len(h)}  {h.index[0]:%d %b %Y} -> {h.index[-1]:%d %b %Y}\n")
    yrs = sorted(set(h.index.year))
    print("| Strategy | CAGR | Sharpe | Max DD | " + " | ".join(str(y) for y in yrs) + " |")
    print("|---|---|---|---|" + "---|" * len(yrs))
    for name, pos in strategies(h).items():
        ret = run(h, pos); c, s, dd = stats(ret)
        by = [f"{100*((1+ret[ret.index.year==y]).prod()-1):+.0f}%" for y in yrs]
        print(f"| {name} | {100*c:+.0f}% | {s:.2f} | {100*dd:.0f}% | " + " | ".join(by) + " |")
    print("\n1x leverage, after 0.07%/side costs. Signals use only past data (next-bar execution).")
