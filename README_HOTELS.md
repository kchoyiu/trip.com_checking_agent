# Trip.com hotel collector

This is a normal Playwright collector for a city such as 高雄 or 東京. It does not use playwright-stealth, proxies, CAPTCHA solvers, Scrapingant/Scrapfly bypass services, or code that tries to defeat Akamai/WhaleGuard. A 403, verification page, or anti-bot marker is saved to artifacts/hotels/ and stops the run.

## Run

From this folder, activate the existing environment:

    .\.venv\Scripts\Activate.ps1

Then:

    python hotel_scraper.py --city 高雄 --check-in 2026-10-04 --check-out 2026-10-07 --output data.csv
    python hotel_scraper.py --city 東京 --check-in 2026-10-04 --check-out 2026-10-07 --output data.csv --headful

The script starts at Trip.com homepage, enters the city in the visible hotel search control, selects a suggestion when available, chooses the requested check-in/check-out dates through the normal date picker, and waits for results. The default dates are 2026-10-04 through 2026-10-07. It uses short randomized pacing and one scroll before and after search for normal interaction; these delays are not an anti-bot bypass.

If the live page changes its markup, the script saves evidence and reports that selectors need manual verification. It never auto-modifies itself or retries indefinitely.

## Test

    python -m pytest -q tests/test_hotel_scraper.py

The test uses local HTML and does not access Trip.com. Successful live output is data.csv with hotel name, price, currency, rating, score, source URL, and capture time.

## Ranking and price-drop alerts

Each result receives a score from 0–100: hotel rating contributes up to 70 points and relative low price contributes up to 30 points. The first row in `data.csv` is the highest-scoring hotel. Prices are saved in `data/hotels.db`; on later runs, if the current top-scoring hotel's price falls by at least `HOTEL_ALERT_DROP_PERCENTAGE` (default 10%), the script sends one Telegram notification and applies a 24-hour cooldown by default.

Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HOTEL_ALERT_DROP_PERCENTAGE`, and `HOTEL_ALERT_COOLDOWN_HOURS` in `.env`. Without Telegram credentials, the price history is still saved and the notification is logged as not configured.

The first successful run only creates the baseline price and does not send an alert. A Telegram alert requires a later run where the current highest-scoring hotel's price is at least the configured percentage lower than its previous price. The script loads `.env` automatically.
