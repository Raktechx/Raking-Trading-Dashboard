"""BTCUSD PAPER trading (no real orders, no exchange keys). Runs daily on GitHub Actions.
Reads public Delta Exchange candles, computes the chosen strategy's position, records paper
trades in data/btc_paper.json and sends a short Telegram message only when the position changes."""
import json, os, datetime as dt
import btc_backtest as bt

STRATEGY = "TSMOM 20d long-only"   # best after-cost result in the 3-year backtest
NOTIONAL = 1000.0                  # paper size in USD
STATE = "data/btc_paper.json"

def send(msg):
    try:
        from raking import notify
        notify.send(msg)
    except Exception as e:
        print("telegram skipped:", type(e).__name__)

def main():
    bt.YEARS = 1
    h = bt.fetch()
    target = float(bt.strategies(h)[STRATEGY].fillna(0).iloc[-1])
    target = 1 if target > 0.5 else (-1 if target < -0.5 else 0)
    px, now = float(h.close.iloc[-1]), dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    st = json.load(open(STATE)) if os.path.exists(STATE) else {"pos": 0, "trades": [], "pnl_usd": 0.0}
    print(f"{now} BTC {px:,.0f} target {target} current {st['pos']}")
    if target == st["pos"]:
        return
    if st["pos"] != 0:   # close the open paper trade
        e = st["entry"]; r = st["pos"] * (px - e) / e - 2 * bt.COST
        usd = round(r * NOTIONAL, 2); st["pnl_usd"] = round(st["pnl_usd"] + usd, 2)
        st["trades"].append({"side": "LONG" if st["pos"] > 0 else "SHORT", "entry": e,
                             "exit": px, "open": st["opened"], "close": now, "ret_pct": round(100 * r, 2), "usd": usd})
        send(f"{'✅' if usd > 0 else '❌'} BTC paper EXIT {px:,.0f}\n{100*r:+.2f}% (${usd:+.2f} on ${NOTIONAL:.0f})\n"
             f"Total: ${st['pnl_usd']:+.2f} · {len(st['trades'])} trades")
    st["pos"] = target
    if target != 0:
        st["entry"], st["opened"] = px, now
        send(f"🟢 BTC paper {'LONG' if target > 0 else 'SHORT'} {px:,.0f}\nRule: {STRATEGY}\nExit when the rule flips (checked daily)")
    else:
        st.pop("entry", None); st.pop("opened", None)
    os.makedirs("data", exist_ok=True)
    json.dump(st, open(STATE, "w"), indent=1)

if __name__ == "__main__":
    main()
