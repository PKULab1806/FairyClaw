# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Logging bootstrap and structured event helpers."""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fairyclaw.config.settings import settings


class TextLogFormatter(logging.Formatter):
    """Format log records into stable plain-text lines with optional fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Render one log record into canonical text layout.

        Args:
            record (logging.LogRecord): Input log record.

        Returns:
            str: Formatted log line including UTC timestamp and extra fields.
        """
        ts = datetime.now(timezone.utc).isoformat()
        msg = f"[{ts}] {record.levelname} [{record.name}] {record.getMessage()}"
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            msg += f" | {fields}"
        if record.exc_info:
            msg += f"\n{self.formatException(record.exc_info)}"
        return msg


def setup_logging() -> None:
    """Initialize root/fairyclaw loggers, handlers, and dependency log levels.

    Returns:
        None
    """
    root = logging.getLogger()
    has_text_formatter = any(isinstance(h.formatter, TextLogFormatter) for h in root.handlers)
    if has_text_formatter:
        return

    level_name = settings.log_level.upper().strip() or "INFO"
    level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)
    formatter = TextLogFormatter()

    if settings.log_to_stdout:
        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(level)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    log_file = Path(settings.log_file_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("duckduckgo_search").setLevel(logging.WARNING)
    logging.getLogger("primp").setLevel(logging.WARNING)
    logging.getLogger("h2").setLevel(logging.WARNING)
    logging.getLogger("rustls").setLevel(logging.WARNING)
    logging.getLogger("hyper_util").setLevel(logging.WARNING)
    logging.getLogger("cookie_store").setLevel(logging.WARNING)
    logging.getLogger("multipart").setLevel(logging.WARNING)
    logging.getLogger("python_multipart").setLevel(logging.WARNING)
    logging.getLogger("python_multipart.multipart").setLevel(logging.WARNING)
    fairyclaw_logger = logging.getLogger("fairyclaw")
    fairyclaw_logger.setLevel(level)
    for handler in root.handlers:
        fairyclaw_logger.addHandler(handler)
    fairyclaw_logger.propagate = False
    if level > logging.INFO:
        root.setLevel(logging.INFO)
    else:
        root.setLevel(level)


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    """Emit structured INFO log event with extra field mapping.

    Args:
        logger (logging.Logger): Target logger.
        message (str): Log message text.
        **fields (Any): Structured field payload.

    Returns:
        None
    """
    logger.info(message, extra={"fields": fields})
