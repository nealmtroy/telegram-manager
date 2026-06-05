"""Auto-verify: detect and handle group verification challenges after joining.

This module scans recent messages in a group for inline-keyboard verification
prompts and automatically clicks the matching button.  It is designed to run
within a short-lived Telethon client session (the same pattern as the rest of
the bot/CLI broadcast flow), so no persistent connection is required.
"""
from __future__ import annotations

from typing import Optional

from telethon import TelegramClient

from .logger import get_logger

log = get_logger("auto_verify")

# Keywords checked against inline-button text (case-insensitive).
_VERIFICATION_KEYWORDS: list[str] = [
    # English
    "verify", "confirm", "i'm not a bot", "i am not a robot",
    "captcha", "human", "press to verify", "click to verify",
    "tap to verify", "i'm human", "not a bot", "start",
    # Indonesian
    "verifikasi", "konfirmasi", "bukan bot", "klik untuk verifikasi",
    # Malay
    "pengesahan",
    # Vietnamese
    "xác minh",
    # Thai
    "ยืนยัน",
]


def _button_matches(btn_text: str) -> bool:
    """Return True if *btn_text* looks like a verification button."""
    lower = btn_text.lower()
    return any(kw in lower for kw in _VERIFICATION_KEYWORDS)


def _is_verification_message(msg) -> bool:
    """Check whether *msg* contains an inline keyboard with a verification button."""
    markup = getattr(msg, "reply_markup", None)
    if markup is None:
        return False
    rows = getattr(markup, "rows", None)
    if not rows:
        return False
    for row in rows:
        for btn in row.buttons:
            text = getattr(btn, "text", "") or ""
            if _button_matches(text):
                return True
    return False


async def auto_verify_group(
    client: TelegramClient,
    entity,
    *,
    limit: int = 10,
) -> Optional[str]:
    """Scan and click a verification button in *entity* if present.

    Args:
        client: An already-connected ``TelegramClient``.
        entity: The Telethon entity (group/channel) to scan.
        limit:  How many recent messages to inspect.

    Returns:
        The button text that was clicked, or ``None`` if no verification
        prompt was detected.
    """
    try:
        messages = await client.get_messages(entity, limit=limit)
    except Exception:
        log.debug("auto_verify: failed to fetch messages for %s", entity, exc_info=True)
        return None

    for msg in messages:
        if getattr(msg, "out", False):
            continue
        if not _is_verification_message(msg):
            continue

        # Found a verification message — try to click the matching button.
        for row in msg.reply_markup.rows:
            for btn in row.buttons:
                text = getattr(btn, "text", "") or ""
                if _button_matches(text):
                    try:
                        await msg.click(data=getattr(btn, "data", None))
                        log.info("auto_verify: clicked '%s' in %s", text, entity)
                        return text
                    except Exception:
                        log.debug(
                            "auto_verify: click failed for '%s' in %s",
                            text,
                            entity,
                            exc_info=True,
                        )
                        return None
    return None
