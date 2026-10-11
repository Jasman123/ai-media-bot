"""Logging hygiene (C7): quiet httpx, redact bot tokens, optional JSON output."""

import json
import logging
import re
import sys
from datetime import datetime, timezone

# Telegram bot tokens look like "123456789:AA...". They appear in Bot API URLs.
TOKEN_RE = re.compile(r"\d{5,}:[A-Za-z0-9_-]{30,}")
NOISY_LOGGERS = ("httpx", "httpcore", "hpack", "botocore", "urllib3")
EXTRA_FIELDS = ("job_id", "user_id", "update_id", "provider")


def redact(text: str) -> str:
    return TOKEN_RE.sub("<redacted-token>", text)


class RedactingFormatter(logging.Formatter):
    """Redacts tokens from the whole formatted record, tracebacks included."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in EXTRA_FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, ensure_ascii=False, default=str))


def configure_logging(level: str = "INFO", json_output: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
