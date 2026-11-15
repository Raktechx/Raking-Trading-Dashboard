import numpy as np, pandas as pd
from raking import quality
from raking.strategy import IndexDay, prepare_fut


def _day(up=True):
    ts = pd.date_range("2026-11-16 09:15", periods=60, freq="5min", tz="Asia/Kolkata")
    base = 100 + np.arange(60) * (0.5 if up else -0.5)
    df = pd.DataFrame({"ts": ts, "open": base - 0.2, "high": base + 0.5, "low": base - 0.5,
                       "close": base + (0.4 if up else -0.4), "volume": 1000.0, "oi": 0})
    return IndexDay("NIFTY", 50, "2026-11-18", prepare_fut(df), df)


def _chain(spot):
    rows = []
    for k in range(int(spot) - 10, int(spot) + 11, 2):
        rows.append({"strike_price": k, "underlying_spot_price": spot,
                     "call_options": {"market_data": {"oi": 1000, "prev_oi": 900},
                                      "option_greeks": {"iv": 14}},
                     "put_options": {"market_data": {"oi": 1300, "prev_oi": 1000},
                                     "option_greeks": {"iv": 15}}})
    return rows


def test_score_range_and_grade():
    d = _day()
    sig = {"side": "CE", "index": "NIFTY", "group": "INDEX", "_pos": 59, "volume_x": 2.0,
           "atm_oi_pct": 4.0, "writing_oi_pct": 3.0, "risk_pts": 1.0, "strike": 130}
    r = quality.score(sig, d, _chain(float(d.fut.iloc[-1]["close"])), 14.0, {"NIFTY": 1, "BANKNIFTY": 1})
    assert 0 <= r["score"] <= 100 and r["grade"] in "ABC"
    assert set(r["score_parts"]) == set(quality.WEIGHTS)


def test_score_survives_missing_data():
    d = _day(False)
    sig = {"side": "PE", "index": "NIFTY", "_pos": 59}
    r = quality.score(sig, d)
    assert 0 <= r["score"] <= 100


def test_bias():
    assert quality.bias(_day(True)) == 1 and quality.bias(_day(False)) == -1


def test_precheck_only_grade_a_and_never_unscored():
    from raking.config import STRATEGY as S
    assert S["min_quality"] == 70
    assert quality.passes({"score": 70}, 70) and quality.passes({"score": 91}, 70)
    assert not quality.passes({"score": 69}, 70)
    assert not quality.passes({}, 70)          # could not be checked -> not sent


def test_paper_record_excludes_blocked():
    from raking import analysis
    base = {"day": "2026-11-16", "index": "NIFTY", "side": "CE", "reasons": [], "result": "SL", "r": -1.0}
    saved = {"a": {**base, "id": "a", "signal_time": "10:05"},
             "b": {**base, "id": "b", "signal_time": "10:35", "blocked": True, "block_reason": "grade C"}}
    df = analysis.frame(saved)
    assert list(df["status"]) == ["Blocked", "Sent"]
    assert analysis.stats(analysis.sent(df))["trades"] == 1
