"""Entrypoint: Discord bot + daily scheduler + weekly summary."""

import asyncio
import os
import signal
import sys
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from .bot import collect_messages, collect_messages_for_user
from .db import (
    cleanup_old_summaries,
    get_last_n_daily_summaries,
    get_user_channel_preferences,
    init_db,
    save_daily_summary,
    set_user_channel_preferences,
)
from .lib import configure_logging, logger
from .state import load_last_run, save_last_run
from .summarizer import answer_question, summarize_messages

load_dotenv()
configure_logging()

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID")
SUMMARY_HOUR = int(os.environ.get("SUMMARY_UTC_HOUR", "8"))
SUMMARY_MINUTE = int(os.environ.get("SUMMARY_UTC_MINUTE", "0"))
SUMMARY_WEEKLY_DAY = int(os.environ.get("SUMMARY_WEEKLY_DAY", "6"))  # 0=Mon, 6=Sun
SUMMARY_WEEKLY_HOUR = int(os.environ.get("SUMMARY_WEEKLY_UTC_HOUR", "9"))
SUMMARY_WEEKLY_MINUTE = int(os.environ.get("SUMMARY_WEEKLY_UTC_MINUTE", "0"))
ENABLE_LEGACY_SCHEDULER = os.environ.get("ENABLE_LEGACY_SCHEDULER", "false").lower() in {
    "1",
    "true",
    "yes",
}
ASK_LOOKBACK_HOURS = int(os.environ.get("ASK_LOOKBACK_HOURS", "24"))


intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)
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


async def _send_dm_text(user: discord.abc.User, text: str) -> None:
    """Send long content in Discord-safe chunks."""
    chunk_size = 1800
    if len(text) <= chunk_size:
        await user.send(text)
        return

    for i in range(0, len(text), chunk_size):
        await user.send(text[i : i + chunk_size])


async def _send_followup_chunks(
    interaction: discord.Interaction,
    text: str,
    *,
    ephemeral: bool = True,
) -> None:
    """Send long content via interaction followups in Discord-safe chunks."""
    chunk_size = 1800
    if len(text) <= chunk_size:
        await interaction.followup.send(text, ephemeral=ephemeral)
        return

    for i in range(0, len(text), chunk_size):
        await interaction.followup.send(text[i : i + chunk_size], ephemeral=ephemeral)


def _selected_channels(
    channel_1: discord.TextChannel,
    channel_2: discord.TextChannel | None,
    channel_3: discord.TextChannel | None,
    channel_4: discord.TextChannel | None,
    channel_5: discord.TextChannel | None,
) -> list[discord.TextChannel]:
    """Normalize and dedupe channel options from slash command params."""
    channels = [channel_1, channel_2, channel_3, channel_4, channel_5]
    deduped: dict[int, discord.TextChannel] = {}
    for channel in channels:
        if channel is not None:
            deduped[channel.id] = channel
    return list(deduped.values())


@tree.command(name="subscribe", description="Select channels for your personalized updates.")
@app_commands.describe(
    channel_1="First text channel",
    channel_2="Second text channel (optional)",
    channel_3="Third text channel (optional)",
    channel_4="Fourth text channel (optional)",
    channel_5="Fifth text channel (optional)",
)
async def subscribe_channels(
    interaction: discord.Interaction,
    channel_1: discord.TextChannel,
    channel_2: discord.TextChannel | None = None,
    channel_3: discord.TextChannel | None = None,
    channel_4: discord.TextChannel | None = None,
    channel_5: discord.TextChannel | None = None,
) -> None:
    """Persist selected channels for the requesting user."""
    if interaction.guild is None:
        await interaction.response.send_message(
            "Use this command in a server text channel.",
            ephemeral=True,
        )
        return

    channels = _selected_channels(channel_1, channel_2, channel_3, channel_4, channel_5)
    user = interaction.user
    readable_channels = [
        channel
        for channel in channels
        if channel.permissions_for(user).view_channel and channel.permissions_for(user).read_message_history
    ]
    if not readable_channels:
        await interaction.response.send_message(
            "None of the selected channels are readable for you.",
            ephemeral=True,
        )
        return

    saved = set_user_channel_preferences(
        user_id=user.id,
        guild_id=interaction.guild.id,
        channel_ids=[ch.id for ch in readable_channels],
    )
    mentions = " ".join(f"<#{channel_id}>" for channel_id in saved)
    await interaction.response.send_message(
        f"Saved {len(saved)} channel(s) for you: {mentions}",
        ephemeral=True,
    )
    logger.info("channels subscribed", user_id=user.id, guild_id=interaction.guild.id, channels=saved)


@tree.command(name="update", description="Fetch latest updates from your selected channels and DM you.")
async def get_personal_update(interaction: discord.Interaction) -> None:
    """Collect channel messages and DM a summary to the requester."""
    if interaction.guild is None:
        await interaction.response.send_message(
            "Use this command in a server text channel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    user = interaction.user
    channel_ids = get_user_channel_preferences(user.id, interaction.guild.id)
    if not channel_ids:
        await interaction.followup.send(
            "No channels configured yet. Use `/subscribe` first.",
            ephemeral=True,
        )
        return

    aggregated = await collect_messages_for_user(client, user.id, channel_ids)
    summary = summarize_messages(aggregated)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    payload = f"**Your Channel Update** ({stamp})\n\n{summary}"
    try:
        await _send_dm_text(user, payload)
    except discord.Forbidden:
        await interaction.followup.send(
            "I couldn't DM you. Please enable DMs from server members and try again.",
            ephemeral=True,
        )
        return

    await interaction.followup.send("Update sent to your DM.", ephemeral=True)
    logger.info("personal update sent", user_id=user.id, guild_id=interaction.guild.id)


@tree.command(name="ask", description="Ask a question about your selected channels.")
@app_commands.describe(question="Your question for the bot")
async def ask_channel_question(interaction: discord.Interaction, question: str) -> None:
    """Answer a user question based on recent selected-channel messages."""
    if interaction.guild is None:
        await interaction.response.send_message(
            "Use this command in a server text channel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    user = interaction.user
    channel_ids = get_user_channel_preferences(user.id, interaction.guild.id)
    if not channel_ids:
        await interaction.followup.send(
            "No channels configured yet. Use `/subscribe` first.",
            ephemeral=True,
        )
        return

    since = datetime.now(timezone.utc) - timedelta(hours=ASK_LOOKBACK_HOURS)
    context = await collect_messages_for_user(
        client,
        user_id=user.id,
        channel_ids=channel_ids,
        since=since,
        update_checkpoints=False,
    )
    answer = answer_question(context, question)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    payload = f"**Answer** ({stamp})\n\n**Question:** {question}\n\n{answer}"
    await _send_followup_chunks(interaction, payload, ephemeral=True)
    logger.info("question answered", user_id=user.id, guild_id=interaction.guild.id)


@client.event
async def on_ready() -> None:
    logger.info("bot ready", user=str(client.user))
    init_db()
    synced = await tree.sync()
    logger.info("slash commands synced", count=len(synced))

    if ENABLE_LEGACY_SCHEDULER and not scheduler.running:
        scheduler.add_job(
            run_daily_summary,
            CronTrigger(hour=SUMMARY_HOUR, minute=SUMMARY_MINUTE, timezone="UTC"),
            id="daily",
            replace_existing=True,
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
            replace_existing=True,
        )
        scheduler.start()
        logger.info(
            "legacy scheduler started",
            daily=f"{SUMMARY_HOUR}:{SUMMARY_MINUTE} UTC",
            weekly=f"day={SUMMARY_WEEKLY_DAY} {SUMMARY_WEEKLY_HOUR}:{SUMMARY_WEEKLY_MINUTE} UTC",
        )
    elif not ENABLE_LEGACY_SCHEDULER:
        logger.info("legacy scheduler disabled")


def shutdown() -> None:
    if scheduler.running:
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
