"""Small Upstox API client.

Free on every account:
  * instrument master (listed futures / options)
  * v3 intraday + historical candles (index, listed futures/options; with volume + OI)
  * v2 option chain (live premium, OI, delta)
Upstox Plus only (needed for a 1-2 year backtest with option OI):
  * expired-instruments: expiries, expired option/future contracts, expired candles
"""
from __future__ import annotations

import gzip
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import pandas as pd
import requests

from . import config

INSTRUMENT_URL = "https://assets.upstox.com/market-quote/instruments/exchange/{ex}.json.gz"
COLS = ["ts", "open", "high", "low", "close", "volume", "oi"]


class UpstoxError(RuntimeError):
    pass


class PlusRequired(UpstoxError):
    pass


def q(key: str) -> str:
    return requests.utils.quote(key, safe="")


def candles_df(payload: dict) -> pd.DataFrame:
    rows = (payload.get("data") or {}).get("candles") or []
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame([list(r[:7]) + [0] * (7 - len(r)) for r in rows], columns=COLS)
    df["ts"] = pd.to_datetime(df["ts"]).dt.tz_convert(config.TZ)
    for k in COLS[1:]:
        df[k] = pd.to_numeric(df[k], errors="coerce").fillna(0.0)
    return df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


KEEP = ["instrument_key", "segment", "instrument_type", "underlying_symbol", "underlying_key",
        "underlying_type", "expiry", "strike_price", "lot_size", "trading_symbol"]


def skey(x) -> float:
    """Strike as a dict key (stock strikes can be like 1012.5)."""
    return round(float(x), 2)


class Upstox:
    min_gap = 0.06   # seconds between calls (Upstox allows far more; this stays polite)

    def __init__(self, token: str | None = None):
        self.token = token or config.upstox_token()
        self.s = requests.Session()
        self._master: dict[str, pd.DataFrame] = {}
        self._last = 0.0
        self._lock = threading.Lock()
        self._plus: bool | None = None

    # ------------------------------------------------------------------ http
    def _get(self, path: str, params: dict | None = None, retries: int = 4) -> dict:
        if not self.token:
            raise UpstoxError("No Upstox token found. Add your Analytics Token (or daily access token) in Settings.")
        url = config.UPSTOX_BASE + path
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.token}"}
        last = None
        for attempt in range(retries):
            with self._lock:
                wait = self.min_gap - (time.time() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.time()
            try:
                r = self.s.get(url, headers=headers, params=params, timeout=25)
            except requests.RequestException as e:
                last = str(e)
                time.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 401:
                raise UpstoxError("Upstox token invalid or expired (401). Use a 1-year Analytics Token, or add a new daily access token.")
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                last = "rate limit"
                continue
            if r.status_code >= 400:
                txt = r.text[:300]
                if "UDAPI1149" in txt or "Plus plan" in txt:
                    raise PlusRequired("This data (expired contracts) is only available with the Upstox Plus plan.")
                last = f"HTTP {r.status_code}: {txt}"
                if r.status_code >= 500:
                    time.sleep(1.5)
                    continue
                raise UpstoxError(last)
            return r.json()
        raise UpstoxError(f"Upstox request fail: {last}")

    # ------------------------------------------------------- instrument master
    def master(self, exchange: str) -> pd.DataFrame:
        if exchange in self._master:
            return self._master[exchange]
        cache = config.CACHE_DIR / f"instruments_{exchange}_{date.today():%Y%m%d}.json"
        if cache.exists():
            rows = json.loads(cache.read_text(encoding="utf-8"))
        else:
            r = self.s.get(INSTRUMENT_URL.format(ex=exchange), timeout=90)
            r.raise_for_status()
            rows = json.load(io.TextIOWrapper(gzip.GzipFile(fileobj=io.BytesIO(r.content)), encoding="utf-8"))
            rows = [{k: x.get(k) for k in KEEP} for x in rows if x.get("instrument_type") in ("FUT", "CE", "PE")]
            cache.write_text(json.dumps(rows), encoding="utf-8")
            for old in config.CACHE_DIR.glob(f"instruments_{exchange}_*.json"):
                if old != cache:
                    old.unlink(missing_ok=True)
        df = pd.DataFrame(rows)
        if not df.empty:
            df["expiry_date"] = pd.to_datetime(df["expiry"], unit="ms", utc=True).dt.tz_convert(config.TZ).dt.date
        self._master[exchange] = df
        return df

    def spec(self, idx: str) -> dict:
        if idx in config.INDICES:
            return config.INDICES[idx]
        f = self.master("NSE")
        row = f[(f["instrument_type"] == "FUT") & (f["underlying_symbol"] == idx)]
        if row.empty:
            raise UpstoxError(f"{idx}: not an F&O stock")
        return config.stock_spec(idx, row["underlying_key"].iloc[0])

    def fno_stock_futures(self, on: date) -> pd.DataFrame:
        """Near-month future of every NSE F&O stock (one row per stock)."""
        m = self.master("NSE")
        f = m[(m["instrument_type"] == "FUT") & (m["segment"] == "NSE_FO")
              & (m["underlying_type"] == "EQUITY") & (m["expiry_date"] >= on)]
        return f.sort_values("expiry_date").groupby("underlying_symbol", as_index=False).first()

    def quotes(self, keys: list[str]) -> dict:
        """Full market quotes, up to 500 instruments per call. Returns {instrument_key: quote}."""
        out = {}
        for i in range(0, len(keys), 500):
            p = self._get("/v2/market-quote/quotes", {"instrument_key": ",".join(keys[i:i + 500])})
            for v in (p.get("data") or {}).values():
                if v.get("instrument_token"):
                    out[v["instrument_token"]] = v
        return out

    def many(self, fn, items, workers: int = 6) -> list:
        """Run fn over items in parallel (rate limit still applies)."""
        with ThreadPoolExecutor(workers) as ex:
            return list(ex.map(fn, items))

    def _listed(self, idx: str, kinds) -> pd.DataFrame:
        c = self.spec(idx)
        m = self.master(c["master"])
        return m[(m["instrument_type"].isin(kinds)) & (m["underlying_symbol"] == c["fut_symbol"])
                 & (m["segment"] == c["segment"])]

    def futures(self, idx: str) -> pd.DataFrame:
        return self._listed(idx, ["FUT"]).sort_values("expiry_date").reset_index(drop=True)

    def near_future(self, idx: str, on: date) -> dict:
        f = self.futures(idx)
        f = f[f["expiry_date"] >= on]
        if f.empty:
            raise UpstoxError(f"{idx} futures contract not found")
        return f.iloc[0].to_dict()

    def listed_option_expiries(self, idx: str) -> list[date]:
        return sorted(set(self._listed(idx, ["CE", "PE"])["expiry_date"]))

    def listed_option_keys(self, idx: str, expiry: date) -> dict:
        o = self._listed(idx, ["CE", "PE"])
        o = o[o["expiry_date"] == expiry]
        return {(skey(r.strike_price), r.instrument_type): r.instrument_key for r in o.itertuples()}

    # ---------------------------------------------------------------- candles
    def intraday(self, key: str, minutes: int = 5) -> pd.DataFrame:
        return candles_df(self._get(f"/v3/historical-candle/intraday/{q(key)}/minutes/{minutes}"))

    def historical(self, key: str, start: date, end: date, minutes: int = 5) -> pd.DataFrame:
        """Listed instruments / index. Chunked by ~1 month (Upstox limit for minute candles)."""
        out, cur_end = [], end
        while cur_end >= start:
            cur_start = max(start, cur_end - timedelta(days=27))
            out.append(candles_df(self._get(
                f"/v3/historical-candle/{q(key)}/minutes/{minutes}/{cur_end:%Y-%m-%d}/{cur_start:%Y-%m-%d}")))
            cur_end = cur_start - timedelta(days=1)
        out = [d for d in out if not d.empty]
        if not out:
            return pd.DataFrame(columns=COLS)
        return pd.concat(out).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)

    # ------------------------------------------------- Upstox Plus: expired data
    def has_plus(self, idx: str = "NIFTY") -> bool:
        if self._plus is None:
            try:
                self.expired_expiries(idx)
                self._plus = True
            except PlusRequired:
                self._plus = False
        return self._plus

    def expired_expiries(self, idx: str) -> list[date]:
        c = config.INDICES[idx]
        p = self._get("/v2/expired-instruments/expiries", {"instrument_key": c["spot_key"]})
        return sorted(pd.to_datetime(x).date() for x in (p.get("data") or []))

    def expired_option_keys(self, idx: str, expiry: date) -> dict:
        c = config.INDICES[idx]
        p = self._get("/v2/expired-instruments/option/contract",
                      {"instrument_key": c["spot_key"], "expiry_date": f"{expiry:%Y-%m-%d}"})
        out = {}
        for r in p.get("data") or []:
            t = r.get("instrument_type")
            if t in ("CE", "PE"):
                out[(skey(r.get("strike_price", 0)), t)] = r.get("instrument_key")
        return out

    def expired_future_key(self, idx: str, expiry: date) -> str | None:
        c = config.INDICES[idx]
        p = self._get("/v2/expired-instruments/future/contract",
                      {"instrument_key": c["spot_key"], "expiry_date": f"{expiry:%Y-%m-%d}"})
        rows = p.get("data") or []
        return rows[0].get("instrument_key") if rows else None

    def expired_candles(self, key: str, start: date, end: date, minutes: int = 5) -> pd.DataFrame:
        out, cur_end = [], end
        while cur_end >= start:
            cur_start = max(start, cur_end - timedelta(days=27))
            out.append(candles_df(self._get(
                f"/v2/expired-instruments/historical-candle/{q(key)}/{minutes}minute/{cur_end:%Y-%m-%d}/{cur_start:%Y-%m-%d}")))
            cur_end = cur_start - timedelta(days=1)
        out = [d for d in out if not d.empty]
        if not out:
            return pd.DataFrame(columns=COLS)
        return pd.concat(out).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)

    # ----------------------------------------------------------- option chain
    def option_chain(self, idx: str, expiry: date) -> list[dict]:
        c = self.spec(idx)
        p = self._get("/v2/option/chain", {"instrument_key": c["spot_key"], "expiry_date": f"{expiry:%Y-%m-%d}"})
        return p.get("data") or []


def chain_delta(chain: list[dict], strike: int, side: str) -> float | None:
    for r in chain or []:
        if skey(r.get("strike_price", 0)) == skey(strike):
            leg = r.get("call_options" if side == "CE" else "put_options") or {}
            d = (leg.get("option_greeks") or {}).get("delta")
            return abs(float(d)) if d else None
    return None
