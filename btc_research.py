"""BTCUSD research v3 - long history walk-forward (public data, no API key).
Data: Bitstamp BTC/USD daily since 2014 (fallback Coinbase). Rules come from well-known sources:
time-series momentum (Moskowitz 2012; Liu & Tsyvinski crypto), Turtle/Donchian, Faber 200d MA,
Carver multi-speed trend ensemble + vol targeting, Chinese EMA144 trend filter.
Rule is chosen ONLY on past data (rolling 2y train -> next 6m test). Fixed ensemble needs no choosing."""
import time, json, urllib.request
import pandas as pd, numpy as np

COST = 0.0007          # per side (fee + slippage)
TRAIN, TEST = 730, 182 # days
UA = {"User-Agent": "Mozilla/5.0"}

def get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))

def fetch_bitstamp(start=1404172800):          # 2014-07-01
    rows, t, end = [], start, int(time.time())
    while t < end:
        try: data = get(f"https://www.bitstamp.net/api/v2/ohlc/btcusd/?step=86400&limit=1000&start={t}")["data"]["ohlc"]
        except Exception as e: print(f"bitstamp err: {type(e).__name__}"); break
        if not data: break
        rows += data; nt = int(data[-1]["timestamp"]) + 86400
        if nt <= t: break
        t = nt; time.sleep(0.3)
    if not rows: return None
    df = pd.DataFrame(rows).drop_duplicates("timestamp")
    df.index = pd.DatetimeIndex(pd.to_datetime(df["timestamp"].astype(int), unit="s")).normalize()
    return df.sort_index()[["open", "high", "low", "close", "volume"]].astype(float)

def fetch_coinbase(start=1420070400):           # 2015-01-01, 300 candles per call
    rows, t, end = [], start, int(time.time())
    while t < end:
        t2 = min(t + 86400 * 300, end)
        a = pd.Timestamp(t, unit="s").isoformat(); b = pd.Timestamp(t2, unit="s").isoformat()
        try: rows += get(f"https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=86400&start={a}&end={b}")
        except Exception as e: print(f"coinbase err: {type(e).__name__}")
        t = t2; time.sleep(0.4)
    if not rows: return None
    df = pd.DataFrame(rows, columns=["time", "low", "high", "open", "close", "volume"]).drop_duplicates("time")
    df.index = pd.DatetimeIndex(pd.to_datetime(df["time"], unit="s")).normalize()
    return df.sort_index()[["open", "high", "low", "close", "volume"]].astype(float)

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

def candidates(d):
    c = d.close; C = {}
    vol = c.pct_change().rolling(30).std() * np.sqrt(365)
    for n in (7, 14, 20, 30, 45, 60, 90, 120, 180):
        m = c.pct_change(n)
        C[f"TSMOM{n} long"] = (m > 0).astype(float)
        C[f"TSMOM{n} L/S"] = np.sign(m)
    for f, s in ((10, 30), (20, 50), (20, 100), (50, 200)):
        C[f"MA{f}/{s} long"] = (c.rolling(f).mean() > c.rolling(s).mean()).astype(float)
    C["Faber 200d MA long"] = (c > c.rolling(200).mean()).astype(float)
    C["CN EMA144 long"] = (c > c.ewm(span=144).mean()).astype(float)
    for e, x in ((20, 10), (55, 20)):
        C[f"Turtle{e}/{x} long"] = latch(c > c.rolling(e).max().shift(), c < c.rolling(x).min().shift())
    # Carver-style ensemble: average of several trend speeds (no single lookback to overfit)
    sig = pd.concat([np.sign(c.pct_change(n)) for n in (10, 20, 45, 90, 180)], axis=1).mean(axis=1)
    C["Ensemble long"] = sig.clip(0, 1)
    C["Ensemble L/S"] = sig
    C["Ensemble long volTgt"] = (sig.clip(0, 1) * (0.6 / vol)).clip(0, 1)
    C["Ensemble long +200MA"] = sig.clip(0, 1) * (c > c.rolling(200).mean())
    C["Ensemble L/S volTgt"] = (sig * (0.6 / vol)).clip(-1, 1)
    C["TSMOM20 lowVol"] = (c.pct_change(20) > 0) * (vol < vol.rolling(365, min_periods=90).median()).astype(float)
    C["Buy & hold"] = pd.Series(1.0, index=d.index)
    return C

def stats(r):
    if len(r) < 30 or r.std() == 0: return 0, 0, 0
    eq = (1 + r).cumprod(); yrs = len(r) / 365
    return eq.iloc[-1] ** (1 / yrs) - 1, r.mean() / r.std() * np.sqrt(365), (eq / eq.cummax() - 1).min()

def line(name, r):
    c_, s, dd = stats(r)
    yrs = " ".join(f"{str(y)[2:]}:{100*((1+r[r.index.year==y]).prod()-1):+.0f}" for y in sorted(set(r.index.year)))
    return f"| {name} | {100*c_:+.0f}% | {s:.2f} | {100*dd:.0f}% | {yrs} |"

if __name__ == "__main__":
    d, src = fetch_bitstamp(), "Bitstamp"
    if d is None or len(d) < 2000: d, src = fetch_coinbase(), "Coinbase"
    d = d[d.close > 0]
    print(f"{src} BTC/USD daily: {len(d)} days {d.index[0]:%d %b %Y} -> {d.index[-1]:%d %b %Y}\n")
    C = candidates(d); R = {k: ret_of(d, v) for k, v in C.items()}
    oos, picks, i = [], [], TRAIN
    while i < len(d):
        tr = slice(d.index[i - TRAIN], d.index[i - 1]); te = d.index[i:i + TEST]
        best = max((k for k in R if k != "Buy & hold"), key=lambda k: stats(R[k][tr])[1])
        oos.append(R[best].loc[te]); picks.append(f"{te[0]:%b%y}:{best}"); i += TEST
    wf = pd.concat(oos); idx = wf.index
    print(f"## Fair test {idx[0]:%b %Y} -> {idx[-1]:%b %Y} (out-of-sample)\n")
    print("| Strategy | CAGR | Sharpe | Max DD | Yearly % |\n|---|---|---|---|---|")
    print(line("Walk-forward pick", wf))
    for k in ("Ensemble long", "Ensemble long volTgt", "Ensemble long +200MA", "Ensemble L/S volTgt", "Buy & hold"):
        print(line(k, R[k].loc[idx]))
    print("\nPicks: " + "; ".join(picks))
    print("\n## All rules, same period (top 12 by Sharpe, hindsight)\n")
    print("| Rule | CAGR | Sharpe | Max DD | Yearly % |\n|---|---|---|---|---|")
    for k in sorted(R, key=lambda k: -stats(R[k].loc[idx])[1])[:12]:
        print(line(k, R[k].loc[idx]))
    print("\n1x, after 0.07%/side costs. Ensembles are fixed (not chosen), so they are fair too.")
