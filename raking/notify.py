"""Telegram alerts (free). Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Settings / .env."""
from __future__ import annotations

import re

import requests

from . import config


def _creds() -> tuple[str, str]:
    """Clean up pasted values: drop quotes, spaces or a 'bot' prefix around the token."""
    raw = config.env("TELEGRAM_BOT_TOKEN")
    m = re.search(r"\d{6,12}:[A-Za-z0-9_-]{30,}", raw)
    chat = re.sub(r"[^0-9-]", "", config.env("TELEGRAM_CHAT_ID"))
    return (m.group(0) if m else raw), chat


def send(text: str) -> bool:
    tok, chat = _creds()
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
