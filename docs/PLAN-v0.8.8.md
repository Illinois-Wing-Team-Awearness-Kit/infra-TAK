# v0.8.8-alpha — Work Plan

**Headline features (both stability fixes, no UI work):**

1. **Bug #1 — LDAP flow stage-binding recursion** (`evaluate_on_plan=true` + `re_evaluate_policies=true` cascade). Pinned Postgres at 900-1500% CPU on every box that ever ran our LDAP blueprint; only surfaced on slow disks.
2. **Bug #2 — `idle_in_transaction_session_timeout=30s` is too aggressive.** Caught the v0.8.4 zombie-tx limit at 30s; on slow disks (sub-2k IOPS) Authentik's Django startup migrations exceed that and get killed mid-flight, leaving a stale advisory lock that crash-loops the server forever.

> **Scope discipline note:** v0.8.8 was originally going to be the rollback feature. Rollback is now **parked to v0.9.0 or later** — Authentik stabilization is the sole priority until the fleet is provably stable across slow disks. We just found two fleet-wide bugs surfaced by the same slow-disk ssdnodes box (this release ships both fixes), and the v0.8.x line will continue to absorb stability work for as long as it takes. Rollback ships when stability is no longer a moving target.
>
> **No UI changes in v0.8.8.** Same discipline as v0.8.7. All audit state lives in `~/.takwerx/settings.json` and is operator-readable; defaults are correct for every box.
>
> **Pattern note:** The slow-disk ssdnodes box has now exposed THREE latent fleet bugs in two days (`AUTHENTIK_WEB_WORKERS` silent-ignore in v0.8.7, plus the two above). Slow-disk boxes are accidental QA gold. Treat any "this box is slow" report as a chance to find another race-conditioned bug hiding behind sub-millisecond fsync.

---

## 1. Bug #1 — Apr 30 2026 ssdnodes investigation (LDAP flow recursion)

A buddy's slow-disk SSDNodes VPS (Dell-class, 1795 random-write 4k IOPS — between spinning rust and slow SATA SSD; 31.7 MB/s sequential write via `dd`) had pulled v0.8.7-alpha cleanly: 4 gunicorn workers running, cache + log tunings applied, runtime config verifier passing. Yet:

- `authentik-postgresql-1` CPU: **1297% / 1085% / 782% / 619% / 165% / 766%** (avg ~900% sustained, multiple 1000%+ spikes) on a box with **0.36 LDAP binds/sec** (essentially idle workload)
- `authentik-server-1` CPU: 200-350% sustained
- 119 idle-in-transaction Postgres connections (oldest 2s — churning, not stuck)
- `pg_stat_activity` showed **5 backends running the same query for 86 seconds each**:

  ```
  SELECT "authentik_policies_policybindingmodel"."pbm_uuid", ...
  ```

- `django_postgres_cache_cacheentry`: 92% dead tuples even immediately after autovacuum (cache-table churn faster than autovacuum can clean)

### The bug

Every stage binding on `ldap-authentication-flow` had **both** `evaluate_on_plan=true` AND `re_evaluate_policies=true`:

```
ldap-authentication-flow | order=10 | evaluate_on_plan=t | re_evaluate_policies=t
ldap-authentication-flow | order=15 | evaluate_on_plan=t | re_evaluate_policies=t
ldap-authentication-flow | order=20 | evaluate_on_plan=t | re_evaluate_policies=t
```

That combo causes a **cascading policy re-evaluation** on every step of every authentication plan: each step re-runs all policy lookups, which re-triggers plan generation, which re-evaluates policies, ad infinitum until the disk catches up. With Authentik 2025.10+ using Postgres for cache + channels + tasks (no Redis), and our `policybindingmodel` table having only the PK as an index, every cascading policy lookup was a full sequential scan.

Compare to `default-authentication-flow` which has `evaluate_on_plan=false, re_evaluate_policies=true` — that flow has zero recursion and works fine on every box.

### The proof

```bash
$ psql -c "UPDATE authentik_flows_flowstagebinding
           SET evaluate_on_plan = false
           WHERE target_id IN (SELECT flow_uuid FROM authentik_flows_flow
                               WHERE slug = 'ldap-authentication-flow');"
UPDATE 3

$ docker restart authentik-server-1   # clears in-memory flow cache

# 60 seconds later:
docker stats:
authentik-postgresql-1   23% / 0.7% / 0.6% / 0.8% / 0.03% / 32% / 0.3%   (avg ~7.8%)
authentik-server-1       133% / 256% / 145% / 97% / 191%                  (normal)

pg_stat_activity:
0 long-running queries
```

**~115x reduction in Postgres CPU.** Same workload. LDAP outpost untouched.

### Why it didn't surface earlier

This bug has been latent on **every box that ever ran our LDAP blueprint** — that's every infra-TAK install since the LDAP feature shipped. Fast-disk boxes hide it because each cascading policy query completes in microseconds. Slow-disk boxes (Alex's Dell R3930 with spinning rust, ssdnodes with VPS storage throttling) explode under it. tak-10/responder/ssdnodes-validated/Alex's box all have this bug right now — it'll get auto-fixed on their next update.

### Source of the bug

Three places in `app.py` were creating these bindings with `evaluate_on_plan=True`:

1. **First blueprint YAML copy** (~line 20384, 20396, 20408)
2. **Second blueprint YAML copy** (~line 22278, 22290, 22302)
3. **`_ensure_ldap_flow_authentication_none()` healing function** (~line 24548)

All three now ship `False`. The default-authentication-flow code path (line 24604-24605, which copies attributes from the existing default flow) was already correct — the default flow has the right values.

---

## 1B. Bug #2 — Apr 30 2026 ssdnodes follow-up (`idle_in_transaction_session_timeout=30s`)

After bug #1 was fixed (Postgres CPU back to ~7.8% from ~900%), the ssdnodes box still couldn't bring Authentik back up. Caddy returned `502 Bad Gateway` then `503 Service Unavailable` for `/api/v3/outposts/instances/`. LDAP outpost stayed `unhealthy` with `FailingStreak: 51+`.

### The trace

`docker logs authentik-server-1` showed a continuous crash loop:

```
psycopg.errors.IdleInTransactionSessionTimeout: terminating connection due to idle-in-transaction timeout
{"error":"exit status 1","event":"gunicorn process died, restarting"}
{"error":"exit status 1","event":"gunicorn failed to start, restarting"}
2026-05-01 00:31:09 [info     ] waiting to acquire database lock
```

`docker logs caddy` confirmed the cascade:

```
dial tcp 127.0.0.1:9090: connect: connection refused → 502 → outpost retry → 503
```

### Root cause

In v0.8.4 we set Postgres `idle_in_transaction_session_timeout=30s` to bound zombie idle-in-tx sessions left behind by misbehaving Authentik connections. Hardcoded in 4 places in `app.py`:

- Line 20500 — fresh-install compose template
- Line 20946 — `_ensure_authentik_compose_patches` migration template
- Line 22555 — second copy of the same template (legacy code path)
- Line 22577 — log message

On NVMe / fast SATA SSD, Authentik's Django startup migrations complete in seconds and never sit idle-in-tx. **On 1795-IOPS storage**, fsync waits push some migration steps into idle-in-tx state for >30s. Postgres kills the migration mid-flight. The killed session leaves a stale Postgres advisory lock (`SELECT pg_try_advisory_lock(...)` from `/lifecycle/migrate.py`). Next server boot calls `release_lock` on a connection that never owned the lock, fails, and the new migration call to `acquire` blocks forever waiting on the dead session's lock.

The server then crash-loops indefinitely:
1. Boot → Django migrations start → idle-in-tx → killed at 30s
2. Restart → tries to acquire migration lock → previous session's stale lock blocks → `waiting to acquire database lock` forever
3. Gunicorn never reaches `bind()` on :9090
4. Caddy upstream is dead → 503 to outpost
5. Loop forever

### The fix

**Bump the timeout to 300s** (5 minutes). Justification:

| Value | Effect |
|---|---|
| `30s` (old) | Slow-disk migrations get killed → crash loop. NVMe-only. |
| `300s` (new) | 10x headroom for slow-disk migrations. Still bounds zombie sessions to 5 min — well under the 1-hour idle-session bound that's the upstream Postgres-default-equivalent. |
| Disabled (`0`) | No bound on zombie sessions — what we had pre-v0.8.4. Bad. |

300s is also within Authentik community norms (`/r/Authentik` and `goauthentik/authentik` issues commonly recommend 60-300s for production).

### Source of the bug

Same `app.py` paths as bug #1 — 4 hardcoded `30s` literals (compose template + regex sentinel + log message × 2 copies). All 4 now ship `300s`. The regex sentinel becomes `m.group(1) != '300'` so `_ensure_authentik_compose_patches` will detect any v0.8.4-vintage box on its next call and update it.

### Why we didn't catch this in v0.8.4

The v0.8.4 PR added the timeout as part of a general "tighten zombie sessions" tuning. Tested only on fast-disk dev boxes. Slow-disk boxes never hit 30s during normal operation, only during the rare full-startup-migration replay. Update Now triggers exactly that replay. So the bug was a true Heisenbug — only fired on the intersection of (slow disk) AND (full server restart with migration replay), and only manifested after several minutes of crash loop, by which point the operator had usually moved on.

---

## 2. What v0.8.8 ships

### Changes to `app.py` — Bug #1 (LDAP flow recursion)

#### a) Blueprint YAML — flip 6 occurrences of `evaluate_on_plan: true` → `false`

Three bindings × two blueprint copies = six edits. `re_evaluate_policies: true` is preserved (matches default flow, not part of the recursion combo).

#### b) `_ensure_ldap_flow_authentication_none()` — line 24548

```python
'evaluate_on_plan': True, 're_evaluate_policies': True,
```
becomes:
```python
'evaluate_on_plan': False, 're_evaluate_policies': True,
```

This function is called by both initial deploy and the post-update healing path. Without this fix, the healing path would re-introduce the bug after we fixed it.

#### c) New idempotent self-healing migration: `_authentik_fix_ldap_flow_recursion(plog)`

Lives next to `_authentik_apply_official_tunings` and `_authentik_verify_runtime_config`. Runs in both `_startup_migrations` (every console start) and `_post_update_auto_deploy` (after every update).

Behavior:

1. Probe: `docker ps -q --filter name=authentik-postgresql-1`. If not running, skip with `ldap flow recursion: authentik-postgresql-1 not running — skipping`.
2. Count: `SELECT COUNT(*) FROM authentik_flows_flowstagebinding fsb JOIN authentik_flows_flow f ON f.flow_uuid = fsb.target_id WHERE f.slug='ldap-authentication-flow' AND fsb.evaluate_on_plan = true;`
3. If `count == 0`: persist `last_outcome='idempotent-noop'`, return False (every startup after the first on already-fixed boxes).
4. If `count > 0`: idempotent UPDATE setting them to false; persist `last_outcome='fixed'` + `last_bad_count=N`; **restart `authentik-server-1` only** (cardinal rule: ldap outpost untouched, no thundering herd) so the in-memory flow plan cache is rebuilt.
5. All outcomes recorded to `settings.authentik_ldap_flow_recursion_fix` for operator audit.

**Idempotent.** On a v0.8.8-clean box, every startup is a single COUNT query (~10ms) plus a settings write. The actual UPDATE + restart only fires on first startup after the upgrade lands.

### Changes to `app.py` — Bug #2 (`idle_in_transaction_session_timeout`)

#### d) Compose template + regex sentinel — flip 4 occurrences of `30s` → `300s`

Two compose-template strings, two regex-comparison literals, plus log messages. Lines 20500, 20946, 20953, 21040, 22558, 22562, 22580 of `app.py`. The regex change (`!= '30'` → `!= '300'`) ensures `_ensure_authentik_compose_patches` detects any v0.8.4-era box on its next call and rewrites the compose line.

#### e) New idempotent self-healing migration: `_authentik_fix_pg_idle_timeout(plog)`

Lives next to `_authentik_fix_ldap_flow_recursion`. Runs in both `_startup_migrations` and `_post_update_auto_deploy`, **before** the recursion fix (ordering is critical — see below).

Behavior:

1. Reads `~/authentik/docker-compose.yml`.
2. Grep for `idle_in_transaction_session_timeout=Ns`. If absent, skip (compose isn't patched yet — first install path).
3. If `N == 300`: persist `last_outcome='idempotent-noop'`, return False.
4. If `N != 300` (i.e. 30 from v0.8.4): call `_ensure_authentik_compose_patches` (which now writes 300s) → force-recreate Postgres container.
5. Force-recreate kills ALL Postgres sessions, which clears any stale advisory lock left by a previous crash loop.
6. Wait up to 60s for Postgres `pg_isready`.
7. Restart `authentik-server-1` and `authentik-worker-1` (cardinal rule: LDAP outpost untouched). On a healthy box this is a clean restart; on a stuck box this unsticks the crash loop.
8. All outcomes recorded to `settings.authentik_pg_idle_timeout_fix`.

**Why before the recursion fix:** the recursion fix restarts `authentik-server-1` to clear the in-memory flow plan cache. On a v0.8.7-vintage box with `30s` still in compose, that server restart would trigger Django startup migrations that hit the 30s timeout and crash-loop the box just from running our healing migration. By bumping the timeout *first*, the subsequent server restart from the recursion fix has 10x the headroom and completes cleanly.

#### f) Verifier extension: `_authentik_verify_runtime_config` adds a Postgres probe

Existing verifier already checks `cache.timeout_*`, `log_level`, `web.workers (process count)`. v0.8.8 adds:

```sql
SHOW idle_in_transaction_session_timeout;
```

Normalizes the result (`300s` / `5min` / `300000ms` are all equivalent) and asserts 300_000 ms. Surfaces in the same `settings.authentik_runtime_config_check.last_results` audit and the same pass/fail summary log line.

### Documentation

- **`docs/PLAN-v0.8.8.md`** — this file.
- **`docs/RELEASE-v0.8.8-alpha.md`** — operator-facing release notes with field evidence.
- **`docs/HANDOFF-LDAP-AUTHENTIK.md`** — adds a "v0.8.8 — flow recursion fix" section.
- **`README.md`** — bumps "Latest release" headline + adds changelog entry.

---

## 3. What v0.8.8 explicitly does NOT ship

- ❌ **UI changes.** Same as v0.8.7.
- ❌ **The rollback feature.** Parked to **v0.9.0+**. v0.8.x line continues to absorb Authentik stability work until the fleet is provably stable across slow disks.
- ❌ **A flow-recursion check inside `_authentik_verify_runtime_config`.** Kept separation of concerns: the runtime verifier is for *runtime config* (what `ak dump_config`, `docker top`, and `SHOW` see). The flow recursion fix is *DB state* (what `psql SELECT` sees). They live in parallel keys (`authentik_runtime_config_check` vs `authentik_ldap_flow_recursion_fix`) and operators audit each independently. **Note:** the `idle_in_transaction_session_timeout` check WAS added to the verifier — that one's runtime config (Postgres `SHOW`), so it fits naturally.
- ❌ **Index on `policybindingmodel.target_id`.** Tempting on slow-disk boxes (would help worst-case sequential scans), but Authentik manages its own schema. We don't fork it. Removing the recursion is the correct fix; the index would be treating a symptom.
- ❌ **Aggressive autovacuum on cache table.** Same reason — once recursion stops, cache churn drops to normal levels and default autovacuum is fine.
- ❌ **Configurable `idle_in_transaction_session_timeout` via env var.** 300s is correct for every box we know about. Adding an env var creates another knob operators can footgun. Revisit only if a box reports startup migration >300s (which would be a different problem — that's catastrophically slow disk).

---

## 4. Cardinal rules upheld

- **Server-only restart.** `docker restart authentik-server-1`. Never `--no-deps server worker` (we don't need the worker recreate; only server holds the in-memory flow plan cache). LDAP outpost (`authentik-ldap-1`) stays up the whole time. `authentik-postgresql-1` and `authentik-worker-1` stay up.
- **Idempotent.** Running the migration twice is safe and cheap.
- **Self-gating.** No-op on already-fixed boxes — the count query short-circuits the UPDATE and restart.
- **Audit trail in `settings.json`.** Operators read `last_outcome` + `last_bad_count` to know what happened.
- **Documented in upstream-style.** `consult-upstream-docs` Cursor rule applied during this investigation: we read [Authentik flows docs](https://docs.goauthentik.io/docs/flow/) on `re_evaluate_policies` and `evaluate_on_plan` semantics before deciding which flag to flip.

---

## 5. Validation plan

### a) ssdnodes (the slow-disk box that surfaced both bugs)

**Bug #1 (recursion):** already manually fixed via SQL during the live session. Postgres CPU dropped 115x. After v0.8.8 lands, the recursion migration will be idempotent-noop on first run. Validates the no-op path.

**Bug #2 (idle timeout):** the box was crash-looping when v0.8.8 work began. We rescued it in-place by sed-ing `30s → 600s` in compose and bringing services back up. After v0.8.8 lands, `_authentik_fix_pg_idle_timeout` will detect the 600s value, see it's not the canonical 300s, and rewrite/recreate Postgres to normalize. Validates the rewrite path on a non-default-but-not-broken value.

### b) tak-10 / responder / ssdnodes-validated / Alex's R3930

These boxes have v0.8.7-alpha. Both bugs are present — latent on fast disks, dormant. After they pull v0.8.8 (Update Now or `git pull main`):

**Bug #2 fix runs first** (ordering is critical):
1. Detect `idle_in_transaction_session_timeout=30s` in compose.
2. Rewrite to `300s`.
3. Force-recreate Postgres (~10-15s blip, kills sessions).
4. Restart server + worker (~10-30s).
5. Persist `last_outcome='fixed'`, `last_previous_value='30s'`.

**Bug #1 fix runs second:**
1. Detect 3 bindings with `evaluate_on_plan=true`.
2. UPDATE them.
3. Restart `authentik-server-1` (~5-10s).
4. Persist `last_outcome='fixed'`, `last_bad_count=3`.
5. Their next CPU samples should show a noticeable drop (less dramatic than ssdnodes since their disks aren't as starved, but measurable).

Total user-visible blip: ~30-60s of Authentik unavailability during the migrations. LDAP outpost stays up the whole time per cardinal rule.

### c) New deploys

After v0.8.8 ships, fresh installs run the corrected blueprint YAML on first import + the corrected compose template on first deploy. Both migrations are no-ops forever after.

### d) Operator acceptance gates

**Bug #1:**
```bash
sudo -u takwerx cat /home/takwerx/infra-TAK/.config/settings.json | python3 -c \
  "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_ldap_flow_recursion_fix', {}), indent=2))"
```

Expected after first console restart on an upgraded box:
```json
{ "last_check_utc": "2026-04-30T...", "last_outcome": "fixed", "last_bad_count": 3 }
```
Subsequent restarts:
```json
{ "last_check_utc": "2026-04-30T...", "last_outcome": "idempotent-noop", "last_bad_count": 0 }
```

**Bug #2:**
```bash
sudo -u takwerx cat /home/takwerx/infra-TAK/.config/settings.json | python3 -c \
  "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_pg_idle_timeout_fix', {}), indent=2))"
```

Expected after first console restart on an upgraded box:
```json
{ "last_check_utc": "2026-04-30T...", "last_outcome": "fixed", "last_previous_value": "30s", "last_new_value": "300s" }
```
Subsequent restarts:
```json
{ "last_check_utc": "2026-04-30T...", "last_outcome": "idempotent-noop", "last_value": "300s" }
```

**Combined verifier output:**
```
authentik config verify: all checks passed (workers=4, cache=600s, log_level=warning, pg_idle_timeout=300s)
```

---

## 6. Release flow

1. Commit to `dev` (this PR).
2. Pull `dev` onto tak-10 + responder for validation soak (operator request: validate before merging to main).
3. Once green, selective merge to `main` (just like v0.8.7 — see `docs/COMMANDS.md`).
4. Tag `v0.8.8-alpha`, push tag.
5. ssdnodes-validated, Alex's box, and any other operator boxes pull main / hit Update Now to get the fix.
