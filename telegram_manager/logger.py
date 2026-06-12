"""Logging setup for Telegram Manager.

Dual-sink logger:
  * Console sink via Rich (colored, human-friendly).
  * Rotating file sink at ``logs/telegram_manager.log``.

Call :func:`setup_logger` once at program start. Subsequent calls are safe
(idempotent) - handlers won't be duplicated.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from rich.logging import RichHandler

_LOGGER_NAME = "telegram_manager"
_INITIALIZED = False


class TelethonLogFilter(logging.Filter):
    """Filter out PersistentTimestampOutdatedError warning logs from Telethon.

    These are caused by stateless StringSession clients attempting to fetch channel difference
    and getting a server-side outdated timestamp error, which is harmless for direct-message-only
    tasks but extremely spammy in logs.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "PersistentTimestampOutdatedError" in msg:
            return False
        if "Telegram is having internal issues" in msg and "PersistentTimestampOutdatedError" in msg:
            return False
        if "GetChannelDifferenceRequest" in msg:
            return False
        return True


def setup_logger(
    log_dir: Path,
    level: str = "INFO",
    *,
    debug: bool = False,
    logger_name: str = _LOGGER_NAME,
) -> logging.Logger:
    """Configure and return the package logger.

    Args:
        log_dir: Directory where ``telegram_manager.log`` will be written.
        level: Base log level (``DEBUG``/``INFO``/``WARNING``/``ERROR``).
        debug: If True, forces DEBUG level and also turns on Telethon's own
            internal logging at INFO (it's very chatty, so we cap it).
        logger_name: Internal - override the root logger name (useful in
            tests).

    Returns:
        The configured :class:`logging.Logger` instance.
    """
    global _INITIALIZED

    effective_level = logging.DEBUG if debug else _coerce_level(level)

    logger = logging.getLogger(logger_name)
    logger.setLevel(effective_level)
    logger.propagate = False  # don't double-log via the root logger

    if _INITIALIZED:
        # Re-tune level in case --debug was toggled later, but keep handlers.
        logger.setLevel(effective_level)
        return logger

    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "telegram_manager.log"

    # ---- Console (Rich) -----------------------------------------------------
    console_handler = RichHandler(
        level=effective_level,
        rich_tracebacks=True,
        show_time=True,
        show_path=debug,  # only show file:line in debug mode
        markup=False,
        log_time_format="[%X]",
    )
    console_handler.setFormatter(logging.Formatter("%(message)s"))

    # ---- Rotating file ------------------------------------------------------
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5 MB per file
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)  # always keep full detail on disk
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    # ---- Third-party loggers ------------------------------------------------
    # Telethon's logger is noisy; keep it INFO unless user explicitly debugs.
    telethon_logger = logging.getLogger("telethon")
    telethon_logger.setLevel(logging.DEBUG if debug else logging.WARNING)
    telethon_logger.addFilter(TelethonLogFilter())
    # Mirror to our sinks so Telethon logs also land in the file.
    for handler in logger.handlers:
        telethon_logger.addHandler(handler)
    telethon_logger.propagate = False

    _INITIALIZED = True
    logger.debug("Logger initialized (level=%s, debug=%s)", effective_level, debug)
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return a child logger under the package namespace."""
    if name is None or name == _LOGGER_NAME:
        return logging.getLogger(_LOGGER_NAME)
    return logging.getLogger(f"{_LOGGER_NAME}.{name}")


def _coerce_level(level: str) -> int:
    """Map a string level to the logging module constant, defaulting to INFO."""
    try:
        return getattr(logging, level.upper())
    except AttributeError:
        return logging.INFO
