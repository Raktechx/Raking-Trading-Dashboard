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


def _hdr(s: dict) -> str:
    kind = "Stock option" if s.get("group") == "STOCK" or s.get("index") not in config.INDICES else "Index option"
    return f"{kind} · exp {s.get('expiry')}"


def _pnl(s: dict) -> str:
    pts, lot = s.get("opt_points"), s.get("lot_size")
    if not isinstance(pts, (int, float)):
        return ""
    sign = "+" if pts >= 0 else "-"
    return f"{pts:+.2f}/unit" + (f" ({sign}₹{abs(pts * lot):,.0f} per lot)" if lot else "")


def signal_alert(s: dict) -> bool:
    up = s["side"] == "CE"
    lines = [f"{'🟢' if up else '🔴'} <b>NEW SIGNAL · BUY {s['option']}</b>",
             _hdr(s)]
    if s.get("grade"):
        lines.append(f"⭐ Grade <b>{s['grade']}</b> ({s.get('score')}/100)")
    if s.get("opt_entry"):
        lines += ["",
                  f"▶️ Entry  : <b>₹{s['opt_entry']}</b>",
                  f"🛑 SL     : <b>₹{s.get('opt_sl')}</b>",
                  f"🎯 Target : <b>₹{s.get('opt_tp')}</b>"]
    else:
        lines.append("Premium not available - check the option price in your broker app first.")
    lines += ["", f"⏰ {s['signal_time']} · exit by {config.STRATEGY['exit_time']}"]
    return send("\n".join(lines))


def result_alert(s: dict) -> bool:
    res = s.get("result")
    head = {"TP": "🎯 <b>TARGET HIT ✅</b>", "SL": "🛑 <b>STOP-LOSS HIT ❌</b>",
            "TIME": f"⏱️ <b>CLOSED AT {config.STRATEGY['exit_time']}</b>"}.get(res, f"ℹ️ <b>{res}</b>")
    if res == "SL" and s.get("breakeven"):
        head = "🟰 <b>STOP AT COST - no loss</b>"
    r = s.get("r")
    lines = [head,
             f"<b>{s.get('option', '')}</b>",
             f"Entry ₹{s.get('opt_entry')} → Exit <b>₹{s.get('opt_exit')}</b>",
             f"P&amp;L: <b>{_pnl(s) or '-'}</b>" + (f" · {float(r):+.1f}R" if r is not None else ""),
             f"⏰ {s.get('signal_time')} → {s.get('exit_time')}"]
    return send("\n".join(lines))
