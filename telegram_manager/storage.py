"""Storage layer for account metadata.

The actual Telegram session is stored by Telethon as ``<phone>.session`` in
``sessions/``. Here we keep a small JSON registry (``accounts.json``) that
tracks the human-friendly metadata: alias, phone, first/last name, whether
2FA is known to be enabled, creation timestamp.

This JSON is rewritten atomically (write-to-temp + rename) so a crash mid-
write can't corrupt it.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .exceptions import (
    AccountAlreadyExistsError,
    AccountNotFoundError,
    StorageError,
)
from .logger import get_logger

log = get_logger("storage")

_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class Account:
    """Metadata about a single Telegram account."""

    phone: str  # canonical form, e.g. "+628123456789"
    alias: str  # user-chosen short name, e.g. "main"
    session_name: str  # file stem inside sessions/, no extension
    first_name: str = ""
    last_name: str = ""
    username: Optional[str] = None
    user_id: Optional[int] = None
    is_2fa: bool = False
    device_preset: str = "default"  # key into DEVICE_PRESETS
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    last_login_at: Optional[str] = None

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or self.alias

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Account":
        # Defensive: ignore unknown keys so old/new schemas don't explode.
        known = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------
class AccountStore:
    """JSON-backed registry of accounts.

    Thread-safety: not thread-safe. The CLI is single-threaded (asyncio),
    so this is fine. If you ever call from multiple threads, add a lock.
    """

    def __init__(self, accounts_file: Path, sessions_dir: Path) -> None:
        self.accounts_file = accounts_file
        self.sessions_dir = sessions_dir
        self._accounts: Dict[str, Account] = {}  # keyed by phone
        self._loaded = False

    # -- loading / saving --------------------------------------------------
    def load(self) -> None:
        """Read accounts.json into memory. Missing file => empty store."""
        if not self.accounts_file.exists():
            log.debug("No accounts.json yet at %s, starting empty", self.accounts_file)
            self._accounts = {}
            self._loaded = True
            return

        try:
            raw = self.accounts_file.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(
                f"Failed to read {self.accounts_file}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise StorageError(
                f"{self.accounts_file} is malformed (expected object, got {type(data).__name__})"
            )

        version = data.get("_schema", _SCHEMA_VERSION)
        if version != _SCHEMA_VERSION:
            log.warning(
                "accounts.json schema v%s differs from current v%s; "
                "attempting best-effort load",
                version,
                _SCHEMA_VERSION,
            )

        accounts_raw = data.get("accounts", [])
        self._accounts = {}
        for item in accounts_raw:
            try:
                acc = Account.from_dict(item)
                self._accounts[acc.phone] = acc
            except TypeError as exc:  # pragma: no cover - defensive
                log.warning("Skipping malformed account entry %r: %s", item, exc)

        self._loaded = True
        log.debug("Loaded %d account(s) from %s", len(self._accounts), self.accounts_file)

    def save(self) -> None:
        """Atomically persist the store to disk."""
        self._ensure_loaded()
        payload = {
            "_schema": _SCHEMA_VERSION,
            "accounts": [a.to_dict() for a in self._accounts.values()],
        }
        try:
            self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: tmp file in same dir, then rename.
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.accounts_file.parent),
                prefix=".accounts.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                json.dump(payload, tmp, indent=2, ensure_ascii=False)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = Path(tmp.name)
            os.replace(tmp_path, self.accounts_file)
        except OSError as exc:
            raise StorageError(
                f"Failed to write {self.accounts_file}: {exc}"
            ) from exc
        log.debug("Saved %d account(s) to %s", len(self._accounts), self.accounts_file)

    # -- CRUD --------------------------------------------------------------
    def add(self, account: Account) -> None:
        self._ensure_loaded()
        if account.phone in self._accounts:
            raise AccountAlreadyExistsError(
                f"Account {account.phone} is already registered "
                f"(alias: {self._accounts[account.phone].alias})."
            )
        # Aliases should also be unique - helps the CLI pickers.
        if any(a.alias == account.alias for a in self._accounts.values()):
            raise AccountAlreadyExistsError(
                f"Alias '{account.alias}' is already in use; choose another."
            )
        self._accounts[account.phone] = account
        self.save()

    def update(self, account: Account) -> None:
        self._ensure_loaded()
        if account.phone not in self._accounts:
            raise AccountNotFoundError(f"Account {account.phone} not registered.")
        self._accounts[account.phone] = account
        self.save()

    def remove(self, phone_or_alias: str, *, delete_session: bool = True) -> Account:
        """Remove an account from the registry (and optionally its session file)."""
        self._ensure_loaded()
        acc = self.find(phone_or_alias)
        if acc is None:
            raise AccountNotFoundError(
                f"No account matches '{phone_or_alias}'."
            )
        del self._accounts[acc.phone]
        if delete_session:
            self._delete_session_file(acc.session_name)
        self.save()
        return acc

    # -- queries -----------------------------------------------------------
    def all(self) -> List[Account]:
        self._ensure_loaded()
        return sorted(self._accounts.values(), key=lambda a: a.alias.lower())

    def find(self, phone_or_alias: str) -> Optional[Account]:
        """Look up by either phone (with or without ``+``) or alias."""
        self._ensure_loaded()
        needle = phone_or_alias.strip()
        if not needle:
            return None
        # Exact phone match first
        if needle in self._accounts:
            return self._accounts[needle]
        # Normalize phone form
        if not needle.startswith("+"):
            plus = "+" + needle.lstrip("+")
            if plus in self._accounts:
                return self._accounts[plus]
        # Alias match (case-insensitive)
        for acc in self._accounts.values():
            if acc.alias.lower() == needle.lower():
                return acc
        return None

    def get(self, phone_or_alias: str) -> Account:
        acc = self.find(phone_or_alias)
        if acc is None:
            raise AccountNotFoundError(f"No account matches '{phone_or_alias}'.")
        return acc

    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._accounts)

    def __iter__(self) -> Iterable[Account]:
        return iter(self.all())

    # -- internals ---------------------------------------------------------
    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def session_path(self, session_name: str) -> Path:
        """Full path to a ``.session`` file (without the extension Telethon adds)."""
        return self.sessions_dir / session_name

    def _delete_session_file(self, session_name: str) -> None:
        # Telethon appends `.session`, and may keep a `.session-journal` temp.
        for suffix in (".session", ".session-journal"):
            p = self.sessions_dir / f"{session_name}{suffix}"
            if p.exists():
                try:
                    p.unlink()
                    log.debug("Deleted session artifact %s", p)
                except OSError as exc:
                    log.warning("Could not delete %s: %s", p, exc)
