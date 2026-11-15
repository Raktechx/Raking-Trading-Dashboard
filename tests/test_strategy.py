"""Offline tests (no Upstox needed).  Run:  python -m pytest -q   (or double-click Run Tests.bat)"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from raking import backtest, demo, store  # noqa: E402
from raking.config import STRATEGY as S  # noqa: E402
from raking.strategy import IndexDay, day_trades, prepare_fut, simulate  # noqa: E402

DAY = date(2026, 9, 15)


def build(kind_today, idx="NIFTY", oi_flip=False, cut=None):
    """3 warm-up days + today with a chosen behaviour."""
    parts, spot_today, opts_today = [], None, None
    px = demo.BASE[idx]
    for i, d in enumerate([DAY - timedelta(days=k) for k in (3, 2, 1)] + [DAY]):
        # day before today trends the same way, so EMA20/50 are already aligned
        k = kind_today if d == DAY else (kind_today if d == DAY - timedelta(days=1) else 0.0)
        s, f, o = demo.make_day(idx, d, px, kind=k)
        px = float(s["close"].iloc[-1])
        parts.append(f)
        if d == DAY:
            spot_today, opts_today = s, o
    if oi_flip:   # same prices, but option OI falling everywhere -> no build-up / no writing
        opts_today = {k: v.assign(oi=v["oi"].iloc[0] * (1 - 0.01 * pd.RangeIndex(len(v)) / 10)) for k, v in opts_today.items()}
    fut = pd.concat(parts, ignore_index=True)
    if cut is not None:
        fut = fut[fut["ts"] <= cut]
        spot_today = spot_today[spot_today["ts"] <= cut]
        opts_today = {k: v[v["ts"] <= cut] for k, v in opts_today.items()}
    return IndexDay(idx, 50 if idx == "NIFTY" else 100, "2026-09-16", prepare_fut(fut), spot_today, opts_today)


def test_bull_day_gives_ce_with_rules():
    trades, _ = day_trades(DAY, {"NIFTY": build(1.0)})
    assert trades and trades[0]["side"] == "CE"
    t = trades[0]
    assert abs((t["tp"] - t["entry"]) - S["reward_risk"] * (t["entry"] - t["sl"])) < 0.11   # 1:2
    assert t["entry"] > t["orb_high"] and t["volume_x"] > 1                                   # range break + volume
    assert "09:30" < t["signal_time"] <= S["last_entry"]
    d = build(1.0)
    f = d.fut[d.fut["day"] == DAY]
    prev = f[f["bar_close"].dt.strftime("%H:%M") < t["signal_time"]].iloc[-1]
    assert abs(t["sl"] - round(prev["low"] / 0.05) * 0.05) < 0.06                              # SL = previous low
    assert t["option"].endswith("CE") and t["opt_entry"]


def test_bear_day_gives_pe():
    trades, _ = day_trades(DAY, {"NIFTY": build(-1.0)})
    assert trades and trades[0]["side"] == "PE"
    assert trades[0]["sl"] > trades[0]["entry"] > trades[0]["tp"]


def test_no_option_oi_confirmation_no_trade():
    trades, _ = day_trades(DAY, {"NIFTY": build(1.0, oi_flip=True)})
    assert trades == []


def test_range_day_no_trade():
    trades, _ = day_trades(DAY, {"NIFTY": build(0.0)})
    assert all(t["signal_time"] <= S["last_entry"] for t in trades)


def test_max_two_trades_a_day_and_no_entry_after_3pm():
    days_by = {i: demo.backtest_days(i, date(2026, 6, 1), date(2026, 9, 30)) for i in ("NIFTY", "BANKNIFTY")}
    t = backtest.run(days_by)
    assert not t.empty
    assert t.groupby("day").size().max() <= S["max_trades_per_day"]
    assert (t["signal_time"] <= S["last_entry"]).all()
    st = backtest.stats(t)
    for k in ("win_rate_pct", "avg_r", "biggest_loss_r", "max_drawdown_r"):
        assert k in st


def test_signal_numbers_fixed_when_more_candles_arrive():
    full, _ = day_trades(DAY, {"NIFTY": build(1.0)})
    t = full[0]
    cut = pd.Timestamp(f"{DAY} {t['signal_time']}").tz_localize("Asia/Kolkata") - pd.Timedelta(minutes=5)
    early, _ = day_trades(DAY, {"NIFTY": build(1.0, cut=cut)})
    keys = ("signal_time", "entry", "sl", "tp", "strike", "opt_entry")
    assert tuple(early[0][k] for k in keys) == tuple(t[k] for k in keys)
    assert early[0]["result"] == "OPEN"


def test_simulate_conservative_same_candle():
    d = build(1.0)
    t = day_trades(DAY, {"NIFTY": d})[0][0]
    s = dict(t, sl=t["entry"] - 0.05, tp=t["entry"] + 0.10)   # both touched next candle
    assert simulate(s, d)["result"] == "SL"


def test_store_never_overwrites(tmp_path):
    p = tmp_path / "s.json"
    data = store.load(p)
    assert store.add_if_new(data, {"id": "a", "day": "2026-09-15", "index": "NIFTY", "entry": 1, "signal_time": "10:00"})
    assert not store.add_if_new(data, {"id": "a", "day": "2026-09-15", "index": "NIFTY", "entry": 2, "signal_time": "10:00"})
    store.save(data, p)
    assert store.load(p)["a"]["entry"] == 1
