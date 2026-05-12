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
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
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


def _main_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Add Account"), KeyboardButton(text="My Accounts")],
            [KeyboardButton(text="Health Check"), KeyboardButton(text="Broadcast")],
            [KeyboardButton(text="Manage Lists"), KeyboardButton(text="Join Group")],
            [KeyboardButton(text="Edit Profile"), KeyboardButton(text="Remove/Logout")],
        ],
        resize_keyboard=True,
    )


def _back_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="<< Menu")]],
        resize_keyboard=True,
    )


def _accounts_kb(admin_id: int) -> ReplyKeyboardMarkup:
    """Generate account selection as reply keyboard."""
    accounts = get_accounts(admin_id)
    buttons = [[KeyboardButton(text=a.alias)] for a in accounts]
    buttons.append([KeyboardButton(text="<< Menu")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


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
    await message.answer(f"Telegram Manager ({n} accounts)", reply_markup=_main_kb())


# ---------------------------------------------------------------------------
# Main menu button handlers
# ---------------------------------------------------------------------------
@router.message(F.text == "<< Menu")
async def btn_menu(message: Message) -> None:
    _state.pop(message.from_user.id, None)
    n = len(get_accounts(message.from_user.id))
    await message.answer(f"Telegram Manager ({n} accounts)", reply_markup=_main_kb())


@router.message(F.text == "Add Account")
async def btn_add(message: Message) -> None:
    _state[message.from_user.id] = {"action": "login_phone"}
    await message.answer("Enter phone number (e.g. +628123456789):", reply_markup=_back_kb())


@router.message(F.text == "My Accounts")
async def btn_accounts(message: Message) -> None:
    accounts = get_accounts(message.from_user.id)
    if not accounts:
        await message.answer("No accounts yet.", reply_markup=_main_kb())
        return
    lines = [f"{i}. [{a.alias}] {a.phone} — {a.display_name}" for i, a in enumerate(accounts, 1)]
    await message.answer("\n".join(lines), reply_markup=_main_kb())


@router.message(F.text == "Health Check")
async def btn_health(message: Message) -> None:
    accounts = get_accounts(message.from_user.id)
    if not accounts:
        await message.answer("No accounts.", reply_markup=_main_kb())
        return
    await message.answer("Checking...")
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
    await message.answer("\n".join(lines), reply_markup=_main_kb())


@router.message(F.text == "Broadcast")
async def btn_broadcast(message: Message) -> None:
    lists = get_lists(message.from_user.id)
    if not lists:
        await message.answer("No lists. Create one first via 'Manage Lists'.", reply_markup=_main_kb())
        return
    buttons = [[KeyboardButton(text=f"bc:{bl.name}")] for bl in lists]
    buttons.append([KeyboardButton(text="<< Menu")])
    await message.answer(
        "Pick a list:",
        reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True),
    )
    _state[message.from_user.id] = {"action": "broadcast_pick"}


@router.message(F.text == "Manage Lists")
async def btn_lists(message: Message) -> None:
    lists = get_lists(message.from_user.id)
    lines = []
    if lists:
        for bl in lists:
            lines.append(f"  {bl.name} ({len(bl.targets)} targets)")
    buttons = [[KeyboardButton(text="+ Create List")]]
    if lists:
        buttons.append([KeyboardButton(text="Delete List")])
    buttons.append([KeyboardButton(text="<< Menu")])
    text = "Lists:\n" + "\n".join(lines) if lines else "No lists yet."
    await message.answer(text, reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))


@router.message(F.text == "+ Create List")
async def btn_createlist(message: Message) -> None:
    _state[message.from_user.id] = {"action": "createlist_name"}
    await message.answer("Enter list name:", reply_markup=_back_kb())


@router.message(F.text == "Delete List")
async def btn_deletelist(message: Message) -> None:
    lists = get_lists(message.from_user.id)
    if not lists:
        await message.answer("No lists.", reply_markup=_main_kb())
        return
    buttons = [[KeyboardButton(text=f"del:{bl.name}")] for bl in lists]
    buttons.append([KeyboardButton(text="<< Menu")])
    _state[message.from_user.id] = {"action": "deletelist_pick"}
    await message.answer("Pick list to delete:", reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True))


@router.message(F.text == "Join Group")
async def btn_join(message: Message) -> None:
    accounts = get_accounts(message.from_user.id)
    if not accounts:
        await message.answer("No accounts.", reply_markup=_main_kb())
        return
    _state[message.from_user.id] = {"action": "join_pick"}
    await message.answer("Pick account:", reply_markup=_accounts_kb(message.from_user.id))


@router.message(F.text == "Edit Profile")
async def btn_edit(message: Message) -> None:
    accounts = get_accounts(message.from_user.id)
    if not accounts:
        await message.answer("No accounts.", reply_markup=_main_kb())
        return
    _state[message.from_user.id] = {"action": "edit_pick"}
    await message.answer("Pick account to edit:", reply_markup=_accounts_kb(message.from_user.id))


@router.message(F.text == "Remove/Logout")
async def btn_cleanup(message: Message) -> None:
    accounts = get_accounts(message.from_user.id)
    if not accounts:
        await message.answer("No accounts.", reply_markup=_main_kb())
        return
    _state[message.from_user.id] = {"action": "cleanup_pick"}
    await message.answer("Pick account:", reply_markup=_accounts_kb(message.from_user.id))


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

    # --- Broadcast pick ---
    elif action == "broadcast_pick":
        if text.startswith("bc:"):
            list_name = text[3:]
            _state[uid] = {"action": "broadcast_msg", "list": list_name}
            await message.answer(f"List: {list_name}\n\nType the message to broadcast:", reply_markup=_back_kb())
        else:
            await message.answer("Pick a list from the buttons.")

    elif action == "broadcast_msg":
        _state[uid]["text"] = text
        _state[uid]["action"] = "broadcast_watermark"
        await message.answer("Enter watermark (will be added below your message).\nSend 'skip' for no watermark:", reply_markup=_back_kb())

    elif action == "broadcast_watermark":
        wm = "" if text.lower() == "skip" else text
        _state[uid]["watermark"] = wm
        _state[uid]["action"] = "broadcast_delay"
        buttons = [
            [KeyboardButton(text="Auto (3-10s)"), KeyboardButton(text="No delay")],
            [KeyboardButton(text="<< Menu")],
        ]
        await message.answer(
            "Delay between each message?\n\nPick or type custom (e.g. '5' or '3-8'):",
            reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True),
        )

    elif action == "broadcast_delay":
        if text == "Auto (3-10s)":
            delay_min, delay_max = 3.0, 10.0
        elif text == "No delay":
            delay_min, delay_max = 0.0, 0.0
        elif "-" in text:
            parts = text.split("-")
            delay_min, delay_max = float(parts[0]), float(parts[1])
        else:
            try:
                delay_min = delay_max = float(text)
            except ValueError:
                await message.answer("Invalid. Enter number or range (e.g. 3-8):")
                return

        st = _state[uid]
        bl = get_list(uid, st["list"])
        accounts = get_accounts(uid)
        if not bl or not accounts:
            await message.answer("List or accounts not found.", reply_markup=_main_kb())
            _state.pop(uid, None)
            return

        # Build final message with watermark
        final_text = st["text"]
        if st.get("watermark"):
            final_text += f"\n\n{st['watermark']}"

        delay_str = f"{delay_min}-{delay_max}s" if delay_min != delay_max else f"{delay_min}s"
        if delay_min == 0:
            delay_str = "none"

        await message.answer(
            f"Broadcasting (continuous loop)\n"
            f"List: {st['list']} ({len(bl.targets)} targets)\n"
            f"Accounts: {len(accounts)}\n"
            f"Delay: {delay_str}\n"
            f"Watermark: {st.get('watermark') or '(none)'}\n\n"
            f"Sending... (send 'stop' to stop)",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="stop")]],
                resize_keyboard=True,
            ),
        )

        _state[uid] = {"action": "broadcasting"}

        # Start continuous broadcast
        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest

        round_num = 0
        bot = message.bot
        chat_id = message.chat.id

        while _state.get(uid, {}).get("action") == "broadcasting":
            round_num += 1
            await bot.send_message(chat_id, f"--- Round {round_num} ---")
            for acc in accounts:
                if _state.get(uid, {}).get("action") != "broadcasting":
                    break
                client = _client_from_session(acc.session_string, acc.device_preset)
                try:
                    await client.connect()
                    await client.get_me()
                    for target in bl.targets:
                        if _state.get(uid, {}).get("action") != "broadcasting":
                            break
                        # Auto-join
                        try:
                            if "t.me/+" in target or "joinchat/" in target:
                                await client(ImportChatInviteRequest(target.split("+")[-1].split("joinchat/")[-1]))
                            else:
                                await client(JoinChannelRequest(target.lstrip("@").replace("https://t.me/", "")))
                        except Exception:
                            pass
                        # Send
                        try:
                            e = target.lstrip("@").replace("https://t.me/", "").split("+")[0]
                            await client.send_message(e, final_text)
                            await bot.send_message(chat_id, f"[{acc.alias}] -> {target}: sent")
                        except Exception as ex:
                            await bot.send_message(chat_id, f"[{acc.alias}] -> {target}: {type(ex).__name__}")
                        # Delay
                        if delay_max > 0:
                            await asyncio.sleep(random.uniform(delay_min, delay_max))
                except Exception as ex:
                    await bot.send_message(chat_id, f"[{acc.alias}] FAIL: {type(ex).__name__}")
                finally:
                    if client.is_connected():
                        await client.disconnect()

            if _state.get(uid, {}).get("action") == "broadcasting":
                await bot.send_message(chat_id, f"Round {round_num} done. Starting next round...")
                await asyncio.sleep(2)

        await bot.send_message(chat_id, "Broadcast stopped.", reply_markup=_main_kb())

    elif action == "broadcasting":
        if text.lower() == "stop":
            _state.pop(uid, None)
            # The loop checks state and will exit
        else:
            await message.answer("Send 'stop' to stop broadcasting.")
    elif action == "deletelist_pick":
        if text.startswith("del:"):
            name = text[4:]
            remove_list(uid, name)
            _state.pop(uid, None)
            await message.answer(f"List '{name}' deleted.", reply_markup=_main_kb())
        else:
            await message.answer("Pick a list from the buttons.")

    # --- Join pick account ---
    elif action == "join_pick":
        acc = find_account(uid, text)
        if not acc:
            await message.answer("Account not found. Pick from buttons.")
            return
        _state[uid] = {"action": "join_target", "alias": text}
        await message.answer(f"Account: {text}\n\nEnter group/channel username or invite link:", reply_markup=_back_kb())

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
            client = _client_from_session(acc.session_string, acc.device_preset)
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
