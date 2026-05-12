"""Device presets for Telegram sessions.

These control what shows up in Telegram's "Active Sessions" panel:

    📱 <device_model> · <app_name_from_api_id> <app_version>
       <system_version> · <login date>

Two sources of ``api_id`` / ``api_hash``:

1. **default** preset -> api_id/api_hash read from ``.env`` (your own app
   registered at https://my.telegram.org/apps). This is the ToS-compliant
   path; app name on Telegram shows whatever title you registered.

2. **official** presets (iphone_*, android_*, desktop_*, macos_*) -> use
   publicly leaked credentials of real Telegram apps. Session appears as
   "Telegram iOS / Android / Desktop / macOS" on the server side.

⚠️ WARNING ABOUT OFFICIAL PRESETS:

Using leaked official api_id/api_hash pairs violates Telegram's Terms of
Service. Telegram can flag or ban accounts that use them, especially under
abnormal usage patterns (rapid messaging, many accounts from one IP, bot-
like behavior). Telegram also occasionally rotates these credentials - if
``ApiIdInvalidError`` starts showing up, the leaked pair has been disabled
and you need to either wait for the community to share a new one or switch
to the ``default`` preset.

Use at your own risk.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class DevicePreset:
    """Parameters passed to ``TelegramClient`` to shape session appearance."""

    key: str
    display_name: str
    device_model: str
    system_version: str
    app_version: str
    lang_code: str = "en"
    system_lang_code: str = "en"
    # Optional: if provided, overrides user's own api_id/api_hash.
    # Presence of these makes the session appear as the official app.
    api_id: Optional[int] = None
    api_hash: Optional[str] = None
    # Optional lang pack tag (e.g. "android", "ios", "tdesktop"). Telethon's
    # public constructor doesn't forward it, so we keep it for docs only.
    lang_pack: Optional[str] = None

    @property
    def uses_official_api(self) -> bool:
        """True when this preset bundles leaked official credentials."""
        return self.api_id is not None and bool(self.api_hash)

    def summary(self) -> str:
        return f"{self.device_model} · {self.system_version} · v{self.app_version}"


# ---------------------------------------------------------------------------
# Built-in catalog
# ---------------------------------------------------------------------------
# NOTE: The api_id / api_hash values below are widely-circulated *leaked*
# pairs from official Telegram clients. They are included because the user
# explicitly asked for this behavior. They are NOT secret - they've been
# published on GitHub/gists/blogs for years - but using them still violates
# Telegram's ToS. See the module docstring above.
DEVICE_PRESETS: Dict[str, DevicePreset] = {
    # ---- Your own API (ToS-compliant escape hatch) -------------------------
    "default": DevicePreset(
        key="default",
        display_name="Your own api_id (from .env)  [SAFE]",
        device_model="iPhone 15 Pro Max",
        system_version="iOS 18.5",
        app_version="11.12.1",
    ),

    # ---- iOS (Telegram iOS official) --------------------------------------
    "iphone_15_pro": DevicePreset(
        key="iphone_15_pro",
        display_name="iPhone 15 Pro Max · Telegram iOS  [OFFICIAL API]",
        device_model="iPhone 15 Pro Max",
        system_version="iOS 18.5",
        app_version="11.12.1",
        api_id=8,
        api_hash="7245de8e747a0d6fbe11f7cc14fcc0bb",
        lang_pack="ios",
    ),
    "iphone_14": DevicePreset(
        key="iphone_14",
        display_name="iPhone 14 · Telegram iOS  [OFFICIAL API]",
        device_model="iPhone 14",
        system_version="iOS 18.5",
        app_version="11.12.1",
        api_id=8,
        api_hash="7245de8e747a0d6fbe11f7cc14fcc0bb",
        lang_pack="ios",
    ),
    "iphone_se": DevicePreset(
        key="iphone_se",
        display_name="iPhone SE (3rd gen) · Telegram iOS  [OFFICIAL API]",
        device_model="iPhone SE",
        system_version="iOS 18.5",
        app_version="11.12.1",
        api_id=8,
        api_hash="7245de8e747a0d6fbe11f7cc14fcc0bb",
        lang_pack="ios",
    ),

    # ---- Android (Telegram for Android official) --------------------------
    "samsung_s24": DevicePreset(
        key="samsung_s24",
        display_name="Samsung Galaxy S24 · Telegram Android  [OFFICIAL API]",
        device_model="SM-S921B",
        system_version="Android 14 (SDK 34)",
        app_version="10.12.1 (4842)",
        api_id=6,
        api_hash="eb06d4abfb49dc3eeb1aeb98ae0f581e",
        lang_pack="android",
    ),
    "samsung_s22": DevicePreset(
        key="samsung_s22",
        display_name="Samsung Galaxy S22 · Telegram Android  [OFFICIAL API]",
        device_model="SM-S901B",
        system_version="Android 13 (SDK 33)",
        app_version="10.9.1 (4770)",
        api_id=6,
        api_hash="eb06d4abfb49dc3eeb1aeb98ae0f581e",
        lang_pack="android",
    ),
    "pixel_8": DevicePreset(
        key="pixel_8",
        display_name="Google Pixel 8 · Telegram Android  [OFFICIAL API]",
        device_model="Pixel 8",
        system_version="Android 14 (SDK 34)",
        app_version="10.12.1 (4842)",
        api_id=6,
        api_hash="eb06d4abfb49dc3eeb1aeb98ae0f581e",
        lang_pack="android",
    ),
    "xiaomi_13": DevicePreset(
        key="xiaomi_13",
        display_name="Xiaomi 13 · Telegram Android  [OFFICIAL API]",
        device_model="2211133G",
        system_version="Android 13 (SDK 33)",
        app_version="10.10.0 (4793)",
        api_id=6,
        api_hash="eb06d4abfb49dc3eeb1aeb98ae0f581e",
        lang_pack="android",
    ),

    # ---- Desktop (Telegram Desktop / tdesktop official) --------------------
    "desktop_windows": DevicePreset(
        key="desktop_windows",
        display_name="Desktop · Windows · Telegram Desktop  [OFFICIAL API]",
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.3.0 x64",
        api_id=2040,
        api_hash="b18441a1ff607e10a989891a5462e627",
        lang_pack="tdesktop",
    ),
    "desktop_linux": DevicePreset(
        key="desktop_linux",
        display_name="Desktop · Linux · Telegram Desktop  [OFFICIAL API]",
        device_model="PC 64bit",
        system_version="Linux 6.6 (Ubuntu 24.04)",
        app_version="5.3.0",
        api_id=2040,
        api_hash="b18441a1ff607e10a989891a5462e627",
        lang_pack="tdesktop",
    ),

    # ---- macOS (Telegram macOS Swift official) ----------------------------
    "desktop_macos": DevicePreset(
        key="desktop_macos",
        display_name="MacBook Pro · Telegram macOS  [OFFICIAL API]",
        device_model="MacBook Pro",
        system_version="macOS 14.5",
        app_version="10.13 (5200)",
        api_id=2834,
        api_hash="68875f756c9b437a8b916ca3de215815",
        lang_pack="macos",
    ),
}

DEFAULT_PRESET_KEY = "default"


def get_preset(key: Optional[str]) -> DevicePreset:
    """Look up a preset by key, falling back to the default."""
    if not key:
        return DEVICE_PRESETS[DEFAULT_PRESET_KEY]
    return DEVICE_PRESETS.get(key, DEVICE_PRESETS[DEFAULT_PRESET_KEY])


def list_presets() -> List[DevicePreset]:
    """Return all presets, default first, then grouped iOS / Android / Desktop."""
    ordered = [DEVICE_PRESETS[DEFAULT_PRESET_KEY]]
    ordered.extend(
        p for k, p in DEVICE_PRESETS.items() if k != DEFAULT_PRESET_KEY
    )
    return ordered
