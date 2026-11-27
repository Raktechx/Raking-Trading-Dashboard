# 📈 Raking the Trader — Combined Strategy

**Instruments:** Nifty 50, Bank Nifty and Sensex index options, plus options of **every NSE F&O stock**, 5-minute candles.
Price, VWAP, EMA and volume come from the near-month **future**, because the index itself has no volume. The ATM strike comes from the spot index.

## Rules

### BUY CE — all conditions together
1. **Trend:** price is above VWAP, and EMA20 is above EMA50.
2. **OI:** the ATM call shows long build-up, meaning premium and OI are both up compared with 3 candles earlier. At the same time, puts are being written at ATM and the 2 strikes below (put OI up, premium down).
3. **Entry:** after 9:30, the 9:15–9:30 range high breaks with volume above the average of the previous 20 candles.
4. **Stop loss:** the previous candle's low.
5. **Target:** 1:2 reward:risk.
6. **Limits:** max 2 trades a day, no new entry after 3:00 PM, and an open trade is closed at 3:15 PM.

### BUY PE
The mirror image: price below VWAP, EMA20 below EMA50, long build-up in the ATM put plus call writing at ATM and the 2 strikes above, the range low breaks with volume, and the SL is the previous candle's high.

### Stock options (automatic, all F&O stocks)
Every scan checks **all** F&O stocks that are past their 9:15–9:30 range on the right side of VWAP (no fixed 25/50 cap; a 150-second time budget per scan keeps Upstox calls within limits). A stock signal is sent only if it passes every rule above **and**:
- grade **A = quality score 80+** (lower grades are logged, never alerted; the Stocks tab lists only grade A signals),
- the **Nifty trend** agrees with the trade direction,
- the stop-loss is **0.35%–0.8%** from entry (too tight gets hit by noise, too wide risks too much),
- entry before **15:00**. Stock strikes come from the real option list, so uneven strike gaps are handled.

### Index options
Only grade **A (score 80+)** index signals are sent.

### MCX Crude Oil (alerts 19:30–23:15 IST)
Updated 27 Nov 2026 after testing 70 rule variants on recent CRUDEOIL 5-min data (train on older 2/3, then check on the newest 1/3 it never saw).
1. **Range:** high and low of the US-open half hour, **19:30–20:00 IST**.
2. **Entry:** a 5-min candle closes beyond the range with volume above the 20-candle average, price on the right side of VWAP.
3. **Trend filter (new):** EMA20 vs EMA50 must agree on **both 5-min and 15-min** candles. This was the best version: about the same total profit as before with fewer trades (42 vs 56), a higher win rate (40% vs 34%), smaller drawdown, and better results on the unseen period (+15R vs +11R).
4. **SL:** 1 ATR. After +1R the SL trails EMA20 (let winners run). Exit by **23:15**.
5. Option alerted: ATM Crude Oil Mini CE/PE.

Tested and **rejected** (lost money on the unseen period): waiting for a retest, stopping entries at 21:00, skipping Wednesdays (EIA report day), booking half at 1.5R, trading only in the direction of the first half hour. The EIA-day momentum trade looked good but had only 4–10 trades, too few to trust; it is being watched, not traded.

⚠️ Upstox free data gave about 3.5 months of crude history (expired contracts need Upstox Plus), so these results are early and will be re-checked as paper trades build up.

Note: Sensex futures trade thinly, so its volume check is less reliable than Nifty's and Bank Nifty's.

### Two extra safety rules
You can change these in `raking/config.py`:
- No entry if the range broke more than 3 candles ago (no late chasing).
- The trade is skipped if the SL is wider than 2 ATR.

Once a signal appears, its entry, SL and target are **fixed**. Every signal and its result goes to `data/signal_log.xlsx`.

---

## ▶️ Run it on your laptop (one click)
1. Install **Python 3.11+** from python.org. During install, tick **"Add python.exe to PATH"**.
2. Double-click **`Start Dashboard.bat`**. The first start takes 2–3 minutes, then your browser opens automatically.
3. In **Settings**, paste your **Upstox Analytics Token** and press **Save**. See the token section below.
4. Market closed? Turn on **🧪 Demo mode** to see the dashboard working on simulated data.

| Tab | What it shows |
|---|---|
| 🔴 Combined Strategy (Live) | New signals with CE/PE, strike, entry, SL, target and time, at both premium and future levels. With no signal, a live checklist of every condition (● met / ○ not yet). |
| 🧪 Backtest | Win rate, average profit, biggest loss, max drawdown, monthly results and an Excel report. |
| 📒 Paper Trading Log | Every live signal and result, a monthly scorecard and an Excel download. |
| 📘 Rules | All the conditions. |

Other files:
- `Run Backtest.bat` runs a 1-year backtest and opens the Excel report.
- `Run Tests.bat` runs the code checks.

---

## 🔑 Upstox token — use the 1-year Analytics Token
A normal Upstox access token expires every day at 3:30 AM. That would stop the automatic scans every morning.
Upstox's **Analytics Token** is read-only, covers market data (candles, option chain, quotes), and is valid for **1 year**.

1. Log in to the **Upstox Developer Apps** page.
2. Generate the **Analytics Token**.
3. Put it in the dashboard Settings and in the GitHub secret `UPSTOX_ANALYTICS_TOKEN`.

The daily access token still works as a fallback.

---

## ☁️ Always live: GitHub Actions + Telegram (laptop can stay off)
Every 5 minutes on weekdays, GitHub Actions:
- scans Nifty, Bank Nifty, Sensex and all F&O stock options from 9:15 to 3:30;
- sends **Telegram** alerts for new signals and for results (target / SL / time exit);
- sends a **"scanner running"** message on the first scan of the day, or an **error alert** if the token or data fails (once a day);
- sends an **end-of-day summary** after 3:30 PM, with your running paper-trading record, and a **weekly report** every Friday;
- reminds you **15 days before** the Upstox token expires (renew once a year);
- rebuilds a **live page**: `LIVE.md` and `docs/index.html`.

### 1. Telegram bot (free)
1. In Telegram, open **@BotFather**, send `/newbot`, and copy the bot token.
2. Send any message to your new bot, then get your chat id from **@userinfobot**.
3. Test it from the dashboard: Settings → **Telegram test message**.

### 2. GitHub repo
1. Create a repo on github.com and upload this whole folder. Leave out `.env`, `.venv` and `data/cache`.
2. Go to **Settings → Secrets and variables → Actions** and add:
   - `UPSTOX_ANALYTICS_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - optional: `UPSTOX_ACCESS_TOKEN`
3. Click **Add file → Create new file**, name it `.github/workflows/scan.yml`, paste the content of `setup/github-actions-scan.yml`, and **Commit**.
4. Open the **Actions** tab, enable workflows, then press **Run workflow** once to test.

### 3. Watching the live page on your phone
- **Private repo (free):** open `LIVE.md` in the GitHub app or website. It is updated on every scan.
- **Web page:** go to **Settings → Pages → Deploy from branch → `main` / `/docs`**. You get a link to `docs/index.html` that refreshes every 60 seconds. On a free GitHub account, Pages only works for **public** repos. A public repo means anyone can see your code and signals.

### Things to know
- **Timing:** GitHub's schedule can run 5–15 minutes late at busy times.
- **Free minutes:** a private repo gets 2,000 free Actions minutes a month. This workflow uses about 1,700 (one short run every 5 minutes), so keep other workflows off. Public repos have no limit.
- **Duplicate alerts:** while GitHub Actions is running, turn off "Send Telegram alerts from this dashboard too" in the dashboard. Otherwise every alert arrives twice.

---

## ⚠️ Backtest data — please read
- The strategy needs **option OI** (ATM build-up and writing). 5-minute data for **expired** options and futures is only available with **Upstox Plus** (paid).
  - **With Plus:** the dashboard detects it and runs the full backtest with option OI.
  - **Without Plus:** the backtest runs **price-only** (no OI checks) and only over about 2–3 months of currently listed futures. That is **not** a full test of the strategy.
- **Download time:** the first 1-year download can take 30–60 minutes because of Upstox rate limits. The data is cached in `data/cache`, so later runs are instant.
- **Costs:** 0.10R is deducted from every trade for brokerage and slippage.
- **Same-candle rule:** if SL and target are both touched in the same candle, SL is assumed.
- **Premium levels:** premium SL and target are estimates (future move × delta). The future levels are exact.

## ✅ Testing plan
1. A 1–2 year backtest.
2. 2–3 months of paper trading, recorded automatically in the Paper Trading Log, `LIVE.md` and Telegram.
3. Subscriptions only if that record is good, and only after SEBI Research Analyst registration.

⚠️ This is a research / paper-trading tool, not investment advice.
