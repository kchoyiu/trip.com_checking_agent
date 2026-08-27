"""Airline aliases and matching helpers for configured result filters."""

import re
from typing import Optional


_CATHAY_ALIASES = (
    "cathay pacific",
    "cathay",
    "國泰航空",
    "國泰",
    "国泰航空",
    "国泰",
)


def configured_airlines(cfg: dict) -> tuple[str, ...]:
    """Return configured airline filters as a normalized tuple of labels."""
    raw = cfg.get("airlines", cfg.get("airline", ()))
    if raw is None or raw == "":
        return ()
    if isinstance(raw, str):
        return (raw.strip(),) if raw.strip() else ()
    return tuple(str(value).strip() for value in raw if str(value).strip())


def canonical_airline(value: Optional[str]) -> str:
    """Map common airline aliases to a stable comparison value."""
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    if any(alias.casefold() in text for alias in _CATHAY_ALIASES):
        return "cathay pacific"
    if re.search(r"(?:^|[^a-z0-9])cx(?:$|[^a-z0-9])", text):
        return "cathay pacific"
    return re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", text).strip()


def display_airline(value: Optional[str]) -> str:
    """Return a readable airline label while preserving unknown labels."""
    return "Cathay Pacific" if canonical_airline(value) == "cathay pacific" else str(value or "Unknown").strip() or "Unknown"


def airline_matches(actual: Optional[str], preferred: tuple[str, ...]) -> bool:
    """Match a scraped airline against configured aliases; unknown never matches."""
    if not preferred:
        return True
    actual_canonical = canonical_airline(actual)
    if not actual_canonical or actual_canonical == "unknown":
        return False
    return any(actual_canonical == canonical_airline(expected) for expected in preferred)


def detect_airline(text: str) -> str:
    """Detect a configured/common airline name from a flight card's text."""
    lowered = " ".join(str(text or "").casefold().split())
    if any(alias.casefold() in lowered for alias in _CATHAY_ALIASES) or re.search(r"(?:^|[^a-z0-9])cx(?:$|[^a-z0-9])", lowered):
        return "Cathay Pacific"
    return "Unknown"
