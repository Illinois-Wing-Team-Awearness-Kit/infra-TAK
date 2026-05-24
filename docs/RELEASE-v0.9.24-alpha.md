# v0.9.24-alpha — Update Now single-flight lock + service recovery sweep + PgBouncer pool headroom

**Date:** 2026-05-16
**Type:** Hotfix release — drop-in update via Update Now. Closes the test8 double-click race + lifts the v0.9.23 PgBouncer pool ceiling after one day of field data.

---

## TL;DR

`v0.9.24-alpha` lands three field-driven fixes from the first 24 hours after `v0.9.23-alpha` shipped:

1. **Update Now single-flight lock.** Closes a race observed on test8 where an operator double-clicked "Update Now" from two browser tabs. Both POSTs to `/api/update/apply` ran in parallel, both scheduled `systemctl restart takwerx-console`, and the second restart killed the first deploy mid-bootstrap — leaving `tak-portal` + `mediamtx` in `Exited` state for 12+ minutes until manual recovery. Docker's `restart: unless-stopped` policy does NOT restart explicitly stopped containers, which is what made this an outage rather than a transient.

2. **Post-update service recovery sweep.** Belt to the lock's suspenders. At the end of every post-update deploy (and on same-version restarts where an Update Now lock is still present), we explicitly start any infra-TAK Docker container in `Exited` state and any infra-TAK systemd unit that's `inactive` or `failed`. Idempotent — starting an already-running service is a fast no-op.

3. **PgBouncer pool headroom: 25 → 35.** Field validation on tak-10 (2026-05-16, the first full day on v0.9.23 PgBouncer) showed `SHOW POOLS` running steady-state at `sv_active=29 / sv_idle=1` under modest concurrent load (84 client-side connections, `maxwait=0` for now but **zero spike headroom**). v0.9.24 bumps `DEFAULT_POOL_SIZE` from 25 → 35, lifting the real-PG ceiling from 30 → 40 connections. Still ~12% of Postgres `max_connections=500`. Operator overrides above 35 are preserved.

Plus a documented manual-fix recipe for a Server One UFW rule-ordering issue that surfaced during v0.9.24 development on test8.

---

## The test8 incident — forensic trace (2026-05-16)

Operator updated test8 from `v0.9.22-alpha` to `v0.9.23-alpha`. After the update, `tak-portal` and `mediamtx` (Docker) were observed in `Exited` state. The console dashboard surfaced them as "stopped"; the operator manually started them via the UI.

Root cause came out a few hours later:

> *"shit im an idiot. i think i fucked this up i like logged into test 8 twice in a browser and may have been updating them simultaneously."*

Timeline (reconstructed from `docker inspect` `FinishedAt` timestamps + `takwerx-console.service` journal):

```
T+0      Operator clicks "Update Now" in browser tab A
T+0.1s   /api/update/apply (tab A) starts: git fetch, checkout v0.9.23-alpha
T+0.4s   Operator clicks "Update Now" in browser tab B (separate SSO session)
T+0.5s   /api/update/apply (tab B) starts: git fetch (idempotent), checkout (idempotent)
T+2.1s   /api/update/apply (tab A) → subprocess.Popen('sleep 2 && systemctl restart takwerx-console')
T+2.5s   /api/update/apply (tab B) → subprocess.Popen('sleep 2 && systemctl restart takwerx-console')
T+4.1s   systemctl restart fires (from tab A's Popen)
T+4.1s   New console boots, _post_update_auto_deploy starts migration thread
T+4.5s   systemctl restart fires AGAIN (from tab B's Popen, ~400ms after tab A's)
T+4.5s   Console is killed mid-migration. Daemon thread (which had called `docker compose up -d tak-portal`) dies.
T+4.6s   tak-portal container, in the middle of its entrypoint bootstrap, receives SIGTERM via the killed compose process.
T+4.7s   New console boots again. _post_update_auto_deploy runs again, sees last_console_version == VERSION → early-return.
T+4.7s+  tak-portal stays Exited. mediamtx stays Exited. `restart: unless-stopped` is silent because containers were *explicitly* stopped.
T+12min  Operator notices the dashboard banner, manually starts the services.
```

The fix is two-pronged: **prevent the race** (Item 1), and **catch any leftover stopped services** (Item 2). Belt and suspenders.

---

## Item 1 — Update Now single-flight lock

### Design

`/api/update/apply` writes a lock file at the top of the handler:

```
/var/lib/takwerx-console/update-now.lock
```

Contents:

```
version=0.9.24-alpha
started_utc=2026-05-16T15:42:11Z
pid=12345
```

If the file already exists AND its mtime is < 20 min old, the second handler returns:

```json
HTTP 409
{
  "success": false,
  "in_progress": true,
  "started_seconds_ago": 7,
  "error": "Update Now is already in progress (started ~7s ago). The console will restart automatically when complete — wait 1–3 minutes then refresh this page. Avoid clicking Update Now in multiple browser tabs.",
  "lock_info": "version=...\nstarted_utc=...\npid=..."
}
```

If the file is older than 20 min, it's treated as stale (a wedged prior run), logged, and overwritten. This is the safety valve — if anything ever crashes after writing the lock but before clearing it, the next click after 20 min still works.

### UI

The frontend (`applyUpdate()` in `app.py`) now branches on `d.in_progress`:

- Status row turns cyan (not red — this isn't an error)
- Button text becomes `Update in progress…` and stays disabled
- Page auto-reloads after 30 s (vs 12 s on a successful update) so the operator lands on the post-update state when the deploy is done

### Lifecycle (where the lock is cleared)

Three places, in order of likelihood:

1. **`_run_post_update_guarded` `finally:` block.** Runs after every migration thread completes — success OR exception. This is the primary clear path.
2. **`_post_update_auto_deploy` no-op early-return.** When the console restarts and `last_console_version == VERSION` but the lock file is present (= a prior deploy was interrupted), we clear the lock here. Also runs the service recovery sweep first.
3. **20-min TTL.** Fallback for the rare case where the process dies between (1) and (2) without either path executing.

### What this does NOT prevent

- A user clicking "Update Now" then immediately running `systemctl restart takwerx-console` manually from the shell. The manual restart kills the migration thread the same way the second tab did. Workaround: don't do that; if you have to, run `rm /var/lib/takwerx-console/update-now.lock` afterwards.
- Two operators on two different browsers + different IPs clicking at the same time. Server-side lock catches it; whichever request arrives second gets the 409.

---

## Item 2 — Post-update service recovery sweep

### Function: `_post_update_service_recovery_sweep(plog)`

Located adjacent to `_post_update_auto_deploy` in `app.py`. Returns a dict with `containers_started`, `units_started`, `errors` lists.

### Docker scan

```python
docker ps -a --format '{{.Names}}\t{{.State}}'
```

Filters by name prefix:

- `authentik-`
- `cloudtak-`
- `tak-portal`, `takportal`
- `mediamtx`, `takmediamtx`
- `fedhub-`, `federation-hub`, `federationhub`
- `caddy`, `takwerx-caddy`
- `nodered`, `node-red`

Containers in `exited` / `created` / `dead` state get `docker start <name>`. This preserves the existing container's image, env, volumes, and network — no `docker compose up` needed.

### Systemd scan

Conservative list, only services infra-TAK installs natively:

- `takserver.service`
- `mediamtx.service` (native install, not Docker)
- `nodered.service`
- `takmediamtxguard.timer`
- `takremotedbguard.timer`
- `takremotedbauthguard.timer`

Logic: if `systemctl is-enabled` returns `enabled` / `static` / `enabled-runtime` AND `systemctl is-active` returns `inactive` or `failed`, run `systemctl start <unit>`. We never call `enable` here — we only start things the operator's intent says should be running.

### Where it's called

1. **End of `_run_post_update()` (normal flow).** After all migrations complete, before logging "auto-deploy complete".
2. **No-op early-return path in `_post_update_auto_deploy`** when an Update Now lock is present on a same-version restart. This is the killed-mid-deploy → console restart → migrations don't re-run scenario.

### What it doesn't sweep

- User-managed containers (e.g. monitoring stacks, custom mqtt brokers). Out of scope.
- Containers that don't exist at all (e.g. `mediamtx` Docker container on a box where mediamtx is native). `docker start` of a non-existent name silently fails into the errors list — harmless.
- Systemd units that are `disabled` or `masked`. Operator's intent is for them to be off.

---

## Item 3 — PgBouncer pool headroom (25 → 35)

### Field data (tak-10, 2026-05-16)

`v0.9.23-alpha` shipped with `DEFAULT_POOL_SIZE=25` + `RESERVE_POOL_SIZE=5` = 30-connection ceiling. The rationale at the time was TAK-NZ/auth-infra (PR #102) running 25 on managed RDS without issues.

One full day of tak-10 traffic showed:

```
$ docker exec authentik-pgbouncer-1 psql -h 127.0.0.1 -p 5432 -U authentik pgbouncer -c "SHOW POOLS;"
 database  |   user    | cl_active | cl_waiting | sv_active | sv_idle | sv_used | maxwait
-----------+-----------+-----------+------------+-----------+---------+---------+---------
 authentik | authentik |       84  |          0 |        29 |       1 |       0 |       0
```

Read: 84 client-side connections (gunicorn workers + outpost binds + django channels listeners), 29/30 real PG slots in use, `maxwait=0` (no client currently waiting). Functionally healthy — but **zero spike headroom**. An OAuth-flow burst, an LDAP outpost reconnect storm, or a single batch job would tip into the RESERVE pool (5 slots) and then into `maxwait`.

### Change

`_AUTHENTIK_PGBOUNCER_DEFAULT_POOL_SIZE = 35` (was 25). New ceiling: 35 + 5 (RESERVE) = **40 real PG connections**, still ~12% of `max_connections=500`. Gives ~33% spike headroom over the steady-state observed on tak-10.

### Migration: `_ensure_authentik_pgbouncer_pool_size(plog, target=None)`

Wired into `_startup_migrations` right after `_ensure_authentik_pgbouncer`. Behavior:

| State on existing box                          | What the migration does                                                  |
|------------------------------------------------|--------------------------------------------------------------------------|
| No PgBouncer installed (e.g. external PG)       | No-op (returns False)                                                    |
| PgBouncer installed, `DEFAULT_POOL_SIZE=25`     | Patches compose YAML, `docker compose up -d --force-recreate pgbouncer`  |
| PgBouncer installed, `DEFAULT_POOL_SIZE>=35`    | No-op — operator overrides above 35 are preserved                        |
| Compose has non-int / quoted DEFAULT_POOL_SIZE  | No-op + log — unusual operator override, hands off                       |

Backs up the compose file to `docker-compose.yml.poolsize-bak-<ts>` before mutation. Rolls back on write error. Waits up to 24s for `authentik-pgbouncer-1` to report `healthy` after the recreate.

`server` and `worker` containers are **NOT** recreated — they keep their existing client-side connections, and PgBouncer's transaction-pool semantics re-pool them within a few seconds via healthcheck cycling.

### Settings audit

`settings.authentik_pgbouncer.default_pool_size`, `last_pool_size_migration_utc`, `last_pool_size_migration_from`, `last_pool_size_migration_to`, `last_pool_size_migration_version` are written on success.

---

## Sidebar — Server One UFW rule-ordering issue (documented, not fixed in code)

Surfaced during v0.9.24 development on test8 (Server Two, two-server install). The Guard Dog dashboard reported the remote DB health agent as unreachable, even though:

- The Python health agent IS running on Server One (`tak-11720985703`, listening on `0.0.0.0:8080`)
- SSH from test8 → Server One works
- Postgres on 5432 is reachable from test8 (TAK Server's `martiuser` connections succeed)
- Only port 8080 specifically times out from test8

`ufw status numbered` on Server One revealed:

```
[ 5] 8080/tcp                   DENY IN     Anywhere
[ 6] 8080/tcp                   ALLOW IN    63.250.55.132   ← test8 (Server Two)
```

UFW evaluates rules top-to-bottom and stops at the first match. The generic DENY at position 5 blocks everything, including test8's traffic, before it ever hits the scoped ALLOW at position 6.

### Manual fix on the Server One DB host

```bash
# Find the existing scoped ALLOW position (probably 6 if you haven't edited rules)
sudo ufw status numbered | grep -E "8080.*ALLOW.*<consoleIP>"

# Delete it (replace 6 with the actual number from the line above)
yes | sudo ufw delete 6

# Re-insert at position 5 (above the generic DENY)
sudo ufw insert 5 allow from <consoleIP> to any port 8080 proto tcp

# Verify
sudo ufw status numbered | grep 8080
```

Expected after the fix:

```
[ 5] 8080/tcp                   ALLOW IN    <consoleIP>
[ 6] 8080/tcp                   DENY IN     Anywhere
```

### Why not fixed in code in v0.9.24

The fix is a 3-line change (`ufw allow ...` → `ufw insert 1 allow ...`) in three locations in `app.py`, but it touches the security-sensitive `_auto_harden_guarddog_8080` path and the Server One deploy path. We chose to keep v0.9.24 tightly scoped to the three Items above (which closed the test8 incident) and ship the UFW reorder as a separate code change in v0.9.25 — with proper testing on a fresh two-server install.

If you're affected, the manual fix above takes 30 seconds and is fully idempotent.

---

## Files touched (code + docs)

```
app.py
  VERSION                                      0.9.23-alpha → 0.9.24-alpha
  _AUTHENTIK_PGBOUNCER_DEFAULT_POOL_SIZE       25 → 35 (plus 6 comment/docstring sites)
  update_apply() (/api/update/apply)           +44 lines: single-flight lock
  applyUpdate() (UI JS)                        +9 lines: handle 409 in_progress
  _post_update_service_recovery_sweep()        NEW (~135 lines)
  _ensure_authentik_pgbouncer_pool_size()      NEW (~95 lines)
  _post_update_auto_deploy()                   +25 lines: no-op path lock+sweep
  _run_post_update() (end)                     +9 lines: sweep call
  _run_post_update_guarded() finally           +8 lines: Update Now lock clear
  _startup_migrations()                        +9 lines: pool-size migration wire-in

README.md                                       Latest release pointer + changelog
docs/RELEASE-v0.9.24-alpha.md                  NEW (this file)
docs/PLAN-v0.9.24-alpha.md                     Existing plan (3 items shipped as planned)
docs/PLAN-v0.9.25-alpha.md                     Node-RED ArcGIS multipart polygon (deferred)
docs/COMMANDS.md                                Selective merge list updated
memory-bank/techContext.md                     v0.9.24 entry under roadmap
```

---

## Verification — on tak-10 after this update

```bash
# 1. Confirm new VERSION
grep "^VERSION" /home/takwerx/infra-TAK/app.py
# → VERSION = "0.9.24-alpha"

# 2. Confirm pool size bumped
docker exec authentik-pgbouncer-1 psql -h 127.0.0.1 -p 5432 -U authentik pgbouncer -c "SHOW CONFIG;" 2>&1 | grep default_pool_size
# → default_pool_size  |  35

# 3. Confirm pool now has headroom under load
docker exec authentik-pgbouncer-1 psql -h 127.0.0.1 -p 5432 -U authentik pgbouncer -c "SHOW POOLS;"
# Expect: sv_active < 35, sv_idle > 0, maxwait = 0

# 4. Test single-flight lock from CLI (POST twice rapidly):
#    Open two terminals, run in each almost simultaneously:
#      curl -X POST -k -b "session=..." https://localhost/api/update/apply
#    Second one should return HTTP 409 with in_progress=true.

# 5. Test service recovery sweep (manually stop a container, then run the sweep):
sudo docker stop authentik-pgbouncer-1  # simulate accidental stop
sudo systemctl restart takwerx-console   # triggers no-op early-return + sweep
sudo journalctl -u takwerx-console --since "1 min ago" | grep "Post-update sweep"
# Expect: "✓ started container authentik-pgbouncer-1 (was exited)"
```

---

## Drop-in upgrade notes

- No operator pre-flight required.
- Click **Update Now** in the console → console restarts → all three items apply on next boot.
- Existing v0.9.23 PgBouncer installs get the pool bump via the new migration; pgbouncer container is recreated (a few seconds of brief connection cycling for the server+worker; no user-visible outage).
- The Update Now single-flight lock takes effect on the FIRST click after this update lands (we wrote the file path / handler logic on the way in).
- If you're on a two-server install and see Guard Dog → Remote DB → Health Agent showing as red, apply the Server One UFW manual fix in the sidebar above (separate from this release).

---

## Lessons (continuing the v0.9.23 thread)

1. **Field validation > theoretical sizing.** v0.9.23's pool ceiling of 30 was sized from another project's RDS deployment. tak-10 hit 29/30 on its first full day. Operator boxes have different traffic patterns than managed-RDS-fronted deployments; always size pool ceilings from your own steady-state plus comfortable headroom for spikes (~33% over observed peak is a reasonable starting point).

2. **`restart: unless-stopped` is not a recovery mechanism.** It only restarts containers killed by Docker daemon failures or non-zero exit codes. Containers that received SIGTERM (e.g. from a parent compose process being killed) end up in `Exited (0)` and Docker considers that a clean stop. Recovery requires explicit application logic — Item 2 is that logic.

3. **Single-flight locks belong at the API door, not in the work loop.** We tried to defend against this via `_post_update_auto_deploy`'s existing `lock_path` (`/tmp/takwerx-post-update.lock`), but that gate runs INSIDE the post-restart code path — by the time it executes, both `systemctl restart` calls have already been scheduled. The fix has to be at the HTTP handler, before any work is scheduled.

4. **UFW rule ordering matters more than rule content.** v0.9.12 hardening shipped the right rules but in the wrong order on Server One installs. UFW is first-match-wins; a generic `deny` at position N+1 is invisible behind a specific `allow` at position N, but a generic `deny` at position N hides the specific `allow` at N+1. The v0.9.25 release will land the code-level fix (`ufw insert 1 allow ...`).
