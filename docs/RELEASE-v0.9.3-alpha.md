# Release Notes — v0.9.3-alpha

## What ships

Six features and three bug fixes shipped in one build. All changes apply automatically on next "Update Now" — no manual intervention required unless called out below.

---

### Feature A — CPU/RAM refresh button

The "What's using CPU/RAM?" resource breakdown on the Console page now includes a **Refresh** button at the top of the expanded section. Previously the data was static until you closed and re-opened the panel.

- Refresh button appears inside the expanded `renderResourceBreakdown` div — click to re-fetch without collapsing the panel
- Existing toggle button (opens/closes the section) is unchanged

---

### Feature B — MediaMTX RTSP Fail2ban Jail

Extends the Fail2ban module with a dedicated jail for MediaMTX RTSP scanner traffic — the same pattern as the SSH jail shipped in v0.9.2.

**Background:** tak-10 audit (2026-05-07) found MediaMTX accumulating 30+ hours of CPU time over 9 days entirely from RTSP scanner traffic. One IP (`178.201.137.69`) sent 20 probes in 10 seconds, all rejected with `closed: invalid path`. The RTSP port (8554) must stay public for ATAK field clients, so firewall closure isn't an option.

**What ships:**

- **Filter file** — `/etc/fail2ban/filter.d/mediamtx-rtsp.conf`: matches `[RTSP] [conn <HOST>:<port>] opened` events — catches scanners before their probe cycle completes
- **Jail config** — `/etc/fail2ban/jail.d/infratak-mediamtx-rtsp.conf`: `backend=systemd`, `journalmatch=_SYSTEMD_UNIT=mediamtx.service`, defaults 10 opens/30s → 1h ban
- **UI card** on Fail2ban page: enable toggle, stats, configurable thresholds, ban list with Unban button. Card is only visible when MediaMTX is detected on the host
- **Guard Dog email alert** fires on ban (reuses `infratak-guarddog.conf`)
- **Post-update auto-install**: if MediaMTX and Fail2ban are both present and the jail does not yet exist, it is written and loaded automatically on next "Update Now"
- **API routes**:
  - `GET /api/fail2ban/mediamtx/status`
  - `POST /api/fail2ban/mediamtx/config`
  - `POST /api/fail2ban/mediamtx/unban`

---

### Feature C — Kernel Patch Banner (CVE-2026-31431)

v0.9.2 release notes called out kernel patching as "action required" but there was no UI prompt — operators who missed the notes wouldn't know to patch.

**What ships:**

- **`GET /api/system/kernel-patch-status`** — runs `uname -r` and `apt list --upgradable | grep linux-image`, returns `{ patched, running_kernel, upgradable }`, cached 60s
- **Dismissible banner** on Console page: appears above the metrics bar when a kernel update is available. Includes the one-liner `apt update && apt full-upgrade && reboot`. Dismiss button stores a `localStorage` flag; banner re-appears on the next page load that still shows an available kernel update.

---

### Feature D — Authentik Domain Migration Fix

**Source:** [GitHub issue #17](https://github.com/takwerx/infra-TAK/issues/17) — field report from a `*.test.example.com` → `*.example.com` migration. Seven locations held the old domain after the previously documented update flow. The most critical miss (`AUTHENTIK_COOKIE_DOMAIN`) locked the operator out entirely. The LDAP miss (`docker-compose.yml AUTHENTIK_HOST`) triggered a request storm that pegged all 8 CPU cores.

**Locations now covered by the new `_authentik_sync_all_domain_refs()` function:**

| Location | Field(s) | Previously |
|---|---|---|
| `~/authentik/.env` | `AUTHENTIK_HOST` | partial |
| `~/authentik/.env` | `AUTHENTIK_COOKIE_DOMAIN` | **missed** ← login loop root cause |
| `~/authentik/docker-compose.yml` | `ldap.AUTHENTIK_HOST` | **missed** ← CPU storm root cause |
| Authentik brand | `domain`, `cookie_domain` | partial |
| Authentik brand | `branding_custom_css`, `branding_default_flow_background` | **missed** |
| Embedded outpost | `config.authentik_host` | **missed** |
| Proxy providers (all) | `external_host`, `cookie_domain`, `redirect_uris[].url` | partial |

The LDAP outpost's `AUTHENTIK_HOST` is now permanently set to `http://authentik-server-1:9000/` (internal Docker hostname) — this is the correct value regardless of domain and survives all future domain changes.

**Trigger:** The sync runs automatically whenever "Save & Reload" is clicked on the Caddy SSL page with a changed FQDN. It also runs during "Update config & reconnect" on the Authentik page.

**Pre-flight warning:** A `confirm()` dialog now appears before applying a domain change, listing the old and new domains and warning that Authentik will restart (~30s downtime).

**New `GET /api/authentik/domain-audit` endpoint:**

Sweeps `.env`, `docker-compose.yml`, and all Authentik API objects for values that don't match the current FQDN. Returns `{ ok, fqdn, stale: [{location, field, current_value}], clean }`.

**Domain Migration Audit panel on Authentik page:**

"Run Audit" button surfaces any remaining stale references in a table. If all clean, shows a green "All clear" message. Includes a "Sync Domain Now" button that triggers a full re-sync.

---

### Feature E — Caddy Custom Blocks — Operator Hint

Operators can add custom vhosts and rules to the Caddyfile without them being overwritten by infra-TAK. The preservation mechanism already existed (marker line `# --- User-added blocks (do not remove) ---`) but was invisible.

**What ships:**

- **UI hint** below the Caddyfile viewer on the Caddy SSL page: explains the marker and how to use it
- **`docs/COMMANDS.md`** — new section "Caddy — adding custom vhosts / rules" with a concrete example

---

### Feature F — Container Hardening Audit in `_auto_harden_containers()`

After recreating Authentik containers during post-update hardening, `docker inspect` is now run on `authentik-server-1` and `authentik-ldap-1` to confirm `CapDrop: ALL` was actually applied. A warning is printed if not. This closes a silent-failure gap where a compose patch could succeed but the running container still miss the capability drop.

---

## Bug fixes

### CloudTAK Reset Config — SQL error (`relation "servers" does not exist`)

**Symptom:** Clicking "Reset Config" on the CloudTAK page returned `SQL reset failed: ERROR: relation "servers" does not exist`.

**Root cause:** The table is named `server` (singular), not `servers`. Additionally, the `auth` column is `NOT NULL jsonb` — `SET auth = NULL` is rejected by the schema constraint.

**Fix:** `UPDATE server SET auth = '{}'::jsonb WHERE id = 1;` — correct table name, resets auth to an empty object (the schema default) rather than NULL.

---

### cert-metadata.sh wrong ownership breaks TAK Portal integration cert download

**Symptom:** TAK Portal → Integrations → Download Certs returns HTTP 400. TAK Server logs show `cert-metadata.sh: Permission denied` followed by `mkdir: cannot create directory ''`.

**Root cause:** `/opt/tak/certs/cert-metadata.sh` ends up owned `root:root 600` after a deploy or manual edit as root. `makeCert.sh` sources it as user `tak`, which cannot read a `root:root 600` file. With `cert-metadata.sh` unreadable, `$DIR` is never populated and the subsequent `mkdir -p "$DIR"` collapses to an empty path.

**Fix:** `_run_post_update()` now checks ownership and mode of `cert-metadata.sh` and corrects both if wrong (sets `tak:tak 600`). Also runs a non-destructive source test (`sudo -u tak bash -c 'cd /opt/tak/certs && . ./cert-metadata.sh && test -n "$DIR"'`) and logs a warning if it still fails. Applied to all hosts on next "Update Now".

**One-liner for affected hosts (if updating now is not possible):**
```bash
sudo chown tak:tak /opt/tak/certs/cert-metadata.sh
sudo chmod u=rw,go= /opt/tak/certs/cert-metadata.sh
sudo -u tak bash -c 'cd /opt/tak/certs && . ./cert-metadata.sh && test -n "$DIR" && echo OK'
```

---

### Authentik cookie domain — login loop after domain change (docs/COMMANDS.md)

A new troubleshooting entry has been added to `docs/COMMANDS.md`:

> **Symptom:** After domain change, login at the new domain loops with no error — credentials appear to be rejected silently.  
> **Cause:** `AUTHENTIK_COOKIE_DOMAIN` in `~/authentik/.env` still holds the old domain. The browser discards the session cookie silently.  
> **Fix:** Use the Domain Migration Audit on the Authentik page, or run `_authentik_sync_all_domain_refs` via "Update config & reconnect".

---

## Scope discipline — what is NOT in v0.9.3

- Non-root console migration (`takwerx` system user) — deferred to v0.9.5
- Split-server TAK snapshot/rollback — deferred to v0.9.4
- RTSPS (port 8322) MediaMTX jail — same pattern as RTSP but separate entry; deferred
- Per-feed Node-RED certs — future
- Multi-brand Authentik domain migration — single default brand only

## Version

`0.9.2-alpha` → `0.9.3-alpha`
