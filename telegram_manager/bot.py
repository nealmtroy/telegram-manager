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
    is_managed_account,
    is_registered_admin,
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


def _log_chat_id():
    """Get log destination from env. Can be user_id (int) or @username."""
    raw = os.getenv("LOG_CHAT_ID", "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return raw  # username like @mylogchannel


def _main_kb(uid: int = 0) -> ReplyKeyboardMarkup:
    lang = get_lang(uid) if uid else "id"
    labels = _MENU_LABELS.get(lang, _MENU_LABELS["id"])
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=labels[0]), KeyboardButton(text=labels[1])],
            [KeyboardButton(text=labels[2]), KeyboardButton(text=labels[3])],
            [KeyboardButton(text=labels[4]), KeyboardButton(text=labels[5])],
            [KeyboardButton(text=labels[6]), KeyboardButton(text=labels[7])],
            [KeyboardButton(text=labels[8]), KeyboardButton(text=labels[9])],
        ],
        resize_keyboard=True,
    )


_MENU_LABELS = {
    "id": [
        "➕ Tambah Akun", "👤 Akun Saya",
        "💚 Health Check", "📣 Broadcast",
        "📋 Kelola List", "📥 Join Group",
        "✏️ Edit Profil", "🗑 Hapus/Logout",
        "🔄 Transfer", "🌐 Bahasa",
    ],
    "en": [
        "➕ Add Account", "👤 My Accounts",
        "💚 Health Check", "📣 Broadcast",
        "📋 Manage Lists", "📥 Join Group",
        "✏️ Edit Profile", "🗑 Remove/Logout",
        "🔄 Transfer", "🌐 Language",
    ],
    "ms": [
        "➕ Tambah Akaun", "👤 Akaun Saya",
        "💚 Health Check", "📣 Broadcast",
        "📋 Kelola List", "📥 Join Group",
        "✏️ Edit Profil", "🗑 Hapus/Logout",
        "🔄 Transfer", "🌐 Bahasa",
    ],
    "th": [
        "➕ เพิ่มบัญชี", "👤 บัญชีของฉัน",
        "💚 Health Check", "📣 Broadcast",
        "📋 จัดการ List", "📥 เข้าร่วมกลุ่ม",
        "✏️ แก้ไขโปรไฟล์", "🗑 ลบ/Logout",
        "🔄 โอนข้อมูล", "🌐 ภาษา",
    ],
    "vi": [
        "➕ Thêm TK", "👤 Tài khoản",
        "💚 Health Check", "📣 Broadcast",
        "📋 Quản lý List", "📥 Tham gia",
        "✏️ Sửa hồ sơ", "🗑 Xóa/Logout",
        "🔄 Chuyển", "🌐 Ngôn ngữ",
    ],
    "zh": [
        "➕ 添加账号", "👤 我的账号",
        "💚 健康检查", "📣 广播",
        "📋 管理列表", "📥 加入群组",
        "✏️ 编辑资料", "🗑 删除/登出",
        "🔄 转移", "🌐 语言",
    ],
    "ja": [
        "➕ アカウント追加", "👤 マイアカウント",
        "💚 ヘルスチェック", "📣 ブロードキャスト",
        "📋 リスト管理", "📥 グループ参加",
        "✏️ プロフィール編集", "🗑 削除/ログアウト",
        "🔄 転送", "🌐 言語",
    ],
    "ko": [
        "➕ 계정 추가", "👤 내 계정",
        "💚 상태 확인", "📣 브로드캐스트",
        "📋 목록 관리", "📥 그룹 참여",
        "✏️ 프로필 편집", "🗑 삭제/로그아웃",
        "🔄 전송", "🌐 언어",
    ],
    "hi": [
        "➕ अकाउंट जोड़ें", "👤 मेरे अकाउंट",
        "💚 Health Check", "📣 Broadcast",
        "📋 List प्रबंधन", "📥 Group जॉइन",
        "✏️ प्रोफ़ाइल एडिट", "🗑 हटाएं/Logout",
        "🔄 ट्रांसफर", "🌐 भाषा",
    ],
    "fil": [
        "➕ Dagdag Account", "👤 Mga Account",
        "💚 Health Check", "📣 Broadcast",
        "📋 Manage Lists", "📥 Join Group",
        "✏️ Edit Profile", "🗑 Remove/Logout",
        "🔄 Transfer", "🌐 Wika",
    ],
}


def _get_menu_action(text: str) -> str | None:
    """Map any language button text to action key."""
    for labels in _MENU_LABELS.values():
        if text in labels:
            idx = labels.index(text)
            return ["add", "accounts", "health", "broadcast",
                    "lists", "join", "edit", "cleanup", "transfer", "lang"][idx]
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
        await message.answer(t("welcome_new", uid), reply_markup=_back_kb())
        return
    await message.answer(t("main_menu", uid, n=len(accounts)), reply_markup=_main_kb())


# ---------------------------------------------------------------------------
# Main menu button handlers
# ---------------------------------------------------------------------------
@router.message(F.text.in_({"<< Menu", "<< menu"}))
async def btn_menu(message: Message) -> None:
    uid = message.from_user.id
    _state.pop(uid, None)
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
    _state[uid]["action"] = "broadcast_delay_type"
    buttons = [[InlineKeyboardButton(text="Per group", callback_data="dt:per_group"),
                InlineKeyboardButton(text="Per round", callback_data="dt:per_round")]]
    await cq.message.edit_text(t("delay_mode", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("dt:"))
async def cb_dt(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    _state[uid]["delay_type"] = cq.data[3:]
    _state[uid]["action"] = "broadcast_delay_value"
    buttons = [[InlineKeyboardButton(text="Auto (3-10s)", callback_data="dv:auto"),
                InlineKeyboardButton(text="No delay", callback_data="dv:none")]]
    await cq.message.edit_text(t("delay_value", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("dv:"))
async def cb_dv(cq: CallbackQuery) -> None:
    await cq.answer()
    uid = cq.from_user.id
    if cq.data[3:] == "auto":
        _state[uid]["delay"] = (3.0, 10.0)
    else:
        _state[uid]["delay"] = (0.0, 0.0)
    _state[uid]["action"] = "broadcasting"
    await cq.message.edit_text(t("broadcast_running", uid))
    await _start_broadcast(cq.message, uid)


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
    buttons = [[InlineKeyboardButton(text="Delete", callback_data=f"dl:{bl.name}")]]
    await cq.message.edit_text(f"{bl.name}:\n{targets}", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.callback_query(F.data.startswith("dl:"))
async def cb_dl(cq: CallbackQuery) -> None:
    await cq.answer()
    remove_list(cq.from_user.id, cq.data[3:])
    await cq.message.edit_text("Deleted.")


@router.callback_query(F.data.startswith("join:"))
async def cb_join(cq: CallbackQuery) -> None:
    await cq.answer()
    _state[cq.from_user.id] = {"action": "join_target", "alias": cq.data[5:]}
    await cq.message.edit_text("Enter group/channel username or invite link:")


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
    await cq.message.edit_text(
        f"[{acc.alias}]\nPhone: {acc.phone}\nName: {acc.first_name} {acc.last_name}\n"
        f"Username: @{acc.username or '-'}\n2FA: {'yes' if acc.is_2fa else 'no'}")


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
    _state[uid]["action"] = "broadcast_delay_type"
    buttons = [
        [KeyboardButton(text="Per group"), KeyboardButton(text="Per round")],
        [KeyboardButton(text="<< Menu")],
    ]
    await message.answer(
        "Media received.\n\nDelay mode:\n"
        "• Per group — delay between each group\n"
        "• Per round — delay after all groups done",
        reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True),
    )


async def _start_broadcast(message: Message, uid: int) -> None:
    """Start the continuous broadcast loop."""
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import ImportChatInviteRequest
    from telethon.errors import ChatWriteForbiddenError, SlowModeWaitError, UserBannedInChannelError
    from datetime import datetime, timezone

    st = _state.get(uid, {})
    bl = get_list(uid, st.get("list", ""))
    accounts = get_accounts(uid)
    if not bl or not accounts:
        _state.pop(uid, None)
        return

    delay_min, delay_max = st.get("delay", (3.0, 10.0))
    delay_type = st.get("delay_type", "per_group")

    watermark = os.getenv("WATERMARK", "")
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
    log_dest = _log_chat_id() or message.chat.id
    round_num = 0

    while _state.get(uid, {}).get("action") == "broadcasting":
        round_num += 1
        round_success = []
        round_failed = []

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
                    try:
                        if "t.me/+" in target or "joinchat/" in target:
                            await client(ImportChatInviteRequest(target.split("+")[-1].split("joinchat/")[-1]))
                        else:
                            await client(JoinChannelRequest(target.lstrip("@").replace("https://t.me/", "")))
                    except Exception:
                        pass
                    try:
                        e = target.lstrip("@").replace("https://t.me/", "").split("+")[0]
                        if has_media and media_bytes:
                            await client.send_file(e, media_bytes, caption=msg_text, parse_mode="html", file_name=media_filename)
                        else:
                            await client.send_message(e, msg_text, parse_mode="html")
                        round_success.append(f"{acc.alias} -> {target}")
                    except (ChatWriteForbiddenError, UserBannedInChannelError):
                        round_failed.append(f"{acc.alias} -> {target}: Blocked")
                    except SlowModeWaitError as sme:
                        round_failed.append(f"{acc.alias} -> {target}: SlowMode {sme.seconds}s")
                    except FloodWaitError as fw:
                        round_failed.append(f"{acc.alias} -> {target}: Flood {fw.seconds}s")
                        await asyncio.sleep(fw.seconds)
                    except Exception as ex:
                        round_failed.append(f"{acc.alias} -> {target}: {type(ex).__name__}")
                    if delay_type == "per_group" and delay_max > 0:
                        await asyncio.sleep(random.uniform(delay_min, delay_max))
            except Exception as ex:
                round_failed.append(f"{acc.alias}: {type(ex).__name__}")
            finally:
                if client.is_connected():
                    await client.disconnect()

        # Log summary per round
        if _state.get(uid, {}).get("action") == "broadcasting":
            now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            log_lines = [f"Round {round_num} | {now}", f"Sent: {len(round_success)}"]
            if round_success:
                log_lines.append("Success:\n  " + "\n  ".join(round_success[:30]))
            if round_failed:
                log_lines.append(f"Failed: {len(round_failed)}\n  " + "\n  ".join(round_failed))
            await bot.send_message(log_dest, "\n".join(log_lines))

            if delay_type == "per_round" and delay_max > 0:
                await asyncio.sleep(random.uniform(delay_min, delay_max))
            else:
                await asyncio.sleep(1)

    await bot.send_message(message.chat.id, t("broadcast_stopped", uid), reply_markup=_main_kb(uid))


async def _dispatch_menu(message: Message, uid: int, action: str) -> None:
    accounts = get_accounts(uid)
    # Must have at least 1 account to use anything except "add" and "lang"
    if not accounts and action not in ("add", "lang"):
        _state[uid] = {"action": "login_phone"}
        await message.answer(t("welcome_new", uid), reply_markup=_back_kb())
        return
    if action == "add":
        _state[uid] = {"action": "login_phone"}
        await message.answer(t("enter_phone", uid), reply_markup=_back_kb())
    elif action == "accounts":
        if not accounts:
            await message.answer(t("no_accounts", uid), reply_markup=_main_kb(uid))
            return
        buttons = [[InlineKeyboardButton(text=f"{a.alias} ({a.phone})", callback_data=f"acc:{a.alias}")] for a in accounts]
        await message.answer(t("pick_account", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "health":
        if not accounts:
            await message.answer(t("no_accounts", uid), reply_markup=_main_kb(uid))
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
        await message.answer("\n".join(lines), reply_markup=_main_kb(uid))
    elif action == "broadcast":
        lists = get_lists(uid)
        if not lists:
            await message.answer(t("no_accounts", uid), reply_markup=_main_kb(uid))
            return
        buttons = [[InlineKeyboardButton(text=f"{bl.name} ({len(bl.targets)})", callback_data=f"bc:{bl.name}")] for bl in lists]
        await message.answer(t("broadcast_pick_list", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "lists":
        lists = get_lists(uid)
        buttons = [[InlineKeyboardButton(text=f"{bl.name} ({len(bl.targets)})", callback_data=f"vl:{bl.name}")] for bl in lists] if lists else []
        buttons.append([InlineKeyboardButton(text="+ Create List", callback_data="createlist")])
        await message.answer("Lists:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "join":
        if not accounts:
            await message.answer(t("no_accounts", uid), reply_markup=_main_kb(uid))
            return
        buttons = [[InlineKeyboardButton(text=a.alias, callback_data=f"join:{a.alias}")] for a in accounts]
        await message.answer(t("pick_account", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "edit":
        if not accounts:
            await message.answer(t("no_accounts", uid), reply_markup=_main_kb(uid))
            return
        buttons = [[InlineKeyboardButton(text=a.alias, callback_data=f"edit:{a.alias}")] for a in accounts]
        await message.answer(t("pick_account", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "cleanup":
        if not accounts:
            await message.answer(t("no_accounts", uid), reply_markup=_main_kb(uid))
            return
        buttons = [[InlineKeyboardButton(text=a.alias, callback_data=f"clean:{a.alias}")] for a in accounts]
        await message.answer(t("pick_account", uid), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    elif action == "transfer":
        if not accounts:
            await message.answer(t("no_accounts", uid), reply_markup=_main_kb(uid))
            return
        _state[uid] = {"action": "transfer_target"}
        await message.answer(f"Enter user ID to transfer {len(accounts)} account(s) to:", reply_markup=_back_kb())
    elif action == "lang":
        buttons, row = [], []
        for code, name in LANGUAGES.items():
            row.append(InlineKeyboardButton(text=name, callback_data=f"lang:{code}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        await message.answer("Pilih bahasa yang kamu inginkan:", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


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
        await message.answer(
            f"Code sent to {text}\nDevice: {preset.device_model}\n\n"
            "⚠️ PENTING: Ketik kode PAKAI SPASI\n"
            "Contoh: 3 6 8 1 5\n\n"
            "Jangan ketik tanpa spasi, Telegram akan otomatis membatalkan kode!"
        )

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
            _state[uid]["action"] = "broadcast_delay_type"
            buttons = [
                [KeyboardButton(text="Per group"), KeyboardButton(text="Per round")],
                [KeyboardButton(text="<< Menu")],
            ]
            await message.answer(
                "Delay mode:\n• Per group\n• Per round",
                reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True),
            )

    elif action == "broadcast_save_name":
        src = _state[uid]["message"]
        raw_text = src.text or src.caption or ""
        entities = src.entities or src.caption_entities or []
        html_text = _entities_to_html(raw_text, entities)
        has_media = bool(src.photo or src.video or src.document or src.animation)
        save_broadcast_msg(uid, text, html_text, has_media)
        _state[uid]["action"] = "broadcast_delay_type"
        buttons = [
            [KeyboardButton(text="Per group"), KeyboardButton(text="Per round")],
            [KeyboardButton(text="<< Menu")],
        ]
        await message.answer(
            f"Saved as '{text}'.\n\nDelay mode:\n• Per group\n• Per round",
            reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True),
        )
        await message.answer(
            "Delay mode:\n"
            "• Per group — delay between each group\n"
            "• Per round — delay after all groups done",
            reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True),
        )

    elif action == "broadcast_delay_type":
        if text not in ("Per group", "Per round"):
            await message.answer("Pick 'Per group' or 'Per round'.")
            return
        _state[uid]["delay_type"] = text.lower().replace(" ", "_")
        _state[uid]["action"] = "broadcast_delay_value"
        buttons = [
            [KeyboardButton(text="Auto (3-10s)"), KeyboardButton(text="No delay")],
            [KeyboardButton(text="<< Menu")],
        ]
        await message.answer(
            "Delay duration?\nPick or type custom (e.g. '5' or '3-8'):",
            reply_markup=ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True),
        )

    elif action == "broadcast_delay_value":
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

        # Build message content preserving formatting + media
        watermark = os.getenv("WATERMARK", "")
        media_bytes = None
        media_filename = None
        has_media = False

        if "saved_text" in st:
            # Using saved message (text only)
            msg_text = st["saved_text"]
        else:
            # Using fresh message with possible media
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

        if watermark:
            msg_text = (msg_text + f"\n\n{watermark}") if msg_text else watermark

        delay_type = st["delay_type"]
        await message.answer(
            f"Broadcasting (continuous)\n"
            f"List: {st['list']} ({len(bl.targets)} targets)\n"
            f"Accounts: {len(accounts)}\n"
            f"Delay: {delay_type.replace('_',' ')} | {delay_min}-{delay_max}s\n"
            f"Media: {'yes' if has_media else 'text only'}\n"
            f"Watermark: {watermark or '(none)'}\n\n"
            f"Running... send 'stop' to stop",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[[KeyboardButton(text="stop")]],
                resize_keyboard=True,
            ),
        )

        _state[uid] = {"action": "broadcasting"}

        from telethon.tl.functions.channels import JoinChannelRequest
        from telethon.tl.functions.messages import ImportChatInviteRequest
        from telethon.errors import ChatWriteForbiddenError, SlowModeWaitError, UserBannedInChannelError
        from datetime import datetime, timezone

        bot = message.bot
        log_dest = _log_chat_id() or message.chat.id
        round_num = 0

        while _state.get(uid, {}).get("action") == "broadcasting":
            round_num += 1
            round_success = []
            round_failed = []

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
                        try:
                            if "t.me/+" in target or "joinchat/" in target:
                                await client(ImportChatInviteRequest(target.split("+")[-1].split("joinchat/")[-1]))
                            else:
                                await client(JoinChannelRequest(target.lstrip("@").replace("https://t.me/", "")))
                        except Exception:
                            pass
                        try:
                            e = target.lstrip("@").replace("https://t.me/", "").split("+")[0]
                            if has_media and media_bytes:
                                await client.send_file(
                                    e, media_bytes,
                                    caption=msg_text,
                                    parse_mode="html",
                                    file_name=media_filename,
                                )
                            else:
                                await client.send_message(e, msg_text, parse_mode="html")
                            round_success.append(f"{acc.alias} -> {target}")
                        except (ChatWriteForbiddenError, UserBannedInChannelError) as ex:
                            round_failed.append(f"{acc.alias} -> {target}: Blocked/Banned")
                        except SlowModeWaitError as sme:
                            round_failed.append(f"{acc.alias} -> {target}: SlowMode {sme.seconds}s")
                        except FloodWaitError as fw:
                            round_failed.append(f"{acc.alias} -> {target}: FloodWait {fw.seconds}s")
                            await asyncio.sleep(fw.seconds)
                        except Exception as ex:
                            round_failed.append(f"{acc.alias} -> {target}: {type(ex).__name__}")
                        if delay_type == "per_group" and delay_max > 0:
                            await asyncio.sleep(random.uniform(delay_min, delay_max))
                except Exception as ex:
                    round_failed.append(f"{acc.alias}: {type(ex).__name__}")
                finally:
                    if client.is_connected():
                        await client.disconnect()

            # Send round summary log
            if _state.get(uid, {}).get("action") == "broadcasting":
                now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
                log_lines = [f"Round {round_num} | {now}"]
                log_lines.append(f"Sent: {len(round_success)}")
                if round_success:
                    log_lines.append("Success:\n  " + "\n  ".join(round_success[:30]))
                if round_failed:
                    log_lines.append(f"Failed: {len(round_failed)}")
                    log_lines.append("Errors:\n  " + "\n  ".join(round_failed))
                await bot.send_message(log_dest, "\n".join(log_lines))

                if delay_type == "per_round" and delay_max > 0:
                    await asyncio.sleep(random.uniform(delay_min, delay_max))
                else:
                    await asyncio.sleep(1)

        await bot.send_message(message.chat.id, "Broadcast stopped.", reply_markup=_main_kb())

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
