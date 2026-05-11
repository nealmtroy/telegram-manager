"""Configuration loader for Telegram Manager.

Loads API credentials and optional settings from a `.env` file (via
python-dotenv) and exposes them as a typed :class:`Config` dataclass.

The config is intentionally *fail-loud*: if TELEGRAM_API_ID / TELEGRAM_API_HASH
are missing, we raise :class:`telegram_manager.exceptions.ConfigError` instead
of silently falling back to defaults, because those are required to talk to
Telegram at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .exceptions import ConfigError

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
SESSIONS_DIR: Path = PROJECT_ROOT / "sessions"
LOGS_DIR: Path = PROJECT_ROOT / "logs"
ACCOUNTS_FILE: Path = PROJECT_ROOT / "accounts.json"
ENV_FILE: Path = PROJECT_ROOT / ".env"


@dataclass
class ProxyConfig:
    """Optional SOCKS5 proxy settings for Telethon."""

    proxy_type: str = "socks5"
    host: str = ""
    port: int = 0
    username: Optional[str] = None
    password: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.port)

    def to_telethon(self) -> Optional[tuple]:
        """Convert to the tuple format Telethon expects, or ``None`` if off."""
        if not self.enabled:
            return None
        # Telethon accepts (proxy_type, host, port, rdns, username, password)
        return (
            self.proxy_type,
            self.host,
            self.port,
            True,
            self.username or None,
            self.password or None,
        )


@dataclass
class Config:
    """Global runtime configuration."""

    api_id: Optional[int]
    api_hash: Optional[str]
    log_level: str = "INFO"
    default_session_name: Optional[str] = None
    proxy: ProxyConfig = field(default_factory=ProxyConfig)

    # Paths (filled from module constants so tests can override)
    sessions_dir: Path = field(default_factory=lambda: SESSIONS_DIR)
    logs_dir: Path = field(default_factory=lambda: LOGS_DIR)
    accounts_file: Path = field(default_factory=lambda: ACCOUNTS_FILE)

    @property
    def has_own_api(self) -> bool:
        """True iff user's own api_id/api_hash are configured in .env."""
        return self.api_id is not None and bool(self.api_hash)

    def ensure_dirs(self) -> None:
        """Create sessions/ and logs/ directories if they don't exist."""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


def _parse_int(value: Optional[str], *, name: str) -> Optional[int]:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value.strip())
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigError(
            f"Invalid {name}: expected an integer, got {value!r}"
        ) from exc


def load_config(env_file: Optional[Path] = None) -> Config:
    """Load configuration from a ``.env`` file (and process env).

    ``TELEGRAM_API_ID`` / ``TELEGRAM_API_HASH`` are optional. They're only
    required if you intend to use the ``default`` device preset (ToS-compliant
    path). If you'll only ever use official device presets (iphone / samsung /
    desktop / ...), you can leave them blank.

    Args:
        env_file: Optional override for the ``.env`` path. Defaults to
            ``<project_root>/.env``.

    Raises:
        ConfigError: If any provided value is malformed.
    """
    env_path = env_file or ENV_FILE
    if env_path.exists():
        load_dotenv(env_path, override=False)

    api_id = _parse_int(os.getenv("TELEGRAM_API_ID"), name="TELEGRAM_API_ID")
    api_hash_raw = os.getenv("TELEGRAM_API_HASH", "").strip() or None

    if api_hash_raw is not None and len(api_hash_raw) < 20:
        raise ConfigError(
            "TELEGRAM_API_HASH looks too short to be valid. "
            "Double-check it at https://my.telegram.org/apps or leave it empty "
            "if you only use official device presets."
        )

    proxy = ProxyConfig(
        proxy_type=os.getenv("PROXY_TYPE", "socks5"),
        host=os.getenv("PROXY_HOST", "").strip(),
        port=int(os.getenv("PROXY_PORT", "0") or 0),
        username=os.getenv("PROXY_USER") or None,
        password=os.getenv("PROXY_PASS") or None,
    )

    cfg = Config(
        api_id=api_id,
        api_hash=api_hash_raw,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        default_session_name=os.getenv("DEFAULT_SESSION_NAME") or None,
        proxy=proxy,
    )
    cfg.ensure_dirs()
    return cfg
