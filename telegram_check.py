"""Telegram check: shows exactly why a Telegram message fails (never prints the token)."""
import os

import requests

tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
print("Bot token set:", bool(tok), "| length:", len(tok), "| has ':' :", ":" in tok)
print("Chat id set:", bool(chat), "| digits only:", chat.lstrip("-").isdigit(), "| length:", len(chat))
r = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=15)
j = r.json()
print("getMe:", r.status_code, j.get("description") or ("bot @" + j.get("result", {}).get("username", "?")))
r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                  json={"chat_id": chat, "text": "Raking the Trader: Telegram test from GitHub Actions"}, timeout=15)
print("sendMessage:", r.status_code, r.json().get("description", "OK - message sent"))
