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
    t2 = backtest.run(days, pullback=True)
    n_days = len({d for m in days.values() for d in m})
    summ = backtest.summary(t, days)
    comp = backtest.compare(t, t2, n_days)
    backtest.save_excel(t2, summ, path, mode, comp)
    print("\nMode:", mode)
    print(summ.to_string(index=False))
    print("\nPULLBACK TEST (go live only if 'Upgraded' total_r and profit_factor beat 'Current'):")
    print(comp[["system", "trades", "win_rate_pct", "total_r", "avg_r", "profit_factor", "max_drawdown_r"]].to_string(index=False))
    print("\nReport saved:", path)
    txt = (f"Backtest {start} to {end} - mode: {mode}\n\n{summ.to_string(index=False)}\n\nPULLBACK TEST\n"
           + comp.to_string(index=False) + "\n")
    (config.DATA_DIR / ("demo_backtest_summary.txt" if arg == "demo" else "backtest_summary.txt")).write_text(
        txt, encoding="utf-8")


if __name__ == "__main__":
    main()
