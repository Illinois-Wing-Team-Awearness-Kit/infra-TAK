# v0.9.0-alpha Release Notes

## What ships

Two operator-facing features: **Custom Identification Banners** and **Fail2ban Brute-Force Protection**.

---

## Feature 1 — Custom Identification Banner

Operators managing multiple infra-TAK instances now have a persistent identity banner at the top of every page. It eliminates the "which box am I on?" problem when managing a fleet.

### What it looks like

```
[ Agency Logo ]   REGION 5 OPS CENTER — ALPHA UNIT   [ Agency Logo ]
                  190.102.110.224 | test6.takworx.com
```

The banner is fixed to the top of every page — Console, Fail2ban, Caddy, TAK Server, all of them — and updates live without a page reload after saving.

### How to set it up

1. Open the sidebar → **Customization** (between Marketplace and Help)
2. Toggle **Enable Banner** on
3. Type your identification text (agency name, unit, server role — up to 120 characters)
4. Choose a **font size**: Small, Medium, or Large
5. Upload an **agency logo** (PNG, SVG, or JPEG — max 512 KB) — appears on both sides of the text
6. Click **Save** — banner appears immediately on all pages

The public IP and FQDN of the server are always shown in small text beneath the banner text, regardless of custom text or logo.

If no agency logo is uploaded, the infra-TAK mark appears on both sides instead.

### Settings stored

Under `customization` in `.config/settings.json`:

| Key | Description |
|-----|-------------|
| `banner_enabled` | bool — whether banner renders |
| `banner_text` | string — the identification text |
| `banner_font_size` | `small` / `medium` / `large` |
| `banner_text_color` | hex color (default `#00d4ff`) |
| `agency_logo_b64` | base64 data URI of uploaded logo, or null |

---

## Feature 2 — Fail2ban Brute-Force Protection (Marketplace Module)

Fail2ban is a host-level intrusion prevention system. When an IP address repeatedly fails authentication, Fail2ban bans it at the firewall level via UFW — no more brute-force attempts, no more log noise.

infra-TAK ships two optional jails: one for **Authentik** login failures and one for **TAK Server** connection probes. Both are operator-controlled with enable/disable toggles, configurable thresholds, IP whitelists, and Guard Dog email alerts on every ban.

### Installing Fail2ban

1. Open the sidebar → **Fail2ban**
2. Click **Install Fail2ban** in the Marketplace card
3. Watch the progress log — it installs the package, writes filters, configures UFW actions, and sets up the log forwarder for Authentik
4. After install, the page reloads showing the dashboard

> **Prerequisite:** Fail2ban requires the v0.8.9 Authentik trusted-proxy CIDR fix to be applied before it will install. This is confirmed automatically. Installs without the fix will see an error message.

---

### Authentik Jail

Monitors `/var/log/authentik/auth.log` for `login_failed` events and bans the source IP after too many failures.

#### How it works

```
Browser → Caddy → Authentik → docker logs → log-forwarder → /var/log/authentik/auth.log
                                                                      ↓
                                              fail2ban filter (login_failed JSON) → UFW ban
```

The **authentik-log-forwarder** is a systemd service that tails `docker logs -f authentik-server-1` to a host-accessible log file. Fail2ban watches that file.

#### Dashboard

The **Authentik Jail** card shows:
- **Enable toggle** — turns the jail on or off (also starts/stops the log forwarder)
- **Active/Disabled badge** with pulsing dot when live
- **Currently Banned** — IPs blocked right now, with Unban buttons
- **Currently Failed** — IPs accumulating failures (not yet banned)
- **Session totals** — total bans and failures since fail2ban last started

#### Configuration

| Field | Default | Description |
|-------|---------|-------------|
| Max Retries | 5 | Failed logins before ban |
| Find Window | 10 min | Time window to count failures |
| Ban Duration | 60 min | How long the ban lasts |
| Whitelist | _(empty)_ | Space-separated IPs / CIDRs that are never banned. Localhost is always exempt. |

Click **Save & Reload** to apply — active bans are not affected.

#### Guard Dog email alerts

When an IP is auto-banned, an email is sent to your configured Guard Dog alert address:

```
Subject: [YOUR-SERVER-NICKNAME] fail2ban: Banned 1.2.3.4 (Authentik)

fail2ban has banned the following IP address:

  IP:     1.2.3.4
  Jail:   Authentik
  Server: YOUR-SERVER-NICKNAME
  Time:   2026-05-02 01:23:45 UTC
  Reason: Too many failed Authentik login attempts

To unban this IP, open your infra-TAK console → Fail2ban page.
```

The server nickname is pulled from your Console settings. Requires Guard Dog email relay to be configured.

#### Log Forwarder

The top-right badge shows **Log Forwarder Active** (green) or **Log Forwarder Stopped** (red).

If the forwarder is stopped after install (common on servers where Fail2ban was installed before this feature):
1. Toggle the Authentik Jail **OFF** then back **ON**
2. The toggle re-writes the service file if missing and starts it automatically

---

### TAK Server Jail

Monitors `/opt/tak/logs/takserver-messaging.log` for repeated TLS handshake failures (bots probing your TAK Server without valid certificates).

#### Why this matters

Internet bots constantly probe open ports. TAK Server on port 8089 gets hit by scanners trying TLS connections without certs. These aren't dangerous (no cert = no access) but they create log noise and consume resources. The TAK Server jail bans the source IPs.

#### Dashboard

The **TAK Server Jail** card shows the same layout as Authentik:
- **Enable toggle** + **Active/Disabled badge**
- **Currently Banned** + **Currently Failed** + session totals
- **Currently Failed is clickable** — expands a panel showing every IP currently accumulating failures with a **Ban Now** button to manually ban before the threshold is reached
- **Refresh button** — spins and reloads stats on demand

#### Configuration

Same fields as Authentik (Max Retries, Find Window, Ban Duration, Whitelist) plus a manual ban field in the watching panel.

#### Guard Dog email alerts

Same format as Authentik with jail label `TAK Server`:

```
Subject: [YOUR-SERVER-NICKNAME] fail2ban: Banned 1.2.3.4 (TAK Server)
```

---

### Uninstalling Fail2ban

Open **Fail2ban** in the sidebar, scroll to the bottom, click **Uninstall Fail2ban**. This removes:
- The `fail2ban` package
- Both jail configs
- The log forwarder service
- The Guard Dog action hooks

---

## Architecture summary

| Component | Path |
|-----------|------|
| Authentik filter | `/etc/fail2ban/filter.d/authentik.conf` |
| TAK Server filter | `/etc/fail2ban/filter.d/takserver.conf` |
| Authentik jail | `/etc/fail2ban/jail.d/infratak-authentik.conf` |
| TAK Server jail | `/etc/fail2ban/jail.d/infratak-takserver.conf` |
| Guard Dog action (Authentik) | `/etc/fail2ban/action.d/infratak-guarddog.conf` |
| Guard Dog action (TAK Server) | `/etc/fail2ban/action.d/infratak-guarddog-takserver.conf` |
| Email alert script | `/usr/local/sbin/infratak-f2b-notify` |
| Log forwarder service | `/etc/systemd/system/authentik-log-forwarder.service` |
| Authentik log | `/var/log/authentik/auth.log` |
| Guard Dog ban log | `/var/log/takguard/restarts.log` |

---

## Operator acceptance checks

### Banner

```bash
# After saving banner settings, verify config is stored
python3 -c "
import json
d = json.load(open('/home/takwerx/infra-TAK/.config/settings.json'))
print(json.dumps(d.get('customization', {}), indent=2))
"
```

Expected: `banner_enabled: true`, `banner_text` contains your text.

### Fail2ban

```bash
# Confirm fail2ban is installed and running
systemctl status fail2ban

# Confirm Authentik jail is active
fail2ban-client status authentik

# Confirm log forwarder is running
systemctl status authentik-log-forwarder

# Confirm auth.log is being written
tail -5 /var/log/authentik/auth.log

# Test a ban manually (triggers email)
echo '{"action": "login_failed", "client_ip": "10.99.99.99", "timestamp": "2026-05-02T01:00:00Z"}' \
  >> /var/log/authentik/auth.log
# Repeat 5x, then check:
fail2ban-client status authentik
```

---

## What was explicitly NOT shipped

- **Authentik Reputation policy** — deferred to v0.9.1. Fail2ban handles host-level brute-force. Reputation policy adds flow-level blocking within Authentik itself.
- **SSH jail** — only Authentik and TAK Server in v0.9.0. SSH hardening is a separate planning item.
- **TAK Server rollback** — parked since v0.8.8, still deferred.
- **Node-RED flow changes** — not in scope.

---

## Cardinal rules upheld

- **Idempotent install.** Running `_fail2ban_install_and_configure` on an already-configured box is a sub-second no-op.
- **Prerequisite-gated.** Fail2ban install is blocked if the v0.8.9 trusted-proxy CIDR fix is not confirmed — prevents banning `172.18.0.1` on unpatched installs.
- **Self-healing service file.** If the log forwarder service file is missing (older installs), toggling the Authentik jail ON re-writes it automatically.
- **No template surgery.** Banner injection uses the existing context processor — one change, all pages covered.
- **Audit trail.** `fail2ban_setup` and `customization` keys in `settings.json` record all config for every box.
