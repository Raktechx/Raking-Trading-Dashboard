"""Checks the Upstox data plumbing (live + backtest, with and without Upstox Plus) using a fake API."""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from raking import backtest, config, data, demo  # noqa: E402
from raking.upstox import COLS, PlusRequired  # noqa: E402


class FakeUpstox:
    """Serves demo candles through the same methods the real client has."""

    def __init__(self, plus: bool, today: date):
        self.plus, self.today, self.min_gap = plus, today, 0
        self.days = {}
        px = demo.BASE["NIFTY"]
        for d in pd.bdate_range(today - timedelta(days=60), today):
            s, f, o = demo.make_day("NIFTY", d.date(), px)
            px = float(s["close"].iloc[-1])
            self.days[d.date()] = (s, f, o)

    # weekly expiries every Tuesday, monthly futures = last Tuesday of the month
    def _tuesdays(self):
        return [d.date() for d in pd.date_range(self.today - timedelta(days=70), self.today + timedelta(days=40),
                                                freq="W-TUE")]

    def spec(self, idx):
        return config.INDICES["NIFTY"]

    def futures(self, idx):
        months = {}
        for e in self._tuesdays():
            months[(e.year, e.month)] = e
        listed = [e for e in months.values() if e >= self.today][:3]
        return pd.DataFrame([{"instrument_key": f"FUT|{e}", "expiry_date": e, "trading_symbol": f"NIFTY FUT {e}",
                              "lot_size": 75} for e in listed])

    def near_future(self, idx, on):
        f = self.futures(idx)
        return f[f["expiry_date"] >= on].iloc[0].to_dict()

    def listed_option_expiries(self, idx):
        return [e for e in self._tuesdays() if e >= self.today]

    def listed_option_keys(self, idx, e):
        return {(float(k), t): f"OPT|{e}|{k}|{t}" for k in range(20000, 32000, 50) for t in ("CE", "PE")}

    expired_option_keys = listed_option_keys

    def has_plus(self, idx="NIFTY"):
        return self.plus

    def expired_expiries(self, idx):
        if not self.plus:
            raise PlusRequired("plus")
        return [e for e in self._tuesdays() if e < self.today]

    def expired_future_key(self, idx, e):
        return f"FUT|{e}"

    def _serve(self, key, start, end):
        parts = []
        for d, (s, f, o) in self.days.items():
            if not (start <= d <= end):
                continue
            if key.startswith("NSE_INDEX"):
                parts.append(s)
            elif key.startswith("FUT"):
                parts.append(f)
            else:
                _, _, k, t = key.split("|")
                if (int(k), t) in o:
                    parts.append(o[(int(k), t)])
        return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLS)

    def historical(self, key, start, end, minutes=5):
        return self._serve(key, start, end)

    def expired_candles(self, key, start, end, minutes=5):
        if not self.plus:
            raise PlusRequired("plus")
        return self._serve(key, start, end)

    def intraday(self, key, minutes=5):
        return self._serve(key, self.today, self.today)

    def option_chain(self, idx, e):
        return []


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(config, "INDICES", {"NIFTY": config.INDICES["NIFTY"]})


def test_backtest_with_plus_uses_option_oi():
    today = date(2026, 11, 13)
    api = FakeUpstox(True, today)
    days, mode = data.backtest_days(api, "NIFTY", today - timedelta(days=45), today - timedelta(days=1))
    assert mode == "full" and len(days) > 25
    assert all(d.opts and not d.skip_oi for d in days.values())
    t = backtest.run({"NIFTY": days})
    assert not t.empty and t["reasons"].str.contains("writing").all()


def test_backtest_without_plus_is_price_only():
    today = date(2026, 11, 13)
    api = FakeUpstox(False, today)
    days, mode = data.backtest_days(api, "NIFTY", today - timedelta(days=45), today - timedelta(days=1))
    assert mode == "price_only"
    assert any(d.skip_oi for d in days.values())


def test_live_day_builds_inputs(monkeypatch):
    today = date(2026, 11, 13)
    api = FakeUpstox(True, today)
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: pd.Timestamp(f"{today} 15:40", tz=tz)))
    d = data.live_day(api, "NIFTY", today)
    assert d is not None and len(d.strikes) > 10 and not d.fut.empty
    atm = min(d.strikes, key=lambda k: abs(k - float(d.spot["close"].iloc[-1])))
    assert not d.opts.get((atm, "CE")).empty      # fetched on demand
    assert d.fut["day"].nunique() > 3   # warm-up days for EMA50


def test_live_page_builds(tmp_path, monkeypatch):
    from raking import live, site, store
    monkeypatch.setattr(store, "DEMO_STORE", tmp_path / "d.json")
    monkeypatch.setattr(store, "DEMO_XLSX", tmp_path / "d.xlsx")
    monkeypatch.setattr(site, "DOCS", tmp_path / "docs")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    out = live.scan(demo_mode=True)
    page = site.build(out, out["signals"], "⚪ Market closed")
    text = page.read_text(encoding="utf-8")
    assert "Raking the Trader" in text and "Paper trading record" in text
    assert (tmp_path / "LIVE.md").exists()


def test_stock_two_step_scan(tmp_path, monkeypatch):
    """Stocks: ORB file + one batch quote pick candidates; only they get the full strategy."""
    from raking import data as dmod
    today = date(2026, 11, 13)
    api = FakeUpstox(True, today)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(pd.Timestamp, "now", classmethod(lambda cls, tz=None: pd.Timestamp(f"{today} 11:00", tz=tz)))
    futs = pd.DataFrame([{"underlying_symbol": "AAA", "instrument_key": "FUT|A"},
                         {"underlying_symbol": "BBB", "instrument_key": "FUT|B"}])
    api.fno_stock_futures = lambda on: futs
    api.many = lambda fn, items, workers=6: [fn(i) for i in items]
    orb = dmod.stock_orbs(api, today)
    assert set(orb) == {"AAA", "BBB"} and (tmp_path / "stock_orb.json").exists()
    hi_a = orb["AAA"][0]
    api.quotes = lambda keys: {"FUT|A": {"last_price": hi_a + 5, "average_price": hi_a - 5},
                               "FUT|B": {"last_price": orb["BBB"][0] - 1, "average_price": 1}}
    assert dmod.stock_candidates(api, orb, 8) == ["AAA"]
