"""Telegram Bot interface for remote multi-admin management.

Anyone can /start and become an admin — UNLESS their Telegram user ID is
already registered as a managed account by another admin (anti-double).

Each admin manages their own isolated set of accounts.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
from pathlib import Path
from typing import Dict, List, Optional, Set

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from .config import load_config
from .device_presets import get_preset
from .logger import get_logger
from .storage import Account, AccountStore, BroadcastList, ListStore

log = get_logger("bot")
router = Router()

_login_state: Dict[int, dict] = {}
_DATA_DIR = Path(os.getenv("DATA_DIR", "."))


# ---------------------------------------------------------------------------
# Admin registry — tracks who is an admin, persisted to admins.json
# ---------------------------------------------------------------------------
def _admins_file() -> Path:
    return _DATA_DIR / "admins.json"


def _load_admins() -> Set[int]:
    f = _admins_file()
    if not f.exists():
        return set()
    data = json.loads(f.read_text(encoding="utf-8"))
    return set(data)


def _save_admins(admins: Set[int]) -> None:
    f = _admins_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(list(admins)), encoding="utf-8")


def _register_admin(user_id: int) -> None:
    admins = _load_admins()
    admins.add(user_id)
    _save_admins(admins)


def _is_registered_admin(user_id: int) -> bool:
    return user_id in _load_admins()


# ---------------------------------------------------------------------------
# Anti-double: check if user_id is a managed account under any admin
# ---------------------------------------------------------------------------
def _get_all_managed_user_ids() -> Dict[int, int]:
    """Returns {managed_user_id: owner_admin_id}."""
    result = {}
    admins_root = _DATA_DIR / "admins"
    if not admins_root.exists():
        return result
    for admin_dir in admins_root.iterdir():
        if not admin_dir.is_dir():
            continue
        acc_file = admin_dir / "accounts.json"
        if not acc_file.exists():
            continue
        try:
            data = json.loads(acc_file.read_text(encoding="utf-8"))
            for item in data.get("accounts", []):
                uid = item.get("user_id")
                if uid:
                    result[uid] = int(admin_dir.name)
        except Exception:
            continue
    return result


def _is_managed_account(user_id: int) -> bool:
    """Check if this user_id is already managed by some admin."""
    managed = _get_all_managed_user_ids()
    return user_id in managed


# ---------------------------------------------------------------------------
# Per-admin helpers
# ---------------------------------------------------------------------------
def _admin_dir(admin_id: int) -> Path:
    base = _DATA_DIR / "admins" / str(admin_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _get_store(admin_id: int) -> AccountStore:
    d = _admin_dir(admin_id)
    sessions_dir = d / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    store = AccountStore(d / "accounts.json", sessions_dir)
    store.load()
    return store


def _get_list_store(admin_id: int) -> ListStore:
    ls = ListStore(_admin_dir(admin_id) / "broadcast_lists.json")
    ls.load()
    return ls


def _build_client(admin_id: int, account: Account) -> TelegramClient:
    cfg = load_config()
    preset = get_preset(account.device_preset)
    session_path = str(_admin_dir(admin_id) / "sessions" / account.session_name)
    return TelegramClient(
        session_path,
        cfg.api_id,
        cfg.api_hash,
        device_model=preset.device_model,
        system_version=preset.system_version,
        app_version=preset.app_version,
        lang_code=preset.lang_code,
        system_lang_code=preset.system_lang_code,
    )


# ---------------------------------------------------------------------------
# Access check
# ---------------------------------------------------------------------------
async def _check_access(message: Message) -> bool:
    """Returns True if user can proceed. Sends denial message if not."""
    uid = message.from_user.id
    if _is_managed_account(uid):
        await message.answer(
            "Access denied.\n"
            "Your Telegram account is already managed by another admin. "
            "A managed account cannot also be an admin."
        )
        return False
    if not _is_registered_admin(uid):
        await message.answer("Use /start to register first.")
        return False
    return True


# ---------------------------------------------------------------------------
# Main menu keyboard
# ---------------------------------------------------------------------------
def _main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Add account", callback_data="menu:login"),
         InlineKeyboardButton(text="My accounts", callback_data="menu:accounts")],
        [InlineKeyboardButton(text="Health check", callback_data="menu:health"),
         InlineKeyboardButton(text="Broadcast", callback_data="menu:broadcast")],
        [InlineKeyboardButton(text="Lists", callback_data="menu:lists"),
         InlineKeyboardButton(text="Help", callback_data="menu:help")],
    ])



# ---------------------------------------------------------------------------
# /start — register + show menu
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    uid = message.from_user.id
    if _is_managed_account(uid):
        await message.answer(
            "Access denied.\n"
            "Your Telegram account is already managed by another admin. "
            "A managed account cannot also be an admin."
        )
        return
    _register_admin(uid)
    store = _get_store(uid)
    n = len(store.all())
    await message.answer(
        f"Welcome to Telegram Manager!\n"
        f"You have {n} account(s).\n\n"
        "Choose an action:",
        reply_markup=_main_menu_kb(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    if not await _check_access(message):
        return
    await message.answer("Choose an action:", reply_markup=_main_menu_kb())


# ---------------------------------------------------------------------------
# Callback handlers for inline menu
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "menu:login")
async def cb_login(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Send the phone number to login:\n/login <phone>\n\nExample: /login +628123456789"
    )


@router.callback_query(F.data == "menu:accounts")
async def cb_accounts(callback: CallbackQuery) -> None:
    await callback.answer()
    store = _get_store(callback.from_user.id)
    accounts = store.all()
    if not accounts:
        await callback.message.answer("No accounts yet. Use /login <phone> to add one.")
        return
    lines = []
    for i, a in enumerate(accounts, 1):
        status = "2FA" if a.is_2fa else "ok"
        lines.append(f"{i}. [{a.alias}] {a.phone} — {a.display_name} ({status})")
    await callback.message.answer("\n".join(lines))


@router.callback_query(F.data == "menu:health")
async def cb_health(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Running health check...")
    store = _get_store(callback.from_user.id)
    accounts = store.all()
    if not accounts:
        await callback.message.answer("No accounts.")
        return
    lines = []
    for acc in accounts:
        client = _build_client(callback.from_user.id, acc)
        try:
            await client.connect()
            me = await client.get_me()
            lines.append(f"[{acc.alias}] OK — {me.first_name}")
        except Exception as e:
            lines.append(f"[{acc.alias}] FAIL — {type(e).__name__}")
        finally:
            if client.is_connected():
                await client.disconnect()
    await callback.message.answer("\n".join(lines))


@router.callback_query(F.data == "menu:broadcast")
async def cb_broadcast(callback: CallbackQuery) -> None:
    await callback.answer()
    ls = _get_list_store(callback.from_user.id)
    lists = ls.all()
    if not lists:
        await callback.message.answer(
            "No broadcast lists yet.\n"
            "Create one: /createlist <name> <target1> <target2> ...\n"
            "Then: /broadcast <list_name> <message>"
        )
        return
    lines = [f"Your lists:"]
    for bl in lists:
        lines.append(f"  {bl.name} ({len(bl.targets)} targets)")
    lines.append(f"\nUse: /broadcast <list_name> <message>")
    await callback.message.answer("\n".join(lines))


@router.callback_query(F.data == "menu:lists")
async def cb_lists(callback: CallbackQuery) -> None:
    await callback.answer()
    ls = _get_list_store(callback.from_user.id)
    lists = ls.all()
    if not lists:
        await callback.message.answer(
            "No lists.\n/createlist <name> <target1> <target2> ..."
        )
        return
    lines = []
    for bl in lists:
        lines.append(f"{bl.name} ({len(bl.targets)}): {', '.join(bl.targets[:5])}")
    await callback.message.answer("\n".join(lines))


@router.callback_query(F.data == "menu:help")
async def cb_help(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(
        "Commands:\n"
        "/login <phone> - Add account\n"
        "/code <code> - Enter login code\n"
        "/password <pass> - Enter 2FA password\n"
        "/accounts - List accounts\n"
        "/health - Check sessions\n"
        "/groups <alias> - List groups/channels\n"
        "/join <alias> <target> - Join group/channel\n"
        "/send <alias> <target> <text> - Send message\n"
        "/broadcast <list> <text> - Broadcast to list\n"
        "/createlist <name> <targets...> - Create list\n"
        "/deletelist <name> - Delete list\n"
        "/editname <alias> <first> [last]\n"
        "/editbio <alias> <bio>\n"
        "/editusername <alias> <username>\n"
        "/logout <alias> - Revoke session\n"
        "/remove <alias> - Remove account\n"
        "/menu - Show main menu"
    )



# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------
@router.message(Command("login"))
async def cmd_login(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /login <phone>\nExample: /login +628123456789")
        return
    phone = parts[1].strip()
    admin_id = message.from_user.id
    cfg = load_config()

    if not cfg.has_own_api:
        await message.answer("TELEGRAM_API_ID/TELEGRAM_API_HASH not set.")
        return

    preset = get_preset("random")
    session_name = phone.lstrip("+")
    session_path = str(_admin_dir(admin_id) / "sessions" / session_name)

    client = TelegramClient(
        session_path, cfg.api_id, cfg.api_hash,
        device_model=preset.device_model,
        system_version=preset.system_version,
        app_version=preset.app_version,
        lang_code=preset.lang_code,
        system_lang_code=preset.system_lang_code,
    )

    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except FloodWaitError as e:
        await client.disconnect()
        await message.answer(f"Flood wait: retry in {e.seconds}s")
        return
    except Exception as e:
        await client.disconnect()
        await message.answer(f"Error: {type(e).__name__}: {e}")
        return

    _login_state[admin_id] = {
        "phone": phone,
        "phone_code_hash": sent.phone_code_hash,
        "client": client,
        "preset": preset,
        "session_name": session_name,
        "step": "code",
    }
    await message.answer(
        f"Code sent to {phone}.\nDevice: {preset.device_model}\n\n"
        "Reply: /code <5-digit-code>"
    )


@router.message(Command("code"))
async def cmd_code(message: Message) -> None:
    admin_id = message.from_user.id
    if admin_id not in _login_state:
        await message.answer("No pending login. Use /login <phone> first.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /code <5-digit-code>")
        return

    state = _login_state[admin_id]
    client = state["client"]
    code = parts[1].strip()

    try:
        await client.sign_in(
            state["phone"], code, phone_code_hash=state["phone_code_hash"]
        )
    except PhoneCodeInvalidError:
        await message.answer("Invalid code. Try again: /code <code>")
        return
    except PhoneCodeExpiredError:
        del _login_state[admin_id]
        await client.disconnect()
        await message.answer("Code expired. Use /login again.")
        return
    except SessionPasswordNeededError:
        state["step"] = "2fa"
        await message.answer("2FA enabled. Send: /password <your_password>")
        return

    await _finish_login(message, admin_id)


@router.message(Command("password"))
async def cmd_password(message: Message) -> None:
    admin_id = message.from_user.id
    if admin_id not in _login_state or _login_state[admin_id].get("step") != "2fa":
        await message.answer("No pending 2FA.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /password <your_2fa_password>")
        return

    state = _login_state[admin_id]
    client = state["client"]
    try:
        await client.sign_in(password=parts[1])
    except Exception as e:
        await message.answer(f"2FA failed: {e}\nTry again: /password <pass>")
        return

    await _finish_login(message, admin_id)


async def _finish_login(message: Message, admin_id: int) -> None:
    state = _login_state.pop(admin_id)
    client = state["client"]
    me = await client.get_me()
    await client.disconnect()

    # Anti-double: check if this account's user_id is already an admin
    if _is_registered_admin(me.id):
        await message.answer(
            f"Cannot add this account — Telegram user {me.id} "
            f"(@{me.username or me.first_name}) is already registered as an admin. "
            "An admin account cannot be managed by another admin."
        )
        return

    account = Account(
        phone=state["phone"],
        alias=state["session_name"],
        session_name=state["session_name"],
        first_name=me.first_name or "",
        last_name=me.last_name or "",
        username=me.username,
        user_id=me.id,
        is_2fa=(state.get("step") == "2fa"),
        device_preset=state["preset"].key,
    )

    store = _get_store(admin_id)
    try:
        store.add(account)
    except Exception:
        store.update(account)

    await message.answer(
        f"Logged in: {me.first_name} (@{me.username or '-'})\n"
        f"Alias: {account.alias}\n"
        f"Device: {state['preset'].device_model}",
        reply_markup=_main_menu_kb(),
    )



# ---------------------------------------------------------------------------
# Account management commands
# ---------------------------------------------------------------------------
@router.message(Command("accounts"))
async def cmd_accounts(message: Message) -> None:
    if not await _check_access(message):
        return
    store = _get_store(message.from_user.id)
    accounts = store.all()
    if not accounts:
        await message.answer("No accounts. Use /login <phone>")
        return
    lines = []
    for i, a in enumerate(accounts, 1):
        lines.append(f"{i}. [{a.alias}] {a.phone} — {a.display_name}")
    await message.answer("\n".join(lines))


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    if not await _check_access(message):
        return
    store = _get_store(message.from_user.id)
    accounts = store.all()
    if not accounts:
        await message.answer("No accounts.")
        return
    lines = []
    for acc in accounts:
        client = _build_client(message.from_user.id, acc)
        try:
            await client.connect()
            me = await client.get_me()
            lines.append(f"[{acc.alias}] OK — {me.first_name}")
        except Exception as e:
            lines.append(f"[{acc.alias}] FAIL — {type(e).__name__}")
        finally:
            if client.is_connected():
                await client.disconnect()
    await message.answer("\n".join(lines))


@router.message(Command("groups"))
async def cmd_groups(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /groups <alias>")
        return
    store = _get_store(message.from_user.id)
    acc = store.find(parts[1].strip())
    if not acc:
        await message.answer("Account not found.")
        return
    client = _build_client(message.from_user.id, acc)
    try:
        await client.connect()
        await client.get_me()
        dialogs = await client.get_dialogs()
        lines = []
        for d in dialogs:
            entity = d.entity
            if hasattr(entity, "megagroup"):
                t = "Group" if entity.megagroup else "Channel"
                u = f"@{entity.username}" if getattr(entity, "username", None) else "-"
                lines.append(f"{getattr(entity, 'title', '?')} | {u} | {t}")
        await message.answer("\n".join(lines[:50]) if lines else "No groups/channels.")
    finally:
        if client.is_connected():
            await client.disconnect()


@router.message(Command("join"))
async def cmd_join(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /join <alias> <@username or invite link>")
        return
    store = _get_store(message.from_user.id)
    acc = store.find(parts[1].strip())
    if not acc:
        await message.answer("Account not found.")
        return
    target = parts[2].strip()
    client = _build_client(message.from_user.id, acc)
    try:
        await client.connect()
        await client.get_me()
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest
        if "t.me/+" in target or "joinchat/" in target:
            h = target.split("+")[-1].split("joinchat/")[-1]
            await client(ImportChatInviteRequest(h))
        else:
            await client(JoinChannelRequest(target.lstrip("@").replace("https://t.me/", "")))
        await message.answer(f"[{acc.alias}] Joined {target}")
    except Exception as e:
        await message.answer(f"Error: {type(e).__name__}: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()


@router.message(Command("send"))
async def cmd_send(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        await message.answer("Usage: /send <alias> <target> <text>")
        return
    store = _get_store(message.from_user.id)
    acc = store.find(parts[1].strip())
    if not acc:
        await message.answer("Account not found.")
        return
    target, text = parts[2].strip(), parts[3]
    client = _build_client(message.from_user.id, acc)
    try:
        await client.connect()
        await client.get_me()
        msg = await client.send_message(target, text)
        await message.answer(f"[{acc.alias}] Sent to {target} (id={msg.id})")
    except Exception as e:
        await message.answer(f"Error: {type(e).__name__}: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()



# ---------------------------------------------------------------------------
# Broadcast & lists
# ---------------------------------------------------------------------------
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /broadcast <list_name> <text>")
        return
    list_name, text = parts[1].strip(), parts[2]
    ls = _get_list_store(message.from_user.id)
    bl = ls.get(list_name)
    if not bl:
        await message.answer(f"List '{list_name}' not found. /lists to see available.")
        return
    store = _get_store(message.from_user.id)
    accounts = store.all()
    if not accounts:
        await message.answer("No accounts.")
        return

    await message.answer(f"Broadcasting to {len(bl.targets)} targets from {len(accounts)} accounts...")

    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest

    results = []
    for acc in accounts:
        client = _build_client(message.from_user.id, acc)
        try:
            await client.connect()
            await client.get_me()
            acc_res = []
            for target in bl.targets:
                try:
                    if "t.me/+" in target or "joinchat/" in target:
                        h = target.split("+")[-1].split("joinchat/")[-1]
                        await client(ImportChatInviteRequest(h))
                    else:
                        await client(JoinChannelRequest(target.lstrip("@").replace("https://t.me/", "")))
                except Exception:
                    pass
                try:
                    entity = target.lstrip("@").replace("https://t.me/", "").split("+")[0]
                    await client.send_message(entity, text)
                    acc_res.append("ok")
                except Exception as e:
                    acc_res.append(type(e).__name__)
                await asyncio.sleep(random.uniform(3, 8))
            results.append(f"[{acc.alias}] {'/'.join(acc_res)}")
        except Exception as e:
            results.append(f"[{acc.alias}] FAIL: {type(e).__name__}")
        finally:
            if client.is_connected():
                await client.disconnect()
    await message.answer("\n".join(results))


@router.message(Command("lists"))
async def cmd_lists(message: Message) -> None:
    if not await _check_access(message):
        return
    ls = _get_list_store(message.from_user.id)
    lists = ls.all()
    if not lists:
        await message.answer("No lists. /createlist <name> <target1> <target2> ...")
        return
    lines = [f"{bl.name} ({len(bl.targets)}): {', '.join(bl.targets[:5])}" for bl in lists]
    await message.answer("\n".join(lines))


@router.message(Command("createlist"))
async def cmd_createlist(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("Usage: /createlist <name> <target1> <target2> ...")
        return
    name, targets = parts[1], parts[2:]
    ls = _get_list_store(message.from_user.id)
    ls.add(BroadcastList(name=name, targets=targets))
    await message.answer(f"List '{name}' created ({len(targets)} targets).")


@router.message(Command("deletelist"))
async def cmd_deletelist(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /deletelist <name>")
        return
    ls = _get_list_store(message.from_user.id)
    ls.remove(parts[1].strip())
    await message.answer(f"Deleted.")



# ---------------------------------------------------------------------------
# Edit profile & cleanup
# ---------------------------------------------------------------------------
@router.message(Command("editname"))
async def cmd_editname(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("Usage: /editname <alias> <first> [last]")
        return
    store = _get_store(message.from_user.id)
    acc = store.find(parts[1].strip())
    if not acc:
        await message.answer("Account not found.")
        return
    first = parts[2]
    last = parts[3] if len(parts) > 3 else ""
    client = _build_client(message.from_user.id, acc)
    try:
        await client.connect()
        await client.get_me()
        from telethon.tl.functions.account import UpdateProfileRequest
        await client(UpdateProfileRequest(first_name=first, last_name=last))
        await message.answer(f"[{acc.alias}] Name: {first} {last}".strip())
    except Exception as e:
        await message.answer(f"Error: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()


@router.message(Command("editbio"))
async def cmd_editbio(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /editbio <alias> <bio>")
        return
    store = _get_store(message.from_user.id)
    acc = store.find(parts[1].strip())
    if not acc:
        await message.answer("Account not found.")
        return
    client = _build_client(message.from_user.id, acc)
    try:
        await client.connect()
        await client.get_me()
        from telethon.tl.functions.account import UpdateProfileRequest
        await client(UpdateProfileRequest(about=parts[2]))
        await message.answer(f"[{acc.alias}] Bio updated.")
    except Exception as e:
        await message.answer(f"Error: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()


@router.message(Command("editusername"))
async def cmd_editusername(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Usage: /editusername <alias> <username>")
        return
    store = _get_store(message.from_user.id)
    acc = store.find(parts[1].strip())
    if not acc:
        await message.answer("Account not found.")
        return
    username = parts[2].strip().lstrip("@")
    client = _build_client(message.from_user.id, acc)
    try:
        await client.connect()
        await client.get_me()
        from telethon.tl.functions.account import UpdateUsernameRequest
        await client(UpdateUsernameRequest(username=username))
        await message.answer(f"[{acc.alias}] Username: @{username}")
    except Exception as e:
        await message.answer(f"Error: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()


@router.message(Command("logout"))
async def cmd_logout(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /logout <alias>")
        return
    store = _get_store(message.from_user.id)
    acc = store.find(parts[1].strip())
    if not acc:
        await message.answer("Account not found.")
        return
    client = _build_client(message.from_user.id, acc)
    try:
        await client.connect()
        await client.log_out()
    except Exception:
        pass
    finally:
        if client.is_connected():
            await client.disconnect()
    store.remove(acc.phone)
    await message.answer(f"[{acc.alias}] Logged out and removed.")


@router.message(Command("remove"))
async def cmd_remove(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /remove <alias>")
        return
    store = _get_store(message.from_user.id)
    try:
        acc = store.remove(parts[1].strip())
        await message.answer(f"[{acc.alias}] Removed.")
    except Exception as e:
        await message.answer(f"Error: {e}")


# ---------------------------------------------------------------------------
# Bot runner
# ---------------------------------------------------------------------------
async def run_bot() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN not set in environment")

    bot = Bot(token=token)
    dp = Dispatcher()
    dp.include_router(router)

    log.info("Bot starting...")
    await dp.start_polling(bot)
