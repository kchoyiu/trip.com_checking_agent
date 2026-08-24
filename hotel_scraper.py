"""Compliant Trip.com hotel list collector.
Uses ordinary Playwright only. It does not use stealth plugins, proxies,
CAPTCHA solvers, or anti-bot bypass. Block/verification pages are saved.
"""
from __future__ import annotations
import argparse, asyncio, csv, logging, os, random, re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright
from hotel_engine import price_value, rank_hotels
from hotel_history import HotelHistory
from flight_agent.notification.hotel_telegram import notify_top_drop
from flight_agent.notification.telegram import TelegramNotifier

LOG = logging.getLogger("hotel_scraper")
BLOCK_MARKERS = ("captcha", "verify you are human", "robot check", "access denied",
                 "whaleguard", "akamai", "anti-bot", "unusual traffic")
CITY_INPUTS = ("#destinationInput", "input[placeholder*='city' i]", "input[placeholder*='城市' i]",
               "input[placeholder*='目的地' i]", "input[name*='city' i]",
               "input[aria-label*='city' i]", "input[aria-label*='目的地' i]",
               "input.gccpoi__TripSearchBox-input")
SEARCH_BUTTONS = ("button:has-text('Search')", "button:has-text('搜尋')",
                  "button:has-text('搜索')", "[role='button']:has-text('Search')",
                  "button:has(i.ic_search)", ".gccpoi__TripSearchBox-btn")
HOTEL_CARDS = ("div.hotel-card", "[data-testid*='hotel' i]", "[class*='hotel-card' i]",
               "[class*='hotelCard' i]", "[class*='HotelCard' i]",
               "[class*='hotel-item' i]")
NAME_SELECTORS = ("a.hotelName", "[data-testid*='hotel-name' i]",
                  "[class*='hotel-name' i]", "[class*='hotelName' i]", "h2", "h3")
PRICE_SELECTORS = (".sale", "[data-testid*='price' i]", "[class*='price' i]",
                   "[class*='Price' i]", "[class*='amount' i]")
RATING_SELECTORS = (".comment-score", "[aria-label*='得' i]", "[aria-label*='score' i]")

@dataclass(frozen=True)
class Hotel:
    hotel_name: str
    price: str
    currency: str
    rating: float | None
    score: float
    source_url: str
    captured_at: str

    @property
    def price_value(self):
        return price_value(self.price)

class BlockedPageError(RuntimeError): pass
class SelectorError(RuntimeError): pass

def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()

def _price_text(value: str) -> str:
    value = _clean(value)
    match = re.search(r"(?:HK\$|US\$|¥|￥|€|£|[$€£¥]|[A-Z]{3})?\s?[\d,]+(?:\.\d{1,2})?", value)
    return match.group(0).strip() if match else value

async def detect_block(page: Page) -> str | None:
    body = (await page.locator("body").inner_text(timeout=5000)).lower()
    haystack = " ".join((body, page.url.lower(), (await page.title()).lower()))
    return next((marker for marker in BLOCK_MARKERS if marker in haystack), None)

async def save_evidence(page: Page, artifacts: Path, label: str):
    artifacts.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot, html = artifacts / f"{stamp}_{label}.png", artifacts / f"{stamp}_{label}.html"
    await page.screenshot(path=str(screenshot), full_page=True)
    html.write_text(await page.content(), encoding="utf-8")
    return screenshot, html

async def first_visible(page: Page, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if await locator.is_visible(timeout=1000):
                return locator
        except PlaywrightTimeoutError:
            continue
    return None

async def human_paced_scroll(page: Page) -> None:
    # Pacing only; not intended to evade detection.
    await page.mouse.move(random.randint(100, 700), random.randint(100, 500))
    await page.mouse.wheel(0, random.randint(350, 750))
    await page.wait_for_timeout(random.randint(800, 1600))

async def set_date_range(page: Page, check_in: date, check_out: date, timeout_ms: int) -> None:
    """Choose dates through Trip.com's normal date picker UI."""
    await page.locator("#checkInInput").click(timeout=timeout_ms)
    target = check_in
    for _ in range(24):
        heading = page.locator(".c-calendar-month__title h2").first
        text = await heading.inner_text(timeout=timeout_ms)
        match = re.search(r"(\d{4}).*?(\d{1,2})", text)
        if not match:
            raise SelectorError("Could not read the current month from Trip.com's date picker.")
        current = (int(match.group(1)), int(match.group(2)))
        wanted = (target.year, target.month)
        if current == wanted:
            break
        if current < wanted:
            await page.get_by_role("button", name="前往下個月").click()
        else:
            await page.get_by_role("button", name="前往上個月").click()
        await page.wait_for_timeout(150)
    else:
        raise SelectorError(f"Could not navigate date picker to {target:%Y-%m}.")

    async def choose(day: date):
        label = f"{day.year} 年 {day.month} 月 {day.day} 日"
        cell = page.get_by_role("gridcell", name=re.compile(re.escape(label))).first
        await cell.click(timeout=timeout_ms)

    await choose(check_in)
    await page.wait_for_timeout(250)
    await choose(check_out)

async def search_city(page: Page, city: str, check_in: date, check_out: date, timeout_ms: int) -> None:
    input_box = await first_visible(page, CITY_INPUTS)
    if input_box is None:
        raise SelectorError("Could not find the hotel city input on Trip.com homepage.")
    await input_box.fill(city)
    await page.wait_for_timeout(random.randint(500, 1200))
    # Do not treat the input's own value as a suggestion; only click an actual
    # dropdown option when the page exposes one.
    suggestion = None
    for _ in range(10):
        suggestion = await first_visible(page, ("[role='option']", "[class*='suggestion' i]",
                                                 "[class*='N7ro' i]"))
        if suggestion is not None:
            break
        await page.wait_for_timeout(500)
    if suggestion is not None:
        await suggestion.click()
    else:
        await input_box.press("Enter")
    await set_date_range(page, check_in, check_out, timeout_ms)
    await page.wait_for_timeout(random.randint(1000, 2000))
    button = await first_visible(page, ("button:has(i.ic_search)",) + SEARCH_BUTTONS)
    if button is not None:
        await button.click()
    else:
        await input_box.press("Enter")
    try:
        await page.wait_for_url("**/hotels/list**", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)

async def collect_hotels(page: Page, city: str, artifacts: Path, timeout_ms: int) -> list[Hotel]:
    marker = await detect_block(page)
    if marker:
        await save_evidence(page, artifacts, "blocked")
        raise BlockedPageError(f"Trip.com returned a verification/anti-bot page ({marker}).")
    cards = None
    for selector in HOTEL_CARDS:
        candidate = page.locator(selector)
        try:
            await candidate.first.wait_for(state="visible", timeout=timeout_ms)
        except PlaywrightTimeoutError:
            continue
        if await candidate.count():
            cards = candidate
            break
    if cards is None:
        await save_evidence(page, artifacts, "selector_error")
        raise SelectorError("No hotel cards found; live selectors need verification.")
    now = datetime.now(timezone.utc).isoformat()
    output = []
    for index in range(min(await cards.count(), 100)):
        card = cards.nth(index)
        name = None
        for selector in NAME_SELECTORS:
            node = card.locator(selector).first
            if await node.count():
                text = _clean(await node.inner_text())
                if text:
                    name = text
                    break
        price = ""
        for selector in PRICE_SELECTORS:
            node = card.locator(selector).first
            if await node.count():
                price = _price_text(await node.inner_text())
                if price:
                    break
        rating = None
        for selector in RATING_SELECTORS:
            node = card.locator(selector).first
            if await node.count():
                rating_text = " ".join(filter(None, [
                    await node.get_attribute("aria-label"),
                    await node.inner_text(),
                ]))
                match = re.search(r"(?:得|score|rating)[^0-9]*([0-9]+(?:\.[0-9]+)?)", rating_text, re.I)
                if match:
                    rating = float(match.group(1))
                    break
        if name and price:
            currency_match = re.match(r"([A-Z]{3}|HK\$|US\$|[$€£¥])", price)
            currency = currency_match.group(1) if currency_match else ""
            output.append(Hotel(name, price, currency, rating, 0.0, page.url, now))
    if not output:
        await save_evidence(page, artifacts, "parse_error")
        raise SelectorError("Hotel cards found but no name/price pairs were parsed.")
    return output

def write_csv(rows: list[Hotel], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=("hotel_name", "price", "currency", "rating", "score", "source_url", "captured_at"))
        writer.writeheader()
        writer.writerows(row.__dict__ for row in rows)

async def run(city: str, check_in: date, check_out: date, output: Path, artifacts: Path,
              headless: bool, timeout_ms: int, history_path: str = "data/hotels.db") -> int:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        page = await browser.new_page(locale="zh-HK")
        blocked_responses = []
        page.on("response", lambda response: blocked_responses.append(response)
                if response.request.resource_type == "document" and response.status in (403, 429)
                else None)
        try:
            response = await page.goto("https://www.trip.com/", wait_until="domcontentloaded", timeout=timeout_ms)
            if response and response.status in (403, 429):
                await save_evidence(page, artifacts, f"http_{response.status}")
                raise BlockedPageError(f"Trip.com returned HTTP {response.status}; stopped without bypass.")
            marker = await detect_block(page)
            if marker:
                await save_evidence(page, artifacts, "blocked")
                raise BlockedPageError(f"Trip.com returned a verification/anti-bot page ({marker}).")
            await human_paced_scroll(page)
            await search_city(page, city, check_in, check_out, timeout_ms)
            if "/hotels/list" in page.url and "city=0" in page.url:
                await save_evidence(page, artifacts, "city_not_resolved")
                raise SelectorError("Trip.com did not resolve the city suggestion; refusing to scrape an unfiltered list.")
            if blocked_responses:
                status = blocked_responses[-1].status
                await save_evidence(page, artifacts, f"http_{status}")
                raise BlockedPageError(f"Trip.com returned HTTP {status}; stopped without bypass.")
            await human_paced_scroll(page)
            hotels = rank_hotels(await collect_hotels(page, city, artifacts, timeout_ms))
            write_csv(hotels, output)
            history = HotelHistory(history_path)
            previous = history.previous_prices(city, [row.hotel_name for row in hotels])
            history.add(city, hotels)
            top = hotels[0]
            old_price = previous.get(top.hotel_name)
            drop_threshold = float(os.getenv("HOTEL_ALERT_DROP_PERCENTAGE", "10"))
            cooldown_hours = int(os.getenv("HOTEL_ALERT_COOLDOWN_HOURS", "24"))
            if old_price and top.price_value:
                drop = (old_price - top.price_value) / old_price * 100
                LOG.info("Top hotel: %s | score %.1f | current %s | previous %.0f | change %.1f%%",
                         top.hotel_name, top.score, top.price, old_price, drop)
                fingerprint = f"{city}:{top.hotel_name}:{top.price_value}:{round(drop, 1)}"
                if drop >= drop_threshold and not history.notification_exists(fingerprint, cooldown_hours):
                    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
                    if token and chat_id and notify_top_drop(city, top, old_price, drop, token, chat_id):
                        history.record_notification(city, top, old_price, drop, fingerprint)
                        LOG.info("Sent price-drop notification for top hotel %s", top.hotel_name)
                    else:
                        LOG.info("Price drop found for top hotel %s, but Telegram is not configured", top.hotel_name)
                elif drop < drop_threshold:
                    LOG.info("No Telegram alert: drop %.1f%% is below threshold %.1f%%", drop, drop_threshold)
                else:
                    LOG.info("No Telegram alert: this price-drop event is within cooldown/dedup window")
            else:
                LOG.info("Top hotel %s has no previous price; baseline saved, no alert sent", top.hotel_name)
            LOG.info("Wrote %d hotels to %s", len(hotels), output)
            return len(hotels)
        finally:
            await browser.close()

def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default="東京")
    parser.add_argument("--check-in", default="2026-10-04", help="入住日期 YYYY-MM-DD")
    parser.add_argument("--check-out", default="2026-10-07", help="退房日期 YYYY-MM-DD")
    parser.add_argument("--output", type=Path, default=Path("data.csv"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/hotels"))
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--timeout-ms", type=int, default=30000)
    parser.add_argument("--history-db", default="data/hotels.db")
    parser.add_argument("--test-telegram", action="store_true",
                        help="Send one test message and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.test_telegram:
            token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
            if not token or not chat_id:
                LOG.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
                return 2
            TelegramNotifier(token, chat_id).send("✅ Hotel scraper Telegram test message")
            LOG.info("Telegram test message sent")
            return 0
        check_in, check_out = date.fromisoformat(args.check_in), date.fromisoformat(args.check_out)
        if check_out <= check_in:
            LOG.error("--check-out must be later than --check-in")
            return 2
        asyncio.run(run(args.city, check_in, check_out, args.output, args.artifacts,
                         not args.headful, args.timeout_ms, args.history_db))
        return 0
    except (BlockedPageError, SelectorError, PlaywrightTimeoutError) as exc:
        LOG.error("%s", exc)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
