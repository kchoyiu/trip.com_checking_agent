import logging, os, re, time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from .selectors import AIRLINE_SELECTORS, CAPTCHA_MARKERS, RESULT_SELECTORS, PRICE_SELECTORS
from ..engine.airlines import detect_airline, display_airline
from ..models import FlightPrice
log=logging.getLogger(__name__)
class BotDetected(RuntimeError): pass
class PageRouteMismatch(RuntimeError): pass
class TripScraper:
    def __init__(self, cfg, artifact_dir="artifacts"):
        self.cfg=cfg; self.artifacts=Path(artifact_dir); self.artifacts.mkdir(parents=True,exist_ok=True)

    @staticmethod
    def _env_flag(name, default=True):
        value = os.getenv(name)
        if value is None or not value.strip():
            return default
        return value.strip().casefold() in {"1", "true", "yes", "on"}

    @staticmethod
    def _env_seconds(name, default=0.0):
        value = os.getenv(name)
        if value is None or not value.strip():
            return default
        try:
            return max(0.0, float(value))
        except ValueError:
            log.warning("Invalid %s=%r; using %.0f seconds", name, value, default)
            return default

    @staticmethod
    def _job_value(job, key):
        try:
            return job[key]
        except (KeyError, IndexError, TypeError):
            return getattr(job, key)

    async def search(self, job):
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            cdp_url = os.getenv("TRIP_CDP_URL", "").strip()
            connected_browser = bool(cdp_url)
            reuse_current_page = self._env_flag("TRIP_REUSE_CURRENT_PAGE", True)
            browser = None
            page = None
            if connected_browser:
                log.info("Connecting to the user-launched Chrome at %s", cdp_url)
                browser = await self._connect_to_cdp(p, cdp_url)
                if not browser.contexts:
                    raise RuntimeError("Connected Chrome has no browser context.")
                context = browser.contexts[0]
                if reuse_current_page:
                    flight_pages = [
                        candidate for candidate in context.pages
                        if "trip.com" in candidate.url and "/flights" in candidate.url
                    ]
                    page = next(
                        (candidate for candidate in flight_pages
                         if self._page_route_matches_job(candidate.url, job) is True),
                        None,
                    )
                    if page is None and flight_pages:
                        page = flight_pages[0]
                if page is None:
                    page = await context.new_page()
            else:
                browser = await p.chromium.launch(headless=self.cfg.get("headless",True))
                context = await browser.new_context(locale=self.cfg.get("locale","en-HK"))
                page = await context.new_page()
            try:
                search_url = self._build_search_url(job)
                keep_manual_page = connected_browser and reuse_current_page and "/flights" in page.url
                navigate_to_job = self._env_flag("TRIP_NAVIGATE_TO_JOB", False)
                if keep_manual_page and not navigate_to_job:
                    page_route_match = self._page_route_matches_job(page.url, job)
                    if page_route_match is False:
                        raise PageRouteMismatch(
                            f"Connected Chrome page is a different flight route for "
                            f"{self._job_value(job, 'origin')} → {self._job_value(job, 'destination')}: {page.url}"
                        )
                    log.info("Using the currently open Trip.com flight page for %s → %s: %s", self._job_value(job, "origin"), self._job_value(job, "destination"), page.url)
                else:
                    target_url = search_url if navigate_to_job else self.cfg.get("base_url", "https://www.trip.com/flights/")
                    await page.goto(target_url,
                        wait_until="domcontentloaded", timeout=self.cfg.get("timeout_ms",30000))
                body=(await page.locator("body").inner_text()).lower()
                marker = next((x for x in CAPTCHA_MARKERS if x in body), None)
                if marker:
                    marker = await self._wait_for_manual_verification(page, marker, connected_browser)
                if marker:
                    await self._capture(page,"captcha")
                    raise BotDetected(f"CAPTCHA/anti-bot page detected ({marker}); stopped without bypass")
                configured_airline = self._job_value(job, "airline")
                if configured_airline:
                    await self._refresh_expired_search(page)
                    body = (await page.locator("body").inner_text()).lower()
                    marker = next((x for x in CAPTCHA_MARKERS if x in body), None)
                    if marker:
                        await self._capture(page,"captcha")
                        raise BotDetected(f"CAPTCHA/anti-bot page detected ({marker}); stopped without bypass")
                    await self._dismiss_page_dialog(page)
                    await self._apply_airline_filter(page, configured_airline)
                log.warning("Trip.com selectors are live-page dependent; validate selectors.py")
                found=page.locator(",".join(RESULT_SELECTORS))
                if await found.count() == 0:
                    # New Trip.com builds may not expose stable flight-card
                    # classes. Prefer card-like containers that contain a
                    # price node, rather than treating the whole page as one
                    # result.
                    found = page.locator("[data-testid*='card'], [class*='card'], li").filter(
                        has=page.locator(",".join(PRICE_SELECTORS))
                    )
                await found.first.wait_for(timeout=self.cfg.get("timeout_ms",30000))
                results=[]
                seen_cards = set()
                for i in range(min(await found.count(),100)):
                    card=found.nth(i); node=card.locator(",".join(PRICE_SELECTORS)).first
                    card_text = await card.inner_text()
                    price_text=(await node.inner_text()) if await node.count() else ""
                    price = self._extract_price(price_text) or self._extract_price(card_text)
                    if price is None:
                        continue
                    airline = await self._extract_airline(card, card_text)
                    if airline == "Unknown":
                        log.info("Unidentified airline card %d text=%s", i, " | ".join(card_text.split())[:500])
                    fingerprint = (round(price, 2), airline, " ".join(card_text.split()))
                    if fingerprint in seen_cards:
                        continue
                    seen_cards.add(fingerprint)
                    link = card.locator("a[href]").first
                    result_url = await link.get_attribute("href") if await link.count() else ""
                    results.append(FlightPrice(0, airline, "Unknown", "", "", None, None, price, self._job_value(job, "currency"), result_url or page.url))
                log.info("Parsed %d flight result cards; airline labels=%s", len(results), sorted({price.airline for price in results}))
                markers = [
                    line.strip() for line in body.splitlines()
                    if any(token in line for token in ("cathay", "國泰", "国泰", " cx "))
                ]
                if markers:
                    log.info("Visible Cathay markers on page=%s", markers[:10])
                return results
            except (BotDetected, PageRouteMismatch): raise
            except Exception:
                await self._capture(page,"error"); raise
            finally:
                if browser is not None and not connected_browser:
                    await browser.close()

    def _build_search_url(self, job):
        """Build a Trip.com flight search URL for the exact active job."""
        base = self.cfg.get("base_url", "https://www.trip.com/flights/")
        params = {
            "dcity": str(self._job_value(job, "origin")).lower(),
            "acity": str(self._job_value(job, "destination")).lower(),
            "ddate": str(self._job_value(job, "depart_date")),
            "adult": self._job_value(job, "adults"),
            "currency": self._job_value(job, "currency"),
        }
        return_date = self._job_value(job, "return_date")
        if return_date:
            params["rdate"] = str(return_date)
        else:
            params["triptype"] = "oneway"
        return f"{base}?{urlencode(params)}"

    async def _apply_airline_filter(self, page, configured_airline):
        """Select the visible Trip.com airline filter when it is available."""
        label = display_airline(configured_airline)
        option = page.get_by_role("checkbox", name=label, exact=True).first
        if await option.count() == 0:
            pattern = re.compile(rf"^{re.escape(label)}(?:\s*\(\d+\))?$", re.IGNORECASE)
            option = page.get_by_text(pattern).first
        if await option.count() == 0:
            log.warning("Airline filter %s was not found on the current Trip.com page", label)
            return False
        try:
            if (await option.get_attribute("aria-checked")) != "true":
                await option.click(timeout=5000)
            await page.wait_for_timeout(1500)
            log.info("Applied Trip.com airline filter: %s", label)
            return True
        except Exception as exc:
            log.warning("Could not apply Trip.com airline filter %s: %s", label, exc)
            return False

    async def _refresh_expired_search(self, page):
        """Refresh a stale Trip.com result page when its modal offers that action."""
        modal = page.locator("[data-testid='dialog-RefreshSearch']")
        if await modal.count() == 0 or not await modal.first.is_visible():
            return False
        refresh = modal.locator("[data-testid='dialog-footer-yes']").first
        if await refresh.count() == 0:
            log.warning("Trip.com result-expired dialog has no Refresh control")
            return False
        try:
            await refresh.click(force=True, timeout=5000)
            await page.wait_for_timeout(3000)
            log.info("Refreshed expired Trip.com search results")
            return True
        except Exception as exc:
            log.warning("Could not refresh expired Trip.com results: %s", exc)
            return False

    async def _dismiss_page_dialog(self, page):
        """Close an obstructing Trip.com modal using an explicit close control."""
        dialogs = page.locator("#dialogWrapper, [role='dialog'], .ift-modal-wrap")
        for dialog_index in range(min(await dialogs.count(), 6)):
            dialog = dialogs.nth(dialog_index)
            if not await dialog.is_visible():
                continue
            controls = dialog.locator("button, [role='button'], [class*='close'], [class*='Close']")
            for control_index in range(min(await controls.count(), 20)):
                control = controls.nth(control_index)
                if not await control.is_visible():
                    continue
                label = " ".join(filter(None, [
                    await control.get_attribute("aria-label"),
                    await control.get_attribute("title"),
                    await control.get_attribute("class"),
                    (await control.inner_text()).strip(),
                ])).casefold()
                if not any(token in label for token in ("close", "關閉", "关闭", "dismiss", "cancel", "×")):
                    continue
                try:
                    await control.click(force=True, timeout=3000)
                    await page.wait_for_timeout(500)
                    log.info("Dismissed an obstructing Trip.com dialog")
                    return True
                except Exception:
                    continue
        return False

    @staticmethod
    def _extract_price(text):
        """Extract the lowest explicitly currency-labelled price from text."""
        if not text:
            return None
        matches = re.findall(r"(?:HK\$|HKD|US\$|USD|TWD|NT\$|\$)\s*([0-9][0-9,]*(?:\.\d+)?)", text, flags=re.IGNORECASE)
        if not matches:
            return None
        return min(float(value.replace(",", "")) for value in matches)

    def _page_route_matches_job(self, url, job):
        """Return False only when Trip.com exposes a conflicting route in the URL."""
        parsed_url = urlsplit(url)
        query = parse_qs(parsed_url.query)
        current_origin = query.get("dcity", [""])[0].strip().upper()
        current_destination = query.get("acity", [""])[0].strip().upper()
        target_origin = str(self._job_value(job, "origin")).upper()
        target_destination = str(self._job_value(job, "destination")).upper()
        if current_origin and current_destination:
            return current_origin == target_origin and current_destination == target_destination

        # Trip.com also uses route slugs such as `airfares-hkg-khh`.
        path = parsed_url.path.casefold()
        target_pair = f"{target_origin.casefold()}-{target_destination.casefold()}"
        reverse_pair = f"{target_destination.casefold()}-{target_origin.casefold()}"
        if target_pair in path:
            return True
        if reverse_pair in path:
            return False
        if re.search(rf"{target_origin.casefold()}[_/]to[_/]{target_destination.casefold()}", path):
            return True
        if re.search(rf"{target_destination.casefold()}[_/]to[_/]{target_origin.casefold()}", path):
            return False
        return None

    async def _extract_airline(self, card, card_text):
        """Read airline names from visible text and common accessibility metadata."""
        candidates = [card_text]
        airline_nodes = card.locator(",".join(AIRLINE_SELECTORS))
        for index in range(min(await airline_nodes.count(), 8)):
            node = airline_nodes.nth(index)
            text = (await node.inner_text()).strip()
            if text:
                candidates.append(text)
            for attribute in ("aria-label", "title", "alt"):
                value = await node.get_attribute(attribute)
                if value:
                    candidates.append(value.strip())

        # Some builds expose the carrier only on an image or accessibility
        # label, without a carrier-specific class or test id.
        metadata_nodes = card.locator("[aria-label], [title], img[alt]")
        for index in range(min(await metadata_nodes.count(), 20)):
            node = metadata_nodes.nth(index)
            for attribute in ("aria-label", "title", "alt"):
                value = await node.get_attribute(attribute)
                if value:
                    candidates.append(value.strip())

        for candidate in candidates:
            detected = detect_airline(candidate)
            if detected != "Unknown":
                return display_airline(detected)
        return "Unknown"

    async def _connect_to_cdp(self, playwright, cdp_url):
        """Connect to Chrome CDP, including Docker Desktop's host-header quirk."""
        from httpx import AsyncClient

        parsed = urlsplit(cdp_url)
        if parsed.scheme in {"ws", "wss"}:
            return await playwright.chromium.connect_over_cdp(cdp_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("TRIP_CDP_URL must be an http(s) or ws(s) CDP endpoint")

        # Chrome accepts the CDP HTTP endpoint only when the Host header matches
        # the listener name. Docker Desktop reaches it as host.docker.internal,
        # while Chrome was launched on localhost.
        host_header = os.getenv("TRIP_CDP_HOST_HEADER", "").strip()
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

        # Chrome reports a loopback WebSocket URL. Rewrite only its authority so
        # the container connects back through Docker Desktop to the host browser.
        websocket = urlsplit(websocket_url)
        websocket_host = parsed.hostname
        websocket_port = parsed.port or websocket.port
        if websocket_port:
            websocket_host = f"{websocket_host}:{websocket_port}"
        websocket_url = urlunsplit(
            (websocket.scheme, websocket_host, websocket.path, websocket.query, websocket.fragment)
        )
        log.info("Using Chrome CDP WebSocket at %s", websocket_url)
        return await playwright.chromium.connect_over_cdp(websocket_url, headers=headers)

    async def _wait_for_manual_verification(self, page, marker, connected_browser):
        if not connected_browser:
            return marker
        timeout_seconds = self._env_seconds("TRIP_MANUAL_VERIFY_TIMEOUT_SECONDS", 180.0)
        if timeout_seconds <= 0:
            return marker
        log.warning(
            "Trip.com verification detected (%s). Complete it manually in the connected Chrome within %.0f seconds.",
            marker, timeout_seconds,
        )
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            await page.wait_for_timeout(2000)
            body = (await page.locator("body").inner_text()).lower()
            marker = next((x for x in CAPTCHA_MARKERS if x in body), None)
            if marker is None:
                log.info("Trip.com verification page is no longer detected")
                return None
        log.error("Manual Trip.com verification timed out")
        return marker

    async def _capture(self,page,label):
        from datetime import datetime
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
        await page.screenshot(path=str(self.artifacts/f"{stamp}_{label}.png"),full_page=True)
        (self.artifacts/f"{stamp}_{label}.html").write_text(await page.content(),encoding="utf-8")
