"""SQLite persistence for summaries and per-user channel preferences."""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_channel_preferences (
                user_id INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (user_id, guild_id, channel_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_channel_preferences_user
            ON user_channel_preferences(user_id, guild_id)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_channel_state (
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                last_seen_utc TEXT NOT NULL,
                PRIMARY KEY (user_id, channel_id)
            )
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


def set_user_channel_preferences(user_id: int, guild_id: int, channel_ids: Iterable[int]) -> list[int]:
    """Replace a user's selected channels for a guild."""
    normalized = sorted(set(int(ch) for ch in channel_ids))
    created = datetime.now(timezone.utc).isoformat()

    with get_connection() as conn:
        conn.execute(
            "DELETE FROM user_channel_preferences WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        )
        if normalized:
            conn.executemany(
                """
                INSERT INTO user_channel_preferences (user_id, guild_id, channel_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [(user_id, guild_id, ch_id, created) for ch_id in normalized],
            )

    logger.info(
        "saved user channel preferences",
        user_id=user_id,
        guild_id=guild_id,
        channel_count=len(normalized),
    )
    return normalized


def get_user_channel_preferences(user_id: int, guild_id: int) -> list[int]:
    """Return a user's selected channel IDs for a guild."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT channel_id
            FROM user_channel_preferences
            WHERE user_id = ? AND guild_id = ?
            ORDER BY channel_id ASC
            """,
            (user_id, guild_id),
        ).fetchall()
    return [int(r["channel_id"]) for r in rows]


def _parse_iso_utc(ts: str) -> datetime:
    """Parse stored timestamp and coerce to timezone-aware UTC."""
    parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def get_user_channel_checkpoints(user_id: int, channel_ids: Iterable[int]) -> dict[int, datetime]:
    """Return checkpoint timestamps for the given user and channels."""
    normalized = sorted(set(int(ch) for ch in channel_ids))
    if not normalized:
        return {}

    placeholders = ",".join("?" for _ in normalized)
    params = [user_id, *normalized]
    query = (
        f"SELECT channel_id, last_seen_utc FROM user_channel_state "
        f"WHERE user_id = ? AND channel_id IN ({placeholders})"
    )
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()

    checkpoints: dict[int, datetime] = {}
    for row in rows:
        try:
            checkpoints[int(row["channel_id"])] = _parse_iso_utc(str(row["last_seen_utc"]))
        except ValueError:
            logger.warning(
                "invalid checkpoint timestamp",
                user_id=user_id,
                channel_id=int(row["channel_id"]),
                value=str(row["last_seen_utc"]),
            )
    return checkpoints


def upsert_user_channel_checkpoint(user_id: int, channel_id: int, last_seen_utc: datetime) -> None:
    """Persist per-user channel checkpoint for incremental collection."""
    utc_value = last_seen_utc.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO user_channel_state (user_id, channel_id, last_seen_utc)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id, channel_id)
            DO UPDATE SET last_seen_utc = excluded.last_seen_utc
            """,
            (user_id, channel_id, utc_value),
        )
