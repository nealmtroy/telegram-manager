# Multi-Target Deployment Design

## Goal

Add deployment support for running Telegram Manager on a small DigitalOcean Ubuntu VPS while preserving Railway compatibility and adding an optional Docker path.

## Recommended Runtime

The default production path is Ubuntu VPS with Python virtualenv and systemd. This is the best fit for a 1 vCPU / 512 MB RAM / 10 GB DigitalOcean droplet because it avoids Docker overhead and lets the bot run as a supervised long-running service.

## Supported Targets

### Ubuntu VPS + systemd

- Application path: `/opt/telegram-manager`
- Runtime command: `python bot_main.py`
- Python dependencies installed into a project-local virtualenv.
- Secrets stored in `/opt/telegram-manager/.env` and never committed.
- systemd service runs the bot continuously and restarts on failure.
- Logs are available through `journalctl -u telegram-manager` and the app's own logging.

### Docker

- Provide a Dockerfile and docker-compose file for portability.
- Docker is not the default recommendation for the 512 MB VPS, but remains available for larger hosts or container-based deployment.
- The container reads environment variables from `.env`.

### Railway

- Keep the existing Railway deployment path intact.
- Continue using the existing `railway.json` start command, `python bot_main.py`.
- Document the same required environment variables for Railway.

### Cloudflare

Cloudflare Workers is not a runtime target for this app. Telegram Manager is a Python long-running bot using aiogram, Telethon, session files, and Supabase. Workers is better suited to JavaScript/TypeScript request handlers, not this process model. Cloudflare can still be used for DNS or tunneling around infrastructure, but not as the bot runtime.

## Files

Add:

- `deploy/systemd/telegram-manager.service`
- `deploy/systemd/install.sh`
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`

Update:

- `README.md` deployment section

## Environment

Required runtime variables:

- `BOT_TOKEN`
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`
- `OWNER_IDS`

Optional variables remain documented in `.env.example`.

## Safety and Operations

- Do not commit `.env`, session files, or account data.
- systemd install script should fail fast if required files are missing.
- Use a non-root service user where practical.
- Do not change bot application code for this deployment pass.
- Keep Railway behavior unchanged.

## Verification

- Compile Python entry points with `python -m py_compile`.
- Validate Dockerfile and compose syntax where local tooling is available.
- Verify generated systemd service points to `bot_main.py` and the expected project directory.
