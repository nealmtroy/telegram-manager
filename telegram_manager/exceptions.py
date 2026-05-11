"""Custom exceptions for Telegram Manager.

We wrap Telethon's exceptions into friendlier, domain-specific errors so the
CLI layer can show actionable messages without importing telethon internals.
"""
from __future__ import annotations

from typing import Optional


class TelegramManagerError(Exception):
    """Base class for all errors raised by this package."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
class ConfigError(TelegramManagerError):
    """Raised when configuration is missing or invalid."""


# ---------------------------------------------------------------------------
# Authentication / login
# ---------------------------------------------------------------------------
class AuthError(TelegramManagerError):
    """Generic authentication failure."""


class InvalidPhoneError(AuthError):
    """Phone number is malformed or rejected by Telegram."""


class InvalidCodeError(AuthError):
    """Login code is wrong or expired."""


class TwoFARequiredError(AuthError):
    """Account has 2FA enabled - a cloud password is required.

    This is raised as a *signal*, not always a hard failure: the auth layer
    catches Telethon's ``SessionPasswordNeededError`` and re-raises this so
    the CLI can prompt the user for their 2FA password.
    """


class InvalidPasswordError(AuthError):
    """2FA password is wrong."""


class PhoneBannedError(AuthError):
    """Phone number is banned or restricted by Telegram."""


# ---------------------------------------------------------------------------
# Network / rate limiting
# ---------------------------------------------------------------------------
class FloodError(TelegramManagerError):
    """Telegram is asking us to slow down (FLOOD_WAIT)."""

    def __init__(self, seconds: int, message: Optional[str] = None) -> None:
        self.seconds = seconds
        super().__init__(
            message
            or f"Flood wait: please retry in {seconds} seconds ({seconds // 60} min)."
        )


class ConnectionError(TelegramManagerError):  # noqa: A001 - shadow built-in intentionally
    """Could not connect to Telegram servers."""


# ---------------------------------------------------------------------------
# Storage / state
# ---------------------------------------------------------------------------
class StorageError(TelegramManagerError):
    """accounts.json is missing, corrupt, or unwritable."""


class AccountNotFoundError(TelegramManagerError):
    """Requested account alias / phone is not registered."""


class AccountAlreadyExistsError(TelegramManagerError):
    """Trying to add an account that's already registered."""


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
class NotAuthorizedError(TelegramManagerError):
    """Session exists but is no longer authorized (logged out / revoked)."""


class UserCancelledError(TelegramManagerError):
    """User aborted an interactive flow (Ctrl+C, empty input, etc.)."""
