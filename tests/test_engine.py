from datetime import date
from flight_agent.engine.search_generator import generate_jobs
from flight_agent.engine.airlines import airline_matches, detect_airline
from flight_agent.models import SearchJob
from flight_agent.scraper.trip import TripScraper
from flight_agent.database.db import Database
from flight_agent.engine.deals import evaluate

def test_generator_count():
    c={"origin":"HKG","destinations":["KIX"],"departure":{"from":"2026-01-01","to":"2026-01-02"},"trip_length":{"min_days":3,"max_days":4}}
    jobs=list(generate_jobs(c))
    assert len(jobs)==4
    assert jobs[0].return_date==date(2026,1,4)

def test_deal_score_and_drop():
    d=evaluate({"current":800,"previous":1000,"avg7":900,"avg30":950,"low":800},{"price_threshold":850,"drop_percentage":15,"deal_score":75})
    assert d["drop_percentage"]==20
    assert d["should_notify"] and d["score"]==100


def test_explicit_one_way_legs_and_airline():
    jobs = list(generate_jobs({
        "legs": [
            {"origin": "HKG", "destination": "KHH", "depart_date": "2026-10-04"},
            {"origin": "TPE", "destination": "HKG", "depart_date": "2026-10-11"},
        ],
        "adults": 1,
        "currency": "HKD",
        "airline": "國泰航空",
    }))
    assert [(job.origin, job.destination, job.depart_date, job.return_date) for job in jobs] == [
        ("HKG", "KHH", date(2026, 10, 4), None),
        ("TPE", "HKG", date(2026, 10, 11), None),
    ]
    assert all(job.airline == "國泰航空" for job in jobs)
    assert airline_matches("Cathay Pacific", ("國泰航空",))
    assert not airline_matches("Unknown", ("Cathay Pacific",))
    assert detect_airline("國泰航空 CX 123") == "Cathay Pacific"


def test_one_way_search_url():
    job = SearchJob("HKG", "KHH", date(2026, 10, 4), None, 1, "HKD")
    url = TripScraper({"base_url": "https://www.trip.com/flights/"})._build_search_url(job)
    assert "dcity=hkg" in url and "acity=khh" in url
    assert "ddate=2026-10-04" in url and "triptype=oneway" in url
    assert TripScraper._extract_price("Cathay | Pacific | HK$1,234") == 1234


def test_manual_page_route_guard():
    scraper = TripScraper({})
    target = SearchJob("HKG", "KHH", date(2026, 10, 4))
    assert scraper._page_route_matches_job(
        "https://www.trip.com/flights/?dcity=hkg&acity=khh&ddate=2026-10-04", target
    ) is True
    assert scraper._page_route_matches_job(
        "https://www.trip.com/flights/?dcity=tpe&acity=hkg&ddate=2026-10-11", target
    ) is False
    assert scraper._page_route_matches_job(
        "https://www.trip.com/flights/flight-route/hong-kong-to-kaohsiung/airfares-hkg-khh/", target
    ) is True
    assert scraper._page_route_matches_job("https://www.trip.com/flights/", target) is None


def test_database_stores_one_way_job_and_filters_active_ids(tmp_path):
    db = Database(tmp_path / "flights.db")
    row = db.upsert_job(SearchJob("HKG", "KHH", date(2026, 10, 4), None, 1, "HKD", False, "Cathay Pacific"))
    assert row["return_date"] == ""
    assert row["airline"] == "Cathay Pacific"
    assert [active["id"] for active in db.due_jobs(10, [row["id"]])] == [row["id"]]
    assert db.due_jobs(10, [999999]) == []
