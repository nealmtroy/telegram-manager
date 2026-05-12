"""Device presets for Telegram sessions.

Each preset controls what shows up in Telegram's "Active Sessions" panel.
All presets use the user's own api_id/api_hash from .env (set app title to
"Telegram iOS" at https://my.telegram.org/apps for full effect).

The ``random`` preset picks a random device from the pool on each login.
"""
from __future__ import annotations

import random
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

    @property
    def uses_official_api(self) -> bool:
        """Always False now — all presets use user's own api_id."""
        return False

    def summary(self) -> str:
        return f"{self.device_model} · {self.system_version} · v{self.app_version}"


# ---------------------------------------------------------------------------
# Device pool for randomization
# ---------------------------------------------------------------------------
_DEVICE_POOL: List[dict] = [
    # iPhone models
    {"device_model": "iPhone 17 Pro Max", "system_version": "iOS 19.0", "app_version": "11.15.0"},
    {"device_model": "iPhone 17 Pro", "system_version": "iOS 19.0", "app_version": "11.15.0"},
    {"device_model": "iPhone 17", "system_version": "iOS 19.0", "app_version": "11.14.2"},
    {"device_model": "iPhone 16 Pro Max", "system_version": "iOS 18.5", "app_version": "11.12.1"},
    {"device_model": "iPhone 16 Pro", "system_version": "iOS 18.5", "app_version": "11.12.1"},
    {"device_model": "iPhone 16", "system_version": "iOS 18.5", "app_version": "11.12.1"},
    {"device_model": "iPhone 15 Pro Max", "system_version": "iOS 18.5", "app_version": "11.12.1"},
    {"device_model": "iPhone 15 Pro", "system_version": "iOS 18.5", "app_version": "11.12.1"},
    {"device_model": "iPhone 15", "system_version": "iOS 18.4", "app_version": "11.11.0"},
    {"device_model": "iPhone 14 Pro Max", "system_version": "iOS 18.5", "app_version": "11.12.1"},
    {"device_model": "iPhone 14 Pro", "system_version": "iOS 18.4", "app_version": "11.11.0"},
    {"device_model": "iPhone 14", "system_version": "iOS 18.3", "app_version": "11.10.2"},
    # Samsung models
    {"device_model": "SM-S928B", "system_version": "Android 15 (SDK 35)", "app_version": "11.8.0 (5100)"},
    {"device_model": "SM-S926B", "system_version": "Android 15 (SDK 35)", "app_version": "11.8.0 (5100)"},
    {"device_model": "SM-S921B", "system_version": "Android 14 (SDK 34)", "app_version": "11.6.2 (5050)"},
    {"device_model": "SM-S918B", "system_version": "Android 14 (SDK 34)", "app_version": "11.5.0 (5020)"},
    # Pixel models
    {"device_model": "Pixel 9 Pro", "system_version": "Android 15 (SDK 35)", "app_version": "11.8.0 (5100)"},
    {"device_model": "Pixel 8 Pro", "system_version": "Android 15 (SDK 35)", "app_version": "11.7.1 (5080)"},
    {"device_model": "Pixel 8", "system_version": "Android 14 (SDK 34)", "app_version": "11.6.2 (5050)"},
]


def _random_device() -> dict:
    """Pick a random device from the pool."""
    return random.choice(_DEVICE_POOL)


# ---------------------------------------------------------------------------
# Preset catalog — all use user's own api_id from .env
# ---------------------------------------------------------------------------
DEVICE_PRESETS: Dict[str, DevicePreset] = {
    "random": DevicePreset(
        key="random",
        display_name="🎲 Random device (changes each login)",
        device_model="(random)",
        system_version="(random)",
        app_version="(random)",
    ),
    "iphone_17_pro_max": DevicePreset(
        key="iphone_17_pro_max",
        display_name="iPhone 17 Pro Max · iOS 19.0",
        device_model="iPhone 17 Pro Max",
        system_version="iOS 19.0",
        app_version="11.15.0",
    ),
    "iphone_17_pro": DevicePreset(
        key="iphone_17_pro",
        display_name="iPhone 17 Pro · iOS 19.0",
        device_model="iPhone 17 Pro",
        system_version="iOS 19.0",
        app_version="11.15.0",
    ),
    "iphone_16_pro_max": DevicePreset(
        key="iphone_16_pro_max",
        display_name="iPhone 16 Pro Max · iOS 18.5",
        device_model="iPhone 16 Pro Max",
        system_version="iOS 18.5",
        app_version="11.12.1",
    ),
    "iphone_16_pro": DevicePreset(
        key="iphone_16_pro",
        display_name="iPhone 16 Pro · iOS 18.5",
        device_model="iPhone 16 Pro",
        system_version="iOS 18.5",
        app_version="11.12.1",
    ),
    "iphone_15_pro_max": DevicePreset(
        key="iphone_15_pro_max",
        display_name="iPhone 15 Pro Max · iOS 18.5",
        device_model="iPhone 15 Pro Max",
        system_version="iOS 18.5",
        app_version="11.12.1",
    ),
    "iphone_15_pro": DevicePreset(
        key="iphone_15_pro",
        display_name="iPhone 15 Pro · iOS 18.5",
        device_model="iPhone 15 Pro",
        system_version="iOS 18.5",
        app_version="11.12.1",
    ),
    "samsung_s25_ultra": DevicePreset(
        key="samsung_s25_ultra",
        display_name="Samsung Galaxy S25 Ultra · Android 15",
        device_model="SM-S928B",
        system_version="Android 15 (SDK 35)",
        app_version="11.8.0 (5100)",
    ),
    "samsung_s24_ultra": DevicePreset(
        key="samsung_s24_ultra",
        display_name="Samsung Galaxy S24 Ultra · Android 14",
        device_model="SM-S921B",
        system_version="Android 14 (SDK 34)",
        app_version="11.6.2 (5050)",
    ),
    "pixel_9_pro": DevicePreset(
        key="pixel_9_pro",
        display_name="Google Pixel 9 Pro · Android 15",
        device_model="Pixel 9 Pro",
        system_version="Android 15 (SDK 35)",
        app_version="11.8.0 (5100)",
    ),
    "desktop_windows": DevicePreset(
        key="desktop_windows",
        display_name="Desktop · Windows 11",
        device_model="PC 64bit",
        system_version="Windows 11",
        app_version="5.8.0 x64",
    ),
    "desktop_macos": DevicePreset(
        key="desktop_macos",
        display_name="MacBook Pro · macOS 15",
        device_model="MacBook Pro",
        system_version="macOS 15.1",
        app_version="11.14 (5300)",
    ),
}

DEFAULT_PRESET_KEY = "random"


def get_preset(key: Optional[str]) -> DevicePreset:
    """Look up a preset by key. If 'random', resolve to a concrete device."""
    if not key or key == "random":
        d = _random_device()
        return DevicePreset(
            key="random",
            display_name=f"Random: {d['device_model']}",
            device_model=d["device_model"],
            system_version=d["system_version"],
            app_version=d["app_version"],
        )
    return DEVICE_PRESETS.get(key, DEVICE_PRESETS["random"])


def get_preset_static(key: Optional[str]) -> DevicePreset:
    """Look up without resolving random — for display purposes only."""
    if not key:
        return DEVICE_PRESETS[DEFAULT_PRESET_KEY]
    return DEVICE_PRESETS.get(key, DEVICE_PRESETS[DEFAULT_PRESET_KEY])


def list_presets() -> List[DevicePreset]:
    """Return all presets, random first."""
    ordered = [DEVICE_PRESETS["random"]]
    ordered.extend(p for k, p in DEVICE_PRESETS.items() if k != "random")
    return ordered
