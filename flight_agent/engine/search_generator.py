from datetime import date, timedelta
from typing import Optional
from ..models import SearchJob


def _airline_value(cfg: dict, leg: Optional[dict] = None) -> str:
    raw = (leg or {}).get("airline", cfg.get("airline", cfg.get("airlines", "")))
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(value).strip() for value in raw if str(value).strip())
    return str(raw or "").strip()


def _date(value, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}") from exc


def generate_jobs(cfg: dict):
    # Explicit legs are useful for open-jaw and multi-city trips where the
    # return origin is not the outbound destination.
    legs = cfg.get("legs") or cfg.get("segments")
    if legs:
        for index, leg in enumerate(legs, start=1):
            if not isinstance(leg, dict):
                raise ValueError(f"Flight leg {index} must be a mapping")
            origin = str(leg.get("origin", "")).strip().upper()
            destination = str(leg.get("destination", "")).strip().upper()
            if not origin or not destination:
                raise ValueError(f"Flight leg {index} needs origin and destination")
            return_date = leg.get("return_date")
            yield SearchJob(
                origin=origin,
                destination=destination,
                depart_date=_date(leg.get("depart_date"), f"legs[{index - 1}].depart_date"),
                return_date=_date(return_date, f"legs[{index - 1}].return_date") if return_date else None,
                adults=int(leg.get("adults", cfg.get("adults", 1))),
                currency=str(leg.get("currency", cfg.get("currency", "HKD"))),
                nonstop_only=bool(leg.get("nonstop_only", cfg.get("nonstop_only", False))),
                airline=_airline_value(cfg, leg),
                priority=int(leg.get("priority", 0)),
            )
        return

    # Backward-compatible generator for the original round-trip config.
    dep = cfg["departure"]
    start, end = date.fromisoformat(dep["from"]), date.fromisoformat(dep["to"])
    lengths = cfg.get("trip_length", {})
    lo = int(lengths.get("min_days", 1))
    hi = int(lengths.get("max_days", lo))
    for destination in cfg.get("destinations", []):
        d = start
        while d <= end:
            for days in range(lo, hi + 1):
                yield SearchJob(
                    origin=cfg["origin"],
                    destination=destination,
                    depart_date=d,
                    return_date=d + timedelta(days=days),
                    adults=int(cfg.get("adults", 1)),
                    currency=cfg.get("currency", "HKD"),
                    nonstop_only=bool(cfg.get("nonstop_only", False)),
                    airline=_airline_value(cfg),
                )
            d += timedelta(days=1)
