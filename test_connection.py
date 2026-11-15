"""Connection test: Telegram + Upstox. Run by the "Test connection" GitHub workflow or by hand."""
import sys
from datetime import date, timedelta

from raking import config, notify
from raking.upstox import Upstox


def main() -> int:
    ok = True
    lines = [f"🔧 <b>{config.APP_NAME}</b> connection test"]
    if not config.upstox_token():
        lines.append("❌ Upstox: no token found (UPSTOX_ANALYTICS_TOKEN secret is empty)")
        ok = False
    else:
        try:
            api = Upstox()
            c = config.INDICES["NIFTY"]
            df = api.historical(c["spot_key"], date.today() - timedelta(days=7), date.today(), 5)
            fut = api.near_future("NIFTY", date.today())
            lines.append(f"✅ Upstox: {len(df)} Nifty 5-min candles from the last 7 days, near future {fut.get('trading_symbol')}")
            sx = api.near_future("SENSEX", date.today())
            stocks = api.fno_stock_futures(date.today())
            q = api.quotes(list(stocks["instrument_key"]))
            lines.append(f"✅ Sensex future {sx.get('trading_symbol')} | {len(stocks)} F&O stocks | {len(q)} live quotes")
        except Exception as e:  # report every kind of failure
            lines.append(f"❌ Upstox: {e}")
            ok = False
    text = "\n".join(lines)
    print(text)
    if notify.send(text):
        print("✅ Telegram: message sent")
    else:
        print("❌ Telegram: message NOT sent - check TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID and press Start in your bot")
        ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
