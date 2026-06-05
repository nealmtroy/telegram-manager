"""Supabase storage layer for Telegram Manager.

Replaces JSON file storage with Supabase PostgreSQL.
Uses StringSession from Telethon so no filesystem needed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from supabase import create_client, Client

from .logger import get_logger

log = get_logger("db")


def _get_client() -> Client:
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
    return create_client(url, key)


_ACCOUNT_OPTIONAL_COLUMNS: Optional[set[str]] = None


def _account_optional_columns() -> set[str]:
    global _ACCOUNT_OPTIONAL_COLUMNS
    if _ACCOUNT_OPTIONAL_COLUMNS is not None:
        return _ACCOUNT_OPTIONAL_COLUMNS
    db = _get_client()
    columns: set[str] = set()
    try:
        db.table("accounts").select("api_credential_index").limit(1).execute()
        columns.add("api_credential_index")
    except Exception:
        pass
    try:
        db.table("accounts").select("proxy_index").limit(1).execute()
        columns.add("proxy_index")
    except Exception:
        pass
    try:
        db.table("accounts").select("auto_reply_enabled").limit(1).execute()
        columns.add("auto_reply_enabled")
    except Exception:
        pass
    try:
        db.table("accounts").select("auto_reply_text").limit(1).execute()
        columns.add("auto_reply_text")
    except Exception:
        pass
    _ACCOUNT_OPTIONAL_COLUMNS = columns
    return _ACCOUNT_OPTIONAL_COLUMNS


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
@dataclass
class AccountRow:
    admin_id: int
    phone: str
    alias: str
    session_string: str
    first_name: str = ""
    last_name: str = ""
    username: Optional[str] = None
    user_id: Optional[int] = None
    is_2fa: bool = False
    device_preset: str = "random"
    api_credential_index: Optional[int] = None
    proxy_index: Optional[int] = None
    auto_reply_enabled: bool = False
    auto_reply_text: str = ""

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or self.alias


@dataclass
class BroadcastListRow:
    admin_id: int
    name: str
    targets: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Admin operations
# ---------------------------------------------------------------------------
def register_admin(user_id: int, username: str = "", first_name: str = "") -> None:
    db = _get_client()
    existing = db.table("admins").select("user_id").eq("user_id", user_id).execute()
    payload = {
        "username": username,
        "first_name": first_name,
    }
    if existing.data:
        db.table("admins").update(payload).eq("user_id", user_id).execute()
        return
    db.table("admins").insert({"user_id": user_id, "lang": "id", **payload}).execute()


def is_registered_admin(user_id: int) -> bool:
    db = _get_client()
    r = db.table("admins").select("user_id").eq("user_id", user_id).execute()
    return len(r.data) > 0


def get_admin_ids() -> List[int]:
    db = _get_client()
    r = db.table("admins").select("user_id").execute()
    return [int(row["user_id"]) for row in r.data if row.get("user_id") is not None]


def is_vip_admin(user_id: int) -> bool:
    db = _get_client()
    r = db.table("admins").select("is_vip").eq("user_id", user_id).execute()
    return bool(r.data and r.data[0].get("is_vip"))


def grant_vip(user_id: int, gifted_by: int) -> bool:
    db = _get_client()
    db.table("admins").upsert({
        "user_id": user_id,
        "is_vip": True,
        "vip_gifted_by": gifted_by,
        "vip_gifted_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="user_id").execute()
    return is_vip_admin(user_id)


def is_managed_account(user_id: int) -> bool:
    """Check if user_id is a managed account under a DIFFERENT admin."""
    db = _get_client()
    r = db.table("accounts").select("admin_id").eq("user_id", user_id).neq("admin_id", user_id).execute()
    return len(r.data) > 0


# ---------------------------------------------------------------------------
# Account operations
# ---------------------------------------------------------------------------
def add_account(acc: AccountRow) -> None:
    db = _get_client()
    payload = {
        "admin_id": acc.admin_id,
        "phone": acc.phone,
        "alias": acc.alias,
        "first_name": acc.first_name,
        "last_name": acc.last_name,
        "username": acc.username,
        "user_id": acc.user_id,
        "is_2fa": acc.is_2fa,
        "device_preset": acc.device_preset,
        "session_string": acc.session_string,
    }
    optional_columns = _account_optional_columns()
    if "api_credential_index" in optional_columns:
        payload["api_credential_index"] = acc.api_credential_index
    if "proxy_index" in optional_columns:
        payload["proxy_index"] = acc.proxy_index
    if "auto_reply_enabled" in optional_columns:
        payload["auto_reply_enabled"] = acc.auto_reply_enabled
    if "auto_reply_text" in optional_columns:
        payload["auto_reply_text"] = acc.auto_reply_text
    db.table("accounts").upsert(payload, on_conflict="admin_id,phone").execute()


def get_account_count() -> int:
    db = _get_client()
    r = db.table("accounts").select("phone", count="exact").limit(1).execute()
    return r.count or 0


def get_accounts(admin_id: int) -> List[AccountRow]:
    db = _get_client()
    r = db.table("accounts").select("*").eq("admin_id", admin_id).order("alias").execute()
    return [AccountRow(
        admin_id=row["admin_id"],
        phone=row["phone"],
        alias=row["alias"],
        session_string=row["session_string"],
        first_name=row.get("first_name", ""),
        last_name=row.get("last_name", ""),
        username=row.get("username"),
        user_id=row.get("user_id"),
        is_2fa=row.get("is_2fa", False),
        device_preset=row.get("device_preset", "random"),
        api_credential_index=row.get("api_credential_index"),
        proxy_index=row.get("proxy_index"),
        auto_reply_enabled=row.get("auto_reply_enabled", False),
        auto_reply_text=row.get("auto_reply_text", "") or "",
    ) for row in r.data]


def get_all_accounts() -> List[AccountRow]:
    db = _get_client()
    r = db.table("accounts").select("*").order("alias").execute()
    return [AccountRow(
        admin_id=row["admin_id"],
        phone=row["phone"],
        alias=row["alias"],
        session_string=row["session_string"],
        first_name=row.get("first_name", ""),
        last_name=row.get("last_name", ""),
        username=row.get("username"),
        user_id=row.get("user_id"),
        is_2fa=row.get("is_2fa", False),
        device_preset=row.get("device_preset", "random"),
        api_credential_index=row.get("api_credential_index"),
        proxy_index=row.get("proxy_index"),
        auto_reply_enabled=row.get("auto_reply_enabled", False),
        auto_reply_text=row.get("auto_reply_text", "") or "",
    ) for row in r.data]


def get_all_admins() -> dict[int, dict[str, str]]:
    db = _get_client()
    r = db.table("admins").select("user_id, username, first_name").execute()
    return {
        row["user_id"]: {
            "username": row.get("username", "") or "",
            "first_name": row.get("first_name", "") or "",
        }
        for row in r.data
        if row.get("user_id") is not None
    }


def find_account(admin_id: int, alias_or_phone: str) -> Optional[AccountRow]:
    accounts = get_accounts(admin_id)
    for a in accounts:
        if a.alias == alias_or_phone or a.phone == alias_or_phone:
            return a
    return None


def remove_account(admin_id: int, alias_or_phone: str) -> Optional[AccountRow]:
    acc = find_account(admin_id, alias_or_phone)
    if not acc:
        return None
    db = _get_client()
    db.table("accounts").delete().eq("admin_id", admin_id).eq("phone", acc.phone).execute()
    return acc


def update_session(admin_id: int, phone: str, session_string: str) -> None:
    db = _get_client()
    db.table("accounts").update({
        "session_string": session_string,
    }).eq("admin_id", admin_id).eq("phone", phone).execute()


def update_auto_reply(admin_id: int, phone: str, enabled: bool, text: str = "") -> None:
    optional_columns = _account_optional_columns()
    if not {"auto_reply_enabled", "auto_reply_text"}.issubset(optional_columns):
        raise RuntimeError("accounts.auto_reply_enabled and accounts.auto_reply_text columns are required")
    db = _get_client()
    db.table("accounts").update({
        "auto_reply_enabled": enabled,
        "auto_reply_text": text if enabled else "",
    }).eq("admin_id", admin_id).eq("phone", phone).execute()


# ---------------------------------------------------------------------------
# Broadcast list operations
# ---------------------------------------------------------------------------
def get_lists(admin_id: int) -> List[BroadcastListRow]:
    db = _get_client()
    r = db.table("broadcast_lists").select("*").eq("admin_id", admin_id).order("name").execute()
    return [BroadcastListRow(
        admin_id=row["admin_id"],
        name=row["name"],
        targets=row.get("targets", []),
    ) for row in r.data]


def get_list(admin_id: int, name: str) -> Optional[BroadcastListRow]:
    db = _get_client()
    r = db.table("broadcast_lists").select("*").eq("admin_id", admin_id).eq("name", name).execute()
    if not r.data:
        return None
    row = r.data[0]
    return BroadcastListRow(admin_id=row["admin_id"], name=row["name"], targets=row.get("targets", []))


def add_list(bl: BroadcastListRow) -> None:
    db = _get_client()
    db.table("broadcast_lists").upsert({
        "admin_id": bl.admin_id,
        "name": bl.name,
        "targets": bl.targets,
    }, on_conflict="admin_id,name").execute()


def remove_list(admin_id: int, name: str) -> None:
    db = _get_client()
    db.table("broadcast_lists").delete().eq("admin_id", admin_id).eq("name", name).execute()



def transfer_all(from_admin_id: int, to_admin_id: int) -> int:
    """Transfer all accounts and lists from one admin to another. Returns count."""
    db = _get_client()
    # Transfer accounts
    r = db.table("accounts").update({"admin_id": to_admin_id}).eq("admin_id", from_admin_id).execute()
    acc_count = len(r.data)
    # Transfer lists
    r2 = db.table("broadcast_lists").update({"admin_id": to_admin_id}).eq("admin_id", from_admin_id).execute()
    return acc_count + len(r2.data)


def resolve_admin_id(identifier: str) -> Optional[int]:
    db = _get_client()
    text = identifier.strip()
    if not text:
        return None

    if text.lstrip("-").isdigit():
        user_id = int(text)
        if is_registered_admin(user_id):
            return user_id
        r = db.table("accounts").select("admin_id").eq("user_id", user_id).limit(1).execute()
        if r.data:
            return int(r.data[0]["admin_id"])
        return None

    username = text.lstrip("@").lower()
    r = db.table("admins").select("user_id").ilike("username", username).limit(1).execute()
    if r.data:
        return int(r.data[0]["user_id"])

    r = db.table("accounts").select("admin_id").eq("phone", text).limit(1).execute()
    if r.data:
        return int(r.data[0]["admin_id"])

    r = db.table("accounts").select("admin_id").ilike("username", username).limit(1).execute()
    if r.data:
        return int(r.data[0]["admin_id"])
    return None



# ---------------------------------------------------------------------------
# Saved broadcast messages
# ---------------------------------------------------------------------------
def save_broadcast_msg(admin_id: int, name: str, text: str, has_media: bool = False) -> None:
    db = _get_client()
    db.table("saved_messages").upsert({
        "admin_id": admin_id,
        "name": name,
        "text": text,
        "has_media": has_media,
    }, on_conflict="admin_id,name").execute()


def get_saved_messages(admin_id: int) -> list:
    db = _get_client()
    r = db.table("saved_messages").select("*").eq("admin_id", admin_id).order("name").execute()
    return r.data


def delete_saved_msg(admin_id: int, name: str) -> None:
    db = _get_client()
    db.table("saved_messages").delete().eq("admin_id", admin_id).eq("name", name).execute()



def set_admin_lang(user_id: int, lang: str) -> None:
    db = _get_client()
    db.table("admins").update({"lang": lang}).eq("user_id", user_id).execute()


def get_admin_lang(user_id: int) -> str:
    db = _get_client()
    r = db.table("admins").select("lang").eq("user_id", user_id).execute()
    if r.data and r.data[0].get("lang"):
        return r.data[0]["lang"]
    return "id"
