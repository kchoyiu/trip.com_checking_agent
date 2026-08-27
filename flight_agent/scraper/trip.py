import logging, os, time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .selectors import CAPTCHA_MARKERS, RESULT_SELECTORS, PRICE_SELECTORS
from ..models import FlightPrice
log=logging.getLogger(__name__)
class BotDetected(RuntimeError): pass
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
                    page = next(
                        (candidate for candidate in context.pages
                         if "trip.com" in candidate.url and "/flights" in candidate.url),
                        None,
                    )
                if page is None:
                    page = await context.new_page()
            else:
                browser = await p.chromium.launch(headless=self.cfg.get("headless",True))
                context = await browser.new_context(locale=self.cfg.get("locale","en-HK"))
                page = await context.new_page()
            try:
                if not (connected_browser and reuse_current_page and "/flights" in page.url):
                    await page.goto(self.cfg.get("base_url","https://www.trip.com/flights/"),
                        wait_until="domcontentloaded", timeout=self.cfg.get("timeout_ms",30000))
                body=(await page.locator("body").inner_text()).lower()
                marker = next((x for x in CAPTCHA_MARKERS if x in body), None)
                if marker:
                    marker = await self._wait_for_manual_verification(page, marker, connected_browser)
                if marker:
                    await self._capture(page,"captcha")
                    raise BotDetected(f"CAPTCHA/anti-bot page detected ({marker}); stopped without bypass")
                log.warning("Trip.com selectors are live-page dependent; validate selectors.py")
                found=page.locator(",".join(RESULT_SELECTORS))
                await found.first.wait_for(timeout=self.cfg.get("timeout_ms",30000))
                results=[]
                for i in range(min(await found.count(),20)):
                    card=found.nth(i); node=card.locator(",".join(PRICE_SELECTORS)).first
                    text=(await node.inner_text()) if await node.count() else ""
                    digits="".join(c for c in text.replace(",","") if c.isdigit() or c==".")
                    if not digits: continue
                    results.append(FlightPrice(0,"Unknown","Unknown","","",None,None,float(digits),job.currency))
                return results
            except BotDetected: raise
            except Exception:
                await self._capture(page,"error"); raise
            finally:
                if browser is not None and not connected_browser:
                    await browser.close()

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
