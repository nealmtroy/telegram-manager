"""Telegram Bot interface with Supabase storage.

Fully stateless — no filesystem needed. Sessions stored as StringSession in DB.
Anyone can /start to become admin, unless their user_id is already managed.
"""
from __future__ import annotations

import asyncio
import os
import random
from typing import Dict

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
from telethon.sessions import StringSession

from .config import load_config
from .db import (
    AccountRow,
    BroadcastListRow,
    add_account,
    add_list,
    find_account,
    get_accounts,
    get_list,
    get_lists,
    is_managed_account,
    is_registered_admin,
    register_admin,
    remove_account,
    remove_list,
)
from .device_presets import get_preset
from .logger import get_logger

log = get_logger("bot")
router = Router()

_login_state: Dict[int, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build_client_from_session(session_string: str, preset_key: str) -> TelegramClient:
    cfg = load_config()
    preset = get_preset(preset_key)
    return TelegramClient(
        StringSession(session_string),
        cfg.api_id,
        cfg.api_hash,
        device_model=preset.device_model,
        system_version=preset.system_version,
        app_version=preset.app_version,
        lang_code=preset.lang_code,
        system_lang_code=preset.system_lang_code,
    )


def _build_new_client(preset_key: str = "random") -> tuple:
    """Returns (client, preset) with empty StringSession."""
    cfg = load_config()
    preset = get_preset(preset_key)
    client = TelegramClient(
        StringSession(),
        cfg.api_id,
        cfg.api_hash,
        device_model=preset.device_model,
        system_version=preset.system_version,
        app_version=preset.app_version,
        lang_code=preset.lang_code,
        system_lang_code=preset.system_lang_code,
    )
    return client, preset


async def _check_access(message: Message) -> bool:
    uid = message.from_user.id
    if is_managed_account(uid):
        await message.answer(
            "Access denied.\n"
            "Your account is managed by another admin."
        )
        return False
    if not is_registered_admin(uid):
        await message.answer("Use /start to register first.")
        return False
    return True


def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Add account", callback_data="m:login"),
         InlineKeyboardButton(text="My accounts", callback_data="m:accounts")],
        [InlineKeyboardButton(text="Health check", callback_data="m:health"),
         InlineKeyboardButton(text="Broadcast", callback_data="m:broadcast")],
        [InlineKeyboardButton(text="Lists", callback_data="m:lists"),
         InlineKeyboardButton(text="Help", callback_data="m:help")],
    ])


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
    n = len(get_accounts(uid))
    await message.answer(
        f"Telegram Manager\nAccounts: {n}\n\nChoose:",
        reply_markup=_main_kb(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    if not await _check_access(message):
        return
    await message.answer("Menu:", reply_markup=_main_kb())



# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "m:login")
async def cb_login(cq: CallbackQuery) -> None:
    await cq.answer()
    await cq.message.answer("Send: /login <phone>\nExample: /login +628123456789")


@router.callback_query(F.data == "m:accounts")
async def cb_accounts(cq: CallbackQuery) -> None:
    await cq.answer()
    accounts = get_accounts(cq.from_user.id)
    if not accounts:
        await cq.message.answer("No accounts. /login <phone> to add.")
        return
    lines = [f"{i}. [{a.alias}] {a.phone} — {a.display_name}" for i, a in enumerate(accounts, 1)]
    await cq.message.answer("\n".join(lines))


@router.callback_query(F.data == "m:health")
async def cb_health(cq: CallbackQuery) -> None:
    await cq.answer()
    accounts = get_accounts(cq.from_user.id)
    if not accounts:
        await cq.message.answer("No accounts.")
        return
    await cq.message.answer("Checking...")
    lines = []
    for acc in accounts:
        client = _build_client_from_session(acc.session_string, acc.device_preset)
        try:
            await client.connect()
            me = await client.get_me()
            lines.append(f"[{acc.alias}] OK — {me.first_name}")
        except Exception as e:
            lines.append(f"[{acc.alias}] FAIL — {type(e).__name__}")
        finally:
            if client.is_connected():
                await client.disconnect()
    await cq.message.answer("\n".join(lines))


@router.callback_query(F.data == "m:broadcast")
async def cb_broadcast(cq: CallbackQuery) -> None:
    await cq.answer()
    lists = get_lists(cq.from_user.id)
    if not lists:
        await cq.message.answer("No lists.\n/createlist <name> <target1> <target2> ...\nThen: /broadcast <list> <msg>")
        return
    lines = [f"{bl.name} ({len(bl.targets)} targets)" for bl in lists]
    lines.append("\n/broadcast <list_name> <message>")
    await cq.message.answer("\n".join(lines))


@router.callback_query(F.data == "m:lists")
async def cb_lists(cq: CallbackQuery) -> None:
    await cq.answer()
    lists = get_lists(cq.from_user.id)
    if not lists:
        await cq.message.answer("No lists. /createlist <name> <t1> <t2> ...")
        return
    lines = [f"{bl.name} ({len(bl.targets)}): {', '.join(bl.targets[:5])}" for bl in lists]
    await cq.message.answer("\n".join(lines))


@router.callback_query(F.data == "m:help")
async def cb_help(cq: CallbackQuery) -> None:
    await cq.answer()
    await cq.message.answer(
        "/login <phone> - Add account\n"
        "/code <code> - Login code\n"
        "/password <pass> - 2FA\n"
        "/accounts - List accounts\n"
        "/health - Check sessions\n"
        "/groups <alias> - Groups/channels\n"
        "/join <alias> <target> - Join\n"
        "/send <alias> <target> <text> - Send\n"
        "/broadcast <list> <text> - Broadcast\n"
        "/createlist <name> <targets...>\n"
        "/deletelist <name>\n"
        "/editname <alias> <first> [last]\n"
        "/editbio <alias> <bio>\n"
        "/editusername <alias> <user>\n"
        "/logout <alias>\n"
        "/remove <alias>\n"
        "/menu - Main menu"
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
        await message.answer("Usage: /login <phone>")
        return
    phone = parts[1].strip()
    admin_id = message.from_user.id
    cfg = load_config()
    if not cfg.has_own_api:
        await message.answer("TELEGRAM_API_ID/TELEGRAM_API_HASH not set.")
        return

    client, preset = _build_new_client("random")
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
    except FloodWaitError as e:
        await client.disconnect()
        await message.answer(f"Flood wait: {e.seconds}s")
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
        "step": "code",
    }
    await message.answer(f"Code sent to {phone}.\nDevice: {preset.device_model}\n\n/code <5-digit>")


@router.message(Command("code"))
async def cmd_code(message: Message) -> None:
    admin_id = message.from_user.id
    if admin_id not in _login_state:
        await message.answer("No pending login. /login <phone>")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("/code <code>")
        return
    state = _login_state[admin_id]
    client = state["client"]
    try:
        await client.sign_in(state["phone"], parts[1].strip(), phone_code_hash=state["phone_code_hash"])
    except PhoneCodeInvalidError:
        await message.answer("Invalid code. /code <code>")
        return
    except PhoneCodeExpiredError:
        del _login_state[admin_id]
        await client.disconnect()
        await message.answer("Code expired. /login again.")
        return
    except SessionPasswordNeededError:
        state["step"] = "2fa"
        await message.answer("2FA required. /password <pass>")
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
        await message.answer("/password <pass>")
        return
    state = _login_state[admin_id]
    try:
        await state["client"].sign_in(password=parts[1])
    except Exception as e:
        await message.answer(f"Failed: {e}")
        return
    await _finish_login(message, admin_id)


async def _finish_login(message: Message, admin_id: int) -> None:
    state = _login_state.pop(admin_id)
    client = state["client"]
    me = await client.get_me()

    # Anti-double
    if is_registered_admin(me.id):
        await client.disconnect()
        await message.answer(f"Cannot add — user {me.id} is already an admin.")
        return

    # Save StringSession
    session_str = client.session.save()
    await client.disconnect()

    acc = AccountRow(
        admin_id=admin_id,
        phone=state["phone"],
        alias=state["phone"].lstrip("+"),
        session_string=session_str,
        first_name=me.first_name or "",
        last_name=me.last_name or "",
        username=me.username,
        user_id=me.id,
        is_2fa=(state.get("step") == "2fa"),
        device_preset=state["preset"].key,
    )
    add_account(acc)
    await message.answer(
        f"Logged in: {me.first_name} (@{me.username or '-'})\n"
        f"Alias: {acc.alias}\nDevice: {state['preset'].device_model}",
        reply_markup=_main_kb(),
    )



# ---------------------------------------------------------------------------
# Account commands
# ---------------------------------------------------------------------------
@router.message(Command("accounts"))
async def cmd_accounts(message: Message) -> None:
    if not await _check_access(message):
        return
    accounts = get_accounts(message.from_user.id)
    if not accounts:
        await message.answer("No accounts. /login <phone>")
        return
    lines = [f"{i}. [{a.alias}] {a.phone} — {a.display_name}" for i, a in enumerate(accounts, 1)]
    await message.answer("\n".join(lines))


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    if not await _check_access(message):
        return
    accounts = get_accounts(message.from_user.id)
    if not accounts:
        await message.answer("No accounts.")
        return
    lines = []
    for acc in accounts:
        client = _build_client_from_session(acc.session_string, acc.device_preset)
        try:
            await client.connect()
            await client.get_me()
            lines.append(f"[{acc.alias}] OK")
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
        await message.answer("/groups <alias>")
        return
    acc = find_account(message.from_user.id, parts[1].strip())
    if not acc:
        await message.answer("Not found.")
        return
    client = _build_client_from_session(acc.session_string, acc.device_preset)
    try:
        await client.connect()
        await client.get_me()
        dialogs = await client.get_dialogs()
        lines = []
        for d in dialogs:
            e = d.entity
            if hasattr(e, "megagroup"):
                t = "Group" if e.megagroup else "Channel"
                u = f"@{e.username}" if getattr(e, "username", None) else "-"
                lines.append(f"{getattr(e, 'title', '?')} | {u} | {t}")
        await message.answer("\n".join(lines[:50]) if lines else "None found.")
    finally:
        if client.is_connected():
            await client.disconnect()


@router.message(Command("join"))
async def cmd_join(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("/join <alias> <target>")
        return
    acc = find_account(message.from_user.id, parts[1].strip())
    if not acc:
        await message.answer("Not found.")
        return
    target = parts[2].strip()
    client = _build_client_from_session(acc.session_string, acc.device_preset)
    try:
        await client.connect()
        await client.get_me()
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest
        if "t.me/+" in target or "joinchat/" in target:
            await client(ImportChatInviteRequest(target.split("+")[-1].split("joinchat/")[-1]))
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
        await message.answer("/send <alias> <target> <text>")
        return
    acc = find_account(message.from_user.id, parts[1].strip())
    if not acc:
        await message.answer("Not found.")
        return
    client = _build_client_from_session(acc.session_string, acc.device_preset)
    try:
        await client.connect()
        await client.get_me()
        msg = await client.send_message(parts[2].strip(), parts[3])
        await message.answer(f"[{acc.alias}] Sent (id={msg.id})")
    except Exception as e:
        await message.answer(f"Error: {type(e).__name__}: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()



# ---------------------------------------------------------------------------
# Broadcast, lists, edit, cleanup
# ---------------------------------------------------------------------------
@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("/broadcast <list_name> <text>")
        return
    bl = get_list(message.from_user.id, parts[1].strip())
    if not bl:
        await message.answer("List not found. /lists")
        return
    accounts = get_accounts(message.from_user.id)
    if not accounts:
        await message.answer("No accounts.")
        return
    text = parts[2]
    await message.answer(f"Broadcasting to {len(bl.targets)} targets from {len(accounts)} accounts...")

    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest

    results = []
    for acc in accounts:
        client = _build_client_from_session(acc.session_string, acc.device_preset)
        try:
            await client.connect()
            await client.get_me()
            res = []
            for target in bl.targets:
                try:
                    if "t.me/+" in target or "joinchat/" in target:
                        await client(ImportChatInviteRequest(target.split("+")[-1].split("joinchat/")[-1]))
                    else:
                        await client(JoinChannelRequest(target.lstrip("@").replace("https://t.me/", "")))
                except Exception:
                    pass
                try:
                    e = target.lstrip("@").replace("https://t.me/", "").split("+")[0]
                    await client.send_message(e, text)
                    res.append("ok")
                except Exception as ex:
                    res.append(type(ex).__name__)
                await asyncio.sleep(random.uniform(3, 8))
            results.append(f"[{acc.alias}] {'/'.join(res)}")
        except Exception as ex:
            results.append(f"[{acc.alias}] FAIL: {type(ex).__name__}")
        finally:
            if client.is_connected():
                await client.disconnect()
    await message.answer("\n".join(results))


@router.message(Command("lists"))
async def cmd_lists(message: Message) -> None:
    if not await _check_access(message):
        return
    lists = get_lists(message.from_user.id)
    if not lists:
        await message.answer("No lists. /createlist <name> <t1> <t2>")
        return
    lines = [f"{bl.name} ({len(bl.targets)}): {', '.join(bl.targets[:5])}" for bl in lists]
    await message.answer("\n".join(lines))


@router.message(Command("createlist"))
async def cmd_createlist(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split()
    if len(parts) < 3:
        await message.answer("/createlist <name> <target1> <target2> ...")
        return
    add_list(BroadcastListRow(admin_id=message.from_user.id, name=parts[1], targets=parts[2:]))
    await message.answer(f"List '{parts[1]}' created ({len(parts)-2} targets).")


@router.message(Command("deletelist"))
async def cmd_deletelist(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("/deletelist <name>")
        return
    remove_list(message.from_user.id, parts[1].strip())
    await message.answer("Deleted.")


@router.message(Command("editname"))
async def cmd_editname(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("/editname <alias> <first> [last]")
        return
    acc = find_account(message.from_user.id, parts[1].strip())
    if not acc:
        await message.answer("Not found.")
        return
    first, last = parts[2], parts[3] if len(parts) > 3 else ""
    client = _build_client_from_session(acc.session_string, acc.device_preset)
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
        await message.answer("/editbio <alias> <bio>")
        return
    acc = find_account(message.from_user.id, parts[1].strip())
    if not acc:
        await message.answer("Not found.")
        return
    client = _build_client_from_session(acc.session_string, acc.device_preset)
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
        await message.answer("/editusername <alias> <username>")
        return
    acc = find_account(message.from_user.id, parts[1].strip())
    if not acc:
        await message.answer("Not found.")
        return
    client = _build_client_from_session(acc.session_string, acc.device_preset)
    try:
        await client.connect()
        await client.get_me()
        from telethon.tl.functions.account import UpdateUsernameRequest
        await client(UpdateUsernameRequest(username=parts[2].strip().lstrip("@")))
        await message.answer(f"[{acc.alias}] Username updated.")
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
        await message.answer("/logout <alias>")
        return
    acc = find_account(message.from_user.id, parts[1].strip())
    if not acc:
        await message.answer("Not found.")
        return
    client = _build_client_from_session(acc.session_string, acc.device_preset)
    try:
        await client.connect()
        await client.log_out()
    except Exception:
        pass
    finally:
        if client.is_connected():
            await client.disconnect()
    remove_account(message.from_user.id, acc.phone)
    await message.answer(f"[{acc.alias}] Logged out.")


@router.message(Command("remove"))
async def cmd_remove(message: Message) -> None:
    if not await _check_access(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("/remove <alias>")
        return
    acc = remove_account(message.from_user.id, parts[1].strip())
    if acc:
        await message.answer(f"[{acc.alias}] Removed.")
    else:
        await message.answer("Not found.")


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
    log.info("Bot starting...")
    await dp.start_polling(bot)
