"""Multi-account orchestration.

:class:`TelegramManager` is the high-level object the CLI talks to. It:

* holds the :class:`AccountStore` and an :class:`AuthFlow`;
* opens / closes Telethon clients for one or many accounts at once;
* exposes small actions (get_me, send_message, health_check) that can target
  a single account or be broadcast across *all* accounts.

Design note: we intentionally do NOT keep clients connected in the background.
Each high-level action opens a client, runs, and disconnects, because this
tool is interactive CLI, not a long-running daemon. If you later build a
daemon, keep clients hot in :attr:`_clients` and reuse them.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from telethon import TelegramClient
from telethon.errors import (
    AuthKeyUnregisteredError,
    FloodWaitError,
    UserDeactivatedBanError,
)

from .auth import AuthFlow
from .config import Config
from .device_presets import get_preset
from .exceptions import (
    AccountNotFoundError,
    ConnectionError,
    FloodError,
    NotAuthorizedError,
    TelegramManagerError,
)
from .logger import get_logger
from .storage import Account, AccountStore

log = get_logger("manager")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------
@dataclass
class AccountStatus:
    """Snapshot of a single account's connectivity/authorization state."""

    account: Account
    authorized: bool
    error: Optional[str] = None  # human-readable if authorized=False

    @property
    def ok(self) -> bool:
        return self.authorized and self.error is None


@dataclass
class BroadcastResult:
    """Per-account outcome of a broadcast/multi action."""

    account: Account
    success: bool
    value: Any = None  # on success: whatever the action returned
    error: Optional[str] = None  # on failure: human-readable message


# A user-supplied action receives a *connected + authorized* client and the
# account record; returns whatever it wants (stored in BroadcastResult.value).
AccountAction = Callable[[TelegramClient, Account], Awaitable[Any]]


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class TelegramManager:
    """High-level facade over storage + auth + runtime actions."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = AccountStore(config.accounts_file, config.sessions_dir)
        self.auth = AuthFlow(config, self.store)
        self.store.load()

    # -- account registry passthroughs ------------------------------------
    def list_accounts(self) -> List[Account]:
        return self.store.all()

    def get_account(self, phone_or_alias: str) -> Account:
        return self.store.get(phone_or_alias)

    # -- health check -----------------------------------------------------
    async def health_check(
        self, accounts: Optional[Sequence[Account]] = None
    ) -> List[AccountStatus]:
        """Probe every account (or a given subset) concurrently."""
        targets = list(accounts) if accounts is not None else self.list_accounts()
        if not targets:
            return []

        async def check(acc: Account) -> AccountStatus:
            try:
                ok = await self.auth.probe(acc)
                if not ok:
                    return AccountStatus(
                        account=acc,
                        authorized=False,
                        error="Session is no longer authorized (re-login required).",
                    )
                return AccountStatus(account=acc, authorized=True)
            except ConnectionError as exc:
                return AccountStatus(account=acc, authorized=False, error=str(exc))
            except Exception as exc:  # noqa: BLE001 - surface as string
                log.exception("Unexpected error probing %s", acc.alias)
                return AccountStatus(
                    account=acc, authorized=False, error=f"{type(exc).__name__}: {exc}"
                )

        return await asyncio.gather(*(check(a) for a in targets))

    # -- single-account actions -------------------------------------------
    async def run_on(
        self,
        phone_or_alias: str,
        action: AccountAction,
    ) -> Any:
        """Open a client for one account, run ``action``, then disconnect.

        Raises the underlying exception if the action fails.
        """
        acc = self.get_account(phone_or_alias)
        client = self._build_client(acc)
        try:
            await self._connect_and_authorize(client, acc)
            return await action(client, acc)
        finally:
            if client.is_connected():
                await client.disconnect()

    # -- multi-account / broadcast actions --------------------------------
    async def run_on_all(
        self,
        action: AccountAction,
        *,
        accounts: Optional[Sequence[Account]] = None,
        concurrency: int = 4,
        stop_on_error: bool = False,
    ) -> List[BroadcastResult]:
        """Run ``action`` across many accounts concurrently.

        Args:
            action: Async function receiving (client, account).
            accounts: Subset to run on. Defaults to all registered accounts.
            concurrency: Max parallel clients (Telegram rate-limits per IP).
            stop_on_error: If True, cancel pending tasks on the first failure.
        """
        targets = list(accounts) if accounts is not None else self.list_accounts()
        if not targets:
            return []

        sem = asyncio.Semaphore(max(1, concurrency))
        stop_event = asyncio.Event()

        async def run_one(acc: Account) -> BroadcastResult:
            if stop_event.is_set():
                return BroadcastResult(
                    account=acc, success=False, error="Skipped (stopped on earlier error)."
                )
            async with sem:
                client = self._build_client(acc)
                try:
                    await self._connect_and_authorize(client, acc)
                    value = await action(client, acc)
                    return BroadcastResult(account=acc, success=True, value=value)
                except FloodWaitError as exc:
                    msg = f"Flood wait: retry in {exc.seconds}s"
                    log.warning("[%s] %s", acc.alias, msg)
                    if stop_on_error:
                        stop_event.set()
                    return BroadcastResult(account=acc, success=False, error=msg)
                except (NotAuthorizedError, AuthKeyUnregisteredError) as exc:
                    msg = f"Not authorized: {exc}"
                    log.warning("[%s] %s", acc.alias, msg)
                    if stop_on_error:
                        stop_event.set()
                    return BroadcastResult(account=acc, success=False, error=msg)
                except UserDeactivatedBanError as exc:
                    msg = f"Account banned/deactivated: {exc}"
                    log.error("[%s] %s", acc.alias, msg)
                    if stop_on_error:
                        stop_event.set()
                    return BroadcastResult(account=acc, success=False, error=msg)
                except TelegramManagerError as exc:
                    if stop_on_error:
                        stop_event.set()
                    return BroadcastResult(account=acc, success=False, error=str(exc))
                except Exception as exc:  # noqa: BLE001 - surface as string
                    log.exception("[%s] action failed", acc.alias)
                    if stop_on_error:
                        stop_event.set()
                    return BroadcastResult(
                        account=acc,
                        success=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                finally:
                    if client.is_connected():
                        await client.disconnect()

        return await asyncio.gather(*(run_one(a) for a in targets))

    # -- convenience wrappers --------------------------------------------
    async def get_me_all(self) -> List[BroadcastResult]:
        """Fetch ``get_me()`` for every account - useful for 'am I still logged in?'."""
        async def _get_me(client: TelegramClient, _acc: Account) -> Dict[str, Any]:
            me = await client.get_me()
            return {
                "id": getattr(me, "id", None),
                "first_name": getattr(me, "first_name", ""),
                "last_name": getattr(me, "last_name", ""),
                "username": getattr(me, "username", None),
                "phone": getattr(me, "phone", None),
            }

        return await self.run_on_all(_get_me)

    async def send_message(
        self,
        target: str,
        text: str,
        *,
        accounts: Optional[Sequence[Account]] = None,
    ) -> List[BroadcastResult]:
        """Send ``text`` to ``target`` (username / user_id / phone / @chat) from each account."""
        async def _send(client: TelegramClient, acc: Account) -> int:
            msg = await client.send_message(target, text)
            log.info("[%s] message sent -> id=%s", acc.alias, getattr(msg, "id", "?"))
            return getattr(msg, "id", -1)

        return await self.run_on_all(_send, accounts=accounts)

    # -- internals --------------------------------------------------------
    def _build_client(self, account: Account) -> TelegramClient:
        """Build a client whose params match the account's device preset."""
        preset = get_preset(account.device_preset)
        session_path = str(self.store.session_path(account.session_name))
        proxy = self.config.proxy.to_telethon()
        if not self.config.has_own_api:
            raise TelegramManagerError(
                f"[{account.alias}] TELEGRAM_API_ID/TELEGRAM_API_HASH "
                "aren't set in .env."
            )
        return TelegramClient(
            session_path,
            self.config.api_id,
            self.config.api_hash,
            proxy=proxy,
            device_model=preset.device_model,
            system_version=preset.system_version,
            app_version=preset.app_version,
            lang_code=preset.lang_code,
            system_lang_code=preset.system_lang_code,
        )

    async def _connect_and_authorize(
        self, client: TelegramClient, account: Account
    ) -> None:
        try:
            await client.connect()
        except OSError as exc:
            raise ConnectionError(
                f"[{account.alias}] could not connect: {exc}"
            ) from exc
        try:
            if not await client.is_user_authorized():
                raise NotAuthorizedError(
                    f"[{account.alias}] session is not authorized; re-login required."
                )
        except AuthKeyUnregisteredError as exc:
            raise NotAuthorizedError(
                f"[{account.alias}] session was revoked; re-login required."
            ) from exc
        except FloodWaitError as exc:
            raise FloodError(seconds=exc.seconds) from exc

    # -- lookup helpers ---------------------------------------------------
    def require_account(self, phone_or_alias: str) -> Account:
        acc = self.store.find(phone_or_alias)
        if acc is None:
            raise AccountNotFoundError(f"No account matches '{phone_or_alias}'.")
        return acc
