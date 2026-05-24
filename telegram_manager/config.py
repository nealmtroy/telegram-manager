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
from typing import List, Optional
from urllib.parse import unquote, urlparse

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


@dataclass(frozen=True)
class TelegramApiCredential:
    api_id: int
    api_hash: str


@dataclass(frozen=True)
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
    api_credentials: List[TelegramApiCredential] = field(default_factory=list)
    api_account_limit: int = 250
    broadcast_per_admin_concurrency: int = 15
    broadcast_global_concurrency: int = 150
    proxies: List[ProxyConfig] = field(default_factory=list)

    # Paths (filled from module constants so tests can override)
    sessions_dir: Path = field(default_factory=lambda: SESSIONS_DIR)
    logs_dir: Path = field(default_factory=lambda: LOGS_DIR)
    accounts_file: Path = field(default_factory=lambda: ACCOUNTS_FILE)

    @property
    def has_own_api(self) -> bool:
        """True iff user's own api_id/api_hash are configured in .env."""
        return bool(self.api_credentials)

    def api_credential_for_index(self, index: Optional[int]) -> TelegramApiCredential:
        if not self.api_credentials:
            raise ConfigError("No Telegram API credentials configured.")
        if index is None:
            return self.api_credentials[0]
        return self.api_credentials[index % len(self.api_credentials)]

    def proxy_for_index(self, index: Optional[int]) -> Optional[ProxyConfig]:
        if not self.proxies:
            return self.proxy if self.proxy.enabled else None
        if index is None:
            return self.proxies[0]
        return self.proxies[index % len(self.proxies)]

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


def _validate_api_hash(api_hash: Optional[str], *, name: str = "TELEGRAM_API_HASH") -> Optional[str]:
    if api_hash is None:
        return None
    value = api_hash.strip()
    if not value:
        return None
    if len(value) < 20:
        raise ConfigError(
            f"{name} looks too short to be valid. "
            "Double-check it at https://my.telegram.org/apps or leave it empty "
            "if you only use official device presets."
        )
    return value


def _parse_api_credentials(primary_id: Optional[int], primary_hash: Optional[str]) -> List[TelegramApiCredential]:
    credentials: List[TelegramApiCredential] = []
    if primary_id is not None and primary_hash:
        credentials.append(TelegramApiCredential(primary_id, primary_hash))

    raw = os.getenv("TELEGRAM_API_CREDENTIALS", "").strip()
    if not raw:
        return credentials

    for item in raw.replace(",", ";").split(";"):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            raise ConfigError("TELEGRAM_API_CREDENTIALS entries must use api_id:api_hash format.")
        api_id_raw, api_hash_raw = item.split(":", 1)
        api_id = _parse_int(api_id_raw, name="TELEGRAM_API_CREDENTIALS api_id")
        api_hash = _validate_api_hash(api_hash_raw, name="TELEGRAM_API_CREDENTIALS api_hash")
        if api_id is None or not api_hash:
            raise ConfigError("TELEGRAM_API_CREDENTIALS entries must include both api_id and api_hash.")
        credentials.append(TelegramApiCredential(api_id, api_hash))
    return credentials


def _parse_proxy_url(value: str) -> ProxyConfig:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"socks5", "socks4", "http"}:
        raise ConfigError(
            "Invalid TELEGRAM_PROXIES entry: proxy URL must start with socks5://, socks4://, or http://"
        )
    if not parsed.hostname or not parsed.port:
        raise ConfigError("Invalid TELEGRAM_PROXIES entry: proxy URL requires host and port")
    return ProxyConfig(
        proxy_type=parsed.scheme,
        host=parsed.hostname,
        port=parsed.port,
        username=unquote(parsed.username) if parsed.username else None,
        password=unquote(parsed.password) if parsed.password else None,
    )


def _parse_proxy_file(path: str) -> List[ProxyConfig]:
    proxy_path = Path(path).expanduser()
    if not proxy_path.is_absolute():
        proxy_path = PROJECT_ROOT / proxy_path
    try:
        lines = proxy_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"Unable to read TELEGRAM_PROXIES_FILE: {proxy_path}") from exc
    proxies: List[ProxyConfig] = []
    for line in lines:
        item = line.strip()
        if item and not item.startswith("#"):
            proxies.append(_parse_proxy_url(item))
    return proxies


def _parse_proxy_pool(legacy_proxy: ProxyConfig) -> List[ProxyConfig]:
    file_path = os.getenv("TELEGRAM_PROXIES_FILE", "").strip()
    if file_path:
        return _parse_proxy_file(file_path)

    raw = os.getenv("TELEGRAM_PROXIES", "").strip()
    proxies: List[ProxyConfig] = []
    if raw:
        for item in raw.replace(",", ";").split(";"):
            item = item.strip()
            if item:
                proxies.append(_parse_proxy_url(item))
    elif legacy_proxy.enabled:
        proxies.append(legacy_proxy)
    return proxies


def _parse_positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = _parse_int(raw, name=name)
    if value is None or value <= 0:
        raise ConfigError(f"Invalid {name}: expected a positive integer, got {raw!r}")
    return value


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
    api_hash_raw = _validate_api_hash(os.getenv("TELEGRAM_API_HASH"), name="TELEGRAM_API_HASH")

    api_credentials = _parse_api_credentials(api_id, api_hash_raw)
    if api_credentials and (api_id is None or api_hash_raw is None):
        api_id = api_credentials[0].api_id
        api_hash_raw = api_credentials[0].api_hash

    proxy = ProxyConfig(
        proxy_type=os.getenv("PROXY_TYPE", "socks5"),
        host=os.getenv("PROXY_HOST", "").strip(),
        port=int(os.getenv("PROXY_PORT", "0") or 0),
        username=os.getenv("PROXY_USER") or None,
        password=os.getenv("PROXY_PASS") or None,
    )
    proxies = _parse_proxy_pool(proxy)

    cfg = Config(
        api_id=api_id,
        api_hash=api_hash_raw,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        default_session_name=os.getenv("DEFAULT_SESSION_NAME") or None,
        proxy=proxy,
        api_credentials=api_credentials,
        api_account_limit=_parse_positive_int_env("TELEGRAM_API_ACCOUNT_LIMIT", 250),
        broadcast_per_admin_concurrency=_parse_positive_int_env("BROADCAST_PER_ADMIN_CONCURRENCY", 15),
        broadcast_global_concurrency=_parse_positive_int_env("BROADCAST_GLOBAL_CONCURRENCY", 150),
        proxies=proxies,
    )
    cfg.ensure_dirs()
    return cfg
