"""Signal quality score (0-100, grade A/B/C).

Extra analysis on top of the fixed strategy rules, and a pre-check before any alert:
a signal scoring below STRATEGY["min_quality"] (or that cannot be scored) is blocked - logged but never sent - and its
outcome is still tracked, so the log proves whether the pre-check really saves losing trades.
"""
from __future__ import annotations

import pandas as pd

from .upstox import skey

VIX_KEY = "NSE_INDEX|India VIX"
GRADES = (("A", 70), ("B", 55), ("C", 40), ("D", 0))

# component -> max points (total 100)
WEIGHTS = {"Trend strength": 15, "Breakout candle": 15, "Volume": 10, "OI build-up": 15,
           "PCR": 10, "Room to OI wall": 15, "Option IV": 10, "India VIX": 5, "Market alignment": 5}


def _clip(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def grade(score: float | None) -> str | None:
    if score is None:
        return None
    return next(g for g, lo in GRADES if score >= lo)


def bias(d) -> int:
    """+1 bullish / -1 bearish / 0 mixed, from the latest candle of an IndexDay."""
    try:
        r = d.fut.iloc[-1]
        if r["close"] > r["vwap"] and r["ema_f"] > r["ema_s"]:
            return 1
        if r["close"] < r["vwap"] and r["ema_f"] < r["ema_s"]:
            return -1
    except Exception:
        pass
    return 0


def vix(api) -> float | None:
    try:
        q = api.quotes([VIX_KEY])
        v = next(iter(q.values()), {}) if q else {}
        return float(v.get("last_price")) if v.get("last_price") else None
    except Exception:
        return None


def _leg(row: dict, side: str) -> dict:
    return row.get("call_options" if side == "CE" else "put_options") or {}


def _md(row: dict, side: str, k: str) -> float:
    try:
        return float((_leg(row, side).get("market_data") or {}).get(k) or 0)
    except (TypeError, ValueError):
        return 0.0


def score(sig: dict, d, chain: list | None = None, vix_now: float | None = None,
          market: dict | None = None) -> dict:
    """Returns {score, grade, score_parts, score_notes}. Unknown inputs score half (neutral)."""
    side, up = sig["side"], sig["side"] == "CE"
    pts, plus, minus = {}, [], []

    def put(name, frac, good=None, bad=None):
        pts[name] = round(WEIGHTS[name] * _clip(frac), 1)
        if frac >= 0.7 and good:
            plus.append(good)
        elif frac <= 0.3 and bad:
            minus.append(bad)

    f = d.fut
    pos = sig.get("_pos")
    row = f.loc[pos] if pos is not None and pos in f.index else f.iloc[-1]
    atr = float(row["atr"]) or float(row["high"] - row["low"]) or 1.0
    c, o, h, lo = (float(row[k]) for k in ("close", "open", "high", "low"))

    # 1. trend strength (distance from VWAP + EMA gap, penalise over-stretched moves)
    dist = abs(c - float(row["vwap"])) / atr
    gap = abs(float(row["ema_f"]) - float(row["ema_s"])) / atr
    fr = 0.5 * _clip(dist / 1.0) + 0.5 * _clip(gap / 0.5)
    if dist > 3:
        fr *= 0.5
        minus.append(f"price stretched {dist:.1f} ATR from VWAP")
    put("Trend strength", fr, "strong trend (clear VWAP and EMA gap)", "weak trend, price close to VWAP")

    # 2. breakout candle quality (body size + close near the extreme, right colour)
    rng = (h - lo) or 1e-9
    body = abs(c - o) / rng if (c > o) == up else 0.0
    loc = (c - lo) / rng if up else (h - c) / rng
    put("Breakout candle", 0.5 * body + 0.5 * loc, "strong breakout candle closing near its " + ("high" if up else "low"),
        "weak breakout candle (small body / long wick)")

    # 3. volume
    vx = sig.get("volume_x") or 1.0
    put("Volume", (vx - 1) / 1.5, f"volume {vx:.1f}x average", "volume only just above average")

    # 4. OI build-up strength
    a, w = sig.get("atm_oi_pct"), sig.get("writing_oi_pct")
    if a is None and w is None:
        put("OI build-up", 0.5)
    else:
        put("OI build-up", 0.5 * _clip((a or 0) / 5) + 0.5 * _clip((w or 0) / 5),
            "heavy fresh OI build-up and writing", "OI build-up only marginal")

    # 5-7 from the live option chain
    if chain:
        spot = float(chain[0].get("underlying_spot_price") or c)
        ce_now = sum(_md(r, "CE", "oi") for r in chain)
        pe_now = sum(_md(r, "PE", "oi") for r in chain)
        ce_prev = sum(_md(r, "CE", "prev_oi") for r in chain)
        pe_prev = sum(_md(r, "PE", "prev_oi") for r in chain)
        if ce_now > 0 and ce_prev > 0:
            pcr, pcr0 = pe_now / ce_now, pe_prev / ce_prev
            lvl = (pcr - 0.7) / 0.6 if up else (1.3 - pcr) / 0.6
            moving = (pcr > pcr0) if up else (pcr < pcr0)
            put("PCR", 0.5 * _clip(lvl) + 0.5 * moving,
                f"PCR {pcr:.2f} {'rising' if pcr > pcr0 else 'falling'} in your favour",
                f"PCR {pcr:.2f} against the trade")
        else:
            put("PCR", 0.5)

        # nearest big OI wall in the trade direction vs. the target distance
        rows = sorted(chain, key=lambda r: float(r.get("strike_price") or 0))
        ahead = [r for r in rows if (float(r["strike_price"]) > spot if up else float(r["strike_price"]) < spot)]
        ahead = ahead[:10] if up else ahead[-10:]
        wall_side = "CE" if up else "PE"
        need = 2 * float(sig.get("risk_pts") or atr)
        if ahead:
            wall = max(ahead, key=lambda r: _md(r, wall_side, "oi"))
            room = abs(float(wall["strike_price"]) - spot)
            put("Room to OI wall", room / need if need else 0.5,
                f"no big OI wall before target (next at {float(wall['strike_price']):g})",
                f"big {'call' if up else 'put'} OI wall at {float(wall['strike_price']):g} before the target")
        else:
            put("Room to OI wall", 0.5)

        # option IV (vs VIX for indices, absolute for stocks)
        iv = None
        for r in chain:
            if skey(r.get("strike_price", 0)) == skey(sig.get("strike")):
                iv = (_leg(r, side).get("option_greeks") or {}).get("iv")
        if iv:
            iv = float(iv)
            if sig.get("group") == "INDEX" and vix_now:
                fr = (1.5 - iv / vix_now) / 0.4
            else:
                fr = (70 - iv) / 35
            put("Option IV", fr, f"premium fairly priced (IV {iv:.1f})", f"premium expensive (IV {iv:.1f})")
        else:
            put("Option IV", 0.5)
    else:
        for k in ("PCR", "Room to OI wall", "Option IV"):
            put(k, 0.5)

    # 8. India VIX regime
    if vix_now:
        fr = 1.0 if 11 <= vix_now <= 20 else 0.5 if (vix_now < 11 or vix_now <= 25) else 0.0
        put("India VIX", fr, None, f"India VIX {vix_now:.1f} very high (premiums jumpy)")
        if vix_now < 11:
            minus.append(f"India VIX {vix_now:.1f} very low (small moves)")
    else:
        put("India VIX", 0.5)

    # 9. market alignment (other indices / Nifty agree with the direction)
    others = {k: v for k, v in (market or {}).items() if k != sig.get("index")}
    if sig.get("group") == "STOCK" and "NIFTY" in others:
        others = {"NIFTY": others["NIFTY"]}
    if others:
        want = 1 if up else -1
        agree = sum(1 for v in others.values() if v == want) / len(others)
        put("Market alignment", agree, "broader market moving the same way", "broader market against the trade")
    else:
        put("Market alignment", 0.5)

    total = round(sum(pts.values()))
    return {"score": total, "grade": grade(total), "score_parts": pts,
            "score_notes": {"plus": plus[:3], "minus": minus[:3]}}


def passes(rec: dict, min_score: float) -> bool:
    """Quality pre-check. A signal that could not be scored is NOT sent (never send an unchecked signal)."""
    sc = rec.get("score")
    return sc is not None and sc >= min_score
