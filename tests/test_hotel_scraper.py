from pathlib import Path
from playwright.async_api import async_playwright
import pytest
from hotel_scraper import collect_hotels

HTML = """
<div class="hotel-card"><h2 class="hotel-name">Harbor Hotel</h2><span class="price">HK$1,234</span></div>
<div class="hotel-card"><h2 class="hotel-name">Central Inn</h2><span class="price">HK$988</span></div>
"""

@pytest.mark.asyncio
async def test_collect_hotels_from_fixture(tmp_path: Path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(HTML)
        rows = await collect_hotels(page, "高雄", tmp_path, 5000)
        await browser.close()
    assert [row.hotel_name for row in rows] == ["Harbor Hotel", "Central Inn"]
    assert rows[0].price == "HK$1,234"
    assert rows[0].score == 0
