"""One scheduled scan (GitHub Actions runs this every 5 minutes on weekdays).

 * 09:15-15:30 IST : scan Nifty / Bank Nifty, send Telegram alerts for new signals and results
 * first scan of the day : "scanner running" message (or an error message if the token / data fails)
 * after 15:30 IST : one end-of-day summary
 * every run : rebuilds the live page (docs/index.html and LIVE.md)

Run by hand:  python scan_once.py        (python scan_once.py demo -> demo data, no alerts)
"""
import base64
import json
import sys

import pandas as pd

from raking import config, live, notify, site, store

STATE = config.DATA_DIR / "state.json"


def load_state(today: str) -> dict:
    try:
        st = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st = {}
    if st.get("day") != today:
        st = {"day": today, "start_sent": False, "eod_sent": False, "errors_sent": []}
    return st


def eod_text(saved: dict, today: str) -> str:
    sigs = store.today_signals(saved, today)
    lines = [f"📊 <b>{config.APP_NAME}</b> — end of day {today}"]
    if not sigs:
        lines.append("No signal today (conditions never all lined up).")
    for s in sigs:
        lines.append(f"• {s['signal_time']} BUY {s['option']} → <b>{s.get('result')}</b> "
                     f"(R {s.get('r')}, premium {s.get('opt_entry')} → {s.get('opt_exit')})")
    done = [s for s in saved.values() if s.get("result") in ("TP", "SL", "TIME")]
    if done:
        r = pd.Series([float(s["r"]) for s in done])
        lines.append(f"Paper record: {len(done)} trades · win rate {(r > 0).mean() * 100:.0f}% · total {r.sum():.2f} R")
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
    start = str((pd.Timestamp(today) - pd.Timedelta(days=6)).date())
    week = [s for s in saved.values() if start <= s["day"] <= today]
    done = [s for s in week if s.get("result") in ("TP", "SL", "TIME")]
    lines = [f"🗓️ <b>{config.APP_NAME}</b> — weekly report ({start} to {today})",
             f"Signals: {len(week)} · closed: {len(done)}"]
    if done:
        r = pd.Series([float(s["r"]) for s in done])
        lines.append(f"Win rate {(r > 0).mean() * 100:.0f}% · total {r.sum():.2f} R · best {r.max():.2f} R · worst {r.min():.2f} R")
    return "\n".join(lines)


def main() -> None:
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
            if msg != "OK" and msg not in st["errors_sent"] and not is_demo:
                notify.send(f"⚠️ <b>{config.APP_NAME}</b> — {config.INDICES[idx]['label']}: {msg}")
                st["errors_sent"].append(msg)
        if not st["start_sent"] and not is_demo and all(v == "OK" for v in out["status"].values()):
            notify.send(f"✅ <b>{config.APP_NAME}</b> scanner running for {today}. "
                        f"Watching Nifty 50 and Bank Nifty every 5 minutes.")
            st["start_sent"] = True
        for s in out["today"]:
            print(f"  SIGNAL {s['option']} @ {s['signal_time']}  future entry {s['entry']} SL {s['sl']} "
                  f"target {s['tp']}  premium {s.get('opt_entry')}  result {s.get('result')}")
    else:
        print("Market closed - scan skipped.")

    saved = store.load(store.DEMO_STORE if is_demo else None)
    if weekday and not is_demo and (now.hour, now.minute) > (15, 30) and not st["eod_sent"]:
        notify.send(eod_text(saved, today))
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
        STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
