"""Persist last run timestamp for incremental message collection."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .lib import logger

DEFAULT_STATE_PATH = Path("data/state.json")


def get_state_path() -> Path:
    """Return state file path from env or default."""
    return Path(os.environ.get("STATE_PATH", str(DEFAULT_STATE_PATH)))


def load_last_run() -> datetime | None:
    """Load last run UTC timestamp from state file. Returns None if not found."""
    path = get_state_path()
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        ts = data.get("last_run_utc")
        if ts:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("failed to load state", path=str(path), error=str(e))
    return None


def save_last_run(utc_dt: datetime) -> None:
    """Persist last run UTC timestamp to state file."""
    path = get_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"last_run_utc": utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")}
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    logger.info("saved last run state", last_run_utc=data["last_run_utc"])
