from hotel_history import HotelHistory
from hotel_scraper import Hotel

def test_history_remembers_previous_price(tmp_path):
    db = HotelHistory(str(tmp_path / "hotels.db"))
    old = Hotel("Top Hotel", "TWD1000", "TWD", 9.0, 90.0, "", "")
    db.add("高雄", [old])
    assert db.previous_prices("高雄", ["Top Hotel"])["Top Hotel"] == 1000
    new = Hotel("Top Hotel", "TWD800", "TWD", 9.0, 92.0, "", "")
    db.add("高雄", [new])
    assert db.previous_prices("高雄", ["Top Hotel"])["Top Hotel"] == 800

