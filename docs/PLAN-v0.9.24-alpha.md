# Plan — v0.9.24-alpha

> **Status:** DRAFT
> **Target:** v0.9.24-alpha
> **Scope:** Update Now resilience (operator-double-click guard + auto-recover stopped infra services) + PgBouncer pool headroom.
>
> **Origin:** Carved out of `v0.9.23-alpha` field validation on tak-10 (PgBouncer headroom finding), and the night-of-release incident on ssdnodes (operator-double-click → tak-portal SIGTERM → 12-min outage) on 2026-05-16. The prior `PLAN-v0.9.24-alpha.md` (Node-RED ArcGIS multipart polygon) is moved verbatim to `PLAN-v0.9.25-alpha.md`.
>
> **Removed item:** an earlier draft included Item 4 "Guard Dog MediaMTX monitor restart-loop hotfix" based on observing repeated `Starting Guard Dog MediaMTX Monitor... / Deactivated successfully. Finished.` lines in the ssdnodes journal. On reading the actual script (`scripts/guarddog/tak-mediamtx-watch.sh`) and its systemd unit, that pattern is **correct `Type=oneshot` timer-driven Guard Dog behavior** — every healthy oneshot run produces the same start/finish pair every minute. All other Guard Dog watch scripts (tak-8089, tak-process, etc.) follow the identical pattern. Not a bug. Lesson: read the script before flagging the symptom. Tracked here for the next dev who sees the same journal noise.

---

## Why v0.9.24 exists

`v0.9.23` shipped two architectural wins (PgBouncer as the connection-leak fix; corrected TAK Server connection-state diagnostic). Field validation in the hours after release surfaced **three concrete gaps** that are independent of the v0.9.23 fixes themselves:

1. **Update Now is not concurrency-safe.** Operator opened two console tabs and clicked **Update Now** in both within seconds. The two `_post_update_auto_deploy()` threads raced on `docker compose` calls; one of them sent SIGTERM to `tak-portal` mid-bootstrap. The container exited 1 and stayed down for 12 minutes until something (probably a manual UI click) revived it.
2. **`restart: unless-stopped` does NOT recover containers that were SIGTERM'd by `docker stop` / `docker compose stop`.** Docker by-design behavior: an explicit stop signal marks the container as "operator-stopped" and the restart policy is skipped. So our compose-side `restart: unless-stopped` is not load-bearing when our own console issues a stop mid-update. There is no end-of-Update-Now sweep that brings stopped infra services back up.
3. **PgBouncer pool is saturated under modest load.** tak-10 measurement post-v0.9.23 v2.2 fix: `cl_active=84  sv_active=29  sv_idle=1  sv_used=0  maxwait=0` — 29/30 real PG conns continuously in transactions. `maxwait=0` holds today but headroom is zero; any traffic spike pushes us into `cl_waiting > 0`. Current `DEFAULT_POOL_SIZE=25 + RESERVE_POOL_SIZE=5 = 30` was a conservative initial value picked before we had production data. Now we have data: bump to 35+5 is warranted, and Postgres `max_connections=500` has 470 slots of slack to absorb it.

None of these are showstoppers individually. Together they are the difference between "v0.9.23 is the PgBouncer release" and "v0.9.23 is the PgBouncer release that survives an operator's accidental double-click on a Friday night."

---

## Item 1 — Update Now single-flight lock

### Problem

Two parallel `_post_update_auto_deploy()` runs race on every shared resource:
- `docker compose stop tak-portal` issued by run A while run B is still in `wait_for_authentik_ready()` → tak-portal SIGTERM
- `docker compose up -d --force-recreate authentik-postgresql-1` issued by both runs back-to-back → orphan postgres processes, PgBouncer churn
- `~/authentik/.env` and `~/authentik/docker-compose.yml` patch operations interleave → corrupted YAML / lost env keys

Tonight (2026-05-16) on ssdnodes: operator opened the console in two browser tabs, clicked **Update Now** in both within seconds. Tak-portal received SIGTERM at `02:21:37` mid-bootstrap (4 minutes into its Authentik-503-retry loop) and stayed exited until `02:33:55` — a 12-minute outage during which a 503 was served on the FQDN.

Existing precedent in the codebase: the spiral monitor already uses a process-level lock (PID-based, line ~28473 of `app.py`: `[spiral monitor] PID 1008 acquired monitor lock — starting`). We need the same shape for Update Now.

### Goal

- Two simultaneous **Update Now** clicks → second one is rejected cleanly with a user-visible message ("An update is already running, please wait — see banner X"). No silent racing on docker/compose state.
- Lock survives across console process death (so a crashed mid-update run doesn't leave a stale lock forever).
- Lock is process-aware (PID written into lock file; stale-PID detection removes orphan locks).
- UI: **Update Now** button is disabled + shows a spinner while the lock is held; status banner reads "Update in progress (started by tab X at HH:MM:SS UTC)" with live refresh.

### Implementation plan

1. **New helper module / functions** (in `app.py`):
   - `_UPDATE_LOCK_PATH = '/tmp/takwerx-console-update.lock'`
   - `_acquire_update_lock(reason: str, plog) -> (bool, str | None)` — atomic `O_CREAT | O_EXCL`, writes JSON `{pid, hostname, started_at_utc, reason, originating_session_id}`. Returns `(True, None)` on success, `(False, holder_summary_str)` on contention.
   - `_release_update_lock(plog)` — unlinks if PID matches ours; warns + leaves alone if PID mismatch.
   - `_check_stale_update_lock(plog) -> bool` — reads lock, checks `kill(pid, 0)` for liveness; if dead, removes and returns `True`.
   - `_update_lock_status() -> dict | None` — returns the lock holder payload for the API.

2. **Wrap `_post_update_auto_deploy()`** with `_acquire_update_lock` at start, `_release_update_lock` in a `try/finally`. On contention: log `Update Now BLOCKED — holder pid={X} started_at={Y}`, return early without doing any work.

3. **Wrap the manual-update endpoints** (`POST /api/update-now`, any subagent paths like `/api/authentik/update`, `/api/caddy/update`, `/api/tak-portal/update`, etc.) with the same lock. Per-service updates compete with the global Update Now — they cannot run concurrently. This is the safer default; if operators ask for per-service-only locking later we can introduce a finer-grained mutex.

4. **New API endpoint** `GET /api/update-now/status` → returns `{running: bool, holder: {...}}`. Used by the UI to drive the button state.

5. **UI changes** (Console page, console JS):
   - Poll `/api/update-now/status` every 3s while the user is on the page.
   - Button disabled with spinner + "Update in progress (HH:MM:SS UTC)" label when `running: true`.
   - When the user's own update finishes, re-enable + green "✓ Update complete" toast.

6. **Startup migration** `_clear_stale_update_lock_on_boot()` — on console startup (single-shot, idempotent), if `/tmp/takwerx-console-update.lock` exists and the PID inside it is dead OR not the current console process, remove the lock and log `Startup migration: cleared stale Update Now lock (was PID {X} from {Y})`. Prevents permanently-locked boxes after a console crash mid-update.

### Acceptance criteria

- [ ] Two simultaneous `curl -X POST .../api/update-now` calls (from two different sessions) → exactly one runs; the second returns HTTP 409 with a JSON body describing the holder.
- [ ] Clicking **Update Now** in two browser tabs within 1 second → button in tab #2 immediately greys out with the holder banner.
- [ ] Killing `gunicorn` (SIGKILL) mid-update → next console boot logs `Startup migration: cleared stale Update Now lock` and the **Update Now** button is enabled again.
- [ ] The lock is also held during `POST /api/authentik/update` / `/api/caddy/update` / per-service updates — concurrent global Update Now is blocked, and vice versa.
- [ ] `journalctl -u takwerx-console` clearly logs each acquire / release / blocked-by-contention event with PID and reason.

---

## Item 2 — Auto-recover stopped infra services at end of `_post_update_auto_deploy()`

### Problem

`docker stop` (and therefore `docker compose stop` and `docker compose down`) sends SIGTERM to the container and marks it as "explicitly stopped" in Docker's state. Per Docker's documented behavior, `restart: unless-stopped` will **not** restart a container in this state on its own — `unless-stopped` only applies to crashes, OOM kills, and daemon restarts.

This means: if any code path inside `_post_update_auto_deploy()` issues a `docker stop` and is then interrupted (concurrency race, exception, machine reboot), the affected container stays down. No amount of compose-level `restart:` configuration will recover it. The only structural fix is an **explicit post-update recovery sweep**.

Tonight's ssdnodes incident is the textbook case: tak-portal received SIGTERM at 02:21:37, took 12 min to come back, and there was no console-side mechanism to notice or recover it. The post-update log even literally says `Post-update: TAK Portal not running, skipped config update` — the auto-deploy *saw* tak-portal was down and explicitly chose to do nothing about it.

### Goal

- At the very end of `_post_update_auto_deploy()`, audit every infra service the console manages locally and bring up any that are exited/stopped.
- Per-service idempotent — running services are not touched (no recreate, no churn).
- Log line per service with outcome: `recovered | already running | failed: {error} | skipped (not installed)`.
- Final one-line summary in the journal: `Post-update service recovery: ran=14, recovered=1, already_running=13, failed=0`.
- Settings audit: persist `last_service_recovery_outcome` so the dashboard can surface it.

### Implementation plan

1. **New helper** `_post_update_service_recovery(plog) -> dict`:
   - Inspect each service compose file (TAK Portal, CloudTAK, Node-RED, MediaMTX, Federation Hub, Authentik (server, worker, ldap, postgresql, pgbouncer, redis)).
   - For each container we expect: `docker inspect --format '{{.State.Status}}'`.
   - If `exited` or `created` and not currently in restart cooldown: `docker compose -f <file> up -d <service>` (no `--force-recreate`).
   - For native services (MediaMTX systemd, etc.): `systemctl is-active`; if `inactive` and `systemctl is-enabled` is true, `systemctl start <unit>`.
   - Build summary dict with per-service status.

2. **Wire into `_post_update_auto_deploy()`** as the last step before `Post-update: auto-deploy complete`. Top-level try/except so a recovery failure does not crash the update flow itself.

3. **Audit persistence**:
   - `settings.last_service_recovery = {ts, ran, recovered, already_running, failed, per_service: {tak-portal: 'recovered', ...}}`
   - Last 5 recoveries kept in `settings.service_recovery_history` for trend visibility.

4. **Operator banner** on the console main page when `last_service_recovery.recovered > 0` or `.failed > 0` since the last page load — yellow for recovered, red for failed.

5. **New API endpoint** `GET /api/services/recovery-status` returning the last recovery audit + a list of currently-stopped containers (live check). Used by the dashboard and by support.

6. **Explicit list of recoverable services**:
   - **Compose-managed (Docker):** `authentik-server-1`, `authentik-worker-1`, `authentik-postgresql-1`, `authentik-ldap-1`, `authentik-redis-1`, `authentik-pgbouncer-1`, `tak-portal`, `nodered`, `cloudtak-api-1`, `cloudtak-events-1`, `cloudtak-retention-1`, `cloudtak-store-1`, `cloudtak-postgis-1`, `cloudtak-tiles-1`, `cloudtak-media-1`.
   - **Native systemd (host):** `mediamtx.service`, `mediamtx-webeditor.service`, `caddy.service`, `takserver.service` (already monitored by Guard Dog — only audit, no restart from this path), Federation Hub (if installed).

### Acceptance criteria

- [ ] On ssdnodes, simulate tonight's scenario: `docker stop tak-portal`, then click **Update Now**. After the update completes, tak-portal is running again with a `recovered` outcome logged.
- [ ] On a box where everything is healthy, **Update Now** logs `Post-update service recovery: ran=15, recovered=0, already_running=15, failed=0` — no docker thrash.
- [ ] A failed recovery (e.g. service missing its compose file) logs `failed: compose file not found at {path}`, increments the `failed` count, but does NOT abort the rest of the recovery sweep.
- [ ] `settings.last_service_recovery` reflects the actual outcome on disk; dashboard banner renders correctly for both recovered and failed cases.
- [ ] Native services (`mediamtx.service`) are recovered via `systemctl start` when stopped (not via docker compose).

---

## Item 3 — PgBouncer pool size headroom

### Problem

tak-10 (Authentik 2026.2.x, post-v0.9.23 v2.2 PgBouncer fix, no unusual load) shows:
- `cl_active=84` Authentik client conns parked at PgBouncer
- `sv_active=29  sv_idle=1  sv_used=0` — 29 of 30 real PG conns continuously in transactions
- `maxwait=0` — no client is currently queued, but only because the sv_active churn is fast enough

`DEFAULT_POOL_SIZE=25` + `RESERVE_POOL_SIZE=5` = 30 real connections. Postgres on the same box runs `max_connections=500` — we have **470 slots of slack** that PgBouncer is structurally barred from using.

If load goes up 20% from current steady-state (e.g. someone clicks around the Admin UI, dramatiq tasks fan out, an outpost flow burst), `cl_waiting > 0` and `maxwait > 0` follow immediately. We'd see queued client conns waiting on a PG slot — the exact symptom PgBouncer is supposed to prevent.

### Goal

- Bump default pool size to **35** (with `RESERVE_POOL_SIZE=5` = **40 ceiling**). Comfortable headroom without approaching Postgres `max_connections=500` (40/500 = 8%).
- Idempotent migration that detects an existing `DEFAULT_POOL_SIZE < 35` in `~/authentik/docker-compose.yml` and patches it in place.
- Respects operator overrides — if a box already has `DEFAULT_POOL_SIZE >= 35` (operator-tuned higher), leave it alone.
- After patch: recreate `authentik-pgbouncer-1` only (not server, worker, postgresql) to apply.
- Audit recorded in `settings.authentik_pgbouncer.default_pool_size` (already exists; just needs value update).

### Implementation plan

1. **Bump the install-time defaults** in `_ensure_authentik_pgbouncer(plog)`:
   - `DEFAULT_POOL_SIZE: "35"` (was `"25"`)
   - `RESERVE_POOL_SIZE: "5"` (unchanged)
   - `MAX_CLIENT_CONN: "1000"` (unchanged — already generous)

2. **New idempotent migration** `_ensure_authentik_pgbouncer_pool_size(plog, target=35)`:
   - Loads `~/authentik/docker-compose.yml` with PyYAML.
   - Inspects `services.pgbouncer.environment.DEFAULT_POOL_SIZE` (handle both dict and list forms; same shape as the v0.9.23 v2.2 fix).
   - If absent or `< target`: write `target` value, save YAML, write `*.bak.pool-size.{ts}` backup.
   - If `>= target`: idempotent no-op.
   - Recreate only `authentik-pgbouncer-1`: `docker compose up -d --force-recreate pgbouncer`. ~10s downtime on the pool itself; Authentik server/worker will see brief `cl_waiting` spike then resume.
   - Update `settings.authentik_pgbouncer.default_pool_size` to new value + write outcome to `settings.authentik_pgbouncer_pool_size_migration = {ts, from, to, outcome}`.

3. **Wire into `_startup_migrations`** (same gate pattern as v0.9.23 v2.2 fix) — runs on every console restart, idempotent no-op once converged.

4. **Verification step** in the migration: after recreate, run `SHOW DATABASES` on PgBouncer and confirm the `pool_size` column reads `target` for the `authentik` row. Persist verification result in the audit.

### Acceptance criteria

- [ ] On a fresh install: PgBouncer ships with `DEFAULT_POOL_SIZE=35` from day one. `SHOW DATABASES` confirms `pool_size=35`.
- [ ] On an existing v0.9.23 box (tak-10): startup migration detects `pool_size=25`, patches to 35, recreates pgbouncer, verification confirms. `cl_waiting` returns to 0 within seconds.
- [ ] On a box with operator override `DEFAULT_POOL_SIZE=50`: no change, log `pgbouncer pool: already at 50 (>= target 35) — idempotent no-op`.
- [ ] Authentik server + worker are NOT recreated by this migration (we don't need to — PgBouncer recreate is enough).
- [ ] `settings.authentik_pgbouncer.default_pool_size` reflects the new value; dashboard tile shows the correct ceiling.

---

## Planned files touched

- `app.py` — Items 1, 2, 3 (Items 1 + 2 in the `_post_update_auto_deploy()` + startup-migration paths; Item 3 in `_ensure_authentik_pgbouncer` + new `_ensure_authentik_pgbouncer_pool_size`)
- `static/` (JS for Console page) — Item 1 UI updates
- `docs/RELEASE-v0.9.24-alpha.md` (new, planned)

---

## Out of scope (explicitly)

- Node-RED ArcGIS multipart polygon support — moved to `PLAN-v0.9.25-alpha.md` (verbatim).
- Authentik connection pool tuning beyond `DEFAULT_POOL_SIZE=35` — current 40-conn ceiling is a 2026-05-16 calibration; future bumps go in their own plan if production data demands it.
- TAK Portal-specific Authentik-503 bootstrap retry behavior — `restart: unless-stopped` plus Item 2 (auto-recover sweep) covers the operational gap. Deeper TAK Portal bootstrap-aware health checking is a TAK Portal upstream concern.
- Migrating away from `/tmp` for the lock file (per-tmpfs persistence across reboots) — `/tmp` is intentional, lock should NOT survive reboot because a fresh boot is a clean slate. Item 1 Step 6 (startup migration) handles the stale-lock case explicitly.

---

## Test plan

### Pre-ship (maintainer machine — fake-low VERSION + Update Now ladder, per `docs/TESTING-UPDATES.md`)

1. Set local box to v0.9.23, run **Update Now** to v0.9.24 dev. Confirm the three migrations log cleanly (Update lock startup clear, PgBouncer pool bump, recovery sweep).
2. Click **Update Now** twice within 500ms (two tabs). Confirm tab #2 receives 409 + banner.
3. `docker stop tak-portal`, click **Update Now**. Confirm post-update recovery sweep brings tak-portal back, logs `recovered`.
4. `kill -9 $(pgrep -f gunicorn | head -1)` mid-update. Restart console manually. Confirm `Startup migration: cleared stale Update Now lock` fires, **Update Now** button is enabled.

### Tak-10 validation (smoke test under real load)

1. Click **Update Now** once. Confirm PgBouncer migrates from `pool_size=25` to `35`. Verify `SHOW DATABASES` post-recreate.
2. Confirm `pg_stat_activity` from authentik DB still shows ONE client_addr (PgBouncer container IP) with count ≤ 40.
3. Run `_takserver_connection_state` diagnostic to confirm TAK Server is unaffected (no migration ladder shouldn't touch TAK Server, but verify).

### Ssdnodes validation (the box where tonight's incident happened)

1. Click **Update Now**. Confirm all infra services are up at end of run; recovery sweep logs `already_running` for everything healthy.
2. `docker stop tak-portal`, then click **Update Now**. Confirm recovery sweep brings tak-portal back without `--force-recreate` (no churn).

---

## Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Update lock leaks across container/console restart | Low | Medium | Startup migration clears stale lock with dead-PID check. |
| Recovery sweep `docker compose up -d` triggers an undesired recreate | Low | Low | Use `up -d` without `--force-recreate`; compose only acts on stopped containers. |
| PgBouncer recreate causes a 5-10s connection blip | Certain | Low | `maxwait` will spike briefly then recover; Authentik retries via `CONN_HEALTH_CHECKS=true`; operators have been told to expect a recreate as part of v0.9.24. |
| Plan execution exposes a deeper bug (e.g. `_post_update_auto_deploy` is not actually idempotent) | Medium | High | Each item is independently revertable; ship behind dev channel first; validate on three different box shapes before merging to main. |

---

## Versioning + release plan

- `VERSION = "0.9.24-alpha"` in `app.py`.
- Selective merge to `main` per `docs/COMMANDS.md` once all four items are validated on dev.
- Tag `v0.9.24-alpha` after merge to main.
- Release notes: `docs/RELEASE-v0.9.24-alpha.md` covers the operator-double-click incident as the headline (because that's the one that affected production tonight), with PgBouncer pool bump as the supporting structural improvement.
- Memory bank update at release time — same pattern as v0.9.22 / v0.9.23.

---

_Plan created 2026-05-16 from v0.9.23 field validation + same-night ssdnodes incident. Existing Node-RED multipart polygon scope moved to `PLAN-v0.9.25-alpha.md` (verbatim, second move)._
