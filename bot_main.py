"""Entry point for the Telegram Manager Bot (remote management via Telegram)."""
import asyncio

from telegram_manager.bot import run_bot

if __name__ == "__main__":
    asyncio.run(run_bot())
