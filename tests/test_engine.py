from datetime import date
from flight_agent.engine.search_generator import generate_jobs
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

