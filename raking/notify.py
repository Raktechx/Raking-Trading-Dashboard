"""Telegram alerts (free). Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Settings / .env."""
from __future__ import annotations

import requests

from . import config


def send(text: str) -> bool:
    tok, chat = config.env("TELEGRAM_BOT_TOKEN"), config.env("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=15)
        return r.ok
    except requests.RequestException:
        return False


def signal_alert(s: dict) -> bool:
    emoji = "🟢" if s["side"] == "CE" else "🔴"
    label = config.INDICES[s["index"]]["label"]
    lines = [f"{emoji} <b>{config.APP_NAME}</b> — {label} BUY {s['side']}",
             f"⏰ {s['day']} {s['signal_time']}",
             f"📌 <b>{s['option']}</b> (expiry {s.get('expiry')})"]
    if s.get("opt_entry"):
        lines.append(f"Premium: entry ₹{s['opt_entry']} | SL ₹{s.get('opt_sl')} | target ₹{s.get('opt_tp')}")
    lines += [f"Future: entry {s['entry']} | SL {s['sl']} | target {s['tp']} (1:2)",
              "✅ " + "\n✅ ".join(s["reasons"]),
              "⚠️ Paper-trading signal. Manage your own risk."]
    return send("\n".join(lines))


def result_alert(s: dict) -> bool:
    icon = {"TP": "🎯", "SL": "🛑", "TIME": "⏱️"}.get(s.get("result"), "ℹ️")
    return send(f"{icon} {config.INDICES[s['index']]['label']} {s['side']} {s.get('option', '')} result: "
                f"<b>{s['result']}</b> @ {s.get('exit')} ({s.get('exit_time')}) | R = {s.get('r')}")
