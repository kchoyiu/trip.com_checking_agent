# Free Trip.com Flight Agent V1

Windows-local Python + Playwright Chromium + SQLite + Telegram monitor. It does not use OpenAI, book flights, pay, bypass CAPTCHA, or bypass anti-bot controls.

## Install

1. Install Python 3.11+ and open PowerShell in this folder.
2. Create an environment: `py -m venv .venv` then `.venv\Scripts\Activate.ps1`.
3. Install packages: `py -m pip install -r requirements.txt`.
4. Install Chromium: `playwright install chromium`.
5. Copy `.env.example` to `.env`, create a Telegram bot with BotFather, and set token/chat ID.
6. Edit `config.yaml`.

## Run

`py main.py`

The first run creates `data/flights.db`; `run_agent.bat` logs to `logs/agent.log`; failures save screenshots/HTML in `artifacts/`. Add `run_agent.bat` to Windows Task Scheduler, ideally every 6–12 hours.

## Tests

Run `py -m pytest -q`. Tests are offline and cover date generation and deal scoring. A live Trip.com run is intentionally not automated.

## Selector note

Trip.com markup, locale, consent dialogs, and anti-bot behavior can change. `flight_agent/scraper/selectors.py` is the single selector registry and must be manually verified against the current page before production use. If CAPTCHA or an anti-bot marker is detected, the run stops and saves evidence; it never attempts a bypass.

If the saved page contains `whaleguard block` or another security interstitial, this is an upstream/network block rather than a selector timeout. The agent now stops immediately and records the page in `artifacts/`; it does not attempt to circumvent the block.

## Database and queue

`search_jobs` queues unique date searches; `flight_prices` stores every observation; `notifications` provides cooldown/dedup history. Each run limits jobs and orders by priority, then oldest/never-checked jobs.
