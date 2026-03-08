"""SQLite storage for daily summaries and 7-day retention (weekly summary support)."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .lib import logger

DEFAULT_DB_PATH = Path("data/summaries.db")
RETENTION_DAYS = 7


def get_db_path() -> Path:
    """Return DB path from env or default."""
    return Path(os.environ.get("DB_PATH", str(DEFAULT_DB_PATH)))


@contextmanager
def get_connection():
    """Yield a DB connection with row factory for dict-like access."""
    path = get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they do not exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                summary_date DATE NOT NULL UNIQUE,
                summary_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_daily_summaries_date
            ON daily_summaries(summary_date DESC)
        """)
    logger.info("db initialized", path=str(get_db_path()))


def save_daily_summary(summary_text: str, summary_date: datetime | None = None) -> None:
    """Store a daily summary. Uses today UTC if summary_date not provided."""
    if summary_date is None:
        summary_date = datetime.now(timezone.utc)
    date_str = summary_date.strftime("%Y-%m-%d")
    created = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO daily_summaries (summary_date, summary_text, created_at) VALUES (?, ?, ?)",
            (date_str, summary_text, created),
        )
    logger.info("saved daily summary", date=date_str)


def get_last_n_daily_summaries(n: int = 7) -> list[tuple[str, str]]:
    """Return last N daily summaries as (date, summary_text)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT summary_date, summary_text FROM daily_summaries ORDER BY summary_date DESC LIMIT ?",
            (n,),
        ).fetchall()
    return [(r["summary_date"], r["summary_text"]) for r in rows]


def cleanup_old_summaries() -> None:
    """Remove summaries older than RETENTION_DAYS."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    with get_connection() as conn:
        cur = conn.execute("DELETE FROM daily_summaries WHERE summary_date < ?", (cutoff,))
        deleted = cur.rowcount
    if deleted > 0:
        logger.info("cleaned up old summaries", deleted=deleted, cutoff=cutoff)
