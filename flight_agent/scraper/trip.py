import logging
from pathlib import Path
from .selectors import CAPTCHA_MARKERS, RESULT_SELECTORS, PRICE_SELECTORS
from ..models import FlightPrice
log=logging.getLogger(__name__)
class BotDetected(RuntimeError): pass
class TripScraper:
    def __init__(self, cfg, artifact_dir="artifacts"):
        self.cfg=cfg; self.artifacts=Path(artifact_dir); self.artifacts.mkdir(parents=True,exist_ok=True)
    async def search(self, job):
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser=await p.chromium.launch(headless=self.cfg.get("headless",True))
            page=await browser.new_page(locale=self.cfg.get("locale","en-HK"))
            try:
                await page.goto(self.cfg.get("base_url","https://www.trip.com/flights/"),
                    wait_until="domcontentloaded", timeout=self.cfg.get("timeout_ms",30000))
                body=(await page.locator("body").inner_text()).lower()
                marker = next((x for x in CAPTCHA_MARKERS if x in body), None)
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
            finally: await browser.close()
    async def _capture(self,page,label):
        from datetime import datetime
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
        await page.screenshot(path=str(self.artifacts/f"{stamp}_{label}.png"),full_page=True)
        (self.artifacts/f"{stamp}_{label}.html").write_text(await page.content(),encoding="utf-8")
