"""Supabase storage layer for Telegram Manager.

Replaces JSON file storage with Supabase PostgreSQL.
Uses StringSession from Telethon so no filesystem needed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
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
    db.table("admins").upsert({
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
    }).execute()


def is_registered_admin(user_id: int) -> bool:
    db = _get_client()
    r = db.table("admins").select("user_id").eq("user_id", user_id).execute()
    return len(r.data) > 0


def is_managed_account(user_id: int) -> bool:
    """Check if user_id is already a managed account under any admin."""
    db = _get_client()
    r = db.table("accounts").select("user_id").eq("user_id", user_id).execute()
    return len(r.data) > 0


# ---------------------------------------------------------------------------
# Account operations
# ---------------------------------------------------------------------------
def add_account(acc: AccountRow) -> None:
    db = _get_client()
    db.table("accounts").upsert({
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
    }, on_conflict="admin_id,phone").execute()


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
    ) for row in r.data]


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
