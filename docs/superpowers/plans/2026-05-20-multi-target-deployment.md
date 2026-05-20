# Multi-Target Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deployment files and documentation for Ubuntu VPS systemd deployment, optional Docker deployment, and preserved Railway deployment.

**Architecture:** Keep application code unchanged and add deployment artifacts around the existing `python bot_main.py` entry point. The systemd path is the recommended default for the small DigitalOcean droplet; Docker is provided as a portable alternative; Railway remains supported through the existing `railway.json`.

**Tech Stack:** Python 3, venv, systemd, Docker, Docker Compose, Railway Nixpacks.

---

## File Structure

- Create `deploy/systemd/telegram-manager.service`: reusable systemd unit template for `/opt/telegram-manager`.
- Create `deploy/systemd/install.sh`: Ubuntu installer that creates the app directory, virtualenv, service user, installs dependencies, installs/enables the service, and prints next commands.
- Create `Dockerfile`: slim Python container for running `python bot_main.py`.
- Create `docker-compose.yml`: compose service loading `.env` and mounting session/log data.
- Create `.dockerignore`: exclude git metadata, caches, env files, sessions, logs, and local virtualenvs.
- Modify `README.md`: add deployment section covering VPS systemd, Docker, Railway, and why Cloudflare Workers is not a runtime target.

---

### Task 1: Add systemd service unit

**Files:**
- Create: `deploy/systemd/telegram-manager.service`

- [ ] **Step 1: Create service directory if missing**

Run:

```powershell
if (-not (Test-Path "deploy\systemd")) { New-Item -ItemType Directory -Path "deploy\systemd" | Out-Null }
```

Expected: `deploy/systemd` exists.

- [ ] **Step 2: Write systemd unit**

Create `deploy/systemd/telegram-manager.service` with:

```ini
[Unit]
Description=Telegram Manager Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=telegram-manager
Group=telegram-manager
WorkingDirectory=/opt/telegram-manager
EnvironmentFile=/opt/telegram-manager/.env
ExecStart=/opt/telegram-manager/venv/bin/python /opt/telegram-manager/bot_main.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Verify file exists**

Run:

```powershell
Test-Path "deploy\systemd\telegram-manager.service"
```

Expected: `True`.

- [ ] **Step 4: Commit service unit**

Run:

```powershell
git add deploy/systemd/telegram-manager.service
git commit -m @'
Add systemd service for Telegram Manager

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
'@
```

Expected: commit succeeds.

---

### Task 2: Add Ubuntu systemd install script

**Files:**
- Create: `deploy/systemd/install.sh`

- [ ] **Step 1: Write install script**

Create `deploy/systemd/install.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

APP_NAME="telegram-manager"
APP_DIR="/opt/${APP_NAME}"
SERVICE_USER="telegram-manager"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root: sudo bash deploy/systemd/install.sh" >&2
  exit 1
fi

if [[ ! -f "${REPO_DIR}/bot_main.py" ]]; then
  echo "bot_main.py not found. Run this script from the Telegram Manager repository." >&2
  exit 1
fi

if [[ ! -f "${REPO_DIR}/requirements.txt" ]]; then
  echo "requirements.txt not found. Run this script from the Telegram Manager repository." >&2
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-pip rsync

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --home "${APP_DIR}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

mkdir -p "${APP_DIR}"
rsync -a --delete \
  --exclude '.git' \
  --exclude '.claude' \
  --exclude '.env' \
  --exclude 'venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  "${REPO_DIR}/" "${APP_DIR}/"

if [[ ! -f "${APP_DIR}/.env" ]]; then
  if [[ -f "${APP_DIR}/.env.example" ]]; then
    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  else
    touch "${APP_DIR}/.env"
  fi
  chmod 600 "${APP_DIR}/.env"
  echo "Created ${APP_DIR}/.env. Edit it before starting the service."
fi

python3 -m venv "${APP_DIR}/venv"
"${APP_DIR}/venv/bin/pip" install --upgrade pip
"${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

install -m 0644 "${APP_DIR}/deploy/systemd/${APP_NAME}.service" "${SERVICE_FILE}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"
chmod 600 "${APP_DIR}/.env"

systemctl daemon-reload
systemctl enable "${APP_NAME}"

cat <<EOF
Installed ${APP_NAME} to ${APP_DIR}.

Next steps:
1. Edit ${APP_DIR}/.env and set BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY, TELEGRAM_API_ID, TELEGRAM_API_HASH, and OWNER_IDS.
2. Start the bot:
   sudo systemctl start ${APP_NAME}
3. Check status:
   sudo systemctl status ${APP_NAME}
4. Follow logs:
   sudo journalctl -u ${APP_NAME} -f
EOF
```

- [ ] **Step 2: Verify Bash syntax**

Run:

```powershell
bash -n deploy/systemd/install.sh
```

Expected: no output and exit code 0.

- [ ] **Step 3: Commit install script**

Run:

```powershell
git add deploy/systemd/install.sh
git commit -m @'
Add Ubuntu systemd installer

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
'@
```

Expected: commit succeeds.

---

### Task 3: Add Docker deployment files

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`

- [ ] **Step 1: Write Dockerfile**

Create `Dockerfile` with:

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot_main.py"]
```

- [ ] **Step 2: Write docker-compose.yml**

Create `docker-compose.yml` with:

```yaml
services:
  telegram-manager:
    build: .
    container_name: telegram-manager
    env_file:
      - .env
    restart: unless-stopped
    volumes:
      - ./sessions:/app/sessions
      - ./logs:/app/logs
```

- [ ] **Step 3: Write .dockerignore**

Create `.dockerignore` with:

```gitignore
.git
.claude
__pycache__
*.pyc
.pytest_cache
.mypy_cache
.ruff_cache
.env
.env.*
!.env.example
venv
.venv
sessions
logs
accounts.json
```

- [ ] **Step 4: Validate compose config if Docker is available**

Run:

```powershell
docker compose config
```

Expected if Docker is installed: rendered compose config exits 0. If Docker is unavailable locally, record that validation was skipped and continue.

- [ ] **Step 5: Commit Docker files**

Run:

```powershell
git add Dockerfile docker-compose.yml .dockerignore
git commit -m @'
Add optional Docker deployment

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
'@
```

Expected: commit succeeds.

---

### Task 4: Document deployment options

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add deployment section after installation/configuration docs**

Insert this section after the `.env` configuration section in `README.md`:

```markdown
---

## Deployment

Telegram Manager can run anywhere that supports a long-running Python process. The default production recommendation for a small DigitalOcean Ubuntu droplet is Python virtualenv with systemd.

### Required environment variables

Set these values in `.env` for every deployment target:

```dotenv
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=your-supabase-key
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
OWNER_IDS=123456789,987654321
```

### Ubuntu VPS with systemd

Recommended for small VPS instances such as a 1 vCPU / 512 MB RAM DigitalOcean droplet.

```bash
sudo bash deploy/systemd/install.sh
sudo nano /opt/telegram-manager/.env
sudo systemctl start telegram-manager
sudo systemctl status telegram-manager
sudo journalctl -u telegram-manager -f
```

The installer copies the app to `/opt/telegram-manager`, creates a Python virtualenv, installs dependencies, creates a `telegram-manager` service user, installs the systemd service, and enables it at boot.

After pulling updates, rerun the installer from the repository and restart the service:

```bash
sudo bash deploy/systemd/install.sh
sudo systemctl restart telegram-manager
```

### Docker

Docker is useful for portability, but systemd is lighter for very small VPS instances.

```bash
cp .env.example .env
# edit .env

docker compose up -d --build
docker compose logs -f
```

### Railway

Railway remains supported through `railway.json`, which starts the bot with:

```bash
python bot_main.py
```

Set the required environment variables in the Railway project settings.

### Cloudflare Workers

Cloudflare Workers is not a runtime target for this app. Telegram Manager is a Python long-running bot using aiogram, Telethon, session files, and Supabase. Workers is designed for JavaScript/TypeScript request handlers at the edge, not this process model. Cloudflare can still be used for DNS or tunneling if needed.
```

- [ ] **Step 2: Verify README formatting**

Run:

```powershell
git diff -- README.md
```

Expected: deployment section renders as Markdown and does not remove existing installation or usage docs.

- [ ] **Step 3: Commit README update**

Run:

```powershell
git add README.md
git commit -m @'
Document deployment options

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
'@
```

Expected: commit succeeds.

---

### Task 5: Final verification

**Files:**
- Verify: `bot_main.py`
- Verify: `telegram_manager/bot.py`
- Verify: `telegram_manager/db.py`
- Verify: `deploy/systemd/install.sh`
- Verify: Docker/compose files if Docker is installed

- [ ] **Step 1: Compile Python files**

Run:

```powershell
python -m py_compile bot_main.py telegram_manager\bot.py telegram_manager\db.py
```

Expected: no output and exit code 0.

- [ ] **Step 2: Verify installer syntax**

Run:

```powershell
bash -n deploy/systemd/install.sh
```

Expected: no output and exit code 0.

- [ ] **Step 3: Verify git status**

Run:

```powershell
git status --short
```

Expected: clean working tree after commits, or only intentional uncommitted spec/plan files if the user did not request committing planning docs.

- [ ] **Step 4: Summarize deployment path**

Report:

```text
VPS default: sudo bash deploy/systemd/install.sh, edit /opt/telegram-manager/.env, sudo systemctl start telegram-manager.
Docker optional: docker compose up -d --build.
Railway preserved: railway.json still starts python bot_main.py.
Cloudflare Workers not supported as runtime for this Python long-running bot.
```
```

---

## Self-Review

- Spec coverage: systemd VPS, Docker, Railway, Cloudflare runtime note, secrets handling, no application code changes, and verification are covered.
- Placeholder scan: no TBD/TODO/fill-in placeholders remain.
- Type/path consistency: all paths match the file structure above and the app entry point remains `bot_main.py`.
