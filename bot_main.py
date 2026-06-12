"""Entry point for the Telegram Manager Bot (remote management via Telegram)."""
import argparse
import asyncio
import sys
from typing import Optional

from telegram_manager.bot import run_bot
from telegram_manager.config import load_config
from telegram_manager.logger import setup_logger


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="telegram-manager-bot",
        description="Run the Telegram Manager Bot interface.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging (also shows Telethon internals).",
    )
    parser.add_argument(
        "--env",
        type=str,
        default=None,
        help="Path to an alternate .env file.",
    )
    return parser.parse_args(argv)


async def main() -> int:
    args = _parse_args()

    # Load config to locate log directory and read global log level
    env_path = None
    if args.env:
        from pathlib import Path
        env_path = Path(args.env)

    config = load_config(env_file=env_path)

    # Initialize our custom dual-sink logging (console + file)
    setup_logger(
        log_dir=config.logs_dir,
        level=config.log_level,
        debug=args.debug,
    )

    await run_bot()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
