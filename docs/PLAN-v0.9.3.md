# v0.9.3 — Feature Plan

> Not yet implemented. This is a planning document.

Non-root console migration (`takwerx` → root replacement) has been moved to [v0.9.5](PLAN-v0.9.5.md).

---

## "What's using CPU/RAM?" — refresh button + move to top of expanded section

Currently the resource breakdown is triggered by a "What's using CPU/RAM?" button at the bottom of each Unattended Upgrades metric card. Once expanded, the data is static until you close and re-open it.

### What needs to happen

- Add a **Refresh** button inside the expanded breakdown div (rendered by `renderResourceBreakdown`) — sits at the top of the expanded section, calls `refreshResourceBreakdown(hostId)`.
- The breakdown content should render **below the Refresh button** (i.e. Refresh at top, then processor/RAM/disk/process tables below it). Currently the tables fill the whole div with no way to re-fetch without toggling.
- The "What's using CPU/RAM?" toggle button can stay where it is on the card — it just opens/closes. The Refresh is inside the expanded area.

---

## MediaMTX RTSP Fail2ban Jail

**Background:** tak-10 audit (2026-05-07) found mediamtx accumulating 30+ hours of CPU time over 9 days entirely from RTSP scanner traffic — one IP (`178.201.137.69`) sent 20 probes in 10 seconds, all rejected with `closed: invalid path`. The RTSP port (8554) must stay public for ATAK field clients, so firewall closure isn't an option. Fail2ban is the right fix.

Follows the same pattern as the SSH jail shipped in v0.9.2 (Feature B). Small addition — essentially a copy of the SSH jail with `journalmatch` swapped to the mediamtx systemd unit and the regex updated for the mediamtx log format.

### What needs to happen

**1. Filter file** — `/etc/fail2ban/filter.d/mediamtx-rtsp.conf`
- Match on RTSP connection opens per IP:
  ```
  failregex = \[RTSP\] \[conn <HOST>:\d+\] opened
  ```
- Rationale: counting `opened` events (not `closed: invalid path`) catches aggressive scanners before they complete their probe cycle; legitimate ATAK clients reconnect occasionally but never at scanner rates.

**2. Jail config** — written by `_fail2ban_mediamtx_write_jail()`
- `backend = systemd`, `journalmatch = _SYSTEMD_UNIT=mediamtx.service` (mediamtx is a systemd service, not Docker)
- Defaults: `maxretry=10`, `findtime=30`, `bantime=3600` — matches the aggression profile seen on tak-10 (20 connections in 10 seconds)
- Operator-configurable from the UI (same fields as SSH jail card)

**3. UI card** on the Fail2ban page — "MediaMTX RTSP Jail"
- Enable/disable toggle
- Config fields: maxretry, findtime, bantime
- Live ban list with Unban button per IP
- Stats (currently banned count, total bans)
- Guard Dog email alert fires on ban (reuses `infratak-guarddog.conf` same as SSH jail)

**4. API routes**
- `GET /api/fail2ban/mediamtx/status`
- `POST /api/fail2ban/mediamtx/config`
- `POST /api/fail2ban/mediamtx/unban`

**5. Post-update migration** — `_auto_fail2ban_mediamtx()` called from `_run_post_update()`
- If mediamtx is installed and Fail2ban is present, write the filter file and enable the jail automatically
- Idempotent — safe to re-run

### What this does NOT cover
- HLS/WebRTC probe traffic on port 8888 (those go through Caddy + Authentik; unauthenticated probes get a 401, not a mediamtx log entry)
- RTSPS (port 8322) — same filter pattern applies but a separate jail entry would be needed if that port is also being scanned

---

## Bug fix — `_auto_harden_containers()` misses authentik-worker-1 and tak-portal

**Found:** tak-10 audit 2026-05-07. After v0.9.2 "Update Now" ran `_auto_harden_containers()`, `docker inspect` showed:

```
authentik-server-1  →  CapDrop: ALL   ✅
authentik-worker-1  →  CapDrop: null  ❌
tak-portal          →  CapDrop: null  ❌
nodered             →  CapDrop: ALL   ✅
```

Server and nodered were hardened; worker and tak-portal were not. This leaves authentik-worker-1 without CVE-2026-31431 mitigation (`cap_drop: ALL` + `no-new-privileges: true`).

**Suspected root cause:** `_auto_harden_containers()` uses string anchors to locate the correct service block in `docker-compose.yml` before injecting cap_drop. The anchor for the worker service (`command: worker`) or the tak-portal service block may not have been present or matched in the compose file as it existed on tak-10 at update time. The server block (`command: server`) matched correctly; worker and tak-portal did not.

**What needs to happen:**

1. **Audit the patcher logic** — inspect `_auto_harden_containers()` in `app.py` and trace exactly which anchor string is used for the worker and tak-portal blocks. Reproduce the non-match with the actual compose files from tak-10.
2. **Fix the anchor or switch approach** — if the compose structure varies enough across installs to cause anchor misses, switch from string-anchor injection to proper YAML parsing (same approach used in `scripts/bootstrap-nodered-flatfile.sh` which uses Python's XML parser safely). For each service, parse the YAML, add `cap_drop: [ALL]` and `security_opt: [no-new-privileges:true]` under the service key, write back.
3. **Re-verify all four containers** on the next update — patcher must confirm `CapDrop: ALL` via `docker inspect` after recreating, not just assume the compose edit succeeded.
4. **Fleet remediation** — any box that ran v0.9.2 may have the same partial-hardening state. The fixed patcher in v0.9.3 must detect and correct missing cap_drop even when the compose file was already (partially) patched.

---

---

## Kernel patch banner (CVE-2026-31431 / Copy Fail)

The v0.9.2 release notes call out this CVE as "action required" but there's no UI prompt — operators who miss the release notes won't know to patch.

### What needs to happen

**Backend — `GET /api/system/kernel-patch-status`:**
- Run `uname -r` to get the running kernel version
- Run `apt list --upgradable 2>/dev/null | grep linux-image` to check if a newer kernel is available via apt
- Return `{ patched: bool, running_kernel: str, upgradable: bool }`
- Cache result for ~60s (don't hit apt on every page load)

**Frontend — dismissible banner on the Console page:**
- On page load, call the endpoint
- If `patched: false` or `upgradable: true`, show a yellow banner at the top of the Console page (above the metrics bar): "Kernel update available — patch now to fix CVE-2026-31431 (Copy Fail). Run: `apt update && apt full-upgrade && reboot`"
- Banner has an X / "I'll do it later" dismiss button that sets a flag in `localStorage` (clears automatically once the kernel is patched)
- Banner does **not** re-appear after dismiss until the next page load confirms the kernel is still unpatched

### Out of scope
- Auto-applying the kernel patch (too risky — reboot required)
- Tracking which specific CVE version the kernel fixes (kernel version strings vary by distro)

---

## Domain migration — fix `Authentik → ⬆ Update` and add Domain Migration Audit

**Source:** [GitHub issue #17](https://github.com/takwerx/infra-TAK/issues/17) — field report from a `*.test.example.com` → `*.example.com` migration on v0.9.1-alpha.

The documented domain-change flow (Caddy → Save & Reload → Authentik → ⬆ Update) left **seven distinct locations** still holding the old domain. The most critical miss — `AUTHENTIK_COOKIE_DOMAIN` — locked the operator out of Authentik entirely with no error message. The LDAP outpost miss triggered a request storm that pegged all 8 CPU cores at 100%.

### Root cause summary

| Location | Field(s) | Currently handled? |
|---|---|---|
| `~/authentik/.env` | `AUTHENTIK_HOST` | partial |
| `~/authentik/.env` | `AUTHENTIK_COOKIE_DOMAIN` | **missed** ← root cause of login loop |
| `~/authentik/docker-compose.yml` | `ldap.environment.AUTHENTIK_HOST` | **missed** ← root cause of LDAP storm |
| Brand DB row | `domain`, `cookie_domain` | partial |
| Brand DB row | `branding_default_flow_background`, `branding_custom_css` | **missed** |
| Embedded outpost | `config.authentik_host` | **missed** |
| Each proxy provider | `external_host`, `cookie_domain` | partial |
| Each proxy provider | `redirect_uris[].url` | **missed** |

### What needs to happen

**1. Fix `_authentik_update_domain()` (or equivalent) to cover all seven locations**

- `~/authentik/.env` — update **both** `AUTHENTIK_HOST` and `AUTHENTIK_COOKIE_DOMAIN` (new base domain, e.g. `.example.com`)
- `~/authentik/docker-compose.yml` — **permanently set** the LDAP outpost's `AUTHENTIK_HOST` to `http://authentik-server-1:9000/` (internal Docker hostname). This survives all future domain changes and is the correct value regardless. Do not use the public FQDN here.
- After updating compose: `docker compose up -d --force-recreate ldap` (recreate, not restart, so new env is picked up)
- Authentik API — PATCH the brand: `domain`, `cookie_domain`, scan and rewrite `branding_custom_css` for old domain strings, reset `branding_default_flow_background` to Authentik's built-in default if it contains the old domain (`/static/dist/assets/images/flow_background.jpg`)
- Authentik API — PATCH the embedded outpost: `config.authentik_host`
- Authentik API — PATCH every proxy provider with full payload (not partial): `external_host`, `cookie_domain`, and **all entries in `redirect_uris[]`**
  - Note: PATCH validation on proxy providers requires sending the full payload including `mode` and `internal_host` even when unchanged, otherwise cross-field validators reject the request
- After all API patches: full `docker compose down && docker compose up -d` (not just restart) to pick up env changes in server and worker

**2. Add `GET /api/authentik/domain-audit` — Domain Migration Audit endpoint**

- Run a PostgreSQL sweep across all text/varchar columns in the `public` schema for any occurrence of the old domain string
- Also check `~/authentik/.env` and `~/authentik/docker-compose.yml` for stale references
- Return structured results: list of `{ location, field, stale_value }` for anything still pointing at the old domain
- Used both by the UI audit panel (below) and as a post-migration verification step inside the update flow

**3. UI — "Domain Migration Audit" panel on the Authentik settings page**

- A button "Run Domain Audit" that calls the endpoint above
- Shows a table of any stale references found — location, field, current value
- Shows a green "All clear — no stale domain references found" if the sweep comes back clean
- Operator can trigger this at any time, not just post-migration

**4. Pre-flight check before domain change is applied**

- Before saving a new domain in the Caddy SSL page, warn the operator: "Changing the domain will trigger a full Authentik sync. This includes `.env`, compose, brand, outpost, and all proxy providers. A `docker compose down/up` will be required. Continue?"
- After the sync completes, automatically run the domain audit and surface any remaining stale references

**5. COMMANDS.md — document the cookie-domain symptom**

Add a troubleshooting entry:
> **Symptom:** After domain change, login at the new domain loops with no error — credentials appear to be rejected silently.  
> **Cause:** `AUTHENTIK_COOKIE_DOMAIN` in `~/authentik/.env` still references the old domain. The browser silently discards the session cookie because the domain doesn't match. No error appears in logs or the browser.  
> **Fix:** Update `AUTHENTIK_COOKIE_DOMAIN` in `~/authentik/.env` to the new base domain, then `docker compose up -d --force-recreate server worker`.

### What this does NOT cover
- ATAK client QR codes enrolled against the old domain — those continue working while Caddy redirects the old domain. Operators should re-issue QRs pointing at the new domain to remove the redirect dependency. Out of scope for automated migration.
- Multi-brand Authentik setups (non-default brand rows) — the sweep finds stale DB rows but the patch logic only targets the default brand. Multi-brand support is out of scope.

---

## Caddy custom blocks — surface the marker to operators

**Source:** [GitHub issue #4](https://github.com/takwerx/infra-TAK/issues/4) — operator asked where to put custom Caddy rules without them being overwritten.

The preservation mechanism already exists: `generate_caddyfile()` reads the existing `/etc/caddy/Caddyfile` before overwriting it and re-appends everything below the line `# --- User-added blocks (do not remove) ---`. The feature is fully built but completely invisible to operators.

### What needs to happen

**1. UI hint below the Caddyfile viewer**

The Caddyfile section on the Caddy SSL page shows the current file in a read-only code block. Add a short hint below it:

> To add custom vhosts or rules that survive Caddyfile regeneration (domain changes, deploys, updates), add them **below** the marker line:
> ```
> # --- User-added blocks (do not remove) ---
> ```
> Everything below this line is preserved automatically. Do not remove the marker itself.

**2. COMMANDS.md — new section: "Adding custom Caddy vhosts / rules"**

Add a section explaining the marker with a concrete example — e.g. a plain HTTPS vhost pointing to an internal service, or an Uptime Robot health-check endpoint on a separate subdomain:

```
# --- User-added blocks (do not remove) ---

# Example: health check vhost for Uptime Robot (no auth, plain proxy)
health.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Note that `generate_caddyfile()` is called on every domain change, deploy, and "Update Now" — the marker is the only safe place for operator-added rules.

### What this does NOT cover
- A UI editor for the custom block section (out of scope — shell access is the right tool for editing raw Caddy config)

---

## Bug fix — `cert-metadata.sh` wrong ownership breaks TAK Portal integration cert download

**Source:** Production incident (field report). TAK Portal → Integrations → Download Certs returns **HTTP 400**. On the TAK Server, `makeCert.sh` (invoked as user `tak` via SSH from Portal) fails with:

```
./makeCert.sh: line 6: cert-metadata.sh: Permission denied
mkdir: cannot create directory '': No such file or directory
```

The empty `mkdir` failure is a follow-on: `makeCert.sh` sources `cert-metadata.sh` on line 6 to populate variables including `DIR`. If that source fails because `tak` cannot read the file, `DIR` is never set and `mkdir -p "$DIR"` collapses to an empty path.

**Root cause:** `cert-metadata.sh` ends up owned by `root:root` with mode `600` after infra-TAK (or an operator) edits or replaces it as root (e.g. a deploy script, `sudo nano`, or file copy). The surrounding files (`makeCert.sh`, `config.cfg`) stay `tak:tak`, so only `cert-metadata.sh` is broken. `tak` cannot read it; cert issuance fails entirely.

**Operational one-liner (affected hosts):**
```bash
sudo chown tak:tak /opt/tak/certs/cert-metadata.sh
sudo chmod u=rw,go= /opt/tak/certs/cert-metadata.sh
# verify:
sudo -u tak bash -c 'cd /opt/tak/certs && . ./cert-metadata.sh && test -n "$DIR" && echo OK'
```

### What needs to happen

**1. Idempotent postcondition in any path that writes `cert-metadata.sh`**

Any function in `app.py` that creates, templates, or overwrites `/opt/tak/certs/cert-metadata.sh` must finish with:
```bash
chown tak:tak /opt/tak/certs/cert-metadata.sh && chmod 600 /opt/tak/certs/cert-metadata.sh
```
This applies to initial provisioning, domain change, TAK update, and any other code path that touches that file.

**2. Post-update validation task — `_validate_cert_metadata_permissions()`**

Add a validation step (called from `_run_post_update()`) that:
- Checks ownership and mode of `/opt/tak/certs/cert-metadata.sh`
- If wrong: corrects it and logs a warning
- Runs the non-destructive source test as `tak` (`sudo -u tak bash -c 'cd /opt/tak/certs && . ./cert-metadata.sh && test -n "$DIR"'`) and surfaces a UI alert if it still fails

**3. Fleet remediation**

Any box running a prior version may have the stale ownership. The v0.9.3 "Update Now" post-update hook must detect and fix it automatically — operators who never noticed the breakage (e.g. not using TAK Portal integration certs) should have it silently corrected.

**4. COMMANDS.md — document the symptom**

Add a troubleshooting entry:
> **Symptom:** TAK Portal → Integrations → Download Certs returns 400. TAK Server log shows `cert-metadata.sh: Permission denied` or `mkdir: cannot create directory ''`.  
> **Cause:** `/opt/tak/certs/cert-metadata.sh` is owned by `root:root 600`. `makeCert.sh` runs as user `tak` and cannot read it.  
> **Fix:** `sudo chown tak:tak /opt/tak/certs/cert-metadata.sh && sudo chmod 600 /opt/tak/certs/cert-metadata.sh`, then re-test from Portal.

### What this does NOT cover
- TAK Portal API error surfacing (optional UX improvement tracked separately in TAK-Portal repo — map SSH stderr patterns to a short actionable message rather than a generic 400)

---

## Out of scope for v0.9.3
- Split-server snapshot/rollback (→ v0.9.4)
- Per-feed Node-RED certs (→ future)
