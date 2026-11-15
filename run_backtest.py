"""Backtest of the combined strategy -> data/backtest_report.xlsx

  python run_backtest.py          # last 1 year  (Upstox)
  python run_backtest.py 2        # last 2 years
  python run_backtest.py demo     # demo data, no Upstox
"""
import sys
from datetime import date, timedelta

from raking import backtest, config, data, demo
from raking.upstox import Upstox


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else "1"
    end = date.today() - timedelta(days=1)
    if arg == "demo":
        start = end - timedelta(days=365)
        days = {i: demo.backtest_days(i, start, end) for i in config.INDICES}
        mode, path = "DEMO (simulated data)", config.DATA_DIR / "demo_backtest_report.xlsx"
    else:
        years = float(arg)
        start = end - timedelta(days=int(365 * years))
        api = Upstox()
        api.min_gap = 1.0      # gentle on Upstox rate limits during long downloads
        print("Upstox Plus check...", "available" if api.has_plus() else "NOT available (price-only mode)")
        days, modes = {}, set()
        for idx in config.INDICES:
            d, m = data.backtest_days(api, idx, start, end, progress=print)
            days[idx] = d
            modes.add(m)
        mode = "full (with option OI)" if modes == {"full"} else "price-only (without OI checks)"
        path = config.DATA_DIR / "backtest_report.xlsx"
    print("Running backtest...")
    t = backtest.run(days)
    summ = backtest.summary(t, days)
    backtest.save_excel(t, summ, path, mode)
    print("\nMode:", mode)
    print(summ.to_string(index=False))
    print("\nReport saved:", path)


if __name__ == "__main__":
    main()
