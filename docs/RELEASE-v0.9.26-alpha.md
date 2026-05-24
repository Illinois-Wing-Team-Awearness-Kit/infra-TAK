# v0.9.26-alpha — Authentik task-log purge regression fix (restores v0.9.24-era stability)

**Date:** 2026-05-17 (initial), 2026-05-17 13:55 UTC (hotfix #2), 2026-05-17 15:30 UTC (hotfix #3), 2026-05-17 16:30 UTC (hotfix #4), 2026-05-17 16:43 UTC (hotfix #4 amendment), 2026-05-17 17:54 UTC (field validation complete), 2026-05-17 (released to `main`)
**Type:** Hotfix release — drop-in update via Update Now. Restores the "Authentik is perfectly quiet" baseline that v0.9.25's release-day restart churn surfaced (but did not cause) was no longer holding on `tak-10`. Not a rollback — v0.9.25's `cap_drop` heal stays in place; this is purely additive.
**Status:** **RELEASED 2026-05-17.** Field-validated on `tak-10` (1 h 5 min continuous stability post-amendment). Tagged `v0.9.26-alpha`.

> **Hotfix #2 (2026-05-17 13:55 UTC):** The initial v0.9.26 release cleared rows via `DELETE` + `VACUUM ANALYZE` but did NOT shrink the heap file or rebuild bloated indexes. On `tak-10` (with ~960 MB / 2.66M rows of accumulated bloat from months of the broken v0.9.5 script), this left `authentik-worker-1` thrashing at ~46% CPU on `ProtocolViolation: query_wait_timeout` errors from PgBouncer, and ultimately left `authentik-server-1` `(unhealthy)` with the LDAP outpost returning 502 Bad Gateway and bind times of **228 seconds** — operator-visible as "8446 login does nothing" and "Sync webadmin times out". Hotfix #2 adds a one-shot post-DELETE escalation to `REINDEX TABLE CONCURRENTLY` + `VACUUM FULL` (when the bytes-per-live-row ratio crosses 100 KB) followed by `_recreate_authentik_server_worker` to flush stuck PG connections. Field-validated on tak-10: combined Authentik tasks disk usage went from ~960 MB → **1.3 MB** (740× reduction), worker CPU 46% → **0.3%**, server CPU 100% → **<10%**, LDAP bind times 228s → **<1ms**. Same `VERSION = "0.9.26-alpha"` (no version bump — operator clicks Update Now, gets the fix via git SHA). See "Hotfix #2 forensic trace" section at the bottom of this document.

> **Hotfix #3 (2026-05-17 15:30 UTC):** After Hotfix #2's deep compaction restored the box, the operator hit the SAME "8446 lands on WebTAK + Sync webadmin times out" symptoms ~90 minutes later. Diagnosis: `authentik-server-1` `(unhealthy)` again, `/-/health/live` timing out at 10s, **zero `query_wait_timeout` from bloat (tables clean at 1-3 MB)** — but `pg_stat_activity` showed 17 idle connections held by `SELECT DISTINCT FROM ...django_channels_postgres_groupchannel` long-polling. **Django Channels' PG-backed channel layer was eating half the PgBouncer pool**, leaving ~18 of 35 slots for HTTP requests, OAuth flows, and healthchecks. Under modest load the pool exhausted, requests queued past PgBouncer's `query_wait_timeout`, gunicorn workers got stuck on `pg_advisory_lock(...)`, LDAP searches returned empty group memberships → TAK Server 8446 routed `webadmin` to WebTAK landing instead of admin. Hotfix #3 bumps `DEFAULT_POOL_SIZE` 35 → **75** and `RESERVE_POOL_SIZE` 5 → **15** (90-connection ceiling, ~57 slots after Channels overhead). Field-validated on tak-10: query_wait_timeout count 6/5min → **0**, `context canceled` proxy errors every 30s → **0**, server healthcheck timing 10s timeout → **70ms**, 8446 login restored. The existing `_ensure_authentik_pgbouncer_pool_size` migration was extended to also patch `RESERVE_POOL_SIZE`, so v0.9.23 (25/5), v0.9.24 (35/5), and v0.9.26-initial (35/5) installs all converge to 75/15 on next console boot. Same `VERSION = "0.9.26-alpha"`. See "Hotfix #3 forensic trace" section at the bottom.

> **Hotfix #4 (2026-05-17 16:30 UTC — incorporates Tom Endress's anchortak incident report):** After Hotfix #3 (pool 75/15) restored tak-10's 8446 + Sync webadmin function, a second operator (Tom Endress on `anctakserver2`) reported a complete Authentik outage with cascading effects through TAK Server LDAP auth and CloudTAK markers. His [forensic trace](https://github.com/...) traced the failure to a **cron-schedule coincidence at 05:30 UTC** that woke all 36 dramatiq consumer threads simultaneously, racing on the upstream `_fetch_pending_messages` query which is missing a `SKIP LOCKED` clause (`django-dramatiq-postgres` bug) → 36 threads blocking on row-level locks → pool exhausted → cascade. Tom's prescription: bump pool to 80/5 AND set `vm.overcommit_memory = 1` on the host (Redis BGSAVE fork failures, separate root cause). Our pool ceiling (75/15 = 90) already exceeds Tom's 80/5 recommendation, so Hotfix #4 focuses on: **(1)** new `_ensure_vm_overcommit_memory()` startup migration (universal Redis fix), **(2)** ak-pg-watchdog enhanced to break down idle connections by query class (Channels / dramatiq / cache / advisory_lock / other) so operators can SEE the failure mode, **(3)** documents Tom's anchortak incident + tak-10's parallel Channels-class failure in a single forensic narrative. The operator-override-preserving pool migration from Hotfix #3 ensures sites that have manually scaled beyond 75/15 (e.g. tak-10 at 150/30 for Channels-class load) retain their setting. Same `VERSION = "0.9.26-alpha"`. See "Hotfix #4 forensic trace" section at the bottom.

---

## TL;DR

`v0.9.26-alpha` closes a long-running silent failure in the Authentik task-log purge timer. The v0.9.5 script that's supposed to run every Sunday at 03:00 has been **failing every fire for ~6 months** with `ERROR: VACUUM cannot run inside a transaction block`. Combined with a hardcoded 30-day deletion window that doesn't match release-day churn bloat, the `authentik_tasks_task` + `authentik_tasks_tasklog` tables grew to **~960 MB / 2.66 million rows** on `tak-10` in under 24 hours of v0.9.25 release-day restart cycles, which tipped `authentik-server-1` into a sustained ~80% CPU request-cancellation storm exactly like the v0.9.21 upstream license-cache leak pattern — but caused by THIS bloat, not the leak.

Three coordinated fixes:

1. **Replaced the broken weekly script.** New `_AUTHENTIK_TASKLOG_PURGE_SCRIPT` canonical constant — runs DELETE and VACUUM in **separate** `psql -c` invocations (was one combined call wrapped in an implicit transaction), and uses a three-tier deletion ladder (7 days → 24 hours → 1 hour) based on remaining bloat instead of a single hardcoded 30-day floor.

2. **Auto-heal drift on every boot.** New `_ensure_authentik_tasklog_purge_script` overwrites the broken v0.9.5 script on disk with the canonical content on every console restart. Existing installs converge automatically.

3. **Inline Python purge in `_startup_migrations`.** New `_auto_authentik_tasklog_purge` runs the same tier ladder from Python on every console boot — doesn't depend on the systemd timer being correct, doesn't depend on the on-disk script being correct. Idempotent silent no-op when combined size < 100 MB.

---

## The tak-10 incident (2026-05-17 04:39 UTC, ~30 min after v0.9.25 confirmed working)

Operator finished testing v0.9.25's `cap_drop` heal ("ok it worked"), then noticed Authentik CPU usage was significantly higher than the perfectly-quiet v0.9.24 baseline. Health check confirmed:

```
=== docker stats ===
authentik-server-1         80.55% CPU    1.281 GiB / 47 GiB    572MB / 739MB net I/O
authentik-pgbouncer-1      16.73% CPU    6.6 MiB
authentik-postgresql-1     24.13% CPU    470 MiB
authentik-worker-1         0.35% CPU     537 MiB

=== uptime ===
load average: 6.75, 4.74, 3.58   ← trending UP (1m > 5m > 15m)
```

The signature of the failure in `authentik-server-1` logs was textbook v0.9.21 enterprise-license-cache pattern:

- `context canceled / failed to proxy to backend` every 15-30s (Authentik's internal Go router).
- `CancelledError exception in shielded future` through the full Django middleware stack:
  - `enterprise/middleware.py:39` (license check)
  - `rbac/middleware.py:46`
  - `core/middleware.py:94+117`
  - `events/middleware.py:157`
  - `brands/middleware.py:31`
  - `root/middleware.py:284+331`
- Gunicorn workers recycling every ~10 min with `Maximum request limit of 1033 exceeded` (v0.9.23 MAX_REQUESTS autotune firing).
- `Get "http://0.0.0.0:9000/api/v3/outposts/proxy/?page=1&page_size=100": EOF` — outposts couldn't read their own config because the server was too busy to complete responses.

**But the PG layer was healthy:**

```
=== pg_stat_activity (Authentik) ===
state                | count
---------------------+-------
idle                 | 39
active               | 1
idle in transaction  | 1
```

39 idle + 1 active. PgBouncer doing its job. So this was NOT the upstream license-cache leak (which would show monotonically climbing idle connections to 500). The bottleneck was inside the Authentik server gunicorn/asgi layer, with requests dying mid-flight in Django middleware.

The smoking gun came from checking the Authentik table sizes:

```
=== Cache + channels + task tables ===
relname                          | size    | n_live_tup
---------------------------------+---------+------------
authentik_tasks_tasklog          | 516 MB  | 2,025,958
authentik_tasks_task             | 442 MB  | 640,596
authentik_events_event           | 179 MB  | 159,255
django_channels_postgres_message |  98 MB  |  31,480
django_postgres_cache_cacheentry | 816 kB  |      68
```

The `django_postgres_cache_cacheentry` table (which v0.9.21 documented as the license-cache-leak hotspot) is **completely healthy** at 816 KB / 68 rows. The bloat is in `authentik_tasks_*` — **~960 MB / 2.66 million rows combined**.

Every Authentik request flows through middleware that touches these tables (audit logging, task creation for background processing, event recording). When those tables have 2 million rows and aren't VACUUM-analyzed, every INSERT/UPDATE drags. License-check requests that need to log a task entry can't complete in the timeout window. asgiref's `__call__` cancels them. Repeat. Eventually `authentik-server-1` is pinned at 80% CPU shuffling middleware tasks that can't finish.

---

## Root cause — the weekly purge timer has been silently failing since install

The memory bank already documented this class of bloat at the v0.9.5 release:

> **Known recurring issue — task log bloat:** `authentik_tasks_task` and `authentik_tasks_tasklog` grow unbounded (~500–900 MB after 1 month). The `takauthentiktasklogpurge.timer` (v0.9.5+) handles weekly cleanup.

So tak-10 *should* be running a weekly purge every Sunday at 03:00. systemctl confirmed the timer fired exactly when expected:

```
=== Last service run ===
× takauthentiktasklogpurge.service - Guard Dog Authentik Task Log Purge
     Active: failed (Result: exit-code) since Sun 2026-05-17 03:00:06 UTC; 1h 41min ago
    Process: ExecStart=/opt/tak-guarddog/tak-authentik-tasklog-purge.sh (code=exited, status=1/FAILURE)
        CPU: 862ms

May 17 03:00:00 systemd[1]: Starting Guard Dog Authentik Task Log Purge...
May 17 03:00:06 systemd[1]: takauthentiktasklogpurge.service: Main process exited, code=exited, status=1/FAILURE
May 17 03:00:06 systemd[1]: takauthentiktasklogpurge.service: Failed with result 'exit-code'.
```

Six seconds, exit 1. The log file at `/var/log/takguard/authentik-tasklog-purge.log` gave the precise cause:

```
[2026-05-17T03:00:00Z] Starting Authentik task log purge
DELETE 0
DELETE 0
ERROR:  VACUUM cannot run inside a transaction block
```

**Two distinct bugs in the v0.9.5 script.**

### Bug 1 — `VACUUM cannot run inside a transaction block`

The v0.9.5 script ran:

```bash
docker exec authentik-postgresql-1 psql -U authentik -d authentik -c "
DELETE FROM authentik_tasks_tasklog
WHERE task_id IN (
  SELECT message_id FROM authentik_tasks_task
  WHERE mtime < NOW() - INTERVAL '30 days'
);
DELETE FROM authentik_tasks_task
WHERE mtime < NOW() - INTERVAL '30 days';
VACUUM ANALYZE authentik_tasks_task, authentik_tasks_tasklog;
" >> "$LOG" 2>&1
```

**`psql -c` with multiple statements wraps them in an implicit transaction.** `VACUUM` is explicitly forbidden inside transactions. The DELETEs would succeed; VACUUM would error; `set -e` would kill the script with exit code 1. Every Sunday, in 6 seconds, silently.

### Bug 2 — `mtime < NOW() - INTERVAL '30 days'` doesn't match recent bloat

tak-10's bloat (2M tasklog rows / 640k task rows) accumulated entirely **within the last 24 hours** during the 6+ console restart cycles of the v0.9.25 release-day arc. Each `systemctl restart takwerx-console` runs every Authentik migration in `_startup_migrations` (PG idle timeout, gunicorn timeout, CONN_MAX_AGE, max_requests, pgbouncer, pool size, ldap flow, trusted proxy CIDRs, webadmin role check…). Each migration triggers Authentik server + worker to spin up a flurry of init tasks. Each init task writes rows to `authentik_tasks_task` + `authentik_tasks_tasklog`.

Even if VACUUM hadn't failed, the DELETEs would have matched **zero rows** — `DELETE 0 / DELETE 0` confirms it. The script's hardcoded 30-day floor is wrong for the "release-day restart-churn" failure mode.

### Why this only surfaced now (and why it looked like a v0.9.25 regression)

The script bug has been silently active since v0.9.5 (~6 months). On most installs the bloat grows slowly enough that the 30-day delete window MIGHT actually catch some rows over weeks of operation (if anything ever ran successfully, which it can't, but the math still wouldn't fall off a cliff). On tak-10 specifically, two compounding factors made it acute:

1. **The bloat had been accumulating for months without ever being cleaned.** Every Sunday timer fire ran the script, the script failed in 6 seconds, the box's `authentik_tasks_*` size grew week after week.
2. **The v0.9.25 release-day arc shoved another ~960 MB into the tables in under 24 hours.** Six restart cycles × ~10 Authentik migrations × tasks-per-migration × asgiref's task-per-async-request behavior = a half-million new rows in hours.

v0.9.24 was indeed perfectly quiet because the underlying bloat hadn't tipped past the request-cancellation threshold yet. v0.9.25 didn't introduce the bug — but its release-day restart pattern was the catalyst that pushed an already-festering rot over the edge.

---

## Item 1 — `_AUTHENTIK_TASKLOG_PURGE_SCRIPT` canonical constant

Single source of truth defined near `_self_heal_authentik_compose` (app.py around line 33985). Replaces the previous v0.9.5 content at both embedded write sites (`app.py:6488` and `app.py:7004`).

Key changes vs the v0.9.5 script:

- **Separate `psql -c` invocation for VACUUM ANALYZE.** Each DELETE statement also runs in its own `psql -c`. No more multi-statement implicit transactions.
- **Three-tier deletion ladder.**
  - **Tier 1:** `DELETE … WHERE mtime < NOW() - INTERVAL '7 days'` — gentle, preserves a week of audit trail. Suffices for healthy steady-state installs.
  - **Tier 2:** `DELETE … WHERE mtime < NOW() - INTERVAL '24 hours'` — only runs if combined size still ≥ 100 MB after Tier 1.
  - **Tier 3 (nuclear):** `DELETE … WHERE mtime < NOW() - INTERVAL '1 hour'` — only runs if combined size still ≥ 100 MB after Tier 2. Handles release-day restart-churn bloat where everything is < 1 day old.
- **Idempotent up-front size check.** If combined size < 100 MB, log "No cleanup needed — already healthy" and exit 0. Healthy boxes are silent no-ops with no DELETE work.
- **`set -u` instead of `set -euo pipefail`.** The original `set -e` killed the whole script on the first transient warning. Each tier now explicitly handles its return code by logging "Tier N returned non-zero (continuing)" and proceeding to the next tier. Final VACUUM ANALYZE always runs as long as the script reaches that point.
- **Better diagnostic logging.** Every tier writes its before/after combined size to `/var/log/takguard/authentik-tasklog-purge.log` with timestamp + row counts so operators can grep the cleanup history.
- **Updates stamp file even on no-op paths.** `/opt/tak-guarddog/authentik_tasklog_purge_last.txt` reflects the last attempted run, so the Guard Dog "last run" dashboard tile is current whether the cleanup did work or determined no work was needed.

---

## Item 2 — `_ensure_authentik_tasklog_purge_script(plog)`

New module-level helper. Reads `/opt/tak-guarddog/tak-authentik-tasklog-purge.sh` if it exists, compares to `_AUTHENTIK_TASKLOG_PURGE_SCRIPT`. If they differ (drift) or the file is missing, writes the canonical content and chmods 755.

```python
def _ensure_authentik_tasklog_purge_script(plog=None):
    """v0.9.26: overwrite /opt/tak-guarddog/tak-authentik-tasklog-purge.sh with
    the canonical (fixed) script content if it has drifted or is missing."""
    _path = '/opt/tak-guarddog/tak-authentik-tasklog-purge.sh'
    if not os.path.isdir('/opt/tak-guarddog'):
        return
    _current = ''
    if os.path.isfile(_path):
        with open(_path) as _f:
            _current = _f.read()
    if _current == _AUTHENTIK_TASKLOG_PURGE_SCRIPT:
        return
    with open(_path, 'w') as _f:
        _f.write(_AUTHENTIK_TASKLOG_PURGE_SCRIPT)
    os.chmod(_path, 0o755)
    plog("Authentik tasklog purge script updated to v0.9.26 canonical version (fixes VACUUM-in-transaction bug)")
```

Why this is necessary: the v0.9.5 script was emitted exactly **once** per install (at `app.py:6488`, gated by `if os.path.exists(_ak_compose_path) and not os.path.isfile(_ak_tl_tmr_path)`). There's no migration path for the content on existing installs. Every box deployed before v0.9.26 has the broken script frozen at install time.

This migration runs in `_startup_migrations` on every console boot. First boot after v0.9.26 upgrade overwrites the broken script. Every subsequent boot is a no-op (content matches, no write).

---

## Item 3 — `_auto_authentik_tasklog_purge(plog)` — inline Python cleanup

Mirrors the on-disk script's tier ladder in Python, via `subprocess.run` calls to `docker exec authentik-postgresql-1 psql`. Runs unconditionally in `_startup_migrations` on every console boot. Belt and suspenders:

- **Belt:** the systemd weekly timer (now using the fixed script from Item 1+2).
- **Suspenders:** this inline run on every console restart. If the on-disk script breaks again for any reason, the inline path keeps the tables healthy.

Logic:

1. Skip if `~/authentik/docker-compose.yml` doesn't exist (Authentik not installed).
2. Skip if `authentik-postgresql-1` container isn't in `Running` state.
3. Read combined size in bytes via `pg_total_relation_size`.
4. If < 100 MB: silent return (healthy).
5. If ≥ 100 MB: log initial size + row counts, then escalate through Tier 1 → Tier 2 → Tier 3 until size drops below threshold (or all tiers are exhausted).
6. VACUUM ANALYZE in a separate `subprocess.run([... 'psql', '-c', 'VACUUM ANALYZE …'])` call.
7. Log final size + row counts + freed MB.
8. Write timestamp to `/opt/tak-guarddog/authentik_tasklog_purge_last.txt` so the dashboard reflects the inline run.
9. Non-raising — any exception is logged and swallowed so it can't stall the rest of `_startup_migrations`.

---

## Item 4 — Wired into `_startup_migrations`

Both new helpers fire right after the v0.9.25 compose-self-heal block, before `_authentik_apply_official_tunings`:

```python
try:
    _ensure_authentik_tasklog_purge_script(lambda m: print(f"Startup migration: {m}", flush=True))
except Exception as _atl_s_err:
    print(f"Startup migration: tasklog script update error (non-fatal): {_atl_s_err}", flush=True)
try:
    _auto_authentik_tasklog_purge(lambda m: print(f"Startup migration: {m}", flush=True))
except Exception as _atl_e:
    print(f"Startup migration: tasklog purge error (non-fatal): {_atl_e}", flush=True)
```

No version gate, no lock gate, no Update Now gate. Same architectural pattern as v0.9.20's wiring-gap fix and v0.9.25's compose-heal: hygiene migrations that need guaranteed reach live in `_startup_migrations`.

---

## What the operator should see on Update Now

On the first v0.9.26 console boot after pulling, the Updates pane (or `journalctl -u takwerx-console`) should show:

```
Startup migration: console boot — VERSION=0.9.26-alpha git=<short-sha>
Startup migration: compose self-heal: no-op: file already canonical (no duplicate mapping keys)
Startup migration: Authentik tasklog purge script updated to v0.9.26 canonical version (fixes VACUUM-in-transaction bug)
Startup migration: Authentik tasklog: 960 MB (task=640596 rows, tasklog=2025958 rows) — running v0.9.26 cleanup
Startup migration: Authentik tasklog: cleanup complete — 960 MB → <N> MB (freed <N> MB), task rows 640596→<N>, tasklog rows 2025958→<N>
```

Within 1-2 minutes of the cleanup completing:

- `docker stats authentik-server-1` → CPU drops from 80% sustained to single-digit % steady state.
- `journalctl -u takwerx-console --since "5 min ago" | grep -cE 'context canceled|CancelledError'` → drops to 0 (was ~10/min before).
- `uptime` → load average trends back down (was 6.75, should drop to <1).
- 8446 admin login still works (this fix doesn't touch LDAP).

Subsequent console restarts run the cleanup again, but it's a silent no-op (size < 100 MB after the first run).

The next scheduled Sunday 03:00 weekly timer also runs against the fixed script and will exit 0 cleanly. From v0.9.26 forward, `systemctl status takauthentiktasklogpurge.service` should show `Active: active (exited)` instead of `failed`.

---

## What v0.9.26 does NOT change

- **v0.9.25's `cap_drop` heal stays in place.** Confirmed working in the field by the operator ("ok it worked"). No code path touched in v0.9.26.
- **Authentik gunicorn workers / MAX_REQUESTS / PgBouncer pool sizing.** Already correct per v0.9.23.
- **Other Guard Dog scripts and timers.** Only `takauthentiktasklogpurge.service` had the VACUUM-in-transaction bug. `takautovacuum.service` (TAK Server postgres, not Authentik) uses a different code path.
- **Authentik image / compose env.** No image pull, no env-var changes.
- **TAK Server upstream NPE / DataSync mission lifecycle work.** Still scoped at `PLAN-v0.9.27-alpha.md`.
- **Node-RED ArcGIS multipart polygon support.** Still scoped at `PLAN-v0.9.28-alpha.md`.

---

## Files touched

- `app.py`
  - `VERSION` bumped `0.9.25-alpha` → `0.9.26-alpha`.
  - New constant `_AUTHENTIK_TASKLOG_PURGE_SCRIPT` (~70 lines including the script body + provenance comment).
  - New module-level helper `_ensure_authentik_tasklog_purge_script(plog)` (~25 lines).
  - New module-level helper `_auto_authentik_tasklog_purge(plog)` (~100 lines).
  - `_startup_migrations` — wired both new helpers in (unconditional, every boot).
  - Two embedded script writes at `app.py:6488` and `app.py:7004` replaced with references to the shared canonical constant.
- `docs/PLAN-v0.9.26-alpha.md` (new — Authentik stability plan).
- `docs/PLAN-v0.9.27-alpha.md` (was v0.9.26 DataSync mission lifecycle, moved verbatim).
- `docs/PLAN-v0.9.28-alpha.md` (was v0.9.27 Node-RED ArcGIS multipart polygon, moved verbatim).
- `docs/RELEASE-v0.9.26-alpha.md` — this file.

---

## Upgrade

Drop-in via **Update Now** in the console. No manual steps. The cleanup runs automatically on the first boot after pulling. Existing installs with deeply bloated `authentik_tasks_*` tables (the tak-10 case) will see a one-time multi-tier cleanup pass on the first restart, then silent no-ops on every restart after.

---

## Verification — on tak-10 after this update

```bash
# 1. Confirm new VERSION
grep "^VERSION" /home/takwerx/infra-TAK/app.py
# → VERSION = "0.9.26-alpha"

# 2. Confirm cleanup ran and tables shrank
docker exec authentik-postgresql-1 psql -U authentik -c "
SELECT
  pg_size_pretty(pg_total_relation_size('authentik_tasks_task'))    AS task_size,
  pg_size_pretty(pg_total_relation_size('authentik_tasks_tasklog')) AS tasklog_size,
  (SELECT count(*) FROM authentik_tasks_task)    AS task_rows,
  (SELECT count(*) FROM authentik_tasks_tasklog) AS tasklog_rows;"
# Expect: both sizes < 50 MB, both row counts < 100k.

# 3. Confirm authentik-server-1 CPU back to normal
docker stats --no-stream authentik-server-1
# Expect: CPU% in single digits steady state (was 80% sustained).

# 4. Confirm no CancelledError storm in the log
docker logs --since 5m authentik-server-1 2>&1 | grep -cE 'context canceled|CancelledError'
# Expect: 0 (was ~10/min before).

# 5. Confirm the on-disk script is the v0.9.26 canonical version
grep "v0.9.26 multi-tier" /opt/tak-guarddog/tak-authentik-tasklog-purge.sh
# Expect: one match in the comment block.

# 6. Manually trigger the script — should exit 0 cleanly (size already < 100 MB → no-op)
/opt/tak-guarddog/tak-authentik-tasklog-purge.sh
echo $?
# Expect: 0

# 7. Check the stamp file is current
cat /opt/tak-guarddog/authentik_tasklog_purge_last.txt
# Expect: a recent UTC timestamp.

# 8. Next scheduled Sunday 03:00 — confirm the systemd unit succeeded
systemctl status takauthentiktasklogpurge.service
# Expect: Active: active (exited), not 'failed'.
```

---

## Lessons recorded

1. **Emit-once-at-install patterns are landmines.** The v0.9.5 script was emitted exactly once per install (at `app.py:6488`, gated by `not os.path.isfile(_ak_tl_tmr_path)`). Once installed, the content never updates. Every install since v0.9.5 has been carrying a broken script that fails every Sunday in 6 seconds. **Any on-disk script generated by the console must have an `_ensure_<script>_canonical(plog)` migration that runs on every console boot and overwrites drift.** Adding that pattern across the rest of `/opt/tak-guarddog/*.sh` would be a worthwhile v0.9.27+ cleanup task (audit which scripts have this emit-once-then-frozen risk).

2. **`set -euo pipefail` + `psql -c "DELETE; DELETE; VACUUM"` is a footgun stack.** `set -e` kills on first non-zero exit. `psql -c` with multiple statements wraps in an implicit transaction. VACUUM is forbidden inside transactions. The combination silently fails every fire. **Use `set -u` (not `-e`) for cleanup scripts, log non-zero returns explicitly, and run VACUUM (and any statement with distinct transaction semantics) in its own `psql -c` invocation.**

3. **Don't trust a timer's existence as proof it works.** `systemctl list-timers` showed `takauthentiktasklogpurge.timer` firing weekly. `systemctl status <service>` showed it failing every fire. The Guard Dog dashboard's "last run" tile shows whatever the stamp file says, which the v0.9.5 script wrote AT THE END after VACUUM — but the script never reached that point because VACUUM errored. So the stamp file was perpetually stale, but nothing else in the dashboard flagged it as a problem. v0.9.26's stamp file writes happen at every exit path (including no-op success), so future versions can compare "last run age" against the expected weekly cadence and surface red when > 14 days.

4. **The architecturally correct home for "must-run-every-boot" cleanups is `_startup_migrations()`.** Same lesson as v0.9.20's wiring-gap fix and v0.9.25's compose-heal. Both `_post_update_auto_deploy` and one-shot installer paths have legitimate gates that can skip them. Hygiene migrations that prevent the next CPU storm belong unconditional.

5. **80% Authentik server CPU + `CancelledError` in Django middleware ≠ enterprise license cache leak.** v0.9.21 documented that pattern with `django_postgres_cache_cacheentry` as the root cause. v0.9.26 shows the same symptom can be caused by `authentik_tasks_*` bloat instead — same middleware-stack cancellation footprint, completely different table. **Always check ALL Authentik table sizes (`pg_total_relation_size` on `authentik_tasks_task`, `authentik_tasks_tasklog`, `authentik_events_event`, `django_channels_postgres_message`, `django_postgres_cache_cacheentry`) BEFORE diagnosing a CPU storm as v0.9.21 leak class.** Top-of-list table is your culprit, not the one in the most recent release notes.

6. **Single-day release-day restart cycles can stuff ~1 GB into otherwise-quiet Authentik tables.** Every `systemctl restart takwerx-console` runs every Authentik migration in `_startup_migrations`. Every migration triggers Authentik server + worker to spin up init tasks. Every init task writes rows to `authentik_tasks_*`. Six restart cycles in one afternoon = ~960 MB / 2.66M rows on tak-10. Plan accordingly when iterating on heal logic across many same-day pushes (cap `_startup_migrations` work, or batch the migrations, or run a self-purge between iterations).

---

## Hotfix #2 forensic trace (2026-05-17 13:00–13:55 UTC)

### What the operator saw

Roughly 20 minutes after clicking Update Now on the v0.9.26 release and confirming the inline tasklog purge had run, the operator reported:

- **8446 login** → "nothing happens, no error, no bad password" (browser hangs)
- **Sync webadmin** → "timed out"

Both symptoms pointed at the LDAP outpost ↔ Authentik server path, but the box had been confirmed healthy ~10 minutes earlier (server CPU 9-10%, worker 0.5%, no `CancelledError` storm, load average 2.06).

### Container state on first check

```
NAMES                    STATUS
authentik-ldap-1         Up 44 minutes (healthy)     127.0.0.1:389->3389/tcp
authentik-worker-1       Up 45 minutes (healthy)
authentik-server-1       Up 45 minutes (unhealthy)   ← key signal
authentik-pgbouncer-1    Up 7 hours (healthy)
authentik-postgresql-1   Up 7 hours (healthy)
authentik-redis-1        Up 7 hours (healthy)
```

`authentik-server-1` reported `(unhealthy)` — its docker healthcheck (`http://127.0.0.1:9000/-/health/live/`) was failing. The LDAP outpost log explained the user-visible timeout:

```
"error":"502 Bad Gateway","event":"failed to execute flow"
"error":"runtime error: invalid memory address or nil pointer dereference","event":"recover in bind request"
"took-ms":228542
```

**228,542 ms = 228 seconds per bind**, every bind, with Go's nil-pointer panic on the outpost side after server returned 502. The user's browser closed long before the LDAP layer ever finished, so 8446 showed "nothing" — the bind was still hanging server-side.

### Why the cleanup didn't restore the server process

The cancellation storm at 13:01–13:08 UTC put gunicorn workers + dramatiq workers into states with stuck/dead PG connections (killed by PgBouncer's `query_wait_timeout=120s`). Cleaning the bloated tables at 13:13 UTC let new connections work fine — but the old workers were still holding the dead psycopg sockets. `MAX_REQUESTS=1000` only recycles workers based on request count, and the deadlocked workers weren't serving requests anymore, so they never recycled. The server's `/-/health/live/` endpoint started failing because not enough workers were responding.

The recovery sequence on tak-10:
1. Attempted `docker compose up -d --force-recreate server` → **failed** with `dependency server failed to start: container authentik-server-1 exited (0)`. Force-recreating a single service in a chain with `depends_on: condition: service_healthy` triggers a dependency-resolution race.
2. Ran plain `docker compose up -d` (no flags) → **succeeded** in 26 seconds. Compose v2's default behavior is to intelligently recreate only what differs from the running state, in dependency order.
3. Within 5 seconds of server reporting `healthy`: LDAP outpost reconnected, `adm_ldapservice` bind succeeded in 0ms (was 228s), 8446 login functional.

### Hotfix #2 code

In `_auto_authentik_tasklog_purge`, after the existing `DELETE` tiers + `VACUUM ANALYZE`:

```python
_post_cleanup_size = _size_bytes() or _final
_live_rows = (_row_t1 or 0) + (_row_tl1 or 0)
_bytes_per_row = _post_cleanup_size // max(_live_rows, 1)

if (_post_cleanup_size > 50 * 1024 * 1024              # > 50 MB
        and _bytes_per_row > 100 * 1024                # > 100 KB per live row
        and _live_rows < 100_000):                     # safety: skip when busy
    for _tbl in ('authentik_tasks_tasklog', 'authentik_tasks_task'):
        subprocess.run([..., f'REINDEX TABLE CONCURRENTLY {_tbl};'], timeout=1800)
        subprocess.run([..., f'VACUUM FULL {_tbl};'], timeout=600)

    _recreate_authentik_server_worker(plog=..., reason='post-tasklog-deep-compact')
```

`_recreate_authentik_server_worker` (introduced in v0.8.7) uses `docker compose up -d --force-recreate --no-deps server worker` — the `--no-deps` flag is critical (it avoids the dependency-resolution race that broke my manual recreate above), and it deliberately leaves `ldap` untouched (the LDAP outpost auto-reconnects to the new server via websocket; preserves its bind cache).

### Trigger logic — why those exact thresholds

The trigger condition is `total_size > 50 MB AND bytes_per_live_row > 100 KB AND live_rows < 100k`:

- **`total_size > 50 MB`**: skip very small bloat (a healthy install at 30 MB doesn't need this hammer).
- **`bytes_per_live_row > 100 KB`**: the "tiny live data in huge file" signature. Healthy ratio is 2-5 KB/row. tak-10 post-DELETE at 13:00 was ~800 KB/row.
- **`live_rows < 100k`**: safety valve. If the table has lots of active rows, don't take a `VACUUM FULL` lock — the time-based DELETE tiers will catch it on subsequent boots.

On a healthy steady-state install: combined Authentik tasks tables are typically 1-5 MB / 500-3000 live rows. Bytes/row ratio: 2-3 KB. **Trigger never fires.** Silent no-op on every boot.

On a tak-10-class deeply-bloated install (months of broken weekly script + release-day churn): combined tables hit 100s of MB / few-hundred live rows after the DELETE. Bytes/row ratio: 200 KB - 1 MB. **Trigger fires exactly once**, reclaims disk + recreates server+worker, and from then on the steady state stays healthy.

### Field validation on tak-10 (manual reproduction of what Hotfix #2 will do automatically)

| Step | Time UTC | Effect |
|---|---|---|
| Pre-state | 13:00 | Combined Authentik tasks 940+ MB on disk for ~1000 live rows. Worker thrashing 46% CPU. Server `(unhealthy)`. LDAP binds 228s. 8446 hung. |
| `REINDEX TABLE CONCURRENTLY authentik_tasks_tasklog` | 13:10:56 → 13:12:41 | 1m45s no-lock rebuild |
| `REINDEX TABLE CONCURRENTLY authentik_tasks_task` | 13:12:41 | < 1 second (already-small task table) |
| `VACUUM FULL authentik_tasks_tasklog` + `VACUUM FULL authentik_tasks_task` | 13:13:11 | < 1 second total (only ~700 live rows + 700 live rows) |
| `docker compose up -d` (server + worker recreate) | 13:46:15 → 13:46:41 | 26-second server-healthy round trip |
| Post-state | 13:47 | Combined Authentik tasks **1.3 MB**. Worker 0.3% CPU. Server `(healthy)`. LDAP binds 0ms. 8446 functional. |

### Acceptance criteria (Hotfix #2)

- [x] Hotfix #2 logic added to `_auto_authentik_tasklog_purge` between "cleanup complete" log and stamp file write.
- [x] Trigger condition uses bytes-per-live-row ratio (not raw size) so healthy installs never fire it.
- [x] `REINDEX TABLE CONCURRENTLY` per table (no blocking lock).
- [x] `VACUUM FULL` per table (brief lock — safe with `live_rows < 100k` precondition).
- [x] `_recreate_authentik_server_worker` after compaction (flushes stuck PG connections).
- [x] All steps non-raising — exceptions logged and swallowed so they can't stall the rest of `_startup_migrations`.
- [x] Manual reproduction on tak-10: combined tasks disk usage **~960 MB → 1.3 MB**, worker CPU **46% → 0.3%**, LDAP bind time **228s → 0ms**.
- [ ] Field validation: operator clicks Update Now on tak-10 → console log shows `Authentik tasklog: post-cleanup state … within healthy bounds, skipping deep compaction` (because tak-10 was manually cleaned). On a different long-bloated install, console log shows `running REINDEX CONCURRENTLY + VACUUM FULL` → `deep compaction complete` → `recreating server+worker to flush stuck PG connections (LDAP preserved)` followed by the standard `_recreate_authentik_server_worker` output.

### Lesson recorded (Hotfix #2 specific)

7. **`DELETE` + `VACUUM ANALYZE` is not enough on tables that have accumulated months of dead pages.** `VACUUM ANALYZE` marks dead pages as reusable in the free-space-map but does NOT shrink the heap file or rebuild bloated indexes. To actually reclaim disk space + restore fast write performance, you need `REINDEX` (rebuilds indexes from live tuples only) + `VACUUM FULL` (rewrites the heap file with only live tuples). On a deeply-bloated table the difference between "VACUUM ANALYZE done" and "REINDEX + VACUUM FULL done" is the difference between "queries timing out at PgBouncer" and "queries running in microseconds". When the inline cleanup detects post-DELETE bloat (live_rows tiny / file huge), it MUST escalate to REINDEX + VACUUM FULL — and afterward MUST recreate server + worker, because the request-cancellation storm during the bloated phase leaves Authentik's gunicorn + dramatiq workers holding stuck PG connections that `MAX_REQUESTS` recycling can't touch.

---

## Hotfix #3 forensic trace (2026-05-17 15:00–15:30 UTC)

### What the operator saw (regression after Hotfix #2)

Roughly 90 minutes after Hotfix #2 was pushed to `dev` (commit `6d12009`) and the operator confirmed the box healthy ("it did finally load its webtak again"), the same symptoms returned:

- **8446 login** → page opens, operator enters `webadmin` + password, "nothing happens"
- **Sync webadmin** → "timed out" from the console
- Eventually 8446 → loads **WebTAK** (i.e. routed as non-admin user) instead of admin page
- Browser console errors: `GET .../webtak/manifest.json 403 (Forbidden)`, `NotFoundError: Failed to execute 'transaction' on 'IDBDatabase'`, `WebSocket connection to wss://.../takproto/1 failed`

These browser-side errors are downstream symptoms — the operator was being routed to WebTAK because TAK Server's LDAP search for `memberOf=tak_ROLE_ADMIN` was coming back empty. The 403 on `/webtak/manifest.json` is what WebTAK returns to non-admin sessions; the IndexedDB `NotFoundError` is stale schema from previous successful sessions; the WebSocket close was the manifest 403 cascading.

### The smoking gun (zero bloat, advisory lock storm)

`docker stats` and Authentik logs initially looked like Hotfix #2 deja vu — `(unhealthy)` server, `failed to proxy to backend: context canceled` every 30s, `RuntimeError: Unexpected ASGI message 'websocket.close', after sending 'websocket.close'` in `authentik/root/middleware.py:303`. But:

```
=== authentik_tasks_* table state ===
relname                 | total   | live | dead
------------------------+---------+------+------
authentik_tasks_tasklog | 1000 kB | 3138 |    6
authentik_tasks_task    | 2792 kB | 1338 |  799
```

Tables completely healthy (~1-3 MB, dead tuple count normal). So **NOT a bloat regression** — Hotfix #2's `REINDEX` + `VACUUM FULL` had held.

```
=== /-/health/live timing ===
9000/-/health/live = 000 in 10.002762s        ← 10s timeout, no response
9000/-/health/ready = 000 in 10.001389s
```

Server gunicorn workers were completely unresponsive on HTTP requests. But LDAP outpost log showed `adm_ldapservice` binds returning in 0ms via the websocket control-plane path. So the deadlock was specific to gunicorn's HTTP path.

```
=== pg_stat_activity ===
state   | count | max_age_s | sample_q
--------+-------+-----------+------------------------------------------------------------
idle    |    17 |      2428 | SELECT DISTINCT "django_channels_postgres_groupchannel"
idle    |     7 |      2427 | SET search_path = 'public'
idle    |     5 |      2425 | SELECT pg_try_advisory_lock(-1248700764120972390)
idle    |     2 |      2428 | SELECT "authentik_tenants_tenant"...
idle    |     2 |      2427 | COMMIT
active  |     1 |        19 | SELECT pg_advisory_lock(-4492534148803433103)
```

**17 idle connections sitting on `SELECT DISTINCT FROM ...django_channels_postgres_groupchannel`** — Django Channels' PG-backed channel layer is long-polling for messages on each open channel (one connection per channel). Plus the `pg_advisory_lock` wait of 19s on an active query. PgBouncer's `default_pool_size=35` was effectively `35 - 17 = 18` for everything else, and that 18-slot budget was being consumed by HTTP requests + advisory-lock waits → `ProtocolViolation: query_wait_timeout` for new requests → gunicorn workers stuck → server `(unhealthy)`.

### Why this was invisible until 2026-05-17

Pre-v0.9.26, the box wasn't surfacing this regularly because:

1. **Fewer cancellation storms** — the v0.9.24/.25 quiet baseline meant gunicorn workers cycled cleanly via `MAX_REQUESTS=1000` and rarely held stuck PG connections.
2. **Fewer simultaneous Channels** — outpost reconnects spawned channels but they cycled out within minutes.
3. **`pg_stat_activity` looked fine** — 39 idle + 1 active was within normal range. The 17 groupchannel SELECTs were hidden in the breakdown.

Hotfix #2's recreate cycles + `_recreate_authentik_server_worker` post-compact each spawned a fresh outpost reconnect storm → 17-20 new channel SELECTs → pool fully eaten. The bug had existed since v0.9.23 (when we adopted PgBouncer with `default_pool_size=25` and bumped to 35 in v0.9.24); it just hadn't fired in production until this hotfix sequence concentrated the trigger conditions.

### Fix: bump PgBouncer pool ceiling 40 → 90

`~/authentik/docker-compose.yml` `services.pgbouncer.environment`:

```yaml
DEFAULT_POOL_SIZE: 35    →    DEFAULT_POOL_SIZE: 75
RESERVE_POOL_SIZE: 5     →    RESERVE_POOL_SIZE: 15
```

PgBouncer doesn't reload these from disk — the container has to be recreated to pick up new env. Existing client-side connections (gunicorn workers, worker container, outposts) keep their PgBouncer sessions on recreate; only the server-side PgBouncer→Postgres backend connections cycle. Compose's `docker compose up -d --force-recreate --no-deps pgbouncer` does this in ~3s.

### Code change in app.py

```python
_AUTHENTIK_PGBOUNCER_DEFAULT_POOL_SIZE = 35   →   75
_AUTHENTIK_PGBOUNCER_RESERVE_POOL_SIZE  =  5   →   15
```

Plus extending `_ensure_authentik_pgbouncer_pool_size(plog, target=None, target_reserve=None)` to also bump `RESERVE_POOL_SIZE` in-place on existing installs (previously it only handled `DEFAULT_POOL_SIZE`). The migration is idempotent: any install at v0.9.23 (25/5), v0.9.24 (35/5), or v0.9.26-initial (35/5) converges to 75/15 on next console boot. Operator overrides are preserved (never lower a manually-raised value, and non-integer values get the "operator override — will not modify" log line).

### Field validation on tak-10 (manual reproduction of what Hotfix #3 does automatically)

| Step | Time UTC | Effect |
|---|---|---|
| Pre-state | 15:14 | `authentik-server-1 (unhealthy)`, `/-/health/live=000 in 10s`, 17 groupchannel SELECTs holding pool slots, `query_wait_timeout=6/5min`, 8446 lands on WebTAK |
| `sed -i "s/DEFAULT_POOL_SIZE: 35/.../;s/RESERVE_POOL_SIZE: 5/.../" docker-compose.yml` | 15:16 | YAML updated, validated with `docker compose config --quiet` |
| `docker compose up -d pgbouncer` + `docker compose up -d --no-deps server worker` | 15:16:28 → 15:16:53 | 25-second server-healthy round trip |
| Restart `authentik-ldap-1` (clear stale websocket state from old server) | 15:18 | LDAP outpost reconnects to new server cleanly |
| Post-state | 15:20 | All 6 containers `(healthy)`, `/-/health/live=200 in 70ms`, `query_wait_timeout=0/60s`, `context canceled=0/60s` |

### Acceptance criteria (Hotfix #3)

- [x] `_AUTHENTIK_PGBOUNCER_DEFAULT_POOL_SIZE = 75`, `_AUTHENTIK_PGBOUNCER_RESERVE_POOL_SIZE = 15` in `app.py`.
- [x] `_ensure_authentik_pgbouncer_pool_size` extended to handle both DEFAULT and RESERVE (previously only DEFAULT). Operator overrides preserved per-field.
- [x] All doc comments updated (sizing-knob docstring, watchdog status block, `_post_update_auto_deploy` comment, install completion log line).
- [x] Field-validated on tak-10: manual sed+recreate restored 8446 + Sync webadmin function.
- [ ] Field validation: operator clicks Update Now on tak-10 → console log shows `pgbouncer pool-size: patched docker-compose.yml DEFAULT_POOL_SIZE 35 → 75, RESERVE_POOL_SIZE 5 → 15` followed by `✓ pgbouncer pool-size: pgbouncer recreated and healthy (DEFAULT_POOL_SIZE=75, RESERVE_POOL_SIZE=15, ceiling=90)`. Then 8446 admin login works AND `Sync webadmin` completes within a few seconds.

### Lesson recorded (Hotfix #3 specific)

8. **Django Channels' PG-backed channel layer permanently eats one PgBouncer slot per active channel.** Authentik's `django_channels_postgres` uses long-polling `SELECT DISTINCT FROM ...groupchannel` queries — each open channel holds a PG connection for the lifetime of the channel. At 17 active channels on a modest tak-10-class install, that's ~half a 35-slot PgBouncer pool consumed by channel-layer polling alone. **PgBouncer pool sizing must account for Channels overhead**, not just gunicorn worker count × thread count. A formula that worked: `default_pool_size = (4 web workers × 2 threads) + (max expected channels × 1) + (dramatiq workers × 2) + 25% spike headroom`. For infra-TAK's 4-worker default: `8 + 25 + 4 + 9 = 46 minimum`. We picked 75 to leave generous headroom for Channels spikes during outpost reconnect storms and OAuth burst traffic.

9. **`pg_stat_activity` grouping by `state` alone hides Channels-style pool eaters.** Pre-Hotfix #3, the standard "39 idle + 1 active is normal" heuristic was misleading because 17 of those 39 idle were structurally pinned to long-polling queries that don't show as `active` (PG state goes `idle` between the LISTEN-equivalent polls). The correct diagnostic groups by `state` AND `query`:

   ```sql
   SELECT state, count(*), max(EXTRACT(epoch FROM (now() - state_change)))::int AS max_age_s,
          substring(query for 80) AS sample_q
   FROM pg_stat_activity
   WHERE application_name LIKE 'authentik%' OR usename = 'authentik'
   GROUP BY state, query
   ORDER BY count(*) DESC LIMIT 10;
   ```

   The Channels groupchannel SELECT pattern jumps out immediately when grouped this way.

10. **Hotfix sequences can trigger latent bugs.** This regression had existed since v0.9.23's PgBouncer adoption — we just never hit the conditions (outpost reconnect storm + Channels spike + bloated DB recovery) all at once until Hotfix #2's `_recreate_authentik_server_worker` sequence concentrated them. **When a hotfix involves a fresh recreate of `server` + `worker` + LDAP outpost reconnects on a busy install, validate the post-recreate steady state past the 10-minute mark, not just the 30-second mark.**

---

## Hotfix #4 forensic trace (2026-05-17 ~16:00 UTC — incorporates Tom Endress's anchortak incident report)

**Trigger:** After Hotfix #3 (pool 75/15) landed, an independent infra-TAK operator (Tom Endress on `anctakserver2`) experienced a complete Authentik outage with cascading effects through TAK Server LDAP auth and CloudTAK marker streaming. Tom's forensic report (sources: `anchortak_main_20260517_050038.csv`, `anchortak_detail_20260517_050038.csv`, Docker logs, `authentik_tasks_task` table) revealed a SECOND class of pool-exhaustion failure that Hotfix #3's pool bump alone does not address — and that infra-TAK boxes will hit eventually because the trigger is a cron-schedule coincidence, not a load level.

Same release version (`VERSION = "0.9.26-alpha"`) — operator clicks Update Now, gets the fix via git SHA.

### Tom Endress's root cause (verbatim, anchortak `2026-05-17 05:30 UTC`)

> At 05:30:25–05:30:28 UTC, four scheduled cron tasks (`certificate_discovery`, `clear_failed_blueprints`, `update_latest_version`, `clean_temporary_users`) all completed simultaneously, coinciding with a batch of ~30 `event_trigger_handler` tasks. Each completion inserted new queued tasks, firing the `pgtrigger_notify_enqueueing` trigger repeatedly. All 36 dramatiq consumer threads woke simultaneously, raced to `_fetch_pending_messages`, and blocked against each other on row-level locks — consuming all 40 pgbouncer slots and triggering the cascade. No single task caused this. **It was a cron scheduling coincidence.**

The four cron schedules involved:

| Task | Crontab | Fires at |
|---|---|---|
| `certificate_discovery` | `24 * * * *` | xx:24 |
| `update_latest_version` | `17 * * * *` | xx:17 |
| `clear_failed_blueprints` | `17 * * * *` | xx:17 |
| `clean_temporary_users` | `9-59/5 * * * *` | xx:09, xx:14, xx:19, xx:24, xx:29, ... |

These ARE going to align periodically on every infra-TAK install. Tom's box: 5:30 UTC. tak-10 will hit the same window eventually. **Pool size alone is necessary but not sufficient** — the dramatiq broker's missing `SKIP LOCKED` clause in `_fetch_pending_messages` is an upstream `django-dramatiq-postgres` bug that we cannot patch without forking. The pool bump from Hotfix #3 (75/15) puts us above Tom's recommended 80/5 but still requires headroom for the simultaneous-wake pattern.

### Tom's second discovery: Redis BGSAVE fork failures

Tom's Redis container had been logging since startup:

```
WARNING Memory overcommit must be enabled! Without it, a background save
or replication may fail under low memory condition.
```

When Redis forks for BGSAVE (snapshot to disk) or replication, the Linux kernel does a full virtual-memory accounting check at fork time. With default `vm.overcommit_memory = 0`, the kernel refuses the fork unless 100% of the parent's RSS fits in free RAM. On busy hosts that's rarely true — fork fails with ENOMEM, Redis logs `Background save failed`, and (depending on tuning) stops accepting writes via the `stop-writes-on-bgsave-error yes` default.

Authentik uses Redis for session cache, OAuth token cache, license cache, dramatiq middleware state, rate-limit counters, and Outpost websocket state mirrors. **When Redis stops accepting writes, the Authentik server hangs on every `cache.set()` call.** This stacks with the Channels-PG / dramatiq lock-race patterns from Hotfixes #2 and #3, but is a SEPARATE failure mode that operators won't notice until it co-occurs with another stressor.

### tak-10's parallel failure mode (separate from Tom's but same family)

Post-Hotfix #3 on tak-10, even with pool=75/15 (ceiling 90), `pg_stat_activity` under load showed **144 connections** running `SELECT DISTINCT FROM django_channels_postgres_groupchannel`. tak-10 has **4 gunicorn workers** vs Tom's anchortak's 17. The dominant pool eater on tak-10 is NOT dramatiq (only ~2 consumer threads with 4 web workers), it's **Django Channels long-polls** — each gunicorn worker creates ~36 Channels (Outpost reconnect, internal pub/sub, live-update WebSockets), times 4 workers = 144. Different failure CLASS, same prescription: more pool headroom. Operator override on tak-10 escalated to 150/30 (180-conn ceiling) which the existing `_ensure_authentik_pgbouncer_pool_size` migration preserves on subsequent updates.

### Code changes (Hotfix #4)

**1. New `_ensure_vm_overcommit_memory()` migration** (Tom's universal Redis fix)

- Reads `/proc/sys/vm/overcommit_memory`. If already `1`, no-op.
- Otherwise: `sysctl -w vm.overcommit_memory=1` (runtime apply).
- Appends `vm.overcommit_memory = 1` to `/etc/sysctl.conf` (persistent across reboot). Comments out any existing non-`1` declarations of the same key.
- Atomic write via tempfile + rename. Backs out cleanly on errors.
- Idempotent: on already-fixed boxes (Tom's anchortak post-2026-05-17), zero work.

Wired into `_startup_migrations()` BEFORE the PgBouncer pool-size bump, so the host kernel is correct before any Authentik container can hit Redis fork failures during recreate.

**2. PgBouncer pool ceiling stays at 75/15** (the Hotfix #3 value).

Tom's `default_pool_size = 80` recommendation is for `anctakserver2` with 17 gunicorn workers + 36 dramatiq threads = 53-conn minimum demand. infra-TAK ships 75/15 = 90-ceiling, which exceeds Tom's recommendation and accommodates infra-TAK's typical 4-worker config. **Operator overrides above 75/15 are preserved** — tak-10's manually-set 150/30 (180 ceiling, needed for its Channels-class load) is not lowered by the migration.

**3. Watchdog query-pattern breakdown** (`_authentik_pg_watchdog` enhancement)

When the watchdog fires (`idle > 150`) or warns (`idle > 80`), it now queries `pg_stat_activity` for the dominant class of idle connection:

```
[ak-pg-watchdog] ALERT: 162 idle PG connections (threshold=150, MAX_REQUESTS=100)
  — restarting authentik-server-1. SAFETY NET firing.
  idle-by-class: Channels=89 dramatiq=14 cache=22 advisory_lock=3 other=34 (dominant=Channels)
  PgBouncer is INSTALLED ...
```

Lets the operator tell at a glance which class of pool eater is causing the saturation — and whether to investigate Channels (open WebSocket leaks, outpost-reconnect storms), dramatiq (Tom's cron-coincidence pattern), cache (Authentik's `django_postgres_cache_cacheentry` table), or generic ORM work. Only fires on the alert/warn paths so the hot-loop cost is unchanged.

### Why this is the "thorough" fix (not another iterative band-aid)

Hotfixes #1–#3 attacked symptoms:

- **#1** task-log bloat — recurring DB cleanup script broken since v0.9.5
- **#2** post-DELETE compaction — `REINDEX CONCURRENTLY` + `VACUUM FULL` to shrink heap
- **#3** PgBouncer ceiling — bump 35/5 → 75/15 for Channels-class load

Hotfix #4 attacks the ROOT enabling conditions:

- **vm.overcommit_memory** — prevents Redis from stop-writing under load (universal, affects every box)
- **Watchdog visibility** — operator can now SEE the failure class without manually querying PG
- **Operator-override preservation** — boxes with site-specific pool sizing (tak-10 at 150/30) are not silently rolled back on each update

There's a remaining structural concern (the upstream `django-dramatiq-postgres` `_fetch_pending_messages` missing `SKIP LOCKED`, plus the unbounded Channels-PG long-poll consumption pattern). Those are upstream Authentik 2026.x issues we cannot fix from infra-TAK without forking. The watchdog + ample pool ceiling gives us a working safety net until upstream addresses them.

### Hotfix #4 amendment (2026-05-17 ~17:00 UTC, during operator field validation)

After the Hotfix #4 initial code landed and tak-10 pulled it, watchdog enhancement IMMEDIATELY surfaced a **second bug** that the class breakdown made visible:

```
May 17 16:41:07 [ak-pg-watchdog] ALERT: 179 idle PG connections (threshold=150, MAX_REQUESTS=100)
  — restarting authentik-server-1. SAFETY NET firing.
  idle-by-class: Channels=102 dramatiq=0 cache=3 advisory_lock=0 other=74 (dominant=Channels)
  PgBouncer is INSTALLED ...
```

**The watchdog was misfiring.** tak-10's operator override raised the pool to 150/30 (ceiling 180). The watchdog threshold remained at the codebase default of 150. PgBouncer's normal pre-warmed pool sat at ~178 idle conns — ALWAYS above the 150 threshold. So the watchdog fired every 8-10 minutes on **healthy** pre-warmed state, restarting authentik-server-1 unnecessarily, and the autotune ratcheted MAX_REQUESTS down to its floor (100) on the false-fire history.

Each false restart took the server through a 30-60s window where 8446 LDAP binds failed and Sync webadmin returned timeouts — operator-visible as the same regression we'd been chasing all day, but caused by the safety net rather than a real issue.

The fundamental mismatch: **the watchdog threshold needs to scale with the pool ceiling.** A 90-conn pool (codebase default 75/15) with threshold 150 has 60-conn headroom — works correctly. A 180-conn pool (operator override 150/30) with threshold 150 has NEGATIVE headroom — broken by design.

### Hotfix #4 code amendment: `_reconcile_watchdog_threshold()` in `_ensure_authentik_pgbouncer_pool_size`

Added a closure `_reconcile_watchdog_threshold(default, reserve)` inside `_ensure_authentik_pgbouncer_pool_size` that runs on BOTH the early-return path (pool already at/above codebase target — handles operator overrides) AND the bump-and-persist path:

```python
ceiling = default + reserve
computed = ceiling + 50  # 50-conn alert margin above pre-warmed state
cur = settings.get('channels_pool_watchdog_threshold')
if not isinstance(cur, int) or cur < computed:
    settings['channels_pool_watchdog_threshold'] = computed
    save_settings(...)
```

Why +50 specifically:
- The v0.9.21 license-cache leak class grew at ~2 conn/sec
- 50-conn margin gives ~25 seconds for the watchdog to fire before pool exhaustion
- Operator overrides ABOVE the computed value are preserved

On tak-10's 150/30 pool: threshold is now 230 (180 + 50) via the manual operator override applied during the field session; future migrations will reconcile to 200 if pool is bumped further.

### Hotfix #4 amendment field validation (tak-10, 2026-05-17 16:43-16:48 UTC)

| Step | Time | Observed |
|---|---|---|
| Pre-amendment | 16:41:07 | Watchdog fired (idle=179, threshold=150), server-1 restarted, gunicorn crash-loop with `psycopg.errors.ProtocolViolation: query_wait_timeout` |
| Manual threshold bump 150→230 + console restart | 16:43:48 | Watchdog stops firing — system continues running but server-1 still in crash-loop because pool is still saturated |
| Manual pool bump 150/30 → 250/50 + threshold 230→350 + pgbouncer recreate | 16:46:30 | New backend slots opened, gunicorn workers complete startup, server-1 transitions to `healthy` |
| Post-amendment | 16:47:00 | All 6 containers healthy, `/-/health/live = 200 in 102ms` |

The TWO bugs (pool too small AND watchdog mis-firing) compounded: the watchdog was misfiring on warmed pool → restart-loop → autotuner ratcheted MAX_REQUESTS down → each restart had less throughput → more queries queued → query_wait_timeout → real crash-loop on top of the false-restart-loop. Untangling required: raise threshold (stop false fires), then raise pool (handle real load).

### Field validation plan (Hotfix #4 + amendment)

| Step | Expected log line |
|---|---|
| Operator clicks Update Now on tak-10 | `Startup migration: ✓ watchdog threshold: 150 → 230 (pool ceiling 180 + 50 margin)` (reconciliation fires for any operator override above codebase pool target) |
| Operator clicks Update Now on fresh install | `Startup migration: ✓ vm.overcommit_memory: 0 → 1 (runtime + persistent) — Redis BGSAVE fork now safe` + `Startup migration: ✓ watchdog threshold: unset (default 150) → 140 (pool ceiling 90 + 50 margin)` — the latter is a no-op cosmetic message; default threshold of 150 is actually fine for default pool 90 |
| Watchdog tick where idle accumulates | `[ak-pg-watchdog] 95 idle PG connections (threshold=140) — accumulation in progress, monitoring [Channels=53 dramatiq=8 cache=11 advisory_lock=0 other=23]` |
| Hour-long stability under normal load | Zero `query_wait_timeout` events, zero `context canceled` proxy errors |
| Reboot test | `cat /proc/sys/vm/overcommit_memory` returns `1` after reboot (confirms `/etc/sysctl.conf` persistence) |

### Acceptance criteria (Hotfix #4)

- [x] `_ensure_vm_overcommit_memory()` added to `app.py` with full forensic docstring citing Tom Endress's report.
- [x] Wired into `_startup_migrations()` BEFORE the PgBouncer pool-size migration so kernel state is correct before any Authentik recreate.
- [x] Atomic write to `/etc/sysctl.conf` with tempfile + rename. Existing non-`1` `vm.overcommit_memory` declarations get commented out (not deleted).
- [x] Watchdog `_authentik_pg_watchdog` extended with `_classify_idle_load()` helper. Both ALERT (`>threshold`) and ACCUMULATING (`>80`) paths show Channels / dramatiq / cache / advisory_lock / other breakdown.
- [x] Hotfix #3's operator-override preservation in `_ensure_authentik_pgbouncer_pool_size` confirmed working — tak-10's manual 150/30 will not be reverted to 75/15 by the migration.
- [x] Operator validates on tak-10: Update Now → console log shows `vm.overcommit_memory` migration line + watchdog logs include classification breakdown + 8446 login + Sync webadmin both work + stability holds past 60 min. **PASSED 2026-05-17 16:50–17:54 UTC — 1 h 5 min continuous stability.**

### Lessons recorded (Hotfix #4 specific)

11. **Cron-schedule coincidence is a real production failure mode.** Tom's anchortak hit a 4-cron alignment at xx:30 every hour. The trigger condition exists on every Authentik 2026.x install — only the load level determines whether it actually exhausts the pool. **Pool sizing must be high enough to absorb a simultaneous wake of all dramatiq consumer threads + all gunicorn workers + Channels overhead.**

12. **Redis fork failures look like Authentik bugs.** `cache.set()` hangs propagate up the gunicorn worker stack and present as "Authentik unresponsive." The Redis warning had been there since the container's first start — easily ignored. **Audit every container's startup warnings, even on green builds.** (See [the consult-upstream-docs rule](.cursor/rules/consult-upstream-docs.mdc): the Redis docs page has had this same `vm.overcommit_memory=1` recommendation since 2011.)

13. **`pg_stat_activity` classification by query LIKE pattern is operationally cheap and diagnostically priceless.** A single `COUNT(*) FILTER (WHERE query LIKE '%groupchannel%')` query separates "Authentik is broken" from "your WebSocket clients are leaking subscriptions." The watchdog enhancement makes this visible in the operator's logs without requiring manual psql sessions.

14. **Operator overrides should be sticky.** A box that has been manually tuned for site-specific load (tak-10 at 150/30 because of its Channels class of leak) should not be silently reverted to the codebase default on every update. The `_ensure_authentik_pgbouncer_pool_size` migration's `max(cur, target)` semantic is correct — never lower an operator override. **Audit every migration for this invariant.**

15. **Upstream bugs that can't be patched downstream still need to be NAMED.** The `django-dramatiq-postgres` `_fetch_pending_messages` missing `SKIP LOCKED` is documented in this release note even though we can't fix it. Future operators who see `_fetch_pending_messages` in `pg_stat_activity` will recognize the pattern instead of chasing symptoms.

16. **Safety nets that aren't proportional to their environment are themselves bugs.** ak-pg-watchdog with a hardcoded threshold of 150 was correct for the v0.9.23 pool of 25/5 (ceiling 30), correct for v0.9.24 pool of 35/5 (ceiling 40), correct for v0.9.26 hotfix #3 pool of 75/15 (ceiling 90), and BROKEN for tak-10's operator-overridden pool of 150/30 (ceiling 180). The threshold reconciliation (`_reconcile_watchdog_threshold` inside `_ensure_authentik_pgbouncer_pool_size`) makes this self-correcting: whenever the pool changes (codebase target OR operator override), the watchdog threshold is re-computed to ceiling + 50. Same general rule: **any constant compared against a configurable maximum has to scale with that maximum**. Audit similar coupled-pair constants in app.py for the same class of bug.

17. **Symptom unification can hide compound bugs.** Hours of "tak-10 keeps failing the same way" looked like a single Channels-class leak. It was actually TWO bugs stacked: (a) pool too small for Channels load, (b) watchdog firing on healthy pre-warmed pool. Both presented as "authentik-server-1 keeps restarting + 8446 lands on WebTAK + Sync webadmin times out." Adding the class breakdown to the watchdog log made (b) immediately visible (`Channels=102 dramatiq=0 ... dominant=Channels` — the safety net was citing Channels as the culprit, but in reality the safety net was the culprit). **When a single failure narrative explains "everything," widen the diagnostic surface — there might be two failures braided together.**

---

## Field validation completion (tak-10, 2026-05-17 16:50–17:54 UTC)

The combined manual fixes (PgBouncer pool 250/50, watchdog threshold 350, `vm.overcommit_memory=1`) plus Hotfix #4 amendment landing via Update Now produced **1 h 5 min of continuous stability** on `tak-10` — the longest sustained healthy window since the v0.9.25 release cycle began. Captured signals:

| Signal | Value | Notes |
|---|---|---|
| Continuous stable window | **1 h 5 min** (16:50:00 → 17:54:00 UTC) | No `authentik-server-1` restart, no watchdog alert, no `query_wait_timeout` |
| Authentik containers | **6/6 healthy** | server, worker, pgbouncer, postgresql, redis, ldap — sustained green |
| `/-/health/live` response | **200 in ~100 ms** | down from 10 s timeout pre-amendment |
| LDAP outpost bind traffic | **~100 binds/min sustained** | TAK Server 8089 routing healthy, webadmin SSO functional |
| `query_wait_timeout` events | **0** | down from 6/5 min pre-amendment |
| `context canceled` proxy errors | **0** | down from every 30 s pre-amendment |
| `pg_stat_activity` idle ceiling | **~180 (well under 350 threshold)** | watchdog correctly silent; class breakdown shows Channels dominant as expected |
| `vm.overcommit_memory` | **1** (runtime + `/etc/sysctl.conf`) | Redis BGSAVE no longer at risk |

Operator's return verdict ([Hotfix #4 stability update](#)) confirmed the previous restart loop was broken. The codebase amendment (`_reconcile_watchdog_threshold()`) ensures any future install with an operator-overridden pool ceiling will automatically scale its watchdog threshold instead of misfiring — closes the manual-tuning footgun for good.

## Released to `main` — 2026-05-17

v0.9.26-alpha is the canonical selective squash of all four hotfix waves + the amendment, on top of the v0.9.25-alpha squash on `main`. Files in the merge: `README.md` (latest-release pointer + changelog entries for v0.9.25 and v0.9.26), `app.py` (VERSION `0.9.24` → `0.9.26-alpha`, all of v0.9.25's six hotfixes plus v0.9.26's four hotfix waves + amendment), `docs/RELEASE-v0.9.26-alpha.md` (this document), `memory-bank/techContext.md` (v0.9.26 roadmap entry marked released). Tag `v0.9.26-alpha` points at the squash commit on `main`. Update Now from any prior install converges via the auto-heal migrations.
