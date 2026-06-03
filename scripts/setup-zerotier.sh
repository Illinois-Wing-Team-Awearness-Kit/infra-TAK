#!/bin/bash
##############################################################################
# ZeroTier Installer
#
# Installs ZeroTier, enables and starts zerotier-one via systemd, prints
# the node ID, and optionally joins a network.
#
# Run setup-zerotier-deps.sh first to ensure prerequisites are in place.
#
# Usage:
#   sudo bash scripts/setup-zerotier.sh [NETWORK_ID]
#
# Arguments:
#   NETWORK_ID  Optional 16-character hex ZeroTier network ID to join
#               Example: sudo bash scripts/setup-zerotier.sh a09acf023364e141
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

ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $*"; }
err()  { echo -e "  ${RED}✗${NC} $*"; }
info() { echo -e "  ${DIM}→${NC} $*"; }
hdr()  { echo -e "\n${BOLD}${CYAN}$*${NC}"; }

NETWORK_ID="${1:-}"

echo ""
echo -e "${BOLD}ZeroTier Installer${NC}"
echo -e "${DIM}Installs and starts ZeroTier on this host${NC}"
echo ""

# ── Root check ────────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    err "This script must be run as root (sudo)"
    exit 1
fi

# ── Validate optional network ID ─────────────────────────────────────────────
if [ -n "$NETWORK_ID" ]; then
    if [[ ! "$NETWORK_ID" =~ ^[0-9a-fA-F]{16}$ ]]; then
        err "NETWORK_ID must be exactly 16 hex characters — got: $NETWORK_ID"
        exit 1
    fi
    info "Will join network: $NETWORK_ID"
fi

# ── Check prerequisites ────────────────────────────────────────────────────────
hdr "Checking prerequisites..."

MISSING=()
for cmd in curl gnupg lsb_release; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        MISSING+=("$cmd")
    fi
done

if [ "${#MISSING[@]}" -gt 0 ]; then
    warn "Missing: ${MISSING[*]}"
    info "Run setup-zerotier-deps.sh first: sudo bash scripts/setup-zerotier-deps.sh"
    exit 1
fi
ok "Prerequisites present"

# ── Check if ZeroTier is already installed ────────────────────────────────────
hdr "Checking for existing ZeroTier installation..."

if command -v zerotier-one >/dev/null 2>&1; then
    ZT_VER=$(zerotier-one -v 2>/dev/null || echo "unknown")
    ok "ZeroTier already installed (version: $ZT_VER)"
    info "Skipping install — proceeding to service check"
else
    # ── Install ZeroTier ──────────────────────────────────────────────────────
    hdr "Installing ZeroTier..."
    info "Downloading installer from https://install.zerotier.com..."
    curl -sf https://install.zerotier.com | bash
    ok "ZeroTier installed"
fi

# ── Enable and start zerotier-one ─────────────────────────────────────────────
hdr "Starting zerotier-one service..."

systemctl enable zerotier-one
ok "zerotier-one enabled (will start on boot)"

if systemctl is-active --quiet zerotier-one; then
    ok "zerotier-one already running"
else
    systemctl start zerotier-one
    sleep 3
    if systemctl is-active --quiet zerotier-one; then
        ok "zerotier-one started"
    else
        err "zerotier-one failed to start"
        info "Check: sudo journalctl -u zerotier-one -n 20 --no-pager"
        exit 1
    fi
fi

# ── Print node ID ──────────────────────────────────────────────────────────────
hdr "Node ID..."

# Give the daemon a moment to initialize if it just started
sleep 2
NODE_ID=$(zerotier-cli info 2>/dev/null | awk '{print $3}' || echo "")

if [ -n "$NODE_ID" ]; then
    ok "ZeroTier node ID: ${BOLD}${CYAN}${NODE_ID}${NC}"
    info "Authorize this node ID in your ZeroTier Central network settings"
else
    warn "Could not retrieve node ID yet — daemon may still be initializing"
    info "Run: sudo zerotier-cli info"
fi

# ── Join network ───────────────────────────────────────────────────────────────
if [ -n "$NETWORK_ID" ]; then
    hdr "Joining network ${NETWORK_ID}..."
    if zerotier-cli join "$NETWORK_ID"; then
        ok "Join request sent for network $NETWORK_ID"
        info "The network admin must authorize this node in ZeroTier Central"
        info "Node ID to authorize: ${BOLD}${NODE_ID}${NC}"
    else
        warn "Join command returned an error — check zerotier-cli status"
    fi
fi

# ── Firewall reminder ──────────────────────────────────────────────────────────
hdr "Firewall reminder..."

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    info "UFW is active. Allow ZeroTier UDP:"
    echo ""
    echo -e "    ${BOLD}sudo ufw allow 9993/udp${NC}"
    echo ""
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state 2>/dev/null | grep -q running; then
    info "firewalld is active. Allow ZeroTier UDP:"
    echo ""
    echo -e "    ${BOLD}sudo firewall-cmd --permanent --add-port=9993/udp && sudo firewall-cmd --reload${NC}"
    echo ""
else
    warn "Could not detect active firewall — if you have one, open UDP port 9993"
    info "UFW: sudo ufw allow 9993/udp"
    info "firewalld: sudo firewall-cmd --permanent --add-port=9993/udp && sudo firewall-cmd --reload"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  ZeroTier is running.${NC}"
echo ""
if [ -n "$NODE_ID" ]; then
    echo -e "  Node ID: ${BOLD}${CYAN}${NODE_ID}${NC}"
fi
if [ -n "$NETWORK_ID" ]; then
    echo -e "  Network: ${BOLD}${NETWORK_ID}${NC} (pending authorization)"
fi
echo ""
echo -e "  Useful commands:"
echo -e "    ${DIM}sudo zerotier-cli info${NC}              — status and node ID"
echo -e "    ${DIM}sudo zerotier-cli listnetworks${NC}      — joined networks"
echo -e "    ${DIM}sudo zerotier-cli join <NETWORK_ID>${NC} — join a network"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
