from __future__ import annotations
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS hotel_prices (
  id INTEGER PRIMARY KEY,
  city TEXT NOT NULL,
  hotel_name TEXT NOT NULL,
  price REAL NOT NULL,
  currency TEXT,
  rating REAL,
  score REAL,
  source_url TEXT,
  observed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hotel_notifications (
  id INTEGER PRIMARY KEY,
  city TEXT NOT NULL,
  hotel_name TEXT NOT NULL,
  previous_price REAL NOT NULL,
  current_price REAL NOT NULL,
  drop_percentage REAL NOT NULL,
  sent_at TEXT NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE
);
"""
def utc_now(): return datetime.now(timezone.utc).isoformat()

class HotelHistory:
    def __init__(self, path="data/hotels.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def previous_prices(self, city, hotel_names):
        if not hotel_names:
            return {}
        placeholders = ",".join("?" for _ in hotel_names)
        rows = self.conn.execute(
            f"""SELECT hotel_name, price FROM hotel_prices
                WHERE city=? AND hotel_name IN ({placeholders})
                ORDER BY observed_at DESC""",
            [city, *hotel_names],
        ).fetchall()
        result = {}
        for row in rows:
            result.setdefault(row["hotel_name"], row["price"])
        return result

    def add(self, city, rows):
        observed = utc_now()
        for row in rows:
            value = row.price_value
            if value is None:
                continue
            self.conn.execute(
                """INSERT INTO hotel_prices
                   (city,hotel_name,price,currency,rating,score,source_url,observed_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (city, row.hotel_name, value, row.currency, row.rating, row.score,
                 row.source_url, observed),
            )
        self.conn.commit()

    def notification_exists(self, fingerprint, hours=24):
        row = self.conn.execute(
            """SELECT 1 FROM hotel_notifications
               WHERE fingerprint=? AND sent_at >= datetime('now', ?)""",
            (fingerprint, f"-{hours} hours"),
        ).fetchone()
        return row is not None

    def record_notification(self, city, row, previous, drop, fingerprint):
        self.conn.execute(
            """INSERT OR IGNORE INTO hotel_notifications
               (city,hotel_name,previous_price,current_price,drop_percentage,sent_at,fingerprint)
               VALUES(?,?,?,?,?,?,?)""",
            (city, row.hotel_name, previous, row.price_value, drop, utc_now(), fingerprint),
        )
        self.conn.commit()

