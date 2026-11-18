#!/usr/bin/env bash
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Run this installer as root: sudo bash deploy-oracle-vps.sh" >&2
    exit 1
fi

if [[ ! -f "${PWD}/app/main.py" || ! -f "${PWD}/worker/worker.py" ]]; then
    echo "Run this script from the extracted Hindi Content Factory folder." >&2
    exit 1
fi

if [[ ! -r /etc/os-release ]]; then
    echo "Cannot identify this Linux distribution." >&2
    exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
    echo "This installer supports Ubuntu 24.04 LTS only." >&2
    exit 1
fi

if id factory >/dev/null 2>&1 || [[ -e /opt/hindi-factory ]]; then
    echo "/opt/hindi-factory or the factory service account already exists; refusing to overwrite it." >&2
    exit 1
fi

read -r -s -p "Telegram bot token (input hidden): " TELEGRAM_BOT_TOKEN
printf '\n'
if [[ ! "${TELEGRAM_BOT_TOKEN}" =~ ^[0-9]{6,}:[A-Za-z0-9_-]{20,}$ ]]; then
    unset TELEGRAM_BOT_TOKEN
    echo "The Telegram token format is invalid. Get the token from BotFather and try again." >&2
    exit 1
fi

APP_ROOT=/opt/hindi-factory
DATA_ROOT="${APP_ROOT}/data"
INSTALLER_SOURCE="${PWD}"
cleanup() {
    unset TELEGRAM_BOT_TOKEN
}
trap cleanup EXIT

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates curl ffmpeg python3 python3-venv

useradd --system --home-dir "${APP_ROOT}" --create-home --shell /usr/sbin/nologin factory
install -d -o factory -g factory -m 0750 \
    "${APP_ROOT}/app" "${APP_ROOT}/worker" "${DATA_ROOT}/media" "${DATA_ROOT}/worker-media"
install -o factory -g factory -m 0640 "${INSTALLER_SOURCE}/requirements.txt" "${APP_ROOT}/requirements.txt"
cp -a "${INSTALLER_SOURCE}/app/." "${APP_ROOT}/app/"
cp -a "${INSTALLER_SOURCE}/worker/." "${APP_ROOT}/worker/"
chown -R root:factory "${APP_ROOT}/app" "${APP_ROOT}/worker"
chmod -R u=rwX,g=rX,o= "${APP_ROOT}/app" "${APP_ROOT}/worker"

python3 -m venv "${APP_ROOT}/.venv"
"${APP_ROOT}/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir \
    -r "${APP_ROOT}/requirements.txt"
"${APP_ROOT}/.venv/bin/python" -m pip install --disable-pip-version-check --no-cache-dir \
    -r "${APP_ROOT}/worker/requirements.txt"

ADMIN_API_TOKEN=$("${APP_ROOT}/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')
WORKER_TOKEN=$("${APP_ROOT}/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')
TOKEN_ENCRYPTION_KEY=$("${APP_ROOT}/.venv/bin/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')

umask 077
cat > "${APP_ROOT}/.env" <<EOF
APP_ENV=production
APP_PAUSED=true
DATABASE_URL=sqlite:////opt/hindi-factory/data/factory.db
PUBLIC_BASE_URL=
ADMIN_API_TOKEN=${ADMIN_API_TOKEN}
WORKER_TOKEN=${WORKER_TOKEN}
WORKER_POLL_SECONDS=30
WORKER_LEASE_SECONDS=900
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_MODE=polling
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_OWNER_IDS=1112018699
YOUTUBE_ENABLED=false
INSTAGRAM_ENABLED=false
AUTOMATION_ENABLED=false
MEDIA_GENERATION_ENABLED=false
MEDIA_ROOT=/opt/hindi-factory/data/media
TOKEN_ENCRYPTION_KEY=${TOKEN_ENCRYPTION_KEY}
MOCK_PUBLISHING=true
MAX_STORAGE_GB=4
MAX_CONCURRENT_JOBS=1
MAX_DAILY_PUBLICATIONS=3
EOF
printf '%s' "${WORKER_TOKEN}" > "${DATA_ROOT}/worker-token.txt"
chown factory:factory "${APP_ROOT}/.env" "${DATA_ROOT}/worker-token.txt"
chmod 0600 "${APP_ROOT}/.env" "${DATA_ROOT}/worker-token.txt"
chmod 0750 "${DATA_ROOT}" "${DATA_ROOT}/media" "${DATA_ROOT}/worker-media"
unset ADMIN_API_TOKEN WORKER_TOKEN TOKEN_ENCRYPTION_KEY TELEGRAM_BOT_TOKEN

cat > /etc/systemd/system/hindi-factory-api.service <<'EOF'
[Unit]
Description=Hindi Content Factory API and Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=factory
Group=factory
WorkingDirectory=/opt/hindi-factory
EnvironmentFile=/opt/hindi-factory/.env
ExecStart=/opt/hindi-factory/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/opt/hindi-factory/data

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/hindi-factory-worker.service <<'EOF'
[Unit]
Description=Hindi Content Factory video renderer
After=network-online.target hindi-factory-api.service
Wants=network-online.target
Requires=hindi-factory-api.service

[Service]
Type=simple
User=factory
Group=factory
WorkingDirectory=/opt/hindi-factory
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/hindi-factory/.venv/bin/python /opt/hindi-factory/worker/worker.py --url http://127.0.0.1:8000 --token-file /opt/hindi-factory/data/worker-token.txt --poll-seconds 30 --output-dir /opt/hindi-factory/data/worker-media
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/opt/hindi-factory/data

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now hindi-factory-api.service hindi-factory-worker.service

for attempt in $(seq 1 30); do
    if curl --fail --silent --show-error http://127.0.0.1:8000/ready >/dev/null 2>&1; then
        break
    fi
    if [[ "${attempt}" -eq 30 ]]; then
        echo "The API did not become ready. Check: sudo journalctl -u hindi-factory-api -n 80 --no-pager" >&2
        exit 1
    fi
    sleep 2
done

if ! systemctl is-active --quiet hindi-factory-worker.service; then
    echo "The renderer service failed to start. Check: sudo journalctl -u hindi-factory-worker -n 80 --no-pager" >&2
    exit 1
fi

echo "Factory API and renderer are running on this VPS."
echo "Telegram uses private long polling; no public app port was opened."
echo "The factory remains paused, mock publishing is enabled, and both platforms are disabled."
echo "Data and private credentials are stored under /opt/hindi-factory; keep the VPS backed up."
echo "Check status: sudo systemctl status hindi-factory-api hindi-factory-worker"
echo "Check logs: sudo journalctl -u hindi-factory-api -u hindi-factory-worker -n 80 --no-pager"
