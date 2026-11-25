"""Signal-log analysis: the same tables feed the Excel log and the dashboard."""
from __future__ import annotations

import pandas as pd

from . import config

CLOSED = ("TP", "SL", "TIME")

COLUMNS = [
    ("day", "Date"), ("signal_time", "Signal time"), ("created_at", "Alert sent at"),
    ("instrument", "Instrument"), ("group", "Type"), ("option", "Option"), ("expiry", "Expiry"),
    ("side", "Side"), ("status", "Pre-check"), ("block_reason", "Blocked because"), ("grade", "Grade"), ("score", "Quality score"), ("opt_entry", "Premium entry"), ("opt_sl", "Premium SL"), ("opt_tp", "Premium target"),
    ("opt_exit", "Premium exit"), ("opt_points", "Premium P/L (pts)"), ("result", "Result"),
    ("exit_time", "Exit time"), ("exit_by", "Exit checked on"), ("r", "R"), ("alt_result", "Method B result"), ("alt_r", "Method B R"),
    ("atm_oi_pct", "ATM OI %"), ("writing_oi_pct", "Writing OI %"), ("volume_x", "Volume x avg"),
    ("lot_size", "Lot qty"), ("pnl_gross", "P&L 1 lot ₹ (gross)"), ("pnl_net", "P&L 1 lot ₹ (net)"),
    ("reasons", "Why (all conditions)"),
]


def frame(saved: dict) -> pd.DataFrame:
    """All signals as a tidy table (newest first)."""
    if not saved:
        return pd.DataFrame(columns=[c for c, _ in COLUMNS])
    df = pd.DataFrame(list(saved.values()))
    df["instrument"] = df["index"].map(config.label)
    df["group"] = df["index"].map(lambda x: "Index" if x in config.INDICES else "Stock")
    df["reasons"] = df["reasons"].map(lambda r: " | ".join(r) if isinstance(r, list) else r)
    df["pnl_gross"] = [None if (v := gross_pnl(s)) is None else round(v) for s in saved.values()]
    df["pnl_net"] = [None if (v := net_pnl(s)) is None else round(v) for s in saved.values()]
    for c, _ in COLUMNS:
        if c not in df:
            df[c] = None
    for c in ("r", "alt_r", "opt_entry", "opt_exit", "opt_points"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df["grade"] = df["grade"].fillna("-")
    df["status"] = df["blocked"].map(lambda b: "Blocked" if b is True else "Sent") if "blocked" in df else "Sent"
    df["hour"] = df["signal_time"].str.slice(0, 2) + ":00"
    df["weekday"] = pd.to_datetime(df["day"]).dt.day_name()
    return df.sort_values(["day", "signal_time"], ascending=False).reset_index(drop=True)


def sent(df: pd.DataFrame) -> pd.DataFrame:
    """Only the signals that were really alerted - the paper record never includes blocked ones."""
    return df[df["status"] == "Sent"] if "status" in df else df


def stats(df: pd.DataFrame, col: str = "r") -> dict:
    d = df[df["result"].isin(CLOSED)] if col == "r" else df[df["alt_result"].isin(CLOSED)]
    r = d[col].dropna()
    if r.empty:
        return {"trades": 0, "win_rate": None, "total_r": 0.0, "avg_r": None, "max_dd": 0.0, "pf": None}
    eq = r.iloc[::-1].cumsum()          # oldest first
    loss = -r[r <= 0].sum()
    return {"trades": int(len(r)), "win_rate": round((r > 0).mean() * 100, 1), "total_r": round(r.sum(), 2),
            "avg_r": round(r.mean(), 2), "max_dd": round(float((eq - eq.cummax()).min()), 2),
            "pf": round(r[r > 0].sum() / loss, 2) if loss > 0 else None}


def breakdown(df: pd.DataFrame, by: str) -> pd.DataFrame:
    d = df[df["result"].isin(CLOSED)]
    if d.empty:
        return pd.DataFrame(columns=[by, "Trades", "Win %", "Total R", "Avg R"])
    g = d.groupby(by)["r"]
    return pd.DataFrame({"Trades": g.size(), "Win %": g.apply(lambda x: round((x > 0).mean() * 100, 1)),
                         "Total R": g.sum().round(2), "Avg R": g.mean().round(2)}).reset_index()


def equity(df: pd.DataFrame) -> pd.DataFrame:
    d = df[df["result"].isin(CLOSED)].iloc[::-1]
    return pd.DataFrame({"Trade": range(1, len(d) + 1), "Cumulative R": d["r"].cumsum().values,
                         "When": (d["day"] + " " + d["signal_time"]).values})


def excel(saved: dict, path) -> None:
    df = frame(saved)
    ds = sent(df)
    a, b = stats(ds), stats(ds, "alt_r")
    summary = pd.DataFrame({
        "Measure": ["Signals", "Closed trades", "Win rate %", "Total R", "Average R", "Max drawdown R",
                    "Profit factor", "Method B (option-candle SL) total R", "Method B trades"],
        "Value": [len(ds), a["trades"], a["win_rate"], a["total_r"], a["avg_r"], a["max_dd"], a["pf"],
                  b["total_r"], b["trades"]]})
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df[[c for c, _ in COLUMNS]].rename(columns=dict(COLUMNS)).to_excel(xw, sheet_name="Signals", index=False)
        summary.to_excel(xw, sheet_name="Summary", index=False)
        breakdown(df, "status").to_excel(xw, sheet_name="Sent vs blocked", index=False)
        for by, name in (("grade", "By grade"), ("instrument", "By instrument"), ("side", "By side"), ("hour", "By hour"),
                         ("weekday", "By weekday"), ("day", "By day")):
            breakdown(ds, by).to_excel(xw, sheet_name=name, index=False)


# ---------------------------------------------------------------------------
# Realistic paper P&L: 1 lot, after brokerage, taxes and slippage (Indian option buying).
# ---------------------------------------------------------------------------
COSTS = {
    "brokerage_per_order": 20.0,   # flat Rs per order (buy + sell = 2 orders)
    "stt_sell_pct": 0.10,          # STT on the sell premium
    "exchange_pct": 0.0355,        # exchange transaction charge, both sides
    "gst_pct": 18.0,               # GST on brokerage + exchange charge
    "stamp_buy_pct": 0.003,        # stamp duty on the buy side
    "slippage_pct": 1.0,           # worse fill than the screen price, each side
}


def gross_pnl(s: dict) -> float | None:
    pts, lot = s.get("opt_points"), s.get("lot_size")
    return float(pts) * float(lot) if isinstance(pts, (int, float)) and lot else None


def trade_costs(s: dict) -> float:
    c, lot = COSTS, float(s.get("lot_size") or 0)
    buy, sell = float(s.get("opt_entry") or 0) * lot, float(s.get("opt_exit") or 0) * lot
    brk = 2 * c["brokerage_per_order"]
    exch = (buy + sell) * c["exchange_pct"] / 100
    return (brk + exch + (brk + exch) * c["gst_pct"] / 100 + sell * c["stt_sell_pct"] / 100
            + buy * c["stamp_buy_pct"] / 100 + (buy + sell) * c["slippage_pct"] / 100)


def net_pnl(s: dict) -> float | None:
    g = gross_pnl(s)
    return None if g is None else g - trade_costs(s)
