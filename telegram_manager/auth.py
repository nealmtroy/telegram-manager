"""Authentication / login flow.

This module does *one* thing: drive Telethon's interactive login, turning its
internal exceptions into our typed exceptions and letting the CLI layer
supply the human-facing prompts (code + 2FA password) via callbacks.

Typical flow:

    async def ask_code() -> str: ...
    async def ask_password(hint: str | None) -> str: ...

    account = await AuthFlow(config, store).login(
        phone="+628123456789",
        alias="main",
        code_callback=ask_code,
        password_callback=ask_password,
    )

The module is side-effect free *except for* writing a ``.session`` file and
updating ``accounts.json`` via :class:`AccountStore`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyUnregisteredError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhoneNumberInvalidError,
    PhoneNumberUnoccupiedError,
    SessionPasswordNeededError,
)

from .config import Config
from .device_presets import DevicePreset, get_preset
from .exceptions import (
    AuthError,
    ConfigError,
    ConnectionError,
    FloodError,
    InvalidCodeError,
    InvalidPasswordError,
    InvalidPhoneError,
    PhoneBannedError,
    UserCancelledError,
)
from .logger import get_logger
from .storage import Account, AccountStore

log = get_logger("auth")

# Callback signatures --------------------------------------------------------
CodeCallback = Callable[[], Awaitable[str]]
# Password callback receives an optional hint from Telegram and must return the
# cloud password. If the user cancels, raise UserCancelledError.
PasswordCallback = Callable[[Optional[str]], Awaitable[str]]


class AuthFlow:
    """Stateless helper that logs in one account at a time."""

    def __init__(self, config: Config, store: AccountStore) -> None:
        self.config = config
        self.store = store

    # ---- public API ------------------------------------------------------
    async def login(
        self,
        *,
        phone: str,
        alias: str,
        code_callback: CodeCallback,
        password_callback: PasswordCallback,
        force_sms: bool = False,
        device_preset: str = "default",
    ) -> Account:
        """Perform an interactive login.

        Creates/updates the account in the store on success.

        Args:
            phone: E.164-ish phone string (with or without leading ``+``).
            alias: Human-friendly short name.
            code_callback: Async callable returning the login code typed by user.
            password_callback: Async callable returning the 2FA password.
            force_sms: If True, request SMS delivery instead of the in-app code.
            device_preset: Key into :mod:`device_presets`. Picks device_model,
                system_version, app_version - and for non-default presets, the
                leaked official api_id/api_hash. See README for ToS warning.

        Raises:
            InvalidPhoneError, InvalidCodeError, InvalidPasswordError,
            PhoneBannedError, FloodError, ConnectionError, AuthError
        """
        phone_norm = self._normalize_phone(phone)
        alias = alias.strip()
        if not alias:
            raise UserCancelledError("Alias is required.")

        preset = get_preset(device_preset)
        session_name = phone_norm.lstrip("+")  # "+62..." -> "62..."
        session_path = str(self.store.session_path(session_name))

        client = self._build_client(session_path, preset)
        log.debug(
            "Connecting client for %s (session=%s, preset=%s, official_api=%s)",
            phone_norm,
            session_path,
            preset.key,
            preset.uses_official_api,
        )

        try:
            await client.connect()
        except OSError as exc:
            raise ConnectionError(
                f"Could not connect to Telegram servers: {exc}"
            ) from exc

        try:
            is_authorized = await client.is_user_authorized()
            if is_authorized:
                log.info("Session already authorized for %s, reusing.", phone_norm)
                me = await client.get_me()
                return self._upsert_account(
                    phone=phone_norm,
                    alias=alias,
                    session_name=session_name,
                    me=me,
                    is_2fa=False,  # we don't actually know; keep existing flag on update
                )

            # --- 1. Send the login code ----------------------------------
            try:
                sent = await client.send_code_request(phone_norm, force_sms=force_sms)
                log.debug(
                    "Code request sent to %s (type=%s)",
                    phone_norm,
                    getattr(sent, "type", "?"),
                )
            except PhoneNumberInvalidError as exc:
                raise InvalidPhoneError(
                    f"Phone number {phone_norm!r} is invalid."
                ) from exc
            except PhoneNumberBannedError as exc:
                raise PhoneBannedError(
                    f"Phone number {phone_norm!r} is banned from Telegram."
                ) from exc
            except PhoneNumberFloodError as exc:
                raise FloodError(
                    seconds=60,
                    message="Too many login attempts for this number. Wait and retry.",
                ) from exc
            except FloodWaitError as exc:
                raise FloodError(seconds=exc.seconds) from exc
            except ApiIdInvalidError as exc:
                raise ConfigError(
                    "TELEGRAM_API_ID/TELEGRAM_API_HASH are invalid. "
                    "Re-check them at https://my.telegram.org/apps."
                ) from exc

            # --- 2. Ask for the code ------------------------------------
            code = (await code_callback()).strip()
            if not code:
                raise UserCancelledError("Login code is empty, aborting.")

            is_2fa = False
            # --- 3. Try signing in with the code -------------------------
            try:
                await client.sign_in(phone=phone_norm, code=code)
            except SessionPasswordNeededError:
                # ---- 2FA branch ---------------------------------------
                is_2fa = True
                log.info("Account %s has 2FA enabled; prompting for password.", phone_norm)
                hint = await self._fetch_password_hint(client)
                password = (await password_callback(hint)).strip()
                if not password:
                    raise UserCancelledError("2FA password is empty, aborting.")
                try:
                    await client.sign_in(password=password)
                except PasswordHashInvalidError as exc:
                    raise InvalidPasswordError(
                        "The 2FA password is incorrect."
                    ) from exc
                except FloodWaitError as exc:
                    raise FloodError(seconds=exc.seconds) from exc
            except (PhoneCodeInvalidError, PhoneCodeEmptyError) as exc:
                raise InvalidCodeError("The login code is incorrect.") from exc
            except PhoneCodeExpiredError as exc:
                raise InvalidCodeError(
                    "The login code has expired. Request a new one."
                ) from exc
            except PhoneNumberUnoccupiedError as exc:
                raise InvalidPhoneError(
                    "This phone number has no Telegram account. "
                    "Sign up in the official app first."
                ) from exc
            except FloodWaitError as exc:
                raise FloodError(seconds=exc.seconds) from exc

            # --- 4. Fetch our own info ---------------------------------
            me = await client.get_me()
            if me is None:
                raise AuthError("Sign-in succeeded but get_me() returned None.")

            log.info(
                "Logged in as %s (id=%s, 2FA=%s)",
                getattr(me, "first_name", "?"),
                getattr(me, "id", "?"),
                is_2fa,
            )
            return self._upsert_account(
                phone=phone_norm,
                alias=alias,
                session_name=session_name,
                me=me,
                is_2fa=is_2fa,
                device_preset=preset.key,
            )
        finally:
            if client.is_connected():
                await client.disconnect()

    async def probe(self, account: Account) -> bool:
        """Connect with an existing session and report whether it's still authorized.

        Does not prompt for anything. Disconnects before returning.
        """
        preset = get_preset(account.device_preset)
        session_path = str(self.store.session_path(account.session_name))
        client = self._build_client(session_path, preset)
        try:
            await client.connect()
        except OSError as exc:
            raise ConnectionError(f"Connect failed for {account.alias}: {exc}") from exc
        try:
            return await client.is_user_authorized()
        except AuthKeyUnregisteredError:
            return False
        finally:
            if client.is_connected():
                await client.disconnect()

    async def logout(self, account: Account) -> None:
        """Revoke the session on Telegram's side and delete the local file."""
        preset = get_preset(account.device_preset)
        session_path = str(self.store.session_path(account.session_name))
        client = self._build_client(session_path, preset)
        try:
            await client.connect()
            if await client.is_user_authorized():
                await client.log_out()
                log.info("Logged out %s on Telegram servers.", account.alias)
            else:
                log.info(
                    "Session for %s was already unauthorized.", account.alias
                )
        except OSError as exc:
            raise ConnectionError(
                f"Could not reach Telegram to log out {account.alias}: {exc}"
            ) from exc
        finally:
            if client.is_connected():
                await client.disconnect()
        # Remove from store regardless (caller decides)
        self.store.remove(account.phone, delete_session=True)

    # ---- helpers ---------------------------------------------------------
    def _build_client(
        self, session_path: str, preset: DevicePreset
    ) -> TelegramClient:
        """Construct a Telethon client shaped by ``preset``.

        - If the preset carries its own api_id/api_hash (official leaked pair),
          those are used and the session will appear as the official Telegram
          app on Telegram's servers.
        - Otherwise we fall back to the user's own api_id/api_hash from .env.
        """
        proxy = self.config.proxy.to_telethon()
        if preset.uses_official_api:
            api_id = preset.api_id
            api_hash = preset.api_hash
        else:
            if not self.config.has_own_api:
                raise ConfigError(
                    "The 'default' device preset needs TELEGRAM_API_ID and "
                    "TELEGRAM_API_HASH in .env. Either fill them in "
                    "(see https://my.telegram.org/apps) or pick an official "
                    "device preset (iphone_*, samsung_*, desktop_*, ...)."
                )
            api_id = self.config.api_id
            api_hash = self.config.api_hash
        return TelegramClient(
            session_path,
            api_id,
            api_hash,
            proxy=proxy,
            device_model=preset.device_model,
            system_version=preset.system_version,
            app_version=preset.app_version,
            lang_code=preset.lang_code,
            system_lang_code=preset.system_lang_code,
        )

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        s = phone.strip().replace(" ", "").replace("-", "")
        if not s:
            raise InvalidPhoneError("Phone number is empty.")
        if not s.startswith("+"):
            s = "+" + s.lstrip("+")
        # Must be digits after the '+'
        if not s[1:].isdigit():
            raise InvalidPhoneError(
                f"Phone number {phone!r} contains invalid characters."
            )
        if len(s) < 7:  # very lax sanity check
            raise InvalidPhoneError(f"Phone number {phone!r} is too short.")
        return s

    @staticmethod
    async def _fetch_password_hint(client: TelegramClient) -> Optional[str]:
        """Best-effort fetch of the 2FA password hint (may be None)."""
        try:
            from telethon.tl.functions.account import GetPasswordRequest

            pwd = await client(GetPasswordRequest())
            hint = getattr(pwd, "hint", None) or None
            return hint
        except Exception as exc:  # noqa: BLE001 - hint is optional, never fatal
            log.debug("Could not fetch 2FA hint: %s", exc)
            return None

    def _upsert_account(
        self,
        *,
        phone: str,
        alias: str,
        session_name: str,
        me: object,
        is_2fa: bool,
        device_preset: str = "default",
    ) -> Account:
        """Create-or-update the account in the store."""
        existing = self.store.find(phone)
        now_iso = datetime.now(timezone.utc).isoformat()
        first = getattr(me, "first_name", "") or ""
        last = getattr(me, "last_name", "") or ""
        username = getattr(me, "username", None)
        user_id = getattr(me, "id", None)

        if existing is None:
            acc = Account(
                phone=phone,
                alias=alias,
                session_name=session_name,
                first_name=first,
                last_name=last,
                username=username,
                user_id=user_id,
                is_2fa=is_2fa,
                device_preset=device_preset,
                last_login_at=now_iso,
            )
            self.store.add(acc)
            return acc

        # Update in place
        existing.alias = alias or existing.alias
        existing.first_name = first
        existing.last_name = last
        existing.username = username
        existing.user_id = user_id
        # Keep 2FA flag sticky: once we saw 2FA, remember it.
        existing.is_2fa = existing.is_2fa or is_2fa
        existing.device_preset = device_preset
        existing.last_login_at = now_iso
        self.store.update(existing)
        return existing
