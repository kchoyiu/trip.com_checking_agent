from hotel_engine import price_value, rank_hotels, top_hotel
from hotel_scraper import Hotel

def hotel(name, price, rating):
    return Hotel(name, price, "TWD", rating, 0.0, "", "")

def test_rank_prefers_high_rating_with_price_factor():
    rows = rank_hotels([hotel("A", "TWD798", 8.6), hotel("B", "TWD5000", 9.2)])
    assert top_hotel(rows).hotel_name == "A"
    assert 0 <= rows[0].score <= 100

def test_price_value():
    assert price_value("TWD1,868") == 1868
    assert price_value("unknown") is None
