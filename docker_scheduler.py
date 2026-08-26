"""Keep the hotel scraper alive for Docker restart policies.

The scraper itself still performs one bounded scan and exits. This wrapper
runs one scan immediately, waits for the configured interval, and starts the
next scan. It never bypasses CAPTCHA or anti-bot pages; a failed scan is
logged and the next scheduled attempt happens after the normal interval.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlparse
from dotenv import load_dotenv

LOG = logging.getLogger("docker_scheduler")


@dataclass(frozen=True)
class TargetGroup:
    city: str
    city_id: str
    city_slug: str
    hotel_names: str


@dataclass(frozen=True)
class TargetDetail:
    city: str
    hotel_name: str
    detail_url: str


def parse_target_groups(value: str) -> tuple[TargetGroup, ...]:
    groups = []
    for raw_group in value.split(";"):
        raw_group = raw_group.strip()
        if not raw_group:
            continue
        parts = [part.strip() for part in raw_group.split("|", 3)]
        if len(parts) != 4 or not all(parts):
            raise ValueError(
                "HOTEL_TARGETS entries must be city|city_id|city_slug|hotel1,hotel2"
            )
        groups.append(TargetGroup(*parts))
    return tuple(groups)


def parse_target_details(value: str) -> tuple[TargetDetail, ...]:
    """Parse exact hotel detail targets: city|hotel name|absolute URL."""
    details = []
    for raw_detail in value.split(";"):
        raw_detail = raw_detail.strip()
        if not raw_detail:
            continue
        parts = [part.strip() for part in raw_detail.split("|", 2)]
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                "HOTEL_TARGET_DETAILS entries must be city|hotel_name|detail_url"
            )
        parsed = urlparse(parts[2])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("HOTEL_TARGET_DETAILS detail_url must be an absolute http(s) URL")
        details.append(TargetDetail(*parts))
    return tuple(details)


def detail_file_stem(detail_url: str, index: int, hotel_name: str = "") -> str:
    """Use the hotel slug from Trip.com's URL for readable mounted filenames."""
    parsed = urlparse(detail_url)
    path = unquote(parsed.path).rstrip("/")
    candidate = path.rsplit("/", 1)[-1] if path else ""
    if candidate.casefold() in {"detail", "details"}:
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        candidate = f"hotel-{query.get('hotelId', '')}".strip("-")
        if candidate == "hotel":
            candidate = hotel_name
    candidate = re.sub(r"[^A-Za-z0-9_-]+", "-", candidate).strip("-")
    return candidate or f"hotel-{index}"


def scraper_command(environment=None) -> list[str]:
    environment = environment or os.environ
    command = [
        sys.executable,
        "hotel_scraper.py",
        "--city",
        environment.get("HOTEL_CITY", "高雄"),
        "--check-in",
        environment.get("HOTEL_CHECK_IN", "2026-10-04"),
        "--check-out",
        environment.get("HOTEL_CHECK_OUT", "2026-10-07"),
        "--output",
        environment.get("HOTEL_OUTPUT", "data/data.csv"),
        "--artifacts",
        environment.get("HOTEL_ARTIFACTS", "artifacts/hotels"),
        "--history-db",
        environment.get("HOTEL_HISTORY_DB", "data/hotels.db"),
        "--timeout-ms",
        environment.get("HOTEL_TIMEOUT_MS", "30000"),
    ]
    if environment.get("HOTEL_HEADFUL", "false").strip().casefold() in {
        "1", "true", "yes", "on"
    }:
        command.append("--headful")
    return command


def interval_seconds() -> float:
    try:
        hours = float(os.getenv("HOTEL_INTERVAL_HOURS", "6"))
    except ValueError:
        hours = 6.0
    return max(hours, 0.1) * 3600


def main(once: bool = False) -> int:
    # Docker Compose loads .env for us; local Windows runs need to load it here.
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()

    def request_stop(signum, _frame):
        LOG.info("Received signal %s; stopping after the current scan", signum)
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    while not stop.is_set():
        try:
            details = parse_target_details(os.getenv("HOTEL_TARGET_DETAILS", ""))
            groups = parse_target_groups(os.getenv("HOTEL_TARGETS", ""))
        except ValueError as exc:
            LOG.error("%s", exc)
            return 2

        if details:
            jobs = []
            for index, detail in enumerate(details, start=1):
                stem = detail_file_stem(detail.detail_url, index, detail.hotel_name)
                jobs.append((
                    detail.city,
                    detail.hotel_name,
                    {
                        "HOTEL_CITY": detail.city,
                        "HOTEL_TARGET_NAMES": detail.hotel_name,
                        "HOTEL_DETAIL_URL": detail.detail_url,
                        "HOTEL_OUTPUT": f"data/{stem}.csv",
                        "HOTEL_ARTIFACTS": f"artifacts/hotels/{stem}",
                    },
                ))
        else:
            if not groups:
                groups = (TargetGroup(
                    os.getenv("HOTEL_CITY", "高雄"),
                    os.getenv("HOTEL_CITY_ID", "720"),
                    os.getenv("HOTEL_CITY_SLUG", "kaohsiung"),
                    os.getenv("HOTEL_TARGET_NAMES", ""),
                ),)
            jobs = []
            for group in groups:
                jobs.append((
                    group.city,
                    group.hotel_names or "all hotels",
                    {
                        "HOTEL_CITY": group.city,
                        "HOTEL_CITY_ID": group.city_id,
                        "HOTEL_CITY_SLUG": group.city_slug,
                        "HOTEL_TARGET_NAMES": group.hotel_names,
                        "HOTEL_DETAIL_URL": "",
                        "HOTEL_OUTPUT": f"data/{group.city_slug}.csv",
                        "HOTEL_ARTIFACTS": f"artifacts/hotels/{group.city_slug}",
                    },
                ))

        overall_code = 0
        for city, label, settings in jobs:
            if stop.is_set():
                break
            group_environment = os.environ.copy()
            group_environment.update(settings)
            LOG.info(
                "Starting scheduled hotel scan for %s (%s to %s): %s",
                city,
                group_environment.get("HOTEL_CHECK_IN", "2026-10-04"),
                group_environment.get("HOTEL_CHECK_OUT", "2026-10-07"),
                label,
            )
            try:
                result = subprocess.run(
                    scraper_command(group_environment),
                    env=group_environment,
                    check=False,
                )
            except OSError:
                LOG.exception("Could not start hotel scraper for %s", city)
                result_code = 1
            else:
                result_code = result.returncode

            if result_code == 0:
                LOG.info("Hotel scan completed successfully for %s", city)
            else:
                overall_code = result_code or 1
                LOG.error("Hotel scan for %s ended with exit code %s; no bypass or rapid retry will be attempted", city, result_code)

        if overall_code:
            LOG.error("One or more hotel groups failed; all groups will wait for the normal interval")

        if once:
            LOG.info("One-shot hotel scheduler completed")
            break
        if not stop.is_set():
            wait_seconds = interval_seconds()
            LOG.info("Next hotel scan will start in %.1f hours", wait_seconds / 3600)
            stop.wait(wait_seconds)

    LOG.info("Docker hotel scheduler stopped")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the hotel scheduler")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one complete scan cycle and exit instead of waiting for the next interval",
    )
    raise SystemExit(main(parser.parse_args().once))
