"""Builds the per-day inputs (spot, future, option candles) for live scans and backtests."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Callable

import pandas as pd

from . import config
from .config import STRATEGY as S
from .strategy import IndexDay, prepare_fut
from .upstox import COLS, PlusRequired, Upstox, UpstoxError

Progress = Callable[[str], None] | None


def _say(p: Progress, msg: str) -> None:
    if p:
        p(msg)


def cached(name: str, start: date, end: date, fetch) -> pd.DataFrame:
    """Disk cache for finished date ranges (ranges touching today are always refetched)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
    path = config.CACHE_DIR / f"{safe}_{start:%Y%m%d}_{end:%Y%m%d}.csv"
    if path.exists():
        df = pd.read_csv(path)
        if df.empty:
            return pd.DataFrame(columns=COLS)
        df["ts"] = pd.to_datetime(df["ts"], utc=True).dt.tz_convert(config.TZ)
        return df
    df = fetch()
    if end < date.today():
        df.to_csv(path, index=False)
    return df


def strikes_for(spot_day: pd.DataFrame, step: int, pad: int | None = None) -> list[int]:
    pad = S["writing_strikes"] if pad is None else pad
    lo = int(spot_day["low"].min() // step * step) - pad * step
    hi = int(-(-spot_day["high"].max() // step) * step) + pad * step
    return list(range(lo, hi + step, step))


def option_expiry_for(day: date, expiries: list[date]) -> date | None:
    for e in expiries:
        if e > day or (e == day and not S["skip_expiry_day"]):
            return e
    return None


def _opts_slice(opts_full: dict, day: date) -> dict:
    out = {}
    for k, f in opts_full.items():
        s = f[f["ts"].dt.date == day]
        if not s.empty:
            out[k] = s.reset_index(drop=True)
    return out


# ======================================================================= LIVE
class LazyOpts(dict):
    """Option candles fetched only when the strategy actually needs a strike
    (most scans never reach the OI check, so this keeps each run short)."""

    def __init__(self, api: Upstox, keys: dict, cutoff, mins: int):
        super().__init__()
        self.api, self.keys, self.cutoff, self.mins = api, keys, cutoff, mins

    def get(self, k, default=None):
        if k not in self and k in self.keys:
            try:
                o = self.api.intraday(self.keys[k], self.mins)
                o = o[o["ts"] <= self.cutoff].reset_index(drop=True)
            except UpstoxError:
                o = pd.DataFrame(columns=COLS)
            self[k] = o
        return super().get(k, default)


def live_day(api: Upstox, idx: str, today: date) -> IndexDay | None:
    c = api.spec(idx)
    mins = S["candle_minutes"]
    cutoff = pd.Timestamp.now(tz=config.TZ) - pd.Timedelta(minutes=mins)   # closed candles only
    spot = api.intraday(c["spot_key"], mins)
    spot = spot[spot["ts"] <= cutoff]
    if spot.empty:
        return None
    fut = api.near_future(idx, today)
    hist = api.historical(fut["instrument_key"], today - timedelta(days=8), today - timedelta(days=1), mins)
    intra = api.intraday(fut["instrument_key"], mins)
    f = pd.concat([x for x in (hist, intra) if not x.empty])
    f = f.sort_values("ts").drop_duplicates("ts")
    f = f[f["ts"] <= cutoff].reset_index(drop=True)
    exp = option_expiry_for(today, api.listed_option_expiries(idx))
    keys = api.listed_option_keys(idx, exp) if exp else {}
    strikes = sorted({k for k, _ in keys})
    return IndexDay(idx, c["step"] or 1, str(exp), prepare_fut(f), spot.reset_index(drop=True),
                    LazyOpts(api, keys, cutoff, mins), fut.get("trading_symbol", ""), fut.get("lot_size"),
                    strikes=strikes or None)


# ---------------------------------------------------------------- stocks
def stock_orbs(api: Upstox, today: date) -> dict:
    """9:15-9:30 range of every F&O stock future, worked out once a day and saved."""
    path = config.DATA_DIR / "stock_orb.json"
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved.get("day") == str(today) and saved.get("orb"):
            return saved["orb"]
    except (OSError, ValueError):
        pass
    futs = api.fno_stock_futures(today)
    end = pd.Timestamp(f"{today} 09:30", tz=config.TZ)

    def one(row):
        try:
            c = api.intraday(row["instrument_key"], S["candle_minutes"])
        except UpstoxError:
            return None
        c = c[c["ts"] < end]
        if len(c) < S["orb_minutes"] // S["candle_minutes"]:
            return None
        return row["underlying_symbol"], [float(c["high"].max()), float(c["low"].min()), row["instrument_key"]]

    orb = dict(x for x in api.many(one, [r for _, r in futs.iterrows()]) if x)
    if orb:
        path.write_text(json.dumps({"day": str(today), "orb": orb}), encoding="utf-8")
    return orb


def stock_candidates(api: Upstox, orb: dict, limit: int) -> list[str]:
    """One batch quote: stocks past their opening range on the matching side of VWAP."""
    q = api.quotes([v[2] for v in orb.values()])
    scored = []
    for sym, (hi, lo, key) in orb.items():
        x = q.get(key)
        if not x:
            continue
        ltp, vwap = float(x.get("last_price") or 0), float(x.get("average_price") or 0)
        if ltp > hi and (not vwap or ltp > vwap):
            scored.append(((ltp - hi) / hi, sym))
        elif ltp < lo and (not vwap or ltp < vwap):
            scored.append(((lo - ltp) / lo, sym))
    # freshest breakouts first: the strategy only enters within 3 candles of the break
    return [s for _, s in sorted(scored)[:limit]]


# =================================================================== BACKTEST
def backtest_days(api: Upstox, idx: str, start: date, end: date, progress: Progress = None,
                  allow_price_only: bool = True) -> tuple[dict[date, IndexDay], str]:
    """Returns ({day: IndexDay}, mode). mode = 'full' (with option OI) or 'price_only'."""
    c = config.INDICES[idx]
    mins = S["candle_minutes"]
    plus = api.has_plus(idx)
    mode = "full" if plus else "price_only"
    if not plus and not allow_price_only:
        raise PlusRequired("Upstox Plus is required for 1-2 years of option OI data.")
    _say(progress, f"{idx}: spot data...")
    warm = start - timedelta(days=10)
    spot = cached(f"spot_{idx}", warm, end, lambda: api.historical(c["spot_key"], warm, end, mins))

    # ---------------- futures (continuous)
    listed_f = api.futures(idx)
    fut_exp = {e: ("listed", r["instrument_key"]) for e, r in zip(listed_f["expiry_date"], listed_f.to_dict("records"))}
    expired_opt_exp: list[date] = []
    if plus:
        expired_opt_exp = api.expired_expiries(idx)
        months = {}
        for e in expired_opt_exp:
            months[(e.year, e.month)] = max(months.get((e.year, e.month), e), e)
        for e in months.values():
            fut_exp.setdefault(e, ("expired", None))
    parts, prev = [], warm - timedelta(days=1)
    for e in sorted(fut_exp):
        s, t = max(warm, prev + timedelta(days=1)), min(end, e)
        prev = e
        if s > t:
            continue
        kind, key = fut_exp[e]
        _say(progress, f"{idx}: future {e} ...")
        try:
            if kind == "listed":
                df = cached(f"fut_{key}", s, t, lambda: api.historical(key, s, t, mins))
            else:
                key = api.expired_future_key(idx, e)
                if not key:
                    continue
                df = cached(f"xfut_{key}", s, t, lambda: api.expired_candles(key, s, t, mins))
        except UpstoxError as err:
            _say(progress, f"  skip {e}: {err}")
            continue
        if not df.empty:
            parts.append(df[(df["ts"].dt.date >= s) & (df["ts"].dt.date <= t)])
        if e >= end:
            break
    if not parts:
        return {}, mode
    fut_all = prepare_fut(pd.concat(parts).sort_values("ts").drop_duplicates("ts").reset_index(drop=True))
    trading_days = [d for d in sorted(fut_all["day"].unique()) if start <= d <= end]
    sym = listed_f["trading_symbol"].iloc[0] if not listed_f.empty else idx

    # ---------------- options (grouped by expiry)
    listed_exp = api.listed_option_expiries(idx)
    all_exp = sorted(set(expired_opt_exp) | set(listed_exp)) if plus else []
    groups: dict = {}
    for d in trading_days:
        e = option_expiry_for(d, all_exp) if plus else "n/a"
        if e:
            groups.setdefault(e, []).append(d)
    days: dict[date, IndexDay] = {}
    for e, ds in sorted(groups.items(), key=lambda kv: kv[1][0]):
        sp = spot[spot["ts"].dt.date.isin(ds)]
        if sp.empty:
            continue
        opts_full = {}
        # without Plus we cannot know which (now expired) contract was the nearest one on a past day,
        # so the free backtest runs price-only (no option OI) for every day
        if mode == "full":
            try:
                keys = api.listed_option_keys(idx, e) if e in listed_exp else api.expired_option_keys(idx, e)
            except UpstoxError as err:
                _say(progress, f"  expiry {e} skip: {err}")
                keys = {}
            need = set()
            for d in ds:
                need.update(strikes_for(sp[sp["ts"].dt.date == d], c["step"]))
            _say(progress, f"{idx}: expiry {e} options ({len(need) * 2} contracts, {len(ds)} days)...")
            s, t = ds[0], ds[-1]
            for k in sorted(need):
                for typ in ("CE", "PE"):
                    key = keys.get((k, typ))
                    if not key:
                        continue
                    try:
                        if e in listed_exp:
                            df = cached(f"opt_{key}", s, t, lambda: api.historical(key, s, t, mins))
                        else:
                            df = cached(f"xopt_{key}", s, t, lambda: api.expired_candles(key, s, t, mins))
                    except UpstoxError:
                        continue
                    if not df.empty:
                        opts_full[(k, typ)] = df
        for d in ds:
            sd = sp[sp["ts"].dt.date == d].reset_index(drop=True)
            od = _opts_slice(opts_full, d)
            iday = IndexDay(idx, c["step"], str(e), fut_all, sd, od, sym, None)
            iday.skip_oi = mode == "price_only"
            days[d] = iday
    return days, mode
