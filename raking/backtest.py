"""Backtest of the combined strategy (same code path as the live scanner)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config
from .strategy import day_trades, public

COST_R = 0.10   # brokerage + slippage per trade, in R (conservative)


def run(days_by_index: dict[str, dict], pullback: bool = False) -> pd.DataFrame:
    """days_by_index = {idx: {date: IndexDay}} -> one row per trade."""
    all_days = sorted({d for m in days_by_index.values() for d in m})
    rows = []
    for day in all_days:
        todays = {idx: m[day] for idx, m in days_by_index.items() if day in m}
        trades, _ = day_trades(day, todays, pullback=pullback)
        for t in trades:
            if t["result"] == "OPEN":
                continue
            r = public(t)
            r["reasons"] = " | ".join(r["reasons"])
            r["net_r"] = round(r["r"] - COST_R, 2)
            rows.append(r)
    return pd.DataFrame(rows)


def stats(t: pd.DataFrame, days: int | None = None) -> dict:
    if t.empty:
        return {"trades": 0, "days": days}
    n = len(t)
    eq = t["net_r"].cumsum()
    wins, losses = t[t["net_r"] > 0], t[t["net_r"] <= 0]
    gl = -losses["net_r"].sum()
    opt = pd.to_numeric(t["opt_points"], errors="coerce").dropna() if "opt_points" in t else pd.Series(dtype=float)
    return {
        "days": days,
        "trades": n,
        "win_rate_pct": round(len(wins) / n * 100, 1),
        "breakeven_win_rate_pct": round(100 / (1 + config.STRATEGY["reward_risk"]), 1),
        "tp": int((t["result"] == "TP").sum()), "sl": int((t["result"] == "SL").sum()),
        "time_exit": int((t["result"] == "TIME").sum()),
        "avg_r": round(t["net_r"].mean(), 3),
        "total_r": round(t["net_r"].sum(), 2),
        "avg_points": round(t["points"].mean(), 1),
        "biggest_loss_points": round(t["points"].min(), 1),
        "biggest_loss_r": round(t["net_r"].min(), 2),
        "max_drawdown_r": round(float((eq - eq.cummax()).min()), 2),
        "profit_factor": round(wins["net_r"].sum() / gl, 2) if gl > 0 else None,
        "avg_option_points": round(opt.mean(), 1) if len(opt) else None,
        "biggest_option_loss": round(opt.min(), 1) if len(opt) else None,
    }


def summary(t: pd.DataFrame, days_by_index: dict[str, dict]) -> pd.DataFrame:
    rows = []
    s = stats(t, len({d for m in days_by_index.values() for d in m}))
    s["index"] = "ALL"
    rows.append(s)
    for idx, m in days_by_index.items():
        part = t[t["index"] == idx] if not t.empty else t
        s = stats(part, len(m))
        s["index"] = idx
        rows.append(s)
    cols = ["index"] + [c for c in rows[0] if c != "index"]
    return pd.DataFrame(rows).reindex(columns=cols)


def compare(base: pd.DataFrame, with_pb: pd.DataFrame, n_days: int) -> pd.DataFrame:
    """Current system (breakout only) vs the same system with the pullback setup added."""
    parts = [("Current: breakout only", base), ("Upgraded: breakout + pullback", with_pb),
             ("  of which pullback trades", with_pb[with_pb["setup"] == "Pullback"] if not with_pb.empty else with_pb)]
    rows = []
    for name, part in parts:
        s = stats(part, n_days)
        s["system"] = name
        rows.append(s)
    cols = ["system"] + [c for c in rows[0] if c != "system"]
    return pd.DataFrame(rows).reindex(columns=cols)


VARIANTS = {
    "Base (current rules)": {},
    "+ 15-min trend filter": {"htf_trend": True},
    "+ breakeven at +1R": {"breakeven_at_r": 1.0},
    "+ skip 12:00-13:30": {"skip_entry_window": ["12:00", "13:30"]},
    "+ stop after 2 losses": {"max_losses_per_day": 2},
}


def variants(days_by_index: dict, n_days: int) -> pd.DataFrame:
    """Each accuracy upgrade tested alone against the same data, then all winners together."""
    from .config import STRATEGY
    keep = {k: STRATEGY[k] for v in VARIANTS.values() for k in v}
    rows, base, winners = [], None, {}
    try:
        for name, ov in VARIANTS.items():
            STRATEGY.update(keep)
            STRATEGY.update(ov)
            s = stats(run(days_by_index), n_days)
            s["variant"] = name
            rows.append(s)
            if base is None:
                base = s
            elif s.get("trades") and _better(s, base):
                winners.update(ov)
        STRATEGY.update(keep)
        STRATEGY.update(winners)
        s = stats(run(days_by_index), n_days)
        s["variant"] = "ALL WINNERS: " + (", ".join(f"{k}={v}" for k, v in winners.items()) or "none")
        rows.append(s)
    finally:
        STRATEGY.update(keep)
    cols = ["variant"] + [c for c in rows[0] if c != "variant"]
    return pd.DataFrame(rows).reindex(columns=cols)


def _better(s: dict, base: dict) -> bool:
    """Better = more total R AND a profit factor at least as good AND no deeper drawdown."""
    pf, bpf = s.get("profit_factor") or 0, base.get("profit_factor") or 0
    return (s.get("total_r", 0) > base.get("total_r", 0) and pf >= bpf
            and s.get("max_drawdown_r", 0) >= base.get("max_drawdown_r", 0) - 0.5)


def monthly(t: pd.DataFrame) -> pd.DataFrame:
    if t.empty:
        return t
    g = t.assign(month=pd.to_datetime(t["day"]).dt.strftime("%Y-%m")).groupby("month")["net_r"]
    return pd.DataFrame({"trades": g.size(), "win_rate_pct": g.apply(lambda x: round((x > 0).mean() * 100, 1)),
                         "total_r": g.sum().round(2)}).reset_index()


def save_excel(t: pd.DataFrame, summ: pd.DataFrame, path: Path, mode: str,
               comp: pd.DataFrame | None = None, var: pd.DataFrame | None = None) -> Path:
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame({"item": ["Mode", "Generated", "Cost per trade", "Note"],
                      "value": [mode, f"{pd.Timestamp.now(tz=config.TZ):%Y-%m-%d %H:%M}", f"{COST_R} R",
                                "If SL and target were both touched in the same candle, SL is assumed"]}).to_excel(xw, sheet_name="Info", index=False)
        summ.to_excel(xw, sheet_name="Summary", index=False)
        if comp is not None and not comp.empty:
            comp.to_excel(xw, sheet_name="Pullback test", index=False)
        if var is not None and not var.empty:
            var.to_excel(xw, sheet_name="Upgrade tests", index=False)
        monthly(t).to_excel(xw, sheet_name="Monthly", index=False)
        (t if not t.empty else pd.DataFrame({"info": ["No trades"]})).to_excel(xw, sheet_name="Trades", index=False)
    return path
