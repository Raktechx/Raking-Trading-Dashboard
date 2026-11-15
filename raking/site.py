"""Static live dashboard for GitHub (docs/index.html + LIVE.md), rebuilt on every scheduled scan.
View it any time on your phone: LIVE.md in the repo, or docs/index.html through GitHub Pages."""
from __future__ import annotations

import html
from pathlib import Path

import pandas as pd

from . import config, store
from .config import STRATEGY as S

DOCS = config.ROOT / "docs"


def _paper_stats(saved: dict) -> dict:
    df = pd.DataFrame(saved.values())
    if df.empty:
        return {"signals": 0}
    done = df[df["result"].isin(["TP", "SL", "TIME"])].sort_values(["day", "signal_time"])
    out = {"signals": len(df), "closed": len(done), "since": df["day"].min()}
    if len(done):
        r = pd.to_numeric(done["r"])
        eq = r.cumsum()
        out.update(win_rate=round((r > 0).mean() * 100, 1), total_r=round(r.sum(), 2),
                   max_dd=round(float((eq - eq.cummax()).min()), 2))
    return out


def _card(s: dict) -> str:
    color = "#16c784" if s["side"] == "CE" else "#ea3943"
    res = {"TP": "🎯 Target hit", "SL": "🛑 SL hit", "TIME": f"⏱️ Time exit ({s.get('r')}R)",
           "OPEN": "🟡 Trade open"}.get(s.get("result"), s.get("result") or "")
    prem = ""
    if s.get("opt_entry"):
        prem = (f"<div class='lv'>Premium entry <b>₹{s['opt_entry']}</b> · SL <b class='r'>₹{s.get('opt_sl')}</b> · "
                f"Target <b class='g'>₹{s.get('opt_tp')}</b></div>")
    reasons = "".join(f"<li>{html.escape(r)}</li>" for r in s.get("reasons", []))
    return (f"<div class='card' style='border-left-color:{color}'>"
            f"<div class='big'>BUY {html.escape(s['option'])} <span class='tag'>exp {s.get('expiry')}</span></div>"
            f"<div class='muted'>{s['day']} {s['signal_time']} · numbers fixed · {res}</div>{prem}"
            f"<ul>{reasons}</ul></div>")


def _checklist(label: str, chk: dict | None) -> str:
    if not chk:
        return f"<div class='card'><div class='big'>{label}</div><div class='muted'>No data yet.</div></div>"
    rows = ""
    for side in ("CE", "PE"):
        items = " · ".join(f"<span class='{'g' if v else 'n'}'>{'●' if v else '○'} {html.escape(k)}</span>"
                           for k, v in chk[side].items())
        rows += f"<div><b>{side}</b> (ATM {chk.get(side + '_atm')}): {items}</div>"
    return (f"<div class='card'><div class='big'>{label} — waiting for all conditions</div>{rows}"
            f"<div class='muted'>{chk['time']} · price {chk['price']:.1f} · VWAP {chk['vwap']:.1f} · "
            f"EMA20 {chk['ema20']:.1f} · EMA50 {chk['ema50']:.1f}</div></div>")


def build(out: dict | None, saved: dict, market_status: str) -> Path:
    DOCS.mkdir(exist_ok=True)
    now = pd.Timestamp.now(tz=config.TZ)
    today = str(now.date())
    upd = (out or {}).get("time")
    upd = upd.strftime("%d %b %Y, %H:%M:%S") if upd is not None else now.strftime("%d %b %Y, %H:%M:%S")
    st = _paper_stats(saved)
    cols = ""
    md_today = []
    for idx, c in config.INDICES.items():
        sigs = store.today_signals(saved, today, idx)
        inner = "".join(_card(s) for s in reversed(sigs))
        if not any(s.get("result") == "OPEN" for s in sigs):
            inner += _checklist("Live checklist", ((out or {}).get("checks") or {}).get(idx))
        status = ((out or {}).get("status") or {}).get(idx, "")
        note = f"<div class='muted'>{html.escape(status)}</div>" if status and status != "OK" else ""
        cols += f"<div class='col'><h2>{c['label']}</h2>{note}{inner}</div>"
        for s in sigs:
            md_today.append(f"| {s['signal_time']} | **BUY {s['option']}** | {s.get('opt_entry') or '-'} | "
                            f"{s.get('opt_sl') or '-'} | {s.get('opt_tp') or '-'} | {s.get('result')} |")
    stock_sigs = [x for x in store.today_signals(saved, today) if x["index"] not in config.INDICES]
    inner = "".join(_card(x) for x in reversed(stock_sigs)) or \
        "<div class='card'><div class='muted'>No stock option signal yet today.</div></div>"
    cand = ", ".join((out or {}).get("candidates") or []) or "—"
    cols += (f"<div class='col'><h2>Stock options</h2><div class='muted'>Being checked now: {html.escape(cand)}</div>"
             f"{inner}</div>")
    for s in stock_sigs:
        md_today.append(f"| {s['signal_time']} | **BUY {s['option']}** | {s.get('opt_entry') or '-'} | "
                        f"{s.get('opt_sl') or '-'} | {s.get('opt_tp') or '-'} | {s.get('result')} |")
    recent = sorted(saved.values(), key=lambda s: (s["day"], s["signal_time"]), reverse=True)[:40]
    rows = "".join(
        f"<tr><td>{s['day']}</td><td>{s['signal_time']}</td><td>{html.escape(s['option'])}</td>"
        f"<td>{s.get('opt_entry') or '-'}</td><td>{s.get('opt_sl') or '-'}</td><td>{s.get('opt_tp') or '-'}</td>"
        f"<td>{s.get('opt_exit') or '-'}</td>"
        f"<td>{s.get('result') or ''}</td><td>{s.get('r') if s.get('r') is not None else ''}</td></tr>" for s in recent)
    stats = (f"Signals {st.get('signals', 0)} · closed {st.get('closed', 0)} · win rate {st.get('win_rate', '—')}% · "
             f"total {st.get('total_r', '—')} R · max drawdown {st.get('max_dd', '—')} R · since {st.get('since', '—')}")
    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60">
<title>{config.APP_NAME} — Live</title>
<style>
body{{margin:0;background:#0b0f19;color:#e5e7eb;font-family:system-ui,Segoe UI,Roboto,sans-serif}}
.wrap{{max-width:1100px;margin:0 auto;padding:16px}} header{{display:flex;justify-content:space-between;gap:12px}}
h1{{margin:0;font-size:1.5rem}} h2{{font-size:1.15rem;margin:14px 0 8px}} .upd{{text-align:right;color:#9aa4b2;font-size:.85rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}}
.card{{background:#121826;border:1px solid #2a3142;border-left:6px solid #6b7280;border-radius:12px;padding:12px 14px;margin-bottom:10px}}
.big{{font-weight:700;font-size:1.1rem}} .muted{{color:#9aa4b2;font-size:.82rem;margin-top:4px}} .lv{{margin-top:6px}}
.tag{{background:#1f2937;border-radius:9px;padding:1px 7px;font-size:.75rem}} .g{{color:#16c784}} .r{{color:#ea3943}} .n{{color:#6b7280}}
ul{{margin:6px 0 0 18px;padding:0;font-size:.85rem}} table{{width:100%;border-collapse:collapse;font-size:.82rem}}
td,th{{border-bottom:1px solid #222a3a;padding:5px;text-align:left}} .box{{overflow-x:auto}}
</style></head><body><div class="wrap">
<header><div><h1>📈 {config.APP_NAME}</h1><div class="muted">Combined Strategy · Nifty 50 · Bank Nifty · Sensex · F&amp;O stocks · 5-min</div></div>
<div class="upd">Last update<br><b>{upd} IST</b><br>{market_status}</div></header>
<div class="grid">{cols}</div>
<h2>Paper trading record</h2><div class="card">{stats}</div>
<div class="box"><table><tr><th>Day</th><th>Time</th><th>Option</th><th>Entry</th><th>SL</th>
<th>Target</th><th>Exit</th><th>Result</th><th>R</th></tr>{rows}</table></div>
<p class="muted">Updated about every 5 minutes during market hours by GitHub Actions. Max {S['max_trades_per_day']} index trades
and {config.STOCKS['max_trades_per_day']} stock trades a day, no new entry after {S['last_entry']}. Paper-trading tool — not investment advice.</p>
</div></body></html>"""
    path = DOCS / "index.html"
    path.write_text(page, encoding="utf-8")
    md = [f"# 📈 {config.APP_NAME} — Live", "", f"**Last update:** {upd} IST · {market_status}", "",
          "## Today's signals", ""]
    if md_today:
        md += ["| Time | Signal | Premium entry | Premium SL | Premium target | Result |",
               "|---|---|---|---|---|---|", *md_today]
    else:
        md.append("No signal yet today.")
    md += ["", "## Paper trading record", "", stats, "",
           "_Updated about every 5 minutes during market hours. Full page: `docs/index.html`._"]
    (config.ROOT / "LIVE.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    return path
