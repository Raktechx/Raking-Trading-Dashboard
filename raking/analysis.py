"""Signal-log analysis: the same tables feed the Excel log and the dashboard."""
from __future__ import annotations

import pandas as pd

from . import config

CLOSED = ("TP", "SL", "TIME")

COLUMNS = [
    ("day", "Date"), ("signal_time", "Signal time"), ("created_at", "Alert sent at"),
    ("instrument", "Instrument"), ("group", "Type"), ("option", "Option"), ("expiry", "Expiry"),
    ("side", "Side"), ("opt_entry", "Premium entry"), ("opt_sl", "Premium SL"), ("opt_tp", "Premium target"),
    ("opt_exit", "Premium exit"), ("opt_points", "Premium P/L (pts)"), ("result", "Result"),
    ("exit_time", "Exit time"), ("r", "R"), ("alt_result", "Method B result"), ("alt_r", "Method B R"),
    ("atm_oi_pct", "ATM OI %"), ("writing_oi_pct", "Writing OI %"), ("volume_x", "Volume x avg"),
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
    for c, _ in COLUMNS:
        if c not in df:
            df[c] = None
    for c in ("r", "alt_r", "opt_entry", "opt_exit", "opt_points"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["hour"] = df["signal_time"].str.slice(0, 2) + ":00"
    df["weekday"] = pd.to_datetime(df["day"]).dt.day_name()
    return df.sort_values(["day", "signal_time"], ascending=False).reset_index(drop=True)


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
    a, b = stats(df), stats(df, "alt_r")
    summary = pd.DataFrame({
        "Measure": ["Signals", "Closed trades", "Win rate %", "Total R", "Average R", "Max drawdown R",
                    "Profit factor", "Method B (option-candle SL) total R", "Method B trades"],
        "Value": [len(df), a["trades"], a["win_rate"], a["total_r"], a["avg_r"], a["max_dd"], a["pf"],
                  b["total_r"], b["trades"]]})
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df[[c for c, _ in COLUMNS]].rename(columns=dict(COLUMNS)).to_excel(xw, sheet_name="Signals", index=False)
        summary.to_excel(xw, sheet_name="Summary", index=False)
        for by, name in (("instrument", "By instrument"), ("side", "By side"), ("hour", "By hour"),
                         ("weekday", "By weekday"), ("day", "By day")):
            breakdown(df, by).to_excel(xw, sheet_name=name, index=False)
