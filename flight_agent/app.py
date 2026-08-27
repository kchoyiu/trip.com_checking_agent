import asyncio, logging, math, os, time
from dotenv import load_dotenv
from .config import load_settings
from .database.db import Database
from .engine.search_generator import generate_jobs
from .engine.deals import evaluate
from .notification.telegram import TelegramNotifier, format_deal
from .scraper.trip import TripScraper, BotDetected

log = logging.getLogger(__name__)


def _env_float(name, default):
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        parsed = float(value)
    except ValueError:
        log.warning("Invalid %s=%r; using %.2f", name, value, default)
        return default
    if not math.isfinite(parsed) or parsed < 0:
        log.warning("Invalid %s=%r; using %.2f", name, value, default)
        return default
    return parsed


def _run_once():
    settings=load_settings(); db=Database(); cfg=settings.raw
    for job in generate_jobs(cfg): db.upsert_job(job)
    rows=db.due_jobs(int(settings.queue.get("max_jobs_per_run",8)))
    scraper=TripScraper(settings.scraper)
    notifier=TelegramNotifier(os.getenv("TELEGRAM_BOT_TOKEN"),os.getenv("TELEGRAM_CHAT_ID"))
    for row in rows:
        try:
            prices=asyncio.run(scraper.search(row))
            for p in prices: p.job_id=row["id"]
            if prices:
                db.add_prices(prices); best=min(prices,key=lambda p:p.price)
                stats=db.stats(row["id"],best.price); deal=evaluate(stats,settings.alerts)
                fp=f"{row['id']}:{best.price}:{deal['score']}"
                cooldown = int(settings.alerts.get("cooldown_hours", 24))
                dedup = int(settings.alerts.get("dedup_hours", 72))
                if deal["should_notify"] and not db.recent_notification(row["id"], cooldown) and not db.notification_exists(fp, dedup):
                    if notifier.send(format_deal(row,best,deal)):
                        db.record_notification(row["id"],",".join(deal["reasons"]),fp)
            db.mark_checked(row["id"]); time.sleep(float(settings.queue.get("min_interval_seconds",8)))
        except BotDetected as e: logging.error(str(e)); break
        except Exception: logging.exception("Job %s failed",row["id"])


def run():
    load_dotenv()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    interval_hours = _env_float("CHECK_INTERVAL_HOURS", 0.0)
    run_once = os.getenv("RUN_ONCE", "0").strip().lower() in {"1", "true", "yes", "on"}

    while True:
        _run_once()
        if run_once or interval_hours <= 0:
            return
        logging.info("Next check scheduled in %.2f hours", interval_hours)
        try:
            time.sleep(interval_hours * 3600)
        except KeyboardInterrupt:
            logging.info("Stopping scheduled checks")
            return
