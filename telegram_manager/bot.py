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
    delete_saved_msg,
    find_account,
    get_accounts,
    get_admin_lang,
    get_list,
    get_lists,
    get_saved_messages,
    grant_vip,
    is_managed_account,
    is_registered_admin,
    is_vip_admin,
    register_admin,
    remove_account,
    remove_list,
    save_broadcast_msg,
    set_admin_lang,
    transfer_all,
)
from .device_presets import get_preset
from .i18n import LANGUAGES, get_lang, set_lang, t
from .logger import get_logger

log = get_logger("bot")
router = Router()

# Per-user state for multi-step flows
_state: Dict[int, dict] = {}
_last_bot_msg: Dict[int, int] = {}  # uid -> message_id to delete


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


def _main_kb(uid: int = 0) -> ReplyKeyboardMarkup:
    lang = get_lang(uid) if uid else "id"
    labels = _MENU_LABELS.get(lang, _MENU_LABELS["id"])
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=labels[0]), KeyboardButton(text=labels[1])],
            [KeyboardButton(text=labels[2]), KeyboardButton(text=labels[3])],
            [KeyboardButton(text=labels[4]), KeyboardButton(text=labels[5])],
            [KeyboardButton(text=labels[6]), KeyboardButton(text=labels[7])],
            [KeyboardButton(text=labels[8])],
        ],
        resize_keyboard=True,
    )


_MENU_LABELS = {
    "id": [
        "➕ Tambah Akun", "👤 Akun Saya",
        "💚 Health Check", "📣 Broadcast",
        "💬 Kelola Text", "👥 Manage Group",
        "🗑 Hapus/Logout", "🔄 Transfer",
        "🌐 Bahasa",
    ],
    "en": [
        "➕ Add Account", "👤 My Accounts",
        "💚 Health Check", "📣 Broadcast",
        "💬 Manage Text", "👥 Manage Group",
        "🗑 Remove/Logout", "🔄 Transfer",
        "🌐 Language",
    ],
    "ms": [
        "➕ Tambah Akaun", "👤 Akaun Saya",
        "💚 Health Check", "📣 Broadcast",
        "💬 Kelola Text", "👥 Manage Group",
        "🗑 Hapus/Logout", "🔄 Transfer",
        "🌐 Bahasa",
    ],
    "th": [
        "➕ เพิ่มบัญชี", "👤 บัญชีของฉัน",
        "💚 Health Check", "📣 Broadcast",
        "💬 Manage Text", "👥 Manage Group",
        "🗑 ลบ/Logout", "🔄 โอนข้อมูล",
        "🌐 ภาษา",
    ],
    "vi": [
        "➕ Thêm TK", "👤 Tài khoản",
        "💚 Health Check", "📣 Broadcast",
        "💬 Manage Text", "👥 Manage Group",
        "🗑 Xóa/Logout", "🔄 Chuyển",
        "🌐 Ngôn ngữ",
    ],
    "zh": [
        "➕ 添加账号", "👤 我的账号",
        "💚 健康检查", "📣 广播",
        "💬 Manage Text", "👥 Manage Group",
        "🗑 删除/登出", "🔄 转移",
        "🌐 语言",
    ],
    "ja": [
        "➕ アカウント追加", "👤 マイアカウント",
        "💚 ヘルスチェック", "📣 ブロードキャスト",
        "💬 Manage Text", "👥 Manage Group",
        "🗑 削除/ログアウト", "🔄 転送",
        "🌐 言語",
    ],
    "ko": [
        "➕ 계정 추가", "👤 내 계정",
        "💚 상태 확인", "📣 브로드캐스트",
        "💬 Manage Text", "👥 Manage Group",
        "🗑 삭제/로그아웃", "🔄 전송",
        "🌐 언어",
    ],
    "hi": [
        "➕ अकाउंट जोड़ें", "👤 मेरे अकाउंट",
        "💚 Health Check", "📣 Broadcast",
        "💬 Manage Text", "👥 Manage Group",
        "🗑 हटाएं/Logout", "🔄 ट्रांसफर",
        "🌐 भाषा",
    ],
    "fil": [
        "➕ Dagdag Account", "👤 Mga Account",
        "💚 Health Check", "📣 Broadcast",
        "💬 Manage Text", "👥 Manage Group",
        "🗑 Remove/Logout", "🔄 Transfer",
        "🌐 Wika",
    ],
}


def _get_menu_action(text: str) -> str | None:
    """Map any language button text to action key."""
    for labels in _MENU_LABELS.values():
        if text in labels:
            idx = labels.index(text)
            return ["add", "accounts", "health", "broadcast",
                    "saved", "lists", "cleanup", "transfer", "lang"][idx]
    return None


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
    # Load language preference
    lang = get_admin_lang(uid)
    set_lang(uid, lang)
    accounts = get_accounts(uid)
    if not accounts:
        _state[uid] = {"action": "login_phone"}
        await message.answer(f"Status: {_vip_label(uid)}\n\n{t('welcome_new', uid)}", reply_markup=_back_kb())
        return
    await message.answer(
        f"Status: {_vip_label(uid)}\n\n{t('main_menu', uid, n=len(accounts))}",
        reply_markup=_main_kb(uid),
    )


@router.message(Command("vip", "status"))
async def cmd_vip_status(message: Message) -> None:
    uid = message.from_user.id
    await message.answer(f"Status kamu: {_vip_label(uid)}")


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

    grant_vip(target_id, uid)
    await message.answer(f"VIP gifted ke user `{target_id}`.", parse_mode="Markdown")


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
    saved = get_saved_messages(uid)
    _state[uid] = {"action": "broadcast_msg_choice", "list": list_name}
    buttons = [[InlineKeyboardButton(text=s["name"], callback_data=f"sm:{s['name']}")] for s in saved]
    buttons.append([InlineKeyboardButton(text="New message", callback_data="newmsg")])
    await cq.message.edit_text(f"List: {list_name}", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


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
    found = next((s for s in get_saved_messages(uid) if s["name"] == cq.data[3:]), None)
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
    name = cq.data[3:]
    found = next((s for s in get_saved_messages(uid) if s["name"] == name), None)
    if not found:
        await cq.message.edit_text("Text tidak ditemukan.")
        return
    preview = found.get("text", "")
    if len(preview) > 3000:
        preview = preview[:3000] + "\n\n..."
    buttons = [[InlineKeyboardButton(text="🗑 Delete", callback_data=f"sd:{name}")]]
    await cq.message.edit_text(
        f"💬 {name}\n\n{preview}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("sd:"))
async def cb_sd(cq: CallbackQuery) -> None:
    await cq.answer()
    delete_saved_msg(cq.from_user.id, cq.data[3:])
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
        "(satu per baris — @username / chat_id / https://t.me/xxx)")


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
    acc = find_account(uid, cq.data[3:])
    if acc:
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
    await cq.message.edit_text(f"[{cq.data[3:]}] Logged out.")


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
    buttons = [
        [InlineKeyboardButton(text="✏️ Edit", callback_data=f"edit:{acc.alias}"),
         InlineKeyboardButton(text="📨 OTP", callback_data=f"otp:{acc.alias}")],
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
    client = _client_from_session(acc.session_string, acc.device_preset)
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


async def _start_broadcast(message: Message, uid: int) -> None:
    """Start the continuous broadcast loop."""
    from telethon.errors import ChatWriteForbiddenError, SlowModeWaitError, UserBannedInChannelError
    from datetime import datetime, timezone

    st = _state.get(uid, {})
    bl = get_list(uid, st.get("list", ""))
    accounts = get_accounts(uid)
    if not bl or not accounts:
        _state.pop(uid, None)
        return

    group_delay_min, group_delay_max = st.get("group_delay", st.get("delay", (3.0, 10.0)))
    round_delay_min, round_delay_max = st.get("round_delay", (0.0, 0.0))

    watermark = _watermark_for_user(uid)
    media_bytes = None
    media_filename = None
    has_media = False

    if "saved_text" in st:
        msg_text = st["saved_text"]
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

    if watermark:
        msg_text = (msg_text + f"\n\n{watermark}") if msg_text else watermark

    bot = message.bot
    log_dest = _log_chat_id()
    round_num = 0

    while _state.get(uid, {}).get("action") == "broadcasting":
        round_num += 1
        round_success = []
        round_failed = []
        target_attempt = 0
        total_targets = len(accounts) * len(bl.targets)

        for acc in accounts:
            if _state.get(uid, {}).get("action") != "broadcasting":
                break
            acc_success = []
            acc_failed = []
            client = _client_from_session(acc.session_string, acc.device_preset)
            try:
                await client.connect()
                await client.get_me()
                for target in bl.targets:
                    if _state.get(uid, {}).get("action") != "broadcasting":
                        break
                    try:
                        entities = await _broadcast_entities_for_target(client, target)
                        sent_count = 0
                        for entity in entities:
                            if has_media and media_bytes:
                                await client.send_file(entity, media_bytes, caption=msg_text, parse_mode="html", file_name=media_filename)
                            else:
                                await client.send_message(entity, msg_text, parse_mode="html")
                            sent_count += 1
                        success_line = f"{acc.alias} -> {target}"
                        if sent_count > 1:
                            success_line += f" ({sent_count} chats)"
                        round_success.append(success_line)
                        acc_success.append(success_line)
                    except (ChatWriteForbiddenError, UserBannedInChannelError):
                        failed_line = f"{acc.alias} -> {target}: Blocked"
                        round_failed.append(failed_line)
                        acc_failed.append(failed_line)
                    except SlowModeWaitError as sme:
                        failed_line = f"{acc.alias} -> {target}: SlowMode {sme.seconds}s"
                        round_failed.append(failed_line)
                        acc_failed.append(failed_line)
                    except FloodWaitError as fw:
                        failed_line = f"{acc.alias} -> {target}: Flood {fw.seconds}s"
                        round_failed.append(failed_line)
                        acc_failed.append(failed_line)
                        await asyncio.sleep(fw.seconds)
                    except Exception as ex:
                        failed_line = f"{acc.alias} -> {target}: {type(ex).__name__}"
                        round_failed.append(failed_line)
                        acc_failed.append(failed_line)
                    target_attempt += 1
                    if (
                        _state.get(uid, {}).get("action") == "broadcasting"
                        and target_attempt < total_targets
                        and group_delay_max > 0
                    ):
                        await asyncio.sleep(random.uniform(group_delay_min, group_delay_max))
            except Exception as ex:
                failed_line = f"{acc.alias}: {type(ex).__name__}"
                round_failed.append(failed_line)
                acc_failed.append(failed_line)
            finally:
                if client.is_connected():
                    if log_dest and _state.get(uid, {}).get("action") == "broadcasting":
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

        # Log summary per round
        if _state.get(uid, {}).get("action") == "broadcasting":
            now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            log_lines = [f"Round {round_num} | {now}", f"Sent: {len(round_success)}"]
            if round_success:
                log_lines.append("Success:\n  " + "\n  ".join(round_success[:30]))
            if round_failed:
                log_lines.append(f"Failed: {len(round_failed)}\n  " + "\n  ".join(round_failed))
            # Send log via admin's Telethon account (not bot)
            log_text = "\n".join(log_lines)
            # Always send to admin via bot (private chat)
            try:
                await bot.send_message(uid, log_text)
            except Exception:
                pass
            if round_delay_max > 0:
                await asyncio.sleep(random.uniform(round_delay_min, round_delay_max))
            else:
                await asyncio.sleep(1)

    await bot.send_message(message.chat.id, t("broadcast_stopped", uid), reply_markup=_main_kb(uid))


async def _dispatch_menu(message: Message, uid: int, action: str) -> None:
    accounts = get_accounts(uid)
    # Must have at least 1 account to use anything except "add" and "lang"
    if not accounts and action not in ("add", "lang"):
        _state[uid] = {"action": "login_phone"}
        await _reply(message, uid, t("welcome_new", uid), reply_markup=_back_kb())
        return
    if action == "add":
        _state[uid] = {"action": "login_phone"}
        await _reply(message, uid, t("enter_phone", uid), reply_markup=_back_kb())
    elif action == "accounts":
        if not accounts:
            await _reply(message, uid, t("no_accounts", uid), reply_markup=_main_kb(uid))
            return
        buttons = []
        for a in accounts:
            label = a.display_name
            if a.username:
                label += f" (@{a.username})"
            buttons.append([InlineKeyboardButton(text=label, callback_data=f"acc:{a.alias}")])
        await _reply(message, uid, t("pick_account", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "health":
        if not accounts:
            await _reply(message, uid, t("no_accounts", uid), reply_markup=_main_kb(uid))
            return
        await _reply(message, uid, "Checking...")
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
        await _reply(message, uid, "\n".join(lines), reply_markup=_main_kb(uid))
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
        await _reply(message, uid, t("broadcast_pick_list", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "saved":
        saved = get_saved_messages(uid)
        buttons = [[InlineKeyboardButton(text=s["name"], callback_data=f"sv:{s['name']}")] for s in saved]
        buttons.append([InlineKeyboardButton(text="+ Save Text", callback_data="savetext")])
        await _reply(message, uid, "Text tersimpan:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "lists":
        lists = get_lists(uid)
        buttons = [[InlineKeyboardButton(text=f"{bl.name} ({len(bl.targets)})", callback_data=f"vl:{bl.name}")] for bl in lists] if lists else []
        buttons.append([InlineKeyboardButton(text="+ Create Group List", callback_data="createlist")])
        await _reply(message, uid, "Group list:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "cleanup":
        if not accounts:
            await _reply(message, uid, t("no_accounts", uid), reply_markup=_main_kb(uid))
            return
        buttons = [[InlineKeyboardButton(text=a.display_name, callback_data=f"clean:{a.alias}")] for a in accounts]
        await _reply(message, uid, t("pick_account", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "transfer":
        if not accounts:
            await _reply(message, uid, t("no_accounts", uid), reply_markup=_main_kb(uid))
            return
        _state[uid] = {"action": "transfer_target"}
        await _reply(message, uid, f"Enter user ID to transfer {len(accounts)} account(s) to:", reply_markup=_back_kb())
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

    # Check if it's a menu button press (no active state)
    if not state:
        menu_action = _get_menu_action(text)
        if menu_action:
            # Delete user's button press message
            try:
                await message.delete()
            except Exception:
                pass
            await _dispatch_menu(message, uid, menu_action)
            return
        n = len(get_accounts(uid))
        await message.answer(t("main_menu", uid, n=n), reply_markup=_main_kb(uid))
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
        _state[uid] = {"action": "savetext_body", "name": text}
        await message.answer("Kirim isi text yang mau disimpan:", reply_markup=_back_kb())

    elif action == "savetext_body":
        save_broadcast_msg(uid, state["name"], _entities_to_html(message.text or "", message.entities or []), False)
        _state.pop(uid, None)
        await message.answer(f"Text '{state['name']}' tersimpan.", reply_markup=_main_kb(uid))

    # --- Create list ---
    elif action == "createlist_name":
        _state[uid] = {"action": "createlist_targets", "name": text, "targets": []}
        await message.answer(f"Group list: {text}\n\nEnter targets one by one.\nSend 'done' when finished:")

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

    # --- List edit: add targets ---
    elif action == "listadd_targets":
        list_name = state["list"]
        bl = get_list(uid, list_name)
        if not bl:
            await message.answer("List not found.", reply_markup=_back_kb())
            _state.pop(uid, None)
            return
        new_targets = [line.strip() for line in text.split("\n") if line.strip()]
        bl.targets.extend(new_targets)
        add_list(bl)
        _state.pop(uid, None)
        await message.answer(
            f"✅ Ditambah {len(new_targets)} target ke '{list_name}' (total: {len(bl.targets)})",
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

        if "message" in st:
            src_msg = st["message"]
            has_media = bool(src_msg.photo or src_msg.video or src_msg.document or src_msg.animation)

        await message.answer(
            f"Broadcasting (continuous)\n"
            f"List: {st['list']} ({len(bl.targets)} targets)\n"
            f"Accounts: {len(accounts)}\n"
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
