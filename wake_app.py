"""Open the Streamlit dashboard in a headless browser and press its wake-up button if it is asleep.
No secrets are used. Never fails the caller."""
import sys

URL = "https://raking-the-trader.streamlit.app/"
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        pg.goto(URL, timeout=90000)
        pg.wait_for_timeout(8000)
        btn = pg.get_by_role("button", name="Yes, get this app back up!")
        if btn.count():
            btn.first.click()
            print("Dashboard was asleep - wake-up pressed.")
            pg.wait_for_timeout(60000)
        else:
            print("Dashboard awake.")
        b.close()
except Exception as e:  # waking the page must never stop the scanner
    print("Wake check skipped:", e)
sys.exit(0)
