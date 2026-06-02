#!/bin/bash
##############################################################################
# infra-TAK Migration Wizard Setup
#
# Run this on the infra-TAK CONSOLE server (not the Authentik server).
# It pulls the migration wizard branch, installs prerequisites, and
# restarts the console so the wizard is ready in your browser.
#
# Usage:
#   bash ~/infra-TAK/scripts/setup-migration.sh
#
##############################################################################

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

BRANCH="claude/inspiring-edison-ULSQG"
SERVICE="takwerx-console"

ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $*"; }
err()  { echo -e "  ${RED}✗${NC} $*"; }
info() { echo -e "  ${DIM}→${NC} $*"; }
hdr()  { echo -e "\n${BOLD}${CYAN}$*${NC}"; }

echo ""
echo -e "${BOLD}infra-TAK Migration Wizard Setup${NC}"
echo -e "${DIM}Prepares the console server to run live Authentik migration${NC}"
echo ""

# ── Locate infra-TAK install dir ─────────────────────────────────────────────
hdr "Locating infra-TAK..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -f "$INSTALL_DIR/app.py" ]; then
    err "app.py not found at $INSTALL_DIR — run this script from inside the infra-TAK repo"
    exit 1
fi
ok "infra-TAK at $INSTALL_DIR"
cd "$INSTALL_DIR"

# ── Check we're not on the Authentik server by mistake ───────────────────────
hdr "Checking server role..."

if systemctl is-active --quiet "$SERVICE" 2>/dev/null || \
   pgrep -f "gunicorn.*app:app" >/dev/null 2>&1; then
    ok "Console service ($SERVICE) is running on this machine — correct server"
elif [ -f ".config/settings.json" ]; then
    ok "Found .config/settings.json — this looks like the console machine"
else
    warn "Cannot confirm this is the console server"
    warn "If this is the Authentik-only server, press Ctrl+C now"
    info "The migration wizard must run on the console machine (the one you access in a browser)"
    echo ""
    read -r -p "  Continue anyway? [y/N] " _confirm
    [[ "$_confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 0; }
fi

# ── Check current branch ──────────────────────────────────────────────────────
hdr "Checking git branch..."

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
ok "Current branch: $CURRENT_BRANCH"

if [ "$CURRENT_BRANCH" = "$BRANCH" ]; then
    ok "Already on migration branch"
    hdr "Pulling latest..."
    git pull origin "$BRANCH" && ok "Up to date" || warn "Pull failed — continuing with current code"
else
    hdr "Switching to migration branch: $BRANCH"
    git fetch origin "$BRANCH" 2>&1 | tail -3
    if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
        git checkout "$BRANCH"
    else
        git checkout -b "$BRANCH" "origin/$BRANCH"
    fi
    git pull origin "$BRANCH" 2>&1 | tail -3
    ok "On branch $BRANCH"
fi

# ── Install sshpass (needed for password-based SSH to destination) ────────────
hdr "Checking prerequisites..."

if command -v sshpass >/dev/null 2>&1; then
    ok "sshpass already installed ($(sshpass -V 2>&1 | head -1))"
else
    warn "sshpass not found — installing..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get install -y -qq sshpass && ok "sshpass installed"
    elif command -v yum >/dev/null 2>&1; then
        sudo yum install -y -q sshpass && ok "sshpass installed"
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y -q sshpass && ok "sshpass installed"
    else
        err "Cannot install sshpass automatically — install it manually: apt install sshpass"
        err "Then re-run this script"
        exit 1
    fi
fi

# nc (netcat) is used to probe destination port 5432
if command -v nc >/dev/null 2>&1; then
    ok "nc (netcat) available"
else
    warn "nc not found — installing netcat-openbsd..."
    sudo apt-get install -y -qq netcat-openbsd 2>/dev/null || \
    sudo yum install -y -q nc 2>/dev/null || \
    warn "Could not install nc — port reachability check in wizard may be inaccurate"
fi

# ── Get the console URL so we can print it at the end ────────────────────────
hdr "Detecting console URL..."

CONSOLE_PORT=$(.venv/bin/python3 -c "
import json, os
try:
    with open('.config/settings.json') as f:
        print(json.load(f).get('console_port', 5001))
except Exception:
    print(5001)
" 2>/dev/null || echo 5001)

# Try to detect public IP
PUBLIC_IP=$(curl -sf --max-time 4 https://api.ipify.org 2>/dev/null || \
            curl -sf --max-time 4 http://ifconfig.me 2>/dev/null || \
            hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_SERVER_IP")

# Detect protocol (https if SSL certs present)
PROTO="http"
if [ -f ".config/ssl/console.crt" ] && [ -f ".config/ssl/console.key" ]; then
    PROTO="https"
fi

CONSOLE_URL="${PROTO}://${PUBLIC_IP}:${CONSOLE_PORT}"
ok "Console URL: $CONSOLE_URL"

# ── Restart the console service ───────────────────────────────────────────────
hdr "Restarting infra-TAK console..."

if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    sudo systemctl restart "$SERVICE"
    sleep 3
    if systemctl is-active --quiet "$SERVICE"; then
        ok "Console restarted successfully"
    else
        err "Console failed to restart"
        info "Check logs: sudo journalctl -u $SERVICE -n 30 --no-pager"
        exit 1
    fi
else
    # Not running as a service — try gunicorn directly
    warn "$SERVICE systemd unit not active — trying direct restart..."
    pkill -f "gunicorn.*app:app" 2>/dev/null || true
    sleep 2
    nohup .venv/bin/gunicorn --bind "0.0.0.0:$CONSOLE_PORT" \
        --workers 1 --threads 4 --timeout 300 \
        app:app >> .config/console.log 2>&1 &
    sleep 3
    if pgrep -f "gunicorn.*app:app" >/dev/null; then
        ok "Console started (PID $(pgrep -f 'gunicorn.*app:app' | head -1))"
    else
        err "Could not start console — check .config/console.log"
        exit 1
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  Ready!${NC}"
echo ""
echo -e "  Open this in your browser:"
echo -e "  ${BOLD}${CYAN}${CONSOLE_URL}/authentik/migration${NC}"
echo ""
echo -e "  Or go to the console home and click ${BOLD}↗ Migration Wizard${NC}"
echo -e "  in the Authentik section."
echo ""
echo -e "  ${DIM}You will need:${NC}"
echo -e "    • IP address of the destination server"
echo -e "    • SSH password for root@destination"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
