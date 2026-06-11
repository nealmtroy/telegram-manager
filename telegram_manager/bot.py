"""Telegram Bot interface with Supabase storage — fully button-driven.

No slash commands needed. All interaction via inline buttons + text input
guided by conversation state.
"""
from __future__ import annotations

import asyncio
import base64
import os
import random
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from telethon import TelegramClient
from telethon import errors as telethon_errors
from telethon.sessions import StringSession


class _MissingTelethonError(Exception):
    """Fallback for Telethon error classes missing in older versions."""


AuthKeyDuplicatedError = getattr(telethon_errors, "AuthKeyDuplicatedError", _MissingTelethonError)
AuthKeyUnregisteredError = getattr(telethon_errors, "AuthKeyUnregisteredError", _MissingTelethonError)
ChannelInvalidError = getattr(telethon_errors, "ChannelInvalidError", _MissingTelethonError)
ChannelPrivateError = getattr(telethon_errors, "ChannelPrivateError", _MissingTelethonError)
ChatAdminRequiredError = getattr(telethon_errors, "ChatAdminRequiredError", _MissingTelethonError)
ChatIdInvalidError = getattr(telethon_errors, "ChatIdInvalidError", _MissingTelethonError)
ChatSendPlainTextForbiddenError = getattr(telethon_errors, "ChatSendPlainTextForbiddenError", _MissingTelethonError)
ChatWriteForbiddenError = getattr(telethon_errors, "ChatWriteForbiddenError", _MissingTelethonError)
FloodWaitError = getattr(telethon_errors, "FloodWaitError", _MissingTelethonError)
InputUserDeactivatedError = getattr(telethon_errors, "InputUserDeactivatedError", _MissingTelethonError)
InviteHashExpiredError = getattr(telethon_errors, "InviteHashExpiredError", _MissingTelethonError)
InviteHashInvalidError = getattr(telethon_errors, "InviteHashInvalidError", _MissingTelethonError)
MessageActionForbiddenError = getattr(telethon_errors, "MessageActionForbiddenError", _MissingTelethonError)
PeerIdInvalidError = getattr(telethon_errors, "PeerIdInvalidError", _MissingTelethonError)
PhoneCodeExpiredError = getattr(telethon_errors, "PhoneCodeExpiredError", _MissingTelethonError)
PhoneCodeInvalidError = getattr(telethon_errors, "PhoneCodeInvalidError", _MissingTelethonError)
SessionPasswordNeededError = getattr(telethon_errors, "SessionPasswordNeededError", _MissingTelethonError)
SessionRevokedError = getattr(telethon_errors, "SessionRevokedError", _MissingTelethonError)
SlowModeWaitError = getattr(telethon_errors, "SlowModeWaitError", _MissingTelethonError)
UserBannedInChannelError = getattr(telethon_errors, "UserBannedInChannelError", _MissingTelethonError)
UserDeactivatedBanError = getattr(telethon_errors, "UserDeactivatedBanError", _MissingTelethonError)
UserIsBlockedError = getattr(telethon_errors, "UserIsBlockedError", _MissingTelethonError)
UserPrivacyRestrictedError = getattr(telethon_errors, "UserPrivacyRestrictedError", _MissingTelethonError)
UserRestrictedError = getattr(telethon_errors, "UserRestrictedError", _MissingTelethonError)
UsernameInvalidError = getattr(telethon_errors, "UsernameInvalidError", _MissingTelethonError)
UsernameNotOccupiedError = getattr(telethon_errors, "UsernameNotOccupiedError", _MissingTelethonError)

from .config import load_config
from .db import (
    AccountRow,
    BroadcastListRow,
    add_account,
    add_list,
    clear_account_broadcast_status,
    create_broadcast_job,
    delete_saved_msg,
    find_account,
    get_account_count,
    get_accounts,
    get_admin_ids,
    get_admin_lang,
    get_broadcast_job,
    get_broadcast_job_items,
    get_list,
    get_lists,
    get_recoverable_broadcast_jobs,
    get_saved_messages,
    grant_vip,
    is_managed_account,
    is_registered_admin,
    is_vip_admin,
    register_admin,
    remove_account,
    remove_list,
    reset_broadcast_items_for_next_round,
    reset_running_broadcast_items,
    resolve_admin_id,
    save_broadcast_msg,
    set_admin_lang,
    transfer_all,
    update_account_runtime,
    update_auto_reply,
    update_broadcast_job,
    update_broadcast_job_item,
    upsert_broadcast_job_items,
)
from .account_locks import account_lease, cleanup_stale_leases
from .auto_verify import auto_verify_group
from .device_presets import get_preset
from .i18n import LANGUAGES, get_lang, set_lang, t
from .logger import get_logger

log = get_logger("bot")
router = Router()

# Per-user state for multi-step flows
_state: Dict[int, dict] = {}
_last_bot_msg: Dict[int, int] = {}  # uid -> message_id to delete
_global_broadcast_sem: Optional[asyncio.Semaphore] = None
_global_broadcast_sem_limit: Optional[int] = None
_owner_error_alerts: Dict[str, float] = {}
_MAX_NAME_LENGTH = 100


def _name_too_long_message(name: str) -> str:
    return f"Name too long ({len(name)}/{_MAX_NAME_LENGTH} characters). Please enter a shorter name:"


def _format_delay(delay: tuple[float, float]) -> str:
    delay_min, delay_max = delay
    if delay_max <= 0:
        return "none"
    if delay_min == delay_max:
        return f"{delay_min:g}s"
    return f"{delay_min:g}-{delay_max:g}s"


def _parse_delay_value(text: str) -> tuple[float, float] | None:
    if text == "Auto (3-10s)":
        return 3.0, 10.0
    if text == "No delay":
        return 0.0, 0.0
    try:
        if "-" in text:
            left, right = text.split("-", 1)
            delay_min, delay_max = float(left.strip()), float(right.strip())
        else:
            delay_min = delay_max = float(text.strip())
    except ValueError:
        return None
    if delay_min < 0 or delay_max < 0:
        return None
    if delay_min > delay_max:
        delay_min, delay_max = delay_max, delay_min
    return delay_min, delay_max


def _delay_value_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Auto (3-10s)"), KeyboardButton(text="No delay")],
            [KeyboardButton(text="<< Menu")],
        ],
        resize_keyboard=True,
    )


async def _ask_group_delay(message: Message) -> None:
    await message.answer(
        "Delay antar group?\nContoh: 45 atau 30-60",
        reply_markup=_delay_value_kb(),
    )


async def _ask_round_delay(message: Message) -> None:
    await message.answer(
        "Delay setelah semua group selesai?\nContoh: 600 atau 500-700",
        reply_markup=_delay_value_kb(),
    )


async def _reply(message: Message, uid: int, text: str, **kwargs):
    """Send reply and delete previous bot message to keep chat clean."""
    # Delete previous bot message
    prev = _last_bot_msg.get(uid)
    if prev:
        try:
            await message.bot.delete_message(message.chat.id, prev)
        except Exception:
            pass
    sent = await message.answer(text, **kwargs)
    _last_bot_msg[uid] = sent.message_id
    return sent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _entities_to_html(text: str, entities) -> str:
    """Convert Telegram Bot API entities to HTML for Telethon."""
    if not entities or not text:
        return text
    # Sort by offset descending so insertions don't shift positions
    sorted_ents = sorted(entities, key=lambda e: e.offset, reverse=True)
    result = text
    for ent in sorted_ents:
        start = ent.offset
        end = ent.offset + ent.length
        inner = result[start:end]
        if ent.type == "bold":
            inner = f"<b>{inner}</b>"
        elif ent.type == "italic":
            inner = f"<i>{inner}</i>"
        elif ent.type == "underline":
            inner = f"<u>{inner}</u>"
        elif ent.type == "strikethrough":
            inner = f"<s>{inner}</s>"
        elif ent.type == "code":
            inner = f"<code>{inner}</code>"
        elif ent.type == "pre":
            inner = f"<pre>{inner}</pre>"
        elif ent.type == "text_link":
            inner = f'<a href="{ent.url}">{inner}</a>'
        elif ent.type == "spoiler":
            inner = f"<tg-spoiler>{inner}</tg-spoiler>"
        result = result[:start] + inner + result[end:]
    return result


def _stable_index(*parts: object, modulo: int) -> Optional[int]:
    if modulo <= 0:
        return None
    text = ":".join(str(p) for p in parts if p is not None)
    return sum(text.encode("utf-8")) % modulo


def _api_index_for_account(acc: AccountRow) -> Optional[int]:
    cfg = load_config()
    if not cfg.api_credentials:
        return None
    if acc.api_credential_index is not None:
        return acc.api_credential_index
    limit = max(1, cfg.api_account_limit)
    bucket = _stable_index(acc.admin_id, acc.phone, modulo=len(cfg.api_credentials) * limit)
    if bucket is None:
        return None
    return min(bucket // limit, len(cfg.api_credentials) - 1)


def _proxy_index_for_account(acc: AccountRow) -> Optional[int]:
    cfg = load_config()
    if not cfg.proxies:
        return None
    if acc.proxy_index is not None:
        return acc.proxy_index
    return _stable_index(acc.admin_id, acc.phone, modulo=len(cfg.proxies))


def _global_broadcast_semaphore() -> asyncio.Semaphore:
    global _global_broadcast_sem, _global_broadcast_sem_limit
    limit = load_config().broadcast_global_concurrency
    if _global_broadcast_sem is None or _global_broadcast_sem_limit != limit:
        _global_broadcast_sem = asyncio.Semaphore(limit)
        _global_broadcast_sem_limit = limit
    return _global_broadcast_sem


def _assignment_for_new_account(admin_id: int, phone: str) -> tuple[Optional[int], Optional[int]]:
    cfg = load_config()
    api_index = None
    if cfg.api_credentials:
        existing_count = get_account_count()
        api_index = min(existing_count // max(1, cfg.api_account_limit), len(cfg.api_credentials) - 1)
    proxy_index = _stable_index(admin_id, phone, modulo=len(cfg.proxies)) if cfg.proxies else None
    return api_index, proxy_index


def _proxy_host_for_account(acc: AccountRow) -> str:
    cfg = load_config()
    proxy = cfg.proxy_for_index(_proxy_index_for_account(acc))
    return proxy.host if proxy else ""


def _runtime_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mark_account_connected(acc: AccountRow, *, status: str = "") -> None:
    update_account_runtime(
        acc.admin_id,
        acc.phone,
        connected_ip=_proxy_host_for_account(acc),
        last_connected_at=_runtime_now(),
        broadcast_status=status,
        broadcast_updated_at=_runtime_now(),
    )


def _client_from_session(session_string: str, preset_key: str, acc: Optional[AccountRow] = None) -> TelegramClient:
    cfg = load_config()
    preset = get_preset(preset_key)
    credential = cfg.api_credential_for_index(_api_index_for_account(acc) if acc else None)
    proxy = cfg.proxy_for_index(_proxy_index_for_account(acc) if acc else None)
    return TelegramClient(
        StringSession(session_string), credential.api_id, credential.api_hash,
        device_model=preset.device_model, system_version=preset.system_version,
        app_version=preset.app_version, lang_code=preset.lang_code,
        system_lang_code=preset.system_lang_code,
        proxy=proxy.to_telethon() if proxy else None,
    )


def _new_client(preset_key: str = "random"):
    cfg = load_config()
    preset = get_preset(preset_key)
    credential = cfg.api_credential_for_index(None)
    proxy = cfg.proxy_for_index(None)
    client = TelegramClient(
        StringSession(), credential.api_id, credential.api_hash,
        device_model=preset.device_model, system_version=preset.system_version,
        app_version=preset.app_version, lang_code=preset.lang_code,
        system_lang_code=preset.system_lang_code,
        proxy=proxy.to_telethon() if proxy else None,
    )
    return client, preset


def _invite_hash(target: str) -> str | None:
    text = target.strip()
    if "joinchat/" in text:
        return text.split("joinchat/", 1)[1].split("?", 1)[0].strip("/")
    if "t.me/+" in text:
        return text.split("t.me/+", 1)[1].split("?", 1)[0].strip("/")
    if text.startswith("+"):
        return text[1:].split("?", 1)[0].strip("/")
    return None


def _chatlist_slug(target: str) -> str | None:
    text = target.strip()
    if "t.me/addlist/" in text:
        return text.split("t.me/addlist/", 1)[1].split("?", 1)[0].strip("/")
    if "telegram.me/addlist/" in text:
        return text.split("telegram.me/addlist/", 1)[1].split("?", 1)[0].strip("/")
    if text.startswith("addlist/"):
        return text.split("addlist/", 1)[1].split("?", 1)[0].strip("/")
    return None


def _public_target(target: str) -> str:
    text = target.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.lstrip("@").split("?", 1)[0].strip("/")


_TELEGRAM_URL_RE = re.compile(
    r"(?:https?://)?(?:t\.me|telegram\.me)/"
    r"(?:addlist/[A-Za-z0-9_-]+|\+[A-Za-z0-9_-]+|joinchat/[A-Za-z0-9_-]+|[A-Za-z0-9_][A-Za-z0-9_/?=&.-]*)",
    re.IGNORECASE,
)
_TELEGRAM_USERNAME_RE = re.compile(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]{5,32}\b")


def _clean_target_token(token: str) -> str:
    return token.strip().strip(".,;:()[]{}<>")


def _extract_group_targets(text: str) -> list[str]:
    """Extract Telegram targets from pasted labels, usernames, links, or IDs."""
    targets: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        target = _clean_target_token(raw)
        if not target:
            return
        key = target.lower()
        if key in seen:
            return
        seen.add(key)
        targets.append(target)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        for match in _TELEGRAM_URL_RE.findall(line):
            add(match)
        for match in _TELEGRAM_USERNAME_RE.findall(line):
            add(match)

        compact = _clean_target_token(line)
        if _TELEGRAM_URL_RE.fullmatch(compact):
            add(compact)
        elif compact.startswith(("+", "addlist/", "joinchat/")):
            add(compact)
        elif re.fullmatch(r"-?\d{5,}", compact):
            add(compact)

    return targets


def _chatlist_peers(invite) -> list:
    if hasattr(invite, "peers"):
        return list(invite.peers or [])
    peers = []
    peers.extend(getattr(invite, "missing_peers", []) or [])
    peers.extend(getattr(invite, "already_peers", []) or [])
    return peers


async def _join_and_resolve_chatlist(client: TelegramClient, target: str) -> list:
    from telethon.tl.functions.chatlists import CheckChatlistInviteRequest, JoinChatlistInviteRequest

    slug = _chatlist_slug(target)
    if not slug:
        return []

    invite = await client(CheckChatlistInviteRequest(slug))
    peers = _chatlist_peers(invite)
    input_peers = []
    for peer in peers:
        try:
            input_peers.append(await client.get_input_entity(peer))
        except Exception:
            pass

    if input_peers:
        try:
            await client(JoinChatlistInviteRequest(slug, input_peers))
        except Exception:
            pass

    entities = []
    for peer in peers:
        try:
            entities.append(await client.get_entity(peer))
        except Exception:
            pass
    return entities


async def _join_and_resolve_target(client: TelegramClient, target: str):
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest

    invite = _invite_hash(target)
    if invite:
        try:
            updates = await client(ImportChatInviteRequest(invite))
            if getattr(updates, "chats", None):
                return updates.chats[0]
        except Exception:
            invite_info = await client(CheckChatInviteRequest(invite))
            chat = getattr(invite_info, "chat", None)
            if chat:
                return chat
            raise

    public = _public_target(target)
    if public.lstrip("-").isdigit():
        return await client.get_entity(int(public))
    try:
        updates = await client(JoinChannelRequest(public))
        if getattr(updates, "chats", None):
            return updates.chats[0]
    except Exception:
        pass
    return await client.get_entity(public)


async def _broadcast_entities_for_target(client: TelegramClient, target: str) -> list:
    if _chatlist_slug(target):
        entities = await _join_and_resolve_chatlist(client, target)
        if entities:
            return entities
    return [await _join_and_resolve_target(client, target)]


def _categorize_broadcast_error(exc: BaseException) -> str:
    if isinstance(exc, AuthKeyDuplicatedError):
        return "Duplicated auth key/session IP conflict"
    if isinstance(exc, SlowModeWaitError):
        return f"SlowMode {exc.seconds}s"
    if isinstance(exc, FloodWaitError):
        return f"Flood {exc.seconds}s"
    if isinstance(exc, UserBannedInChannelError):
        return "Banned from group"
    if isinstance(exc, ChatWriteForbiddenError):
        return "Muted / can't write"
    if isinstance(exc, ChatAdminRequiredError):
        return "Admin-only chat / admin required"
    if isinstance(exc, ChatSendPlainTextForbiddenError):
        return "Text disabled / plain text forbidden"
    if isinstance(exc, MessageActionForbiddenError):
        return "Text/action disabled in chat"
    if isinstance(exc, (ChannelPrivateError, ChannelInvalidError, ChatIdInvalidError, PeerIdInvalidError)):
        return "Group inaccessible/private/invalid peer"
    if isinstance(exc, (UsernameNotOccupiedError, UsernameInvalidError)):
        return "Invalid username/link"
    if isinstance(exc, (InviteHashExpiredError, InviteHashInvalidError)):
        return "Invalid/expired invite link"
    if isinstance(exc, (UserRestrictedError, UserPrivacyRestrictedError, UserIsBlockedError)):
        return "Restricted/privacy/blocked target"
    if isinstance(exc, InputUserDeactivatedError):
        return "Target user deactivated"
    if isinstance(exc, (OSError, TimeoutError, ConnectionError)):
        return f"Proxy/Network {type(exc).__name__}"
    detail = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__


def _log_chat_id():
    """Get log destination from env. Can be user_id (int) or @username."""
    raw = os.getenv("LOG_CHAT_ID", "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return raw  # username like @mylogchannel


def _owner_ids() -> set[int]:
    raw = os.getenv("OWNER_IDS", "") or os.getenv("OWNER_ID", "")
    ids: set[int] = set()
    for part in raw.replace(",", " ").split():
        try:
            ids.add(int(part.strip()))
        except ValueError:
            continue
    return ids


def _is_owner(user_id: int) -> bool:
    return user_id in _owner_ids()


def _owner_error_alert_cooldown() -> float:
    raw = os.getenv("OWNER_ERROR_ALERT_COOLDOWN_SECONDS", "600")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 600.0


def _runtime_error_alert_key(context: str, exc: BaseException) -> str:
    detail = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{context}|{type(exc).__name__}|{detail[:160]}"


def _should_send_owner_error_alert(key: str, now: float) -> bool:
    cooldown = _owner_error_alert_cooldown()
    last_sent = _owner_error_alerts.get(key)
    if last_sent is not None and now - last_sent < cooldown:
        return False
    _owner_error_alerts[key] = now
    return True


async def _notify_owners(bot: Bot, text: str) -> None:
    for owner_id in _owner_ids():
        try:
            await bot.send_message(owner_id, text)
        except Exception:
            log.exception("Failed to send owner runtime error alert to %s", owner_id)


async def _alert_runtime_error(bot: Bot, context: str, exc: BaseException) -> None:
    import time

    key = _runtime_error_alert_key(context, exc)
    if not _should_send_owner_error_alert(key, time.monotonic()):
        return
    detail = str(exc).strip().splitlines()[0] if str(exc).strip() else "-"
    if len(detail) > 500:
        detail = detail[:500] + "..."
    await _notify_owners(
        bot,
        "Runtime error\n"
        f"Context: {context}\n"
        f"Error: {type(exc).__name__}\n"
        f"Detail: {detail}",
    )


def _vip_label(user_id: int) -> str:
    if _is_owner(user_id):
        return "OWNER"
    try:
        if is_vip_admin(user_id):
            return "VIP"
    except Exception:
        log.exception("Failed to fetch VIP status for %s", user_id)
    return "FREE"


def _watermark_for_user(user_id: int) -> str:
    if _vip_label(user_id) in {"OWNER", "VIP"}:
        return ""
    return os.getenv("WATERMARK", "")


def _auto_health_check_interval_seconds() -> int:
    raw = os.getenv("AUTO_HEALTH_CHECK_INTERVAL_SECONDS", "43200")
    try:
        return max(3600, int(raw))
    except ValueError:
        return 43200


def _is_terminal_account_error(exc: BaseException) -> bool:
    return isinstance(exc, (AuthKeyUnregisteredError, SessionRevokedError, UserDeactivatedBanError))


async def _remove_invalid_account(
    bot: Bot,
    admin_id: int,
    acc: AccountRow,
    reason: str,
    *,
    notify_admin: bool = True,
) -> None:
    removed = remove_account(admin_id, acc.phone)
    if not removed:
        return
    detail = f"[{acc.alias}] removed from account list: {reason}"
    log.warning("Auto-removed invalid account admin=%s alias=%s reason=%s", admin_id, acc.alias, reason)
    if notify_admin:
        username = f"@{acc.username}" if acc.username else "-"
        try:
            await bot.send_message(
                admin_id,
                "⚠️ Invalid session auto-removed\n"
                f"Account: {acc.alias}\n"
                f"Username: {username}\n"
                f"Reason: {reason}\n\n"
                "Session akun ini sudah tidak valid, jadi akun otomatis dihapus dari daftar akun.",
            )
        except Exception:
            log.exception("Failed to notify admin about auto-removed account")
    await _notify_owners(bot, f"Invalid account auto-removed\nAdmin: {admin_id}\nAccount: {acc.alias}\nReason: {reason}")


async def _check_account_health(bot: Bot, admin_id: int, acc: AccountRow) -> None:
    async with account_lease(admin_id, acc.phone, purpose="health_check", ttl_seconds=60, wait_seconds=0) as lease:
        if not lease.acquired:
            log.debug("health check skipped locked account admin=%s alias=%s", admin_id, acc.alias)
            return
        client = _client_from_session(acc.session_string, acc.device_preset, acc)
        try:
            await client.connect()
            await client.get_me()
            _mark_account_connected(acc)
        except Exception as exc:
            if _is_terminal_account_error(exc):
                await _remove_invalid_account(
                    bot,
                    admin_id,
                    acc,
                    type(exc).__name__,
                    notify_admin=not isinstance(exc, UserDeactivatedBanError),
                )
            else:
                await _alert_runtime_error(bot, f"auto account health check admin={admin_id} account={acc.alias}", exc)
        finally:
            if client.is_connected():
                await client.disconnect()


async def _run_auto_health_check_once(bot: Bot) -> None:
    for admin_id in get_admin_ids():
        for acc in get_accounts(admin_id):
            await _check_account_health(bot, admin_id, acc)
            await asyncio.sleep(2)


async def _auto_health_check_loop(bot: Bot) -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await cleanup_stale_leases()
            await _run_auto_health_check_once(bot)
        except Exception as exc:
            await _alert_runtime_error(bot, "auto health check scheduler", exc)
        await asyncio.sleep(_auto_health_check_interval_seconds())


async def _auto_reply_reconcile_loop() -> None:
    """Periodically reconcile auto-reply clients with DB state."""
    from .auto_reply import reconcile_auto_reply_clients

    await asyncio.sleep(30)  # initial delay
    interval = int(os.getenv("AUTO_REPLY_RECONCILE_INTERVAL", "90"))
    while True:
        try:
            await reconcile_auto_reply_clients()
        except Exception:
            log.debug("auto_reply reconcile error", exc_info=True)
        await asyncio.sleep(interval)


def _display_user_name(user) -> str:
    parts = [getattr(user, "first_name", "") or "", getattr(user, "last_name", "") or ""]
    name = " ".join(part for part in parts if part).strip()
    return name or (f"@{user.username}" if getattr(user, "username", None) else str(user.id))


def _profile_value(value) -> str:
    return str(value) if value else "-"


def _welcome_text(message: Message, uid: int, accounts: list, status: str) -> str:
    user = message.from_user
    return t(
        "welcome_new" if not accounts else "main_menu",
        uid,
        n=len(accounts),
        name=_display_user_name(user),
        username=f"@{user.username}" if getattr(user, "username", None) else "-",
        telegram_id=uid,
        first_name=_profile_value(getattr(user, "first_name", "")),
        last_name=_profile_value(getattr(user, "last_name", "")),
        status=status,
    )


def _main_kb(uid: int = 0, has_accounts: bool = True) -> ReplyKeyboardMarkup:
    lang = get_lang(uid) if uid else "id"
    labels = _MENU_LABELS.get(lang, _MENU_LABELS["id"])
    if not has_accounts:
        keyboard = [[KeyboardButton(text=labels[0])], [KeyboardButton(text=labels[7])]]
    else:
        keyboard = [
            [KeyboardButton(text=labels[0]), KeyboardButton(text=labels[1])],
            [KeyboardButton(text=labels[2]), KeyboardButton(text=labels[3])],
            [KeyboardButton(text=labels[4]), KeyboardButton(text=labels[8])],
            [KeyboardButton(text=labels[5]), KeyboardButton(text=labels[6])],
            [KeyboardButton(text=labels[7])],
        ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


_MENU_LABELS = {
    "id": [
        "➕ Tambah Akun", "👤 Akun Saya",
        "📣 Broadcast", "💬 Kelola Text",
        "👥 Manage Group", "🗑 Hapus/Logout",
        "🔄 Transfer", "🌐 Bahasa", "💬 Auto Reply",
    ],
    "en": [
        "➕ Add Account", "👤 My Accounts",
        "📣 Broadcast", "💬 Manage Text",
        "👥 Manage Group", "🗑 Remove/Logout",
        "🔄 Transfer", "🌐 Language", "💬 Auto Reply",
    ],
    "ms": [
        "➕ Tambah Akaun", "👤 Akaun Saya",
        "📣 Broadcast", "💬 Kelola Text",
        "👥 Manage Group", "🗑 Hapus/Logout",
        "🔄 Transfer", "🌐 Bahasa", "💬 Auto Reply",
    ],
    "th": [
        "➕ เพิ่มบัญชี", "👤 บัญชีของฉัน",
        "📣 Broadcast", "💬 Manage Text",
        "👥 Manage Group", "🗑 ลบ/Logout",
        "🔄 โอนข้อมูล", "🌐 ภาษา", "💬 Auto Reply",
    ],
    "vi": [
        "➕ Thêm TK", "👤 Tài khoản",
        "📣 Broadcast", "💬 Manage Text",
        "👥 Manage Group", "🗑 Xóa/Logout",
        "🔄 Chuyển", "🌐 Ngôn ngữ", "💬 Auto Reply",
    ],
    "zh": [
        "➕ 添加账号", "👤 我的账号",
        "📣 广播", "💬 Manage Text",
        "👥 Manage Group", "🗑 删除/登出",
        "🔄 转移", "🌐 语言", "💬 Auto Reply",
    ],
    "ja": [
        "➕ アカウント追加", "👤 マイアカウント",
        "📣 ブロードキャスト", "💬 Manage Text",
        "👥 Manage Group", "🗑 削除/ログアウト",
        "🔄 転送", "🌐 言語", "💬 Auto Reply",
    ],
    "ko": [
        "➕ 계정 추가", "👤 내 계정",
        "📣 브로드캐스트", "💬 Manage Text",
        "👥 Manage Group", "🗑 삭제/로그아웃",
        "🔄 전송", "🌐 언어", "💬 Auto Reply",
    ],
    "hi": [
        "➕ अकाउंट जोड़ें", "👤 मेरे अकाउंट",
        "📣 Broadcast", "💬 Manage Text",
        "👥 Manage Group", "🗑 हटाएं/Logout",
        "🔄 ट्रांसफर", "🌐 भाषा", "💬 Auto Reply",
    ],
    "fil": [
        "➕ Dagdag Account", "👤 Mga Account",
        "📣 Broadcast", "💬 Manage Text",
        "👥 Manage Group", "🗑 Remove/Logout",
        "🔄 Transfer", "🌐 Wika", "💬 Auto Reply",
    ],
}


def _get_menu_action(text: str) -> str | None:
    """Map any language button text to action key."""
    for labels in _MENU_LABELS.values():
        if text in labels:
            idx = labels.index(text)
            return ["add", "accounts", "broadcast", "saved",
                    "lists", "cleanup", "transfer", "lang", "auto_reply"][idx]
    return None


def _saved_message_at(admin_id: int, raw_index: str) -> dict | None:
    try:
        index = int(raw_index)
    except ValueError:
        return None
    saved = get_saved_messages(admin_id)
    if 0 <= index < len(saved):
        return saved[index]
    return None


def _saved_message_buttons(admin_id: int, prefix: str) -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton(text=s["name"], callback_data=f"{prefix}:{index}")]
        for index, s in enumerate(get_saved_messages(admin_id))
    ]


def _back_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="<< Menu")]],
        resize_keyboard=True,
    )


def _phone_contact_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📂 Kirim Nomor", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def _accounts_kb(admin_id: int) -> ReplyKeyboardMarkup:
    """Generate account selection as reply keyboard."""
    accounts = get_accounts(admin_id)
    buttons = [[KeyboardButton(text=a.alias)] for a in accounts]
    buttons.append([KeyboardButton(text="<< Menu")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def _auto_reply_account_label(acc: AccountRow) -> str:
    identity = f"@{acc.username}" if acc.username else acc.display_name
    if not identity:
        identity = acc.alias
    if acc.user_id:
        return f"{acc.user_id} {identity}"
    return identity


def _auto_reply_accounts_kb(admin_id: int) -> tuple[ReplyKeyboardMarkup, dict[str, str]]:
    accounts = get_accounts(admin_id)
    label_map: dict[str, str] = {}
    buttons = []
    for acc in accounts:
        label = _auto_reply_account_label(acc)
        label_map[label] = acc.alias
        buttons.append([KeyboardButton(text=label)])
    buttons.append([KeyboardButton(text="<< Menu")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True), label_map


def _auto_reply_choice_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="✅ ON / Set Text"), KeyboardButton(text="❌ OFF")],
            [KeyboardButton(text="<< Menu")],
        ],
        resize_keyboard=True,
    )


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    uid = message.from_user.id
    if is_managed_account(uid):
        await message.answer("Access denied. Your account is managed by another admin.")
        return
    register_admin(uid, message.from_user.username or "", message.from_user.first_name or "")
    # Load language preference
    lang = get_admin_lang(uid)
    set_lang(uid, lang)
    accounts = get_accounts(uid)
    _state.pop(uid, None)
    status = _vip_label(uid)
    if not accounts:
        await message.answer(
            _welcome_text(message, uid, accounts, status),
            reply_markup=_main_kb(uid, has_accounts=False),
            parse_mode="HTML",
        )
        return
    await message.answer(
        _welcome_text(message, uid, accounts, status),
        reply_markup=_main_kb(uid, has_accounts=True),
        parse_mode="HTML",
    )


@router.message(Command("vip", "status"))
async def cmd_vip_status(message: Message) -> None:
    uid = message.from_user.id
    await message.answer(f"User ID: `{uid}`\nStatus kamu: {_vip_label(uid)}", parse_mode="Markdown")


@router.message(Command("gift"))
async def cmd_gift(message: Message) -> None:
    uid = message.from_user.id
    if not _is_owner(uid):
        await message.answer("Access denied. Command ini khusus owner.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /gift <telegram_user_id>")
        return

    try:
        target_id = int(parts[1].strip())
    except ValueError:
        await message.answer("User ID harus angka. Contoh: /gift 123456789")
        return

    if not grant_vip(target_id, uid):
        await message.answer(
            f"Gagal verify VIP untuk user `{target_id}` setelah update DB. "
            "Cek kolom is_vip di table admins.",
            parse_mode="Markdown",
        )
        return
    await message.answer(f"VIP aktif untuk user `{target_id}`. Status: VIP", parse_mode="Markdown")


@router.message(Command("control"))
async def cmd_control(message: Message) -> None:
    uid = message.from_user.id
    if not _is_owner(uid):
        await message.answer("Access denied. Command ini khusus owner.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /control <userid|phone|username>")
        return

    target_id = resolve_admin_id(parts[1])
    if target_id is None:
        await message.answer("Admin tidak ditemukan. Pakai user ID, nomor HP, atau username yang sudah terdaftar.")
        return
    if target_id == uid:
        await message.answer("Data itu sudah ada di owner.")
        return

    count = transfer_all(target_id, uid)
    await message.answer(
        f"Control berhasil. Admin `{target_id}` dipindahkan ke owner `{uid}`.\n"
        f"Transferred {count} item(s).",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Main menu button handlers
# ---------------------------------------------------------------------------
@router.message(F.text.in_({"<< Menu", "<< menu"}))
async def btn_menu(message: Message) -> None:
    uid = message.from_user.id
    _state.pop(uid, None)
    # Delete user's message and previous bot message
    try:
        await message.delete()
    except Exception:
        pass
    prev = _last_bot_msg.pop(uid, None)
    if prev:
        try:
            await message.bot.delete_message(message.chat.id, prev)
        except Exception:
            pass
    accounts = get_accounts(uid)
    if not accounts:
        _state[uid] = {"action": "login_phone"}
        await message.answer(t("welcome_new", uid), reply_markup=_back_kb())
        return
    await message.answer(t("main_menu", uid, n=len(accounts)), reply_markup=_main_kb(uid))


@router.callback_query(F.data.startswith("lang:"))
async def cb_lang(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    set_lang(uid, cq.data[5:])
    set_admin_lang(uid, cq.data[5:])
    await cq.message.edit_text(t("lang_changed", uid))
    await cq.message.answer(t("main_menu", uid, n=len(get_accounts(uid))), reply_markup=_main_kb(uid))


@router.callback_query(F.data.startswith("bc:"))
async def cb_bc(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    list_name = cq.data[3:]
    _state[uid] = {"action": "broadcast_mode_choice", "list": list_name}
    buttons = [
        [InlineKeyboardButton(text="Single Text", callback_data="bm:single")],
        [InlineKeyboardButton(text="Multi Random", callback_data="bm:multi")],
        [InlineKeyboardButton(text="New message", callback_data="newmsg")],
    ]
    await cq.message.edit_text(
        f"List: {list_name}\nPilih mode text broadcast:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "bm:single")
async def cb_bm_single(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    saved = get_saved_messages(uid)
    if not saved:
        await cq.message.edit_text("Belum ada text tersimpan. Simpan text dulu di Kelola Text.")
        _state.pop(uid, None)
        return
    _state[uid]["action"] = "broadcast_msg_choice"
    buttons = _saved_message_buttons(uid, "sm")
    await cq.message.edit_text(
        "Pilih text tersimpan:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "bm:multi")
async def cb_bm_multi(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    saved = get_saved_messages(uid)
    if not saved:
        await cq.message.edit_text("Belum ada text tersimpan. Simpan text dulu di Kelola Text.")
        _state.pop(uid, None)
        return
    _state[uid]["saved_texts"] = [s["text"] for s in saved]
    _state[uid]["text_mode"] = "multi_random"
    _state[uid]["action"] = "broadcast_delay_group"
    buttons = [[InlineKeyboardButton(text="Auto (3-10s)", callback_data="dg:auto"),
                InlineKeyboardButton(text="No delay", callback_data="dg:none")]]
    await cq.message.edit_text(
        f"Multi Random aktif ({len(saved)} text).\nDelay antar group?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "newmsg")
async def cb_newmsg(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    _state[uid]["action"] = "broadcast_msg"
    await cq.message.edit_text(t("broadcast_send_msg", uid))


@router.callback_query(F.data.startswith("sm:"))
async def cb_sm(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    found = _saved_message_at(uid, cq.data[3:])
    if not found:
        await cq.message.edit_text("Not found.")
        return
    _state[uid]["saved_text"] = found["text"]
    _state[uid]["action"] = "broadcast_delay_group"
    buttons = [[InlineKeyboardButton(text="Auto (3-10s)", callback_data="dg:auto"),
                InlineKeyboardButton(text="No delay", callback_data="dg:none")]]
    await cq.message.edit_text(
        "Delay antar group?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "savetext")
async def cb_savetext(cq: CallbackQuery) -> None:
    await cq.answer()
    _state[cq.from_user.id] = {"action": "savetext_name"}
    await cq.message.edit_text("Nama text tersimpan:")


@router.callback_query(F.data.startswith("sv:"))
async def cb_sv(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    found = _saved_message_at(uid, cq.data[3:])
    if not found:
        await cq.message.edit_text("Text tidak ditemukan.")
        return
    name = found["name"]
    preview = found.get("text", "")
    if len(preview) > 3000:
        preview = preview[:3000] + "\n\n..."
    buttons = [[InlineKeyboardButton(text="🗑 Delete", callback_data=f"sd:{cq.data[3:]}")]]
    await cq.message.edit_text(
        f"💬 {name}\n\n{preview}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("sd:"))
async def cb_sd(cq: CallbackQuery) -> None:
    await cq.answer()
    found = _saved_message_at(cq.from_user.id, cq.data[3:])
    if not found:
        await cq.message.edit_text("Text tidak ditemukan.")
        return
    delete_saved_msg(cq.from_user.id, found["name"])
    await cq.message.edit_text("Text tersimpan dihapus.")


@router.callback_query(F.data.startswith("dt:"))
async def cb_dt(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    _state[uid]["action"] = "broadcast_delay_group"
    buttons = [[InlineKeyboardButton(text="Auto (3-10s)", callback_data="dv:auto"),
                InlineKeyboardButton(text="No delay", callback_data="dv:none")]]
    await cq.message.edit_text("Delay antar group?", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("dg:"))
async def cb_dg(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    _state[uid]["group_delay"] = (3.0, 10.0) if cq.data[3:] == "auto" else (0.0, 0.0)
    _state[uid]["action"] = "broadcast_delay_round"
    buttons = [[InlineKeyboardButton(text="Auto (3-10s)", callback_data="dr:auto"),
                InlineKeyboardButton(text="No delay", callback_data="dr:none")]]
    await cq.message.edit_text(
        "Delay setelah semua group selesai?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("dr:"))
async def cb_dr(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    _state[uid]["round_delay"] = (3.0, 10.0) if cq.data[3:] == "auto" else (0.0, 0.0)
    _state[uid]["action"] = "broadcasting"
    await cq.message.edit_text(t("broadcast_running", uid))
    await _start_broadcast(cq.message, uid)


@router.callback_query(F.data.startswith("dv:"))
async def cb_dv(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    if cq.data[3:] == "auto":
        _state[uid]["group_delay"] = (3.0, 10.0)
    else:
        _state[uid]["group_delay"] = (0.0, 0.0)
    _state[uid]["action"] = "broadcast_delay_round"
    buttons = [[InlineKeyboardButton(text="Auto (3-10s)", callback_data="dr:auto"),
                InlineKeyboardButton(text="No delay", callback_data="dr:none")]]
    await cq.message.edit_text(
        "Delay setelah semua group selesai?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data == "createlist")
async def cb_cl(cq: CallbackQuery) -> None:
    await cq.answer()
    _state[cq.from_user.id] = {"action": "createlist_name"}
    await cq.message.edit_text("Enter list name:")


@router.callback_query(F.data.startswith("vl:"))
async def cb_vl(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    bl = get_list(uid, cq.data[3:])
    if not bl:
        await cq.message.edit_text("Not found.")
        return
    targets = "\n".join(f"  {i}. {t_}" for i, t_ in enumerate(bl.targets, 1))
    buttons = [
        [InlineKeyboardButton(text="➕ Add", callback_data=f"la:{bl.name}"),
         InlineKeyboardButton(text="➖ Remove", callback_data=f"lr:{bl.name}")],
        [InlineKeyboardButton(text="🗑 Delete List", callback_data=f"dl:{bl.name}")],
    ]
    await cq.message.edit_text(f"📋 {bl.name}:\n{targets}", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("la:"))
async def cb_la(cq: CallbackQuery) -> None:
    """Add targets to list."""
    await cq.answer()
    uid = cq.from_user.id
    _state[uid] = {"action": "listadd_targets", "list": cq.data[3:]}
    await cq.message.edit_text(
        f"[{cq.data[3:]}] Kirim target yang mau ditambah:\n"
        "(boleh paste daftar campur nama + @username / chat_id / https://t.me/xxx / addlist)")


@router.callback_query(F.data.startswith("lr:"))
async def cb_lr(cq: CallbackQuery) -> None:
    """Show numbered targets to remove."""
    await cq.answer()
    uid = cq.from_user.id
    bl = get_list(uid, cq.data[3:])
    if not bl or not bl.targets:
        await cq.message.edit_text("List kosong.")
        return
    _state[uid] = {"action": "listremove_pick", "list": bl.name}
    targets = "\n".join(f"  {i}. {t_}" for i, t_ in enumerate(bl.targets, 1))
    await cq.message.edit_text(
        f"[{bl.name}] Ketik nomor yang mau dihapus (pisah spasi):\n{targets}")


@router.callback_query(F.data.startswith("dl:"))
async def cb_dl(cq: CallbackQuery) -> None:
    await cq.answer()
    remove_list(cq.from_user.id, cq.data[3:])
    await cq.message.edit_text("Deleted.")


@router.callback_query(F.data.startswith("edit:"))
async def cb_edit(cq: CallbackQuery) -> None:
    await cq.answer()
    alias = cq.data[5:]
    buttons = [[InlineKeyboardButton(text="Name", callback_data=f"en:{alias}"),
                InlineKeyboardButton(text="Bio", callback_data=f"eb:{alias}"),
                InlineKeyboardButton(text="Username", callback_data=f"eu:{alias}")]]
    await cq.message.edit_text(f"[{alias}] Edit:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("en:"))
async def cb_en(cq: CallbackQuery) -> None:
    await cq.answer()
    _state[cq.from_user.id] = {"action": "edit_name", "alias": cq.data[3:]}
    await cq.message.edit_text("Enter new name (first last):")


@router.callback_query(F.data.startswith("eb:"))
async def cb_eb(cq: CallbackQuery) -> None:
    await cq.answer()
    _state[cq.from_user.id] = {"action": "edit_bio", "alias": cq.data[3:]}
    await cq.message.edit_text("Enter new bio:")


@router.callback_query(F.data.startswith("eu:"))
async def cb_eu(cq: CallbackQuery) -> None:
    await cq.answer()
    _state[cq.from_user.id] = {"action": "edit_username", "alias": cq.data[3:]}
    await cq.message.edit_text("Enter new username (without @):")


@router.callback_query(F.data.startswith("ar:"))
async def cb_ar(cq: CallbackQuery) -> None:
    """Auto Reply toggle / setup."""
    await cq.answer()
    uid = cq.from_user.id
    alias = cq.data[3:]
    acc = find_account(uid, alias)
    if not acc:
        await cq.message.edit_text("Not found.")
        return
    if acc.auto_reply_enabled:
        # Disable
        try:
            update_auto_reply(uid, acc.phone, enabled=False)
        except Exception as e:
            await cq.message.edit_text(f"Error disabling auto-reply: {e}")
            return
        await cq.message.edit_text(f"[{alias}] Auto Reply disabled.")
    else:
        _state[uid] = {"action": "auto_reply_text", "alias": alias}
        await cq.message.edit_text(
            f"[{alias}] Auto Reply\n\n"
            "Send the auto-reply message you want to use.\n"
            "Formatting (bold, italic, links, etc.) will be preserved exactly as you send it.\n\n"
            "This will only reply to NEW chats (users who never messaged before)."
        )


@router.callback_query(F.data.startswith("clean:"))
async def cb_clean(cq: CallbackQuery) -> None:
    await cq.answer()
    alias = cq.data[6:]
    buttons = [[InlineKeyboardButton(text="Logout", callback_data=f"lo:{alias}"),
                InlineKeyboardButton(text="Remove", callback_data=f"rm:{alias}")]]
    await cq.message.edit_text(f"[{alias}]:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("lo:"))
async def cb_lo(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    alias = cq.data[3:]
    acc = find_account(uid, alias)
    if not acc:
        await cq.message.edit_text("Not found.")
        return

    result_message = f"[{alias}] Logged out and removed from the account list."
    async with account_lease(uid, acc.phone, purpose="logout", ttl_seconds=60, wait_seconds=10) as lease:
        if not lease.acquired:
            await cq.message.edit_text(f"[{alias}] Account is busy. Try again later.")
            return
        client = _client_from_session(acc.session_string, acc.device_preset, acc)
        try:
            await client.connect()
            await client.log_out()
        except Exception as exc:
            if _is_terminal_account_error(exc):
                result_message = f"[{alias}] Session was already invalid and has been removed from the account list."
            else:
                await cq.message.edit_text(f"[{alias}] Logout failed: {type(exc).__name__}: {exc}")
                return
        finally:
            if client.is_connected():
                await client.disconnect()

    removed = remove_account(uid, acc.phone)
    if not removed:
        await cq.message.edit_text(f"[{alias}] Logout finished, but failed to remove the account from the list.")
        return
    await cq.message.edit_text(result_message)


@router.callback_query(F.data.startswith("rm:"))
async def cb_rm(cq: CallbackQuery) -> None:
    await cq.answer()
    remove_account(cq.from_user.id, cq.data[3:])
    await cq.message.edit_text("Removed.")


@router.callback_query(F.data.startswith("acc:"))
async def cb_acc(cq: CallbackQuery) -> None:
    await cq.answer()
    acc = find_account(cq.from_user.id, cq.data[4:])
    if not acc:
        await cq.message.edit_text("Not found.")
        return
    name = acc.display_name
    info = f"👤 {name}\n📱 {acc.phone}"
    if acc.username:
        info += f"\n🔗 @{acc.username}"
    info += f"\n🔐 2FA: {'yes' if acc.is_2fa else 'no'}"
    info += f"\n📟 Device: {acc.device_preset}"
    if acc.last_connected_at:
        info += f"\n🕐 Last connected: {acc.last_connected_at}"
    if acc.connected_ip:
        info += f"\n🌐 Proxy/IP: {acc.connected_ip}"
    if acc.broadcast_status:
        info += f"\n📣 Broadcast: {acc.broadcast_status}"
    if acc.broadcast_updated_at:
        info += f"\n♻️ Updated: {acc.broadcast_updated_at}"
    ar_label = "✅ Auto Reply ON" if acc.auto_reply_enabled else "💤 Auto Reply OFF"
    buttons = [
        [InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit:{acc.alias}"),
         InlineKeyboardButton(text="📨 OTP", callback_data=f"otp:{acc.alias}")],
        [InlineKeyboardButton(text=ar_label, callback_data=f"ar:{acc.alias}")],
        [InlineKeyboardButton(text="🗑 Remove", callback_data=f"clean:{acc.alias}")],
    ]
    await cq.message.edit_text(info, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("otp:"))
async def cb_otp(cq: CallbackQuery) -> None:
    """Fetch latest OTP/verification codes from Telegram service messages."""
    await cq.answer()
    uid = cq.from_user.id
    acc = find_account(uid, cq.data[4:])
    if not acc:
        await cq.message.edit_text("Not found.")
        return
    async with account_lease(uid, acc.phone, purpose="otp", ttl_seconds=60, wait_seconds=5) as lease:
        if not lease.acquired:
            await cq.message.edit_text(f"[{acc.alias}] Account is busy. Try again later.")
            return
        client = _client_from_session(acc.session_string, acc.device_preset, acc)
        try:
            await client.connect()
            from telethon.tl.types import InputPeerUser
            from datetime import datetime, timezone
            import re as _re
            codes = []
            async for msg in client.iter_messages(777000, limit=5):
                # 777000 is Telegram's official service notifications
                nums = _re.findall(r"\b\d{4,6}\b", msg.text or "")
                if nums:
                    ts = msg.date.astimezone(timezone.utc).strftime("%H:%M:%S")
                    codes.append(f"🕐 {ts} — 🔑 {nums[0]}")
            if not codes:
                await cq.message.edit_text(f"[{acc.alias}] Tidak ada OTP terbaru.")
            else:
                await cq.message.edit_text(
                    f"📨 OTP [{acc.alias}]:\n\n" + "\n".join(codes))
        except Exception as e:
            await _alert_runtime_error(cq.message.bot, "otp lookup", e)
            await cq.message.edit_text(f"[{acc.alias}] Error: {type(e).__name__}")
        finally:
            if client.is_connected():
                await client.disconnect()


# ---------------------------------------------------------------------------
# Text message handler — processes all state-driven input
# ---------------------------------------------------------------------------
@router.message(F.photo | F.video | F.document | F.animation)
async def handle_media(message: Message) -> None:
    """Handle media messages — only relevant during broadcast_msg state."""
    uid = message.from_user.id
    state = _state.get(uid)
    if not state or state.get("action") != "broadcast_msg":
        return
    # Save full message and proceed to delay setup
    _state[uid]["message"] = message
    _state[uid]["action"] = "broadcast_save_ask"
    buttons = [
        [KeyboardButton(text="Save & continue"), KeyboardButton(text="Just continue")],
        [KeyboardButton(text="<< Menu")],
    ]
    await message.answer("Media received.\n\nSave this message for reuse?", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))


def _serialize_broadcast_media(media_bytes: Optional[bytes]) -> Optional[str]:
    if not media_bytes:
        return None
    return base64.b64encode(media_bytes).decode("ascii")


def _deserialize_broadcast_media(raw: Optional[str]) -> Optional[bytes]:
    if not raw:
        return None
    return base64.b64decode(raw.encode("ascii"))


def _build_broadcast_job_payload(
    uid: int,
    list_name: str,
    msg_text: str,
    saved_texts: list[str],
    has_media: bool,
    media_bytes: Optional[bytes],
    media_filename: Optional[str],
    group_delay: tuple[float, float],
    round_delay: tuple[float, float],
) -> dict:
    return {
        "job_id": f"bc-{uid}-{uuid.uuid4().hex[:12]}",
        "admin_id": uid,
        "list_name": list_name,
        "status": "running",
        "text_mode": "multi_random" if saved_texts else ("single" if msg_text else "message"),
        "message_html": msg_text,
        "saved_texts": saved_texts,
        "has_media": has_media,
        "media_blob_base64": _serialize_broadcast_media(media_bytes),
        "media_filename": media_filename,
        "group_delay_min": group_delay[0],
        "group_delay_max": group_delay[1],
        "round_delay_min": round_delay[0],
        "round_delay_max": round_delay[1],
        "round_num": 0,
        "started_at": _runtime_now(),
        "updated_at": _runtime_now(),
        "completed_at": None,
    }


def _message_text_for_send(base_text: str, saved_texts: list[str], watermark: str) -> str:
    selected_text = random.choice(saved_texts) if saved_texts else base_text
    if watermark:
        return (selected_text + f"\n\n{watermark}") if selected_text else watermark
    return selected_text


async def _run_broadcast_job(
    bot: Bot,
    uid: int,
    job: dict,
    accounts: list[AccountRow],
    *,
    stop_if_state_missing: bool,
    notify_chat_id: Optional[int],
) -> None:
    list_name = job["list_name"]
    bl = get_list(uid, list_name)
    if not bl or not accounts:
        update_broadcast_job(job["job_id"], status="failed", completed_at=_runtime_now())
        return

    saved_texts = list(job.get("saved_texts") or [])
    msg_text = job.get("message_html", "") or ""
    has_media = bool(job.get("has_media"))
    media_bytes = _deserialize_broadcast_media(job.get("media_blob_base64"))
    media_filename = job.get("media_filename")
    watermark = _watermark_for_user(uid)
    group_delay_min = float(job.get("group_delay_min") or 0.0)
    group_delay_max = float(job.get("group_delay_max") or 0.0)
    round_delay_min = float(job.get("round_delay_min") or 0.0)
    round_delay_max = float(job.get("round_delay_max") or 0.0)
    log_dest = _log_chat_id()
    round_num = int(job.get("round_num") or 0)

    def should_keep_running() -> bool:
        if stop_if_state_missing:
            return _state.get(uid, {}).get("action") == "broadcasting"
        current = get_broadcast_job(job["job_id"])
        return bool(current and current.get("status") == "running")

    while should_keep_running():
        round_num += 1
        update_broadcast_job(job["job_id"], round_num=round_num, status="running")
        round_success = []
        round_failed = []
        target_attempt = 0
        target_attempt_lock = asyncio.Lock()
        round_lock = asyncio.Lock()
        per_admin_sem = asyncio.Semaphore(max(1, load_config().broadcast_per_admin_concurrency))
        global_sem = _global_broadcast_semaphore()
        remaining_items = [item for item in get_broadcast_job_items(job["job_id"]) if item.get("status") != "success"]
        if not remaining_items:
            break
        items_by_phone: dict[str, list[dict]] = {}
        for item in remaining_items:
            items_by_phone.setdefault(item["account_phone"], []).append(item)
        total_targets = len(remaining_items)

        async def maybe_delay_after_target() -> None:
            nonlocal target_attempt
            async with target_attempt_lock:
                target_attempt += 1
                should_delay = should_keep_running() and target_attempt < total_targets and group_delay_max > 0
            if should_delay:
                await asyncio.sleep(random.uniform(group_delay_min, group_delay_max))

        async def append_success(line: str) -> None:
            async with round_lock:
                round_success.append(line)

        async def append_failed(line: str) -> None:
            async with round_lock:
                round_failed.append(line)

        async def run_account(acc: AccountRow) -> None:
            account_items = items_by_phone.get(acc.phone, [])
            if not account_items or not should_keep_running():
                return
            async with per_admin_sem:
                async with global_sem:
                    async with account_lease(acc.admin_id, acc.phone, purpose="broadcast", ttl_seconds=60, wait_seconds=0) as lease:
                        if not lease.acquired:
                            for item in account_items:
                                update_broadcast_job_item(job["job_id"], acc.phone, item["target"], status="failed", last_error="Account busy/locked")
                            await append_failed(f"{acc.alias}: busy/locked, skipped this round")
                            return
                        acc_success = []
                        acc_failed = []
                        client = None
                        try:
                            client = _client_from_session(acc.session_string, acc.device_preset, acc)
                            await client.connect()
                            await client.get_me()
                            _mark_account_connected(acc, status="broadcasting")
                            update_account_runtime(acc.admin_id, acc.phone, broadcast_job_id=job["job_id"], broadcast_updated_at=_runtime_now())
                            for item in account_items:
                                target = item["target"]
                                if not should_keep_running():
                                    break
                                update_broadcast_job_item(job["job_id"], acc.phone, target, status="running", attempts_increment=True)
                                try:
                                    entities = await _broadcast_entities_for_target(client, target)
                                    for entity in entities:
                                        try:
                                            clicked = await auto_verify_group(client, entity)
                                            if clicked:
                                                log.info("[%s] Auto-verified '%s' in %s", acc.alias, clicked, target)
                                        except Exception:
                                            pass
                                    sent_count = 0
                                    for entity in entities:
                                        text_to_send = _message_text_for_send(msg_text, saved_texts, watermark)
                                        if has_media and media_bytes:
                                            await client.send_file(entity, media_bytes, caption=text_to_send, parse_mode="html", file_name=media_filename)
                                        else:
                                            await client.send_message(entity, text_to_send, parse_mode="html")
                                        sent_count += 1
                                    update_broadcast_job_item(job["job_id"], acc.phone, target, status="success", last_error=None)
                                    success_line = f"{acc.alias} -> {target}"
                                    if sent_count > 1:
                                        success_line += f" ({sent_count} chats)"
                                    await append_success(success_line)
                                    acc_success.append(success_line)
                                except FloodWaitError as fw:
                                    detail = _categorize_broadcast_error(fw)
                                    update_broadcast_job_item(job["job_id"], acc.phone, target, status="failed", last_error=detail)
                                    failed_line = f"{acc.alias} -> {target}: {detail}"
                                    await append_failed(failed_line)
                                    acc_failed.append(failed_line)
                                    await asyncio.sleep(fw.seconds)
                                except Exception as ex:
                                    detail = _categorize_broadcast_error(ex)
                                    update_broadcast_job_item(job["job_id"], acc.phone, target, status="failed", last_error=detail)
                                    failed_line = f"{acc.alias} -> {target}: {detail}"
                                    await append_failed(failed_line)
                                    acc_failed.append(failed_line)
                                    if isinstance(ex, AuthKeyDuplicatedError):
                                        await _notify_owners(
                                            bot,
                                            "Auth key duplication detected\n"
                                            f"Admin: {uid}\n"
                                            f"Account: {acc.alias} ({acc.phone})\n"
                                            f"Proxy/IP: {acc.connected_ip or _proxy_host_for_account(acc) or '-'}\n"
                                            f"Last connected: {acc.last_connected_at or '-'}"
                                        )
                                await maybe_delay_after_target()
                        except Exception as ex:
                            detail = _categorize_broadcast_error(ex)
                            if _is_terminal_account_error(ex):
                                await _remove_invalid_account(
                                    bot,
                                    uid,
                                    acc,
                                    detail,
                                    notify_admin=not isinstance(ex, UserDeactivatedBanError),
                                )
                                failed_line = f"{acc.alias}: {detail} (auto-removed)"
                            else:
                                failed_line = f"{acc.alias}: {detail}"
                            update_account_runtime(acc.admin_id, acc.phone, broadcast_status="failed", broadcast_updated_at=_runtime_now())
                            await append_failed(failed_line)
                            acc_failed.append(failed_line)
                            await _alert_runtime_error(bot, "broadcast account runtime", ex)
                        finally:
                            if client and client.is_connected():
                                if log_dest and should_keep_running():
                                    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                                    log_lines = [
                                        f"Round {round_num} | {now}",
                                        f"Account: {acc.alias}",
                                        f"Sent: {len(acc_success)}",
                                    ]
                                    if acc_success:
                                        log_lines.append("Success:\n  " + "\n  ".join(acc_success[:30]))
                                    if acc_failed:
                                        log_lines.append(f"Failed: {len(acc_failed)}\n  " + "\n  ".join(acc_failed))
                                    try:
                                        await client.send_message(log_dest, "\n".join(log_lines))
                                    except Exception:
                                        pass
                                await client.disconnect()
                            if acc_failed and not acc_success:
                                update_account_runtime(acc.admin_id, acc.phone, broadcast_status="failed", broadcast_updated_at=_runtime_now())
                            else:
                                clear_account_broadcast_status(acc.admin_id, acc.phone)

        await asyncio.gather(*(run_account(acc) for acc in accounts))

        if should_keep_running():
            now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            log_lines = [f"Round {round_num} | {now}", f"Sent: {len(round_success)}"]
            if round_success:
                log_lines.append("Success:\n  " + "\n  ".join(round_success[:30]))
            if round_failed:
                log_lines.append(f"Failed: {len(round_failed)}\n  " + "\n  ".join(round_failed))
            if notify_chat_id is not None and not log_dest:
                try:
                    await bot.send_message(notify_chat_id, "\n".join(log_lines))
                except Exception:
                    pass
            remaining_after_round = [item for item in get_broadcast_job_items(job["job_id"]) if item.get("status") != "success"]
            if not remaining_after_round:
                reset_broadcast_items_for_next_round(job["job_id"])
            if round_delay_max > 0:
                await asyncio.sleep(random.uniform(round_delay_min, round_delay_max))
            else:
                await asyncio.sleep(1)
        else:
            break

    remaining = [item for item in get_broadcast_job_items(job["job_id"]) if item.get("status") != "success"]
    final_status = "completed" if not remaining else "interrupted"
    update_broadcast_job(job["job_id"], status=final_status, completed_at=_runtime_now())
    for acc in accounts:
        clear_account_broadcast_status(acc.admin_id, acc.phone)
    if notify_chat_id is not None:
        await bot.send_message(notify_chat_id, t("broadcast_stopped", uid), reply_markup=_main_kb(uid))


async def _start_broadcast(message: Message, uid: int) -> None:
    """Start the continuous broadcast loop."""
    st = _state.get(uid, {})
    bl = get_list(uid, st.get("list", ""))
    accounts = get_accounts(uid)
    if not bl or not accounts:
        _state.pop(uid, None)
        return

    group_delay_min, group_delay_max = st.get("group_delay", st.get("delay", (3.0, 10.0)))
    round_delay_min, round_delay_max = st.get("round_delay", (0.0, 0.0))

    media_bytes = None
    media_filename = None
    has_media = False
    saved_texts = st.get("saved_texts") or []
    if "saved_text" in st:
        msg_text = st["saved_text"]
    elif saved_texts:
        msg_text = ""
    elif "message" in st:
        src_msg = st["message"]
        raw_text = src_msg.text or src_msg.caption or ""
        entities = src_msg.entities or src_msg.caption_entities or []
        msg_text = _entities_to_html(raw_text, entities)
        has_media = bool(src_msg.photo or src_msg.video or src_msg.document or src_msg.animation)
        if has_media:
            if src_msg.photo:
                media_bytes = await src_msg.bot.download(src_msg.photo[-1], destination=None)
                media_filename = "photo.jpg"
            elif src_msg.video:
                media_bytes = await src_msg.bot.download(src_msg.video, destination=None)
                media_filename = src_msg.video.file_name or "video.mp4"
            elif src_msg.animation:
                media_bytes = await src_msg.bot.download(src_msg.animation, destination=None)
                media_filename = "animation.gif"
            elif src_msg.document:
                media_bytes = await src_msg.bot.download(src_msg.document, destination=None)
                media_filename = src_msg.document.file_name or "file"
    else:
        _state.pop(uid, None)
        return

    job = _build_broadcast_job_payload(
        uid,
        bl.name,
        msg_text,
        saved_texts,
        has_media,
        media_bytes,
        media_filename,
        (group_delay_min, group_delay_max),
        (round_delay_min, round_delay_max),
    )
    create_broadcast_job(job)
    upsert_broadcast_job_items([
        {
            "job_id": job["job_id"],
            "admin_id": uid,
            "account_phone": acc.phone,
            "target": target,
            "status": "pending",
            "last_error": None,
            "attempts": 0,
            "last_attempted_at": None,
        }
        for acc in accounts
        for target in bl.targets
    ])
    now = _runtime_now()
    for acc in accounts:
        update_account_runtime(
            acc.admin_id,
            acc.phone,
            broadcast_status="broadcasting",
            broadcast_job_id=job["job_id"],
            broadcast_updated_at=now,
        )
    await _run_broadcast_job(message.bot, uid, job, accounts, stop_if_state_missing=True, notify_chat_id=message.chat.id)
    _state.pop(uid, None)


async def _recover_broadcast_jobs(bot: Bot) -> None:
    await asyncio.sleep(15)
    for job in get_recoverable_broadcast_jobs():
        job_id = job["job_id"]
        reset_running_broadcast_items(job_id)
        update_broadcast_job(job_id, status="running")
        accounts = get_accounts(job["admin_id"])
        if not accounts:
            update_broadcast_job(job_id, status="failed", completed_at=_runtime_now())
            continue
        remaining = [item for item in get_broadcast_job_items(job_id) if item.get("status") != "success"]
        if not remaining:
            update_broadcast_job(job_id, status="completed", completed_at=_runtime_now())
            continue
        try:
            await bot.send_message(
                job["admin_id"],
                f"Resuming broadcast {job_id}\nList: {job['list_name']}\nRemaining: {len(remaining)} item(s)"
            )
        except Exception:
            pass
        asyncio.create_task(_run_broadcast_job(bot, job["admin_id"], job, accounts, stop_if_state_missing=False, notify_chat_id=job["admin_id"]))


async def _dispatch_menu(message: Message, uid: int, action: str) -> None:
    accounts = get_accounts(uid)
    # Must have at least 1 account to use anything except "add" and "lang"
    if not accounts and action not in ("add", "lang"):
        _state[uid] = {"action": "login_phone"}
        await _reply(message, uid, t("welcome_new", uid), reply_markup=_back_kb(), parse_mode="HTML")
        return
    if action == "add":
        _state[uid] = {"action": "login_phone"}
        await _reply(message, uid, t("enter_phone", uid), reply_markup=_phone_contact_kb(), parse_mode="HTML")
    elif action == "accounts":
        if not accounts:
            await _reply(message, uid, t("no_accounts", uid), reply_markup=_main_kb(uid), parse_mode="HTML")
            return
        buttons = []
        for a in accounts:
            label = a.display_name
            if a.username:
                label += f" (@{a.username})"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"acc:{a.alias}")])
        await _reply(message, uid, t("pick_account", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    elif action == "broadcast":
        lists = get_lists(uid)
        if not lists:
            await _reply(
                message,
                uid,
                "Belum ada group list. Buat dulu di Manage Group.",
                reply_markup=_main_kb(uid),
            )
            return
        buttons = [[InlineKeyboardButton(text=f"{bl.name} ({len(bl.targets)})", callback_data=f"bc:{bl.name}")] for bl in lists]
        await _reply(message, uid, t("broadcast_pick_list", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    elif action == "saved":
        saved = get_saved_messages(uid)
        buttons = _saved_message_buttons(uid, "sv")
        buttons.append([InlineKeyboardButton(text="+ Save Text", callback_data="savetext")])
        await _reply(message, uid, t("saved_text_menu", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    elif action == "lists":
        lists = get_lists(uid)
        buttons = [[InlineKeyboardButton(text=f"{bl.name} ({len(bl.targets)})", callback_data=f"vl:{bl.name}")] for bl in lists] if lists else []
        buttons.append([InlineKeyboardButton(text="+ Create Group List", callback_data="createlist")])
        await _reply(message, uid, t("group_list_menu", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    elif action == "cleanup":
        if not accounts:
            await _reply(message, uid, t("no_accounts", uid), reply_markup=_main_kb(uid), parse_mode="HTML")
            return
        buttons = [[InlineKeyboardButton(text=a.display_name, callback_data=f"clean:{a.alias}")] for a in accounts]
        await _reply(message, uid, t("pick_account", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons), parse_mode="HTML")
    elif action == "auto_reply":
        if not accounts:
            await _reply(message, uid, t("no_accounts", uid), reply_markup=_main_kb(uid), parse_mode="HTML")
            return
        if len(accounts) == 1:
            acc = accounts[0]
            _state[uid] = {"action": "auto_reply_choose", "alias": acc.alias}
            status = "ON" if acc.auto_reply_enabled else "OFF"
            await _reply(
                message,
                uid,
                f"💬 Auto Reply [{acc.alias}]\nStatus: {status}\n\nPilih aksi:",
                reply_markup=_auto_reply_choice_kb(),
            )
            return
        kb, account_map = _auto_reply_accounts_kb(uid)
        _state[uid] = {"action": "auto_reply_pick", "account_map": account_map}
        await _reply(message, uid, "Pilih akun untuk Auto Reply:", reply_markup=kb)
    elif action == "transfer":
        if not accounts:
            await _reply(message, uid, t("no_accounts", uid), reply_markup=_main_kb(uid), parse_mode="HTML")
            return
        _state[uid] = {"action": "transfer_target"}
        await _reply(
            message,
            uid,
            f"🔄 <b>Transfer Data</b>\n\nKirim user ID admin tujuan untuk memindahkan <b>{len(accounts)} akun</b>.\n\n<blockquote>Pastikan user tujuan sudah pernah membuka bot dengan /start.</blockquote>",
            reply_markup=_back_kb(),
            parse_mode="HTML",
        )
    elif action == "lang":
        buttons, row = [], []
        for code, name in LANGUAGES.items():
            row.append(InlineKeyboardButton(text=name, callback_data=f"lang:{code}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        await _reply(message, uid, t("choose_lang", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(F.text)
async def handle_text(message: Message) -> None:
    uid = message.from_user.id
    if not is_registered_admin(uid):
        return

    text = message.text.strip()
    state = _state.get(uid)
    menu_action = _get_menu_action(text)

    if menu_action and (not state or state.get("action") not in {"login_code", "login_2fa"}):
        _state.pop(uid, None)
        try:
            await message.delete()
        except Exception:
            pass
        await _dispatch_menu(message, uid, menu_action)
        return

    if not state:
        return

    action = state["action"]

    # --- Login flow ---
    if action == "login_phone":
        cfg = load_config()
        if not cfg.has_own_api:
            await message.answer("API credentials not configured.", reply_markup=_back_kb())
            _state.pop(uid, None)
            return
        client, preset = _new_client("random")
        await client.connect()
        try:
            sent = await client.send_code_request(text)
        except FloodWaitError as e:
            await client.disconnect()
            await message.answer(f"Flood wait: {e.seconds}s. Try later.", reply_markup=_back_kb())
            _state.pop(uid, None)
            return
        except Exception as e:
            await client.disconnect()
            await message.answer(f"Error: {type(e).__name__}: {e}", reply_markup=_back_kb())
            _state.pop(uid, None)
            return
        _state[uid] = {
            "action": "login_code",
            "phone": text,
            "phone_code_hash": sent.phone_code_hash,
            "client": client,
            "preset": preset,
        }
        sent_msg = await message.answer(
            f"Code sent to {text}\nDevice: {preset.device_model}\n\n"
            "⚠️ PENTING: Ketik kode PAKAI SPASI\n"
            "Contoh: 3 6 8 1 5\n\n"
            "Jangan ketik tanpa spasi, Telegram akan otomatis membatalkan kode!"
        )
        _state[uid]["code_sent_msg"] = sent_msg.message_id
        # Auto-delete user's phone number message
        try:
            await message.delete()
        except Exception:
            pass

    elif action == "login_code":
        client = state["client"]
        code = text.replace(" ", "")
        # Delete the message containing the code
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await client.sign_in(state["phone"], code, phone_code_hash=state["phone_code_hash"])
        except PhoneCodeInvalidError:
            await message.answer("Invalid code. Try again:")
            return
        except PhoneCodeExpiredError:
            await client.disconnect()
            _state.pop(uid, None)
            await message.answer(
                "Kode expired/invalid.\n\n"
                "Kemungkinan penyebab:\n"
                "• Kode diketik TANPA spasi → Telegram otomatis cancel\n"
                "• Kode sudah lewat 5 menit\n\n"
                "Coba lagi, pastikan PAKAI SPASI: 3 6 8 1 5",
                reply_markup=_back_kb(),
            )
            return
        except SessionPasswordNeededError:
            _state[uid]["action"] = "login_2fa"
            await message.answer("2FA enabled. Enter your cloud password:")
            return
        await _finish_login(message, uid)

    elif action == "login_2fa":
        client = state["client"]
        # Delete the message containing the password
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await client.sign_in(password=text)
        except Exception as e:
            await message.answer(f"Wrong password: {e}\nTry again:")
            return
        state["is_2fa"] = True
        await _finish_login(message, uid)

    # --- Saved text ---
    elif action == "savetext_name":
        if len(text) > _MAX_NAME_LENGTH:
            await message.answer(_name_too_long_message(text))
            return
        _state[uid] = {"action": "savetext_body", "name": text}
        await message.answer("Kirim isi text yang mau disimpan:", reply_markup=_back_kb())

    elif action == "savetext_body":
        save_broadcast_msg(uid, state["name"], _entities_to_html(message.text or "", message.entities or []), False)
        _state.pop(uid, None)
        await message.answer(f"Text '{state['name']}' tersimpan.", reply_markup=_main_kb(uid))

    # --- Create list ---
    elif action == "createlist_name":
        if len(text) > _MAX_NAME_LENGTH:
            await message.answer(_name_too_long_message(text))
            return
        _state[uid] = {"action": "createlist_targets", "name": text, "targets": []}
        await message.answer(
            f"Group list: {text}\n\n"
            "Paste daftar group/link. Bisa campur nama + @username/link invite/addlist.\n"
            "Kirim 'done' atau 'selesai' kalau sudah selesai:"
        )

    elif action == "createlist_targets":
        if text.lower() in {"done", "selesai"}:
            targets = state["targets"]
            if not targets:
                await message.answer("No targets added. Cancelled.", reply_markup=_main_kb(uid))
            else:
                add_list(BroadcastListRow(admin_id=uid, name=state["name"], targets=targets))
                await message.answer(f"List '{state['name']}' created ({len(targets)} targets).", reply_markup=_main_kb(uid))
            _state.pop(uid, None)
        else:
            new_targets = _extract_group_targets(text)
            existing = {target.lower() for target in state["targets"]}
            added = [target for target in new_targets if target.lower() not in existing]
            state["targets"].extend(added)
            if not added:
                await message.answer(
                    "Belum nemu target Telegram di pesan itu.\n"
                    "Kirim @username, t.me link, invite private, addlist, atau chat_id."
                )
                return
            preview = "\n".join(f"- {target}" for target in added[:10])
            if len(added) > 10:
                preview += f"\n... +{len(added) - 10} lagi"
            await message.answer(
                f"Added {len(added)} target:\n{preview}\n\n"
                f"Total: {len(state['targets'])}\n"
                "Kirim target lain atau 'done' / 'selesai':"
            )

    # --- List edit: add targets ---
    elif action == "listadd_targets":
        list_name = state["list"]
        bl = get_list(uid, list_name)
        if not bl:
            await message.answer("List not found.", reply_markup=_back_kb())
            _state.pop(uid, None)
            return
        new_targets = _extract_group_targets(text)
        existing = {target.lower() for target in bl.targets}
        added = [target for target in new_targets if target.lower() not in existing]
        if not added:
            await message.answer(
                "Belum nemu target Telegram baru di pesan itu.",
                reply_markup=_back_kb(),
            )
            _state.pop(uid, None)
            return
        bl.targets.extend(added)
        add_list(bl)
        _state.pop(uid, None)
        await message.answer(
            f"✅ Ditambah {len(added)} target ke '{list_name}' (total: {len(bl.targets)})",
            reply_markup=_main_kb(uid))

    # --- List edit: remove targets ---
    elif action == "listremove_pick":
        list_name = state["list"]
        bl = get_list(uid, list_name)
        if not bl:
            await message.answer("List not found.", reply_markup=_back_kb())
            _state.pop(uid, None)
            return
        try:
            indices = sorted([int(x) - 1 for x in text.split()], reverse=True)
            removed = []
            for i in indices:
                if 0 <= i < len(bl.targets):
                    removed.append(bl.targets.pop(i))
            add_list(bl)
            _state.pop(uid, None)
            await message.answer(
                f"✅ Dihapus {len(removed)} target dari '{list_name}' (sisa: {len(bl.targets)})",
                reply_markup=_main_kb(uid))
        except ValueError:
            await message.answer("Ketik nomor yang mau dihapus (pisah spasi), contoh: 1 3 5")

    # --- Edit name ---
    elif action == "edit_name":
        alias = state["alias"]
        _state.pop(uid, None)
        acc = find_account(uid, alias)
        if not acc:
            await message.answer("Not found.", reply_markup=_back_kb())
            return
        parts = text.split(maxsplit=1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ""
        async with account_lease(uid, acc.phone, purpose="edit_profile", ttl_seconds=60, wait_seconds=10) as lease:
            if not lease.acquired:
                await message.answer(f"[{alias}] Account is busy. Try again later.", reply_markup=_back_kb())
                return
            client = _client_from_session(acc.session_string, acc.device_preset, acc)
            try:
                await client.connect()
                await client.get_me()
                from telethon.tl.functions.account import UpdateProfileRequest
                await client(UpdateProfileRequest(first_name=first, last_name=last))
                await message.answer(f"[{alias}] Name: {first} {last}".strip(), reply_markup=_back_kb())
            except Exception as e:
                await message.answer(f"Error: {e}", reply_markup=_back_kb())
            finally:
                if client.is_connected():
                    await client.disconnect()

    # --- Edit bio ---
    elif action == "edit_bio":
        alias = state["alias"]
        _state.pop(uid, None)
        acc = find_account(uid, alias)
        if not acc:
            await message.answer("Not found.", reply_markup=_back_kb())
            return
        async with account_lease(uid, acc.phone, purpose="edit_profile", ttl_seconds=60, wait_seconds=10) as lease:
            if not lease.acquired:
                await message.answer(f"[{alias}] Account is busy. Try again later.", reply_markup=_back_kb())
                return
            client = _client_from_session(acc.session_string, acc.device_preset, acc)
            try:
                await client.connect()
                await client.get_me()
                from telethon.tl.functions.account import UpdateProfileRequest
                await client(UpdateProfileRequest(about=text))
                await message.answer(f"[{alias}] Bio updated.", reply_markup=_back_kb())
            except Exception as e:
                await message.answer(f"Error: {e}", reply_markup=_back_kb())
            finally:
                if client.is_connected():
                    await client.disconnect()

    # --- Edit username ---
    elif action == "edit_username":
        alias = state["alias"]
        _state.pop(uid, None)
        acc = find_account(uid, alias)
        if not acc:
            await message.answer("Not found.", reply_markup=_back_kb())
            return
        async with account_lease(uid, acc.phone, purpose="edit_profile", ttl_seconds=60, wait_seconds=10) as lease:
            if not lease.acquired:
                await message.answer(f"[{alias}] Account is busy. Try again later.", reply_markup=_back_kb())
                return
            client = _client_from_session(acc.session_string, acc.device_preset, acc)
            try:
                await client.connect()
                await client.get_me()
                from telethon.tl.functions.account import UpdateUsernameRequest
                await client(UpdateUsernameRequest(username=text.lstrip("@")))
                await message.answer(f"[{alias}] Username: @{text.lstrip('@')}", reply_markup=_back_kb())
            except Exception as e:
                await message.answer(f"Error: {e}", reply_markup=_back_kb())
            finally:
                if client.is_connected():
                    await client.disconnect()

    # --- Auto Reply ---
    elif action == "auto_reply_pick":
        alias = state.get("account_map", {}).get(text, text)
        acc = find_account(uid, alias)
        if not acc:
            kb, account_map = _auto_reply_accounts_kb(uid)
            state["account_map"] = account_map
            await message.answer("Akun tidak ditemukan. Pilih akun dari keyboard:", reply_markup=kb)
            return
        _state[uid] = {"action": "auto_reply_choose", "alias": acc.alias}
        status = "ON" if acc.auto_reply_enabled else "OFF"
        await message.answer(
            f"💬 Auto Reply [{acc.alias}]\nStatus: {status}\n\nPilih aksi:",
            reply_markup=_auto_reply_choice_kb(),
        )

    elif action == "auto_reply_choose":
        alias = state["alias"]
        acc = find_account(uid, alias)
        if not acc:
            _state.pop(uid, None)
            await message.answer("Not found.", reply_markup=_main_kb(uid))
            return
        if text == "❌ OFF":
            try:
                update_auto_reply(uid, acc.phone, enabled=False)
            except Exception as e:
                await message.answer(f"Error disabling auto-reply: {e}", reply_markup=_back_kb())
                return
            _state.pop(uid, None)
            await message.answer(f"[{alias}] Auto Reply disabled.", reply_markup=_main_kb(uid))
            return
        if text == "✅ ON / Set Text":
            _state[uid] = {"action": "auto_reply_text", "alias": alias}
            await message.answer(
                f"[{alias}] Kirim text Auto Reply yang mau dipakai.\n"
                "Formatting seperti bold/italic/link akan disimpan.\n\n"
                "Auto Reply hanya untuk chat baru/fresh yang belum pernah ada outgoing chat.",
                reply_markup=_back_kb(),
            )
            return
        await message.answer("Pilih ON / Set Text atau OFF dari keyboard:", reply_markup=_auto_reply_choice_kb())

    elif action == "auto_reply_text":
        alias = state["alias"]
        _state.pop(uid, None)
        acc = find_account(uid, alias)
        if not acc:
            await message.answer("Not found.", reply_markup=_back_kb())
            return
        html_text = _entities_to_html(message.text or "", message.entities or [])
        try:
            update_auto_reply(uid, acc.phone, enabled=True, text=html_text)
        except Exception as e:
            await message.answer(f"Error saving auto-reply: {e}", reply_markup=_back_kb())
            return
        await message.answer(
            f"[{alias}] Auto Reply enabled.\n\n"
            f"Preview:\n{html_text[:500]}",
            reply_markup=_main_kb(uid),
            parse_mode="HTML",
        )

    elif action == "broadcast_msg":
        # Save the full message (text + entities + media)
        _state[uid]["message"] = message
        _state[uid]["action"] = "broadcast_save_ask"
        buttons = [
            [KeyboardButton(text="Save & continue"), KeyboardButton(text="Just continue")],
            [KeyboardButton(text="<< Menu")],
        ]
        await message.answer("Save this message for reuse?", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))

    elif action == "broadcast_save_ask":
        if text == "Save & continue":
            _state[uid]["action"] = "broadcast_save_name"
            await message.answer("Enter a name for this saved message:", reply_markup=_back_kb())
        else:
            _state[uid]["action"] = "broadcast_delay_group"
            await _ask_group_delay(message)

    elif action == "broadcast_save_name":
        if len(text) > _MAX_NAME_LENGTH:
            await message.answer(_name_too_long_message(text))
            return
        src = _state[uid]["message"]
        raw_text = src.text or src.caption or ""
        entities = src.entities or src.caption_entities or []
        html_text = _entities_to_html(raw_text, entities)
        has_media = bool(src.photo or src.video or src.document or src.animation)
        save_broadcast_msg(uid, text, html_text, has_media)
        _state[uid]["action"] = "broadcast_delay_group"
        await message.answer(f"Saved as '{text}'.")
        await _ask_group_delay(message)

    elif action == "broadcast_delay_group":
        parsed_delay = _parse_delay_value(text)
        if parsed_delay is None:
            await message.answer("Invalid. Enter number or range (e.g. 45 or 30-60):")
            return
        _state[uid]["group_delay"] = parsed_delay
        _state[uid]["action"] = "broadcast_delay_round"
        await _ask_round_delay(message)

    elif action == "broadcast_delay_round":
        parsed_delay = _parse_delay_value(text)
        if parsed_delay is None:
            await message.answer("Invalid. Enter number or range (e.g. 600 or 500-700):")
            return
        _state[uid]["round_delay"] = parsed_delay

        st = _state[uid]
        bl = get_list(uid, st["list"])
        accounts = get_accounts(uid)
        if not bl or not accounts:
            await message.answer("List or accounts not found.", reply_markup=_main_kb())
            _state.pop(uid, None)
            return

        watermark = _watermark_for_user(uid)
        has_media = False

        if st.get("saved_texts"):
            text_mode = f"multi random ({len(st['saved_texts'])} text)"
        elif st.get("saved_text"):
            text_mode = "single saved text"
        else:
            text_mode = "new message"

        if "message" in st:
            src_msg = st["message"]
            has_media = bool(src_msg.photo or src_msg.video or src_msg.document or src_msg.animation)

        await message.answer(
            f"Broadcasting (continuous)\n"
            f"List: {st['list']} ({len(bl.targets)} targets)\n"
            f"Accounts: {len(accounts)}\n"
            f"Text mode: {text_mode}\n"
            f"Delay per group: {_format_delay(st['group_delay'])}\n"
            f"Delay after all groups: {_format_delay(st['round_delay'])}\n"
            f"Media: {'yes' if has_media else 'text only'}\n"
            f"Watermark: {watermark or '(none)'}\n\n"
            f"Running... send 'stop' to stop",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="stop")]],
                resize_keyboard=True,
            ),
        )

        _state[uid]["action"] = "broadcasting"
        await _start_broadcast(message, uid)

    elif action == "broadcasting":
        if text.lower() == "stop":
            _state.pop(uid, None)
            await message.answer("Stopping broadcast...")
        else:
            await message.answer("Send 'stop' to stop.")
    elif action == "deletelist_pick":
        if text.startswith("del:"):
            name = text[4:]
            remove_list(uid, name)
            _state.pop(uid, None)
            await message.answer(f"List '{name}' deleted.", reply_markup=_main_kb())
        else:
            await message.answer("Pick a list from the buttons.")

    # --- Edit pick account ---
    elif action == "edit_pick":
        acc = find_account(uid, text)
        if not acc:
            await message.answer("Account not found. Pick from buttons.")
            return
        _state[uid] = {"action": "edit_choose", "alias": text}
        buttons = [
            [KeyboardButton(text="Edit Name"), KeyboardButton(text="Edit Bio")],
            [KeyboardButton(text="Edit Username")],
            [KeyboardButton(text="<< Menu")],
        ]
        await message.answer(f"Editing [{text}]:", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))

    elif action == "edit_choose":
        alias = state["alias"]
        if text == "Edit Name":
            _state[uid] = {"action": "edit_name", "alias": alias}
            await message.answer(f"[{alias}] Enter new name (first last):", reply_markup=_back_kb())
        elif text == "Edit Bio":
            _state[uid] = {"action": "edit_bio", "alias": alias}
            await message.answer(f"[{alias}] Enter new bio:", reply_markup=_back_kb())
        elif text == "Edit Username":
            _state[uid] = {"action": "edit_username", "alias": alias}
            await message.answer(f"[{alias}] Enter new username (without @):", reply_markup=_back_kb())

    # --- Cleanup pick account ---
    elif action == "cleanup_pick":
        acc = find_account(uid, text)
        if not acc:
            await message.answer("Account not found. Pick from buttons.")
            return
        _state[uid] = {"action": "cleanup_choose", "alias": text}
        buttons = [
            [KeyboardButton(text="Logout (revoke)"), KeyboardButton(text="Remove only")],
            [KeyboardButton(text="<< Menu")],
        ]
        await message.answer(f"[{text}] What to do?", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))

    elif action == "cleanup_choose":
        alias = state["alias"]
        _state.pop(uid, None)
        acc = find_account(uid, alias)
        if not acc:
            await message.answer("Not found.", reply_markup=_main_kb())
            return
        if text == "Logout (revoke)":
            async with account_lease(uid, acc.phone, purpose="logout", ttl_seconds=60, wait_seconds=10) as lease:
                if not lease.acquired:
                    await message.answer(f"[{alias}] Account is busy. Try again later.", reply_markup=_main_kb())
                    return
                client = _client_from_session(acc.session_string, acc.device_preset, acc)
                try:
                    await client.connect()
                    await client.log_out()
                except Exception:
                    pass
                finally:
                    if client.is_connected():
                        await client.disconnect()
                remove_account(uid, acc.phone)
                await message.answer(f"[{alias}] Logged out and removed.", reply_markup=_main_kb())
        elif text == "Remove only":
            remove_account(uid, acc.phone)
            await message.answer(f"[{alias}] Removed.", reply_markup=_main_kb())

    # --- Transfer ---
    elif action == "transfer_target":
        _state.pop(uid, None)
        try:
            target_id = int(text)
        except ValueError:
            await message.answer("Invalid ID. Enter a numeric Telegram user ID.", reply_markup=_main_kb())
            return
        if target_id == uid:
            await message.answer("Can't transfer to yourself.", reply_markup=_main_kb())
            return
        count = transfer_all(uid, target_id)
        await message.answer(
            f"Transferred {count} item(s) to user {target_id}.\n"
            f"Your data is now under their control.",
            reply_markup=_main_kb(),
        )


async def _finish_login(message: Message, admin_id: int) -> None:
    state = _state.pop(admin_id)
    client = state["client"]
    me = await client.get_me()

    # Delete the "Code sent" instruction message
    code_msg_id = state.get("code_sent_msg")
    if code_msg_id:
        try:
            await message.bot.delete_message(message.chat.id, code_msg_id)
        except Exception:
            pass

    # Anti-double: block only if managed by ANOTHER admin
    if is_registered_admin(me.id) and me.id != admin_id:
        await client.disconnect()
        await message.answer("Cannot add — this user is already an admin.", reply_markup=_back_kb())
        return

    session_str = client.session.save()
    await client.disconnect()

    api_credential_index, proxy_index = _assignment_for_new_account(admin_id, state["phone"])
    acc = AccountRow(
        admin_id=admin_id,
        phone=state["phone"],
        alias=state["phone"].lstrip("+"),
        session_string=session_str,
        first_name=me.first_name or "",
        last_name=me.last_name or "",
        username=me.username,
        user_id=me.id,
        is_2fa=state.get("is_2fa", False),
        device_preset=state["preset"].key,
        api_credential_index=api_credential_index,
        proxy_index=proxy_index,
        connected_ip=(load_config().proxy_for_index(proxy_index).host if load_config().proxy_for_index(proxy_index) else ""),
        last_connected_at=_runtime_now(),
    )
    add_account(acc)
    await message.answer(
        f"Logged in: {me.first_name} (@{me.username or '-'})\n"
        f"Alias: {acc.alias}\nDevice: {state['preset'].device_model}",
        reply_markup=_main_kb(),
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
async def run_bot() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN not set")
    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(_auto_health_check_loop(bot))
    asyncio.create_task(_auto_reply_reconcile_loop())
    asyncio.create_task(_recover_broadcast_jobs(bot))
    log.info("Bot starting...")
    await dp.start_polling(bot)
