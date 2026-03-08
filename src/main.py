"""Entrypoint: Discord bot + daily scheduler + weekly summary."""

import asyncio
import os
import signal
import sys
from datetime import datetime, timezone

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from .bot import collect_messages
from .db import cleanup_old_summaries, get_last_n_daily_summaries, init_db, save_daily_summary
from .lib import configure_logging, logger
from .state import load_last_run, save_last_run
from .summarizer import summarize_messages

load_dotenv()
configure_logging()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID")
SUMMARY_HOUR = int(os.environ.get("SUMMARY_UTC_HOUR", "8"))
SUMMARY_MINUTE = int(os.environ.get("SUMMARY_UTC_MINUTE", "0"))
SUMMARY_WEEKLY_DAY = int(os.environ.get("SUMMARY_WEEKLY_DAY", "6"))  # 0=Mon, 6=Sun
SUMMARY_WEEKLY_HOUR = int(os.environ.get("SUMMARY_WEEKLY_UTC_HOUR", "9"))
SUMMARY_WEEKLY_MINUTE = int(os.environ.get("SUMMARY_WEEKLY_UTC_MINUTE", "0"))


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

client = discord.Client(intents=intents)
scheduler = AsyncIOScheduler()


async def run_daily_summary() -> None:
    """Collect messages, summarize, DM user, save state and DB."""
    if not DISCORD_TOKEN or not DISCORD_USER_ID:
        logger.error("DISCORD_TOKEN and DISCORD_USER_ID required")
        return
    user_id = int(DISCORD_USER_ID)
    user = await client.fetch_user(user_id)
    if user is None:
        logger.error("could not fetch user", user_id=user_id)
        return

    now_utc = datetime.now(timezone.utc)
    aggregated = await collect_messages(client)
    summary = summarize_messages(aggregated)

    try:
        await user.send(f"**Daily Summary** ({now_utc.strftime('%Y-%m-%d %H:%M UTC')})\n\n{summary}")
    except discord.Forbidden:
        logger.error("cannot DM user - DMs may be disabled", user_id=user_id)
        return

    save_last_run(now_utc)
    save_daily_summary(summary, now_utc)
    cleanup_old_summaries()
    logger.info("daily summary sent", user_id=user_id)


async def run_weekly_summary() -> None:
    """Aggregate last 7 daily summaries and send weekly digest."""
    if not DISCORD_TOKEN or not DISCORD_USER_ID:
        return
    user_id = int(DISCORD_USER_ID)
    user = await client.fetch_user(user_id)
    if user is None:
        logger.error("could not fetch user for weekly summary", user_id=user_id)
        return

    rows = get_last_n_daily_summaries(7)
    if not rows:
        await user.send("**Weekly Summary**: No daily summaries in the last 7 days.")
        return

    combined = "\n\n---\n\n".join(
        f"**{date}**\n{text}" for date, text in reversed(rows)
    )

    from .summarizer import summarize_messages
    weekly = summarize_messages(
        f"Weekly digest. Summarize these daily summaries into a concise weekly overview:\n\n{combined}"
    )

    try:
        await user.send(
            f"**Weekly Summary** ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})\n\n{weekly}"
        )
    except discord.Forbidden:
        logger.error("cannot DM user for weekly summary", user_id=user_id)
        return

    logger.info("weekly summary sent", user_id=user_id)


@client.event
async def on_ready() -> None:
    logger.info("bot ready", user=str(client.user))
    init_db()
    scheduler.add_job(
        run_daily_summary,
        CronTrigger(hour=SUMMARY_HOUR, minute=SUMMARY_MINUTE, timezone="UTC"),
        id="daily",
    )
    scheduler.add_job(
        run_weekly_summary,
        CronTrigger(
            day_of_week=SUMMARY_WEEKLY_DAY,
            hour=SUMMARY_WEEKLY_HOUR,
            minute=SUMMARY_WEEKLY_MINUTE,
            timezone="UTC",
        ),
        id="weekly",
    )
    scheduler.start()
    logger.info("scheduler started", daily=f"{SUMMARY_HOUR}:{SUMMARY_MINUTE} UTC", weekly=f"day={SUMMARY_WEEKLY_DAY} {SUMMARY_WEEKLY_HOUR}:{SUMMARY_WEEKLY_MINUTE} UTC")


def shutdown() -> None:
    scheduler.shutdown(wait=False)
    if client.is_ready():
        asyncio.run_coroutine_threadsafe(client.close(), client.loop)


@client.event
async def on_disconnect() -> None:
    logger.info("bot disconnected")


def main() -> None:
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN not set")
        sys.exit(1)

    def sig_handler(_signum: int, _frame: object) -> None:
        logger.info("received signal, shutting down")
        shutdown()

    signal.signal(signal.SIGTERM, sig_handler)
    signal.signal(signal.SIGINT, sig_handler)

    client.run(DISCORD_TOKEN)


if __name__ == "__main__":
    main()
