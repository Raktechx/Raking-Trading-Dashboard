"""Fixed signal store. Once a signal is saved for (day, index) its numbers never change.
Also keeps an Excel log of every signal and its final result."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from . import config

STORE = config.DATA_DIR / "signals.json"
LOG_XLSX = config.DATA_DIR / "signal_log.xlsx"


DEMO_STORE = config.DATA_DIR / "demo_signals.json"
DEMO_XLSX = config.DATA_DIR / "demo_signal_log.xlsx"


def load(path: Path | None = None) -> dict:
    path = path or STORE
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save(data: dict, path: Path | None = None) -> None:
    path = path or STORE
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def today_signals(data: dict, day: str, idx: str | None = None) -> list[dict]:
    out = [s for s in data.values() if s.get("day") == day and (idx is None or s.get("index") == idx)]
    return sorted(out, key=lambda s: s["signal_time"])


def add_if_new(data: dict, sig: dict) -> bool:
    if sig["id"] in data:
        return False          # already fixed - never overwrite
    data[sig["id"]] = sig
    return True


def update_result(data: dict, k: str, res: dict) -> bool:
    s = data.get(k)
    if not s or s.get("result") in ("TP", "SL", "TIME"):
        return False
    if res.get("result") and res["result"] != s.get("result"):
        s.update(res)
        return True
    return False


def write_excel(data: dict, path: Path | None = None) -> Path:
    path = path or LOG_XLSX
    rows = []
    for s in data.values():
        r = dict(s)
        r["reasons"] = " | ".join(r.get("reasons", []))
        rows.append(r)
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["day", "signal_time"], ascending=[False, False])
    try:
        df.to_excel(path, index=False)
    except PermissionError:   # file open in Excel
        pass
    return path
