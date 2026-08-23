#!/usr/bin/env bash
set -Eeuo pipefail
APP_NAME="v2ray-monitor"; INSTALL_DIR="/opt/v2ray-monitor"; SERVICE_NAME="v2ray-monitor"; REPO="https://github.com/TheOnlyOneWithAi/v2ray_monitor.git"; BRANCH="main"; XRAY_VERSION="26.3.27"
[[ "${EUID}" -eq 0 ]] || { echo "Please run as root."; exit 1; }
command -v apt-get >/dev/null 2>&1 || { echo "This installer supports Debian/Ubuntu (apt) only." >&2; exit 1; }
say(){ printf '\n[%s] %s\n' "$APP_NAME" "$1"; }; fail(){ echo "ERROR: $1" >&2; exit 1; }; prompt_required(){ local label="$1" value=""; while [[ -z "$value" ]]; do read -r -p "$label: " value; done; printf '%s' "$value"; }
export DEBIAN_FRONTEND=noninteractive
say "Installing prerequisites"; apt-get update -y; apt-get install -y --no-install-recommends ca-certificates curl git python3 python3-venv python3-pip unzip
BOT_TOKEN="${BOT_TOKEN:-}"; [[ -n "$BOT_TOKEN" ]] || BOT_TOKEN="$(prompt_required 'Telegram Bot Token')"
ADMIN_IDS="${ADMIN_IDS:-${ADMINS:-}}"; [[ -n "$ADMIN_IDS" ]] || ADMIN_IDS="$(prompt_required 'Admin Telegram ID(s), comma-separated')"
read -r -p "Web port [8000]: " WEB_PORT; WEB_PORT="${WEB_PORT:-8000}"; [[ "$WEB_PORT" =~ ^[0-9]+$ ]] && ((WEB_PORT>=1 && WEB_PORT<=65535)) || fail "Invalid web port"
say "Downloading application"; if [[ -d "$INSTALL_DIR/.git" ]]; then git -C "$INSTALL_DIR" fetch --depth=1 origin "$BRANCH"; git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"; else rm -rf "$INSTALL_DIR"; git clone --depth=1 --branch "$BRANCH" "$REPO" "$INSTALL_DIR"; fi
cd "$INSTALL_DIR"; python3 -m venv .venv; . .venv/bin/activate; python -m pip install --upgrade pip wheel; python -m pip install -r requirements.txt
say "Generating secure local configuration"; ENCRYPTION_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"; SELLER_API_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
cat > "$INSTALL_DIR/.env" <<EOF
APP_NAME=V2Ray Monitor
BOT_TOKEN=${BOT_TOKEN}
ADMIN_IDS=${ADMIN_IDS}
WEBAPP_URL=http://127.0.0.1:${WEB_PORT}
WEB_PORT=${WEB_PORT}
DATABASE_URL=sqlite+aiosqlite:///./data/monitor.db
ENCRYPTION_KEY=${ENCRYPTION_KEY}
SELLER_API_TOKEN=${SELLER_API_TOKEN}
XRAY_BINARY=/usr/local/bin/xray
PROBE_TIMEOUT=8
PROBE_INTERVAL=60
PROBE_CONCURRENCY=10
SYNC_INTERVAL=300
MAX_SUBSCRIPTION_BYTES=5000000
MAX_NODES_PER_SUBSCRIPTION=2000
EOF
chmod 600 "$INSTALL_DIR/.env"
say "Installing pinned Xray-core ${XRAY_VERSION}"; TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT; curl -fsSL "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-64.zip" -o "$TMP/xray.zip"; unzip -p "$TMP/xray.zip" xray > /usr/local/bin/xray; chmod 0755 /usr/local/bin/xray
say "Creating restricted service account"; id v2ray-monitor >/dev/null 2>&1 || useradd --system --home "$INSTALL_DIR" --shell /usr/sbin/nologin v2ray-monitor; mkdir -p "$INSTALL_DIR/data"; chown -R v2ray-monitor:v2ray-monitor "$INSTALL_DIR/data"; chmod 700 "$INSTALL_DIR/data"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=V2Ray Monitor
After=network-online.target
Wants=network-online.target
[Service]
Type=simple
User=v2ray-monitor
Group=v2ray-monitor
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=${INSTALL_DIR}/.venv/bin/python -m app.main
Restart=always
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${INSTALL_DIR}/data
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload; systemctl enable --now "$SERVICE_NAME"; sleep 2
if ! systemctl is-active --quiet "$SERVICE_NAME"; then journalctl -u "$SERVICE_NAME" --no-pager -n 80 || true; fail "Service failed to start"; fi
say "Installation complete"; echo "Web: http://SERVER-IP:${WEB_PORT}"; echo "Seller API token: ${SELLER_API_TOKEN}"; echo "Status: systemctl status ${SERVICE_NAME}"; echo "Logs: journalctl -u ${SERVICE_NAME} -f"
