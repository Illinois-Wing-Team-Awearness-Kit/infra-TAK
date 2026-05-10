# Progress — infra-TAK

> What works, what's left, current status, and known issues. Update at end of every session.

## Current status

- **Released (`main`):** `v0.9.4-alpha` (2026-05-08).
- **In flight (`dev`):** `v0.9.5-alpha` — see `docs/PLAN-v0.9.5.md` and `activeContext.md`. Several commits already on `dev`; not yet tagged.

## What works (production-validated)

### Console core
- Single-command install (`sudo ./start.sh`) on Ubuntu 22.04 LTS.
- Browser console at `:5001` (HTTPS, self-signed in IP mode, LE-fronted via Caddy in FQDN mode).
- Backdoor `https://<vps_ip>:5001` always reachable when Authentik/Caddy are broken.
- `reset-console-password.sh` and `fix-console-after-pull.sh` recoveries.
- Universal SSH recovery block in README (force-fetches official `main`, fixes wrong `origin`).
- Gunicorn-based production server (replaced Flask dev server in v0.2.0).
- "Update Now" with idempotent post-update migration ladder.
- One-click Console Rollback to previous version (recorded per Update Now cycle, single-shot).

### Caddy SSL
- FQDN mode with Let's Encrypt for all subdomains (`infratak`, `auth`, `portal`, `nodered`, `tak`, `cloudtak`, `map`, `stream`, `fedhub`).
- Custom vhosts / rules block (operator-editable, hint repositioned in v0.9.4).
- Forward auth glue for all Authentik-protected services.

### Authentik
- Fully automated deploy (~7 min): bootstrap creds, LDAP blueprint, standalone LDAP outpost container, embedded forward-auth outpost, PostgreSQL 16 + Redis, 4 GB swap auto-provisioned.
- Forward auth wired for: infra-TAK, TAK Portal, Node-RED, MediaMTX, CloudTAK, Federation Hub.
- LDAP outpost exposes `:389`/`:636` for TAK Server.
- "Connect TAK Server to LDAP" flow: patches CoreConfig, creates `webadmin` and service account, restarts TAK Server.
- Domain Migration Audit (v0.9.4): scans 7 locations for stale FQDN references, "Fix All" applies migrations.
- Reputation Policy (v0.9.2): blocks failed-login source IPs at threshold.
- Self-healing migrations resolve historical silent-default bugs (workers, cache, log level, trusted-proxy CIDRs, LDAP flow recursion, gunicorn timeout).
- Runtime config verifier asserts `ak dump_config` shows the values we set.

### TAK Server
- `.deb` upload + deploy from browser.
- CoreConfig.xml generated/patched automatically.
- Cert chain (CA, server, admin, optional `nodered`) generated via `makeCert.sh`.
- LDAP auth wiring (with optional flat-file fallback).
- JVM heap tuning button (writes `-Xms`/`-Xmx` to `/opt/tak/setenv.sh`).
- Snapshots (v0.9.2): CoreConfig + UAF + `takserver.default` + certs/ + `pg_dump`. Daily timer, manual button, automatic pre-upgrade.
- Rollback (v0.9.2): per-snapshot Restore button.
- Plugins (v0.9.2): JAR + YAML upload, inline editor, restart banner.
- Two-server topology (Server One = DB, Server Two = TAK).
- External DB support (AWS RDS, Azure DB for PG, GCP Cloud SQL, etc.) — `docs/EXTERNAL-DB-SETUP.md`.
- Snapshot upload progress bar (v0.9.5 in flight).

### TAK Portal
- Auto-configured `settings.json` (Authentik URL/token, TAK Server connection).
- Forward auth via Caddy.
- "Sync TAK Server CA" button after CA rotation.
- "Sync TAK Server to TAK Portal" forces re-read of TAK connection.
- Hardened: `cap_drop: ALL`, `no-new-privileges:true`.

### Node-RED + Configurator
- Configurator UI at `:1880/configurator`.
- Source types: ArcGIS Feature Service, Tablet Command AVL, PulsePoint, FAA TFR, KML Network Link, IPAWS.
- Per-source streaming TCP CoT port (`cotStreamPort`), with global fallback.
- DataSync (Mission API) toggle for ArcGIS / TFR / KML.
- Multi-layer ArcGIS configs, multi-geometry, per-class styling (v0.6.3+).
- Stable-ID multi-pill picker, compound UIDs (v0.6.5).
- Strict mission ownership + Purge Orphans (v0.6.5).
- Tablet Command AVL streaming (v0.7.1).
- Hardened: `nodered/node-red:4.0` pinned, UID 1000, `cap_drop: ALL`, `mem_limit: 2g`, scoped per-file cert mounts, `127.0.0.1:1880` binding for remote deploys, opt-in `adminAuth` via `~/node-red/.env`.
- Phase 1A flat-file `nodered` cert path **wired defensively** — activates if `/certs/nodered.pem` exists, falls back to `admin.pem` otherwise. Bootstrap script (`scripts/bootstrap-nodered-flatfile.sh`) ready but unrun.
- CoT attribution: `<__nodered flow="<name>"/>` injected into every CoT's `<detail>` (v0.9.2 Phase 1B).
- LDAP password propagation: 2-min cache (was 24h), via `ldap-authentication-login.session_duration=120s`.
- `nodered/deploy.sh` safety gate validates global-context backup before stopping container.
- `flows.json` ships with **zero** static feed tabs (`FEEDS=[]`).

### CloudTAK
- Compose-driven deploy.
- Hardened (`cap_drop: ALL`, `no-new-privileges:true`).
- nginx user directive auto-patched (`user nginx;` → `user root;`) so workers can write logs under `cap_drop: ALL` (v0.9.4 fix; extended to fresh-deploy path in v0.9.5).
- Reset Server Config clears all four fields (`auth`, `url`, `api`, `webtak`) — v0.9.4 fix.

### MediaMTX
- Compose-driven deploy with Authentik forward auth options.
- RTSP fail2ban jail with watching panel + manual ban (v0.9.4).
- Pill-style UI on the console.

### Federation Hub
- Local deployment (v0.9.4) — same machine as console, no SSH.
- Remote SSH deployment.
- Authentik OAuth wiring.
- Cert management + Guard Dog monitoring.

### Email Relay
- Postfix on localhost:25.
- Per-provider configuration (Mailgun, SendGrid, AWS SES, Gmail App Password, etc.).
- Auto-pushes relay settings into Authentik for password recovery.

### Guard Dog
- TAK Server health monitoring (port 8089, processes, OOM, PostgreSQL, CoT DB size, certs, disk, disk I/O).
- Optional monitors for Authentik, Node-RED, MediaMTX, CloudTAK, Federation Hub.
- 15-min boot delay + cooldowns to avoid restart loops.
- TAK Server soft start (waits for PostgreSQL + network).
- Disk I/O benchmark timer (`takdiskioguard.timer`) with on/off toggle.
- Email/SMS alerts (Email Relay → operator inbox).
- Container log limits applier.
- Auto-redeploys when console version changes (v0.4.7+).

### Fail2ban
- SSH jail (v0.9.2) — sshd filter, configurable maxretry/findtime/bantime.
- MediaMTX RTSP jail (v0.9.4) — currently-watching panel + manual ban.
- Guard Dog email alerts on ban.

### Security hardening
- All managed compose files have `cap_drop: ALL`, `no-new-privileges:true`.
- Authentik worker no longer mounts `/var/run/docker.sock` (CVE-2026-31431 fix).
- Node-RED: scoped per-file cert mounts, no whole-tree mount.
- Optional Node-RED egress allowlist (`scripts/nodered-egress-firewall.sh`).
- Idempotent post-update patcher (`_auto_harden_containers`) applies hardening to existing installs on Update Now.

## What's left to build

### Active (v0.9.5)
- TAK Server snapshot — split-server (Server One DB) `pg_dump`/`pg_restore` over SSH.
- TAK Server snapshot — finish upload/restore endpoint and validation.
- Authentik Postgres `shm_size: 256m` in compose template + `_auto_harden_containers` patcher.
- Authentik task log purge — weekly Guard Dog systemd timer.
- Move Console Rollback banner from home page to Guard Dog page.
- Bump VERSION, write `docs/RELEASE-v0.9.5-alpha.md`, selective merge `dev` → `main`, tag.

### Backlog / parked
- **Non-root console runtime.** Scaffolding (`_sudo_wrap`/`_write_priv`/`_read_priv`) is in place; user provisioning + `User=takwerx` in the systemd unit is the remaining work. Was deferred from v0.9.2.
- **Universal installer.** Currently Ubuntu 22.04 LTS only. Goal is RHEL family + Debian family.
- **Phase 0 flat-file `nodered` spike.** Not yet run on a live VPS. Needs T0–T6 from `docs/SPIKE-flatfile-nodered.md`. Decision gate: T1 + T3 pass → Phase 1A migration; otherwise → Phase 1B (keep admin cert, harden runtime).
- **Air-gapped install.** Not in scope yet — pulls Docker images and Let's Encrypt at deploy time.
- **mbtileserver module.** Plan exists (`docs/PLAN-mbtileserver-module.md`), not implemented.
- **Edge bridge module.** Plan exists (`docs/EDGE-BRIDGE-MODULE-PLAN.md`), not implemented.
- **Customization page UI** — partial; some panels still use older HTML pattern.

## Known issues

- **`flows.json` conflicts on `git pull` (test VPS).** Recovery: `git checkout -- nodered/flows.json && git pull && bash nodered/deploy.sh --no-pull`. Customers don't see this because they pull `main` and don't edit `flows.json` directly.
- **`Skipped configurator.html template injection (EACCES)`** — log noise from `build-flows.js` running inside the container after host injection already ran. Harmless.
- **TLS node `tls=undefined` in deploy log** for dynamic engine tabs created before the v0.6+ TLS fix. Cosmetic; rebuild the tab via Configurator to clear.
- **Default Node-RED cert passphrase `atakatak`.** Documented weakness if certs leak. Operator should rotate.
- **TAK Server LDAP group-direction bug.** Confirmed in TAK Server 5.x: x509 certs that resolve groups via LDAP get OUT-only direction even when LDAP says BOTH. Workaround for Node-RED: flat-file user (Phase 1A) or `admin` cert. See `docs/HANDOFF-LDAP-AUTHENTIK.md`.
- **Two-server snapshot rollback (TAK Server)**: doesn't work yet — captures config files but not Postgres dump. Operators on split deployments must `pg_dump` manually until v0.9.5 ships.
- **Snapshots taken before v0.9.2** lack `UserAuthenticationFile.xml`. Rollback logs a notice and skips that step.
- **Phase 1A rollback gotcha.** If the operator runs `bootstrap-nodered-flatfile.sh` and then restores a pre-bootstrap snapshot, UAF won't have `<user identifier="nodered">`. Re-run bootstrap (idempotent) after restore.

## Validated environments

- `tak-10` (SSDNodes, single-server) — primary maintainer test VPS, tracks `dev`. ✅
- `responder` (busy, Mission API / DataSync load) — secondary test VPS. ✅
- `tak-test-3` (Azure D8as_v5, P10 64 GiB OS disk, ~145 MB/s sync write) — Azure NAT validation done in v0.8.6. ✅
- DigitalOcean fast-disk VPS — clean baseline. ✅
- Various `ssdnodes` slow-disk VPS — caught the v0.8.8 LDAP recursion and v0.8.x Authentik tunings.

## Pending validation

- v0.9.5 changes on `tak-10` (just pulled `dev` and restarted the console; next "Update Now" or restart will fire the v0.9.5 migrations).
- Phase 0 flat-file spike (six curl tests) — never run.
