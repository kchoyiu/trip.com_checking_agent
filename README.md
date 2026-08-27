# Free Trip.com Flight Agent V1

Windows-local Python + Playwright Chromium + SQLite + Telegram monitor. It does not use OpenAI, book flights, pay, bypass CAPTCHA, or bypass anti-bot controls.

## Install

1. Install Python 3.11+ and open PowerShell in this folder.
2. Create an environment: `py -m venv .venv` then `.venv\Scripts\Activate.ps1`.
3. Install packages: `py -m pip install -r requirements.txt`.
4. Install Chromium: `playwright install chromium`.
5. Copy `.env.example` to `.env`, create a Telegram bot with BotFather, and set token/chat ID.
6. Edit `config.yaml`. For a multi-city itinerary, use explicit one-way `legs`, for example:

       legs:
         - origin: HKG
           destination: KHH
           depart_date: "2026-10-04"
         - origin: TPE
           destination: HKG
           depart_date: "2026-10-11"
       adults: 1
       currency: HKD
       airline: "Cathay Pacific"

## Run

`py main.py`

The first run creates `data/flights.db`; `run_agent.bat` logs to `logs/agent.log`; failures save screenshots/HTML in `artifacts/`. Add `run_agent.bat` to Windows Task Scheduler, ideally every 6–12 hours.

## Docker

1. Copy `.env.example` to `.env` and fill in a newly generated Telegram bot token and chat ID.
2. Start the scheduled container with `docker compose up -d --build`.
3. View logs with `docker compose logs -f`.

The Docker service checks once every hour by default and restarts if it exits unexpectedly. Set `CHECK_INTERVAL_HOURS` in `.env` to change the interval. For a single run, use `docker compose run --rm -e RUN_ONCE=1 trip-com-checking-agent`.

Never commit `.env`; it is ignored by Git and Docker.

### Optional manual Chrome connection for flight checks

When the headless container receives a Trip.com verification page, you can let it read a user-launched Chrome session after you complete the verification manually. This is a user-authorized browser connection, not an anti-bot bypass.

On macOS, start a separate Chrome profile with remote debugging enabled:

    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222 --user-data-dir="/Users/joekwan/Library/Application Support/TripAgentChrome"

Open the Trip.com flight page in that window and complete any verification yourself. Then add the following to `.env` and restart the flight service:

    TRIP_CDP_URL=http://host.docker.internal:9222
    TRIP_REUSE_CURRENT_PAGE=true
    TRIP_NAVIGATE_TO_JOB=false
    TRIP_MANUAL_VERIFY_TIMEOUT_SECONDS=180

Keep `TRIP_NAVIGATE_TO_JOB=false` when you have already opened the exact Trip.com result page manually; this preserves the user-visible page. For multiple explicit legs, open one matching result tab per leg in that same CDP Chrome profile; the agent selects tabs by the `dcity`/`acity` route in the URL. Set it to `true` only when your Trip.com deployment accepts the generated date/route query parameters. The agent waits up to 180 seconds for manual verification, then saves the page evidence and stops safely if the verification remains. It never solves or bypasses the verification automatically.

## Tests

Run `py -m pytest -q`. Tests are offline and cover date generation and deal scoring. A live Trip.com run is intentionally not automated.

## Selector note

Trip.com markup, locale, consent dialogs, and anti-bot behavior can change. `flight_agent/scraper/selectors.py` is the single selector registry and must be manually verified against the current page before production use. If CAPTCHA or an anti-bot marker is detected, the run stops and saves evidence; it never attempts a bypass.

If the saved page contains `whaleguard block` or another security interstitial, this is an upstream/network block rather than a selector timeout. The agent now stops immediately and records the page in `artifacts/`; it does not attempt to circumvent the block.

## Database and queue

`search_jobs` queues unique date searches; `flight_prices` stores every observation; `notifications` provides cooldown/dedup history. Each run limits the currently configured jobs and orders by priority, then oldest/never-checked jobs. Explicit `legs` searches are one-way; the legacy `origin`/`destinations`/`departure` format remains supported for generated round trips. Set `airline` (or `airlines`) to filter results, with `Cathay Pacific`, `國泰航空`, and `CX` treated as equivalent aliases.

For exact hotels, set `HOTEL_TARGET_DETAILS=city|hotel_name|Trip.com_detail_url;...` in `.env`. This is useful when a hotel is not among the first featured cards on its city page. It takes priority over `HOTEL_TARGETS` and writes one CSV per detail URL under `data/`. The desktop URL form `https://hk.trip.com/hotels/detail/?cityEnName=Kaohsiung&cityId=720&hotelId=7932167` can be used as a detail URL. `HOTEL_HEADFUL=true` is for a visible Windows run only; Docker normally remains headless.
