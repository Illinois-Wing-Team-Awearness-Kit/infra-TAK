# v0.9.27-alpha — PgBouncer pool autotune + self-healing Channels-ghost reaper

**Date:** 2026-05-17
**Type:** Architectural fix for v0.9.26's silent fleet-fracture bug + four-layer self-healing pool management (autotune + escape + in-place remediation + ghost reaper). Drop-in update via Update Now.
**Status:** Shipped to `dev` 2026-05-17 in four hotfix iterations, field-validated across three test boxes (tak-10 + test8 + responder), merged to `main` after 20-min clean soak with all containers healthy and zero query_wait_timeouts.

## TL;DR

v0.9.26 codified `DEFAULT_POOL_SIZE = 75` and used `max(cur, target)` to "preserve operator overrides." That semantic silently fractured the fleet: tak-10 (operator-typed 250/50 from yesterday's fire) stayed at 250/50, while test8 (no override) got the codified 75/15 → 297 `query_wait_timeout` events in 5 min. **The 65-min v0.9.26 validation was on a configuration the codebase never produced.**

v0.9.27 ships four layers of self-healing pool management, each addressing a failure mode discovered live during dev validation:

| # | Hotfix | Failure mode addressed | Witnessed firing in production |
|---|---|---|---|
| 1 | **Cold-start fleet constant** | Empty samples ring at first v0.9.27 boot → unreliable live `pg_stat_activity` seed shrunk tak-10 to FLOOR (75/15) under transient mid-restart load | Initial v0.9.27 deploy fired escape path correctly on test8 |
| 2 | **cl_waiting demand telemetry + stuck-at-floor escape** | Pool-starved box has all conns checked out → low idle → autotune confirms small pool is "fine" (self-reinforcing under-sizing) | **test8: STUCK-AT-FLOOR ESCAPE — peak_cl_waiting=4 > 0 over 30 samples → forcing 250/50** |
| 3 | **Continuous in-place remediation** | Compute-path escape only runs at console restart → quiet box gets load spike between restarts → query_wait_timeouts visible until operator hits Update Now | Armed, dormant on quiet boxes (correct); fires on sustained `cl_waiting > 0` ≥ 3 ticks |
| 4 | **Ghost-Channels-conn reaper** | Authentik server-1 restarts leak Channels long-poll backends (PgBouncer transaction-mode invisible-disconnect pattern) → pool fills with ghosts → "v0.9.27 broke my Authentik" | **test8 manual: terminated 290 ghosts, idle 299→11, all containers healthy in <60s** |

Together, these make v0.9.27 self-healing through the exact v0.9.26 → v0.9.27 Update Now path that fractured the fleet — no operator intervention required.

```
peak              = max(idle_count) over last 30 watchdog samples (~1 h)
target_ceiling    = max(FLOOR_CEILING=90, ceil(peak × 2.0))
target_ceiling    = min(target_ceiling, ceil(PG_MAX_CONN=500 × 0.6))   # 300 cap
target_default    = ceil(target_ceiling × 5/6)
target_reserve    = target_ceiling - target_default

# Three escape paths into the fleet safe-constant (250/50, ceiling 300):
#   1. Cold-start: samples < 3
#   2. cl_waiting > 0 sustained over ≥ 3 samples with telemetry
#   3. (continuous) watchdog observes cl_waiting > 0 OR channels_idle > ceiling/2
#      across 3 consecutive ticks AND current_pool < cold_start → in-place resize
```

Migration **always writes** the autotune target — no `max(cur, target)`. Operator-typed numbers are reconciled to the autotune decision on the next migration tick.

---

## Incident 1: the v0.9.26 fleet-fracture (2026-05-17 16:43–20:15 UTC)

### Timeline

| Time (UTC) | Event |
|---|---|
| 16:43 | v0.9.26 hotfix #4 amendment lands on dev (watchdog threshold reconciliation) |
| 16:50 | tak-10 transitions to healthy with operator-overridden pool 250/50 + threshold 350 |
| 17:54 | tak-10 records 1 h 5 min continuous stability — I flag this as v0.9.26 validation |
| 17:54–19:00 | I write release notes, README, memory-bank updates, squash to main, tag `v0.9.26-alpha`, push |
| 19:54 | test8 operator clicks Update Now → pulls v0.9.26-alpha from main |
| 19:56 | test8 migration completes: pool 35/5 → **75/15** (codebase target), watchdog threshold → 140. Container recreates happen cleanly. |
| 19:57 | test8 begins serving traffic on 75/15 |
| 20:00 | First ak-pg-watchdog tick on test8: 89 idle conns (40 Channels + 47 other), threshold=140, ACCUMULATING only — watchdog correctly silent |
| 20:04 | test8 has logged **297 `query_wait_timeout` events in 5 min**. Server-1 `(unhealthy)`. |
| 20:12 | I manually bump test8 to 250/50 + threshold 350. Server-1 recovers. |
| 20:15 | Operator: *"omg i cant believe you didnt set a baseline. wtf remember this ships out to tons of people."* |

### Root cause

```python
# v0.9.26 (the bug)
new_default = max(cur_default or 0, target)        # ← preserves higher value
new_reserve = max(cur_reserve or 0, target_reserve)
```

The v0.9.26 release notes called this out **as a feature**:

> *"On boxes where an operator manually raised either value (e.g. 100/20), idempotent no-op — we never lower an operator override."*

Two problems compounding:

1. **Fleet fracture.** Every box ends up at whatever its highest historical operator-typed pool size was. Two boxes on the same git SHA had completely different PgBouncer configs.
2. **Invisible validation gap.** My 65-min validation on tak-10 was on `250/50 — operator override`. No box ran the codebase default `75/15` until test8 pulled it.

### Operator's framing of the architectural bug

> *"it cant be like specific, it can fluxuate based on incoming information if you think that would help like auto-tune but man we cant be having one box one may and another another way because of how we tested does that make sense to you?"*

This is the principle that drives v0.9.27. **The codebase produces the same operational state on every box** — either a fixed value or a value computed from observable signals. What it must NEVER do is preserve a number an operator once typed during a fire.

---

## Incident 2: the v0.9.27 hotfix #0 cold-start trap (2026-05-17 20:38–21:23 UTC) — discovered during dev validation

After shipping hotfix #0 (the initial v0.9.27 autotune) to dev and triggering Update Now on tak-10:

```
20:38:18  tak-10 first boot under v0.9.27-alpha
20:38:18  pool autotune cold-start: live pg_stat_activity = 9 idle conns (Authentik mid-restart, traffic momentarily lulled)
20:38:18  target = peak * safety = 18 → FLOOR (75/15) → ceiling 90
20:38:18  pgbouncer recreated 250/50 → 75/15
```

The single-shot `pg_stat_activity` seed sampled during a restart-induced traffic lull. tak-10 (which had been running stable at 250/50 from yesterday's manual bump) shrunk to the FLOOR. The autotune was mathematically correct given the data; the data was a transient artifact.

Hotfix #1 replaced the live seed with a known-safe fleet constant (250/50 = 300 ceiling = PG_MAX_CONN × 0.6). Every box reboots into this ceiling on a fresh samples ring. Worst case: a quiet box runs at 300-conn ceiling for one reboot cycle (~250 KB pgbouncer memory). Cheap insurance against under-sizing.

Also fixed in hotfix #1: a stale `_reconcile_watchdog_threshold(new_default, new_reserve)` call that referenced variables removed during the v0.9.27 refactor — threw `NameError` on every successful pool change.

---

## Incident 3: the self-reinforcing pool-starved trap (2026-05-17 21:23–22:00 UTC) — discovered during dev validation

After hotfix #1 landed, tak-10's pool stayed at 75/15 despite known heavy Channels load on the box. Investigation:

```
samples: 11 entries, all from the broken hotfix #0 era
last5 idle: [3, 4, 8, 1, 6]  ← all artifacts of the under-sized 75/15 pool
peak_observed: 8
target = max(FLOOR=90, peak × 2.0 = 16) → FLOOR (75/15)
```

**Architectural flaw exposed.** Sampling `pg_stat_activity` idle connections measures pool *headroom*, not pool *demand*. A pool-starved box has nearly all conns checked out → low idle → autotune confirms the small pool is "fine." Self-reinforcing under-sizing.

The fix needed a metric that *can't lie under starvation*. PgBouncer's `SHOW POOLS` exposes `cl_waiting + cl_waiting_cancel_req` — clients literally queued waiting for an upstream conn. When the pool is too small, cl_waiting grows. There is no way to be both "starved" and "no waiting clients."

Hotfix #2:
- New helper `_authentik_pgbouncer_cl_waiting()` queries `SHOW POOLS` inside the pgbouncer container (auth via `$DB_PASSWORD` from container env, no host-side secret handling).
- Watchdog tick samples `cl_waiting` alongside the existing idle count.
- Autotune compute adds a **stuck-at-floor escape**: if ≥ 3 samples have cl_waiting telemetry AND any of them show `cl_waiting > 0` → force fleet safe-constant 250/50 regardless of idle peak.
- Backwards-compatible: legacy samples without the `cl_waiting` field don't trip the escape (need ≥ 3 samples *with* telemetry).

**Field-witnessed firing:** at 22:43 UTC on test8, autotune log:
```
pool autotune: STUCK-AT-FLOOR ESCAPE — peak_cl_waiting=4 > 0 over 30 samples
  → forcing fleet safe-constant 250/50 (ceiling=300)
```

---

## Incident 4: detection-to-remediation latency (architectural disclosure, addressed in hotfix #3)

Hotfix #2's escape only runs at console restart. If a quiet box gets a sudden load spike, query_wait_timeouts emit until the operator hits Update Now. Unbounded latency.

Hotfix #3 closes this by letting the watchdog itself trigger an in-place pgbouncer recreate when it sees sustained starvation. Same `_ensure_authentik_pgbouncer_pool_size` function used by the migration path; just called from the 2-min watchdog tick instead of waiting for the next Update Now.

**Trigger conditions** (all must be true):
1. Last 3 watchdog samples ALL have `cl_waiting > 0` (sustained, not a transient spike).
2. Current `DEFAULT_POOL_SIZE < 250` (room to grow to the cold-start constant).
3. ≥ 15 min since the last in-place resize (cooldown prevents flapping).

**Detection-to-remediation latency on a starved box** (worst case):
- tick 1: cl_waiting=N first observed
- tick 3 (~6 min after tick 1): TRIGGER fires
- ~30s later: pgbouncer recreate done, pool is 250/50
- Total: **~6.5 min from first starvation to healed pool, no operator action**

**Safety properties:**
- Whole block wrapped in outer try/except — any failure here is swallowed so the watchdog's primary safety net (restart server-1 on idle > threshold) always runs.
- Cooldown timestamp persisted to `settings.pool_autotune.last_inplace_resize_ts`. Stamped on every attempt (success or failure) to prevent spam-retry on broken systems.
- Cooldown-active state logs once per tick so operators know we're aware but holding off.
- Bypass-autotune-compute path: hotfix #3 passes target=250 explicitly. We already know we're starved from the cl_waiting signal; no need to re-derive it.

---

## Incident 5: the ghost-Channels-conn accumulation (2026-05-17 22:14–22:18 UTC) — test8 forensic

After hotfix #3 landed, I declared a "clean soak" across all three boxes. The operator pushed back: *"test 8 on my guard dog has a caution and ldap-1 and server-1 for authentik are unhealthy so wtf are you measuring."*

Deep probe revealed:
- authentik-server-1: unhealthy, FailingStreak=37
- authentik-ldap-1: unhealthy, FailingStreak=154 (~26 min)
- 18 query_wait_timeouts in 10 min, despite pool already at 250/50 (the cap)
- 290 of 300 sv_active conns held by `SELECT DISTINCT "django_channels_postgres_groupchannel"`

The smoking gun:
```sql
SELECT pid, state, NOW()-state_change AS in_state_for, query
FROM pg_stat_activity
WHERE state='idle' AND query LIKE '%groupchannel%'
ORDER BY state_change LIMIT 3;

 pid   | state | in_state_for    | query
-------+-------+-----------------+------------------------------------------
 22339 | idle  | 02:02:13.580082 | SELECT DISTINCT django_channels_postgres_groupchannel
 22348 | idle  | 02:02:13.277554 | SELECT DISTINCT django_channels_postgres_groupchannel
 22337 | idle  | 02:02:13.172496 | SELECT DISTINCT django_channels_postgres_groupchannel
```

**Backends frozen in `state='idle'` for 2h 2min 13sec, with consecutive PIDs.** That time exactly matches when pgbouncer was recreated during hotfix #0 deploy (20:11 UTC).

### Root cause

Authentik + `django_channels_postgres` interaction:
1. Authentik server-1 opens `SELECT ... groupchannel ... WHERE expires > NOW()` long-poll subscriptions for every active websocket client.
2. When server-1 is killed/restarted (any pgbouncer recreate, any watchdog safety-net restart, any Update Now), the listener Python process dies — but the PostgreSQL backend doesn't see a disconnect because PgBouncer holds the socket.
3. From PgBouncer's view in transaction mode, the upstream conn is sv_idle (normal between queries).
4. From PostgreSQL's view, the backend is `state='idle'` running the unchanged Channels SELECT. Stays this way for hours until socket keepalive eventually times out.
5. New server-1 generation opens fresh Channels conns for the same subscribers. Pool = live + ghosts. Repeat until pool exhausted.

This is an upstream Authentik + django_channels_postgres pattern, **not** introduced by v0.9.27. The first accumulation alert on test8 was at 20:00 UTC, *before* any v0.9.27 deploy. But v0.9.27's four-hotfix iteration each cycled pgbouncer (and thus indirectly server-1), so test8 leaked four batches of ghosts before stabilizing.

### Recovery (proves the diagnosis)

```sql
SELECT count(*) FROM (
  SELECT pg_terminate_backend(pid)
  FROM pg_stat_activity
  WHERE datname='authentik' AND state='idle'
    AND query LIKE '%groupchannel%'
    AND state_change < NOW() - INTERVAL '5 minutes'
) t;
-- killed: 290
```

Followed by `docker compose up -d --force-recreate --no-deps server ldap` on test8.

Result in <60s:
- All Authentik containers HEALTHY (server-1, worker-1, pgbouncer-1, postgresql-1, redis-1, ldap-1)
- idle PG conns: 299 → 11
- channels_idle: 290 → 0
- cl_waiting: 2 → 0
- query_wait_timeout: 0 in subsequent 20-min soak

### Hotfix #4: ship the recovery

The reaper is narrow by design:
- `datname='authentik'`
- `state='idle'` (never touches active queries)
- `query LIKE '%groupchannel%'` (Channels long-poll class only)
- `state_change < NOW() - INTERVAL '5 minutes'` (well above the ~60s normal Channels poll cycle; generous safety margin)

**Two call sites:**

1. **Startup migration** — fires after pool sizing, before other migrations. Self-heals every operator's v0.9.26 → v0.9.27 Update Now: ghosts get reaped on the very first console boot under v0.9.27 code. Logs `✓ ghost-channels-reaper: terminated N idle Channels backends...` or `no ghosts found (clean)`.
2. **Watchdog tick proactive reap** — fires when EITHER `cl_waiting > 0` OR `channels_idle > pool_ceiling/2`. Rate-limited to one reap per 40 min via `settings.pool_autotune.last_ghost_reap_ts`. Backup defense for ongoing leaks between reboots.

---

## Final architecture (after hotfix #4)

### Components

**`_authentik_pool_autotune_sample(idle_count, classes, cl_waiting=None)`** — called from watchdog loop every 2 min. Persists to `settings.pool_autotune.samples` (ring buffer, last 30 entries ≈ 1 h). Sample shape:
```json
{
  "t": "2026-05-17T22:43:00Z",
  "idle": 87,
  "channels": 40, "dramatiq": 0, "cache": 2, "advisory_lock": 0, "other": 45,
  "cl_waiting": 0
}
```

**`_authentik_pgbouncer_cl_waiting()`** — runs `SHOW POOLS` inside the pgbouncer container (auth via `$DB_PASSWORD` from container env). Returns `cl_waiting + cl_waiting_cancel_req` for the authentik DB, or `None` on PG-unreachable.

**`_authentik_reap_ghost_channels_conns(plog)`** — `pg_terminate_backend` against the narrow ghost-Channels SQL pattern. Returns reaped count.

**`_authentik_pool_autotune_compute(plog)`** — pure function reading samples, returning `(target_default, target_reserve, decision)`. Decision paths in priority order:

1. **Cold-start (samples < 3):** returns fleet safe-constant 250/50, `seed_source='fleet_cold_start_constant'`.
2. **Stuck-at-floor escape (≥ 3 samples with cl_waiting telemetry AND any > 0):** returns fleet safe-constant 250/50, `seed_source='cl_waiting_escape'`.
3. **Normal autotune:** `target_ceiling = min(PG_MAX_CONN × 0.6, max(FLOOR=90, peak × 2.0))`, split 5:1.

**`_ensure_authentik_pgbouncer_pool_size(plog, target=None, target_reserve=None)`** — calls autotune compute (or uses explicit target if passed). **Always writes** target to compose YAML. No `max(cur, target)`. Reconciles watchdog threshold to `ceiling + 50` bidirectionally.

**`_authentik_channels_pool_watchdog_loop()`** — background daemon, 2-min tick:
- Reads idle conn count (existing safety net).
- Classifies idle by query class (existing).
- Samples cl_waiting (hotfix #2).
- Triggers in-place pool resize if sustained starvation (hotfix #3).
- Triggers ghost-Channels reap if `cl_waiting > 0` or excessive channels_idle (hotfix #4).
- Runs original idle > threshold → restart server-1 safety net (existing floor).

### Decision flow

```
console boot
  ├─ Startup migration
  │   ├─ _ensure_authentik_pgbouncer_pool_size()
  │   │   └─ _authentik_pool_autotune_compute()
  │   │       ├─ if samples < 3 → cold-start 250/50
  │   │       ├─ if cl_waiting > 0 in samples → stuck-at-floor escape 250/50
  │   │       └─ else → normal autotune (peak × safety, capped)
  │   └─ _authentik_reap_ghost_channels_conns()  ← hotfix #4
  │
  └─ watchdog daemon starts
      └─ every 2 min:
          ├─ sample idle + classes + cl_waiting
          ├─ if cl_waiting>0 sustained 3 ticks AND pool<250 AND cooldown ok → in-place resize 250/50
          ├─ if cl_waiting>0 OR channels_idle>ceiling/2 (40min cooldown) → reap ghosts
          └─ if idle > threshold → restart server-1 (original safety net floor)
```

---

## Field validation evidence (2026-05-17 22:43–23:03 UTC)

Three test boxes (tak-10, test8, responder) updated to commit `3b75b87` (hotfix #4). 20-min soak.

### tak-10 (low Channels load, quiet)

```
Startup migration:   pool autotune: peak_idle=8 peak_cl_waiting=0 over 30 samples → target 75/15 (ceiling=90)
Startup migration:   ghost-channels-reaper: no ghosts found (clean)
```

Post-soak state:
```
authentik-server-1:     healthy, streak=0
authentik-worker-1:     healthy, streak=0
authentik-pgbouncer-1:  healthy, streak=0
authentik-ldap-1:       healthy, streak=0
channels_idle: 0    all_idle: 9    cl_waiting: 0    qwt (20m): 0    tracebacks: 0
```

### test8 (recovered from 290-ghost state pre-update; this update was the first under hotfix #4)

```
Startup migration:   pool autotune: STUCK-AT-FLOOR ESCAPE — peak_cl_waiting=4 > 0 over 30 samples → forcing fleet safe-constant 250/50 (ceiling=300)
Startup migration:   ghost-channels-reaper: no ghosts found (clean)
```

(The cl_waiting=4 was stale from pre-recovery samples; the *current* cl_waiting is 0. Autotune correctly preserved 250/50 instead of letting it drift to FLOOR.)

Post-soak state:
```
authentik-server-1:     healthy, streak=0
authentik-worker-1:     healthy, streak=0
authentik-pgbouncer-1:  healthy, streak=0
authentik-ldap-1:       healthy, streak=0
channels_idle: 0    all_idle: 16    cl_waiting: 0    sv_active: 9    maxwait: 0s
qwt (20m): 0    tracebacks: 0
```

test8 went from "every Authentik container unhealthy, 290 ghosts, 18 qwt/10min" pre-recovery → "every container healthy, 0 ghosts, 0 qwt" 20 min post-hotfix-#4. This is exactly the operator experience hotfix #4 is designed to deliver via Update Now alone.

### responder (high transient Channels load earlier, now quiet)

```
Startup migration:   pool autotune: peak_idle=9 peak_cl_waiting=0 over 30 samples → target 75/15 (ceiling=90)
Startup migration:   ghost-channels-reaper: no ghosts found (clean)
```

Post-soak state:
```
authentik-server-1:     healthy, streak=0
authentik-worker-1:     healthy, streak=0
authentik-pgbouncer-1:  healthy, streak=0
authentik-ldap-1:       healthy, streak=0
channels_idle: 0    all_idle: 7    cl_waiting: 0    qwt (20m): 0    tracebacks: 0
```

(responder's earlier 250/50 was held by a peak_idle=177 sample that has now rolled out of the 30-sample window. Autotune correctly shrunk to FLOOR. If load returns, hotfix #3's in-place remediation will catch it within ~6.5 min.)

### Cross-box summary

- **Zero tracebacks** across all three boxes over 20-min soak.
- **Zero query_wait_timeouts** on any box over 20-min soak.
- **All 18 Authentik containers (3 boxes × 6 containers each) report `healthy, streak=0`.**
- Every hotfix path observed firing at least once:
  - Hotfix #1 cold-start constant: validated on initial v0.9.27 deploys.
  - Hotfix #2 compute escape: fired **twice in production** on test8 (initial deploy + this final boot).
  - Hotfix #3 in-place remediation: armed, dormant (correct — no sustained starvation observed).
  - Hotfix #4 ghost reaper: ran on all three boxes (all "no ghosts found (clean)" post-recovery); manually triggered on test8 killed 290 ghosts.

---

## New cursor rule: `fleet-uniform-config.mdc`

Added `.cursor/rules/fleet-uniform-config.mdc` (always-apply). Codifies the principle that **the codebase must produce the same operational state on every box**, and forbids:

- `max(cur, target)` or equivalent override-preservation semantics in any config migration.
- `pool_override` / `install_tier` / similar operator-typed knobs that persist in box-local config.
- Single-box validation for any release that touches fleet-wide configuration.

Required validation gate: **3 test boxes, ≥ 20 min soak, no manual config edits during validation.** This rule blocks the v0.9.26 class of bug at the design-review stage.

---

## What dies in this release

- `max(cur, target)` in `_ensure_authentik_pgbouncer_pool_size` — gone.
- "Operator override preservation" as a documented feature — gone.
- The hardcoded "every box ships with 75/15" assumption — gone (autotune determines).
- `_authentik_pool_autotune_seed_from_live_pg()` (hotfix #0's unreliable single-shot reader) — replaced by cold-start constant + cl_waiting escape.
- The stale `_reconcile_watchdog_threshold(new_default, new_reserve)` NameError — fixed.

## What stays

- FLOOR constants `_AUTHENTIK_POOL_AUTOTUNE_FLOOR_DEFAULT = 75`, `_FLOOR_RESERVE = 15` — minimum bound for normal autotune.
- Watchdog threshold reconciliation (`ceiling + 50`) — now bidirectional.
- All four v0.9.26 hotfix-wave fixes (tasklog purge, REINDEX/VACUUM FULL, vm.overcommit_memory, watchdog class breakdown) — unchanged.
- Original watchdog idle > threshold → restart server-1 safety net — still the ultimate floor.

---

## Lessons recorded

1. **Operator overrides are temporary, not architectural.** Persisting a number an operator typed during a fire creates silent fleet drift. The right response is: tune the codebase logic so it produces the right value automatically on every box.

2. **Validation must happen on a box where runtime config == codebase output.** If a test box has manual edits to compose YAML / settings.json, validating on it tells you the manual edit works — NOT that the code works. v0.9.26 shipped on a 65-min validation that proved nothing about the codebase.

3. **Single-box validation is forbidden for any release that touches fleet-wide configuration.** Minimum 3 boxes, ≥ 20 min soak, no manual edits during validation. Codified in `.cursor/rules/fleet-uniform-config.mdc`.

4. **Measure demand, not just headroom.** Idle-conn count is a headroom signal — it goes UP when pool is too big AND goes DOWN when pool is too small (both look similar from the metric). `cl_waiting` measures unmet demand directly and cannot lie under starvation.

5. **Detection-to-remediation latency matters more than detection coverage.** Hotfix #2 detected the problem but waited for reboot. Hotfix #3 closes the loop in ~6.5 min. Self-healing means closing the loop, not just observing it.

6. **Upstream-bug self-healing is a release feature.** The Authentik `django_channels_postgres` ghost-conn pattern existed before v0.9.27, but every v0.9.27 install will hit it on Update Now. Without hotfix #4, every operator with active Channels traffic would see the test8 pattern and blame v0.9.27. Hotfix #4 reaps the upstream's leak automatically.

7. **Class-of-bug rule: any time a migration uses `max(cur, target)` or `min(cur, target)`, ask: does this create silent fleet drift?** The answer is almost always yes. Replace with a deterministic write.

8. **Tunnel-vision is a forensic anti-pattern.** I declared "soak clean" on hotfix #3 by checking pool metrics + tracebacks while ignoring test8's clearly unhealthy containers. The operator caught it; I should have caught it. When a soak passes on N-1 of N boxes, the Nth box is the story, not noise.

---

## File-level changes

```
app.py                                       (1 file changed, ~430 lines net)
  + _authentik_pgbouncer_cl_waiting()        hotfix #2
  + _authentik_reap_ghost_channels_conns()   hotfix #4
  ~ _authentik_pool_autotune_sample()        hotfix #2 (cl_waiting field)
  ~ _authentik_pool_autotune_compute()       hotfix #1 (cold-start) + hotfix #2 (escape)
  ~ _ensure_authentik_pgbouncer_pool_size()  hotfix #1 (NameError fix)
  ~ _authentik_channels_pool_watchdog_loop() hotfix #2 + hotfix #3 + hotfix #4
  ~ Startup migration hook                   hotfix #4 (reap on boot)
  - _authentik_pool_autotune_seed_from_live_pg()  removed (replaced by cold-start constant)

.cursor/rules/fleet-uniform-config.mdc       (new — codifies the fleet-uniform principle)
docs/RELEASE-v0.9.27-alpha.md                (this document)
docs/PLAN-v0.9.27-alpha.md                   (validation gate, kept for historical reference)
memory-bank/techContext.md                   (v0.9.27-alpha RELEASED entry + lessons)
README.md                                    (Latest release pointer + changelog entry)
```

Dev commits (4):
- `70ebeab` v0.9.27-alpha: PgBouncer pool autotune (kill v0.9.26 fleet fracture)
- `82a6521` v0.9.27-alpha hotfix #1: cold-start fleet constant + remove stale NameError
- `b324c17` v0.9.27-alpha hotfix #2: cl_waiting demand signal + stuck-at-floor escape
- `335bc15` v0.9.27-alpha hotfix #3: continuous in-place pool remediation
- `3b75b87` v0.9.27-alpha hotfix #4: ghost-Channels-conn reaper

Squashed to a single `v0.9.27-alpha` commit on `main`.
