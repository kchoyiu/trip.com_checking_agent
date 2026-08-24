import asyncio, logging, os, time
from dotenv import load_dotenv
from .config import load_settings
from .database.db import Database
from .engine.search_generator import generate_jobs
from .engine.deals import evaluate
from .notification.telegram import TelegramNotifier, format_deal
from .scraper.trip import TripScraper, BotDetected

def run():
    load_dotenv(); logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
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
