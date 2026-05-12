"""Interactive CLI for Telegram Manager.

Uses ``questionary`` for prompts/menus and ``rich`` for tables/panels.

The CLI is organized as one top-level menu that delegates to small async
handlers. Every handler is defensive: any :class:`TelegramManagerError`
bubbles up to :meth:`InteractiveCLI.run` which catches, logs, and returns to
the menu rather than crashing.
"""
from __future__ import annotations

import asyncio
from typing import List, Optional

import questionary
from questionary import Choice
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .device_presets import DEFAULT_PRESET_KEY, DevicePreset, get_preset, get_preset_static, list_presets
from .exceptions import (
    AccountNotFoundError,
    RecaptchaRequiredError,
    TelegramManagerError,
    UserCancelledError,
)
from .logger import get_logger
from .manager import BroadcastResult, TelegramManager
from .storage import Account

log = get_logger("cli")
console = Console()


# ---------------------------------------------------------------------------
# Main CLI class
# ---------------------------------------------------------------------------
class InteractiveCLI:
    """Asyncio-friendly interactive menu wrapper."""

    def __init__(self, manager: TelegramManager) -> None:
        self.manager = manager

    # ---- entry point ----------------------------------------------------
    async def run(self) -> None:
        """Main event loop. Returns when the user chooses Exit."""
        self._print_banner()
        while True:
            try:
                choice = await self._main_menu()
                if choice is None or choice == "exit":
                    console.print("[dim]Bye![/dim]")
                    return
                handler = self._dispatch.get(choice)
                if handler is None:
                    console.print(f"[red]Unknown choice:[/red] {choice}")
                    continue
                await handler(self)
            except UserCancelledError as exc:
                console.print(f"[yellow]Cancelled:[/yellow] {exc}")
            except AccountNotFoundError as exc:
                console.print(f"[red]Not found:[/red] {exc}")
            except RecaptchaRequiredError as exc:
                console.print(
                    Panel.fit(
                        str(exc),
                        title="⚠  reCAPTCHA wall hit",
                        border_style="red",
                    )
                )
                log.debug("Handled reCAPTCHA error", exc_info=True)
            except TelegramManagerError as exc:
                console.print(f"[red]Error:[/red] {exc}")
                log.debug("Handled error", exc_info=True)
            except (KeyboardInterrupt, asyncio.CancelledError):
                console.print("\n[dim]Interrupted. Bye![/dim]")
                return
            except Exception as exc:  # noqa: BLE001 - last-resort safety net
                log.exception("Unhandled error in CLI loop")
                console.print(f"[red bold]Unexpected error:[/red bold] {exc}")

    # ---- menus ----------------------------------------------------------
    async def _main_menu(self) -> Optional[str]:
        n = len(self.manager.list_accounts())
        title = f"Telegram Manager  (accounts: {n})"
        choices = [
            Choice(title="➕  Add / login account", value="add"),
            Choice(title="📋  List accounts", value="list"),
            Choice(title="🫀  Health check (all)", value="health"),
            Choice(title="👤  Single-account action", value="single"),
            Choice(title="📣  Multi-account action", value="multi"),
            Choice(title="♻️   Re-login account", value="relogin"),
            Choice(title="🗑   Remove account", value="remove"),
            Choice(title="🚪  Logout (revoke session)", value="logout"),
            Choice(title="❌  Exit", value="exit"),
        ]
        return await questionary.select(title, choices=choices).ask_async()

    # ---- handlers -------------------------------------------------------
    async def add_account(self) -> None:
        phone = await _ask_text(
            "Phone number (with country code, e.g. +628123456789):",
            validate=lambda s: bool(s and s.strip()),
        )
        alias_default = self._suggest_alias(phone)
        alias = await _ask_text(
            f"Short alias for this account [{alias_default}]:",
            default=alias_default,
            validate=lambda s: bool(s and s.strip()),
        )
        preset = await self._pick_preset(
            "Pick a device to impersonate:", default_key="random"
        )
        if preset is None:
            return

        console.print(
            Panel.fit(
                f"Sending login code to [bold]{phone}[/bold]...\n"
                f"Device: [cyan]{preset.display_name}[/cyan]\n"
                f"        [dim]{preset.summary()}[/dim]",
                title="Login",
                border_style="cyan",
            )
        )

        async def ask_code() -> str:
            return await _ask_text(
                "Enter the login code you received:",
                validate=lambda s: bool(s and s.strip()),
            )

        async def ask_password(hint: Optional[str]) -> str:
            label = "Enter your 2FA cloud password"
            if hint:
                label += f" (hint: {hint})"
            label += ":"
            return await _ask_password(label)

        account = await self.manager.auth.login(
            phone=phone,
            alias=alias,
            code_callback=ask_code,
            password_callback=ask_password,
            device_preset=preset.key,
        )
        badge = "[yellow]2FA[/yellow]" if account.is_2fa else "[green]no-2FA[/green]"
        console.print(
            Panel.fit(
                f"Logged in as [bold]{account.display_name}[/bold] "
                f"(@{account.username or '-'}, id={account.user_id})\n"
                f"Alias: [cyan]{account.alias}[/cyan]   {badge}\n"
                f"Device: [cyan]{preset.device_model}[/cyan] · {preset.system_version}",
                title="✅  Success",
                border_style="green",
            )
        )

    async def list_accounts(self) -> None:
        accounts = self.manager.list_accounts()
        if not accounts:
            console.print("[dim]No accounts yet. Use 'Add / login account'.[/dim]")
            return
        _render_accounts_table(accounts)

    async def health_check(self) -> None:
        accounts = self.manager.list_accounts()
        if not accounts:
            console.print("[dim]No accounts to check.[/dim]")
            return
        console.print(f"Probing [bold]{len(accounts)}[/bold] account(s)...")
        statuses = await self.manager.health_check()
        table = Table(title="Health check", show_lines=False)
        table.add_column("Alias", style="cyan")
        table.add_column("Phone")
        table.add_column("Status")
        table.add_column("Detail", overflow="fold")
        for s in statuses:
            if s.ok:
                status_cell = "[green]authorized[/green]"
                detail = ""
            else:
                status_cell = "[red]FAIL[/red]"
                detail = s.error or "unknown error"
            table.add_row(s.account.alias, s.account.phone, status_cell, detail)
        console.print(table)

    async def single_action(self) -> None:
        acc = await self._pick_account("Pick an account:")
        if acc is None:
            return
        action = await questionary.select(
            f"What to do on [{acc.alias}]?",
            choices=[
                Choice(title="Get profile (get_me)", value="me"),
                Choice(title="List groups & channels", value="list_chats"),
                Choice(title="BotFather", value="botfather"),
                Choice(title="Edit name", value="edit_name"),
                Choice(title="Edit bio", value="edit_bio"),
                Choice(title="Edit username", value="edit_username"),
                Choice(title="Send a message", value="send"),
                Choice(title="Back", value="back"),
            ],
        ).ask_async()
        if action == "me":
            await self._show_me([acc])
        elif action == "list_chats":
            await self._list_chats(acc)
        elif action == "botfather":
            await self._botfather(acc)
        elif action == "edit_name":
            await self._edit_name(acc)
        elif action == "edit_bio":
            await self._edit_bio(acc)
        elif action == "edit_username":
            await self._edit_username(acc)
        elif action == "send":
            await self._send_message(accounts=[acc])

    async def multi_action(self) -> None:
        accounts = await self._pick_accounts("Pick accounts (space to toggle):")
        if not accounts:
            return
        action = await questionary.select(
            f"What to do on {len(accounts)} accounts?",
            choices=[
                Choice(title="Get profile (get_me) for each", value="me"),
                Choice(title="Broadcast a message", value="send"),
                Choice(title="Back", value="back"),
            ],
        ).ask_async()
        if action == "me":
            await self._show_me(accounts)
        elif action == "send":
            await self._send_message(accounts=accounts)

    async def relogin(self) -> None:
        acc = await self._pick_account("Pick an account to re-login:")
        if acc is None:
            return
        current_preset = get_preset(acc.device_preset)
        console.print(
            f"[dim]Current device preset: {current_preset.display_name}[/dim]"
        )
        change = await questionary.confirm(
            "Change device preset?", default=False
        ).ask_async()
        if change:
            preset = await self._pick_preset(
                "Pick a new device preset:", default_key=acc.device_preset
            )
            if preset is None:
                return
        else:
            preset = current_preset

        confirm = await questionary.confirm(
            f"Re-login {acc.alias} ({acc.phone}) as {preset.display_name}?",
            default=True,
        ).ask_async()
        if not confirm:
            return

        async def ask_code() -> str:
            return await _ask_text(
                "Enter the login code you received:",
                validate=lambda s: bool(s and s.strip()),
            )

        async def ask_password(hint: Optional[str]) -> str:
            label = "Enter your 2FA cloud password"
            if hint:
                label += f" (hint: {hint})"
            label += ":"
            return await _ask_password(label)

        account = await self.manager.auth.login(
            phone=acc.phone,
            alias=acc.alias,
            code_callback=ask_code,
            password_callback=ask_password,
            device_preset=preset.key,
        )
        console.print(
            f"[green]Re-logged in:[/green] {account.display_name} "
            f"({account.alias}) as [cyan]{preset.display_name}[/cyan]"
        )

    async def remove_account(self) -> None:
        acc = await self._pick_account("Pick an account to REMOVE (local only):")
        if acc is None:
            return
        confirm = await questionary.confirm(
            f"Remove [{acc.alias}] {acc.phone} locally? "
            "(Session on Telegram stays valid; use 'Logout' to revoke.)",
            default=False,
        ).ask_async()
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            return
        removed = self.manager.store.remove(acc.phone, delete_session=True)
        console.print(f"[green]Removed[/green] {removed.alias} ({removed.phone}).")

    async def logout(self) -> None:
        acc = await self._pick_account("Pick an account to LOGOUT (revoke session):")
        if acc is None:
            return
        confirm = await questionary.confirm(
            f"Revoke session for [{acc.alias}] {acc.phone}? "
            "This signs the account out on Telegram's side.",
            default=False,
        ).ask_async()
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            return
        await self.manager.auth.logout(acc)
        console.print(f"[green]Logged out[/green] {acc.alias}.")

    # ---- shared sub-flows ----------------------------------------------
    async def _list_chats(self, acc: Account) -> None:
        async def _action(client, _acc):
            from telethon.tl.types import Channel, Chat, Megagroup
            dialogs = await client.get_dialogs()
            chats = []
            for d in dialogs:
                entity = d.entity
                if hasattr(entity, "megagroup"):
                    if entity.megagroup:
                        chat_type = "Group"
                    elif entity.broadcast:
                        chat_type = "Channel"
                    else:
                        chat_type = "Group"
                elif hasattr(entity, "participants_count"):
                    chat_type = "Group"
                else:
                    continue
                chats.append({
                    "name": getattr(entity, "title", "?"),
                    "username": getattr(entity, "username", None),
                    "type": chat_type,
                    "members": getattr(entity, "participants_count", None),
                })
            return chats

        chats = await self.manager.run_on(acc.phone, _action)
        if not chats:
            console.print("[dim]No groups or channels found.[/dim]")
            return
        table = Table(title=f"Groups & Channels ({len(chats)})", show_lines=False)
        table.add_column("#", justify="right", style="dim")
        table.add_column("Name")
        table.add_column("Username")
        table.add_column("Type", justify="center")
        table.add_column("Members", justify="right")
        for i, c in enumerate(chats, 1):
            table.add_row(
                str(i),
                c["name"],
                f"@{c['username']}" if c["username"] else "-",
                c["type"],
                str(c["members"]) if c["members"] else "-",
            )
        console.print(table)


    async def _botfather(self, acc: Account) -> None:
        """Interactive BotFather session — send commands and see responses."""
        import asyncio as _aio

        COMMANDS = [
            "/mybots",
            "/newbot",
            "/setname",
            "/setdescription",
            "/setabouttext",
            "/setuserpic",
            "/setcommands",
            "/deletebot",
            "/token",
            "/revoke",
            "/setinline",
            "/setinlinegeo",
            "/setinlinefeedback",
            "/setjoingroups",
            "/setprivacy",
            "/myapps",
            "/newapp",
            "/listapps",
            "/editapp",
            "/deleteapp",
            "/cancel",
            "/help",
        ]

        cmd = await questionary.select(
            "BotFather command:", choices=[
                Choice(title=c, value=c) for c in COMMANDS
            ] + [Choice(title="Custom command...", value="_custom"),
                 Choice(title="Back", value=None)],
        ).ask_async()
        if cmd is None:
            return
        if cmd == "_custom":
            cmd = await _ask_text("Enter command/message to send to BotFather:")

        async def _action(client, _acc):
            await client.send_message("@BotFather", cmd)
            await _aio.sleep(1.5)  # wait for BotFather reply
            msgs = await client.get_messages("@BotFather", limit=1)
            if msgs and msgs[0].sender_id == 93372553:  # BotFather's user_id
                return msgs[0].text
            return "(no response yet)"

        console.print(f"[dim]Sending to BotFather: {cmd}[/dim]")
        response = await self.manager.run_on(acc.phone, _action)
        console.print(Panel.fit(response or "(empty)", title="BotFather", border_style="cyan"))

        # Continue conversation loop
        while True:
            follow = await _ask_text(
                "Reply to BotFather (empty to stop):", default=""
            )
            if not follow:
                break

            async def _reply(client, _acc, msg=follow):
                await client.send_message("@BotFather", msg)
                await _aio.sleep(1.5)
                msgs = await client.get_messages("@BotFather", limit=1)
                if msgs and msgs[0].sender_id == 93372553:
                    return msgs[0].text
                return "(no response yet)"

            response = await self.manager.run_on(acc.phone, _reply)
            console.print(Panel.fit(response or "(empty)", title="BotFather", border_style="cyan"))


    async def _edit_name(self, acc: Account) -> None:
        first = await _ask_text(
            f"First name [{acc.first_name}]:",
            default=acc.first_name,
            validate=lambda s: bool(s and s.strip()),
        )
        last = await _ask_text(
            f"Last name (leave empty to clear) [{acc.last_name}]:",
            default=acc.last_name,
        )

        async def _action(client, _acc):
            from telethon.tl.functions.account import UpdateProfileRequest
            await client(UpdateProfileRequest(first_name=first.strip(), last_name=last.strip()))
            return f"{first.strip()} {last.strip()}".strip()

        result = await self.manager.run_on(acc.phone, _action)
        console.print(f"[green]Name updated:[/green] {result}")

    async def _edit_bio(self, acc: Account) -> None:
        bio = await _ask_text("New bio (max 70 chars, empty to clear):", default="")

        async def _action(client, _acc):
            from telethon.tl.functions.account import UpdateProfileRequest
            await client(UpdateProfileRequest(about=bio.strip()))
            return bio.strip() or "(cleared)"

        result = await self.manager.run_on(acc.phone, _action)
        console.print(f"[green]Bio updated:[/green] {result}")

    async def _edit_username(self, acc: Account) -> None:
        current = acc.username or ""
        username = await _ask_text(
            f"New username (without @, empty to remove) [{current}]:",
            default=current,
        )

        async def _action(client, _acc):
            from telethon.tl.functions.account import UpdateUsernameRequest
            await client(UpdateUsernameRequest(username=username.strip()))
            return f"@{username.strip()}" if username.strip() else "(removed)"

        result = await self.manager.run_on(acc.phone, _action)
        console.print(f"[green]Username updated:[/green] {result}")

    async def _show_me(self, accounts: List[Account]) -> None:
        results = await self.manager.run_on_all(
            _action_get_me, accounts=accounts
        )
        _render_broadcast_table(results, value_header="Profile")

    async def _send_message(self, *, accounts: List[Account]) -> None:
        target = await _ask_text(
            "Target (username, @chat, user_id, or phone):",
            validate=lambda s: bool(s and s.strip()),
        )
        text = await _ask_text(
            "Message text:", validate=lambda s: bool(s and s.strip())
        )
        confirm = await questionary.confirm(
            f"Send to {target!r} from {len(accounts)} account(s)?",
            default=True,
        ).ask_async()
        if not confirm:
            return
        results = await self.manager.send_message(target, text, accounts=accounts)
        _render_broadcast_table(results, value_header="Msg ID")

    async def _pick_account(self, prompt: str) -> Optional[Account]:
        accounts = self.manager.list_accounts()
        if not accounts:
            console.print("[dim]No accounts registered yet.[/dim]")
            return None
        choices = [
            Choice(title=f"{a.alias}  ({a.phone})  {a.display_name}", value=a.phone)
            for a in accounts
        ]
        choices.append(Choice(title="Cancel", value=None))
        picked = await questionary.select(prompt, choices=choices).ask_async()
        if picked is None:
            return None
        return self.manager.get_account(picked)

    async def _pick_preset(
        self, prompt: str, *, default_key: str = "random"
    ) -> Optional[DevicePreset]:
        """Pick a device preset."""
        presets = list_presets()
        choices: List[Choice] = []
        for p in presets:
            marker = " ← current" if p.key == default_key else ""
            choices.append(Choice(title=f"{p.display_name}{marker}", value=p.key))
        choices.append(Choice(title="Cancel", value=None))

        picked_key = await questionary.select(
            prompt, choices=choices, default=default_key
        ).ask_async()
        if picked_key is None:
            return None

        if not self.manager.config.has_own_api:
            console.print(
                Panel.fit(
                    "[yellow]TELEGRAM_API_ID / TELEGRAM_API_HASH required in "
                    ".env (from https://my.telegram.org/apps).[/yellow]",
                    title="Missing credentials",
                    border_style="yellow",
                )
            )
            return None

        # Resolve (random picks a concrete device here)
        preset = get_preset(picked_key)
        return preset

    async def _pick_accounts(self, prompt: str) -> List[Account]:
        accounts = self.manager.list_accounts()
        if not accounts:
            console.print("[dim]No accounts registered yet.[/dim]")
            return []
        choices = [
            Choice(title=f"{a.alias}  ({a.phone})  {a.display_name}", value=a.phone)
            for a in accounts
        ]
        picked = await questionary.checkbox(prompt, choices=choices).ask_async()
        if not picked:
            return []
        return [self.manager.get_account(p) for p in picked]

    # ---- helpers --------------------------------------------------------
    @staticmethod
    def _suggest_alias(phone: str) -> str:
        digits = "".join(c for c in phone if c.isdigit())
        return f"acc_{digits[-4:] or 'new'}"

    def _print_banner(self) -> None:
        n = len(self.manager.list_accounts())
        console.print(
            Panel.fit(
                "[bold cyan]Telegram Manager[/bold cyan]\n"
                f"Registered accounts: [bold]{n}[/bold]\n"
                "[dim]Tip: run with --debug for verbose logs.[/dim]",
                border_style="cyan",
            )
        )

    # Dispatch table (set after methods exist to avoid forward references).
    _dispatch = {
        "add": add_account,
        "list": list_accounts,
        "health": health_check,
        "single": single_action,
        "multi": multi_action,
        "relogin": relogin,
        "remove": remove_account,
        "logout": logout,
    }


# ---------------------------------------------------------------------------
# Top-level action helpers (so we can reuse them in tests)
# ---------------------------------------------------------------------------
async def _action_get_me(client, account):
    me = await client.get_me()
    return (
        f"{getattr(me, 'first_name', '')} {getattr(me, 'last_name', '') or ''} "
        f"@{getattr(me, 'username', None) or '-'}  id={getattr(me, 'id', '?')}"
    ).strip()


# ---------------------------------------------------------------------------
# Prompt helpers (wrap questionary -> raise UserCancelledError on abort)
# ---------------------------------------------------------------------------
async def _ask_text(
    message: str,
    *,
    default: str = "",
    validate=None,
) -> str:
    answer = await questionary.text(
        message, default=default, validate=validate
    ).ask_async()
    if answer is None:  # Ctrl+C
        raise UserCancelledError("Aborted by user.")
    return answer.strip()


async def _ask_password(message: str) -> str:
    answer = await questionary.password(message).ask_async()
    if answer is None:
        raise UserCancelledError("Aborted by user.")
    return answer


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def _render_accounts_table(accounts: List[Account]) -> None:
    table = Table(title=f"Accounts ({len(accounts)})", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Alias", style="cyan")
    table.add_column("Phone")
    table.add_column("Name")
    table.add_column("Username")
    table.add_column("2FA", justify="center")
    table.add_column("Device", overflow="fold")
    table.add_column("Last login")
    for i, a in enumerate(accounts, 1):
        preset = get_preset_static(a.device_preset)
        table.add_row(
            str(i),
            a.alias,
            a.phone,
            a.display_name,
            f"@{a.username}" if a.username else "-",
            "✓" if a.is_2fa else "-",
            preset.display_name,
            (a.last_login_at or "-").split(".")[0].replace("T", " "),
        )
    console.print(table)


def _render_broadcast_table(
    results: List[BroadcastResult], *, value_header: str = "Value"
) -> None:
    table = Table(title="Results", show_lines=False)
    table.add_column("Alias", style="cyan")
    table.add_column("Phone")
    table.add_column("OK", justify="center")
    table.add_column(value_header, overflow="fold")
    table.add_column("Error", overflow="fold", style="red")
    for r in results:
        table.add_row(
            r.account.alias,
            r.account.phone,
            "✓" if r.success else "✗",
            str(r.value) if r.success and r.value is not None else "",
            r.error or "",
        )
    console.print(table)
    ok = sum(1 for r in results if r.success)
    console.print(
        f"[bold]Summary:[/bold] [green]{ok}[/green]/{len(results)} succeeded"
    )
