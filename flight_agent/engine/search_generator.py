from datetime import date, timedelta
from ..models import SearchJob

def generate_jobs(cfg: dict):
    dep = cfg["departure"]
    start, end = date.fromisoformat(dep["from"]), date.fromisoformat(dep["to"])
    lengths = cfg.get("trip_length", {})
    lo = int(lengths.get("min_days", 1))
    hi = int(lengths.get("max_days", lo))
    for destination in cfg.get("destinations", []):
        d = start
        while d <= end:
            for days in range(lo, hi + 1):
                yield SearchJob(cfg["origin"], destination, d, d + timedelta(days=days),
                    int(cfg.get("adults", 1)), cfg.get("currency", "HKD"),
                    bool(cfg.get("nonstop_only", False)))
            d += timedelta(days=1)
