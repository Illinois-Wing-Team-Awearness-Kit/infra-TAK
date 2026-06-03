#!/bin/bash
##############################################################################
# ZeroTier Dependency Checker
#
# Installs prerequisite packages for ZeroTier and verifies the host is
# ready to run setup-zerotier.sh.
#
# Supports: Ubuntu/Debian (apt) and RHEL/Fedora (yum/dnf)
#
# Usage:
#   sudo bash scripts/setup-zerotier-deps.sh
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

echo ""
echo -e "${BOLD}ZeroTier Dependency Checker${NC}"
echo -e "${DIM}Prepares this host to install ZeroTier${NC}"
echo ""

# ── Root check ────────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    err "This script must be run as root (sudo)"
    exit 1
fi

# ── Detect package manager ────────────────────────────────────────────────────
hdr "Detecting package manager..."

if command -v apt-get >/dev/null 2>&1; then
    PKG_MGR="apt"
    ok "apt-get found (Ubuntu/Debian)"
elif command -v dnf >/dev/null 2>&1; then
    PKG_MGR="dnf"
    ok "dnf found (RHEL/Fedora)"
elif command -v yum >/dev/null 2>&1; then
    PKG_MGR="yum"
    ok "yum found (RHEL/CentOS)"
else
    err "No supported package manager found (apt, dnf, or yum required)"
    exit 1
fi

# ── Install packages ──────────────────────────────────────────────────────────
hdr "Installing prerequisite packages..."

PKGS="curl gnupg lsb-release ca-certificates iproute2"

install_pkg() {
    local pkg="$1"
    if command -v "$pkg" >/dev/null 2>&1 || \
       dpkg -s "$pkg" >/dev/null 2>&1 || \
       rpm -q "$pkg" >/dev/null 2>&1; then
        ok "$pkg already installed"
        return
    fi
    info "Installing $pkg..."
    case "$PKG_MGR" in
        apt) apt-get install -y -qq "$pkg" && ok "$pkg installed" ;;
        dnf) dnf install -y -q "$pkg"      && ok "$pkg installed" ;;
        yum) yum install -y -q "$pkg"      && ok "$pkg installed" ;;
    esac
}

case "$PKG_MGR" in
    apt)
        apt-get update -qq
        for pkg in $PKGS; do
            install_pkg "$pkg"
        done
        ;;
    dnf|yum)
        # iproute2 is iproute on RHEL/Fedora
        RHEL_PKGS="curl gnupg2 redhat-lsb-core ca-certificates iproute"
        for pkg in $RHEL_PKGS; do
            install_pkg "$pkg" || warn "Could not install $pkg — continuing"
        done
        ;;
esac

# ── Check TUN kernel module ───────────────────────────────────────────────────
hdr "Checking TUN kernel module..."

if lsmod | grep -q '^tun '; then
    ok "TUN module loaded"
elif modprobe tun 2>/dev/null; then
    ok "TUN module loaded (just now)"
else
    warn "Cannot load TUN module — ZeroTier requires TUN/TAP support"
    info "On VPS hosts: confirm your provider supports TUN (most KVM/VirtIO do; OpenVZ may not)"
    info "Try: modprobe tun"
fi

if [ -c /dev/net/tun ]; then
    ok "/dev/net/tun device present"
else
    warn "/dev/net/tun not found — ZeroTier may fail to create virtual interfaces"
fi

# ── Verify connectivity to install.zerotier.com ───────────────────────────────
hdr "Verifying connectivity to install.zerotier.com..."

if curl -sf --max-time 10 https://install.zerotier.com >/dev/null 2>&1; then
    ok "Reached https://install.zerotier.com"
else
    warn "Cannot reach https://install.zerotier.com"
    info "Check outbound HTTPS (port 443) is allowed from this host"
    info "ZeroTier installation will fail without this"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  Prerequisites ready.${NC}"
echo ""
echo -e "  Run next:"
echo -e "  ${BOLD}${CYAN}sudo bash scripts/setup-zerotier.sh [NETWORK_ID]${NC}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
