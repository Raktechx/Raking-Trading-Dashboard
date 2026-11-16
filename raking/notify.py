"""Telegram alerts (free). Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in Settings / .env."""
from __future__ import annotations

import html
import re

import requests

from . import config


def _creds() -> tuple[str, str]:
    """Clean up pasted values: drop quotes, spaces or a 'bot' prefix around the token."""
    raw = re.sub(r"\s+", "", config.env("TELEGRAM_BOT_TOKEN"))
    m = re.search(r"\d{6,12}:[A-Za-z0-9_-]{30,}", raw)
    chat = re.sub(r"[^0-9-]", "", config.env("TELEGRAM_CHAT_ID"))
    return (m.group(0) if m else raw), chat


def send(text: str) -> bool:
    tok, chat = _creds()
    if not tok or not chat:
        return False
    try:
        url = f"https://api.telegram.org/bot{tok}/sendMessage"
        r = requests.post(url, json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                     "disable_web_page_preview": True}, timeout=15)
        if not r.ok and r.status_code == 400:
            # formatting problem -> never lose the alert: send it again as plain text
            plain = html.unescape(re.sub(r"</?b>", "", text))
            r = requests.post(url, json={"chat_id": chat, "text": plain,
                                         "disable_web_page_preview": True}, timeout=15)
        if not r.ok:
            print("Telegram send failed:", r.status_code)
        return r.ok
    except requests.RequestException:
        return False


def signal_alert(s: dict) -> bool:
    emoji = "🟢" if s["side"] == "CE" else "🔴"
    label = config.label(s["index"])
    lines = [f"{emoji} <b>{config.APP_NAME}</b> — {label} BUY {s['side']}" + (" (stock option)" if s.get("group") == "STOCK" else ""),
             f"⏰ {s['day']} {s['signal_time']}" + (" · ↩️ Pullback setup" if s.get("setup") == "Pullback" else ""),
             f"📌 <b>{s['option']}</b> (expiry {s.get('expiry')})"]
    if s.get("opt_entry"):
        lines.append(f"Premium: entry ₹{s['opt_entry']} | SL ₹{s.get('opt_sl')} | target ₹{s.get('opt_tp')}")
    else:
        lines.append("Premium: not available yet - check the option price in your broker app before entering")
    if s.get("grade"):
        n = s.get("score_notes") or {}
        lines.append(f"⭐ Quality: <b>Grade {s['grade']} · {s['score']}/100</b>")
        lines += ["  ➕ " + html.escape(x) for x in n.get("plus", [])] + ["  ➖ " + html.escape(x) for x in n.get("minus", [])]
    lines += ["✅ " + "\n✅ ".join(html.escape(r) for r in s["reasons"]),
              "⚠️ Paper-trading signal. Manage your own risk."]
    return send("\n".join(lines))


def result_alert(s: dict) -> bool:
    res = s.get("result")
    icon = {"TP": "🎯", "SL": "🛑", "TIME": "⏱️"}.get(res, "ℹ️")
    what = {"TP": "TARGET HIT ✅ profit", "SL": "STOP-LOSS HIT ❌ loss",
            "TIME": f"CLOSED at {config.STRATEGY['exit_time']} (time exit)"}.get(res, res)
    pnl = s.get("opt_points")
    lot = s.get("lot_size")
    lines = [f"{icon} <b>{config.APP_NAME}</b> — trade closed",
             f"📌 <b>{s.get('option', '')}</b> (signal {s.get('signal_time')})",
             f"Result: <b>{what}</b> at {s.get('exit_time')}"
             + (" · live-price exit" if s.get("exit_by") == "live premium" else ""),
             f"Premium: entry ₹{s.get('opt_entry')} | SL ₹{s.get('opt_sl')} | target ₹{s.get('opt_tp')}",
             f"Exit premium: ₹{s.get('opt_exit')}"
             + (f" ({pnl:+.2f} per unit" + (f", ₹{pnl * lot:+,.0f} per lot of {lot})" if lot else ")")
                if isinstance(pnl, (int, float)) else ""),
             f"R = {s.get('r')}  (1R = the risk you planned; target = +{config.STRATEGY['reward_risk']:.0f}R)"]
    return send("\n".join(lines))
