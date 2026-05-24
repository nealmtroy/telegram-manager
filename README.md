# Telegram Manager

Manage multiple Telegram accounts through a terminal CLI or a Telegram bot UI.
The bot mode stores accounts in Supabase using Telethon `StringSession` values and
supports scalable multi-account broadcasting.

Built on top of [Telethon](https://docs.telethon.dev/), with a terminal CLI powered
by [Rich](https://rich.readthedocs.io/) / [Questionary](https://questionary.readthedocs.io/)
and a Telegram bot interface powered by aiogram.

License: GPL-3.0 — see [LICENSE](LICENSE).

---

## Features

- Login and manage multiple Telegram accounts
- Bot-mode account storage in Supabase with Telethon string sessions
- Automatic 2FA detection during login
- Device spoofing with randomized or fixed device fingerprints
- Saved broadcast texts, including multi-random text mode
- Saved group/broadcast target lists with pasted list parsing
- Bounded parallel broadcasts across managed accounts
- Multi `api_id` / `api_hash` credential pool for large account pools
- Proxy pool support through `proxies.txt` or inline environment variables
- Owner/VIP controls, including `/gift`
- Owner runtime error alerts with duplicate cooldown
- Automatic bot-mode account health checks twice daily, alerting owners only on errors
- Terminal CLI remains available for local JSON/session-file workflows

---

## Installation

### 1. Clone

```bash
git clone https://github.com/nealmtroy/telegram-manager.git
cd telegram-manager
```

### 2. Virtual environment

```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS / Linux:
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

Requires Python 3.9+.

### 4. Configure environment

```bash
cp .env.example .env
```

Minimum bot-mode variables:

```dotenv
BOT_TOKEN=123456:ABC-DEF
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=your-supabase-key
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
OWNER_IDS=123456789,987654321
LOG_LEVEL=INFO
```

For larger deployments, add optional scaling settings:

```dotenv
TELEGRAM_API_CREDENTIALS=111111:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa;222222:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
TELEGRAM_API_ACCOUNT_LIMIT=250
BROADCAST_PER_ADMIN_CONCURRENCY=15
BROADCAST_GLOBAL_CONCURRENCY=150
TELEGRAM_PROXIES_FILE=proxies.txt
OWNER_ERROR_ALERT_COOLDOWN_SECONDS=600
AUTO_HEALTH_CHECK_INTERVAL_SECONDS=43200
```

`OWNER_IDS` is a comma/space-separated list of Telegram user IDs allowed to run
owner-only commands such as `/gift <telegram_user_id>` and receive runtime error
alerts.

Never commit `.env`, session files, Supabase keys, bot tokens, or proxy files.
They are intentionally ignored by `.gitignore`.

---

## Running

### Telegram bot mode

```bash
python bot_main.py
```

The bot UI is button/text-driven. New users default to Indonesian language and can
switch language from the menu.

Bot-mode health checks are automatic. The manual Health Check menu is intentionally
removed; the bot checks accounts every 12 hours by default and sends owner alerts
only when a runtime/account health error occurs.

### Terminal CLI mode

```bash
python main.py
```

Useful CLI flags:

| Flag          | Purpose                                                        |
|---------------|----------------------------------------------------------------|
| `--debug`     | Verbose logs, including Telethon internals                     |
| `--list`      | Print the local account table and exit                         |
| `--health`    | Run a local CLI health check and exit                          |
| `--env PATH`  | Use an alternate `.env` file                                   |

```bash
python main.py --debug
python main.py --list
python main.py --health
```

---

## Supabase schema note

Bot mode stores admins, accounts, broadcast lists, saved messages, language
preferences, and VIP status in Supabase.

For API/proxy assignment persistence, make sure the `accounts` table has these
optional columns:

```sql
alter table accounts
add column if not exists api_credential_index integer,
add column if not exists proxy_index integer;
```

The app can still run without those columns, but new account API/proxy routing is
more stable when they exist.

---

## Proxy pool

For large proxy pools, prefer a `proxies.txt` file and set:

```dotenv
TELEGRAM_PROXIES_FILE=proxies.txt
```

Format: one proxy URL per line. Blank lines and `#` comments are ignored.

```text
socks5://user:pass@host1:1080
socks5://user:pass@host2:1080
http://user:pass@host3:8080
```

`proxies.txt` and `*.proxies.txt` are gitignored. Upload the file to your VPS
separately and restrict permissions, for example:

```bash
chmod 600 proxies.txt
```

Inline proxy pools are also supported:

```dotenv
TELEGRAM_PROXIES=socks5://user:pass@host1:1080;socks5://user:pass@host2:1080
```

Legacy single-proxy variables (`PROXY_TYPE`, `PROXY_HOST`, `PROXY_PORT`,
`PROXY_USER`, `PROXY_PASS`) are still supported.

---

## Broadcast scaling

Bot broadcasts run accounts in parallel with bounded concurrency:

- `BROADCAST_PER_ADMIN_CONCURRENCY` limits active broadcasting accounts for one admin.
- `BROADCAST_GLOBAL_CONCURRENCY` limits active broadcasting accounts across the bot process.

Targets are still sent sequentially per account to reduce rate-limit pressure.
When multiple saved texts are selected, each target send chooses one text randomly.

For large account pools, configure multiple API credentials with
`TELEGRAM_API_CREDENTIALS`. New accounts are distributed across credentials using
`TELEGRAM_API_ACCOUNT_LIMIT` as the soft preferred account count per credential.

---

## Device presets

Each account gets a device fingerprint that shows up in Telegram's Active Sessions
panel. The default is **Random**.

Available preset keys include:

| Key                  | Device shown                         |
|----------------------|--------------------------------------|
| random               | Random pick from the preset pool     |
| iphone_17_pro_max    | iPhone 17 Pro Max                    |
| iphone_17_pro        | iPhone 17 Pro                        |
| iphone_16_pro_max    | iPhone 16 Pro Max                    |
| iphone_16_pro        | iPhone 16 Pro                        |
| iphone_15_pro_max    | iPhone 15 Pro Max                    |
| iphone_15_pro        | iPhone 15 Pro                        |
| samsung_s25_ultra    | Samsung Galaxy S25 Ultra             |
| samsung_s24_ultra    | Samsung Galaxy S24 Ultra             |
| pixel_9_pro          | Google Pixel 9 Pro                   |
| desktop_windows      | PC 64bit, Windows                    |
| desktop_macos        | MacBook Pro, macOS                   |

---

## Project layout

```text
telegram-manager/
├── .env.example
├── .gitignore
├── bot_main.py              # Telegram bot entry point
├── main.py                  # CLI entry point
├── requirements.txt
├── README.md
├── CLAUDE.md
├── sessions/                # local .session files (gitignored)
├── logs/                    # rotating logs (gitignored)
├── accounts.json            # local CLI account metadata (gitignored)
└── telegram_manager/
    ├── __init__.py
    ├── auth.py              # CLI login flow + 2FA handling
    ├── bot.py               # aiogram bot UI and bot-mode orchestration
    ├── cli.py               # terminal interactive menu
    ├── config.py            # .env loader, API/proxy/concurrency config
    ├── db.py                # Supabase storage layer
    ├── device_presets.py    # device fingerprint catalog
    ├── exceptions.py        # typed domain errors
    ├── i18n.py              # bot UI translations
    ├── logger.py            # logging setup
    ├── manager.py           # CLI account operations
    └── storage.py           # local JSON storage for CLI mode
```

---

## Debugging

Run with `--debug` for full Telethon transport-level logs in both console and
`logs/telegram_manager.log`.

Common issues:

| Symptom | Fix |
|---------|-----|
| Missing required env var | Create `.env` and fill the variables needed for the selected mode |
| Flood wait: retry in N seconds | Rate limited — wait it out or lower concurrency |
| Session no longer authorized | Re-login the account |
| 2FA password incorrect | Wrong cloud password — check hint |
| Proxy connection failures | Check proxy URL format, credentials, country, and provider quality |
| Supabase errors | Verify `SUPABASE_URL`, `SUPABASE_KEY`, and table schema |

### reCAPTCHA wall

If Telegram demands a reCAPTCHA challenge, the phone number may need to be
registered through the official app first, or the API credential/proxy may be
rate-limited. Try a cleaner proxy, lower concurrency, or wait before retrying.
