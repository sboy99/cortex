"""Global JSON logger. Unifies structlog and stdlib (e.g. discord.py) into one format."""

import logging
import sys

import structlog


def _add_logger_name_from_record(_logger: object, _method_name: str, event_dict: dict) -> dict:
    """Add logger name from LogRecord for foreign (stdlib) log entries."""
    record = event_dict.get("_record")
    if record:
        event_dict["logger"] = record.name
    return event_dict


def configure_logging() -> None:
    """
    Configure structlog and redirect stdlib logging through it.
    All logs (app + discord.py) will output as JSON.
    """
    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_logger_name_from_record,
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def get_logger(*args: str, **kwargs: object) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. Use from .lib import get_logger; logger = get_logger()."""
    return structlog.get_logger(*args, **kwargs)


# Default app logger
logger = get_logger()
