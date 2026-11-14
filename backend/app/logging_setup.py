"""JSON logging with request metadata and no visitor content."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_LOG_EXTRA_FIELDS = (
    "request_id",
    "route",
    "status",
    "duration_ms",
    "message_count",
    "error_type",
    "suspicious_input",
)


class JsonFormatter(logging.Formatter):
    """Render log records as one JSON object per line.

    Only whitelisted metadata fields are copied, so arbitrary extras cannot leak
    prompts or message text into the logs.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Serialise a record with stable, content-free fields."""
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _LOG_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if record.exc_info:
            payload.setdefault("error_type", record.exc_info[0].__name__)
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def setup_logging(level: int = logging.INFO) -> None:
    """Install the JSON formatter once per process."""
    root = logging.getLogger()
    if getattr(setup_logging, "_configured", False):
        return

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)
    setup_logging._configured = True  # type: ignore[attr-defined]
