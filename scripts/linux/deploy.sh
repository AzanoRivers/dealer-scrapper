#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Update and restart DealerScrapper on the VPS
#
# Usage:
#   chmod +x scripts/linux/deploy.sh
#   ./scripts/linux/deploy.sh
#
# What it does:
#   1. Fetches from GitHub — exits early if nothing changed
#   2. Pulls new code
#   3. Reinstalls dependencies only if requirements.txt changed
#   4. Syncs nginx conf and systemd service file if they changed
#   5. Cleans orphan job dirs from /tmp/dealerscrapper
#   6. Restarts the systemd service (always — gunicorn has no auto-reload)
#   7. Verifies the service is running
#
# Requirements:
#   - setup.sh must have been run at least once
#   - .env must exist (setup.sh creates it from .env.example on first run)
# =============================================================================

set -euo pipefail

# Always run from the project root (2 levels up from this script)
cd "$(dirname "${BASH_SOURCE[0]}")/../.."

SERVICE_NAME="dealerscrapper"
GIT_BRANCH="master"
VENV_DIR=".venv"
REQUIREMENTS="requirements.txt"
HASH_FILE=".requirements.hash"
JOBS_DIR="/tmp/dealerscrapper"

# ─── Output colors ───────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[dealerscrapper:deploy]${NC} $1"; }
warn()  { echo -e "${YELLOW}[dealerscrapper:deploy]${NC} $1"; }
error() { echo -e "${RED}[dealerscrapper:deploy] $1${NC}"; exit 1; }

# ─── Ensure setup was run first ──────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    error "Virtualenv not found. Run first: ./scripts/linux/setup.sh"
fi

if [ ! -f ".env" ]; then
    error ".env not found. Run setup.sh first or create it manually."
fi

# ─── Pull latest changes from GitHub ─────────────────────────────────────────
info "Fetching changes from GitHub (branch: $GIT_BRANCH)..."
git fetch origin

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse "origin/$GIT_BRANCH")

if [ "$LOCAL" = "$REMOTE" ]; then
    info "No new changes in the repository. Nothing to update."
    exit 0
fi

git pull origin "$GIT_BRANCH"
info "Code updated."

# ─── Activate virtualenv ─────────────────────────────────────────────────────
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# ─── Compare requirements.txt hash ───────────────────────────────────────────
CURRENT_HASH=$(sha256sum "$REQUIREMENTS")
PACKAGES_CHANGED=false

if [ ! -f "$HASH_FILE" ]; then
    warn "Hash file not found. Forcing dependency install."
    PACKAGES_CHANGED=true
elif [ "$CURRENT_HASH" != "$(cat "$HASH_FILE")" ]; then
    warn "requirements.txt changed. Updating dependencies..."
    PACKAGES_CHANGED=true
else
    info "requirements.txt unchanged. Skipping package install."
fi

if [ "$PACKAGES_CHANGED" = true ]; then
    info "Installing dependencies..."
    pip install --upgrade pip --quiet
    pip install -r "$REQUIREMENTS"
    sha256sum "$REQUIREMENTS" > "$HASH_FILE"
    info "Dependencies updated and hash saved."
fi

# ─── Verify import ───────────────────────────────────────────────────────────
python -c "from app.main import app; print('Import OK')" || error "Import check failed. Aborting deploy."

# ─── Sync nginx conf if changed ──────────────────────────────────────────────
NGINX_SRC="dealerscrapper.conf"
NGINX_DEST="/etc/nginx/conf.d/dealerscrapper.conf"

if [ -f "$NGINX_SRC" ]; then
    if ! diff -q "$NGINX_SRC" "$NGINX_DEST" > /dev/null 2>&1; then
        info "nginx config changed. Updating and reloading..."
        sudo cp "$NGINX_SRC" "$NGINX_DEST"
        sudo nginx -t && sudo systemctl reload nginx
        info "nginx reloaded."
    else
        info "nginx config unchanged. Skipping reload."
    fi
fi

# ─── Sync systemd service file if changed ────────────────────────────────────
SERVICE_SRC="dealerscrapper.service"
SERVICE_DEST="/etc/systemd/system/$SERVICE_NAME.service"

if [ -f "$SERVICE_SRC" ]; then
    if ! diff -q "$SERVICE_SRC" "$SERVICE_DEST" > /dev/null 2>&1; then
        info "Service file changed. Updating $SERVICE_DEST..."
        sudo cp "$SERVICE_SRC" "$SERVICE_DEST"
        sudo systemctl daemon-reload
        info "systemd daemon reloaded."
    else
        info "Service file unchanged. Skipping daemon-reload."
    fi
fi

# ─── Clean orphan job dirs from /tmp/dealerscrapper ──────────────────────────
if [ -d "$JOBS_DIR" ]; then
    COUNT=$(find "$JOBS_DIR" -mindepth 1 -maxdepth 1 -type d | wc -l)
    if [ "$COUNT" -gt 0 ]; then
        warn "Cleaning $COUNT orphan job dir(s) from $JOBS_DIR..."
        rm -rf "${JOBS_DIR:?}/"*
        info "Jobs directory cleaned."
    else
        info "Jobs directory already empty."
    fi
fi

# ─── Restart service (always — gunicorn has no auto-reload) ──────────────────
info "Restarting service '$SERVICE_NAME'..."

if systemctl is-active --quiet "$SERVICE_NAME"; then
    sudo systemctl restart "$SERVICE_NAME"
else
    sudo systemctl start "$SERVICE_NAME"
fi

sleep 2

if systemctl is-active --quiet "$SERVICE_NAME"; then
    info "Service '$SERVICE_NAME' is running."
    systemctl status "$SERVICE_NAME" --no-pager -l
else
    error "Service '$SERVICE_NAME' failed to start. Check logs with: journalctl -u $SERVICE_NAME -n 50"
fi
