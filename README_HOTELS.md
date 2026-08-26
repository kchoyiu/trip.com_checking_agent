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

Set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `HOTEL_ALERT_DROP_PERCENTAGE`, and `HOTEL_ALERT_COOLDOWN_HOURS` in `.env`. Set `HOTEL_NOTIFY_CURRENT_PRICE=true` to send the current price after every successful scan even when there is no drop. With selected hotels, the message includes all selected hotels; without a watchlist, it includes the highest-scoring hotel. Without Telegram credentials, the price history is still saved and the notification is logged as not configured.

The first successful run only creates the baseline price and does not send an alert. A Telegram alert requires a later run where the current highest-scoring hotel's price is at least the configured percentage lower than its previous price. The script loads `.env` automatically.

To monitor only selected hotels, set `HOTEL_TARGET_NAMES` to a comma-separated list of hotel names or distinctive name fragments, for example `HOTEL_TARGET_NAMES=高雄喜達絲飯店,鈞怡大飯店`. The scraper scrolls a bounded amount to find lazy-loaded cards, filters the CSV and ranking to the selected hotels, and alerts only for the highest-scoring selected hotel. If a requested hotel is not found, the run stops and saves evidence under `artifacts/hotels/`.

For selected hotels in different cities, use `HOTEL_TARGETS` instead. Each semicolon-separated entry is `city|Trip.com city ID|Trip.com city slug|hotel1,hotel2`, for example `HOTEL_TARGETS=高雄|720|kaohsiung|喜迎旅店;新北|7662|new-taipei-city|傑仕堡有氧;台北|617|taipei|路徒PLUS行旅-主題館`. The Docker scheduler checks each city group in sequence. Each group writes to `data/<city-slug>.csv` and `artifacts/hotels/<city-slug>/`, while sharing the SQLite history. Name matching tolerates whitespace, punctuation, the 酒店/飯店 wording difference, and a city prefix; use distinctive full names or fragments because generic fragments can match more than one hotel.

City pages are not a complete hotel directory: they can expose only featured or currently loaded cards. For an exact watchlist, use `HOTEL_TARGET_DETAILS` instead. Each semicolon-separated entry is `city|hotel_name|Trip.com detail URL`; this mode opens that hotel's official detail page and adds the configured dates, currency, adults, and children as ordinary Trip.com search parameters. For the current three hotels, the setting is:

    HOTEL_TARGET_DETAILS=高雄|喜迎旅店|https://hk.trip.com/hotels/detail/?cityEnName=Kaohsiung&cityId=720&hotelId=7932167;新北|傑仕堡有氧飯店|https://hk.trip.com/hotels/detail/?cityEnName=New%20Taipei%20City&cityId=7662&hotelId=63341173;台北|路徒PLUS行旅-主題館|https://hk.trip.com/hotels/detail/?cityEnName=Taipei&cityId=617&hotelId=78247238

When `HOTEL_TARGET_DETAILS` is set, it takes priority over `HOTEL_TARGETS`; clear or comment out the old `HOTEL_TARGETS` line to avoid confusion. The scheduler writes one CSV per detail URL using the URL's final slug, and stores all observations in the same SQLite history. If the detail page has no dated room price, the run stops with an evidence snapshot; it does not substitute a generic city-list price or bypass an anti-bot response.

The `hk.trip.com/hotels/detail/?cityId=...&hotelId=...` form shown above matches the desktop page URL format. If normal Chrome displays an offer card but a headless run receives HTTP 430, set `HOTEL_HEADFUL=true` only for a Windows visible-browser run; Docker containers normally have no desktop display and should remain headless.

If a normal Chrome session can see the page but a new Playwright window is redirected to sign-in, use the optional Windows CDP mode. Start a separate Chrome profile with remote debugging, sign in or open the Trip.com page manually, and set `HOTEL_CDP_URL=http://127.0.0.1:9222` plus `HOTEL_REUSE_CURRENT_PAGE=true`. The scraper then reads that user-launched Chrome context and leaves Chrome open when finished. This is not a CAPTCHA or anti-bot bypass: verification pages, HTTP 430, and other blocks still stop the run.

Example PowerShell launch (close the dedicated profile after use):

    $chrome = "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
    & $chrome --remote-debugging-port=9222 --user-data-dir="$env:TEMP\trip-agent-chrome"

Keep the CDP Chrome window open while the scraper runs. Docker cannot use this Windows browser connection; use this mode with the local Windows Python command.

For a single local test cycle across all exact targets, run `python docker_scheduler.py --once`; it exits after the targets finish instead of waiting for the next interval.

## Docker Desktop on Windows

Docker is the recommended local option when the computer should use an isolated Python and Chromium environment. The container scans once immediately, then stays alive and scans every `HOTEL_INTERVAL_HOURS` hours (default 6).

1. Install and start Docker Desktop using Linux containers.
2. In PowerShell, from this project folder, create the local environment file:

       Copy-Item .env.example .env

   Edit `.env` and use a newly rotated Telegram bot token. Do not commit `.env`.
3. Build the image once:

       docker compose build

4. Start the always-on scanner:

       .\run_hotel_docker.bat

   Results are written to `data/data.csv`, history to `data/hotels.db`, and screenshots/HTML evidence to `artifacts/hotels/`. The Compose service uses `restart: unless-stopped`, so Docker Desktop starts it again after Docker restarts, provided it was started with `docker compose up -d` and was not manually stopped.
5. In Docker Desktop Settings, enable “Start Docker Desktop when you sign in”. To inspect the running scanner:

       docker compose ps
       docker compose logs -f hotel-scraper

   To stop it, use `docker compose stop hotel-scraper`; start it again with `docker compose up -d hotel-scraper`.
6. To run a one-off Telegram test without starting a scan:

       docker compose run --rm --no-deps hotel-scraper python hotel_scraper.py --test-telegram

7. Windows Task Scheduler is optional if Docker Desktop is configured to start at sign-in. If you prefer Task Scheduler, create a task that runs:

       `C:\path\to\flight-agent\run_hotel_docker.bat`

   Set it to run whether the user is logged on or not, and enable “Run task as soon as possible after a scheduled start is missed”. The task can only run while Docker Desktop and Windows are available.

The Compose file defaults to 高雄, 2026-10-04 through 2026-10-07. Change `HOTEL_CITY`, `HOTEL_CHECK_IN`, and `HOTEL_CHECK_OUT` in `.env` to change the search without editing the Compose file. If Trip.com does not expose a usable suggestion, set the matching `HOTEL_CITY_ID` explicitly (Kaohsiung is `720`). For a city not built into the scraper, also set its Trip.com slug in `HOTEL_CITY_SLUG`; the scraper then uses Trip.com's normal city-page search form. CAPTCHA/anti-bot pages still stop the run; Docker does not bypass them.
