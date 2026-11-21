"""One scheduled scan (GitHub Actions runs this every 5 minutes on weekdays).

 * 09:15-15:30 IST : scan Nifty / Bank Nifty / Sensex / F&O stocks, send Telegram alerts for new signals and results
 * first scan of the day : "scanner running" message (or an error message if the token / data fails)
 * after 15:30 IST : one end-of-day summary
 * every run : rebuilds the live page (docs/index.html and LIVE.md)

Run by hand:  python scan_once.py        (python scan_once.py demo -> demo data, no alerts)
"""
import base64
import json
import sys

import pandas as pd

from raking import analysis, config, live, notify, site, store

STATE = config.DATA_DIR / "state.json"


def load_state(today: str) -> dict:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st = {}
    if st.get("day") != today:
        st = {"day": today, "start_sent": False, "eod_sent": False, "errors_sent": []}
    return st


def lot_pnl(s: dict) -> float | None:
    """1-lot P&L after brokerage, taxes and slippage."""
    return analysis.net_pnl(s)


def eod_text(saved: dict, today: str, everything: dict | None = None) -> str:
    """Market-close report: today's trades, result of each, today's R and the running record."""
    icon = {"TP": "🎯", "SL": "🛑", "TIME": "⏱️", "OPEN": "🟡"}
    sigs = store.today_signals(saved, today)
    day = pd.Timestamp(today).strftime("%a %d %b %Y")
    lines = [f"📊 <b>MARKET CLOSE REPORT</b>", day, ""]
    if not sigs:
        lines.append("No signal sent today - the rules never all lined up with grade A.")
    else:
        tp = sum(s.get("result") == "TP" for s in sigs)
        sl = sum(s.get("result") == "SL" for s in sigs)
        lines.append(f"Signals: <b>{len(sigs)}</b> · 🎯 {tp} · 🛑 {sl}")
        for s in sigs:
            r = s.get("r")
            lines.append(f"{icon.get(s.get('result'), '•')} {s['signal_time']} <b>{s['option']}</b> "
                         f"{s.get('grade') or '-'} ₹{s.get('opt_entry')}→₹{s.get('opt_exit')}"
                         + (f" {float(r):+.1f}R" if r is not None else "")
                         + (f" ₹{lot_pnl(s):+,.0f}" if lot_pnl(s) is not None else ""))
        rs = [float(s["r"]) for s in sigs if s.get("r") is not None]
        ps = [lot_pnl(s) for s in sigs if lot_pnl(s) is not None]
        if rs:
            lines.append(f"Today: <b>{sum(rs):+.1f}R · ₹{sum(ps):+,.0f}</b> (1 lot, after costs)")
    blk = [s for s in (everything or {}).values() if s.get("blocked") and s.get("day") == today]
    if blk:
        lines.append("")
        lines.append(f"🛡️ Blocked (not sent): {len(blk)}")
        for s in blk:
            r = s.get("r")
            lines.append(f"  {s['signal_time']} {s['option']} grade {s.get('grade') or '-'} → "
                         f"{s.get('result')}" + (f" {float(r):+.1f}R" if r is not None else ""))
    done = [s for s in saved.values() if s.get("result") in ("TP", "SL", "TIME")]
    if done:
        r = pd.Series([float(s["r"]) for s in done])
        p = sum(lot_pnl(s) or 0 for s in done)
        lines += ["", f"📈 All-time: {len(done)} trades · win {(r > 0).mean() * 100:.0f}% · "
                      f"<b>{r.sum():+.1f}R · ₹{p:+,.0f}</b> (1 lot, after costs)"]
    return "\n".join(lines)


def token_days_left() -> int | None:
    """Days until the Upstox token expires (read from the token itself, nothing is sent anywhere)."""
    try:
        part = config.upstox_token().split(".")[1]
        exp = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("exp")
        return int((exp - pd.Timestamp.now(tz="UTC").timestamp()) // 86400) if exp else None
    except (IndexError, ValueError, TypeError):
        return None


def week_text(saved: dict, today: str) -> str:
    """Weekly report: results and P&L for the last 7 days, day by day."""
    start = str((pd.Timestamp(today) - pd.Timedelta(days=6)).date())
    week = [s for s in saved.values() if start <= s["day"] <= today]
    done = [s for s in week if s.get("result") in ("TP", "SL", "TIME")]
    lines = [f"🗓️ <b>WEEKLY REPORT</b>", f"{pd.Timestamp(start):%d %b} – {pd.Timestamp(today):%d %b %Y}", ""]
    if not done:
        lines.append("No closed trades this week.")
        return "\n".join(lines)
    r = pd.Series([float(s["r"]) for s in done])
    pnl = sum(lot_pnl(s) or 0 for s in done)
    lines.append(f"Trades: <b>{len(done)}</b> · 🎯 {int((r > 0).sum())} · 🛑 {int((r <= 0).sum())} · "
                 f"win {(r > 0).mean() * 100:.0f}%")
    lines.append(f"Week: <b>{r.sum():+.1f}R · ₹{pnl:+,.0f}</b> (1 lot, after costs)")
    lines.append("")
    for day in sorted({s["day"] for s in done}):
        ds = [s for s in done if s["day"] == day]
        lines.append(f"{pd.Timestamp(day):%a %d}: {len(ds)} trades · {sum(float(s['r']) for s in ds):+.1f}R · "
                     f"₹{sum(lot_pnl(s) or 0 for s in ds):+,.0f}")
    idx = [s for s in done if s["index"] in config.INDICES]
    stk = [s for s in done if s["index"] not in config.INDICES]
    lines.append("")
    for name, grp in (("Index", idx), ("Stocks", stk)):
        if grp:
            lines.append(f"{name}: {len(grp)} · {sum(float(s['r']) for s in grp):+.1f}R · "
                         f"₹{sum(lot_pnl(s) or 0 for s in grp):+,.0f}")
    return "\n".join(lines)


def save_live_state(out, status: str) -> None:
    """Today's candles, indicators and condition checklist for the online dashboard."""
    path = config.DATA_DIR / "live_state.json"
    if out is None:
        try:
            st = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
        st["market"] = status
        st["checked_at"] = pd.Timestamp.now(tz=config.TZ).strftime("%Y-%m-%d %H:%M:%S")
        path.write_text(json.dumps(st), encoding="utf-8")
        return
    charts = {}
    for k, d in out["charts"].items():
        c = d[["bar_close", "open", "high", "low", "close", "vwap", "ema_f", "ema_s"]].copy()
        c["bar_close"] = c["bar_close"].dt.strftime("%Y-%m-%d %H:%M")
        charts[k] = c.round(2).to_dict("list")
    st = {"updated": out["time"].strftime("%Y-%m-%d %H:%M:%S"), "market": status,
          "status": out["status"], "candidates": out.get("candidates", []), "top_stocks": out.get("top_stocks", []),
          "checks": out["checks"], "charts": charts,
          "checked_at": pd.Timestamp.now(tz=config.TZ).strftime("%Y-%m-%d %H:%M:%S")}
    path.write_text(json.dumps(st, default=str), encoding="utf-8")


def exits_only() -> None:
    closed = live.fast_exits()
    for s in closed:
        print(f"  EXIT {s['option']}: {s['result']} at {s['exit_time']} premium {s.get('opt_exit')}")
    print(f"Exit check {pd.Timestamp.now(tz=config.TZ):%H:%M:%S}: {len(closed)} closed")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "exits":
        return exits_only()
    is_demo = len(sys.argv) > 1 and sys.argv[1] == "demo"
    now = pd.Timestamp.now(tz=config.TZ)
    today = str(now.date())
    st = load_state(today)
    out = None
    weekday = now.weekday() < 5

    if is_demo or live.market_open():
        out = live.scan(send_alerts=not is_demo, demo_mode=is_demo)
        print("Scan time:", out["time"].strftime("%Y-%m-%d %H:%M:%S"))
        for idx, msg in out["status"].items():
            print(f"  {idx}: {msg}")
            if not msg.startswith("OK") and msg not in st["errors_sent"] and not is_demo:
                notify.send(f"⚠️ <b>{config.APP_NAME}</b> — {config.label(idx)}: {msg}")
                st["errors_sent"].append(msg)
        if not st["start_sent"] and not is_demo and all(v.startswith("OK") for v in out["status"].values()):
            notify.send(f"✅ <b>{config.APP_NAME}</b> scanner running for {today}. "
                        f"Watching Nifty 50, Bank Nifty, Sensex and all F&O stock options every 5 minutes.")
            st["start_sent"] = True
        for s in out["today"]:
            print(f"  SIGNAL {s['option']} @ {s['signal_time']}  future entry {s['entry']} SL {s['sl']} "
                  f"target {s['tp']}  premium {s.get('opt_entry')}  result {s.get('result')}")
    else:
        print("Market closed - scan skipped.")

    everything = store.load(store.DEMO_STORE if is_demo else None)
    saved = store.active(everything)
    if weekday and not is_demo and (now.hour, now.minute) > (15, 30) and not st["eod_sent"]:
        notify.send(eod_text(saved, today, everything))
        if now.weekday() == 4:   # Friday: weekly report
            notify.send(week_text(saved, today))
        days = token_days_left()
        if days is not None and days <= 15:
            notify.send(f"🔑 <b>{config.APP_NAME}</b>: your Upstox token expires in {days} day(s). "
                        "Generate a new Analytics Token on the Upstox Developer Apps page and update the "
                        "UPSTOX_ANALYTICS_TOKEN secret on GitHub.")
        st["eod_sent"] = True

    status = "🟢 Market open" if live.market_open() else "⚪ Market closed"
    if not is_demo:
        site.build(out, saved, status)
        save_live_state(out, status)
        STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
