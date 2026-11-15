"""Raking the Trader - Combined Strategy (as per the user's document).

Instruments: Nifty 50, Bank Nifty index options - 5 minute candles.
Price, VWAP, EMA and volume come from the near-month FUTURE (the index has no volume);
ATM strike comes from the spot index; OI checks use the option contracts.

BUY CE - all together on a closed 5-min candle:
 1. Trend : close > VWAP  and  EMA20 > EMA50
 2. OI    : ATM CALL long build-up (premium up + OI up vs 3 candles ago)
            and PUT writing at ATM + 2 strikes below (their OI up, premium down)
 3. Entry : after 9:30, close breaks the 9:15-9:30 high with volume > average of previous 20 candles
 4. SL    : previous candle low
 5. Target: entry + 2 x risk  (1:2)
 6. Limit : max 2 trades a day, no new entry after 3:00 PM
BUY PE - mirror image (VWAP below, EMA20 < EMA50, ATM PUT long build-up + CALL writing at ATM + 2
strikes above, range low breaks with volume, SL previous candle high).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time, timedelta

import numpy as np
import pandas as pd

from .config import STRATEGY as S


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def r05(x: float) -> float:
    return round(round(float(x) / 0.05) * 0.05, 2)


def bar() -> timedelta:
    return timedelta(minutes=S["candle_minutes"])


# ------------------------------------------------------------------ inputs
def prepare_fut(df: pd.DataFrame) -> pd.DataFrame:
    """Future candles (continuous, a few earlier days included for EMA50 warm-up)."""
    d = df.copy().sort_values("ts").reset_index(drop=True)
    d["day"] = d["ts"].dt.date
    d["bar_close"] = d["ts"] + bar()
    tp = (d["high"] + d["low"] + d["close"]) / 3.0
    vol = d["volume"].clip(lower=0).astype(float)
    cum_v = vol.groupby(d["day"]).cumsum()
    d["vwap"] = ((tp * vol).groupby(d["day"]).cumsum() / cum_v.replace(0, np.nan)).fillna(tp)
    d["ema_f"] = d["close"].ewm(span=S["ema_fast"], adjust=False).mean()
    d["ema_s"] = d["close"].ewm(span=S["ema_slow"], adjust=False).mean()
    d["vol_avg"] = vol.shift(1).rolling(S["volume_avg_candles"], min_periods=S["volume_avg_candles"]).mean()
    pc = d["close"].shift(1)
    tr = np.maximum(d["high"] - d["low"], np.maximum((d["high"] - pc).abs(), (d["low"] - pc).abs()))
    d["atr"] = tr.ewm(alpha=1 / S["atr_period"], adjust=False).mean()
    return d


@dataclass
class IndexDay:
    """Everything the strategy needs for one index on one day."""
    idx: str
    step: int
    expiry: str                       # option expiry used that day
    fut: pd.DataFrame                 # prepare_fut() output (may include earlier days)
    spot: pd.DataFrame                # spot candles of the day (ts, close ...)
    opts: dict = field(default_factory=dict)   # (strike, "CE"/"PE") -> DataFrame(ts, close, oi)
    fut_symbol: str = ""
    lot_size: int | None = None
    skip_oi: bool = False             # price-only backtest (no option OI data available)


def _asof(df: pd.DataFrame | None, ts, col: str):
    if df is None or df.empty:
        return None
    i = int(np.searchsorted(df["ts"].values, pd.Timestamp(ts).to_datetime64(), side="right")) - 1
    if i < 0:
        return None
    return float(df[col].iat[i])


def atm_strike(d: IndexDay, ts) -> int | None:
    px = _asof(d.spot, ts, "close")
    if px is None:
        return None
    return int(round(px / d.step) * d.step)


def _change(df, ts, col):
    """(now, before, pct) comparing the candle at ts with the one N candles earlier."""
    prev_ts = ts - S["oi_lookback_candles"] * bar()
    now, before = _asof(df, ts, col), _asof(df, prev_ts, col)
    if now is None or before is None:
        return None, None, None
    pct = (now - before) / before * 100 if before else 0.0
    return now, before, pct


def oi_check(d: IndexDay, ts, side: str) -> dict:
    """side CE -> ATM call long build-up + put writing (ATM and below).
       side PE -> ATM put long build-up + call writing (ATM and above)."""
    atm = atm_strike(d, ts)
    out = {"atm": atm, "atm_ok": False, "writing_ok": False, "atm_oi_pct": None, "atm_px_pct": None,
           "writing_oi_pct": None, "writing_strikes": []}
    if atm is None:
        return out
    leg = d.opts.get((atm, side))
    _, _, px_pct = _change(leg, ts, "close")
    _, _, oi_pct = _change(leg, ts, "oi")
    out["atm_px_pct"], out["atm_oi_pct"] = px_pct, oi_pct
    out["atm_ok"] = px_pct is not None and oi_pct is not None and px_pct > 0 and oi_pct >= S["atm_oi_min_pct"]

    w_type = "PE" if side == "CE" else "CE"
    sign = -1 if side == "CE" else 1
    strikes = [atm + sign * i * d.step for i in range(S["writing_strikes"])]
    out["writing_strikes"] = strikes
    oi_now = oi_before = px_now = px_before = 0.0
    for k in strikes:
        f = d.opts.get((k, w_type))
        a, b, _ = _change(f, ts, "oi")
        c, e, _ = _change(f, ts, "close")
        if a is None or c is None:
            return out          # a strike is missing -> no confirmation (strict)
        oi_now, oi_before, px_now, px_before = oi_now + a, oi_before + b, px_now + c, px_before + e
    w_pct = (oi_now - oi_before) / oi_before * 100 if oi_before else 0.0
    out["writing_oi_pct"] = w_pct
    price_ok = (px_now < px_before) if S["writing_needs_price_fall"] else True
    out["writing_ok"] = w_pct >= S["writing_oi_min_pct"] and price_ok
    return out


def orb_levels(day_fut: pd.DataFrame):
    first = day_fut["ts"].iloc[0]
    start = first.replace(hour=9, minute=15, second=0, microsecond=0)
    orb = day_fut[day_fut["bar_close"] <= start + timedelta(minutes=S["orb_minutes"])]
    if len(orb) < S["orb_minutes"] // S["candle_minutes"]:
        return None
    return float(orb["high"].max()), float(orb["low"].min())


def evaluate(d: IndexDay, pos: int, orb, full: bool = True) -> dict:
    """All conditions on candle `pos` of d.fut for both sides."""
    row = d.fut.loc[pos]
    oh, ol = orb
    c = float(row["close"])
    atr = float(row["atr"]) or float(row["high"] - row["low"]) or 1.0
    vol_ok = bool(pd.notna(row["vol_avg"]) and row["volume"] > row["vol_avg"])
    start = d.fut["ts"].searchsorted(row["ts"].normalize())
    day_closes = d.fut["close"].to_numpy()[start:pos + 1]
    w = S["breakout_window"]

    def recent_break(outside) -> bool:
        # closed beyond the level now, and was still inside the range within the last `w` candles
        if not outside(day_closes[-1]):
            return False
        inside = [i for i, x in enumerate(day_closes) if not outside(x)]
        return bool(inside) and (len(day_closes) - 1 - inside[-1]) <= w

    res = {}
    for side in ("CE", "PE"):
        if side == "CE":
            trend = c > row["vwap"] and row["ema_f"] > row["ema_s"]
            brk = recent_break(lambda x: x > oh)
        else:
            trend = c < row["vwap"] and row["ema_f"] < row["ema_s"]
            brk = recent_break(lambda x: x < ol)
        if not full and not (trend and brk and vol_ok):
            oi = {"atm": None, "atm_ok": False, "writing_ok": False, "atm_oi_pct": None,
                  "atm_px_pct": None, "writing_oi_pct": None, "writing_strikes": []}
        elif d.skip_oi:
            oi = {"atm": atm_strike(d, row["ts"]), "atm_ok": True, "writing_ok": True, "atm_oi_pct": None,
                  "atm_px_pct": None, "writing_oi_pct": None, "writing_strikes": [], "skipped": True}
        else:
            oi = oi_check(d, row["ts"], side)
        res[side] = {"trend": bool(trend), "breakout": bool(brk), "volume": vol_ok,
                     "atm_buildup": bool(oi["atm_ok"]), "writing": bool(oi["writing_ok"]), "oi": oi}
        res[side]["all"] = all(res[side][k] for k in ("trend", "breakout", "volume", "atm_buildup", "writing"))
    res["row"] = row
    res["atr"] = atr
    return res


# ------------------------------------------------------------------ trades
def make_signal(d: IndexDay, pos: int, side: str, ev: dict, orb) -> dict | None:
    f = d.fut
    row, prev = f.loc[pos], f.loc[pos - 1]
    entry = r05(row["close"])
    sl = r05(prev["low"] if side == "CE" else prev["high"])
    risk = (entry - sl) if side == "CE" else (sl - entry)
    if risk <= 0 or risk > S["max_risk_atr"] * ev["atr"]:
        return None
    risk = r05(risk)
    tp = r05(entry + S["reward_risk"] * risk) if side == "CE" else r05(entry - S["reward_risk"] * risk)
    oi = ev[side]["oi"]
    strike = oi["atm"]
    opt_px = _asof(d.opts.get((strike, side)), row["ts"], "close")
    w = "Put" if side == "CE" else "Call"
    lvl = orb[0] if side == "CE" else orb[1]
    if oi.get("skipped"):
        oi_lines = ["OI checks skipped (price-only backtest - no option OI data)"]
    else:
        oi_lines = [
            f"ATM {strike} {side} long build-up: premium {oi['atm_px_pct']:+.1f}%, OI {oi['atm_oi_pct']:+.1f}%",
            f"{w} writing ({', '.join(str(k) for k in oi['writing_strikes'])}): OI {oi['writing_oi_pct']:+.1f}%"]
    reasons = [
        f"Trend: price {'above' if side == 'CE' else 'below'} VWAP {row['vwap']:.1f}, "
        f"EMA20 {'>' if side == 'CE' else '<'} EMA50",
        *oi_lines,
        f"Opening range {'high' if side == 'CE' else 'low'} {lvl:.1f} broken, "
        f"volume {row['volume'] / row['vol_avg']:.1f}x (vs 20-candle avg)",
        f"SL = previous candle {'low' if side == 'CE' else 'high'}, target 1:{S['reward_risk']:.0f}",
    ]
    sig = {
        "id": f"{row['day']}|{d.idx}|{row['bar_close']:%H:%M}",
        "index": d.idx, "day": str(row["day"]), "side": side,
        "signal_time": row["bar_close"].strftime("%H:%M"),
        "future": d.fut_symbol, "entry": entry, "sl": sl, "tp": tp, "risk_pts": risk,
        "strike": strike, "expiry": d.expiry, "option": f"{d.idx} {strike} {side}",
        "opt_entry": r05(opt_px) if opt_px else None, "opt_sl": None, "opt_tp": None,
        "orb_high": orb[0], "orb_low": orb[1], "vwap": round(float(row["vwap"]), 2),
        "volume_x": round(float(row["volume"] / row["vol_avg"]), 2),
        "atm_oi_pct": None if oi["atm_oi_pct"] is None else round(oi["atm_oi_pct"], 2),
        "writing_oi_pct": None if oi["writing_oi_pct"] is None else round(oi["writing_oi_pct"], 2),
        "lot_size": d.lot_size, "reasons": reasons, "_pos": int(pos),
    }
    if opt_px:
        mv = S["assumed_delta"] * risk
        sig["opt_sl"] = max(r05(opt_px - mv), 0.05)
        sig["opt_tp"] = r05(opt_px + S["reward_risk"] * mv)
    return sig


def simulate(sig: dict, d: IndexDay) -> dict:
    """Walk candles after the signal. SL and TP in the same candle -> SL (conservative)."""
    f = d.fut
    sig_ts = pd.Timestamp(f"{sig['day']} {sig['signal_time']}").tz_localize(f["ts"].dt.tz) - bar()
    after = f[(f["day"] == pd.Timestamp(sig["day"]).date()) & (f["ts"] > sig_ts)]
    xt = _t(S["exit_time"])
    leg = d.opts.get((sig["strike"], sig["side"]))
    for _, row in after.iterrows():
        hit = None
        if sig["side"] == "CE":
            if row["low"] <= sig["sl"]:
                hit = ("SL", sig["sl"], -1.0)
            elif row["high"] >= sig["tp"]:
                hit = ("TP", sig["tp"], S["reward_risk"])
        else:
            if row["high"] >= sig["sl"]:
                hit = ("SL", sig["sl"], -1.0)
            elif row["low"] <= sig["tp"]:
                hit = ("TP", sig["tp"], S["reward_risk"])
        if hit is None and row["bar_close"].time() >= xt:
            px = float(row["close"])
            r = (px - sig["entry"]) / sig["risk_pts"] if sig["side"] == "CE" else (sig["entry"] - px) / sig["risk_pts"]
            hit = ("TIME", r05(px), round(r, 2))
        if hit:
            opt_exit = _asof(leg, row["ts"], "close")
            pts = (hit[1] - sig["entry"]) if sig["side"] == "CE" else (sig["entry"] - hit[1])
            return {"result": hit[0], "exit": hit[1], "exit_time": row["bar_close"].strftime("%H:%M"),
                    "r": hit[2], "points": round(pts, 2),
                    "opt_exit": r05(opt_exit) if opt_exit else None,
                    "opt_points": round(opt_exit - sig["opt_entry"], 2) if opt_exit and sig.get("opt_entry") else None,
                    "_exit_ts": row["ts"]}
    return {"result": "OPEN", "exit": None, "exit_time": None, "r": None, "points": None,
            "opt_exit": None, "opt_points": None, "_exit_ts": None}


def day_trades(day, days: dict[str, IndexDay]) -> tuple[list[dict], dict]:
    """Run the whole day for all indices in time order, applying the limits.
    Returns (trades, last evaluation per index for the dashboard checklist)."""
    es, le = _t(S["entry_start"]), _t(S["last_entry"])
    frames, state = {}, {}
    for idx, d in days.items():
        dd = d.fut[d.fut["day"] == day]
        if dd.empty:
            continue
        frames[idx] = (dd, orb_levels(dd), {t: p for p, t in zip(dd.index, dd["ts"])})
        state[idx] = {"n": 0, "busy": None}
    total, trades, checks = 0, [], {}
    times = sorted({t for dd, _, _ in frames.values() for t in dd["ts"]})
    last_ts = {idx: dd["ts"].iloc[-1] for idx, (dd, _, _) in frames.items()}
    for ts in times:
        for idx, (dd, orb, pos_of) in frames.items():
            if orb is None or ts not in pos_of:
                continue
            pos = pos_of[ts]
            bc = dd.loc[pos, "bar_close"].time()
            if bc <= es:
                continue
            ev = evaluate(days[idx], pos, orb, full=(ts == last_ts[idx]))
            checks[idx] = ev
            st = state[idx]
            if bc > le or total >= S["max_trades_per_day"] or st["n"] >= S["max_trades_per_index"]:
                continue
            if st["busy"] == "open" or (st["busy"] is not None and ts <= st["busy"]):
                continue
            for side in ("CE", "PE"):
                if not ev[side]["all"]:
                    continue
                sig = make_signal(days[idx], pos, side, ev, orb)
                if not sig:
                    continue
                res = simulate(sig, days[idx])
                sig.update(res)
                trades.append(sig)
                st["n"] += 1
                total += 1
                st["busy"] = res["_exit_ts"] if res["_exit_ts"] is not None else "open"
                break
    return trades, checks


def public(sig: dict) -> dict:
    return {k: v for k, v in sig.items() if not k.startswith("_")}
