# Plan — v0.9.26-alpha

> **Status:** SHIPPING (code complete on `dev`, awaiting operator field validation on tak-10)
> **Target:** v0.9.26-alpha
> **Scope:** Restore the v0.9.24-era "Authentik is perfectly quiet" baseline that v0.9.25 surfaced (but did not cause) was no longer holding. Two-part fix: replace the v0.9.5 weekly tasklog purge script that has been silently failing every Sunday for months, and add a Python-side inline purge that runs unconditionally on every console boot so we never depend on a single-point-of-failure systemd timer for hygiene that critical to Authentik stability.
>
> **Origin:** Field observation on `tak-10` (2026-05-17 04:39 UTC, ~30 minutes after operator confirmed v0.9.25 fixed the Sync webadmin / cap_drop issue). Authentik server CPU at sustained ~80%, load average 6.75/4.74/3.58 (trending up). Symptom-level pattern matched the v0.9.21 enterprise license cache leak, but root cause was different: `authentik_tasks_task` + `authentik_tasks_tasklog` tables grew to 442 MB / 640,596 rows and 516 MB / 2,025,958 rows respectively in under 24 hours of release-day churn, which made every license-cache request through Django's middleware stack slow enough to be cancelled by asgiref, causing the request-cancellation storm visible in `authentik-server-1` logs as repeated `context canceled / failed to proxy to backend` + `CancelledError exception in shielded future`.
>
> **Scope reshuffle:** The previous v0.9.26 plan (DataSync mission lifecycle investigation — ATAK "mission deleted but still in list" symptom) moved verbatim to `PLAN-v0.9.27-alpha.md`. The previous v0.9.27 plan (Node-RED ArcGIS multipart polygon) moved verbatim to `PLAN-v0.9.28-alpha.md`. Both moved scopes are unchanged.
>
> **Not a rollback.** v0.9.25's `cap_drop` heal stays in place — that fix is correct and confirmed working in the field. v0.9.26 is purely additive on top of v0.9.25.

---

## Why v0.9.26 exists

### The symptom (tak-10, 2026-05-17 04:39 UTC)

```
=== docker stats (10s sample) ===
CONTAINER                  CPU %     MEM USAGE / LIMIT     NET I/O
authentik-server-1         80.55%    1.281GiB / 47.04GiB   572MB / 739MB
authentik-pgbouncer-1      16.73%    6.645MiB / 47.04GiB   1.8GB / 1.82GB
authentik-postgresql-1     24.13%    469.8MiB / 47.04GiB   1.02GB / 757MB
authentik-worker-1         0.35%     537.3MiB / 47.04GiB   230MB / 301MB
```

- Load average: `6.75 / 4.74 / 3.58` — elevated and trending UP (1m > 5m > 15m).
- `authentik-server-1` pinned at 80% CPU for the entire observation window.
- `authentik-server-1` logs filled with `context canceled / failed to proxy to backend` (every ~15-30s) and `CancelledError exception in shielded future` through the full Django middleware stack:
  - `authentik/enterprise/middleware.py:39` (enterprise license check)
  - `authentik/rbac/middleware.py:46`
  - `authentik/core/middleware.py:94+117`
  - `authentik/events/middleware.py:157`
  - `authentik/brands/middleware.py:31`
  - `authentik/root/middleware.py:284+331`
- Gunicorn workers recycling every ~10 minutes with `Maximum request limit of 1033 exceeded. Terminating process.` (the v0.9.23 MAX_REQUESTS=1000 autotune firing).
- Outpost-side: `Get "http://0.0.0.0:9000/api/v3/outposts/proxy/?page=1&page_size=100": EOF` — outposts can't even read their own config from the server because the server is too busy to complete the response before the client times out.

### The PG state — surprisingly healthy

```
=== pg_stat_activity (Authentik) ===
state                | count
---------------------+-------
idle                 | 39
active               | 1
idle in transaction  | 1
```

39 idle + 1 active + 1 idle-in-tx. PgBouncer is doing its job. So the bottleneck is NOT at the connection layer — it's at the Authentik server gunicorn/asgi layer, with requests dying mid-flight in Django middleware.

### The smoking gun — task table bloat

```
=== Cache + channels table sizes ===
relname                          | size    | n_live_tup
---------------------------------+---------+------------
authentik_tasks_tasklog          | 516 MB  | 2,025,958
authentik_tasks_task             | 442 MB  | 640,596
authentik_events_event           | 179 MB  | 159,255
django_channels_postgres_message | 98 MB   | 31,480
django_postgres_cache_cacheentry | 816 kB  | 68
```

**~960 MB / 2.66 million rows in `authentik_tasks_*`**. The memory bank had documented these tables (v0.9.5):

> **Known recurring issue — task log bloat:** `authentik_tasks_task` and `authentik_tasks_tasklog` grow unbounded (~500–900 MB after 1 month). The `takauthentiktasklogpurge.timer` (v0.9.5+) handles weekly cleanup. `_authentik_tasklog_cleanup()` (v0.9.6+) also runs the DELETE + VACUUM on "Update Now" if either table exceeds 100 MB — clears the one-time backlog on first update.

But the v0.9.5 weekly timer is **failing**:

```
=== Last service run ===
× takauthentiktasklogpurge.service - Guard Dog Authentik Task Log Purge
   Loaded: loaded (/etc/systemd/system/takauthentiktasklogpurge.service; static)
   Active: failed (Result: exit-code) since Sun 2026-05-17 03:00:06 UTC
   Process: 3929285 ExecStart=/opt/tak-guarddog/tak-authentik-tasklog-purge.sh (code=exited, status=1/FAILURE)
        CPU: 862ms

May 17 03:00:00 systemd[1]: Starting Guard Dog Authentik Task Log Purge...
May 17 03:00:06 systemd[1]: takauthentiktasklogpurge.service: Main process exited, code=exited, status=1/FAILURE
```

Six seconds — fast fail. The log shows the exact reason:

```
[2026-05-17T03:00:00Z] Starting Authentik task log purge
DELETE 0
DELETE 0
ERROR:  VACUUM cannot run inside a transaction block
```

Two distinct bugs:

### Bug 1 — `VACUUM cannot run inside a transaction block`

The v0.9.5 script used a single `psql -c` argument with `DELETE; DELETE; VACUUM` in it. **psql wraps multi-statement `-c` arguments in an implicit transaction by default**, and `VACUUM` is explicitly forbidden inside transactions. The DELETEs succeeded (returning 0 — see Bug 2), then `VACUUM` errored, then `set -e` killed the script with exit code 1.

This script has been emitted exactly once at install time per `app.py:6488` (`if os.path.exists(_ak_compose_path) and not os.path.isfile(_ak_tl_tmr_path)`), so once installed, it's never updated. **Every install since v0.9.5 has been shipping with this broken script for ~6 months.** The Sunday timer fires correctly, runs the script, fails in 6 seconds, and the operator never sees anything unless they check `systemctl status` of the obscure unit name.

### Bug 2 — `mtime < NOW() - INTERVAL '30 days'`

The threshold was 30 days. tak-10's bloat (~2M rows of tasklog, ~640k rows of task) accumulated entirely **within the last 24 hours** during the 6+ console restart cycles of the v0.9.25 release-day arc. Even if VACUUM hadn't failed, the DELETEs would have matched **zero rows** — `DELETE 0 / DELETE 0` confirms it. The script's hardcoded 30-day floor doesn't help when the bloat is from yesterday.

### Why v0.9.25 looked like the culprit

It wasn't the cause, but it amplified the symptom:

1. The bloat was accumulating slowly before today (the v0.9.5 timer has been silently failing since install).
2. The v0.9.25 release-day arc forced 6+ console restarts (`systemctl restart takwerx-console`), each of which:
   - Runs every Authentik migration in `_startup_migrations` (PG idle timeout, gunicorn timeout, CONN_MAX_AGE, max_requests, pgbouncer, pool size, ldap flow, trusted proxy CIDRs, webadmin role check…).
   - Triggers Authentik server + worker to spin up a flurry of init tasks.
   - Each init task writes rows to `authentik_tasks_task` + `authentik_tasks_tasklog`.
3. Sustained operator load all day (Sync webadmin clicks, manual compose-heal API hits, LDAP outpost recreates) added ongoing task churn.

Net effect: tak-10 went from "moderately bloated, almost handling it" to "deeply bloated, tipped into request-cancellation storm" over the course of release day. Not v0.9.25's fault, but v0.9.25's release pattern was the catalyst that surfaced the underlying broken-timer rot.

**v0.9.24 was perfectly stable on tak-10** because the box had less accumulated bloat at that point AND wasn't under the release-day restart churn.

---

## What v0.9.26 ships

### Item 1 — Canonical task-log purge script with multi-tier escalation

Single source of truth: `_AUTHENTIK_TASKLOG_PURGE_SCRIPT` constant defined near `_self_heal_authentik_compose` (app.py around line 33985). Replaces the original v0.9.5 script content at both embedded write sites (app.py:6488 and app.py:7004).

Key changes from v0.9.5:

- **VACUUM ANALYZE in a SEPARATE `psql -c` invocation** so it is NOT wrapped in psql's implicit transaction. Bug 1 is closed.
- **Three-tier deletion ladder** based on remaining table size:
  - **Tier 1:** rows older than 7 days — gentle, keeps a week of audit trail for normal-operation installs.
  - **Tier 2:** rows older than 24 hours — only fires if combined table size still ≥ 100 MB after Tier 1.
  - **Tier 3 (nuclear):** rows older than 1 hour — only fires if combined size still ≥ 100 MB after Tier 2. Handles the "runaway-churn" case (release-day restart cycles, bloat purely from the last few hours).
- **Replaced `set -euo pipefail` with `set -u`** — the original `set -e` + `pipefail` setup turned every transient warning into a hard exit. Each tier explicitly handles non-zero returns by logging and continuing.
- **Idempotent size check up front** — if combined size < 100 MB, exit 0 silently. Healthy boxes are no-ops.
- **Diagnostic logging** — every tier writes its before/after size to `/var/log/takguard/authentik-tasklog-purge.log` so operators can grep the history.

### Item 2 — `_ensure_authentik_tasklog_purge_script(plog)` — overwrite drift heal

Runs on every console boot from `_startup_migrations`. Compares the on-disk script at `/opt/tak-guarddog/tak-authentik-tasklog-purge.sh` against the canonical content. If they differ, overwrites the on-disk version. Idempotent — only writes when content has drifted.

This is what gets the fix onto existing installs. The v0.9.5 emit-once-at-install pattern doesn't update existing boxes; this migration does.

### Item 3 — `_auto_authentik_tasklog_purge(plog)` — inline Python cleanup, every boot

Mirrors the on-disk script's tier ladder, executed by Python `subprocess.run` calls. Runs in `_startup_migrations` on every console boot. Doesn't depend on the systemd timer firing OR on the on-disk script being correct.

- Silent no-op when combined size < 100 MB.
- Logs initial + final sizes and row counts when cleanup runs.
- Each DELETE and the final VACUUM ANALYZE run in their own `psql -c` invocations (per the same Bug 1 fix as Item 1).
- Updates `/opt/tak-guarddog/authentik_tasklog_purge_last.txt` on success so the Guard Dog dashboard "last run" tile reflects the inline runs as well as the timer runs.
- Non-raising — any exception is logged and swallowed so it can't stall the rest of `_startup_migrations`.

### Item 4 — Wired into `_startup_migrations`

Placement: right after the v0.9.25 compose self-heal block, before `_authentik_apply_official_tunings`. Both helpers fire unconditionally on every console restart, no version gate, no lock gate. Same architectural lesson as v0.9.20's wiring-gap fix: migrations that need guaranteed reach belong in `_startup_migrations`, not `_post_update_auto_deploy`.

---

## What v0.9.26 does NOT change

- **v0.9.25's `cap_drop` heal stays in place.** Confirmed working in the field; not touched.
- **Authentik gunicorn workers / MAX_REQUESTS / PgBouncer pool sizing.** Already correct per v0.9.23.
- **Other Guard Dog timers.** Only `takauthentiktasklogpurge.service` had the VACUUM-in-transaction bug; `takautovacuum.service` (TAK Server postgres) uses a different code path.
- **Authentik version.** No image pull, no compose env changes.
- **TAK Server upstream NPE / DataSync mission lifecycle work.** Moved to v0.9.27 plan.

---

## Acceptance criteria

- [x] `_AUTHENTIK_TASKLOG_PURGE_SCRIPT` constant defined and shared by both embedded write sites.
- [x] `_ensure_authentik_tasklog_purge_script` overwrites broken on-disk scripts on every console boot.
- [x] `_auto_authentik_tasklog_purge` runs unconditionally in `_startup_migrations`, idempotent, silent when healthy.
- [x] Both DELETE and VACUUM run in separate `psql -c` invocations (Bug 1 fixed).
- [x] Three-tier threshold ladder (7 days → 24 hours → 1 hour) (Bug 2 fixed).
- [x] `app.py` AST-parses cleanly, no lint errors.
- [ ] Field validation on tak-10: combined `authentik_tasks_*` size drops from ~960 MB to < 100 MB on next console restart.
- [ ] Field validation on tak-10: `authentik-server-1` CPU returns to single-digit % steady state (was 80% sustained).
- [ ] Field validation on tak-10: `authentik-server-1` log free of `context canceled / CancelledError` for 30+ min after cleanup.
- [ ] Field validation on tak-10: next Sunday's `takauthentiktasklogpurge.service` run shows `Active: active (exited)` not `failed`.

---

## Test plan

### Pre-push (local)

1. `python3 -c "import ast; ast.parse(open('app.py').read())"` → exits 0.
2. Inspect `_AUTHENTIK_TASKLOG_PURGE_SCRIPT` to confirm three tiers + separate VACUUM invocation.
3. ReadLints on app.py — no new errors.

### Post-push (tak-10 field)

1. Operator clicks **Update Now** → console restarts → on first boot:
   - `_ensure_authentik_tasklog_purge_script` overwrites `/opt/tak-guarddog/tak-authentik-tasklog-purge.sh` with the canonical version.
   - `_auto_authentik_tasklog_purge` runs, detects ~960 MB bloat, escalates through Tier 1 → Tier 2 → Tier 3, then VACUUM ANALYZE.
   - Console log should show:
     ```
     Startup migration: Authentik tasklog purge script updated to v0.9.26 canonical version (fixes VACUUM-in-transaction bug)
     Startup migration: Authentik tasklog: <N> MB (task=<N> rows, tasklog=<N> rows) — running v0.9.26 cleanup
     Startup migration: Authentik tasklog: cleanup complete — <N> MB → <N> MB (freed <N> MB), task rows <N>→<N>, tasklog rows <N>→<N>
     ```
2. Operator runs `docker stats --no-stream` → `authentik-server-1` CPU should be < 10% within 1-2 minutes after cleanup.
3. Operator runs `journalctl -u takwerx-console --since "5 min ago" | grep -cE 'context canceled|CancelledError'` → should be 0 (was ~10/min before fix).
4. Next manual run of `/opt/tak-guarddog/tak-authentik-tasklog-purge.sh` → exits 0, log shows `No cleanup needed — already healthy` (the inline run already cleaned everything).
5. Wait for next scheduled Sunday 03:00 → `systemctl status takauthentiktasklogpurge.service` shows `Active: active (exited)` not `failed`.

---

## Files touched

- `app.py`
  - `VERSION` bumped `0.9.25-alpha` → `0.9.26-alpha`.
  - New constant `_AUTHENTIK_TASKLOG_PURGE_SCRIPT` (~70 lines).
  - New helper `_ensure_authentik_tasklog_purge_script(plog)` (~25 lines).
  - New helper `_auto_authentik_tasklog_purge(plog)` (~100 lines).
  - `_startup_migrations` — wired both new helpers in (unconditional, every boot).
  - Two embedded script writes (`_ak_tl_script_content` at line ~6490 and `_ak_purge_script` at line ~7004) replaced with references to the shared constant.
- `docs/PLAN-v0.9.26-alpha.md` — this file.
- `docs/PLAN-v0.9.27-alpha.md` — previously v0.9.26 (DataSync mission lifecycle).
- `docs/PLAN-v0.9.28-alpha.md` — previously v0.9.27 (Node-RED ArcGIS multipart polygon).
- `docs/RELEASE-v0.9.26-alpha.md` — release notes (planned).

---

## Lessons recorded

1. **Emit-once-at-install patterns are landmines.** The v0.9.5 script was correct at install time, became wrong at psql's transaction semantics (or always was wrong — silently masked by psql's default behavior). Either way, there's no migration path for the on-disk content unless you write one. **Any on-disk script generated by the console must have an `_ensure_<script>_canonical(plog)` migration that runs on every boot and overwrites drift.**
2. **Bash `set -euo pipefail` + `psql -c "DELETE; DELETE; VACUUM"` is a footgun.** `set -e` kills the script on the first non-zero exit. `psql -c` with multiple statements wraps them in an implicit transaction. VACUUM cannot run inside transactions. The combination silently fails every time. Use `set -u` + explicit error logging, and one `psql -c` per statement that needs distinct transaction semantics.
3. **Don't trust a systemd timer's existence as proof it's working.** `systemctl list-timers` shows the timer fires weekly. `systemctl status <service>` shows it failing. Add `_authentik_tasklog_purge_last.txt` checks to the Guard Dog dashboard so operators see "last successful run > 14 days ago → red" without having to know the exit-code-versus-trigger distinction.
4. **The architecturally correct home for "must-run-every-boot" cleanups is `_startup_migrations()`.** Same lesson as v0.9.20's wiring-gap fix and v0.9.25's compose-heal home. Both `_post_update_auto_deploy` and one-shot installer paths have legitimate gates that can skip them. Hygiene migrations that prevent CPU storms belong unconditional.
5. **80% CPU + middleware CancelledError ≠ enterprise license cache leak.** v0.9.21 documented that pattern with `django_postgres_cache_cacheentry` as the root cause. v0.9.26 shows the same symptom can be caused by `authentik_tasks_*` bloat instead. The cache table on tak-10 is healthy (816 kB / 68 rows). Always look at table sizes BEFORE diagnosing a CPU storm as the v0.9.21 leak class.

---

_Plan introduced 2026-05-17 in place of the v0.9.26 DataSync mission lifecycle plan (moved to v0.9.27) and the v0.9.27 Node-RED ArcGIS multipart polygon plan (moved to v0.9.28). Triggered by operator feedback after tak-10 surfaced sustained Authentik CPU regression immediately after the v0.9.25 release-day arc._
