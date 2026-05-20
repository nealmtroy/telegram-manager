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
SERVICE_WAS_RUNNING=0
if systemctl is-active --quiet "${APP_NAME}"; then
  echo "Stopping ${APP_NAME} service..."
  systemctl stop "${APP_NAME}"
  SERVICE_WAS_RUNNING=1
fi

rsync -a --delete \
  --exclude '.git' \
  --exclude '.claude' \
  --exclude '.env' \
  --exclude 'venv' \
  --exclude 'sessions' \
  --exclude 'logs' \
  --exclude 'accounts.json' \
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

if [[ "${SERVICE_WAS_RUNNING}" -eq 1 ]]; then
  echo "Restarting ${APP_NAME} service..."
  systemctl start "${APP_NAME}"
fi

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
