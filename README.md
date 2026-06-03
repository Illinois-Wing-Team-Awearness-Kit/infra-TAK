<div align="center">

<img src="docs/ilwg-tak-logo.png" alt="ILWG TAK" height="160"/>
&nbsp;&nbsp;&nbsp;&nbsp;
<img src="docs/cap-wing-logo.png" alt="Civil Air Patrol" height="160"/>

# ILWG infra-TAK

**Illinois Wing — Team Awareness Kit Infrastructure**

One browser tab. One password. Manage everything.

</div>

---

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

---

## Setup Order

> Complete each step before moving to the next.

| Step | Where | What |
|------|-------|------|
| **1. ZeroTier** | All hosts | Install on ILWG-Server2, Authentik host, and CloudTAK host |
| **2. Console** | ILWG-Server2 | Clone repo and run `start.sh` |
| **3. Authentik** | Browser → console | Deploy to remote host via ZeroTier IP |
| **4. TAK Server** | Browser → console | Upload `.deb`, deploy, Connect LDAP |
| **5. CloudTAK** | Browser → console | Deploy to remote host via ZeroTier IP |

### ZeroTier — run on every host

**Fresh machine (no repo yet):**
```bash
git clone https://github.com/Illinois-Wing-Team-Awearness-Kit/infra-TAK.git && cd infra-TAK && git checkout Seperate-ZTScripts && sudo bash scripts/setup-zerotier-deps.sh && sudo bash scripts/setup-zerotier.sh
```

**Already cloned:**
```bash
cd ~/infra-TAK && git pull origin Seperate-ZTScripts && sudo bash scripts/setup-zerotier-deps.sh && sudo bash scripts/setup-zerotier.sh
```

The setup script will prompt for your ZeroTier network ID. Authorize each node in [ZeroTier Central](https://my.zerotier.com) after it joins.

### Console — ILWG-Server2 only

```bash
git clone https://github.com/Illinois-Wing-Team-Awearness-Kit/infra-TAK.git
cd infra-TAK
sudo ./start.sh
```

Then open `https://<ILWG-Server2 IP>:5001` in your browser.

---

## Recovery

### Can't reach the console (Authentik or Caddy is down)

Direct backdoor — bypasses Caddy and Authentik:

```
https://<ILWG-Server2 IP>:5001
```

Log in with the console password set during `./start.sh`.

### Forgot console password

```bash
cd ~/infra-TAK
sudo ./reset-console-password.sh
```

Then log in via the backdoor URL above.

### Git / Update Now is broken

Force-reset to `main` from the official repo:

```bash
cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service)
git fetch https://github.com/Illinois-Wing-Team-Awearness-Kit/infra-TAK.git main
git checkout --force -B main FETCH_HEAD
grep '^VERSION' app.py
sudo systemctl restart takwerx-console
```

---

## Firewall & Ports

### ILWG-Server2 — open in UFW

| Port | Protocol | Service |
|------|----------|---------|
| 80 | TCP | Caddy — HTTP → HTTPS redirect |
| 443 | TCP | Caddy — HTTPS reverse proxy |
| 5001 | TCP | Console backdoor (direct IP, skips Caddy) |
| 8089 | TCP | TAK Server — ATAK/iTAK/WinTAK clients |
| 8443 | TCP | TAK Server — admin WebGUI (client cert) |
| 8446 | TCP | TAK Server — admin WebGUI (password/LDAP) |
| 9993 | UDP | ZeroTier |

### ILWG-Server2 — loopback only (deny in UFW)

| Port | Service |
|------|---------|
| 9090 / 9443 | Authentik (if deployed locally) |
| 5000 | CloudTAK API (if deployed locally) |
| 1880 | Node-RED |
| 3000 | TAK Portal |

### Remote hosts — required inbound

| Port | Protocol | From | Purpose |
|------|----------|------|---------|
| 22 | TCP | ILWG-Server2 ZeroTier IP | SSH — console remote deploy |
| 9090 | TCP | ILWG-Server2 ZeroTier IP | Authentik API token injection |
| 9993 | UDP | Any | ZeroTier peer-to-peer |

---

## Scripts Reference

| Script | Purpose |
|--------|---------|
| [`scripts/setup-zerotier-deps.sh`](scripts/setup-zerotier-deps.sh) | Install prerequisites, verify TUN module, check connectivity to `install.zerotier.com` |
| [`scripts/setup-zerotier.sh`](scripts/setup-zerotier.sh) | Install ZeroTier, enable service, print node ID, join network |
| [`scripts/setup-migration.sh`](scripts/setup-migration.sh) | Authentik live-migration wizard (run on console server) |
| [`scripts/ldap-diagnose-and-fix.sh`](scripts/ldap-diagnose-and-fix.sh) | Diagnose and repair LDAP auth |
| [`scripts/set-docker-log-limits.sh`](scripts/set-docker-log-limits.sh) | Apply Docker container log size limits |
| [`scripts/nodered-egress-firewall.sh`](scripts/nodered-egress-firewall.sh) | Lock down Node-RED outbound traffic |
| [`scripts/bootstrap-nodered-flatfile.sh`](scripts/bootstrap-nodered-flatfile.sh) | Initialize Node-RED with flat-file credentials |
| [`scripts/fix-mediamtx-webeditor-now.sh`](scripts/fix-mediamtx-webeditor-now.sh) | Immediate MediaMTX web editor fix |
| [`scripts/fix-mediamtx-stream-redirect.sh`](scripts/fix-mediamtx-stream-redirect.sh) | Fix MediaMTX stream redirect config |
