"""Live scanner: today's candles -> combined-strategy signals -> fixed store + Excel + Telegram."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from . import config, data, demo, holidays, notify, quality, store
from .config import STRATEGY as S
from .strategy import opt_levels, public, r05, simulate, simulate_option
from .strategy import _t, day_trades, evaluate, orb_levels
from .upstox import Upstox, UpstoxError, chain_delta, skey


def now_ist() -> datetime:
    return pd.Timestamp.now(tz=config.TZ).to_pydatetime()


def market_open(t: datetime | None = None) -> bool:
    t = t or now_ist()
    if t.weekday() >= 5:
        return False
    return (9, 15) <= (t.hour, t.minute) <= (15, 30) and not holidays.closed("NSE", t)


def _checklist(ev: dict | None) -> dict | None:
    if not ev:
        return None
    row = ev["row"]
    out = {"time": row["bar_close"].strftime("%H:%M"), "price": float(row["close"]),
           "vwap": float(row["vwap"]), "ema20": float(row["ema_f"]), "ema50": float(row["ema_s"]),
           "vol_x": float(row["volume"] / row["vol_avg"]) if pd.notna(row["vol_avg"]) and row["vol_avg"] else None}
    for side in ("CE", "PE"):
        e = ev[side]
        out[side] = {"Trend (VWAP + EMA20/50)": e["trend"], "ATM long build-up": e["atm_buildup"],
                     ("Put" if side == "CE" else "Call") + " writing": e["writing"],
                     "Range " + ("high" if side == "CE" else "low") + " break" + (" / pullback" if S["pullback_live"] else ""):
                         e["breakout"] or (S["pullback_live"] and e.get("pullback", False)),
                     "Volume > 20-candle avg": e["volume"]}
        out[side + "_atm"] = e["oi"].get("atm")
    return out


def _price_rules_timeline(d, day, side: str) -> tuple[str | None, float | None]:
    """First time today the 3 price rules (trend + range break + volume) all held, and the latest volume x avg."""
    dd = d.fut[d.fut["day"] == day]
    orb = orb_levels(dd) if not dd.empty else None
    if orb is None:
        return None, None
    first, es = None, _t(S["entry_start"])
    for pos in dd.index:
        bc = dd.loc[pos, "bar_close"]
        if bc.time() <= es:
            continue
        e = evaluate(d, pos, orb, full=False)[side]
        if e["trend"] and e["breakout"] and e["volume"]:
            first = bc.strftime("%H:%M")
            break
    row = dd.iloc[-1]
    vx = float(row["volume"] / row["vol_avg"]) if pd.notna(row["vol_avg"]) and row["vol_avg"] else None
    return first, (round(vx, 1) if vx is not None else None)


def _group(sig: dict) -> str:
    return "INDEX" if sig.get("index") in config.INDICES else "STOCK"


def _fresh(sig: dict) -> bool:
    t = pd.Timestamp(f"{sig['day']} {sig['signal_time']}").tz_localize(config.TZ)
    return (pd.Timestamp.now(tz=config.TZ) - t) <= pd.Timedelta(minutes=S["fresh_minutes"])


def _store_new(trades: list, saved: dict, tday: str, group: str, max_total: int, api, demo_mode: bool,
               send_alerts: bool, days: dict | None = None, ctx: dict | None = None) -> None:
    ctx = ctx if ctx is not None else {}
    for t in trades:
        mine = [s for s in store.today_signals(saved, tday) if _group(s) == group]
        if t["id"] in saved or len(mine) >= max_total:
            continue
        if not demo_mode and not _fresh(t):
            continue   # never send an old signal (e.g. a stock that is only now being checked)
        if any(s["result"] == "OPEN" and s["index"] == t["index"] for s in mine):
            continue
        n_blocked = [s for s in store.today_signals(saved, tday, t["index"], include_blocked=True) if s.get("blocked")]
        if len([s for s in mine if s["index"] == t["index"]]) >= S["max_trades_per_index"]:
            continue
        rec = public(t)
        rec["group"] = group
        chain = None
        if not demo_mode:
            try:
                chain = api.option_chain(t["index"], pd.Timestamp(rec["expiry"]).date())
            except Exception:
                chain = None
        if chain and rec.get("opt_entry"):
            try:   # use the live delta for the option SL / target
                dl = chain_delta(chain, rec["strike"], rec["side"])
                if dl:
                    rec["opt_sl"], rec["opt_tp"] = opt_levels(rec["opt_entry"], min(max(dl, 0.2), 0.9),
                                                              rec["risk_pts"])
                    rec["delta"] = round(dl, 2)
            except Exception:
                pass
        if not demo_mode:
            try:   # option instrument key, so exits can be checked every minute on the live premium
                rec["opt_key"] = api.listed_option_keys(
                    t["index"], pd.Timestamp(rec["expiry"]).date()).get((skey(rec["strike"]), rec["side"]))
            except Exception:
                pass
        try:   # quality score (pre-check below)
            if "vix" not in ctx:
                ctx["vix"] = None if demo_mode else quality.vix(api)
            d = (days or {}).get(t["index"])
            if d is not None:
                rec.update(quality.score({**t, "group": group}, d, chain, ctx["vix"], ctx.get("market")))
                rec.pop("_pos", None)
        except Exception:
            pass
        rec["created_at"] = now_ist().strftime("%Y-%m-%d %H:%M:%S")
        if group == "STOCK":   # late-move check: the stock already made its move before the breakout
            try:
                d = (days or {}).get(t["index"])
                dd = d.fut[d.fut["day"] == pd.Timestamp(tday).date()]
                rng = (float(rec["orb_high"]) - float(rec["orb_low"])) / float(rec["orb_low"]) * 100
                mv = abs(float(rec["entry"]) / float(dd["open"].iloc[0]) - 1) * 100
                rec["range_pct"], rec["day_move_pct"] = round(rng, 2), round(mv, 2)
                rec["late_move"] = bool(rng > config.STOCKS.get("max_range_pct", 2.5)
                                        or mv > config.STOCKS.get("max_day_move_pct", 3.0))
            except Exception:
                pass
        why = None
        if group == "STOCK":
            nb = (ctx.get("market") or {}).get("NIFTY")
            if config.STOCKS.get("need_nifty_trend", True) and nb != (1 if rec["side"] == "CE" else -1):
                why = "against Nifty trend (" + {1: "Nifty up", -1: "Nifty down"}.get(nb, "Nifty mixed") + ")"
            elif rec["signal_time"] > config.STOCKS.get("last_entry", "13:30"):
                why = f"after {config.STOCKS.get('last_entry', '13:30')} (afternoon stock entries)"
        if not why:   # chasing check: a wide SL means the entry came after the move already happened
            try:
                rp = float(rec["risk_pts"]) / float(rec["entry"]) * 100
                if rp > config.STOCKS.get("max_risk_pct", 0.6):
                    why = f"chasing: SL {rp:.2f}% away (max {config.STOCKS.get('max_risk_pct', 0.6)}%)"
            except (TypeError, ValueError, KeyError, ZeroDivisionError):
                pass
        late = bool(rec.get("late_move"))
        if why or late or not quality.passes(rec, S["min_quality"]):
            if len(n_blocked) >= S["max_blocked_per_index"]:
                continue
            rec["blocked"] = True   # quality pre-check failed: logged, result tracked, never alerted
            rec["block_reason"] = (why if why else f"late move: opening range {rec.get('range_pct')}%, day move "
                                   f"{rec.get('day_move_pct')}% (measuring)" if late else
                                   "quality could not be checked" if rec.get("score") is None else
                                   f"grade {rec.get('grade')} ({rec.get('score')}/100): "
                                   + ("; ".join((rec.get("score_notes") or {}).get("minus") or []) or "overall quality too low"))
        for k in ("result", "exit", "exit_time", "r", "points", "opt_exit", "opt_points", "alt_result", "alt_r"):
            rec[k] = None   # filled by the result check, never from look-ahead
        rec["result"] = "OPEN"
        if store.add_if_new(saved, rec) and send_alerts and not rec.get("blocked"):
            notify.signal_alert(rec)


def _load(api, idx, today, demo_mode, status, days):
    try:
        d = demo.live_day(idx, today) if demo_mode else data.live_day(api, idx, today)
        if d is None:
            status[idx] = ("OK (waiting for first candle)" if now_ist().strftime("%H:%M") < "09:35" else "No candles after 9:35 - Upstox data delayed (or exchange holiday)")
            return
        days[idx] = d
        status[idx] = "OK"
    except UpstoxError as e:
        status[idx] = str(e)
    except Exception as e:  # keep the others running
        status[idx] = f"Error: {e}"


def scan(api: Upstox | None = None, send_alerts: bool = True, demo_mode: bool = False) -> dict:
    api = api or (None if demo_mode else Upstox())
    spath, xpath = (store.DEMO_STORE, store.DEMO_XLSX) if demo_mode else (None, None)
    if demo_mode:
        send_alerts = False
    saved = store.load(spath)
    today = now_ist().date()
    tday = str(today)
    status, days = {}, {}

    # ---------------- index options
    for idx in config.INDICES:
        _load(api, idx, today, demo_mode, status, days)
    charts = {k: d.fut[d.fut["day"] == today].copy() for k, d in days.items()}
    checks = {}
    ctx = {"market": {k: quality.bias(d) for k, d in days.items()}}
    if days:
        trades, evs = day_trades(today, days, S["max_trades_per_day"], pullback=S["pullback_live"])
        checks = {k: _checklist(v) for k, v in evs.items()}
        _store_new(trades, saved, tday, "INDEX", S["max_trades_per_day"], api, demo_mode, send_alerts,
                   days, ctx)

    # ---------------- stock options (two-step, all F&O stocks)
    stock_days, cands, top = {}, [], []
    open_stocks = [s["index"] for s in store.today_signals(saved, tday)
                   if _group(s) == "STOCK" and s.get("result") == "OPEN"]
    if config.STOCKS["enabled"] and not demo_mode:
        try:
            now = now_ist()
            if (now.hour, now.minute) >= (9, 35):
                orb = data.stock_orbs(api, today)
                top = data.stock_candidates(api, orb, config.STOCKS["max_candidates_per_scan"])
                cands = [r["symbol"] for r in top]
            for sym in dict.fromkeys(cands + open_stocks):
                _load(api, sym, today, demo_mode, status, stock_days)
            status["STOCKS"] = f"OK ({len(cands)} candidates: {', '.join(cands) or '-'})"
        except UpstoxError as e:
            status["STOCKS"] = str(e)
        except Exception as e:
            status["STOCKS"] = f"Error: {e}"
        for sym in list(status):   # a failing single stock is not an error worth an alert
            if sym not in config.INDICES and sym != "STOCKS":
                status.pop(sym)
        if stock_days:
            st_trades, st_evs = day_trades(today, stock_days, config.STOCKS["max_trades_per_day"], full_check=False,
                                           pullback=S["pullback_live"])
            for r in top:   # how many of the price rules each listed stock meets right now
                e = (st_evs.get(r["symbol"]) or {}).get(r["side"])
                if e:
                    r["rules"] = int(bool(e["trend"])) + int(bool(e["breakout"])) + int(bool(e["volume"]))
                if r["symbol"] in stock_days:
                    try:
                        r["rules3_at"], r["vol_x"] = _price_rules_timeline(stock_days[r["symbol"]], today, r["side"])
                    except Exception:
                        pass
            _store_new(st_trades, saved, tday, "STOCK", config.STOCKS["max_trades_per_day"], api, demo_mode,
                       send_alerts, stock_days, ctx)

    # ---------------- results of today's open signals, using the FIXED stored numbers
    every = {**days, **stock_days}
    for s in store.today_signals(saved, tday, include_blocked=True):
        if s.get("alt_result") in (None, "OPEN") and s["index"] in every:
            try:
                s.update(simulate_option(s, every[s["index"]]))
            except Exception:   # the shadow test must never stop the real scan
                pass
        if s.get("result") == "OPEN" and s["index"] in every:
            res = simulate(s, every[s["index"]])
            if res["result"] != "OPEN":
                res.pop("_exit_ts", None)
                res["exit_by"] = "5-min candle"
                try:   # judge the result on the option premium we would really hold, not the underlying
                    risk = float(s["opt_entry"]) - float(s["opt_sl"])
                    if res.get("opt_exit") is not None and risk > 0:
                        pr = round((float(res["opt_exit"]) - float(s["opt_entry"])) / risk, 2)
                        res["r"] = max(min(pr, float(S["reward_risk"])), -1.0) if res["result"] != "TIME" else pr
                        if res["result"] == "TP" and pr <= 0:
                            res["result"] = "SL" if pr <= -1 else "TIME"
                except (TypeError, ValueError, KeyError):
                    pass
                s.update(res)
                if send_alerts and not s.get("blocked"):
                    notify.result_alert(s)
    store.save(saved, spath)
    store.write_excel(saved, xpath)
    return {"signals": saved, "today": store.today_signals(saved, tday), "status": status,
            "charts": charts, "checks": checks, "time": now_ist(), "candidates": cands, "top_stocks": top}


def check_exit(s: dict, ltp: float | None, hhmm: str) -> dict | None:
    """Live-premium exit: premium at/above the option target -> TP, at/below the option SL -> SL."""
    if ltp is None or s.get("result") != "OPEN" or not s.get("opt_entry"):
        return None
    if s.get("opt_tp") and ltp >= s["opt_tp"]:
        res, r = "TP", float(S["reward_risk"])
    elif s.get("opt_sl") and ltp <= s["opt_sl"]:
        res, r = "SL", -1.0
    else:
        return None
    return {"result": res, "exit": None, "exit_time": hhmm, "r": r,
            "points": round(r * s["risk_pts"], 2) if s.get("risk_pts") else None,
            "opt_exit": r05(ltp), "opt_points": round(ltp - s["opt_entry"], 2), "exit_by": "live premium"}


def fast_exits(api: Upstox | None = None, send_alerts: bool = True) -> list[dict]:
    """Every-minute check between the 5-minute scans: has an open trade hit its option target / SL?"""
    now = now_ist()
    if not market_open(now) or (now.hour, now.minute) >= tuple(map(int, S["exit_time"].split(":"))):
        return []   # the 15:15 time exit stays with the 5-minute scan
    saved = store.load()
    tday = str(now.date())
    open_ = [s for s in store.today_signals(saved, tday, include_blocked=True)
             if s.get("result") == "OPEN" and s.get("opt_entry") and (s.get("opt_sl") or s.get("opt_tp"))]
    if not open_:
        return []
    api = api or Upstox()
    for s in open_:
        if not s.get("opt_key"):
            try:
                s["opt_key"] = api.listed_option_keys(
                    s["index"], pd.Timestamp(s["expiry"]).date()).get((skey(s["strike"]), s["side"]))
            except Exception:
                pass
    keys = [s["opt_key"] for s in open_ if s.get("opt_key")]
    q = api.quotes(keys) if keys else {}
    hhmm = now.strftime("%H:%M")
    closed = []
    for s in open_:
        ltp = (q.get(s.get("opt_key")) or {}).get("last_price")
        if ltp:                      # live premium for the dashboard's running-trades table
            s["ltp"], s["ltp_time"] = float(ltp), hhmm
        res = check_exit(s, float(ltp) if ltp else None, hhmm)
        if res:
            s.update(res)
            closed.append(s)
            if send_alerts and not s.get("blocked"):
                notify.result_alert(s)
    store.save(saved)
    if closed:
        store.write_excel(saved)
    return closed
