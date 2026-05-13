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
- **Service unit:** `/etc/systemd/system/takwerx-console.service` — `User=root` in v0.9.x (non-root migration scheduled for **v1.0.0** as the major-version disruptive change; see `docs/PLAN-v1.0.0.md`. Scaffolding is in place via `_sudo_wrap`/`_write_priv`/`_read_priv`).
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

- Single `VERSION = "X.Y.Z-alpha"` constant near the top of `app.py` (currently `0.9.16-alpha` — **on `dev`, pending test on tak-10 2026-05-13**).
- Sidebar shows the running version. Mismatch with the "Latest release" line in the README's top-of-file pointer indicates the customer is behind.
- Tags are pushed to GitHub when a release is shipped (`v0.9.4-alpha`). Pushing the tag is what triggers the in-product "Update Available" banner on customer installs (the customer's console polls GitHub releases).
- Update flow: customer clicks "Update Now" → console does `git fetch && git reset --hard origin/main` (or `dev` for testers) → restarts → `_run_post_update()` runs the migration ladder.
- Console rollback: every "Update Now" records `settings.console_rollback = {available, version, tag, snapshot_at}` so the operator can revert in one click for that update cycle.

### Release roadmap (post v0.9.11)

- **v0.9.16 — Authentik worker CPU hotfix + Caddy update button — SHIPPED 2026-05-13** (see `docs/RELEASE-v0.9.16-alpha.md`). Two changes: (1) `_auto_remove_stale_docker_service_connections()` added to `_run_post_update()`. Deletes the "Local Docker" service connection that Authentik's upstream quickstart creates by default. The v0.9.2 CVE-2026-31431 hardening removed `/var/run/docker.sock` from the worker compose but left the service connection in Authentik's DB — worker's `outpost_service_connection_monitor` task retried the dead socket every 30s causing ~26% sustained CPU. Fix uses Authentik API `GET /api/v3/core/service_connections/docker/` + `DELETE` for each `local: true` connection. Idempotent, non-fatal. (2) Caddy detail page now shows current installed version + `update available` cyan indicator in the status banner (fed from the existing `_get_caddy_version_info()` apt check). New `⬆ Update` button in controls runs `apt-get install --only-upgrade caddy` then `systemctl reload caddy` via new `POST /api/caddy/update` route. Consistent with update button pattern on all other service pages (TAK Portal, CloudTAK, FedHub, Guard Dog, MediaMTX).
- **v0.9.15 — TAK Portal admin-account guardrail — SHIPPED 2026-05-12** (see `docs/RELEASE-v0.9.15-alpha.md`). Drop-in defense-in-depth release on top of v0.9.13 + v0.9.14. v0.9.13's incident class (TAK Portal user clicks **Disable** on `akadmin` and `webadmin`, locks operator out of Authentik) is now closed from three independent directions: **Prevent** (v0.9.15 — TAK Portal hides + action-locks both protected admins via `USERS_HIDDEN_PREFIXES` + `USERS_ACTIONS_HIDDEN_PREFIXES` defaults; operator can't click Disable / Delete on them); **Detect** (v0.9.13 + v0.9.14 — Protected Admin Accounts panel on `/authentik` shows live `is_active` state, reads survive Authentik 403s via ak-shell fallback); **Recover** (v0.9.13 + v0.9.14 — one-click Reactivate, layered API → `ak shell` recovery). TAK Portal author (Justin Davis) surfaced that TAK Portal already had the two settings fields needed for this; infra-TAK is already authoritative for both in `_takportal_build_settings_dict` so the change is two strings: `USERS_HIDDEN_PREFIXES` `"ak-,adm_,nodered-,ma-"` → `"akadmin,webadmin,ak-,adm_,nodered-,ma-"` and `USERS_ACTIONS_HIDDEN_PREFIXES` `""` → `"akadmin,webadmin"`. (TAK Portal does PREFIX matching; the existing `ak-` had a trailing dash and did not cover the literal `akadmin`.) New self-healing migration `_auto_harden_takportal_settings()` runs in `_run_post_update` right after `_auto_harden_takportal` (port hardening): reads live `settings.json` from the `tak-portal` container; if both prefixes are already in both fields, no-op; otherwise pushes merged settings (preserving `BRAND_LOGO_URL`, `TAK_SSH_ONBOARDED`, `TAK_SSH_LAST_HANDSHAKE_AT` via `PRESERVE_TAKPORTAL_KEYS`) and restarts the container. Skipped when TAK Portal isn't deployed locally — remote-Portal installs apply the new defaults via TAK Portal → Update config & reconnect in the console UI. Recovery surface unchanged from v0.9.14. **Design rule for the next dev:** when a recovery feature exists for a foot-gun, the recovery panel should be the last layer of defense, not the first. Prevention (hide the foot gun) > Detection (show it's been pulled) > Recovery (unbreak it). v0.9.13/14 built layers 2+3 first because we had an incident; v0.9.15 closes layer 1. All three are kept in place because any of them can fail (operator clears the prefix lists; future TAK Portal release exposes a way past the lock; Authentik's native admin UI is still always usable for `is_active` toggles).
- **v0.9.14 — Authentik Admin Recovery hotfix — SHIPPED 2026-05-12** (see `docs/RELEASE-v0.9.14-alpha.md`). Drop-in hotfix to v0.9.13. v0.9.13 shipped the **Protected Admin Accounts** panel but the *status read* path (`_get_authentik_admin_accounts_status`) only knew how to talk to the Authentik REST API — when `akadmin.is_active = false` (the exact state the panel exists to recover from), the `AUTHENTIK_BOOTSTRAP_TOKEN` (owned by `akadmin`) authenticates but Authentik returns **HTTP 403** on every protected endpoint because the token's *owner* has no permissions. Net effect on customer installs that had already hit the bug: panel rendered `? Authentik API 403` for both accounts, never reached the `is_active === false` branch, never drew the **Reactivate** button — the recovery panel couldn't see the very state it was supposed to expose. v0.9.13 had built the right architecture for the *write* path (`_recover_authentik_user`: API → `docker exec authentik-server-1 ak shell` fallback) but missed the symmetric problem on the *read* path. v0.9.14 introduces `_read_authentik_admin_accounts_via_ak_shell()` (reads both whitelisted users in one `docker exec` via a base64-encoded Django ORM snippet, parses `AK-STATUS|<user>|EXISTS|<is_active>|<is_superuser>` lines) and wires it into `_get_authentik_admin_accounts_status()` as a layered read: API first, fall back to ak-shell for *all* users if any API call errors so the panel renders from one consistent source. Response JSON gains `source: 'api'|'ak-shell'` for diagnostics; UI shows a dim caption `status read via ak shell (Authentik API unavailable)` when the fallback is active. UI escape hatch: the JS now also renders the Reactivate button when `a.error` is set (not only when `a.is_active === false`) so the operator always has a manual lever even if both read paths fail — the recover endpoint has its own independent ak-shell fallback that doesn't depend on the read working. **Lesson recorded for the next dev**: a recovery feature has to survive the failure mode it's recovering from. If you can express the failure as "the bootstrap token works *iff* akadmin is active," then any read **or** write through the bootstrap token is a bootstrap problem and needs the same ak-shell escape hatch. v0.9.13 nailed the write path and missed the read path; do not repeat.
- **v0.9.13 — Authentik Admin Recovery — SHIPPED 2026-05-12** (see `docs/RELEASE-v0.9.13-alpha.md`). Operator unlock-out recovery feature, prompted by a real incident where a TAK Portal user clicked **Disable** on both `webadmin` and `akadmin`. Authentik's "Disable" = `is_active=false` (reversible, just blocks login + LDAP bind), but with both protected admins disabled the operator is locked out of the Authentik UI entirely. New **Protected Admin Accounts** panel on `/authentik` (under the existing "Admin user: akadmin · Show Password" row) polls `is_active`/`is_superuser` for both accounts and shows a red `⚠ DEACTIVATED` row with a **Reactivate** button when an account is down. `_recover_authentik_user()` recovery is layered: (1) `PATCH /api/v3/core/users/{pk}/` with `AUTHENTIK_BOOTSTRAP_TOKEN` from `~/authentik/.env` (normal case → banner `[via api]`); (2) `docker exec authentik-server-1 sh -c 'echo $b64 | base64 -d | ak shell'` running Django ORM `User.objects.filter(username=…).save()` — base64-encoded so no quoting concerns across shell/ssh/docker layers; bypasses API auth, broken flows, broken policies (→ banner `[via ak-shell]`). Whitelist `_AUTHENTIK_RECOVERABLE_USERS = ('akadmin', 'webadmin')` enforced server-side so the endpoint cannot be used to re-enable arbitrary accounts, and the whitelist also guarantees the username interpolated into the ak-shell snippet is one of two literals. Bundled one-line fix: `_ensure_authentik_webadmin()` now also includes `is_active: True` in its patch fields, matching what `_ensure_authentik_ldap_service_account` already did for `adm_ldapservice` — without this the existing **Sync webadmin to Authentik** button silently failed to recover a disabled webadmin (it would set the password but `is_active=false` blocks the LDAP bind, so 8446 login still failed). No migrations, no operator pre-flight; drop-in update from v0.9.12. Late-cycle UI bug shipped in the same release: the initial `btnHtml` build for the Reactivate button used `\'` escapes inside a Python triple-quoted JS string — Python's triple-quoted string parser interprets `\'` as `'` even inside `'''...'''`, so the rendered JS string terminated mid-expression and the whole `<script>` block failed to parse with `Uncaught SyntaxError: Unexpected string`, leaving `refreshAdminAccounts` undefined and the Refresh button silently dead. Fix: switch that line to a JS **template literal** (backticks) — Python doesn't interpret backticks, and single quotes inside backticks don't need escaping. Recorded in the release roadmap so the next dev-template author who reaches for `\'` in embedded JS knows to use template literals instead.
- **v0.9.12 — Cyber Security Hardening — SHIPPED 2026-05-11** (see `docs/RELEASE-v0.9.12-alpha.md`). Generalised the v0.9.11 CloudTAK `!reset` override pattern to TAK Portal, MediaMTX (local + remote), remote Authentik, and CloudTAK's other public-bound services (`api`/`tiles`/`events`/`media`); added new `_auto_harden_takportal()`, `_auto_harden_mediamtx()`, `_auto_authentik_ports_remote()` post-update steps; patched the post-auth code bugs surfaced by the 2026-05-10 audit (snapshot path traversal via new `_validate_snapshot_label`, external-DB SQLi via psql `-v` substitution + identifier regex, webadmin-password RCE via argv-only `subprocess.run`, external-DB test-connection RCE via `socket.create_connection` replacing `bash -c "</dev/tcp/..."`, hardcoded `adm_ldapservice` fallback password replaced with `secrets.token_urlsafe(24)` persisted to `.env`, SSH host/user injection via new `_validate_ssh_target`). Also locked down Server One Postgres + Guard Dog health-agent UFW (removed unconditional `allow {port}/tcp` that overrode source-scope rules above). Ships `docs/PORT-EXPOSURE-POLICY.md` as the canonical Tier 1/3/4/5 reference. **Late-cycle additions during test phase:** B7 HOME pinning fix in `takwerx-console.service` (operator clicked Sync webadmin and `cd ~/authentik` failed under gunicorn — same class of bug as v0.2.7's `takupdatesguard.service`, finally closed for the console unit); B8 Authentik ReputationPolicy `negate=True` (binding semantics were inverted — `passes()` returns `True` for *bad* reputation, so `negate=False` denied all normal users; hid for 10 releases because LDAP outpost's `bind_mode: cached` masked the misconfig). Self-healing startup migrations now patch existing v0.9.11 installs end-to-end on first Update Now (port hardening compose files, LDAP SA bind drift, ReputationPolicy binding drift).
- **v1.0.0 — NEXT MAJOR. Non-root console migration** (planned, see `docs/PLAN-v1.0.0.md`). Moves the console off `User=root` to a `takwerx` sudo user. Reserved for v1.0.0 because the runtime behavioural change warrants the major-version semver bump. Scaffolding (`_sudo_wrap`, `_write_priv`, `_read_priv`) already in place.
- **v0.9.14 — Auth hardening + UI cleanup** (tentative, may slot in before or after v1.0.0). Shared-secret header for `X-Authentik-Username` trust, `SESSION_COOKIE_SECURE`, masked secrets in API responses with re-auth-to-reveal, `atakatak` keystore rotation button.

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
- v0.9.8/v0.9.9 (new bug, friendly fire): the cgroup check used `kill if cgroup does not contain authentik-postgresql-1 ID` — this also killed UID-70 postgres processes inside `cloudtak-postgis-1` (CloudTAK's PostGIS spatial DB), which restart-loop on every update and spike CPU to 800–1100% catching up. Verified on responder and tak-10
- v0.9.10 (fixed): check against ALL running Docker container IDs (`docker ps -q --no-trunc`), kill only when cgroup matches NO running container; preserves legitimate processes in any postgres container regardless of name

**Authentik vs CloudTAK postgres** — both run as UID 70 on the host. The cgroup is the only reliable way to tell them apart at the process level. Never assume "UID 70 + not in container X" means orphan; always check against the full set of running containers.

**v0.9.11 — CloudTAK upstream RCE / PG_MEM cryptominer (security hotfix)** — dfpc-coe/CloudTAK ships `postgis ports: 5433:5432` on `0.0.0.0` with hardcoded `POSTGRES_PASSWORD=docker` literal. Live compromise observed on responder May 8-10 2026: scanner → brute-force `docker:docker` succeeds → `COPY FROM PROGRAM` drops `gcmanager-1.so` into postgis data volume → modify `postgresql.conf` `shared_preload_libraries` → Monero miner runs at 1000%+ CPU with C2 over Tor SOCKS5. Family: PG_MEM/PGMiner (Aqua Nautilus, Palo Alto Unit 42). Sample hash on responder: SHA256 `715348a40250549100cbbeb2a8d68ffa323e671b55fc46e8df24c7016b11e10a` (566 KB ELF static-pie, statically-linked musl libc, compiled for Alpine). Hashes vary across deployments — reliable IOC is the persistence technique (.so in data dir + shared_preload_libraries in postgresql.conf), not the hash.

**v0.9.11 mitigation in app.py:**
- `_cloudtak_build_override_yml()`: `postgis ports: !reset []` removes the public 5433 mapping entirely (CloudTAK uses internal Docker network); `store ports: !reset` binds only `127.0.0.1:9002:9002` (drops public 9000 S3 API, keeps console on loopback for SSH tunnel); `postgis environment.POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-docker}"` substitutes from .env.
- `_cloudtak_build_env_content()`: new `postgres_pass` parameter, emits `POSTGRES_PASSWORD=` line + uses it in `POSTGRES=postgres://docker:<pass>@postgis:5432/gis` connection string. Default 'docker' for backwards compat.
- 4 caller sites updated: fresh-install paths (2) generate `secrets.token_hex(24)` and save to `~/CloudTAK/.postgres-password` (chmod 600); reconfig paths (2) read existing value from `.env` (local) or via SSH grep from remote `.env` to preserve DB connection.
- New `_auto_harden_cloudtak()` runs every Update Now after `cloudtak_t.join()`: scans postgis data volume for `*.so` files + uncommented `shared_preload_libraries` in `postgresql.conf`. If compromised: stops all CloudTAK containers, quarantines `.so` files to `quarantine-YYYYMMDD-HHMMSS/` subdir (preserves forensics, does not delete), comments out the malicious config line with `#INFRATAK_DISABLED# ` prefix, writes `~/CloudTAK/COMPROMISE-DETECTED.txt`, prints loud banner, leaves CloudTAK STOPPED pending operator Remove + Reinstall. Always (idempotent): writes hardened override, applies UFW deny rules for 5433/9000/9002, force-recreates only if clean.
- Critical: Postgres reads `POSTGRES_PASSWORD` env var ONLY on `initdb` (first boot of empty volume). On existing volumes the env var is ignored and the previously-baked password persists. This is why update-only flows can't rotate the password on existing installs — only Remove + Reinstall (which wipes the volume) gets a fresh strong password baked in.

**Operator paths post-v0.9.11:**
- Update Now alone = network locked down via override + UFW; existing weak password remains but unreachable; compromised installs quarantined + stopped.
- Update Now + Remove + Reinstall (recommended) = wipes data volume + reinstall path generates strong password baked into fresh DB. Full remediation.

**Authentik 2026.x task table schema** — `authentik_tasks_task` PK is `message_id` (uuid), timestamp is `mtime`. The old assumed column names (`pk`, `finish_timestamp`) do not exist. `authentik_tasks_tasklog.task_id` → `authentik_tasks_task(message_id)`. Correct DELETE: `WHERE message_id IN (SELECT message_id ... WHERE mtime < NOW() - INTERVAL '30 days')`.

## v0.9.12 lessons learned (May 2026)

Patterns and gotchas that emerged during v0.9.12 testing on `tak-10` and `responder`. Each section is durable — if you see the symptom, the fix is here.

### Pattern: systemd unit must pin `Environment=HOME=` if the service shells out with `~`

**Symptom (v0.9.12):** clicking **Sync webadmin to Authentik** returned `/bin/sh: 1: cd: can't cd to ~/authentik` even though `/root/authentik` clearly existed. `_ensure_authentik_ldap_service_account` and `_auto_harden_*` paths could fail intermittently with the same error. Surfaces specifically under gunicorn-spawned `subprocess.run('cd ~/… && …', shell=True)` calls.

**Root cause:** systemd does **not** inherit `HOME` from login env. `takwerx-console.service` only pinned `Environment=PYTHONUNBUFFERED=1` and `Environment=CONFIG_DIR=…`. Under gunicorn, `os.environ.get('HOME')` returned `None`, and `/bin/sh` cannot expand `~` without `$HOME`. Same root cause as:
- **v0.2.7-alpha** — `takupdatesguard.service` needed `Environment=HOME=…` for the Guard Dog Updates timer.
- **v0.9.2-alpha** — `git safe.directory` / `git config --global` writes failed because `git` couldn't find `~/.gitconfig`.

**Three-layer fix pattern (canonical for this class of bug):**
1. **Runtime guard at the top of `app.py` (after stdlib imports):** if `os.environ.get('HOME')` is unset, derive from `pwd.getpwuid(os.getuid()).pw_dir` (fallback `/root`) and write it to `os.environ`. Fixes the **current** process immediately; child shells inherit.
2. **Installer (`start.sh create_service()`):** new installs get `Environment=HOME=$SERVICE_HOME` baked into the unit file (`$SERVICE_HOME` derived from `${HOME:-/root}`).
3. **Idempotent startup migration (`_startup_pin_console_service_home()`):** patches existing v0.9.11- unit files on Update Now (sed-in-place insert of `Environment=HOME=/root` after the existing `Environment=` block, `systemctl daemon-reload` only when a change was made).

**Rule of thumb:** any future systemd unit added under `/etc/systemd/system/` that wraps a Python/bash process which might `subprocess.run('cd ~/…', shell=True)` **must carry `Environment=HOME=…`**. If you're adding a new unit, set it at create time. If you find a unit missing it in the field, add a migration like `_startup_pin_console_service_home()`.

### Pattern: Authentik PolicyBinding fields require DELETE+POST to change (PATCH returns 405)

**Symptom (v0.9.12):** trying to fix the `negate` / `failure_result` fields on an existing `infratak-brute-force` PolicyBinding via `PATCH /api/v3/policies/bindings/{pk}/` returned `405 Method Not Allowed` on Authentik 2026.2.2.

**Workaround:** DELETE the existing binding, then POST a fresh one with the corrected fields. Used in both `_authentik_setup_reputation_policy()` retro-fix branch and `_startup_fix_reputation_policy_drift()`. Capture the existing binding's `pk` from a GET first, DELETE by `pk`, then POST the new body — order matters because POST will fail with a uniqueness error if the old binding still exists on the same (policy, target) pair.

### Pattern: Authentik `ReputationPolicy.passes()` semantics are inverted from intuition

**The bug, verified against Authentik 2026.2.2 source via `inspect.getsource(ReputationPolicy.passes)` on the running container:**

```python
def passes(self, request: PolicyRequest) -> PolicyResult:
    # ... aggregate Reputation score for this IP/username ...
    passing = score <= self.threshold
    return PolicyResult(bool(passing))
```

`passes()` returns `True` **only when the user has accumulated enough bad reputation to act on** (score ≤ threshold, threshold is negative — default `-5`). A normal user with `score = 0` returns `False`. Therefore:

| Binding `negate` | Normal user (score 0) | Brute-force abuser (score ≤ -5) | Result |
|---|---|---|---|
| `False` (intuitive default) | binding denies | binding allows | **EVERYONE denied** (intuitive but wrong) |
| `True` (correct) | binding allows | binding denies | only abusers blocked (correct brute-force gate) |

**Cardinal rule (now documented in `docs/HANDOFF-LDAP-AUTHENTIK.md`):** when adding a ReputationPolicy as a brute-force gate on `ldap-authentication-flow`, ALWAYS set `negate=True, failure_result=True` on the PolicyBinding.

**Why this hid for ten releases (v0.9.2 → v0.9.12-rc):** the LDAP outpost runs in `bind_mode: cached`. Once `adm_ldapservice`'s first successful bind landed in the cache, all subsequent binds for that DN were served from cache without re-consulting the flow. The misconfigured binding never had a chance to fail in production. v0.9.12's `_startup_resync_ldap_service_account` force-recreates the LDAP outpost on every console boot (to fix a separate password drift issue), wiping the bind cache and **exposing** the long-standing reputation-policy misconfig. This is a generalisable warning: any cached upstream behaviour can mask config bugs for a long time; wiping the cache is what surfaces them. Test cache-clearing paths in pre-release.

### Pattern: never raw-copy YAML edits via Python regex backreferences when the value contains regex metacharacters

**Symptom (v0.9.12 pre-rc):** `_patch_takportal_compose_ports` and `_patch_cloudtak_compose_ports` corrupted `docker-compose.yml` files into `ports:J7.0.0.1...` (literal `J` inserted) and similar garbage. Operator-reported as "TAK Portal map not working, weird YAML errors."

**Root cause:** `re.sub(r'(ports:\s*)\N\1...', ...)` with `\N` backreferences silently consumes characters when the captured group contains characters that look like backreferences. Python's `re` module treats `\1` ... `\9` as backreferences in the replacement string, and `\N` (capital N) is ambiguous.

**Fix:** use `\g<N>` syntax instead of `\N` for ALL Python regex backreferences in replacement strings. This is unambiguous. Also add corruption detection: if a compose file no longer parses as YAML after a patch, `git checkout` it from the current branch and re-apply.

**Generalised rule:** when you find yourself doing `re.sub` on a structured config file (YAML, JSON, ini), strongly prefer:
1. Parse the file with the proper parser (`yaml.safe_load`), modify the data structure, serialise back. This is the right answer 90% of the time and avoids all regex pitfalls.
2. If you MUST use regex (because the file has comments / formatting / `!reset` directives you need to preserve), always:
   - Use `\g<N>` not `\N` for backreferences.
   - Validate the file parses correctly after the substitution.
   - Have a fallback: detect corruption, `git checkout` from `origin/$branch`, re-apply.

### Pattern: Docker Compose `ports: !reset` is NOT compatible with `docker-compose.yml` if the base file has `ports: [...]` as a list (vs map)

Discovered while generalising the v0.9.11 CloudTAK pattern to TAK Portal and CloudTAK's other services. The `!reset` tag works when overriding compose files **at the override layer**, but if the base `docker-compose.yml` already has `ports:` as an explicit list, dropping `!reset` into that file directly is a syntax error in some compose versions.

**Generalised rule:** the `!reset []` pattern in `_cloudtak_build_override_yml()` belongs in the **override file** (`docker-compose.override.yml`). For the base compose files (`docker-compose.yml`), patch the port mappings **directly** by parsing the file and rewriting the `ports:` list to bind only loopback. Don't try to inject `!reset` into the base file. The corrected v0.9.12 implementation uses `_patch_takportal_compose_ports` and `_patch_cloudtak_compose_ports` which parse + rewrite + serialise.

### Pattern: end-to-end auto-heal migrations need to verify with the authoritative source (not log inspection)

**Bug we briefly shipped in v0.9.12-rc:** `_test_ldap_bind()` checked Authentik server logs for `Bind successful` markers. This gave **false positives** because positive log lines from previous successful binds (cached) were within the `--since` window even when the current bind attempt was failing.

**Fix:** replaced with `_test_ldap_bind_dn_verdict(dn, password)` which runs `ldapsearch -x -H ldap://… -D <dn> -w <pw> -b 'dc=takldap' -s base '(objectClass=*)'` and captures the **ldapsearch exit code directly into a variable** (don't rely on `$?` after pipelines). Returns one of `'ok' | 'fail' | 'inconclusive'`. Authoritative because it actually attempts the bind through the live LDAP outpost; doesn't depend on any log parsing.

**Generalised rule:** when self-healing migrations need to know whether a state has been reached, prefer the **authoritative live probe** (CLI exit code, API GET, direct DB query) over **log scraping**. Logs are append-only and a stale positive will mask a current negative. The only exceptions are when:
1. The log line is being written by the **current** process whose `pid` you can match, OR
2. You can `truncate -s 0` / rotate the log immediately before the action.

### Pattern: Authentik blueprint plaintext password fallback can drift `adm_ldapservice` even when the operator never touched it

**The recurring class of bug** (this has bitten infra-TAK multiple times — May 2026 v0.9.12 was the most recent):

The Authentik blueprint applied by `_authentik_apply_blueprint()` historically contained `password: !Env [AUTHENTIK_BOOTSTRAP_LDAPSERVICE_PASSWORD]` inside the service-account user definition. When the blueprint engine processes this on every Authentik startup, it writes the env var value to the user's `password` column **as plaintext**, not as a hash. Authentik then refuses to authenticate against the stored password because all bind paths expect the hashed form.

**Symptom:** `ldapsearch -D 'cn=adm_ldapservice,ou=users,dc=takldap' …` returns 49 (Invalid Credentials) right after a fresh blueprint apply (e.g. Update Now), even though `~/authentik/.env` has the expected `AUTHENTIK_BOOTSTRAP_LDAPSERVICE_PASSWORD=<value>` and that value should be the one Authentik is checking.

**Why it stays hidden:** the LDAP outpost's `bind_mode: cached` serves previously-successful binds from cache. The plaintext-vs-hash mismatch only manifests on the **first** bind after a cache clear (container recreate, outpost restart, fresh deploy). Once you hit it manually with `set_password` via the Authentik API (which always writes a proper hash), subsequent binds succeed and the cache rebuilds; the operator never sees the bug for weeks.

**v0.9.12 mitigation (`_startup_resync_ldap_service_account`):**
1. Test the bind with `_test_ldap_bind_dn_verdict` (authoritative).
2. If it fails, call `POST /api/v3/core/users/{pk}/set_password/` with the `.env` value — Authentik hashes it correctly.
3. Recreate the LDAP outpost container to clear its cache.
4. Retest. If success, restart `takserver` so it picks up the new bind (TAK Server caches LDAP credentials itself; restart is the only documented way to flush that cache).
5. Persist outcome to `settings.json` so future boots can audit what happened.

**Generalised rule:** when an Authentik blueprint contains a literal `password:` field, ALWAYS audit it for `!Env` substitution that would write plaintext. Either remove the password from the blueprint entirely (set it via API afterwards) or accept that you need a startup-time resync migration to put the correct hash in place after every blueprint apply.

### Pattern: when verifying upstream behaviour, read the live container source, not docs

`.cursor/rules/consult-upstream-docs.mdc` is the always-applied rule that says read official docs first. The v0.9.12 ReputationPolicy bug surfaced the **next** level of rigour: even the docs can be ambiguous or omit edge cases. The decisive proof on `tak-10` was:

```python
docker exec authentik-server-1 ak shell -c "
import inspect
from authentik.policies.reputation.models import ReputationPolicy
print(inspect.getsource(ReputationPolicy.passes))
"
```

That ran the live code path against the deployed image (`authentik:2026.2.2`), printed the exact `passing = score <= self.threshold` line, and proved `negate=True` was required. **Generalised rule:** for any "the API says one thing but my install behaves differently" symptom, drop into the running container's interpreter and use `inspect.getsource()` to dump the live function. Authentik exposes `ak shell` which is a Django shell with all the auth models pre-imported — perfect for this.

## Things that are NOT in scope (yet)

- Air-gapped install.
- Non-root console runtime (scaffolded, deferred).
- Windows / RHEL host support.
- Kubernetes deployment.
- Multi-tenant console (one VPS = one customer).
- TAK Server clustering beyond the official `cluster` flag in CoreConfig.
