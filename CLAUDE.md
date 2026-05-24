# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Set up a virtual environment:
  - Windows PowerShell: `python -m venv venv; .\venv\Scripts\Activate.ps1`
  - macOS/Linux: `python -m venv venv && source venv/bin/activate`
- Install dependencies: `pip install -r requirements.txt`
- Create local configuration: copy `.env.example` to `.env` and fill the variables needed for the mode being run.
- Run the terminal CLI: `python main.py`
- Run the CLI with debug logging: `python main.py --debug`
- List registered local accounts: `python main.py --list`
- Run local CLI account health checks: `python main.py --health`
- Use an alternate env file for CLI runs: `python main.py --env path\to\.env`
- Run the Telegram bot interface: `python bot_main.py`
- Syntax-check the project: `python -m compileall main.py bot_main.py telegram_manager`

There is no committed test suite, pytest config, packaging config, or lint config in the current repository. Do not document or rely on `pytest`, `ruff`, `mypy`, or package build commands unless those files are added.

## Runtime configuration and generated state

- Python 3.9+ is required.
- Core dependencies are Telethon, Rich, Questionary, python-dotenv, cryptg, aiogram, and supabase.
- `.env.example` documents supported environment variables.
- CLI/local mode uses `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, optional legacy proxy variables, and `LOG_LEVEL`.
- Bot mode requires `BOT_TOKEN`, Supabase credentials (`SUPABASE_URL`, `SUPABASE_KEY`), and Telegram API credentials.
- Bot mode supports multiple Telegram API credential pairs through `TELEGRAM_API_CREDENTIALS` and distributes new accounts with `TELEGRAM_API_ACCOUNT_LIMIT`.
- Bot broadcasts are bounded by `BROADCAST_PER_ADMIN_CONCURRENCY` and `BROADCAST_GLOBAL_CONCURRENCY`.
- Bot mode supports proxy pools through `TELEGRAM_PROXIES_FILE` (preferred for large pools) or `TELEGRAM_PROXIES`; `proxies.txt` is gitignored and should be uploaded/deployed separately from source control.
- Bot mode supports owner runtime alerts through `OWNER_IDS` / `OWNER_ID` with duplicate cooldown via `OWNER_ERROR_ALERT_COOLDOWN_SECONDS`.
- Bot account health checks run automatically on a safe interval (`AUTO_HEALTH_CHECK_INTERVAL_SECONDS`, default 43200 seconds / twice daily) and only notify owners on errors.
- Generated local state includes `.env`, `sessions/`, `accounts.json`, `broadcast_lists.json`, `logs/`, proxy files, and Telethon `*.session` files. These are intentionally gitignored because they contain credentials or logged-in session material.

## Architecture overview

This repository has two user interfaces over Telegram account management:

1. **Terminal CLI**: `main.py` loads config/logging, builds `TelegramManager`, then either runs non-interactive flags or starts `telegram_manager.cli.InteractiveCLI`.
2. **Telegram bot UI**: `bot_main.py` starts `telegram_manager.bot.run_bot()`, an aiogram polling bot with button/text-driven conversation state and Supabase-backed persistence.

The shared local CLI core is organized around these modules:

- `telegram_manager.config` resolves project paths, loads `.env`, constructs `Config`, creates `sessions/` and `logs/`, parses API credential pools, broadcast concurrency limits, and proxy pool settings.
- `telegram_manager.logger` configures Rich console logging and rotating file logs.
- `telegram_manager.storage` stores local CLI account metadata in `accounts.json` and broadcast target lists in `broadcast_lists.json`; Telethon session files live separately in `sessions/`.
- `telegram_manager.auth.AuthFlow` owns interactive Telethon login, 2FA handling, re-login, logout, and session probing. It writes session/account state through `AccountStore`.
- `telegram_manager.manager.TelegramManager` is the local CLI facade. It constructs short-lived Telethon clients per action, checks authorization, and provides single-account and multi-account/broadcast execution helpers.
- `telegram_manager.cli.InteractiveCLI` contains the Rich/Questionary menu flows and delegates account operations to `TelegramManager`; broadcast lists in this mode are local JSON-backed lists.
- `telegram_manager.device_presets` defines device fingerprints used when creating Telethon clients.
- `telegram_manager.exceptions` contains typed domain errors that CLI/auth/manager code translate from Telethon exceptions.

The bot path is separate from the local JSON/session-file path:

- `telegram_manager.bot` contains the aiogram router, per-user in-memory conversation state, login flow using Telethon `StringSession`, account editing, OTP lookup, saved message management, group list management, transfer, VIP/owner handling, bounded parallel broadcast loops, owner runtime alerts, and automatic account health checks.
- `telegram_manager.db` is the Supabase storage layer for bot mode. It stores admins, managed accounts, string sessions, broadcast lists, saved messages, language preferences, VIP status, and optional account routing indexes for API credentials/proxies.
- `telegram_manager.i18n` provides bot UI strings and in-memory language selection helpers; persistent language preference is read/written through `telegram_manager.db`.

When modifying behavior, keep the two persistence models distinct: CLI mode uses local JSON plus `.session` files, while bot mode uses Supabase plus Telethon `StringSession` values.

## Security-sensitive behavior

- Treat Telethon session files, StringSession values, `.env`, Supabase keys, bot tokens, proxy credentials, `accounts.json`, and `broadcast_lists.json` as sensitive.
- Logout/revoke operations call Telegram APIs and affect live account sessions; distinguish them from local-only remove operations.
- Broadcast, join, login, and health-check flows contact Telegram and can hit rate limits (`FloodWaitError`) or account restrictions. Preserve existing explicit delay/rate-limit handling when changing these paths.
- Never commit `.env`, `proxies.txt`, `*.proxies.txt`, `sessions/`, `*.session`, `accounts.json`, or generated logs.
