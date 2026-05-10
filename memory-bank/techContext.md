# Tech Context — infra-TAK

> Technologies, dev setup, constraints, and dependencies. Update when stack changes.

## Stack at a glance

| Layer | Tech | Where |
|---|---|---|
| Language (console) | Python 3 (Flask + Gunicorn) | `app.py`, served on `:5001` HTTPS |
| Language (Node-RED subsystem) | Node.js (build script) + Node-RED runtime | `nodered/build-flows.js`, `nodered/flows.json` |
| OS (supported) | Ubuntu 22.04 LTS (production target) | Goal: universal installer |
| OS (dev) | macOS (Apple Silicon, this maintainer) | Code edited locally, deployed to VPS |
| Container runtime | Docker + Docker Compose v2 | All services except TAK Server itself |
| Reverse proxy | Caddy 2.x | Let's Encrypt + forward auth |
| Identity | Authentik 2026.x | LDAP outpost + embedded forward auth |
| TAK Server | Official `.deb` from tak.gov, 5.x | Native install (not containerized) |
| Database (TAK) | PostgreSQL 15 | Local or remote (two-server) |
| Database (Authentik) | PostgreSQL 16 (`postgres:16-alpine` in compose) | Container, separate DB |
| Process supervision | systemd | `takwerx-console.service`, Guard Dog timers |
| Monitoring | Guard Dog (custom bash + systemd timers) | TAK Server health, disk I/O, CoT DB size, certs |
| Auth: console | Session-based, password hash in `.config/auth.json` | Plus IP-mode backdoor at `:5001` |
| Auth: services behind FQDN | Authentik forward auth via Caddy | TAK Portal, Node-RED, etc. |
| Auth: TAK Server | LDAP via Authentik outpost (`:389`/`:636`) | Plus optional flat-file fallback |

## Console runtime

- **Entry point:** `app.py` (loaded by Gunicorn).
- **Port:** `5001` HTTPS (self-signed by default, switchable to LE via Caddy in FQDN mode).
- **Service unit:** `/etc/systemd/system/takwerx-console.service` — `User=root` in v0.9.x (non-root migration was deferred from v0.9.2 to a future release; scaffolding is in place via `_sudo_wrap`/`_write_priv`/`_read_priv`).
- **Working directory:** Whatever `start.sh` was run from on first boot (typically `/home/takwerx/infra-TAK`, but NOT guaranteed — varies per install). **ALWAYS resolve dynamically:** `grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service`. Never hardcode `/root/infra-TAK` or `/home/takwerx/infra-TAK` in pull commands given to the operator — this causes `cd: No such file or directory` failures.
- **Config / state:** `<install_dir>/.config/auth.json` (password hash, mode 600), `<install_dir>/.config/settings.json` (FQDN, `last_version`, migration outcomes, schedules).
- **Logs:** `journalctl -u takwerx-console`. UI long-running ops stream `plog` lines into the per-page log panel.

## Dependencies (Python)

The list lives in `start.sh` (apt + pip) — no `requirements.txt` is committed because deps are pinned in code. Notable:
- `Flask`, `gunicorn` — HTTP server.
- `requests`, `urllib.request` — outbound HTTP (Authentik API, Caddy admin, etc.).
- `cryptography` / `pyOpenSSL` — cert generation, parsing, validation.
- `paramiko` — SSH for remote deploys (Authentik / CloudTAK / MediaMTX / Node-RED / Federation Hub remote, two-server TAK).
- `bcrypt` / `werkzeug.security` — password hashing.
- `psycopg2-binary` — Postgres queries (TAK DB diagnostics, Authentik DB direct queries, snapshot pre-flight checks).

## Dependencies (Node-RED side)

- `nodered/node-red:4.0` (image — pinned, not `:latest`).
- Container runs as UID `1000:1000`, `cap_drop: [ALL]`, `no-new-privileges:true`, `mem_limit: 2g`, `restart: unless-stopped`.
- Contrib packages installed inside the container — pinned (no `^`/`~`) per `docs/NODERED-OPERATIONS.md`.
- `build-flows.js` runs both on host (during `nodered/deploy.sh`) and inside the container (post-install hook).

## External dependencies (per service)

- **Authentik** — Docker images from ghcr.io. Bootstrap creds auto-generated. PostgreSQL 16 + Redis (compose).
- **TAK Server** — Operator-uploaded `.deb` from tak.gov. PostgreSQL 15 (apt install or remote).
- **Caddy** — apt repo (cloudsmith). Let's Encrypt for ACME.
- **CloudTAK** — Docker image from upstream (compose).
- **MediaMTX** — Docker image from upstream (compose).
- **Node-RED** — `nodered/node-red:4.0` Docker image.
- **Federation Hub** — Operator-uploaded `.deb` from tak.gov (same packaging as TAK Server).
- **Guard Dog** — Pure bash + Python helpers; no external runtime deps beyond what TAK Server already requires.
- **Email Relay (Postfix)** — apt install, configured against operator-supplied SMTP relay (Mailgun, SendGrid, AWS SES, Gmail App Password, etc.).

## Network & ports

| Service | Port | Proto | Notes |
|---|---|---|---|
| infra-TAK Console | `5001` | HTTPS | Backdoor (always direct IP) |
| Caddy | `80` / `443` | HTTP/HTTPS | Reverse proxy + LE |
| TAK Server | `8089` | TLS | TAK client connections |
| TAK Server | `8443` | HTTPS | Admin (client cert auth) |
| TAK Server | `8446` | HTTPS | Admin (LE cert + LDAP/password auth) |
| TAK Server | `8087` | TCP | Disabled by default |
| PostgreSQL (TAK) | `5432` | TCP | localhost or remote (two-server) |
| Authentik | `9090` | HTTP | API + admin (proxied via Caddy) |
| Authentik | `9443` | HTTPS | Direct, rarely needed |
| LDAP outpost | `389` / `636` | TCP | TAK Server uses for auth |
| TAK Portal | `3000` | HTTP | Proxied via Caddy |
| Email Relay | `25` | SMTP | localhost only |
| Node-RED | `1880` | HTTP | Proxied via Caddy on FQDN, direct on IP-mode |
| MediaMTX | `8554` / `8889` / `5080` | RTSP / WebRTC+HLS / HTTP | Editor on `:5080` |
| CloudTAK | `5000` | HTTP | Proxied via Caddy |
| Federation Hub | `9100` (admin) / `9101–9103` (peers) / `8080` (Caddy → localhost) | HTTPS / TCP / HTTP | |

## Deployment topologies

1. **Single-server.** All services + console + TAK Server on one VPS. Default.
2. **Two-server TAK.** Server One = PostgreSQL DB. Server Two = TAK Server core + console + everything else. Configured per-host in TAK Server settings; deploy from console via SSH.
3. **Remote per-service.** Authentik / CloudTAK / MediaMTX / Node-RED / Federation Hub each can be deployed to a different host via SSH. Console SSHs and runs Docker/scripts there.
4. **External DB.** TAK Server can target AWS RDS, Azure Database for PostgreSQL, Google Cloud SQL, or any PostgreSQL 15 host (`docs/EXTERNAL-DB-SETUP.md`).

## Test/maintainer infrastructure

- **VPS — `tak-10` (`172.93.50.47`):** maintainer's primary test box. SSDNodes. Tracks `dev` branch. Repo at `/home/takwerx/infra-TAK` — **BUT NEVER HARDCODE THIS PATH**. Always resolve via `grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service`. Pull command: `cd $(grep -oP 'WorkingDirectory=\K.*' /etc/systemd/system/takwerx-console.service) && git fetch origin dev && git checkout -B dev origin/dev && sudo systemctl restart takwerx-console`. Configurator at `http://172.93.50.47:1880/configurator`.
- **VPS — `responder`:** secondary test box, busier (Mission API / DataSync load) — used for spiral-detection / load testing.
- **Azure / AWS test boxes:** spun up ad hoc for fresh-install validation, especially around NAT (`start.sh` shows public IP via `curl api.ipify.org`) and slow disks.
- Maintainer dev machine: macOS (this workspace is `/Users/andreasjohansson/GitHub/infra-TAK`). Code is edited locally, pushed to `dev`, pulled on VPS.

## Constraints

- **Ubuntu 22.04 LTS only** (current). Goal is universal installer.
- **Root required** for `start.sh` (writes systemd unit, installs packages, opens privileged ports). Console itself currently runs as root (v0.9.x) — non-root migration scaffolding exists but is gated.
- **Disk I/O matters.** SSD-backed storage strongly recommended; <200 MB/s sync write causes Docker build timeouts and Authentik startup failures. The README has a `dd if=/dev/zero of=/tmp/testfile bs=1M count=1024 oflag=dsync` pre-flight check for operators.
- **8 GB RAM minimum** for TAK Server, more for the full stack. 4 GB swap is auto-provisioned on Authentik deploy.
- **Container egress** is generally open by default; opt-in egress allowlist exists for Node-RED (`scripts/nodered-egress-firewall.sh`).
- **Image pulls require internet** at deploy time. Air-gapped install is not yet supported.
- **TAK Server `.deb` must be operator-supplied** (license + tak.gov signup is theirs to handle).

## Versioning

- Single `VERSION = "X.Y.Z-alpha"` constant near the top of `app.py` (currently `0.9.9-alpha` on `main` and `dev`).
- Sidebar shows the running version. Mismatch with the "Latest release" line in the README's top-of-file pointer indicates the customer is behind.
- Tags are pushed to GitHub when a release is shipped (`v0.9.4-alpha`). Pushing the tag is what triggers the in-product "Update Available" banner on customer installs (the customer's console polls GitHub releases).
- Update flow: customer clicks "Update Now" → console does `git fetch && git reset --hard origin/main` (or `dev` for testers) → restarts → `_run_post_update()` runs the migration ladder.
- Console rollback: every "Update Now" records `settings.console_rollback = {available, version, tag, snapshot_at}` so the operator can revert in one click for that update cycle.

## Tooling discipline

- **Cursor rules in `.cursor/rules/`:** `consult-upstream-docs.mdc` (always-applied) — read upstream docs before chasing symptoms. `cursor-memory-bank.mdc` — this memory bank pattern.
- **Top-level `.cursorrules`:** Node-RED safety contracts (FEEDS empty, never raw-`docker cp`, deploy.sh safety gate, never wipe global context).
- **Pre-release maintainer process:** `docs/TESTING-UPDATES.md` — fake-low VERSION, click Update Now, watch the migration ladder, restore. Pushing the tag without testing is forbidden.
- **Selective merge** `dev` → `main` for releases — `docs/COMMANDS.md`.

## Container hardening — known cap_drop exclusions

Several containers legitimately need their default capability set and must NOT have `cap_drop: ALL` applied:

- **CloudTAK `api`** — runs both nginx and Node.js in the same image. `cap_drop: ALL` silently breaks nginx workers (can't write `/dev/stdout`) and the nginx→Node loopback proxy. No error in logs — workers just die with exit 2.
- **TAK Portal** — Node.js reads `tak-client.p12` (owned by uid 889, mode 600). `cap_drop: ALL` removes `CAP_DAC_OVERRIDE`; the read silently fails and the dashboard shows `--` for all stats.
- **Authentik worker** — similar issue; `cap_drop: ALL` broke the worker in a way that produced no obvious log errors.

Node-RED is the exception — it legitimately runs with `cap_drop: [ALL]` and `no-new-privileges:true` (it doesn't need elevated capabilities).

The rule: before adding `cap_drop` to any service, verify the container doesn't mix multiple processes (nginx + app) or need filesystem access across UID boundaries.

## CloudTAK Reset Config notes

`cloudtak_reset_server_config` uses `TRUNCATE profile CASCADE` (not `DELETE WHERE system_admin=true`). Reason: `profile_overlays`, `profile_config`, and other tables have FK references to `profile.username`. A plain DELETE hits FK violations. TRUNCATE CASCADE handles all dependent tables. This is correct for a reset — all profile data is tied to the old TAK Server's user accounts. The error check uses exit code only (not string scan for "ERROR") because `TRUNCATE CASCADE` emits `NOTICE:` messages to stderr that would false-positive on string checks.

## Fail2ban / Scheduler toggle pattern

The `*-toggle-track` spans must NOT have `onclick` — they are already inside a `<label>` that natively toggles the checkbox on click. Adding `onclick=".click()"` causes double-fire (onchange fires twice per click), making disable impossible. As of v0.9.5, all 7 track spans have no onclick.

## Authentik Postgres — two-cluster setup and known maintenance issues

There are **two completely separate Postgres clusters** on every infra-TAK install with Authentik:

1. **TAK Server Postgres** — runs on the host as the `postgres` OS user, holds the `cot` database. Guard Dog auto-vacuum and the POSTGRES-DIAGNOSTICS.md runbook target this cluster. Generally healthy.

2. **Authentik Postgres** — runs inside `authentik-postgresql-1` Docker container (UID 70, `postgres:16-alpine`). Separate DB, separate process, separate config. All Authentik psql commands must go through `docker exec authentik-postgresql-1 psql -U authentik`.

**Known recurring issue — task log bloat:** `authentik_tasks_task` and `authentik_tasks_tasklog` grow unbounded (~500–900 MB after 1 month). The `takauthentiktasklogpurge.timer` (v0.9.5+) handles weekly cleanup. `_authentik_tasklog_cleanup()` (v0.9.6+) also runs the DELETE + VACUUM on "Update Now" if either table exceeds 100 MB — clears the one-time backlog on first update.

**`shm_size: 256m` required on `postgresql` service** — Docker's 64 MB default `/dev/shm` is too small for `VACUUM ANALYZE` with parallel workers in postgres:16-alpine. Multiple bugs across v0.9.5/v0.9.6/v0.9.7:
- v0.9.5: regex to add shm_size matched wrong field order (looked for `restart` then `command`, but compose has them reversed); also `--force-recreate` never included `postgresql`
- v0.9.6: whole-file `'shm_size:' not in file` check false-positives when server/worker services have their own `shm_size` values; docker inspect check only ran when `'shm_size: 256m' in file`
- v0.9.7 (fixed): anchor detection on postgres image line, scan only the postgresql service block; docker inspect check is unconditional — always compares `HostConfig.ShmSize` against `268435456`
- v0.9.7 (new bug): `docker compose up -d --force-recreate` default 10s stop timeout too short for loaded postgres — process survives container stop as orphan at 1100%+ CPU
- v0.9.8 (fixed): `docker stop -t 30` gives postgres 30s to checkpoint; cgroup-based orphan check runs unconditionally on every update — reads `/proc/<pid>/cgroup` for all UID-70 postgres processes, kills any not belonging to current container ID
- v0.9.8 (new bug, verified on responder): orphan check runs at end of `_auto_harden_containers()`, but `_auto_authentik()` runs LATER and recreates containers again — fresh orphans were not caught by the first check
- v0.9.9 (fixed): second cgroup-based orphan kill right before `auto-deploy complete`, after `_auto_authentik()` finishes its reconfigure-time recreate

**Authentik 2026.x task table schema** — `authentik_tasks_task` PK is `message_id` (uuid), timestamp is `mtime`. The old assumed column names (`pk`, `finish_timestamp`) do not exist. `authentik_tasks_tasklog.task_id` → `authentik_tasks_task(message_id)`. Correct DELETE: `WHERE message_id IN (SELECT message_id ... WHERE mtime < NOW() - INTERVAL '30 days')`.

## Things that are NOT in scope (yet)

- Air-gapped install.
- Non-root console runtime (scaffolded, deferred).
- Windows / RHEL host support.
- Kubernetes deployment.
- Multi-tenant console (one VPS = one customer).
- TAK Server clustering beyond the official `cluster` flag in CoreConfig.
