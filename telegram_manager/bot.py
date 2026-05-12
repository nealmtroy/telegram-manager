"""Telegram Bot interface with Supabase storage — fully button-driven.

No slash commands needed. All interaction via inline buttons + text input
guided by conversation state.
"""
from __future__ import annotations

import asyncio
import os
import random
from typing import Dict

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
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

# Per-user state for multi-step flows
_state: Dict[int, dict] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _client_from_session(session_string: str, preset_key: str) -> TelegramClient:
    cfg = load_config()
    preset = get_preset(preset_key)
    return TelegramClient(
        StringSession(session_string), cfg.api_id, cfg.api_hash,
        device_model=preset.device_model, system_version=preset.system_version,
        app_version=preset.app_version, lang_code=preset.lang_code,
        system_lang_code=preset.system_lang_code,
    )


def _new_client(preset_key: str = "random"):
    cfg = load_config()
    preset = get_preset(preset_key)
    client = TelegramClient(
        StringSession(), cfg.api_id, cfg.api_hash,
        device_model=preset.device_model, system_version=preset.system_version,
        app_version=preset.app_version, lang_code=preset.lang_code,
        system_lang_code=preset.system_lang_code,
    )
    return client, preset


def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Add Account", callback_data="add"),
         InlineKeyboardButton(text="My Accounts", callback_data="accounts")],
        [InlineKeyboardButton(text="Health Check", callback_data="health"),
         InlineKeyboardButton(text="Broadcast", callback_data="broadcast")],
        [InlineKeyboardButton(text="Manage Lists", callback_data="lists"),
         InlineKeyboardButton(text="Join Group", callback_data="join")],
        [InlineKeyboardButton(text="Edit Profile", callback_data="edit"),
         InlineKeyboardButton(text="Remove/Logout", callback_data="cleanup")],
    ])


def _back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="<< Menu", callback_data="menu")]
    ])


def _accounts_kb(admin_id: int, action: str) -> InlineKeyboardMarkup:
    """Generate account selection buttons."""
    accounts = get_accounts(admin_id)
    buttons = [[InlineKeyboardButton(
        text=f"{a.alias} ({a.phone})", callback_data=f"{action}:{a.alias}"
    )] for a in accounts]
    buttons.append([InlineKeyboardButton(text="<< Menu", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ---------------------------------------------------------------------------
# /start + menu
# ---------------------------------------------------------------------------
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    uid = message.from_user.id
    if is_managed_account(uid):
        await message.answer("Access denied. Your account is managed by another admin.")
        return
    register_admin(uid, message.from_user.username or "", message.from_user.first_name or "")
    n = len(get_accounts(uid))
    await message.answer(f"Telegram Manager ({n} accounts)", reply_markup=_main_kb())


@router.callback_query(F.data == "menu")
async def cb_menu(cq: CallbackQuery) -> None:
    await cq.answer()
    _state.pop(cq.from_user.id, None)
    n = len(get_accounts(cq.from_user.id))
    await cq.message.edit_text(f"Telegram Manager ({n} accounts)", reply_markup=_main_kb())


# ---------------------------------------------------------------------------
# Add Account flow (button-driven)
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "add")
async def cb_add(cq: CallbackQuery) -> None:
    await cq.answer()
    _state[cq.from_user.id] = {"action": "login_phone"}
    await cq.message.edit_text("Enter phone number (e.g. +628123456789):", reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "accounts")
async def cb_accounts(cq: CallbackQuery) -> None:
    await cq.answer()
    accounts = get_accounts(cq.from_user.id)
    if not accounts:
        await cq.message.edit_text("No accounts yet.", reply_markup=_back_kb())
        return
    lines = [f"{i}. [{a.alias}] {a.phone} — {a.display_name}" for i, a in enumerate(accounts, 1)]
    await cq.message.edit_text("\n".join(lines), reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "health")
async def cb_health(cq: CallbackQuery) -> None:
    await cq.answer()
    accounts = get_accounts(cq.from_user.id)
    if not accounts:
        await cq.message.edit_text("No accounts.", reply_markup=_back_kb())
        return
    await cq.message.edit_text("Checking...")
    lines = []
    for acc in accounts:
        client = _client_from_session(acc.session_string, acc.device_preset)
        try:
            await client.connect()
            me = await client.get_me()
            lines.append(f"[{acc.alias}] OK — {me.first_name}")
        except Exception as e:
            lines.append(f"[{acc.alias}] FAIL — {type(e).__name__}")
        finally:
            if client.is_connected():
                await client.disconnect()
    await cq.message.edit_text("\n".join(lines), reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Broadcast
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "broadcast")
async def cb_broadcast(cq: CallbackQuery) -> None:
    await cq.answer()
    lists = get_lists(cq.from_user.id)
    if not lists:
        await cq.message.edit_text(
            "No broadcast lists.\nUse 'Manage Lists' to create one first.",
            reply_markup=_back_kb()
        )
        return
    buttons = [[InlineKeyboardButton(
        text=f"{bl.name} ({len(bl.targets)} targets)", callback_data=f"bc:{bl.name}"
    )] for bl in lists]
    buttons.append([InlineKeyboardButton(text="<< Menu", callback_data="menu")])
    await cq.message.edit_text("Pick a list to broadcast:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("bc:"))
async def cb_broadcast_pick(cq: CallbackQuery) -> None:
    await cq.answer()
    list_name = cq.data[3:]
    _state[cq.from_user.id] = {"action": "broadcast_msg", "list": list_name}
    await cq.message.edit_text(f"List: {list_name}\n\nType the message to broadcast:", reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Lists management
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "lists")
async def cb_lists(cq: CallbackQuery) -> None:
    await cq.answer()
    lists = get_lists(cq.from_user.id)
    buttons = []
    if lists:
        for bl in lists:
            buttons.append([InlineKeyboardButton(
                text=f"{bl.name} ({len(bl.targets)})", callback_data=f"viewlist:{bl.name}"
            )])
    buttons.append([InlineKeyboardButton(text="+ Create List", callback_data="createlist")])
    buttons.append([InlineKeyboardButton(text="<< Menu", callback_data="menu")])
    await cq.message.edit_text("Broadcast Lists:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("viewlist:"))
async def cb_viewlist(cq: CallbackQuery) -> None:
    await cq.answer()
    name = cq.data[9:]
    bl = get_list(cq.from_user.id, name)
    if not bl:
        await cq.message.edit_text("List not found.", reply_markup=_back_kb())
        return
    targets = "\n".join(f"  {i}. {t}" for i, t in enumerate(bl.targets, 1))
    buttons = [
        [InlineKeyboardButton(text="Delete this list", callback_data=f"dellist:{name}")],
        [InlineKeyboardButton(text="<< Lists", callback_data="lists")],
    ]
    await cq.message.edit_text(f"List: {name}\n\n{targets}", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("dellist:"))
async def cb_dellist(cq: CallbackQuery) -> None:
    await cq.answer()
    name = cq.data[8:]
    remove_list(cq.from_user.id, name)
    await cq.message.edit_text(f"List '{name}' deleted.", reply_markup=_back_kb())


@router.callback_query(F.data == "createlist")
async def cb_createlist(cq: CallbackQuery) -> None:
    await cq.answer()
    _state[cq.from_user.id] = {"action": "createlist_name"}
    await cq.message.edit_text("Enter a name for the new list:", reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Join group
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "join")
async def cb_join(cq: CallbackQuery) -> None:
    await cq.answer()
    accounts = get_accounts(cq.from_user.id)
    if not accounts:
        await cq.message.edit_text("No accounts.", reply_markup=_back_kb())
        return
    await cq.message.edit_text("Pick account to join with:", reply_markup=_accounts_kb(cq.from_user.id, "join"))


@router.callback_query(F.data.startswith("join:"))
async def cb_join_pick(cq: CallbackQuery) -> None:
    await cq.answer()
    alias = cq.data[5:]
    _state[cq.from_user.id] = {"action": "join_target", "alias": alias}
    await cq.message.edit_text(f"Account: {alias}\n\nEnter group/channel username or invite link:", reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Edit profile
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "edit")
async def cb_edit(cq: CallbackQuery) -> None:
    await cq.answer()
    accounts = get_accounts(cq.from_user.id)
    if not accounts:
        await cq.message.edit_text("No accounts.", reply_markup=_back_kb())
        return
    await cq.message.edit_text("Pick account to edit:", reply_markup=_accounts_kb(cq.from_user.id, "editpick"))


@router.callback_query(F.data.startswith("editpick:"))
async def cb_editpick(cq: CallbackQuery) -> None:
    await cq.answer()
    alias = cq.data[9:]
    buttons = [
        [InlineKeyboardButton(text="Edit Name", callback_data=f"ename:{alias}"),
         InlineKeyboardButton(text="Edit Bio", callback_data=f"ebio:{alias}")],
        [InlineKeyboardButton(text="Edit Username", callback_data=f"euser:{alias}")],
        [InlineKeyboardButton(text="<< Menu", callback_data="menu")],
    ]
    await cq.message.edit_text(f"Editing [{alias}]:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("ename:"))
async def cb_ename(cq: CallbackQuery) -> None:
    await cq.answer()
    alias = cq.data[6:]
    _state[cq.from_user.id] = {"action": "edit_name", "alias": alias}
    await cq.message.edit_text(f"[{alias}] Enter new name (first last):", reply_markup=_back_kb())


@router.callback_query(F.data.startswith("ebio:"))
async def cb_ebio(cq: CallbackQuery) -> None:
    await cq.answer()
    alias = cq.data[5:]
    _state[cq.from_user.id] = {"action": "edit_bio", "alias": alias}
    await cq.message.edit_text(f"[{alias}] Enter new bio:", reply_markup=_back_kb())


@router.callback_query(F.data.startswith("euser:"))
async def cb_euser(cq: CallbackQuery) -> None:
    await cq.answer()
    alias = cq.data[6:]
    _state[cq.from_user.id] = {"action": "edit_username", "alias": alias}
    await cq.message.edit_text(f"[{alias}] Enter new username (without @):", reply_markup=_back_kb())


# ---------------------------------------------------------------------------
# Remove / Logout
# ---------------------------------------------------------------------------
@router.callback_query(F.data == "cleanup")
async def cb_cleanup(cq: CallbackQuery) -> None:
    await cq.answer()
    accounts = get_accounts(cq.from_user.id)
    if not accounts:
        await cq.message.edit_text("No accounts.", reply_markup=_back_kb())
        return
    buttons = [[InlineKeyboardButton(
        text=f"{a.alias} ({a.phone})", callback_data=f"cleanpick:{a.alias}"
    )] for a in accounts]
    buttons.append([InlineKeyboardButton(text="<< Menu", callback_data="menu")])
    await cq.message.edit_text("Pick account:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("cleanpick:"))
async def cb_cleanpick(cq: CallbackQuery) -> None:
    await cq.answer()
    alias = cq.data[10:]
    buttons = [
        [InlineKeyboardButton(text="Logout (revoke)", callback_data=f"logout:{alias}"),
         InlineKeyboardButton(text="Remove only", callback_data=f"remove:{alias}")],
        [InlineKeyboardButton(text="<< Menu", callback_data="menu")],
    ]
    await cq.message.edit_text(f"[{alias}] What to do?", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("logout:"))
async def cb_logout(cq: CallbackQuery) -> None:
    await cq.answer()
    alias = cq.data[7:]
    acc = find_account(cq.from_user.id, alias)
    if not acc:
        await cq.message.edit_text("Not found.", reply_markup=_back_kb())
        return
    client = _client_from_session(acc.session_string, acc.device_preset)
    try:
        await client.connect()
        await client.log_out()
    except Exception:
        pass
    finally:
        if client.is_connected():
            await client.disconnect()
    remove_account(cq.from_user.id, acc.phone)
    await cq.message.edit_text(f"[{alias}] Logged out and removed.", reply_markup=_back_kb())


@router.callback_query(F.data.startswith("remove:"))
async def cb_remove(cq: CallbackQuery) -> None:
    await cq.answer()
    alias = cq.data[7:]
    acc = remove_account(cq.from_user.id, alias)
    if acc:
        await cq.message.edit_text(f"[{alias}] Removed.", reply_markup=_back_kb())
    else:
        await cq.message.edit_text("Not found.", reply_markup=_back_kb())



# ---------------------------------------------------------------------------
# Text message handler — processes all state-driven input
# ---------------------------------------------------------------------------
@router.message(F.text)
async def handle_text(message: Message) -> None:
    uid = message.from_user.id
    if not is_registered_admin(uid):
        return

    state = _state.get(uid)
    if not state:
        # No active state, show menu
        n = len(get_accounts(uid))
        await message.answer(f"Telegram Manager ({n} accounts)", reply_markup=_main_kb())
        return

    action = state["action"]
    text = message.text.strip()

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
        await message.answer(f"Code sent to {text}\nDevice: {preset.device_model}\n\nEnter the 5-digit code:")

    elif action == "login_code":
        client = state["client"]
        try:
            await client.sign_in(state["phone"], text, phone_code_hash=state["phone_code_hash"])
        except PhoneCodeInvalidError:
            await message.answer("Invalid code. Try again:")
            return
        except PhoneCodeExpiredError:
            await client.disconnect()
            _state.pop(uid, None)
            await message.answer("Code expired. Start over.", reply_markup=_back_kb())
            return
        except SessionPasswordNeededError:
            _state[uid]["action"] = "login_2fa"
            await message.answer("2FA enabled. Enter your cloud password:")
            return
        await _finish_login(message, uid)

    elif action == "login_2fa":
        client = state["client"]
        try:
            await client.sign_in(password=text)
        except Exception as e:
            await message.answer(f"Wrong password: {e}\nTry again:")
            return
        state["is_2fa"] = True
        await _finish_login(message, uid)

    # --- Broadcast message ---
    elif action == "broadcast_msg":
        list_name = state["list"]
        _state.pop(uid, None)
        bl = get_list(uid, list_name)
        if not bl:
            await message.answer("List not found.", reply_markup=_back_kb())
            return
        accounts = get_accounts(uid)
        if not accounts:
            await message.answer("No accounts.", reply_markup=_back_kb())
            return
        await message.answer(f"Broadcasting to {len(bl.targets)} targets from {len(accounts)} accounts...")

        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest

        results = []
        for acc in accounts:
            client = _client_from_session(acc.session_string, acc.device_preset)
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
        await message.answer("\n".join(results), reply_markup=_back_kb())

    # --- Create list ---
    elif action == "createlist_name":
        _state[uid] = {"action": "createlist_targets", "name": text, "targets": []}
        await message.answer(f"List: {text}\n\nEnter targets one by one.\nSend 'done' when finished:")

    elif action == "createlist_targets":
        if text.lower() == "done":
            targets = state["targets"]
            if not targets:
                await message.answer("No targets added. Cancelled.", reply_markup=_back_kb())
            else:
                add_list(BroadcastListRow(admin_id=uid, name=state["name"], targets=targets))
                await message.answer(f"List '{state['name']}' created ({len(targets)} targets).", reply_markup=_back_kb())
            _state.pop(uid, None)
        else:
            state["targets"].append(text)
            await message.answer(f"Added: {text} ({len(state['targets'])} total)\n\nNext target or 'done':")

    # --- Join ---
    elif action == "join_target":
        alias = state["alias"]
        _state.pop(uid, None)
        acc = find_account(uid, alias)
        if not acc:
            await message.answer("Account not found.", reply_markup=_back_kb())
            return
        client = _client_from_session(acc.session_string, acc.device_preset)
        try:
            await client.connect()
            await client.get_me()
            from telethon.tl.functions.channels import JoinChannelRequest
            from telethon.tl.functions.messages import ImportChatInviteRequest
            if "t.me/+" in text or "joinchat/" in text:
                await client(ImportChatInviteRequest(text.split("+")[-1].split("joinchat/")[-1]))
            else:
                await client(JoinChannelRequest(text.lstrip("@").replace("https://t.me/", "")))
            await message.answer(f"[{alias}] Joined {text}", reply_markup=_back_kb())
        except Exception as e:
            await message.answer(f"Error: {type(e).__name__}: {e}", reply_markup=_back_kb())
        finally:
            if client.is_connected():
                await client.disconnect()

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
        client = _client_from_session(acc.session_string, acc.device_preset)
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
        client = _client_from_session(acc.session_string, acc.device_preset)
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
        client = _client_from_session(acc.session_string, acc.device_preset)
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


async def _finish_login(message: Message, admin_id: int) -> None:
    state = _state.pop(admin_id)
    client = state["client"]
    me = await client.get_me()

    if is_registered_admin(me.id):
        await client.disconnect()
        await message.answer("Cannot add — this user is already an admin.", reply_markup=_back_kb())
        return

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
        is_2fa=state.get("is_2fa", False),
        device_preset=state["preset"].key,
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
    log.info("Bot starting...")
    await dp.start_polling(bot)
