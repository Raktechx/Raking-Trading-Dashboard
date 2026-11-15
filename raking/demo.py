"""Demo data (simulated) - lets the dashboard, backtest and tests run without Upstox.
Same seed for the same index + day, so demo signals are stable too."""
from __future__ import annotations

import zlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import config
from .strategy import IndexDay, prepare_fut

BASE = {"NIFTY": 25800.0, "BANKNIFTY": 58000.0}


def _seed(idx: str, d: date) -> int:
    return zlib.crc32(f"{idx}{d}".encode())


def day_kind(idx: str, d: date) -> float:
    """+1 bull, -1 bear, 0 range, +2/-2 fake breakout that reverses (repeatable)."""
    return [1.0, -1.0, 0.0, 1.0, 0.0, 2.0, -2.0][_seed(idx, d) % 7]


def make_day(idx: str, d: date, start_px: float, kind: float | None = None, upto=None):
    kind = day_kind(idx, d) if kind is None else kind
    rng = np.random.default_rng(_seed(idx, d))
    step = config.INDICES[idx]["step"]
    scale = start_px / 25000
    ts = pd.date_range(f"{d} 09:15", f"{d} 15:25", freq="5min", tz=config.TZ)
    n = len(ts)
    sgn = np.sign(kind)
    drift = np.where(np.arange(n) >= 3, sgn * 7 * scale, 0.0)
    if abs(kind) == 2:   # trap: breaks out, then reverses hard
        drift[9:] = -sgn * 9 * scale
    elif kind:           # trend stalls in the afternoon
        drift[45:] *= 0.2
    ret = drift + rng.normal(0, 5 * scale, n)
    close = start_px + np.cumsum(ret)
    open_ = np.r_[start_px, close[:-1]]
    hi = np.maximum(open_, close) + np.abs(rng.normal(0, 3 * scale, n))
    lo = np.minimum(open_, close) - np.abs(rng.normal(0, 3 * scale, n))
    spot = pd.DataFrame({"ts": ts, "open": open_, "high": hi, "low": lo, "close": close,
                         "volume": 0.0, "oi": 0.0})
    basis = start_px * 0.004
    vol = 8000 + 2500 * np.abs(ret) / scale + rng.normal(0, 800, n)
    fut = spot.copy()
    for k in ("open", "high", "low", "close"):
        fut[k] = spot[k] + basis
    fut["volume"] = np.clip(vol, 500, None).round()
    fut["oi"] = 1_500_000 * np.cumprod(1 + (0.0008 if kind else 0) + rng.normal(0, 0.0003, n))

    lo_k = int((spot["low"].min() // step) * step) - 4 * step
    hi_k = int(-(-spot["high"].max() // step) * step) + 4 * step
    opts = {}
    t_left = np.linspace(1.0, 0.85, n)
    for k in range(lo_k, hi_k + step, step):
        tv = 120 * scale * np.exp(-np.abs(close - k) / (6 * step)) * t_left
        ce = np.maximum(close - k, 0) + tv
        pe = np.maximum(k - close, 0) + tv
        for typ, px in (("CE", ce), ("PE", pe)):
            g = 0.0
            if kind > 0:
                g = 0.010 if (typ == "CE" or k <= close[0] + step) else 0.002
            elif kind < 0:
                g = 0.010 if (typ == "PE" or k >= close[0] - step) else 0.002
            oi = 200_000 * np.cumprod(1 + g + rng.normal(0, 0.002, n))
            opts[(k, typ)] = pd.DataFrame({"ts": ts, "open": px, "high": px, "low": px,
                                           "close": np.round(px, 2), "volume": 1000.0, "oi": oi})
    if upto is not None:
        spot, fut = spot[spot["ts"] <= upto], fut[fut["ts"] <= upto]
        opts = {k: v[v["ts"] <= upto] for k, v in opts.items()}
    return spot, fut, opts


def _series(idx: str, start: date, end: date, upto=False, end_kind=None):
    px = BASE[idx]
    spots, futs, opts_by_day = {}, [], {}
    ds = sorted({x.date() for x in pd.bdate_range(start, end)} | ({end} if upto is not False else set()))
    for d in ds:
        u = upto if (d == end and upto not in (False, None)) else None
        k = end_kind if (d == end and end_kind is not None) else (
            end_kind if (d == ds[-2] if len(ds) > 1 else False) and end_kind is not None else None)
        s, f, o = make_day(idx, d, px, kind=k, upto=u)
        if s.empty:
            continue
        px = float(s["close"].iloc[-1])
        spots[d], opts_by_day[d] = s, o
        futs.append(f)
    return spots, futs, opts_by_day


def backtest_days(idx: str, start: date, end: date) -> dict[date, IndexDay]:
    spots, futs, opts = _series(idx, start - timedelta(days=7), end, False)
    fut = prepare_fut(pd.concat(futs, ignore_index=True))
    step = config.INDICES[idx]["step"]
    return {d: IndexDay(idx, step, str(d + timedelta(days=2)), fut, spots[d], opts[d], f"{idx} FUT (demo)", None)
            for d in spots if d >= start}


def live_day(idx: str, today: date) -> IndexDay | None:
    upto = pd.Timestamp.now(tz=config.TZ) - pd.Timedelta(minutes=5)
    if upto.date() != today or upto.time() < pd.Timestamp("09:20").time():
        upto = None   # market closed -> show the full demo day
    # demo today: Nifty bullish, Bank Nifty bearish, so both signal types can be seen
    spots, futs, opts = _series(idx, today - timedelta(days=7), today, upto, 1.0 if idx == "NIFTY" else -1.0)
    if today not in spots:
        return None
    fut = prepare_fut(pd.concat(futs, ignore_index=True))
    step = config.INDICES[idx]["step"]
    return IndexDay(idx, step, str(today + timedelta(days=2)), fut, spots[today], opts[today],
                    f"{idx} FUT (demo)", 75 if idx == "NIFTY" else 35)
