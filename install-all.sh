#!/usr/bin/env bash
set -Eeuo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }
TTY=/dev/tty
prompt(){ local v=''; while [ -z "$v" ]; do printf '%s: ' "$1" >"$TTY"; IFS= read -r v <"$TTY" || exit 1; done; printf '%s' "$v"; }
export BOT_TOKEN="${BOT_TOKEN:-$(prompt 'Monitor Telegram Bot Token')}"
export ADMIN_IDS="${ADMIN_IDS:-$(prompt 'Admin Telegram ID(s), comma-separated')}"
export SELLER_BOT_TOKEN="${SELLER_BOT_TOKEN:-$(prompt 'Seller Telegram Bot Token')}"
export SELLER_ADMIN_IDS="${SELLER_ADMIN_IDS:-$ADMIN_IDS}"
export WEB_PORT="${WEB_PORT:-8091}"
export SELLER_WEB_PORT="${SELLER_WEB_PORT:-8090}"
export MONITOR_API_URL="http://127.0.0.1:${WEB_PORT}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL https://raw.githubusercontent.com/TheOnlyOneWithAi/v2ray_monitor/main/install.sh -o "$TMP/monitor-install.sh"
chmod +x "$TMP/monitor-install.sh"
BOT_TOKEN="$BOT_TOKEN" ADMIN_IDS="$ADMIN_IDS" WEB_PORT="$WEB_PORT" bash "$TMP/monitor-install.sh"
curl -fsSL https://raw.githubusercontent.com/TheOnlyOneWithAi/v2ray_monitor_seller/main/install.sh -o "$TMP/seller-install.sh"
chmod +x "$TMP/seller-install.sh"
SELLER_BOT_TOKEN="$SELLER_BOT_TOKEN" ADMIN_IDS="$SELLER_ADMIN_IDS" WEB_PORT="$SELLER_WEB_PORT" MONITOR_API_URL="$MONITOR_API_URL" bash "$TMP/seller-install.sh"
echo
echo '=================================================='
echo 'V2Ray Monitor + Seller installed successfully.'
echo 'Seller API token is NOT required.'
echo '=================================================='
systemctl --no-pager --full status v2ray-monitor v2ray-monitor-seller || true
echo
echo 'Monitor logs: journalctl -u v2ray-monitor -f'
echo 'Seller logs:  journalctl -u v2ray-monitor-seller -f'
