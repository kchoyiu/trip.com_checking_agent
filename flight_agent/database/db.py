import sqlite3
from pathlib import Path
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_jobs (
 id INTEGER PRIMARY KEY, origin TEXT NOT NULL, destination TEXT NOT NULL,
 depart_date TEXT NOT NULL, return_date TEXT NOT NULL, adults INTEGER,
 currency TEXT, nonstop_only INTEGER, airline TEXT NOT NULL DEFAULT '', priority INTEGER DEFAULT 0,
 last_checked_at TEXT, created_at TEXT NOT NULL,
 UNIQUE(origin,destination,depart_date,return_date,adults,currency,nonstop_only));
CREATE TABLE IF NOT EXISTS flight_prices (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES search_jobs(id), observed_at TEXT NOT NULL,
 airline TEXT, flight_number TEXT, depart_time TEXT, arrive_time TEXT, duration_minutes INTEGER,
 stops INTEGER, price REAL NOT NULL, currency TEXT NOT NULL, url TEXT);
CREATE TABLE IF NOT EXISTS notifications (
 id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES search_jobs(id), sent_at TEXT NOT NULL,
 reason TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE);
"""
def now(): return datetime.now(timezone.utc).isoformat()
class Database:
    def __init__(self, path="data/flights.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path); self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA); self.conn.commit()
        self._ensure_column("search_jobs", "airline", "TEXT NOT NULL DEFAULT ''")

    def _ensure_column(self, table, column, definition):
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self.conn.commit()

    def upsert_job(self, j):
        return_date = j.return_date.isoformat() if j.return_date else ""
        airline = j.airline or ""
        self.conn.execute("""INSERT OR IGNORE INTO search_jobs(origin,destination,depart_date,return_date,adults,currency,nonstop_only,airline,priority,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",(j.origin,j.destination,j.depart_date.isoformat(),return_date,j.adults,j.currency,int(j.nonstop_only),airline,j.priority,now()))
        self.conn.commit()
        row = self.conn.execute("""SELECT * FROM search_jobs WHERE origin=? AND destination=? AND depart_date=? AND return_date=? AND adults=? AND currency=? AND nonstop_only=?""",
            (j.origin,j.destination,j.depart_date.isoformat(),return_date,j.adults,j.currency,int(j.nonstop_only))).fetchone()
        if row and row["airline"] != airline:
            self.conn.execute("UPDATE search_jobs SET airline=?, priority=? WHERE id=?", (airline, j.priority, row["id"]))
            self.conn.commit()
            row = self.conn.execute("SELECT * FROM search_jobs WHERE id=?", (row["id"],)).fetchone()
        return row
    def due_jobs(self, limit, job_ids=None):
        query = "SELECT * FROM search_jobs"
        params = []
        if job_ids is not None:
            job_ids = [int(job_id) for job_id in job_ids]
            if not job_ids:
                return []
            placeholders = ",".join("?" for _ in job_ids)
            query += f" WHERE id IN ({placeholders})"
            params.extend(job_ids)
        query += " ORDER BY priority DESC, COALESCE(last_checked_at,'') ASC LIMIT ?"
        params.append(limit)
        return self.conn.execute(query, params).fetchall()
    def add_prices(self, prices):
        for p in prices:
            self.conn.execute("""INSERT INTO flight_prices(job_id,observed_at,airline,flight_number,depart_time,arrive_time,duration_minutes,stops,price,currency,url)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",(p.job_id,now(),p.airline,p.flight_number,p.depart_time,p.arrive_time,p.duration_minutes,p.stops,p.price,p.currency,p.url))
        self.conn.commit()
    def mark_checked(self, job_id):
        self.conn.execute("UPDATE search_jobs SET last_checked_at=? WHERE id=?",(now(),job_id)); self.conn.commit()
    def stats(self, job_id, current):
        rows=self.conn.execute("SELECT price FROM flight_prices WHERE job_id=? ORDER BY observed_at DESC",(job_id,)).fetchall()
        vals=[r["price"] for r in rows]; a7=vals[:7]; a30=vals[:30]
        return {"current":current,"previous":vals[1] if len(vals)>1 else None,
                "avg7":sum(a7)/len(a7) if a7 else current,"avg30":sum(a30)/len(a30) if a30 else current,
                "low":min(vals+[current])}
    def notification_exists(self, fingerprint, hours):
        row=self.conn.execute("SELECT 1 FROM notifications WHERE fingerprint=? AND sent_at >= datetime('now', ?)",
                              (fingerprint,f"-{hours} hours")).fetchone()
        return row is not None
    def recent_notification(self, job_id, hours):
        row = self.conn.execute("SELECT 1 FROM notifications WHERE job_id=? AND sent_at >= datetime('now', ?)",
                                (job_id, f"-{hours} hours")).fetchone()
        return row is not None
    def record_notification(self, job_id, reason, fingerprint):
        self.conn.execute("INSERT OR IGNORE INTO notifications(job_id,sent_at,reason,fingerprint) VALUES(?,?,?,?)",
                          (job_id,now(),reason,fingerprint)); self.conn.commit()
