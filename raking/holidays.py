"""Exchange holiday calendar from Upstox (cached once per day). Any error -> assume the market is open."""
from __future__ import annotations

import json
import os
from datetime import datetime

import pandas as pd
import requests

from . import config

URL = "https://api.upstox.com/v2/market/holidays"
CACHE = config.DATA_DIR / "cache" / "holidays.json"


def _load() -> list:
    today = str(pd.Timestamp.now(tz=config.TZ).date())
    try:
        c = json.loads(CACHE.read_text())
        if c.get("fetched") == today:
            return c["data"]
    except Exception:
        pass
    try:
        h = {"Accept": "application/json"}
        tok = os.environ.get("UPSTOX_ACCESS_TOKEN") or os.environ.get("UPSTOX_ANALYTICS_TOKEN")
        if tok:
            h["Authorization"] = "Bearer " + tok
        r = requests.get(URL, headers=h, timeout=8)
        data = (r.json().get("data") or []) if r.ok else []
        if data:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps({"fetched": today, "data": data}))
        return data
    except Exception:
        return []


def closed(exch: str, when: datetime) -> str | None:
    """Holiday name if `exch` (NSE, BSE, MCX...) is shut at `when`, else None."""
    for h in _load():
        if h.get("date") != str(when.date()) or h.get("holiday_type") != "TRADING_HOLIDAY":
            continue
        name = h.get("description") or "Exchange holiday"
        if exch in (h.get("closed_exchanges") or []):
            return name
        for o in h.get("open_exchanges") or []:
            if o.get("exchange") == exch:
                ms = when.timestamp() * 1000
                if not (o.get("start_time", 0) <= ms <= o.get("end_time", 0)):
                    return name + " (this session closed)"
    return None


def gate(exch: str, st: dict, when: datetime, send) -> bool:
    """True if closed; sends one Telegram note per day."""
    why = closed(exch, when)
    if not why:
        return False
    key = f"holiday:{exch}"
    if key not in st.setdefault("sent", []):
        send(f"📅 {exch} closed now: {why}. No signals until it reopens.")
        st["sent"].append(key)
    return True
