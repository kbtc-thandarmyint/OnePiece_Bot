#!/usr/bin/env bash
#
# One-command deploy of the multi-tenant Telegram Web Manager to the droplet.
#
# Usage:
#   SSHPASS='your-ssh-password' GATE_PASS='basic-auth-pass' ./deploy/push_to_prod.sh
#
# Env:
#   SERVER     ssh target            (default: thomas@188.166.228.156)
#   SSHPASS    ssh password          (optional; omit if you use SSH keys)
#   GATE_PASS  nginx Basic-Auth pass (optional; skips the gate if unset)
#   DOMAIN     server_name           (default: login.mlbbshop.app)
#
set -euo pipefail

# Load local deploy secrets (SSHPASS, GATE_PASS, ADMIN_PASSWORD) if present, so
# the command line carries no password (and can be safely allow-listed).
_here="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$_here/.deploy.env" ]; then set -a; . "$_here/.deploy.env"; set +a; fi

SERVER="${SERVER:-thomas@188.166.228.156}"
DOMAIN="${DOMAIN:-login.mlbbshop.app}"
APP_DIR="/opt/videobot"

SSH="ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"
RSH="ssh -o StrictHostKeyChecking=accept-new"
if [ -n "${SSHPASS:-}" ]; then
  command -v sshpass >/dev/null || { echo "sshpass not found (brew install sshpass)"; exit 1; }
  export SSHPASS
  SSH="sshpass -e $SSH"
  RSH="sshpass -e $RSH"
fi

echo "==> 1/5  Syncing code to $SERVER:$APP_DIR"
rsync -az --rsh="$RSH" \
  --exclude __pycache__ \
  web_app.py downloader.py import_session.py topup_bot.py requirements.txt web deploy \
  "$SERVER:$APP_DIR/"

echo "==> 2/5  Installing deps + sessions dir"
# shellcheck disable=SC2087
$SSH "$SERVER" "bash -s" <<REMOTE
set -e
cd "$APP_DIR"
mkdir -p sessions
.venv/bin/pip install -q -r requirements.txt
REMOTE

# Operator (admin console) credentials -> written to prod .env (never committed).
# Use an alphanumeric/dash ADMIN_PASSWORD so it's shell-safe here.
if [ -n "${ADMIN_PASSWORD:-}" ]; then
  echo "==> 2.5  Setting operator credentials in prod .env"
  ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
  # shellcheck disable=SC2087
  $SSH "$SERVER" "bash -s" <<REMOTE
set -e
cd "$APP_DIR"; touch .env
grep -vE '^(ADMIN_USERNAME|ADMIN_PASSWORD)=' .env > .env.tmp || true
printf 'ADMIN_USERNAME=%s\n' "$ADMIN_USERNAME" >> .env.tmp
printf 'ADMIN_PASSWORD=%s\n' "$ADMIN_PASSWORD" >> .env.tmp
mv .env.tmp .env && chmod 600 .env
REMOTE
fi

# MLBB topup hub config (player-name lookup) -> prod .env
if [ -n "${MLBB_HUB_KEY:-}" ]; then
  echo "==> 2.6  Setting MLBB hub config in prod .env"
  MLBB_HUB_URL="${MLBB_HUB_URL:-https://root.dkgamingshop.com/api}"
  # shellcheck disable=SC2087
  $SSH "$SERVER" "bash -s" <<REMOTE
set -e
cd "$APP_DIR"; touch .env
grep -vE '^(MLBB_HUB_KEY|MLBB_HUB_URL)=' .env > .env.tmp || true
printf 'MLBB_HUB_KEY=%s\n' "$MLBB_HUB_KEY" >> .env.tmp
printf 'MLBB_HUB_URL=%s\n' "$MLBB_HUB_URL" >> .env.tmp
mv .env.tmp .env && chmod 600 .env
REMOTE
fi

# Top-up bot config -> prod .env
if [ -n "${TELEGRAM_TOPUP_BOT_TOKEN:-}" ]; then
  echo "==> 2.7  Setting top-up bot config in prod .env"
  TOPUP_ORDERS_ENABLED="${TOPUP_ORDERS_ENABLED:-false}"
  TOPUP_ADMIN_IDS="${TOPUP_ADMIN_IDS:-}"
  TOPUP_FEATURED_ITEM_ID="${TOPUP_FEATURED_ITEM_ID:-}"
  TELEGRAM_STARS_50_ITEM_ID="${TELEGRAM_STARS_50_ITEM_ID:-}"
  # Default OFF: only TOPUP_ADMIN_IDS may place real money orders. Never hardcode true.
  TOPUP_PUBLIC_CLAIM="${TOPUP_PUBLIC_CLAIM:-false}"
  # shellcheck disable=SC2087
  $SSH "$SERVER" "bash -s" <<REMOTE
set -e
cd "$APP_DIR"; touch .env
grep -vE '^(TELEGRAM_TOPUP_BOT_TOKEN|TOPUP_ORDERS_ENABLED|TOPUP_ADMIN_IDS|TOPUP_FEATURED_ITEM_ID|TOPUP_BOT_USERNAME|TELEGRAM_STARS_50_ITEM_ID|TOPUP_PUBLIC_CLAIM)=' .env > .env.tmp || true
printf 'TELEGRAM_TOPUP_BOT_TOKEN=%s\n' "$TELEGRAM_TOPUP_BOT_TOKEN" >> .env.tmp
printf 'TOPUP_ORDERS_ENABLED=%s\n' "$TOPUP_ORDERS_ENABLED" >> .env.tmp
printf 'TOPUP_ADMIN_IDS=%s\n' "$TOPUP_ADMIN_IDS" >> .env.tmp
printf 'TOPUP_FEATURED_ITEM_ID=%s\n' "$TOPUP_FEATURED_ITEM_ID" >> .env.tmp
printf 'TOPUP_BOT_USERNAME=%s\n' "${TOPUP_BOT_USERNAME:-}" >> .env.tmp
printf 'TELEGRAM_STARS_50_ITEM_ID=%s\n' "${TELEGRAM_STARS_50_ITEM_ID:-}" >> .env.tmp
printf 'TOPUP_PUBLIC_CLAIM=%s\n' "$TOPUP_PUBLIC_CLAIM" >> .env.tmp
mv .env.tmp .env && chmod 600 .env
REMOTE
fi

echo "==> 3/5  Installing systemd unit videobot-web.service"
# shellcheck disable=SC2087
$SSH "$SERVER" "echo '${SUDOPASS:-}' | sudo -S cp $APP_DIR/deploy/videobot-web.service /etc/systemd/system/videobot-web.service && echo '${SUDOPASS:-}' | sudo -S systemctl daemon-reload"

echo "==> 3.5  Top-up bot service (auto-starts only if a token is set in .env)"
# shellcheck disable=SC2087
$SSH "$SERVER" "echo '${SUDOPASS:-}' | sudo -S cp $APP_DIR/deploy/topup-bot.service /etc/systemd/system/topup-bot.service && echo '${SUDOPASS:-}' | sudo -S systemctl daemon-reload; if grep -qE '^TELEGRAM_TOPUP_BOT_TOKEN=.+' $APP_DIR/.env; then echo '${SUDOPASS:-}' | sudo -S systemctl enable --now topup-bot && echo '${SUDOPASS:-}' | sudo -S systemctl restart topup-bot && echo '   topup-bot RUNNING'; else echo '   topup-bot installed (no token yet — not started)'; fi"

echo "==> 4/5  Deploying nginx site (premium in-app login is the front door; no Basic-Auth popup)"
# shellcheck disable=SC2087
$SSH "$SERVER" "echo '${SUDOPASS:-}' | sudo -S bash -c 'cp $APP_DIR/deploy/nginx_videobot.conf /etc/nginx/sites-available/videobot.conf && ln -sf /etc/nginx/sites-available/videobot.conf /etc/nginx/sites-enabled/videobot.conf && rm -f /etc/nginx/.videobot_htpasswd && nginx -t && systemctl reload nginx'"

echo "==> 5/5  Restarting videobot-web"
$SSH "$SERVER" "echo '${SUDOPASS:-}' | sudo -S systemctl enable --now videobot-web && echo '${SUDOPASS:-}' | sudo -S systemctl restart videobot-web && sleep 2 && systemctl is-active videobot-web"

echo ""
echo "✅ Deployed. Check:  https://$DOMAIN/"
echo "   Logs:  $SSH $SERVER 'journalctl -u videobot-web -n 40 --no-pager'"
