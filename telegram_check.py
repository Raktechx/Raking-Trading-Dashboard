"""Telegram check: shows exactly why a Telegram message fails (never prints the token)."""
import os
import re

import requests

raw = os.getenv("TELEGRAM_BOT_TOKEN", "")
chat = re.sub(r"[^0-9-]", "", os.getenv("TELEGRAM_CHAT_ID", ""))
m = re.search(r"\d{6,12}:[A-Za-z0-9_-]{30,}", re.sub(r"\s+", "", raw))
tok = m.group(0) if m else raw.strip()
print("Raw secret length:", len(raw), "| starts with 'bot':", raw.strip().lower().startswith("bot"),
      "| has quotes:", any(q in raw for q in "\"'"), "| has spaces inside:", " " in raw.strip())
print("Token pattern found inside:", bool(m), "| cleaned length:", len(tok))
print("Chat id digits:", len(chat))
r = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=15)
j = r.json()
print("getMe:", r.status_code, j.get("description") or ("bot @" + j.get("result", {}).get("username", "?")))
r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                  json={"chat_id": chat, "text": "Raking the Trader: Telegram test from GitHub Actions"}, timeout=15)
print("sendMessage:", r.status_code, r.json().get("description", "OK - message sent"))
