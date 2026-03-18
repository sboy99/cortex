"""Discord client and message collection."""

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import discord

from .db import get_user_channel_checkpoints, upsert_user_channel_checkpoint
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


async def collect_messages_for_user(
    client: discord.Client,
    user_id: int,
    channel_ids: list[int],
    since: datetime | None = None,
    update_checkpoints: bool = True,
) -> str:
    """
    Fetch messages for a specific user's selected channels.

    If ``since`` is provided, it is used for every channel.
    Otherwise per-user per-channel checkpoints are used.
    """
    normalized_ids = sorted(set(int(ch_id) for ch_id in channel_ids))
    if not normalized_ids:
        return ""

    checkpoints = {}
    if since is None:
        checkpoints = get_user_channel_checkpoints(user_id, normalized_ids)

    aggregated: list[str] = []
    for ch_id in normalized_ids:
        channel = client.get_channel(ch_id)
        if channel is None:
            try:
                channel = await client.fetch_channel(ch_id)
            except discord.NotFound:
                logger.warning("channel not found", user_id=user_id, channel_id=ch_id)
                continue
            except discord.Forbidden:
                logger.warning("no access to channel", user_id=user_id, channel_id=ch_id)
                continue

        if not hasattr(channel, "history"):
            logger.warning("channel has no history", user_id=user_id, channel_id=ch_id)
            continue

        channel_after = since if since is not None else checkpoints.get(ch_id)
        newest_seen: datetime | None = None

        try:
            lines: list[str] = []
            async for msg in channel.history(
                limit=MAX_MESSAGES_PER_CHANNEL,
                after=channel_after,
                oldest_first=True,
            ):
                line = _format_message(msg)
                if line:
                    lines.append(line)

                msg_created = msg.created_at
                if msg_created.tzinfo is None:
                    msg_created = msg_created.replace(tzinfo=timezone.utc)
                else:
                    msg_created = msg_created.astimezone(timezone.utc)
                newest_seen = msg_created

            if lines:
                aggregated.append(f"\n--- #{getattr(channel, 'name', ch_id)} ---\n" + "\n".join(lines))
                if update_checkpoints and newest_seen is not None:
                    upsert_user_channel_checkpoint(user_id, ch_id, newest_seen)
        except discord.Forbidden:
            logger.warning("forbidden reading channel", user_id=user_id, channel_id=ch_id)
        except Exception as e:
            logger.exception(
                "error fetching channel history",
                user_id=user_id,
                channel_id=ch_id,
                error=str(e),
            )

        await asyncio.sleep(RATE_LIMIT_DELAY)

    return "\n\n".join(aggregated) if aggregated else ""
