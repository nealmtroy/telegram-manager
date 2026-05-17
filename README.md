# Telegram Manager

Manage multiple Telegram accounts from a single terminal-based tool, including
accounts with Two-Step Verification (2FA) enabled.

Built on top of [Telethon](https://docs.telethon.dev/), with an interactive
CLI powered by [Rich](https://rich.readthedocs.io/) and
[Questionary](https://questionary.readthedocs.io/).

License: GPL-3.0 — see [LICENSE](LICENSE).

---

## Features

- Login multiple accounts — each stored as its own Telethon session file
- Automatic 2FA detection — prompts for cloud password with hint when needed
- Device spoofing — randomized or fixed device fingerprint per account
  (iPhone, Samsung, Pixel, Desktop) so sessions look like real devices
- Single-account mode — run actions on one selected account
- Multi-account mode — broadcast actions concurrently across many accounts
- Health check — probe all sessions in parallel to spot revoked logins
- Re-login / logout — refresh a session, switch device, or revoke it
- Structured logging — colored console + rotating file log (5 MB x 3)
- Robust error handling — typed exceptions for every Telethon error

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

### 4. Get Telegram API credentials

1. Go to https://my.telegram.org/apps and sign in.
2. Create a new application (any title/platform works).
3. Copy `api_id` (number) and `api_hash` (32-char string).
4. Tip: set the app title to "Telegram iOS" and platform to "iOS" if you
   want sessions to appear as the official Telegram app.

### 5. Configure .env

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
LOG_LEVEL=INFO
OWNER_IDS=123456789,987654321
```

`OWNER_IDS` is a comma/space-separated list of Telegram user IDs allowed to run
owner-only commands such as `/gift <telegram_user_id>`.

Never commit `.env` or anything in `sessions/`. Both are in `.gitignore`.

---

## Usage

### Interactive menu

```bash
python main.py
```

Menu options:

```
Add / login account
List accounts
Health check (all)
Single-account action
Multi-account action
Re-login account
Remove account
Logout (revoke session)
Exit
```

### Command-line flags

| Flag          | Purpose                                                          |
|---------------|------------------------------------------------------------------|
| `--debug`     | Verbose logs, including Telethon internals                       |
| `--list`      | Print the account table and exit                                 |
| `--health`    | Run a health check and exit (exit code 1 if any account fails)   |
| `--env PATH`  | Use an alternate .env file                                       |

```bash
python main.py --debug
python main.py --list
python main.py --health
```

---

## First-time walkthrough

### Adding an account (no 2FA)

1. Select "Add / login account"
2. Enter phone in international form: `+628123456789`
3. Pick a short alias (e.g. `main`)
4. Pick a device preset (or leave as Random)
5. Enter the 5-digit login code
6. Done — session saved to `sessions/628123456789.session`

### Adding an account with 2FA

Same flow, but after the login code the CLI will prompt:

```
Account +62... has 2FA enabled; prompting for password.
Enter your 2FA cloud password (hint: my-dog): ********
```

Wrong password returns you to the menu to retry.

### Single-account action

1. Pick "Single-account action" then select the account
2. Choose "Get profile" or "Send a message"
3. For send-message: provide target (@username, user_id, phone, or `me`)

### Broadcasting across accounts

1. Pick "Multi-account action"
2. Toggle accounts with space, confirm with Enter
3. Pick the action — each account runs concurrently (4 parallel by default)
4. Results table shows success/failure per account

---

## Device presets

Each account gets a device fingerprint that shows up in Telegram's
"Active Sessions" panel. The default is **Random** — a different device
is picked from a pool on each login.

Available presets:

| Key                  | Device shown                              |
|----------------------|-------------------------------------------|
| random (default)     | Random pick from 19 devices               |
| iphone_17_pro_max    | iPhone 17 Pro Max, iOS 19.0               |
| iphone_17_pro        | iPhone 17 Pro, iOS 19.0                   |
| iphone_16_pro_max    | iPhone 16 Pro Max, iOS 18.5               |
| iphone_16_pro        | iPhone 16 Pro, iOS 18.5                   |
| iphone_15_pro_max    | iPhone 15 Pro Max, iOS 18.5               |
| iphone_15_pro        | iPhone 15 Pro, iOS 18.5                   |
| samsung_s25_ultra    | Samsung Galaxy S25 Ultra, Android 15      |
| samsung_s24_ultra    | Samsung Galaxy S24 Ultra, Android 14      |
| pixel_9_pro          | Google Pixel 9 Pro, Android 15            |
| desktop_windows      | PC 64bit, Windows 11                      |
| desktop_macos        | MacBook Pro, macOS 15                     |

All presets use your own api_id/api_hash from `.env`. The app name shown
in Active Sessions is whatever you set at my.telegram.org (tip: set it to
"Telegram iOS" for maximum stealth).

Use "Re-login account" to switch an account's device preset.

---

## Project layout

```
telegram-manager/
├── .env.example
├── .gitignore
├── main.py                  # CLI entry point
├── requirements.txt
├── README.md
├── LICENSE
├── sessions/                # .session files (gitignored)
├── logs/                    # rotating logs (gitignored)
├── accounts.json            # account metadata (gitignored)
└── telegram_manager/
    ├── __init__.py
    ├── config.py            # .env loader
    ├── logger.py            # Rich console + rotating file handler
    ├── exceptions.py        # Typed domain errors
    ├── device_presets.py    # Device fingerprint catalog + randomizer
    ├── storage.py           # accounts.json management
    ├── auth.py              # Login flow + 2FA detection
    ├── manager.py           # Multi-account orchestration
    └── cli.py               # Interactive menu
```

---

## Debugging

Run with `--debug` for full Telethon transport-level logs in both console
and `logs/telegram_manager.log`.

Common issues:

| Symptom | Fix |
|---------|-----|
| Missing required env var | Create `.env` with API_ID and API_HASH |
| Flood wait: retry in N seconds | Rate limited — wait it out |
| Session no longer authorized | Session revoked — use Re-login |
| 2FA password incorrect | Wrong cloud password — check hint |
| reCAPTCHA wall hit | See below |

### reCAPTCHA wall

If you see "Telegram is demanding a reCAPTCHA challenge", it means either:

- The phone number has no Telegram account yet (register via official app first)
- Your api_id is being rate-limited for this number

Workarounds:

1. Register the number via the official Telegram app first, then login here
2. Wait a few hours and retry
3. Try from a different IP

---

## Security notes

- Session files and `accounts.json` are equivalent to logged-in devices.
  Never push them to Git.
- Use "Logout" to revoke sessions you don't need — it calls Telegram's
  `log_out` and deletes the local file.
- `.gitignore` covers `.env`, `sessions/`, `logs/`, and `accounts.json`.

---

## Extending

The manager's broadcast primitive is generic:

```python
from telegram_manager.manager import TelegramManager
from telegram_manager.config import load_config

async def my_action(client, account):
    await client.send_message("me", f"Hello from {account.alias}!")
    return "ok"

cfg = load_config()
mgr = TelegramManager(cfg)
results = await mgr.run_on_all(my_action, concurrency=3)
for r in results:
    print(r.account.alias, r.success, r.error)
```

Any async callable `(client, account) -> Any` works.

---

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
