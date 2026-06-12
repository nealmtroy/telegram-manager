"""Telegram Manager - multi-account Telegram CLI manager.

Public package surface.
"""
from __future__ import annotations

__version__ = "0.1.0"
__author__ = "nealmtroy"
__license__ = "GPL-3.0"

__all__ = ["__version__", "__author__", "__license__"]

# Monkeypatch Telethon to avoid retrying on PersistentTimestampOutdatedError
try:
    from telethon import TelegramClient
    from telethon.tl.functions.updates import GetChannelDifferenceRequest

    _original_call = TelegramClient.__call__

    async def _patched_call(self, request, ordered=False, flood_sleep_threshold=None):
        # If the request is GetChannelDifferenceRequest, set retries to 0 to avoid
        # 5 useless retries (each sleeping 2s) on PersistentTimestampOutdatedError.
        if isinstance(request, GetChannelDifferenceRequest):
            old_retries = getattr(self, "_request_retries", 5)
            self._request_retries = 0
            try:
                return await _original_call(self, request, ordered=ordered, flood_sleep_threshold=flood_sleep_threshold)
            finally:
                self._request_retries = old_retries
        return await _original_call(self, request, ordered=ordered, flood_sleep_threshold=flood_sleep_threshold)

    TelegramClient.__call__ = _patched_call
except ImportError:
    pass

