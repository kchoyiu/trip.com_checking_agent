from dataclasses import dataclass
from datetime import date
from typing import Optional

@dataclass(frozen=True)
class SearchJob:
    origin: str
    destination: str
    depart_date: date
    return_date: date
    adults: int = 1
    currency: str = "HKD"
    nonstop_only: bool = False
    priority: int = 0
    id: Optional[int] = None

@dataclass
class FlightPrice:
    job_id: int
    airline: str
    flight_number: str
    depart_time: str
    arrive_time: str
    duration_minutes: Optional[int]
    stops: Optional[int]
    price: float
    currency: str
    url: str = ""

