#!/usr/bin/env python3
"""Telegram Manager - CLI entry point.

Usage:
    python main.py            # interactive menu
    python main.py --debug    # verbose logging
    python main.py --list     # non-interactive: list accounts and exit
    python main.py --health   # non-interactive: health check and exit

Setup:
    1.  pip install -r requirements.txt
    2.  cp .env.example .env   (then fill in TELEGRAM_API_ID / TELEGRAM_API_HASH)
    3.  python main.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Optional

from rich.console import Console

from telegram_manager.cli import InteractiveCLI, _render_accounts_table
from telegram_manager.config import load_config
from telegram_manager.exceptions import ConfigError, TelegramManagerError
from telegram_manager.logger import setup_logger
from telegram_manager.manager import TelegramManager

console = Console()


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telegram-manager",
        description="Manage multiple Telegram accounts from your terminal.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging (also shows Telethon internals).",
    )
    parser.add_argument(
        "--list",
        dest="list_only",
        action="store_true",
        help="Print registered accounts and exit.",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Run a health check on all accounts and exit.",
    )
    parser.add_argument(
        "--env",
        type=str,
        default=None,
        help="Path to an alternate .env file.",
    )
    return parser.parse_args(argv)


async def _run_async(args: argparse.Namespace) -> int:
    # ---- Load config & set up logging ---------------------------------------
    try:
        env_path = None
        if args.env:
            from pathlib import Path

            env_path = Path(args.env)
        config = load_config(env_file=env_path)
    except ConfigError as exc:
        console.print(
            f"[red bold]Configuration error:[/red bold] {exc}\n"
            f"[dim]Tip: copy .env.example to .env and fill in your credentials.[/dim]"
        )
        return 2

    log = setup_logger(
        log_dir=config.logs_dir, level=config.log_level, debug=args.debug
    )
    log.debug("Startup args: %s", args)
    log.debug(
        "Config loaded: api_id=%s, sessions_dir=%s, proxy=%s",
        config.api_id,
        config.sessions_dir,
        "on" if config.proxy.enabled else "off",
    )

    manager = TelegramManager(config)

    # ---- Non-interactive modes ---------------------------------------------
    if args.list_only:
        accounts = manager.list_accounts()
        if not accounts:
            console.print("[dim]No accounts registered yet.[/dim]")
        else:
            _render_accounts_table(accounts)
        return 0

    if args.health:
        accounts = manager.list_accounts()
        if not accounts:
            console.print("[dim]No accounts registered yet.[/dim]")
            return 0
        statuses = await manager.health_check()
        bad = [s for s in statuses if not s.ok]
        for s in statuses:
            tag = "[green]OK[/green]" if s.ok else "[red]FAIL[/red]"
            detail = f"  - {s.error}" if s.error else ""
            console.print(f"{tag}  {s.account.alias}  ({s.account.phone}){detail}")
        console.print(
            f"\n[bold]{len(statuses) - len(bad)}/{len(statuses)}[/bold] healthy"
        )
        return 0 if not bad else 1

    # ---- Interactive CLI ---------------------------------------------------
    cli = InteractiveCLI(manager)
    await cli.run()
    return 0


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_run_async(args))
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        return 130
    except TelegramManagerError as exc:
        console.print(f"[red bold]Fatal:[/red bold] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
