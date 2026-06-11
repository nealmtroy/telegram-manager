"""Auto-reply: persistent Telethon clients that reply to fresh private chats.

Each managed account that has auto-reply enabled gets a persistent Telethon
client.  When a new private message arrives from someone the account has
*never* chatted with, the configured reply text (HTML) is sent exactly once.

The module exposes:

* ``reconcile_auto_reply_clients()``: called periodically from the bot's
  background loop to start/stop clients as the DB state changes.
* ``stop_all()``: graceful shutdown.
"""
from __future__ import annotations

import asyncio
import os
from typing import Dict, Optional, Set, Tuple

from telethon import TelegramClient, events
from telethon.errors import (
    AuthKeyUnregisteredError,
    SessionRevokedError,
    UserDeactivatedBanError,
)
from telethon.sessions import StringSession

from .account_locks import acquire_persistent_lease, release_persistent_lease
from .config import load_config
from .db import AccountRow, get_all_accounts, remove_account
from .device_presets import get_preset
from .logger import get_logger

log = get_logger("auto_reply")

# (admin_id, phone) -> running client
_active_clients: Dict[Tuple[int, str], TelegramClient] = {}
# (admin_id, phone) -> persistent lease context
_active_leases: Dict[Tuple[int, str], object] = {}
# (admin_id, phone) -> set of user_ids already replied to this session
_already_replied: Dict[Tuple[int, str], Set[int]] = {}

_MAX_CLIENTS = int(os.getenv("AUTO_REPLY_MAX_CLIENTS", "20"))


def _is_terminal(exc: BaseException) -> bool:
    return isinstance(exc, (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError))


def _build_client(acc: AccountRow) -> TelegramClient:
    """Build a TelegramClient from an AccountRow's session string + config."""
    cfg = load_config()
    preset = get_preset(acc.device_preset)

    # Resolve API credentials
    api_index = acc.api_credential_index
    credential = cfg.api_credential_for_index(api_index)

    # Resolve proxy
    proxy_index = acc.proxy_index
    proxy = cfg.proxy_for_index(proxy_index)

    return TelegramClient(
        StringSession(acc.session_string),
        credential.api_id,
        credential.api_hash,
        device_model=preset.device_model,
        system_version=preset.system_version,
        app_version=preset.app_version,
        lang_code=preset.lang_code,
        system_lang_code=preset.system_lang_code,
        proxy=proxy.to_telethon() if proxy else None,
    )


async def _is_fresh_chat(client: TelegramClient, user_id: int) -> bool:
    """Return True if the managed account has never sent a message to *user_id*."""
    try:
        async for msg in client.iter_messages(user_id, limit=20, from_user="me"):
            # Found at least one outgoing message → not fresh
            return False
    except Exception:
        log.debug("Failed to check history for user %s", user_id, exc_info=True)
        return False
    return True


async def _start_client(acc: AccountRow) -> bool:
    """Connect a persistent client and register the auto-reply handler.

    Returns True on success, False if the session is invalid or another error
    prevents connection.
    """
    key = (acc.admin_id, acc.phone)
    if key in _active_clients:
        return True

    reply_text = acc.auto_reply_text
    if not reply_text:
        return False

    lease_ctx, lease = await acquire_persistent_lease(
        acc.admin_id,
        acc.phone,
        purpose="auto_reply",
        ttl_seconds=300,
        wait_seconds=0,
    )
    if not lease.acquired:
        log.debug("auto_reply: account busy/locked for %s", acc.alias)
        await release_persistent_lease(lease_ctx)
        return False

    client = _build_client(acc)

    try:
        await client.connect()
        me = await client.get_me()
        if me is None:
            log.warning("auto_reply: session not authorized for %s", acc.alias)
            await client.disconnect()
            await release_persistent_lease(lease_ctx)
            return False
    except Exception as exc:
        if _is_terminal(exc):
            log.warning("auto_reply: terminal error for %s: %s", acc.alias, exc)
            try:
                removed = remove_account(acc.admin_id, acc.phone)
                if removed:
                    log.warning("auto_reply: auto-removed invalid account %s", acc.alias)
            except Exception:
                log.debug("auto_reply: failed to remove %s", acc.alias, exc_info=True)
        else:
            log.debug("auto_reply: connect error for %s: %s", acc.alias, exc)
        try:
            await client.disconnect()
        except Exception:
            pass
        await release_persistent_lease(lease_ctx)
        return False

    replied_set: Set[int] = set()
    _already_replied[key] = replied_set

    @client.on(events.NewMessage(incoming=True))
    async def _handler(event):
        # Only private (user) chats
        if not event.is_private:
            return
        sender = event.sender
        if sender is None:
            return
        # Ignore bots
        if getattr(sender, "bot", False):
            return
        sender_id = sender.id
        # Already replied this session
        if sender_id in replied_set:
            return
        # Check if chat is fresh (no prior outgoing messages)
        if not await _is_fresh_chat(client, sender_id):
            replied_set.add(sender_id)  # mark so we don't re-check
            return
        # Send the auto-reply
        try:
            await client.send_message(sender_id, reply_text, parse_mode="html")
            replied_set.add(sender_id)
            log.info("auto_reply: replied to %s from %s", sender_id, acc.alias)
        except Exception:
            log.debug("auto_reply: failed to send to %s from %s", sender_id, acc.alias, exc_info=True)

    _active_clients[key] = client
    _active_leases[key] = lease_ctx
    log.info("auto_reply: started client for %s", acc.alias)
    return True


async def _stop_client(key: Tuple[int, str]) -> None:
    """Disconnect and remove a client from the active pool."""
    client = _active_clients.pop(key, None)
    lease_ctx = _active_leases.pop(key, None)
    _already_replied.pop(key, None)
    if client and client.is_connected():
        try:
            await client.disconnect()
        except Exception:
            pass
    await release_persistent_lease(lease_ctx)
    log.info("auto_reply: stopped client for key=%s", key)


async def reconcile_auto_reply_clients() -> None:
    """Synchronise running clients with the current DB state.

    * Starts clients for accounts that have auto_reply_enabled and aren't
      running yet (up to ``_MAX_CLIENTS``).
    * Stops clients whose accounts no longer exist or have auto-reply disabled.
    """
    try:
        all_accounts = get_all_accounts()
    except Exception:
        log.debug("auto_reply reconcile: failed to fetch accounts", exc_info=True)
        return

    enabled: Dict[Tuple[int, str], AccountRow] = {}
    for acc in all_accounts:
        if acc.auto_reply_enabled and acc.auto_reply_text:
            enabled[(acc.admin_id, acc.phone)] = acc

    # Stop clients that are no longer enabled
    to_stop = [k for k in list(_active_clients) if k not in enabled]
    for key in to_stop:
        await _stop_client(key)

    # Start new clients up to the limit
    running = len(_active_clients)
    for key, acc in enabled.items():
        if key in _active_clients:
            continue
        if running >= _MAX_CLIENTS:
            log.debug("auto_reply: max clients (%d) reached, skipping %s", _MAX_CLIENTS, acc.alias)
            break
        ok = await _start_client(acc)
        if ok:
            running += 1


async def stop_all() -> None:
    """Gracefully disconnect all auto-reply clients."""
    keys = list(_active_clients.keys())
    for key in keys:
        await _stop_client(key)
