"""Discord client and message collection."""

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import discord

from .lib import logger
from .state import load_last_run

RATE_LIMIT_DELAY = 0.25  # seconds between channel fetches
MAX_MESSAGES_PER_CHANNEL = 500


def _parse_channel_ids() -> list[int]:
    """Parse CHANNEL_IDS env (comma-separated) into list of ints."""
    raw = os.environ.get("CHANNEL_IDS", "")
    if not raw:
        return []
    ids = []
    for s in raw.strip().split(","):
        s = s.strip()
        if s.isdigit():
            ids.append(int(s))
    return ids


def _format_message(msg: discord.Message) -> str:
    """Format a single message for aggregation."""
    author = msg.author.display_name or str(msg.author)
    content = (msg.content or "").strip()
    if not content:
        return ""
    return f"[#{msg.channel.name}] @{author}: {content}"


async def collect_messages(client: discord.Client) -> str:
    """
    Fetch messages from configured channels since last run.
    Returns aggregated string for summarization.
    """
    channel_ids = _parse_channel_ids()
    if not channel_ids:
        logger.warning("no channel IDs configured in CHANNEL_IDS")
        return ""

    last_run = load_last_run()
    if last_run is None:
        last_run = datetime.now(timezone.utc)
        logger.info("no previous run, using current time as baseline")

    aggregated: list[str] = []
    for ch_id in channel_ids:
        channel = client.get_channel(ch_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(ch_id)
            except discord.NotFound:
                logger.warning("channel not found", channel_id=ch_id)
                continue
            except discord.Forbidden:
                logger.warning("no access to channel", channel_id=ch_id)
                continue

        if not hasattr(channel, "history"):
            logger.warning("channel has no history", channel_id=ch_id)
            continue

        try:
            lines: list[str] = []
            async for msg in channel.history(
                limit=MAX_MESSAGES_PER_CHANNEL,
                after=last_run,
                oldest_first=True,
            ):
                line = _format_message(msg)
                if line:
                    lines.append(line)
            if lines:
                aggregated.append(f"\n--- #{getattr(channel, 'name', ch_id)} ---\n" + "\n".join(lines))
        except discord.Forbidden:
            logger.warning("forbidden reading channel", channel_id=ch_id)
        except Exception as e:
            logger.exception("error fetching channel history", channel_id=ch_id, error=str(e))

        await asyncio.sleep(RATE_LIMIT_DELAY)

    return "\n\n".join(aggregated) if aggregated else ""
