from __future__ import annotations
from dataclasses import replace
import re
from typing import Iterable

def price_value(text: str) -> float | None:
    cleaned = text.replace(",", "")
    match = re.search(r"\d+(?:\.\d+)?", cleaned)
    return float(match.group()) if match else None

def hotel_score(rating: float | None, price: float | None, lowest: float | None, highest: float | None) -> float:
    if price is None:
        return 0.0
    if lowest is None or highest is None or highest == lowest:
        price_part = 30.0
    else:
        price_part = 30.0 * (highest - price) / (highest - lowest)
    if rating is None:
        return round(price_part, 1)
    return round(max(0.0, min(70.0, rating / 10.0 * 70.0)) + price_part, 1)

def rank_hotels(rows: Iterable):
    rows = list(rows)
    values = [price_value(row.price) for row in rows]
    valid = [value for value in values if value is not None]
    lowest, highest = (min(valid), max(valid)) if valid else (None, None)
    ranked = []
    for row in rows:
        score = hotel_score(row.rating, price_value(row.price), lowest, highest)
        ranked.append(replace(row, score=score))
    return sorted(ranked, key=lambda row: (-row.score, price_value(row.price) or float("inf")))

def top_hotel(rows):
    ranked = rank_hotels(rows)
    return ranked[0] if ranked else None

