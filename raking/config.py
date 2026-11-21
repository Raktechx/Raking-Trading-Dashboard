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
    "FINNIFTY": {
        "label": "Fin Nifty",
        "spot_key": "NSE_INDEX|Nifty Fin Service",
        "fut_symbol": "FINNIFTY",
        "master": "NSE",
        "segment": "NSE_FO",
        "step": 50,
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
    "max_candidates_per_scan": 25,  # top-25 strongest F&O stocks checked in full each scan
    "max_break_pct": 3.0,           # more than 3% past the opening range = too late to chase
    "max_trades_per_day": 50,       # practice mode: no real daily cap - every grade A stock signal is sent
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
    # Minimum SL size, so normal noise does not stop the trade out (fixes tiny SL / target)
    "min_risk_atr": 0.6,           # SL at least 0.6 ATR away from entry
    "min_risk_pct": 0.15,          # ... and at least 0.15% of the price away
    "min_opt_sl_pct": 12.0,        # option SL at least 12% below the entry premium (target = 2x that)
    "min_premium": 3.0,            # skip options cheaper than Rs 3 (too illiquid, costs eat the move)
    "atr_period": 14,
    # 6. Limits
    "max_trades_per_day": 50,      # practice mode: no real daily cap - every grade A index signal is sent
    "max_trades_per_index": 10,    # re-entry allowed after the previous trade on that instrument has closed
    "last_entry": "15:00",         # no new entry after 3:00 PM
    "exit_time": "15:15",          # open trade squared off at this time
    "skip_expiry_day": True,       # on expiry day use the next expiry's options
    "assumed_delta": 0.5,          # for option SL/TP when the live delta is not available
    "fresh_minutes": 10,           # live: only the latest closed candle (+ scan delay) can alert - no late entries
    # 7. Quality pre-check: only grade A (score >= 70) is sent; lower grades are logged, never alerted
    "min_quality": 70,
    # 8. Second setup - PULLBACK: range already broken earlier, price dips back to VWAP / EMA20
    #    and the candle bounces in the trend direction with volume (OI checks the same).
    "pullback_enabled": True,      # evaluated + backtested always; this switch controls live signals
    "pullback_live": False,        # turned on only after the backtest shows it adds profit
    "pullback_touch_atr": 0.3,     # candle low/high must come within 0.3 ATR of VWAP or EMA20
    "max_blocked_per_index": 3,    # blocked signals logged per instrument per day (to measure the pre-check)
    # 9. Accuracy upgrades - each one is backtested on its own; switched on only if it adds profit
    "htf_trend": True,              # also need the 15-min trend (EMA60 > EMA150 on 5-min = 15-min EMA20/50)
    "breakeven_at_r": 0.0,         # move SL to entry once price is +1R in profit (0 = off)
    "skip_entry_window": None,     # e.g. ["12:00", "13:30"]: no new entries in the midday chop
    "max_losses_per_day": 0,       # stop new entries after this many losing trades in a day (0 = off)
}

UPSTOX_BASE = "https://api.upstox.com"
