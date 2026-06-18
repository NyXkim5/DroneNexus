"""
Structured logging configuration for the OVERWATCH ISR Platform.

When the OVERWATCH_LOG_FORMAT environment variable is set to 'json', log output
is emitted as one JSON object per line. Otherwise the default human-readable
format is used. No external dependencies are required.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        entry: dict = {
            "timestamp": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        extra = {
            k: v
            for k, v in record.__dict__.items()
            if k not in logging.LogRecord(
                "", 0, "", 0, "", (), None
            ).__dict__
            and k not in ("message", "msg", "args")
        }
        if extra:
            entry["extra"] = extra
        return json.dumps(entry, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Apply logging configuration based on OVERWATCH_LOG_FORMAT env var.

    Call this once at startup before any log output. When the env var is 'json'
    a JSONFormatter is attached to stdout. Otherwise the default human-readable
    format is used.
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers to avoid duplicates on re-init
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)

    log_format = os.environ.get("OVERWATCH_LOG_FORMAT", "text").lower()
    if log_format == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
        )

    root.addHandler(handler)
