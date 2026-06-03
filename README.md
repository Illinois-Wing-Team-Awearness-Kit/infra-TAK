# ILWG infra-TAK

Illinois Wing Team Awareness Kit infrastructure — managed from one browser tab.

## Topology

```
                        ┌──────────────────────────────────┐
                        │         ILWG-Server2 (VPS)       │
                        │   TAK Server  +  infra-TAK       │
                        │         console (:5001)           │
                        └────────────────┬─────────────────┘
                                         │  ZeroTier VPN
                      ┌──────────────────┴──────────────────┐
                      │                                      │
           ┌──────────┴──────────┐                ┌─────────┴──────────┐
           │   Remote Host A     │                │   Remote Host B    │
           │     Authentik       │                │     CloudTAK       │
           │  (SSH deploy from   │                │  (SSH deploy from  │
           │     console)        │                │     console)       │
           └─────────────────────┘                └────────────────────┘
```

All three hosts join the same ZeroTier network. The console SSHes to the remote hosts over ZeroTier IPs to deploy and manage Authentik and CloudTAK.

## Setup order

```
1. ZeroTier      Install on ALL hosts (ILWG-Server2, Authentik host, CloudTAK host)
                 sudo bash scripts/setup-zerotier-deps.sh
                 sudo bash scripts/setup-zerotier.sh <NETWORK_ID>
         ↓
2. Console       Clone repo + run start.sh on ILWG-Server2
                 git clone https://github.com/Illinois-Wing-Team-Awearness-Kit/infra-TAK.git
                 cd infra-TAK && sudo ./start.sh
         ↓
3. Authentik     Deploy from the console → Authentik page
                 Set "Deployment target" to the remote host's ZeroTier IP
         ↓
4. TAK Server    Deploy from the console → TAK Server page
                 Upload .deb, deploy, then click Connect TAK Server to LDAP
         ↓
5. CloudTAK      Deploy from the console → CloudTAK page
                 Set "Deployment target" to the remote host's ZeroTier IP
```

## Recovery

### Can't reach the console via domain / Authentik is down

Open the backdoor directly — bypasses Caddy and Authentik:

```
https://<ILWG-Server2 IP>:5001
```

Log in with the console password set during `./start.sh`.

### Forgot console password

SSH to ILWG-Server2 and reset it:

```bash
cd /home/takwerx/infra-TAK   # or your install path
sudo ./reset-console-password.sh
```

Then log in via the backdoor URL above.

### Git / Update Now is broken (wrong version after update)

Force-reset to main from the official repo:

```bash
cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
git fetch https://github.com/Illinois-Wing-Team-Awearness-Kit/infra-TAK.git main
git checkout --force -B main FETCH_HEAD
grep '^VERSION' app.py
sudo systemctl restart takwerx-console
```

## Firewall / Ports

### ILWG-Server2 — open in UFW

| Port | Protocol | Service | Notes |
|------|----------|---------|-------|
| 80 | TCP | Caddy | HTTP → HTTPS redirect |
| 443 | TCP | Caddy | HTTPS reverse proxy (Authentik, TAK Portal, etc.) |
| 5001 | TCP | Console | Backdoor — direct IP access, skips Caddy |
| 8089 | TCP | TAK Server | ATAK/iTAK/WinTAK client connections |
| 8443 | TCP | TAK Server | Admin WebGUI (client cert auth) |
| 8446 | TCP | TAK Server | Admin WebGUI (password/LDAP auth) |
| 9993 | UDP | ZeroTier | ZeroTier peer-to-peer traffic |

### ILWG-Server2 — loopback only (deny in UFW)

| Port | Service |
|------|---------|
| 9090 / 9443 | Authentik (when deployed locally) |
| 5000 | CloudTAK API (when deployed locally) |
| 1880 | Node-RED |
| 3000 | TAK Portal |

### Remote hosts — required inbound

| Port | Protocol | Source | Purpose |
|------|----------|--------|---------|
| 22 | TCP | ILWG-Server2 ZeroTier IP | SSH for console remote deploy |
| 9090 | TCP | ILWG-Server2 ZeroTier IP | Authentik API (token injection during deploy) |
| 9993 | UDP | Any | ZeroTier peer-to-peer |

### All hosts — ZeroTier

ZeroTier uses **UDP 9993** outbound to connect to roots and peers. Open this on every host's firewall.

## Scripts reference

| Script | Purpose |
|--------|---------|
| `scripts/setup-zerotier-deps.sh` | Install curl/gnupg/lsb-release/ca-certificates/iproute2, verify TUN module, check connectivity to install.zerotier.com |
| `scripts/setup-zerotier.sh` | Install ZeroTier, enable zerotier-one, print node ID, optionally join a network |
| `scripts/setup-migration.sh` | Authentik live-migration wizard setup (run on console server) |
| `scripts/ldap-diagnose-and-fix.sh` | Diagnose and repair LDAP auth between TAK Server and Authentik |
| `scripts/set-docker-log-limits.sh` | Apply Docker container log size limits |
| `scripts/nodered-egress-firewall.sh` | Lock down Node-RED outbound traffic |
| `scripts/bootstrap-nodered-flatfile.sh` | Initialize Node-RED with flat-file credentials |
| `scripts/fix-mediamtx-webeditor-now.sh` | Immediate MediaMTX web editor fix |
| `scripts/fix-mediamtx-stream-redirect.sh` | Fix MediaMTX stream redirect config |
