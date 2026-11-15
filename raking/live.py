"""Live scanner: today's candles -> combined-strategy signals -> fixed store + Excel + Telegram."""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from . import config, data, demo, notify, quality, store
from .config import STRATEGY as S
from .strategy import public, r05, simulate, simulate_option
from .strategy import day_trades
from .upstox import Upstox, UpstoxError, chain_delta


def now_ist() -> datetime:
    return pd.Timestamp.now(tz=config.TZ).to_pydatetime()


def market_open(t: datetime | None = None) -> bool:
    t = t or now_ist()
    if t.weekday() >= 5:
        return False
    return (9, 15) <= (t.hour, t.minute) <= (15, 30)


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
                     "Range " + ("high" if side == "CE" else "low") + " break": e["breakout"],
                     "Volume > 20-candle avg": e["volume"]}
        out[side + "_atm"] = e["oi"].get("atm")
    return out


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
                    mv = min(max(dl, 0.2), 0.9) * rec["risk_pts"]
                    rec["opt_sl"] = max(r05(rec["opt_entry"] - mv), 0.05)
                    rec["opt_tp"] = r05(rec["opt_entry"] + S["reward_risk"] * mv)
                    rec["delta"] = round(dl, 2)
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
        if not quality.passes(rec, S["min_quality"]):
            if len(n_blocked) >= S["max_blocked_per_index"]:
                continue
            rec["blocked"] = True   # quality pre-check failed: logged, result tracked, never alerted
            rec["block_reason"] = ("quality could not be checked" if rec.get("score") is None else
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
            status[idx] = "No data today (market closed / holiday?)"
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
        trades, evs = day_trades(today, days, S["max_trades_per_day"])
        checks = {k: _checklist(v) for k, v in evs.items()}
        _store_new(trades, saved, tday, "INDEX", S["max_trades_per_day"], api, demo_mode, send_alerts,
                   days, ctx)

    # ---------------- stock options (two-step, all F&O stocks)
    stock_days, cands = {}, []
    open_stocks = [s["index"] for s in store.today_signals(saved, tday)
                   if _group(s) == "STOCK" and s.get("result") == "OPEN"]
    if config.STOCKS["enabled"] and not demo_mode:
        try:
            now = now_ist()
            if (now.hour, now.minute) >= (9, 35):
                orb = data.stock_orbs(api, today)
                cands = data.stock_candidates(api, orb, config.STOCKS["max_candidates_per_scan"])
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
            st_trades, _ = day_trades(today, stock_days, config.STOCKS["max_trades_per_day"], full_check=False)
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
                s.update(res)
                if send_alerts and not s.get("blocked"):
                    notify.result_alert(s)
    store.save(saved, spath)
    store.write_excel(saved, xpath)
    return {"signals": saved, "today": store.today_signals(saved, tday), "status": status,
            "charts": charts, "checks": checks, "time": now_ist(), "candidates": cands}
