# Telegram Manager

Manage multiple Telegram accounts from a single terminal-based tool, including
accounts with **Two-Step Verification (2FA)** enabled.

Built on top of [Telethon](https://docs.telethon.dev/), with an interactive
CLI powered by [Rich](https://rich.readthedocs.io/) and
[Questionary](https://questionary.readthedocs.io/).

> **License:** GPL-3.0 — see [LICENSE](LICENSE).

---

## ✨ Features

- 🔐 **Login multiple accounts** — store each account as its own Telethon
  session file under `sessions/`.
- 🛡️ **Automatic 2FA detection** — when Telegram requires a cloud password,
  the CLI detects it (`SessionPasswordNeededError`) and prompts you, including
  the password hint if available.
- 📱 **Device presets (impersonate Telegram iOS / Android / Desktop / macOS)** —
  each account can be assigned a device profile so the session appears as the
  real Telegram app on Telegram's side. See the **Device appearance** section
  below for the ToS warning.
- 👤 **Single-account mode** — run actions (`get_me`, send message, …) on one
  selected account.
- 📣 **Multi-account mode** — run the same action concurrently across many
  accounts with per-account success/error reporting.
- 🫀 **Health check** — probe every stored session in parallel to spot
  revoked/expired logins.
- ♻️ **Re-login / logout** — refresh a session, switch its device preset, or
  revoke it on Telegram's side.
- 🧾 **Structured logging** — colorized console output plus rotating file log
  in `logs/telegram_manager.log` (5 MB × 3). `--debug` unlocks Telethon's
  internal logs too.
- 🌀 **Robust error handling** — every Telethon exception is mapped to a typed
  error (`InvalidPhoneError`, `InvalidCodeError`, `TwoFARequiredError`,
  `InvalidPasswordError`, `PhoneBannedError`, `FloodError`, …).

---

## 📦 Installation

### 1. Clone

```bash
git clone https://github.com/nealmtroy/telegram-manager.git
cd telegram-manager
```

### 2. Create a virtual environment (recommended)

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

> Requires **Python 3.9+**.

### 4. Get Telegram API credentials

1. Go to <https://my.telegram.org/apps> and sign in with the phone number you
   intend to manage.
2. Fill in the "Create new application" form — any values work (e.g. app
   title `telegram-manager`, platform `Desktop`).
3. Copy **`api_id`** (a number) and **`api_hash`** (32-char string).

### 5. Configure `.env`

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
LOG_LEVEL=INFO
```

> ⚠️ **Never commit `.env` or anything in `sessions/`.** They contain
> credentials that grant full access to your accounts. Both are already in
> `.gitignore`.

---

## 🚀 Usage

### Interactive menu

```bash
python main.py
```

You'll see:

```
╭─ Telegram Manager (accounts: 0) ─╮
│  ➕  Add / login account         │
│  📋  List accounts               │
│  🫀  Health check (all)          │
│  👤  Single-account action       │
│  📣  Multi-account action        │
│  ♻️   Re-login account           │
│  🗑   Remove account             │
│  🚪  Logout (revoke session)    │
│  ❌  Exit                        │
╰──────────────────────────────────╯
```

### Command-line flags

| Flag          | Purpose                                                         |
|---------------|-----------------------------------------------------------------|
| `--debug`     | Verbose logs, including Telethon internals.                     |
| `--list`      | Print the account table and exit (non-interactive).             |
| `--health`    | Run a health check and exit. Exit code `1` if any account fails.|
| `--env PATH`  | Use an alternate `.env` file.                                   |

Examples:

```bash
python main.py --debug
python main.py --list
python main.py --health --env path/to/other.env
```

---

## 🧭 Typical first-time walkthrough

### Adding an account without 2FA

1. Select **"Add / login account"**.
2. Enter phone in international form, e.g. `+628123456789`.
3. Pick a short alias (e.g. `main`). A default is suggested.
4. Optionally request an SMS code.
5. Enter the 5-digit login code.
6. Done — the session file is saved to `sessions/628123456789.session`.

### Adding an account **with 2FA**

Same flow, but after step 5 the CLI will show:

```
Account +62… has 2FA enabled; prompting for password.
? Enter your 2FA cloud password (hint: my-dog): ********
```

If the hint is missing, the prompt just says
`Enter your 2FA cloud password:`. A wrong password raises
`InvalidPasswordError` and you're returned to the main menu to try again.

### Running an action on a single account

1. Pick **"Single-account action"** → select the account.
2. Choose *"Get profile"* (quick auth sanity check) or *"Send a message"*.
3. For send-message, provide a target (`@username`, numeric user_id, a phone,
   or `me` for your own Saved Messages) and the text.

### Broadcasting across accounts

1. Pick **"Multi-account action"**.
2. Use `space` to toggle which accounts to include, then `Enter`.
3. Pick the action. Each account runs concurrently (default 4 in parallel).
4. You get a results table with ✓/✗ per account.

---

## 📁 Project layout

```
telegram-manager/
├── .env.example
├── .gitignore
├── main.py                  # CLI entry point
├── requirements.txt
├── README.md
├── LICENSE
├── sessions/                # Telethon .session files (gitignored)
├── logs/                    # rotating log files (gitignored)
├── accounts.json            # account metadata (gitignored)
└── telegram_manager/
    ├── __init__.py
    ├── config.py            # .env loader → Config dataclass
    ├── logger.py            # Rich console + rotating file handler
    ├── exceptions.py        # Typed domain errors
    ├── storage.py           # accounts.json + session file management
    ├── auth.py              # Login flow incl. 2FA detection
    ├── manager.py           # Multi-account orchestration
    └── cli.py               # Interactive menu (questionary + rich)
```

---

## 🐞 Debugging

- Start with `python main.py --debug`. Both the console and
  `logs/telegram_manager.log` will show DEBUG-level detail, including the
  low-level Telethon transport layer.
- Logs rotate at 5 MB, keeping 3 backups
  (`telegram_manager.log.1`, `.2`, `.3`).
- If the CLI crashes before the logger is set up, the exception will be
  printed to stderr.

Common issues:

| Symptom                                                      | Likely cause / fix                                         |
|--------------------------------------------------------------|-------------------------------------------------------------|
| `Configuration error: Missing required env var …`            | You didn't create `.env` or didn't fill both `TELEGRAM_API_ID` and `TELEGRAM_API_HASH`. |
| `Flood wait: please retry in N seconds`                      | You hit Telegram's rate limit — wait it out.                |
| `Session is no longer authorized (re-login required).`       | Another device revoked the session. Use **Re-login**.       |
| `The 2FA password is incorrect.`                             | Wrong cloud password. Retry; use the hint if shown.         |
| `This phone number has no Telegram account.`                 | The number has never registered on Telegram.                |

---

## 📱 Device appearance (impersonate Telegram iOS / Android / Desktop)

Each account can be assigned a **device preset** that controls how the session
shows up in Telegram's "Active Sessions" panel:

```
📱 iPhone 15 Pro · Telegram iOS 10.12.0
   iOS 17.5.1 · Sept 5, 2024
   Jakarta, Indonesia · 192.168.x.x
```

Presets ship in two flavors:

| Preset family                                     | `api_id` source                | App name shown |
|---------------------------------------------------|--------------------------------|----------------|
| `default`                                         | Your `.env`                    | Whatever you registered at my.telegram.org |
| `iphone_15_pro`, `iphone_14`, `iphone_se`         | Official Telegram iOS (leaked) | **Telegram iOS** |
| `samsung_s24`, `samsung_s22`, `pixel_8`, `xiaomi_13` | Official Telegram Android (leaked) | **Telegram Android** |
| `desktop_windows`, `desktop_linux`                | Official Telegram Desktop (leaked) | **Telegram Desktop** |
| `desktop_macos`                                   | Official Telegram macOS (leaked) | **Telegram macOS** |

You'll pick one when you add an account (and can change it via **Re-login**).

### ⚠️ ToS warning (read before using official presets)

All presets except `default` use **leaked official Telegram `api_id` /
`api_hash` pairs**. Consequences:

- **This violates Telegram's Terms of Service.** Using official credentials
  that aren't registered to you is against ToS, even though the pairs have
  been publicly circulating for years.
- **Risk of flagging / ban.** Telegram actively monitors for abuse on the
  official `api_id`s. Accounts exhibiting bot-like traffic (rapid messaging,
  broadcasts, many accounts from the same IP) are more likely to be
  restricted or banned.
- **Credentials can be rotated.** Telegram occasionally disables leaked
  pairs. If login suddenly fails with `ApiIdInvalidError`, that pair has
  been killed and you need to either switch to `default` or wait for a new
  public pair.
- You accept these risks by choosing an official preset. The CLI asks you
  to confirm with an `I understand the risk, continue?` prompt each time.

### Safer path: your own `api_id`

If you prefer the ToS-compliant path, stick with the `default` preset:

1. Go to <https://my.telegram.org/apps>, register an app.
   - App title: anything (e.g. *"My Telegram Client"*).
   - Platform: pick Desktop/Android/iOS based on how you want it to appear.
2. Put `api_id` / `api_hash` into `.env`.
3. Choose the `default` preset when logging in.

The session will still show **your** app name on Telegram's server side
(not "Telegram iOS"), but the `device_model` / `system_version` /
`app_version` you pass from the default preset are anything you want.

### Switching device presets per account

You can run some accounts on `default` (your own api_id, ToS-safe) and
others on official presets (risk but native appearance). Each account
stores its own preset in `accounts.json`.

Use **Re-login account** in the menu to switch an account's preset without
losing the session (Telethon re-authenticates and keeps the same login).

---

## 🔒 Security notes

- Session files (`sessions/*.session`) and `accounts.json` are equivalent to
  logged-in devices. Back them up privately, never push them to Git.
- Use **"Logout"** to revoke a session you don't need anymore — it calls
  Telegram's `log_out` and deletes the local file.
- The default `.gitignore` already covers `.env`, `sessions/`, `logs/`,
  `accounts.json`, and common Python/IDE junk.

---

## 🛠 Extending

The manager's broadcast primitive is generic:

```python
from telegram_manager.manager import TelegramManager
from telegram_manager.config import load_config

async def star_saved(client, account):
    await client.send_message("me", f"Hello from {account.alias}!")
    return "ok"

cfg = load_config()
mgr = TelegramManager(cfg)
results = await mgr.run_on_all(star_saved, concurrency=3)
for r in results:
    print(r.account.alias, r.success, r.error)
```

Any async callable `(client, account) -> Any` can be used — that's how the
built-in "get profile" and "send message" flows are implemented.

---

## 📝 License

GNU General Public License v3.0. See [LICENSE](LICENSE) for full text.
