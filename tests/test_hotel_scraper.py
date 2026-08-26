from pathlib import Path
from datetime import date
from playwright.async_api import async_playwright
import pytest
from hotel_scraper import (
    collect_hotel_detail,
    collect_hotels,
    detail_page_matches,
    detail_page_url,
    hotel_name_matches,
    parse_target_names,
)
from flight_agent.notification.hotel_telegram import format_hotel_prices
from docker_scheduler import detail_file_stem, parse_target_details, parse_target_groups

HTML = """
<div class="hotel-card"><h2 class="hotel-name">Harbor Hotel</h2><span class="price">HK$1,234</span></div>
<div class="hotel-card"><h2 class="hotel-name">Central Inn</h2><span class="price">HK$988</span></div>
"""

DETAIL_HTML = """
<h1 class="hotelNameRow_hotelOverview_name__test">Harbor Hotel</h1>
<div class="components_scoreBlock__test"><div class="components_score__test">9.2</div></div>
<div class="page_detailRoomListVerticalContent__test">
  <div class="roomCard__test"><h2>Deluxe Room</h2><span class="roomPrice__test">HKD 572</span></div>
  <div class="roomCard__test"><h2>Family Room</h2><span class="roomPrice__test">HKD 800</span></div>
</div>
"""

DETAIL_OFFER_HTML = """
<h1 class="hotelNameRow_hotelOverview_name__test">Harbor Hotel</h1>
<div class="page_detailRoomListVerticalContent__test"><h2>選擇房間</h2></div>
<aside class="offer-card__test"><strong>HK$509</strong></aside>
<div>早餐：成人 HK$135.48／人</div>
"""

DETAIL_LIVE_PRICE_HTML = """
<h1 class="hotelNameRow_hotelOverview_name__test">Harbor Hotel</h1>
<div class="page_detailRoomListVerticalContent__test">
  <div class="saleRoomItemBox-priceBox-priceExplain__test">總額：HK$1,764（連稅及附加費）</div>
  <div class="saleRoomItemBox-priceBox-deletePrice__test">HK$671</div>
  <div class="saleRoomItemBox-priceBox-displayPrice__test">HK$509</div>
</div>
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

@pytest.mark.asyncio
async def test_collect_hotel_detail_from_fixture(tmp_path: Path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(DETAIL_HTML)
        rows = await collect_hotel_detail(
            page, "高雄", tmp_path, 5000, ("Harbor Hotel",),
            "https://example.test/hotel/harbor-hotel",
        )
        await browser.close()
    assert rows[0].hotel_name == "Harbor Hotel"
    assert rows[0].price == "HKD 572"
    assert rows[0].rating == 9.2

@pytest.mark.asyncio
async def test_collect_hotel_detail_falls_back_to_visible_offer_card(tmp_path: Path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(DETAIL_OFFER_HTML)
        import os
        old_currency = os.environ.get("HOTEL_CURRENCY")
        os.environ["HOTEL_CURRENCY"] = "HKD"
        try:
            rows = await collect_hotel_detail(
                page, "高雄", tmp_path, 5000, ("Harbor Hotel",),
                "https://example.test/hotel/harbor-hotel",
            )
        finally:
            if old_currency is None:
                os.environ.pop("HOTEL_CURRENCY", None)
            else:
                os.environ["HOTEL_CURRENCY"] = old_currency
        await browser.close()
    assert rows[0].price == "HK$509"


@pytest.mark.asyncio
async def test_detail_prefers_trip_current_price_over_original_and_total(tmp_path: Path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(DETAIL_LIVE_PRICE_HTML)
        rows = await collect_hotel_detail(
            page, "高雄", tmp_path, 5000, ("Harbor Hotel",),
            "https://example.test/hotel/harbor-hotel",
        )
        await browser.close()
    assert rows[0].price == "HK$509"

def test_target_hotel_names_are_normalized():
    assert parse_target_names(" Harbor Hotel, ,Central Inn ") == ("Harbor Hotel", "Central Inn")
    assert hotel_name_matches("高雄 喜達絲飯店", ("高雄喜達絲飯店",))
    assert hotel_name_matches("傑仕堡有氧飯店", ("傑仕堡有氧酒店",))

def test_current_price_message_marks_a_drop():
    row = type("Row", (), {
        "hotel_name": "Harbor Hotel",
        "price": "HK$900",
        "price_value": 900,
        "score": 88.5,
        "source_url": "https://example.test/hotel",
    })()
    message = format_hotel_prices("高雄", [row], {"Harbor Hotel": 1000}, 10)
    assert "Harbor Hotel 🔥" in message
    assert "drop: 10.0%" in message


def test_current_price_message_marks_an_increase_clearly():
    row = type("Row", (), {
        "hotel_name": "Harbor Hotel",
        "price": "HK$1200",
        "price_value": 1200,
        "score": 80.0,
        "source_url": "https://example.test/hotel",
    })()
    message = format_hotel_prices("高雄", [row], {"Harbor Hotel": 1000}, 10)
    assert "increase: 20.0%" in message

def test_multi_city_target_groups():
    groups = parse_target_groups("高雄|720|kaohsiung|高雄喜迎旅店;台北|617|taipei|路徒PLUS行旅-主題館")
    assert groups[0].city_id == "720"
    assert groups[1].city_slug == "taipei"

def test_exact_hotel_detail_targets_and_url_parameters():
    details = parse_target_details(
        "高雄|喜迎旅店|https://tw.trip.com/hotels/kaohsiung-hotel-detail-7932167/greet-inn/"
    )
    assert details[0].hotel_name == "喜迎旅店"
    assert detail_file_stem(details[0].detail_url, 1) == "greet-inn"
    url = detail_page_url(
        details[0].detail_url,
        date(2026, 10, 4),
        date(2026, 10, 7),
        "HKD",
        2,
        0,
    )
    assert "checkIn=2026-10-04" in url
    assert "checkOut=2026-10-07" in url
    assert "curr=HKD" in url
    desktop_url = "https://hk.trip.com/hotels/detail/?cityId=720&hotelId=7932167"
    assert detail_file_stem(desktop_url, 1) == "hotel-7932167"


def test_connected_chrome_detail_page_matches_requested_dates_and_hotel():
    target = detail_page_url(
        "https://hk.trip.com/hotels/detail/?cityId=720&hotelId=7932167",
        date(2026, 10, 4), date(2026, 10, 7), "HKD", 2, 0,
    )
    current = (
        "https://hk.trip.com/hotels/detail/?cityId=720&hotelId=7932167"
        "&checkIn=2026-10-04&checkOut=2026-10-07&locale=zh-HK"
    )
    other_hotel = current.replace("7932167", "63341173")
    assert detail_page_matches(current, target)
    assert not detail_page_matches(other_hotel, target)
