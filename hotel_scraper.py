"""Compliant Trip.com hotel list collector.
Uses ordinary Playwright only. It does not use stealth plugins, proxies,
CAPTCHA solvers, or anti-bot bypass. Block/verification pages are saved.
"""
from __future__ import annotations
import argparse, asyncio, csv, logging, os, random, re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunparse, urlunsplit
from dotenv import load_dotenv
from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright
from hotel_engine import price_value, rank_hotels
from hotel_history import HotelHistory
from flight_agent.notification.hotel_telegram import notify_current_prices, notify_top_drop
from flight_agent.notification.telegram import TelegramNotifier

LOG = logging.getLogger("hotel_scraper")
BLOCK_MARKERS = ("captcha", "verify you are human", "robot check", "access denied",
                 "whaleguard", "akamai", "anti-bot", "unusual traffic")
MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
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
DETAIL_NAME_SELECTORS = (
    "h1[class*='hotelOverview_name' i]",
    "h1[class*='hotelName' i]",
    "h1[data-testid*='hotel-name' i]",
    "h1",
)
DETAIL_RATING_SELECTORS = (
    "[class*='scoreBlock' i] [class*='score' i]",
    "[class*='hotelOverview' i] [aria-label^='9']",
    "[aria-label*='score' i]",
)
DETAIL_ROOM_SCOPE_SELECTORS = (
    "[class*='RoomList' i]",
    "[class*='roomList' i]",
)
DETAIL_ROOM_CARD_SELECTORS = (
    "[class*='RoomCard' i]",
    "[class*='roomCard' i]",
    "[class*='room-item' i]",
    "[class*='roomItem' i]",
    "[data-testid*='room' i]",
)
DETAIL_PRICE_SELECTORS = (
    "[data-testid*='price' i]",
    "[class*='price' i]",
    "[class*='Price' i]",
    "[class*='amount' i]",
    "[class*='Amount' i]",
    "[aria-label*='price' i]",
)
DETAIL_CURRENT_PRICE_SELECTORS = (
    "[class*='displayPrice' i]",
    "[data-testid*='current-price' i]",
    "[aria-label*='current price' i]",
)
DETAIL_OFFER_EXCLUDED_TERMS = ("早餐", "每人", "每位", "小童", "兒童", "成人", "餐飲")
CITY_SUGGESTION_SELECTORS = (
    "[role='option']",
    "[class*='suggestion' i]",
    "[class*='N7ro' i]",
    "[class*='poi' i]",
)
CITY_SLUGS = {
    "高雄": "kaohsiung",
    "kaohsiung": "kaohsiung",
    "東京": "tokyo",
    "tokyo": "tokyo",
}

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

CURRENCY_PRICE_RE = re.compile(
    r"(?:HKD|TWD|NT\$|HK\$|US\$|[A-Z]{3}|[￥¥€£$])"
    r"\s*[\d,]+(?:\.\d{1,2})?",
    re.I,
)

def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


async def connect_to_cdp(playwright, cdp_url: str):
    """Connect to a user-launched Chrome, including Docker Desktop on macOS."""
    from httpx import AsyncClient

    parsed = urlsplit(cdp_url)
    if parsed.scheme in {"ws", "wss"}:
        return await playwright.chromium.connect_over_cdp(cdp_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("HOTEL_CDP_URL must be an http(s) or ws(s) CDP endpoint")

    # Chrome accepts the CDP HTTP endpoint only when the Host header matches
    # the listener name. Docker Desktop reaches it as host.docker.internal,
    # while Chrome was launched on localhost.
    host_header = os.getenv("HOTEL_CDP_HOST_HEADER", "").strip()
    if not host_header and parsed.hostname == "host.docker.internal":
        host_header = f"localhost:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    headers = {"Host": host_header} if host_header else None
    version_url = urlunsplit((parsed.scheme, parsed.netloc, "/json/version", "", ""))
    async with AsyncClient(timeout=10.0) as client:
        response = await client.get(version_url, headers=headers)
        response.raise_for_status()
        version = response.json()

    websocket_url = version.get("webSocketDebuggerUrl")
    if not websocket_url:
        raise RuntimeError("Chrome CDP response did not include webSocketDebuggerUrl")
    websocket = urlsplit(websocket_url)
    websocket_host = parsed.hostname
    websocket_port = parsed.port or websocket.port
    if websocket_port:
        websocket_host = f"{websocket_host}:{websocket_port}"
    websocket_url = urlunsplit(
        (websocket.scheme, websocket_host, websocket.path, websocket.query, websocket.fragment)
    )
    LOG.info("Using Chrome CDP WebSocket at %s", websocket_url)
    return await playwright.chromium.connect_over_cdp(websocket_url, headers=headers)


def detail_page_matches(current_url: str, target_url: str) -> bool:
    """Return whether a connected Chrome tab is the requested dated detail page."""
    current = urlparse(current_url or "")
    target = urlparse(target_url or "")
    if current.scheme not in {"http", "https"} or not current.netloc:
        return False
    if "/hotels/" not in current.path:
        return False
    current_query = dict(parse_qsl(current.query, keep_blank_values=True))
    target_query = dict(parse_qsl(target.query, keep_blank_values=True))
    target_hotel_id = target_query.get("hotelId")
    if target_hotel_id and current_query.get("hotelId") != target_hotel_id:
        return False
    for key in ("checkIn", "checkOut"):
        expected = target_query.get(key)
        if expected and current_query.get(key) != expected:
            return False
    return True


def parse_target_names(value: str) -> tuple[str, ...]:
    return tuple(name for name in (_clean(part) for part in value.split(",")) if name)


def detail_page_url(detail_url: str, check_in: date, check_out: date,
                    currency: str = "TWD", adults: int = 2, children: int = 0) -> str:
    """Add the ordinary Trip.com detail-page search parameters to a saved URL."""
    parsed = urlparse(detail_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("HOTEL_DETAIL_URL must be an absolute http(s) URL.")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({
        "curr": currency or query.get("curr", "TWD"),
        "locale": query.get("locale", os.getenv("HOTEL_LOCALE", "zh-TW")),
        "checkIn": check_in.isoformat(),
        "checkOut": check_out.isoformat(),
        "crn": "1",
        "adult": str(max(1, adults)),
        "children": str(max(0, children)),
    })
    return urlunparse(parsed._replace(query=urlencode(query)))

def _name_key(value: str) -> str:
    value = value.replace("酒店", "飯店")
    return re.sub(r"[\s\-_/·・（）()（）]+", "", value).casefold()

def hotel_name_matches(name: str, targets: tuple[str, ...]) -> bool:
    name_key = _name_key(name)
    return any(
        target_key in name_key or name_key in target_key
        for target in targets
        if (target_key := _name_key(target))
    )

def _price_text(value: str) -> str:
    value = _clean(value)
    match = CURRENCY_PRICE_RE.search(value)
    if match:
        return match.group(0).strip()
    match = re.search(r"[\d,]+(?:\.\d{1,2})?", value)
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

def _city_names(city: str) -> tuple[str, ...]:
    normalized = _clean(city).casefold()
    aliases = {
        "高雄": ("高雄", "kaohsiung"),
        "kaohsiung": ("高雄", "kaohsiung"),
        "東京": ("東京", "tokyo"),
        "tokyo": ("東京", "tokyo"),
    }
    return aliases.get(normalized, (normalized,))

async def find_city_suggestion(page: Page, city: str, timeout_ms: int):
    """Find a visible suggestion whose text matches the requested city.

    Trip.com has changed the suggestion markup several times. Matching the
    visible text prevents clicking an unrelated generic option when the page
    exposes multiple role=option elements.
    """
    names = _city_names(city)
    deadline = asyncio.get_running_loop().time() + min(timeout_ms, 15000) / 1000
    while asyncio.get_running_loop().time() < deadline:
        for selector in CITY_SUGGESTION_SELECTORS:
            candidates = page.locator(selector)
            try:
                count = await candidates.count()
            except Exception:
                continue
            for index in range(min(count, 30)):
                candidate = candidates.nth(index)
                try:
                    if not await candidate.is_visible(timeout=300):
                        continue
                    text = _clean(await candidate.inner_text()).casefold()
                except Exception:
                    continue
                if text and any(name in text for name in names):
                    return candidate
        await page.wait_for_timeout(300)
    return None

async def set_date_range(page: Page, check_in: date, check_out: date, timeout_ms: int) -> None:
    """Choose dates through Trip.com's normal date picker UI."""
    date_input = await first_visible(page, (
        "#checkInInput",
        ".calendar-container .time-tab.checkin input",
        ".li-item-calendar .checkin input",
    ))
    if date_input is None:
        raise SelectorError("Could not find Trip.com's check-in date input.")
    await date_input.click(timeout=timeout_ms)
    target = check_in
    for _ in range(24):
        wanted = (target.year, target.month)
        month_titles = page.locator(".c-calendar-month__title, .c-calendar-month__title h2")
        visible_months = []
        for index in range(await month_titles.count()):
            title = month_titles.nth(index)
            if not await title.is_visible(timeout=300):
                continue
            text = await title.inner_text(timeout=timeout_ms)
            match = re.search(r"(\d{4}).*?(\d{1,2})", text)
            if match:
                visible_months.append((int(match.group(1)), int(match.group(2))))
                continue
            year_match = re.search(r"\b(20\d{2})\b", text)
            month_match = next(
                (name for name in MONTH_NAMES if re.search(rf"\b{name}\b", text, re.I)),
                None,
            )
            if year_match and month_match:
                visible_months.append((int(year_match.group(1)), MONTH_NAMES[month_match]))
        if wanted in visible_months:
            break
        direction = "next" if not visible_months or visible_months[-1] < wanted else "previous"
        button_names = (
            ("前往下個月", "下個月", "Next month") if direction == "next"
            else ("前往上個月", "上個月", "Previous month")
        )
        clicked = False
        for name in button_names:
            button = page.get_by_role("button", name=re.compile(re.escape(name), re.I)).first
            try:
                if await button.is_visible(timeout=300):
                    await button.click(timeout=timeout_ms)
                    clicked = True
                    break
            except PlaywrightTimeoutError:
                continue
        if not clicked:
            # The current Trip.com calendar renders icon-only controls without
            # an accessible name on some locales, so keep a class-based
            # fallback after trying the semantic button names above.
            icon_classes = (
                (".c-calendar-icon-next", ".c-calendar-icon-next-mon", "[aria-label='Go to next month']")
                if direction == "next"
                else (".c-calendar-icon-prev", ".c-calendar-icon-prev-mon", "[aria-label='Go to previous month']")
            )
            for icon_class in icon_classes:
                button = page.locator(icon_class).first
                try:
                    if await button.is_visible(timeout=300):
                        await button.click(timeout=timeout_ms)
                        clicked = True
                        break
                except PlaywrightTimeoutError:
                    continue
        if not clicked:
            raise SelectorError("Could not navigate Trip.com's date picker to the requested month.")
        await page.wait_for_timeout(150)
    else:
        raise SelectorError(f"Could not navigate date picker to {target:%Y-%m}.")

    async def choose(day: date):
        month_name = next(name for name, number in MONTH_NAMES.items() if number == day.month)
        month = page.locator(".c-calendar-month").filter(
            has_text=re.compile(
                fr"(?:{day.year}\s*年\s*{day.month}\s*月|{month_name}\s+{day.year})",
                re.I,
            )
        ).first
        if await month.count():
            cells = month.locator("[role='gridcell'], li.is-allow-hover")
            for index in range(await cells.count()):
                cell = cells.nth(index)
                day_node = cell.locator(".day").first
                text = _clean(await day_node.inner_text()) if await day_node.count() else _clean(await cell.inner_text())
                if text == str(day.day) and await cell.is_visible(timeout=300):
                    await cell.click(timeout=timeout_ms)
                    return
        labels = (
            f"{day.year} 年 {day.month} 月 {day.day} 日",
            f"{month_name} {day.day}, {day.year}",
            f"{month_name} {day.day} {day.year}",
        )
        for label in labels:
            cell = page.get_by_role("gridcell", name=re.compile(re.escape(label), re.I)).first
            try:
                if await cell.is_visible(timeout=300):
                    await cell.click(timeout=timeout_ms)
                    return
            except PlaywrightTimeoutError:
                continue
        raise SelectorError(f"Could not select date {day.isoformat()} from Trip.com's date picker.")

    await choose(check_in)
    await page.wait_for_timeout(250)
    await choose(check_out)

async def search_city(page: Page, city: str, check_in: date, check_out: date, timeout_ms: int) -> None:
    input_box = await first_visible(page, CITY_INPUTS)
    if input_box is None:
        raise SelectorError("Could not find the hotel city input on Trip.com homepage.")
    await input_box.fill(city)
    await page.wait_for_timeout(random.randint(500, 1200))
    # Prefer a visible option whose text matches the requested city. If the
    # page uses a markup variant not covered by the selectors, use the normal
    # keyboard selection flow; never accept an unrelated option.
    suggestion = await find_city_suggestion(page, city, timeout_ms)
    if suggestion is not None:
        await suggestion.click()
    else:
        await input_box.press("ArrowDown")
        await page.wait_for_timeout(300)
        await input_box.press("Enter")
        LOG.info("City suggestion DOM was not matched; attempted keyboard selection for %s", city)
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

def city_page_url(city: str, city_id: str) -> str:
    slug = os.getenv("HOTEL_CITY_SLUG", "").strip().strip("/")
    slug = slug or CITY_SLUGS.get(_clean(city).casefold(), "")
    if not slug:
        raise SelectorError(
            f"No Trip.com city slug is configured for {city}; set HOTEL_CITY_SLUG in .env."
        )
    params = {"curr": os.getenv("HOTEL_CURRENCY", "TWD"), "locale": "zh-TW"}
    return f"https://tw.trip.com/hotels/{slug}-hotels-list-{city_id}/?{urlencode(params)}"


async def search_from_city_page(page: Page, city: str, city_id: str,
                                check_in: date, check_out: date, timeout_ms: int) -> None:
    """Use Trip.com's public city page, then submit its normal search form.

    The generic /hotels/search/list URL currently returns a homepage/404 for
    this site. The city page keeps the city selected and its search form
    produces the date-specific /hotels/list URL.
    """
    await page.goto(city_page_url(city, city_id), wait_until="domcontentloaded", timeout=timeout_ms)
    await page.wait_for_timeout(1500)
    await set_date_range(page, check_in, check_out, timeout_ms)
    button = await first_visible(page, (
        ".li-item-btn .search-btn-wrap",
        ".li-item-btn",
        "button:has-text('搜尋飯店')",
    ))
    if button is None:
        raise SelectorError("Could not find Trip.com's city-page hotel search button.")
    await button.click(timeout=timeout_ms)
    try:
        await page.wait_for_url("**/hotels/list**", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)


async def search_hotel_detail(page: Page, detail_url: str, check_in: date,
                              check_out: date, timeout_ms: int, artifacts: Path,
                              reuse_current_page: bool = False) -> None:
    """Open one saved hotel page with Trip.com's ordinary date parameters.

    Detail URLs avoid the city-list limitation: a city page may only render a
    small set of featured hotels, while a detail URL identifies the requested
    property exactly. The URL parameters are the same values that the normal
    detail-page date form writes after pressing its update button.
    """
    currency = os.getenv("HOTEL_CURRENCY", "TWD").strip().upper()
    try:
        adults = int(os.getenv("HOTEL_ADULTS", "2"))
    except ValueError:
        adults = 2
    try:
        children = int(os.getenv("HOTEL_CHILDREN", "0"))
    except ValueError:
        children = 0
    url = detail_page_url(detail_url, check_in, check_out, currency, adults, children)
    if reuse_current_page and detail_page_matches(page.url, url):
        LOG.info("Reusing the connected Chrome hotel detail page")
        response = await page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
    else:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    if response and response.status in (403, 429):
        await save_evidence(page, artifacts, f"http_{response.status}")
        raise BlockedPageError(f"Trip.com returned HTTP {response.status}; stopped without bypass.")
    await page.wait_for_timeout(2500)
    if "/account/signin" in page.url:
        await save_evidence(page, artifacts, "detail_redirected_to_signin")
        raise SelectorError(
            "Trip.com redirected the hotel detail URL to sign-in; "
            "the public detail page was not available for this session."
        )
    marker = await detect_block(page)
    if marker:
        await save_evidence(page, artifacts, "blocked")
        raise BlockedPageError(f"Trip.com returned a verification/anti-bot page ({marker}).")


async def _detail_hotel_name(page: Page) -> str:
    for selector in DETAIL_NAME_SELECTORS:
        node = page.locator(selector).first
        try:
            if await node.count() and await node.is_visible(timeout=300):
                text = _clean(await node.inner_text())
                if text:
                    return text
        except Exception:
            continue
    return ""


async def _detail_rating(page: Page) -> float | None:
    for selector in DETAIL_RATING_SELECTORS:
        nodes = page.locator(selector)
        for index in range(min(await nodes.count(), 10)):
            node = nodes.nth(index)
            try:
                if not await node.is_visible(timeout=300):
                    continue
                text = " ".join(filter(None, [
                    await node.get_attribute("aria-label"),
                    await node.inner_text(),
                ]))
            except Exception:
                continue
            match = re.search(r"(?<!\d)(10(?:\.0)?|[0-9](?:\.[0-9])?)(?!\d)", text)
            if match:
                return float(match.group(1))
    return None


async def _detail_prices(scope) -> list[tuple[float, str, str]]:
    """Return unique currency-labelled prices inside a room-list scope."""
    async def collect(selectors, exclude_non_room_totals=False):
        results = []
        seen = set()
        for selector in selectors:
            nodes = scope.locator(selector)
            for index in range(min(await nodes.count(), 100)):
                node = nodes.nth(index)
                try:
                    if not await node.is_visible(timeout=300):
                        continue
                    text = _clean(await node.inner_text())
                    class_name = await node.get_attribute("class") or ""
                except Exception:
                    continue
                key = (text, class_name)
                if not text or key in seen:
                    continue
                seen.add(key)
                if exclude_non_room_totals and any(term in text for term in (
                    "總額", "早餐", "每人", "每位", "小童", "兒童", "成人", "餐飲",
                )):
                    continue
                matches = list(CURRENCY_PRICE_RE.finditer(text))
                for match in matches:
                    parsed = match.group(0).strip()
                    value = price_value(parsed)
                    if value is None or value <= 0:
                        continue
                    currency_match = re.match(
                        r"(?:HKD|TWD|NT\$|HK\$|US\$|[￥¥€£$]|[A-Z]{3})",
                        parsed,
                        re.I,
                    )
                    currency = currency_match.group(0) if currency_match else ""
                    results.append((value, parsed, currency))
        return results

    # Trip.com's current offer has a stable semantic class prefix while the
    # surrounding container also contains the struck-through price and total.
    current = await collect(DETAIL_CURRENT_PRICE_SELECTORS)
    if current:
        return current
    return await collect(DETAIL_PRICE_SELECTORS, exclude_non_room_totals=True)


async def _visible_offer_prices(page: Page, currency: str) -> list[tuple[float, str, str]]:
    """Read visible currency prices from the rendered offer card as a fallback.

    Some Trip.com layouts show the selected hotel's price in a right-hand offer
    card instead of inside the room-list container. Only short, visible,
    currency-labelled leaf texts are considered, and meal/person prices are
    excluded so they cannot become the hotel price.
    """
    currency = (currency or "").upper()
    markers = [currency] if currency else []
    if currency == "HKD":
        markers.append("HK$")
    if currency == "TWD":
        markers.append("NT$")
    payload = await page.evaluate(
        """(markers) => [...document.querySelectorAll('body *')]
          .filter((element) => element.children.length === 0)
          .filter((element) => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.visibility !== 'hidden' && style.display !== 'none'
              && rect.width > 0 && rect.height > 0;
          })
          .map((element) => ({
            text: (element.textContent || '').trim(),
            className: String(element.className || '')
          }))
          .filter((item) => item.text.length > 0 && item.text.length <= 120
            && markers.some((marker) => item.text.toUpperCase().includes(marker)))
          .slice(0, 300)
        """,
        markers,
    )
    results = []
    seen = set()
    for item in payload:
        text = _clean(item["text"])
        if any(term in text for term in DETAIL_OFFER_EXCLUDED_TERMS):
            continue
        for match in CURRENCY_PRICE_RE.finditer(text):
            parsed = match.group(0).strip()
            value = price_value(parsed)
            if value is None or value <= 0:
                continue
            found_currency = re.match(
                r"(?:HKD|TWD|NT\$|HK\$|US\$|[￥¥€£$]|[A-Z]{3})",
                parsed,
                re.I,
            )
            found_currency = found_currency.group(0) if found_currency else currency
            key = (value, parsed, found_currency)
            if key not in seen:
                seen.add(key)
                results.append(key)
    return results


async def collect_hotel_detail(page: Page, city: str, artifacts: Path, timeout_ms: int,
                               target_names: tuple[str, ...], detail_url: str) -> list[Hotel]:
    """Collect the lowest visible room price from one hotel detail page."""
    marker = await detect_block(page)
    if marker:
        await save_evidence(page, artifacts, "blocked")
        raise BlockedPageError(f"Trip.com returned a verification/anti-bot page ({marker}).")

    hotel_name = await _detail_hotel_name(page)
    if not hotel_name:
        await save_evidence(page, artifacts, "detail_name_selector_error")
        raise SelectorError("Could not read the hotel name from the detail page.")
    if target_names and not hotel_name_matches(hotel_name, target_names):
        await save_evidence(page, artifacts, "detail_name_mismatch")
        raise SelectorError(
            f"Detail page hotel {hotel_name!r} does not match the requested hotel."
        )

    scope = None
    for selector in DETAIL_ROOM_SCOPE_SELECTORS:
        candidates = page.locator(selector)
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                if not await candidate.is_visible(timeout=300):
                    continue
                text = _clean(await candidate.inner_text())
            except Exception:
                continue
            has_price_nodes = await candidate.locator(
                ", ".join(DETAIL_PRICE_SELECTORS)
            ).count()
            if ("選擇房間" in text or "選擇房型" in text or "Select room" in text
                    or has_price_nodes):
                scope = candidate
                break
        if scope is not None:
            break

    prices = await _detail_prices(scope) if scope is not None else []
    if not prices:
        prices = await _visible_offer_prices(page, os.getenv("HOTEL_CURRENCY", "TWD"))
    if not prices:
        await save_evidence(page, artifacts, "detail_price_selector_error")
        raise SelectorError(
            "No dated room prices found on the hotel detail page; "
            "live detail-page selectors/API response need verification."
        )
    _, price, currency = min(prices, key=lambda item: item[0])
    rating = await _detail_rating(page)
    now = datetime.now(timezone.utc).isoformat()
    source_url = page.url or detail_url
    return [Hotel(hotel_name, price, currency, rating, 0.0, source_url, now)]

async def collect_hotels(page: Page, city: str, artifacts: Path, timeout_ms: int,
                         target_names: tuple[str, ...] = ()) -> list[Hotel]:
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

    if target_names:
        # Hotel lists are lazy-loaded. Scroll a bounded number of times to
        # give selected hotels below the initial viewport a chance to appear.
        unchanged = 0
        for _ in range(8):
            loaded = await cards.count()
            names = []
            for index in range(loaded):
                card = cards.nth(index)
                for selector in NAME_SELECTORS:
                    node = card.locator(selector).first
                    if await node.count():
                        text = _clean(await node.inner_text())
                        if text:
                            names.append(text)
                            break
            if all(any(hotel_name_matches(name, (target,)) for name in names) for target in target_names):
                break
            await human_paced_scroll(page)
            await page.wait_for_timeout(800)
            if await cards.count() == loaded:
                unchanged += 1
                if unchanged >= 2:
                    break
            else:
                unchanged = 0

    now = datetime.now(timezone.utc).isoformat()
    output = []
    found_targets = set()
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
        if target_names and (not name or not hotel_name_matches(name, target_names)):
            continue
        if name and target_names:
            found_targets.update(target for target in target_names if hotel_name_matches(name, (target,)))
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
    if target_names:
        missing = [target for target in target_names if target not in found_targets]
        if missing:
            await save_evidence(page, artifacts, "targets_not_found")
            raise SelectorError("Requested hotel(s) not found in the loaded results: " + ", ".join(missing))
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
              headless: bool, timeout_ms: int, history_path: str = "data/hotels.db",
              detail_url: str = "") -> int:
    async with async_playwright() as playwright:
        cdp_url = os.getenv("HOTEL_CDP_URL", "").strip()
        connected_browser = bool(cdp_url)
        reuse_current_page = env_flag("HOTEL_REUSE_CURRENT_PAGE", True)
        locale = os.getenv("HOTEL_LOCALE", "zh-TW")
        if connected_browser:
            LOG.info("Connecting to the user-launched Chrome at %s", cdp_url)
            browser = await connect_to_cdp(playwright, cdp_url)
            if not browser.contexts:
                raise RuntimeError("Connected Chrome has no browser context.")
            context = browser.contexts[0]
            page = None
            if reuse_current_page:
                page = next((candidate for candidate in context.pages
                             if "/hotels/" in candidate.url), None)
            if page is None:
                page = await context.new_page()
        else:
            launch_options = {"headless": headless}
            executable_path = os.getenv("HOTEL_EXECUTABLE_PATH", "").strip()
            if executable_path:
                launch_options["executable_path"] = executable_path
            browser = await playwright.chromium.launch(**launch_options)
            context = await browser.new_context(locale=locale)
            page = await context.new_page()
        blocked_responses = []
        detail_api_blocked = []

        def remember_blocked_response(response):
            if response.request.resource_type == "document" and response.status in (403, 429):
                blocked_responses.append(response)
            if (detail_url.strip() and "getHotelRoomListOversea" in response.url
                    and response.status in (403, 429, 430)):
                detail_api_blocked.append(response)

        page.on("response", remember_blocked_response)
        try:
            target_names = parse_target_names(os.getenv("HOTEL_TARGET_NAMES", ""))
            if target_names:
                LOG.info("Checking selected hotels only: %s", ", ".join(target_names))
            if detail_url.strip():
                await search_hotel_detail(page, detail_url, check_in, check_out,
                                          timeout_ms, artifacts,
                                          connected_browser and reuse_current_page)
                if detail_api_blocked:
                    status = detail_api_blocked[-1].status
                    await save_evidence(page, artifacts, f"detail_http_{status}")
                    raise BlockedPageError(
                        f"Trip.com room availability returned HTTP {status}; stopped without bypass."
                    )
                hotels = rank_hotels(await collect_hotel_detail(
                    page, city, artifacts, timeout_ms, target_names, detail_url
                ))
            else:
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
                    city_id = os.getenv("HOTEL_CITY_ID", "").strip()
                    if city_id:
                        LOG.warning("Trip.com did not resolve %s from the suggestion list; using configured city ID %s via the city page", city, city_id)
                        await search_from_city_page(page, city, city_id, check_in, check_out, timeout_ms)
                        if f"city={city_id}" not in page.url:
                            await save_evidence(page, artifacts, "city_id_not_applied")
                            raise SelectorError(f"Trip.com city page did not apply configured city ID {city_id}.")
                    else:
                        await save_evidence(page, artifacts, "city_not_resolved")
                        raise SelectorError("Trip.com did not resolve the city suggestion; refusing to scrape an unfiltered list.")
                if blocked_responses:
                    status = blocked_responses[-1].status
                    await save_evidence(page, artifacts, f"http_{status}")
                    raise BlockedPageError(f"Trip.com returned HTTP {status}; stopped without bypass.")
                await human_paced_scroll(page)
                hotels = rank_hotels(await collect_hotels(page, city, artifacts, timeout_ms, target_names))
            write_csv(hotels, output)
            history = HotelHistory(history_path)
            previous = history.previous_prices(city, [row.hotel_name for row in hotels])
            history.add(city, hotels)
            top = hotels[0]
            old_price = previous.get(top.hotel_name)
            drop_threshold = float(os.getenv("HOTEL_ALERT_DROP_PERCENTAGE", "10"))
            cooldown_hours = int(os.getenv("HOTEL_ALERT_COOLDOWN_HOURS", "24"))
            always_notify = os.getenv("HOTEL_NOTIFY_CURRENT_PRICE", "false").strip().casefold() in {
                "1", "true", "yes", "on"
            }
            token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
            current_price_sent = False
            if always_notify:
                rows_to_notify = hotels if target_names else [top]
                if token and chat_id:
                    current_price_sent = notify_current_prices(
                        city, rows_to_notify, previous, token, chat_id, drop_threshold
                    )
                    LOG.info("Sent current-price notification for %d hotel(s)", len(rows_to_notify))
                else:
                    LOG.info("Current-price notification enabled, but Telegram is not configured")
            if old_price and top.price_value:
                drop = (old_price - top.price_value) / old_price * 100
                LOG.info("Top hotel: %s | score %.1f | current %s | previous %.0f | change %.1f%%",
                         top.hotel_name, top.score, top.price, old_price, drop)
                fingerprint = f"{city}:{top.hotel_name}:{top.price_value}:{round(drop, 1)}"
                if drop >= drop_threshold and not history.notification_exists(fingerprint, cooldown_hours):
                    if current_price_sent:
                        history.record_notification(city, top, old_price, drop, fingerprint)
                        LOG.info("Price-drop marker included in current-price notification for %s", top.hotel_name)
                    elif token and chat_id and notify_top_drop(city, top, old_price, drop, token, chat_id):
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
            if connected_browser:
                LOG.info("Leaving the connected Chrome browser open")
            else:
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
    parser.add_argument("--detail-url", default=os.getenv("HOTEL_DETAIL_URL", ""),
                        help="Optional exact Trip.com hotel detail URL")
    parser.add_argument("--test-telegram", action="store_true",
                        help="Send one test message and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # httpx includes the full Telegram API URL in INFO logs, which would expose
    # the bot token. Keep request details out of normal application logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
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
                         not args.headful, args.timeout_ms, args.history_db,
                         args.detail_url))
        return 0
    except (BlockedPageError, SelectorError, PlaywrightTimeoutError) as exc:
        LOG.error("%s", exc)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
