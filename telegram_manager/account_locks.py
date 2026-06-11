from __future__ import annotations

import asyncio
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Optional

from .db import acquire_account_lock, cleanup_stale_account_locks, heartbeat_account_lock, release_account_lock
from .logger import get_logger

log = get_logger("account_locks")

_LOCKING_AVAILABLE: Optional[bool] = None


@dataclass
class AccountLease:
    admin_id: int
    phone: str
    holder: str
    purpose: str
    acquired: bool


async def _to_thread(func, *args):
    return await asyncio.to_thread(func, *args)


def _make_holder() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"


async def _locking_available() -> bool:
    global _LOCKING_AVAILABLE
    if _LOCKING_AVAILABLE is not None:
        return _LOCKING_AVAILABLE
    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_KEY"):
        _LOCKING_AVAILABLE = False
        return False
    try:
        await _to_thread(cleanup_stale_account_locks)
        _LOCKING_AVAILABLE = True
    except Exception:
        log.info("account locking unavailable; continuing without distributed leases", exc_info=True)
        _LOCKING_AVAILABLE = False
    return _LOCKING_AVAILABLE


class _AccountLeaseContext:
    def __init__(self, admin_id: int, phone: str, *, purpose: str, ttl_seconds: int, wait_seconds: float) -> None:
        self.admin_id = admin_id
        self.phone = phone
        self.purpose = purpose
        self.ttl_seconds = ttl_seconds
        self.wait_seconds = wait_seconds
        self.holder = _make_holder()
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._lease = AccountLease(admin_id, phone, self.holder, purpose, acquired=False)

    async def _heartbeat_loop(self) -> None:
        interval = max(5.0, min(float(self.ttl_seconds) / 3.0, 60.0))
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    await _to_thread(heartbeat_account_lock, self.admin_id, self.phone, self.holder)
                except Exception:
                    log.debug("account lease heartbeat failed for %s/%s", self.admin_id, self.phone, exc_info=True)
        except asyncio.CancelledError:
            raise

    async def __aenter__(self) -> AccountLease:
        if not await _locking_available():
            self._lease.acquired = True
            return self._lease

        deadline = asyncio.get_running_loop().time() + max(0.0, self.wait_seconds)
        while True:
            try:
                acquired = await _to_thread(
                    acquire_account_lock,
                    self.admin_id,
                    self.phone,
                    self.holder,
                    self.purpose,
                    self.ttl_seconds,
                )
            except Exception:
                log.debug("account lease acquire failed for %s/%s", self.admin_id, self.phone, exc_info=True)
                acquired = False
            if acquired:
                self._lease.acquired = True
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                return self._lease
            if self.wait_seconds <= 0 or asyncio.get_running_loop().time() >= deadline:
                return self._lease
            await asyncio.sleep(1)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        if self._lease.acquired and await _locking_available():
            try:
                await _to_thread(release_account_lock, self.admin_id, self.phone, self.holder)
            except Exception:
                log.debug("account lease release failed for %s/%s", self.admin_id, self.phone, exc_info=True)


async def cleanup_stale_leases() -> int:
    if not await _locking_available():
        return 0
    try:
        return await _to_thread(cleanup_stale_account_locks)
    except Exception:
        log.debug("stale lease cleanup failed", exc_info=True)
        return 0


async def acquire_persistent_lease(
    admin_id: int,
    phone: str,
    *,
    purpose: str,
    ttl_seconds: int = 300,
    wait_seconds: float = 0,
) -> tuple[_AccountLeaseContext, AccountLease]:
    ctx = _AccountLeaseContext(
        admin_id,
        phone,
        purpose=purpose,
        ttl_seconds=ttl_seconds,
        wait_seconds=wait_seconds,
    )
    lease = await ctx.__aenter__()
    return ctx, lease


async def release_persistent_lease(ctx: Optional[_AccountLeaseContext]) -> None:
    if ctx is None:
        return
    await ctx.__aexit__(None, None, None)


def account_lease(admin_id: int, phone: str, *, purpose: str, ttl_seconds: int = 60, wait_seconds: float = 0) -> _AccountLeaseContext:
    return _AccountLeaseContext(
        admin_id,
        phone,
        purpose=purpose,
        ttl_seconds=ttl_seconds,
        wait_seconds=wait_seconds,
    )
