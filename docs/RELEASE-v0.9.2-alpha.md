# Release Notes — v0.9.2-alpha

## Action required — kernel patch (CVE-2026-31431 / Copy Fail)

A Linux kernel vulnerability published April 29 2026 ([CVE-2026-31431](https://nvd.nist.gov/vuln/detail/CVE-2026-31431), "Copy Fail") allows an unprivileged user with code execution inside a container to escalate to a root shell. **This is a kernel bug — the only real fix is patching the kernel.**

```bash
apt update && apt full-upgrade
reboot
```

The reboot triggers Guard Dog's boot sequencer, which restarts all containers in the correct order automatically. No other operator action is required.

**What v0.9.2 adds on top of the kernel patch** (applied automatically on next "Update Now"):

- Removes `/var/run/docker.sock` from the Authentik worker container. This socket was present because the upstream Authentik quickstart includes it for Docker-managed outposts; infra-TAK uses the embedded outpost and a standalone LDAP container, so the socket was never needed. With it mounted, a compromised Authentik worker was equivalent to host root — that path is now closed.
- Adds `cap_drop: ALL` and `no-new-privileges: true` to Authentik (server, worker, ldap), TAK Portal, and CloudTAK (api, events, media). With `cap_drop: ALL`, even if Copy Fail achieves container root the privilege escalation step fails because no Linux capabilities are available.
- **Node-RED hardening lands alongside this release**: image pin (`nodered/node-red:4.0` — was `:latest`), `cap_drop: ALL` + `no-new-privileges: true`, `user: 1000:1000`, `mem_limit: 2g`, `restart: unless-stopped`, `env_file: .env` for optional adminAuth. Local deploy also switches to scoped per-file cert mounts (`/opt/tak/certs/files/<name>:/certs/<name>:ro`) instead of mounting the whole `/opt/tak/certs/files` tree. Remote deploy port binding fixed from `0.0.0.0:1880` → `127.0.0.1:1880` (was inconsistent with local). All applied via `_auto_nodered` post-update patcher — operators see `Post-update: Node-RED ...` log lines.

These mitigations are applied automatically to existing running deployments on next "Update Now" — no reinstall of Authentik, TAK Portal, or Node-RED is needed. The console log will show:

```
Post-update: Authentik — removed docker.sock from worker
Post-update: Authentik compose hardened — recreating worker/server/ldap
Post-update: TAK Portal compose hardened — recreating container
```

To verify after updating:

```bash
# Must return empty — docker.sock is gone
docker inspect authentik-worker-1 | grep docker.sock

# Must show CapDrop set on each service
docker inspect authentik-worker-1 | grep -A5 CapDrop
docker inspect tak-portal         | grep -A5 CapDrop
```

---

## What ships

Six features shipped in one build.

---

### Feature A — Authentik Reputation Policy

Flow-level brute-force blocking inside Authentik, complementary to Fail2ban.

- Every failed login decrements the source-IP score by 1
- When the score drops below the threshold (default -5), Authentik blocks the login at the flow level — before the password stage even runs
- Scores recover automatically over time
- Applied automatically to `ldap-authentication-flow` on first startup via `_authentik_setup_reputation_policy()`
- **UI card** on the Authentik page (visible when Authentik is running): enable/disable toggle, configurable threshold, live table of top flagged IPs, "Clear All Scores" button
- **API routes**:
  - `GET /api/authentik/reputation/status`
  - `POST /api/authentik/reputation/config`
  - `POST /api/authentik/reputation/scores/clear`

---

### Feature B — SSH Fail2ban Jail

Extends the existing Fail2ban module with an SSH jail for host-level brute-force protection.

- Uses the built-in `sshd` filter; monitors `/var/log/auth.log`
- Default thresholds: maxretry=3, findtime=10 min, bantime=60 min (stricter than Authentik — SSH brute-force is more severe)
- Opt-in via the Fail2ban page — does not require Authentik to be installed
- Guard Dog email alert fires on ban (reuses `infratak-guarddog.conf`)
- **New UI card** on the Fail2ban page: enable toggle, stats, config, whitelist (ignoreip), ban list, unban button
- **API routes**:
  - `GET /api/fail2ban/ssh/status`
  - `POST /api/fail2ban/ssh/config`
  - `POST /api/fail2ban/ssh/unban`

---

### Feature C — TAK Server Snapshots

Automated and on-demand snapshots of the full TAK Server state:

| What | How | Stored at |
|------|-----|-----------|
| Installed TAK version | `dpkg -l takserver` | snapshot metadata |
| `CoreConfig.xml` | file copy | `/opt/tak/snapshots/<label>/CoreConfig.xml` |
| `UserAuthenticationFile.xml` | file copy | `/opt/tak/snapshots/<label>/UserAuthenticationFile.xml` |
| JVM heap settings | file copy | `/opt/tak/snapshots/<label>/takserver.default` |
| PostgreSQL cot dump | `pg_dump -Fc` via `docker exec takserver-db` | `/opt/tak/snapshots/<label>/cot.pgdump` |
| Certificates | directory copy | `/opt/tak/snapshots/<label>/certs/` |

`UserAuthenticationFile.xml` capture closes a gap that would otherwise lose flat-file users (admin, webadmin, optional Phase 1A `nodered`) on rollback even though their cert files in `certs/` survived. Snapshot + rollback are now consistent — restoring takes you to a fully working TAK Server state including who can authenticate.

**Pre-upgrade snapshot**: automatically taken at the start of every `run_takserver_upgrade()`. If the snapshot fails the upgrade is **aborted** — current data is protected.

**Scheduled snapshots**: systemd timer (daily by default at 02:00 local time). Operator configures schedule and retention count from the UI.

**Manual snapshot**: "Take Snapshot Now" button on the TAK Server page.

**Off-box export**: each snapshot has a "Download" button (`GET /api/takserver/snapshot/<label>/download`) that streams a `.tar.gz`.

**API routes**:
- `GET /api/takserver/snapshots`
- `POST /api/takserver/snapshot/run`
- `GET /api/takserver/snapshot/status`
- `GET /api/takserver/snapshot/<label>/download`
- `DELETE /api/takserver/snapshot/<label>`
- `GET /api/takserver/snapshot/schedule`
- `POST /api/takserver/snapshot/schedule`

---

### Feature D — TAK Server Rollback

One-click restore from any snapshot:

1. Validates snapshot has a `cot.pgdump`
2. Stops `takserver`
3. Restores `CoreConfig.xml`, `UserAuthenticationFile.xml`, `takserver.default`, `certs/`
4. Runs `pg_restore --clean -d cot` inside the `takserver-db` container
5. Starts `takserver`

Older snapshots (taken before v0.9.2) won't have a `UserAuthenticationFile.xml` in them — the rollback logs `snapshot has no UserAuthenticationFile.xml — leaving current file in place` and skips that one step. Take a fresh snapshot post-update to fully use the new behavior.

**UI card** on TAK Server page: snapshot table, Restore / Download / Delete buttons, scheduled snapshot config.

**API routes**:
- `POST /api/takserver/rollback`
- `GET /api/takserver/rollback/log`

---

### Feature E — infra-TAK Console Rollback

Before every "Update Now", the console records the current version and git tag. If the update breaks something, one click rolls back.

- "Update Now" saves `settings.console_rollback = {available, version, tag, snapshot_at}` before pulling
- Console page shows a yellow "Rollback available — vX.Y.Z" bar after updates
- One click: fetches the previous tag from GitHub and checks it out, then restarts
- Rollback availability is cleared after use (one rollback per update cycle)

**API routes**:
- `POST /api/console/rollback`

---

### Feature F — TAK Server Plugins

New collapsible "TAK Server Plugins" section on the TAK Server page (visible when TAK is installed). Based on the official TAK Server Plugin SDK (tak.gov) and reference plugins including TAK CAD Server Plugin (RTX/BBN).

**Plugin mechanics (from SDK):**
- Plugin JARs live at `/opt/tak/lib/` — loaded by the plugin manager on startup
- Plugin configs auto-generated at `/opt/tak/conf/plugins/<fully.qualified.ClassName>.yaml` on first run
- Restart required after any JAR or config change

**What the section does:**

- **Installed plugins list**: on section open, scans `/opt/tak/lib/*.jar` and `/opt/tak/conf/plugins/*.yaml` and renders a table — plugin name, config status, Edit config button, Remove button
- **Install**: single upload area accepts `.jar` (copied to `/opt/tak/lib/`) or `.yaml` (copied to `/opt/tak/conf/plugins/`) — portal routes by extension. After any file placement, a "Restart TAK Server to apply changes" banner appears with a Restart button
- **Edit config**: inline expand per plugin — reads YAML, shows in monospace textarea, save writes it back. If no YAML yet: "Config will be generated on first run after restart"
- **Remove**: deletes JAR from `/opt/tak/lib/` + matching YAML from `/opt/tak/conf/plugins/` if present, then prompts restart

**API routes:**
- `GET /api/takserver/plugins/list`
- `POST /api/upload/takserver-plugin`
- `POST /api/takserver/plugins/install-jar`
- `POST /api/takserver/plugins/install-yaml`
- `GET /api/takserver/plugins/config/<classname>`
- `POST /api/takserver/plugins/config/<classname>`
- `POST /api/takserver/plugins/remove/<jarname>`

---

---

### Bug fix — Guard Dog socket leak (console hang after ~3 days)

**Symptom:** After ~3 days of uptime, the infra-TAK console becomes completely unresponsive. Port 5001 stops accepting connections (TLS handshake never completes). The root process (gunicorn PID) accumulates 140+ open file descriptors — all leaked sockets — and one background thread stalls in `select()` waiting on all of them simultaneously.

**Root cause:** Five calls to `urllib.request.urlopen()` in the Guard Dog health check functions (`_guarddog_health_check` and `_monitor_health_check`) stored the response in a variable but never called `resp.close()`. The response object holds the underlying TCP socket open. Python's garbage collector eventually reclaims most, but not all — so over 3 days at one poll per 25 seconds, ~135 sockets survive and accumulate in the process.

The five affected checks: `authentik` service check, `nodered` service check, `remotedb` monitor, `authentik_http` monitor, `nodered_http` monitor.

Every other `urlopen` call in the file already used `with urllib.request.urlopen(...) as resp:`. These five were missed.

**Fix:** Wrap all five with the context manager (`with ... as resp:`), which calls `resp.close()` on exit. No logic change — identical behavior, sockets released immediately after each check.

**Diagnosed live** on responder (ssdnodes) after 3 days of uptime: 144 FDs on PID, `wchan=do_select`, thread CPU time 6× higher than peers, `curl https://127.0.0.1:5001/` timing out at 20s. Console restored immediately after `systemctl restart takwerx-console`. Other boxes not yet affected (shorter uptime).

---

### Security — Console service runs as `takwerx` (non-root)

The infra-TAK console (gunicorn/`app.py`) now runs as a dedicated `takwerx` system user (UID != 0) instead of root. Applied automatically when `sudo ./start.sh` is re-run during update.

**What changes for operators:**
- `start.sh` creates the `takwerx` user on first run, adds it to `sudo` and `docker` groups, writes `/etc/sudoers.d/takwerx` with `NOPASSWD: ALL`
- Existing module data (`~/authentik`, `~/CloudTAK`, `~/node-red`, `~/TAK-Portal`) is migrated from `/root/` to `/home/takwerx/` automatically if found
- `ps aux` will show gunicorn workers owned by `takwerx` instead of `root`
- The generated systemd units for MediaMTX, mediamtx-webeditor, and the TAK snapshot timer also set `User=takwerx`
- All operator commands are unchanged — `sudo ./start.sh` still does everything

**Security posture:** `takwerx` has `NOPASSWD: ALL` sudo and is in the `docker` group (equivalent to root for container access), so this is not a strong isolation boundary. The gain is: UID != 0 satisfies common compliance audit tools, and a compromise of the console process does not directly yield a root shell or access to root's SSH keys, history, or authorized_keys.

---

---

## Node-RED hardening + flat-file `nodered` user migration (v0.9.2)

This is a multi-phase project running in parallel with the v0.9.2 feature set. The runtime hardening (Phase 2) ships now and is applied automatically. The cert-identity migration (Phase 1A) is gated on a validation spike the operator runs on the live VPS.

### Background — why admin.pem and why that's a problem

Node-RED uses `/certs/admin.pem` to authenticate to TAK Server's Mission API. This is the same pattern TAK Portal (the official TAK component) uses — TAK Portal also authenticates with the admin cert. The cert works for DataSync because admin is always a mission owner.

The problem isn't the pattern — it's the blast radius. If Node-RED's runtime is compromised (malicious npm contrib, supply-chain attack, attacker through the editor), they get full TAK admin authority over every mission on the server. Before this release there were no egress controls, so a compromised flow could also read the cert off the container filesystem and POST it to an external host.

**Why a scoped cert couldn't just replace admin:** In April 2026, a scoped cert (`nodered-global-datasyncfeed`) was tested and failed. TAK Server 5.x has an unfixed bug — x509 certs that resolve their groups via LDAP get **OUT-only** direction on every group, even when the base group says BOTH. The Mission API write path requires IN direction and returns 403 without it. That bug blocked every previous attempt to use a non-admin cert for DataSync.

### The unlock — flat-file users bypass the LDAP group bug

The `admin` user is defined in `/opt/tak/UserAuthenticationFile.xml` — it's a **flat-file** user, not an LDAP user. Flat-file users declare their groups directly in XML. They never touch the LDAP resolution path that has the bug.

**Hypothesis:** create a flat-file `nodered` user with `ROLE_USER` (not ROLE_ADMIN), generate its cert, and have Node-RED create DataSync missions itself via `PUT /Marti/api/missions/{name}?creatorUid=nodered`. The creator is automatically the owner — no admin involvement, no role-elevation hack.

Whether this works is gated on the **Phase 0 spike** (see below and `docs/SPIKE-flatfile-nodered.md`).

### What ships in v0.9.2 (applies to all installs, no spike needed)

**Container hardening (auto-applied via `_auto_nodered` on next update):**
- Image pinned: `nodered/node-red:4.0` (was `:latest` — no more uncontrolled updates)
- `user: 1000:1000` — non-root inside container
- `cap_drop: [ALL]` + `no-new-privileges:true` — no capability inheritance even if a flow gets container root
- `mem_limit: 2g` — caps memory against runaway flows
- `restart: unless-stopped`
- `env_file: .env` — scaffolds `~/node-red/.env` with commented-out adminAuth vars (opt-in defense-in-depth on top of Caddy+Authentik)
- Local deploy: scoped per-file cert mounts (only the cert files Node-RED actually uses — not the whole `/opt/tak/certs/files` tree)
- Remote deploy: port binding fixed to `127.0.0.1:1880` (was `0.0.0.0:1880`)

**Cert preference in `nodered/deploy.sh`:**
If `/opt/tak/certs/files/nodered.pem` exists on the host, the deploy script now auto-fills `tls_tak` with it instead of `admin.pem`. Falls back to admin otherwise. Existing installs continue working unchanged.

**CoT-level attribution (`nodered/build-flows.js`):**
Every CoT event Node-RED streams now includes `<__nodered flow="<feedName>"/>` inside `<detail>`. Invisible to ATAK field users (TAK Server parser ignores unknown tags), but lets centralized log collection tie each CoT to a specific Configurator feed even when all feeds share a cert identity.

**New scripts:**
- `scripts/nodered-egress-firewall.sh` — opt-in iptables egress allowlist. Apply it and a compromised Node-RED container cannot ship a stolen cert off-host. See `docs/NODERED-EGRESS.md`.
- `scripts/bootstrap-nodered-flatfile.sh` — Phase 1A bootstrap (run only after spike confirms the approach works). Generates cert, edits UserAuthenticationFile.xml safely via Python XML parser, restarts TAK Server, verifies.

**New docs:**
- `docs/SPIKE-flatfile-nodered.md` — step-by-step Phase 0 test plan
- `docs/NODERED-EGRESS.md` — egress allowlist guide (iptables and Squid sidecar)
- `docs/NODERED-DEPLOY.md` — updated with cert priority, Phase 1A migration runbook, adminAuth enable instructions
- `docs/NODERED-OPERATIONS.md` — Phase 3 runbook: Project mode, package pinning, docker.sock check, logging

### Phase 0 — Validation spike (operator runs this on VPS)

**Before running any of the cert-migration steps**, follow `docs/SPIKE-flatfile-nodered.md`. Six curl tests against the live TAK Server:

| Test | Gate |
|---|---|
| T1: `GET /Marti/api/groups/all` with `nodered.pem` | **Critical: must show BOTH IN and OUT for DATASYNC-FEEDS** |
| T3: stream CoT + `PUT /missions/spike-test/contents` | **Critical: must return 200** |
| T2, T4, T5, T6 | Confirming tests |

- T1 + T3 pass → **Phase 1A** (migrate to flat-file nodered cert)
- Either fails → **Phase 1B** (admin cert stays, hardening-only path)

### Phase 1A — Flat-file migration (only if spike passes)

After Phase 0 confirms the approach works:

```bash
# 1. Generate cert + add nodered to UserAuthenticationFile.xml + restart TAK
sudo bash scripts/bootstrap-nodered-flatfile.sh apply

# 2. Re-deploy Node-RED — deploys deploy.sh auto-detects nodered.pem and switches tls_tak
cd ~/infra-TAK && bash nodered/deploy.sh

# 3. In Node-RED editor: TAK Mission API TLS → confirm /certs/nodered.pem → passphrase atakatak → Deploy

# 4. In Configurator: open each feed → set group (DATASYNC-FEEDS) → "Create / verify in TAK"
#    New missions: nodered becomes owner, no elevation hack needed
#    Existing admin-owned missions: continue working via elevation hack (which now uses nodered.pem)
```

**What this achieves:** Node-RED runs on `ROLE_USER` cert, not admin. Missions nodered creates, nodered owns. TAK Server logs show `creatorUid=nodered` instead of `admin`. Blast radius drops from "full TAK admin authority" to "DataSync feeds in the configured group."

### Phase 1B — Fallback if spike fails

Keep `admin.pem`. The runtime hardening (Phase 2) still applies — that's cert-agnostic. The CoT `<__nodered flow="...">` attribution is also in place for log correlation. `docs/SECURITY-AUDIT-v0.2.0-alpha.md` documents why admin is unavoidable on this install, with a link to the spike results.

---

## Scope discipline — what is NOT in v0.9.2

- Two-server TAK rollback (complex, lower demand)
- Rolling back TAK Portal or Authentik integrations
- Rolling back infra-TAK more than one version back
- Node-RED cert/passphrase rotation runbook (operator decides schedule)
- Per-feed-group certs (`nodered-fire`, `nodered-weather`, etc.) — single flat-file user is sufficient

## Version

`0.9.1-alpha` → `0.9.2-alpha`
