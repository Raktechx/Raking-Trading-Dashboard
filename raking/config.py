"""Raking the Trader - central settings.

All settings live here. To change any strategy number, edit only this file.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
ENV_FILE = ROOT / ".env"

load_dotenv(ENV_FILE, override=True)

APP_NAME = "Raking the Trader"
TZ = "Asia/Kolkata"


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def reload_env() -> None:
    load_dotenv(ENV_FILE, override=True)


def upstox_token() -> str:
    """1-year Analytics Token (preferred - no daily login) or the daily access token."""
    return env("UPSTOX_ANALYTICS_TOKEN") or env("UPSTOX_ACCESS_TOKEN")


# ---------------------------------------------------------------------------
# Index options: Nifty 50, Bank Nifty, Sensex
#   spot_key   -> Upstox index key (spot price, ATM strike, option chain)
#   fut_symbol -> underlying_symbol in the Upstox instrument master. The index
#                 itself has no volume, so VWAP and the volume check use the
#                 near-month FUTURE (price, volume).
# ---------------------------------------------------------------------------
INDICES = {
    "NIFTY": {
        "label": "Nifty 50",
        "spot_key": "NSE_INDEX|Nifty 50",
        "fut_symbol": "NIFTY",
        "master": "NSE",
        "segment": "NSE_FO",
        "step": 50,
    },
    "BANKNIFTY": {
        "label": "Bank Nifty",
        "spot_key": "NSE_INDEX|Nifty Bank",
        "fut_symbol": "BANKNIFTY",
        "master": "NSE",
        "segment": "NSE_FO",
        "step": 100,
    },
    "SENSEX": {
        "label": "Sensex",
        "spot_key": "BSE_INDEX|SENSEX",
        "fut_symbol": "SENSEX",
        "master": "BSE",
        "segment": "BSE_FO",
        "step": 100,
    },
}

# ---------------------------------------------------------------------------
# Stock options: every NSE F&O stock is checked automatically (no list to keep).
# Step 1 - one batch quote for all stock futures finds stocks that are past their
#          9:15-9:30 range on the right side of VWAP.
# Step 2 - only those candidates get the full strategy (EMA, volume, option OI).
# ---------------------------------------------------------------------------
STOCKS = {
    "enabled": True,
    "max_candidates_per_scan": 8,   # strongest movers checked in full each scan
    "max_trades_per_day": 2,        # stock trades a day (separate from the index limit)
}


def label(name: str) -> str:
    """Display name for an index or a stock symbol."""
    return INDICES[name]["label"] if name in INDICES else name


def stock_spec(symbol: str, underlying_key: str) -> dict:
    return {"label": symbol, "spot_key": underlying_key, "fut_symbol": symbol,
            "master": "NSE", "segment": "NSE_FO", "step": None}

# ---------------------------------------------------------------------------
# Combined strategy - exactly as in the user's document
# ---------------------------------------------------------------------------
STRATEGY = {
    "candle_minutes": 5,
    # 1. Trend
    "ema_fast": 20,
    "ema_slow": 50,
    # 2. OI confirmation (ATM option long build-up + writing on the other side)
    "oi_lookback_candles": 3,      # "rising" = compared with 3 candles (15 min) earlier
    "atm_oi_min_pct": 1.0,         # ATM option OI must rise at least this %
    "writing_strikes": 3,          # ATM + 2 strikes (below for puts / above for calls)
    "writing_oi_min_pct": 1.0,     # total OI of those strikes must rise at least this %
    "writing_needs_price_fall": True,  # writing = OI up AND premium down
    # 3. Entry
    "orb_minutes": 15,             # opening range 9:15 - 9:30
    "entry_start": "09:30",
    "volume_avg_candles": 20,      # breakout candle volume > average of previous 20 candles
    "breakout_window": 3,          # the range must have broken within the last 3 candles (no late chasing)
    # 4/5. Stop loss = previous candle low/high, target = R:R x risk
    "reward_risk": 2.0,
    "max_risk_atr": 2.0,           # skip if previous candle SL is too far (> 2 ATR)
    "atr_period": 14,
    # 6. Limits
    "max_trades_per_day": 2,       # index trades a day (Nifty + Bank Nifty + Sensex together)
    "max_trades_per_index": 2,
    "last_entry": "15:00",         # no new entry after 3:00 PM
    "exit_time": "15:15",          # open trade squared off at this time
    "skip_expiry_day": True,       # on expiry day use the next expiry's options
    "assumed_delta": 0.5,          # for option SL/TP when the live delta is not available
    "fresh_minutes": 20,           # live: a signal older than this is never sent (no stale alerts)
}

UPSTOX_BASE = "https://api.upstox.com"
