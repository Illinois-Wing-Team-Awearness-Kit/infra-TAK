# v0.8.8-alpha Release Notes

## Headline: TWO fleet-wide latent bugs surfaced and fixed

Both bugs were caught on the same Apr 30 2026 slow-disk SSDNodes investigation. Both have been latent on every infra-TAK install for several releases. Fast disks hide them — slow disks (sub-2k IOPS) explode under them.

1. **Bug #1 — LDAP flow stage-binding recursion** (`evaluate_on_plan=true` + `re_evaluate_policies=true` cascade). Pinned Postgres at 900-1500% CPU on the slow box; ~115x reduction in 60s after fix. Latent since the LDAP feature shipped.
2. **Bug #2 — `idle_in_transaction_session_timeout=30s` was too aggressive.** Set in v0.8.4 to bound zombie idle-in-tx sessions. On 1795-IOPS storage, Authentik's Django startup migrations exceed 30s in idle-in-tx state and get killed mid-flight. Stale advisory lock then crash-loops the server forever (`waiting to acquire database lock`). Latent in v0.8.4 — v0.8.7. Bumped to 300s.

> **Bug #1 — recursion.** Every infra-TAK install since the LDAP feature shipped has had `evaluate_on_plan=true` AND `re_evaluate_policies=true` on all three `ldap-authentication-flow` stage bindings. That combo causes a cascading policy re-evaluation on every step of every authentication plan. On a buddy's slow-disk SSDNodes box (1795 random-write 4k IOPS, 31.7 MB/s sequential write — between spinning rust and slow SATA SSD), it surfaced spectacularly: **Postgres CPU pinned at 900-1500% sustained**, with five backends running 86-second `policybindingmodel` queries on a box doing 0.36 LDAP binds per second. Setting `evaluate_on_plan=false` (matching how `default-authentication-flow` is configured) dropped Postgres CPU **~115x in 60 seconds** with the LDAP outpost untouched.
>
> **Bug #2 — idle timeout.** After bug #1 was fixed, the same box still couldn't bring Authentik back up. Caddy returned `502 Bad Gateway` then `503 Service Unavailable`. Server logs showed `psycopg.errors.IdleInTransactionSessionTimeout: terminating connection due to idle-in-transaction timeout` followed by an unbreakable `waiting to acquire database lock` loop. Root cause: our v0.8.4 hardcoded `idle_in_transaction_session_timeout=30s` is below the floor for slow-disk migrations. Bumped to `300s` (10x headroom, still bounds zombie sessions to 5 min).

The investigation started from a buddy's box that "just won't stop screaming after v0.8.7." Initial diagnostics ruled out CPU steal time (0%) and worker count (correct, 4). `fio` exposed the slow disk (1795 IOPS), but the smoking gun for bug #1 came from `pg_stat_activity`: five backends sitting on the same `SELECT FROM authentik_policies_policybindingmodel` query for 86 seconds each. That pattern is not slow-disk noise — that's a recursive query loop. A `grep` of `app.py` found we ourselves had been shipping `evaluate_on_plan: true` AND `re_evaluate_policies: true` in the LDAP blueprint since day one. The fix was a one-row UPDATE per binding.

Bug #2 surfaced an hour later when the same box, despite the recursion fix and idle Postgres CPU, still wouldn't serve traffic. Caddy logs (`dial tcp 127.0.0.1:9090: connect: connection refused`) pointed to a dead upstream. Server logs showed the gunicorn crash loop. Five minutes of `pg_stat_activity` archaeology showed each migration attempt being killed at exactly the 30-second mark. A `grep idle_in_transaction_session_timeout app.py` revealed our own v0.8.4 hardcode.

**Result on the slow-disk box, ~60 seconds after bug #1 fix (`evaluate_on_plan=false` + server restart):**

| Metric | Before fix | After fix |
|---|---|---|
| `authentik-postgresql-1` CPU avg | ~900% sustained, multiple 1000%+ spikes | ~7.8% (idle) |
| Long-running queries (>5s) | 5 backends × 86s on `policybindingmodel` | 0 |
| `authentik-server-1` CPU | 200-350% | 100-250% (normal traffic handling) |

LDAP outpost (`authentik-ldap-1`) `StartedAt` unchanged. Cardinal rule held.

**Result on the same box, ~3 minutes after bug #2 rescue (`30s → 600s` + Postgres recreate + server restart):**

| Symptom | Before fix | After fix |
|---|---|---|
| Server crash loop | gunicorn dying every ~10s on `IdleInTransactionSessionTimeout` | gunicorn stable, "Booting worker with pid:" |
| Caddy upstream | `connection refused` to :9090 → 502/503 | upstream healthy, 200 OK on `/health/ready` |
| LDAP outpost `Health.Status` | `unhealthy` (FailingStreak: 51+) | `healthy` |
| `psql -c "SHOW idle_in_transaction_session_timeout"` | `30s` | `600s` (will normalize to canonical `300s` once v0.8.8 migration runs) |

---

## Bug #1, explained — the LDAP flow recursion

Each Authentik flow has stage bindings. Each binding has two flags that control when policies are evaluated:

- **`evaluate_on_plan`** — when `true`, policies attached to this binding are evaluated *during plan generation* (before the user even sees a step). When `false`, they're only evaluated lazily as the user reaches each step.
- **`re_evaluate_policies`** — when `true`, policies are re-evaluated *each time* the flow plan is replayed/inspected.

When **both** are `true` on every binding in a flow, replays re-trigger plan generation, which re-evaluates policies, which re-triggers replays. With Authentik 2025.10+ using Postgres for cache + channels + tasks (no Redis), every step of that loop is a Postgres query on `authentik_policies_policybindingmodel`. The table has only the primary key as an index, so each lookup is a sequential scan. On fast disks the loop completes per-bind in well under a millisecond — invisible. On slow disks it explodes.

`default-authentication-flow` (which works fine on every box) has `evaluate_on_plan=false, re_evaluate_policies=true`. That's the combo we now ship for `ldap-authentication-flow`.

---

## Bug #2, explained — the 30s idle-in-tx timeout

Postgres has a setting `idle_in_transaction_session_timeout` that kills any session sitting idle (no active query) inside a transaction. It's a defense against zombie connections that hold transaction locks indefinitely.

In v0.8.4 we added `-c idle_in_transaction_session_timeout=30s` to the Authentik Postgres command line. The intent was to bound a misbehavior we'd seen where Authentik connection pool sessions occasionally got stuck idle-in-tx and held row locks. 30s seemed conservative.

It wasn't, on slow disks. Authentik's Django startup migrations run inside transactions. On NVMe each migration step completes in a few ms — never idle. On 1795-IOPS storage, individual fsync waits push the connection into "idle in transaction" state for tens of seconds. Postgres kills the connection at the 30s mark. The killed connection leaves a stale Postgres advisory lock (acquired by `/lifecycle/migrate.py`'s `pg_try_advisory_lock` call). The next server boot calls `release_lock` on a connection that never owned the lock, fails silently, then calls `acquire` which blocks forever on the dead session's stale lock. Result: gunicorn never reaches `bind()` on :9090, Caddy returns 503 to the LDAP outpost, the box appears dead.

**Why 300s, not "disabled":** zombie idle-in-tx sessions are a real failure mode (we've seen it). 300s tightly bounds them while giving slow disks ~10x headroom. Authentik community consensus and Postgres-stable practice both center on 60-300s for production.

**Why we didn't catch it in v0.8.4:** the v0.8.4 PR was tested only on fast-disk dev boxes. The bug requires the intersection of (slow disk) AND (full server restart with migration replay), and only manifests after several minutes of crash loop — by which point the operator has usually moved on.

---

## Changes

### Bug #1 — LDAP flow recursion

#### 1. Blueprint YAML — flip `evaluate_on_plan: true` → `false` on all 3 LDAP flow stage bindings

Two YAML copies in `app.py` (initial deploy + healing reimport), three bindings each = six edits. `re_evaluate_policies: true` is preserved (matches default flow, not part of the recursion combo).

```yaml
# was:
evaluate_on_plan: true
re_evaluate_policies: true
# is now:
evaluate_on_plan: false
re_evaluate_policies: true
```

#### 2. `_ensure_ldap_flow_authentication_none()` healing path

```python
# was:
'evaluate_on_plan': True, 're_evaluate_policies': True,
# is now:
'evaluate_on_plan': False, 're_evaluate_policies': True,
```

This function is called both during initial deploy and as a post-update healing pass. Without this fix, the heal would re-introduce the bug on already-fixed boxes.

#### 3. New idempotent self-healing migration: `_authentik_fix_ldap_flow_recursion(plog)`

Lives next to `_authentik_apply_official_tunings` and `_authentik_verify_runtime_config` in `app.py`. Hooked into both `_startup_migrations` (every console start) and `_post_update_auto_deploy` (after every update).

What it does:

1. Probes for `authentik-postgresql-1`. If not running, skip.
2. Counts bindings on `ldap-authentication-flow` with `evaluate_on_plan=true`.
3. If `count == 0`: persist `last_outcome='idempotent-noop'`, return False. (Every startup after the first on already-fixed boxes — costs a single COUNT query + one settings.json write.)
4. If `count > 0`: idempotent UPDATE setting them to false; **restart `authentik-server-1` only** (server alone — no `--no-deps server worker`, no docker compose recreate, no LDAP outpost touch); persist `last_outcome='fixed'` + `last_bad_count=N`.

Persists outcome to `settings.authentik_ldap_flow_recursion_fix` for operator audit. Like every other v0.8.7+ self-healing migration, this is fully idempotent and self-gating.

### Bug #2 — `idle_in_transaction_session_timeout`

#### 4. Compose template + sentinel — flip 4 occurrences of `30s` → `300s`

`app.py` had 4 hardcoded literals: the fresh-install compose template, the in-place compose patcher, the regex value used to detect "compose needs update", and the log message. All four now read `300s` instead of `30s`. The regex sentinel change (`!= '30'` → `!= '300'`) ensures `_ensure_authentik_compose_patches` will detect any v0.8.4-era box on its next call and rewrite the compose line.

#### 5. New idempotent self-healing migration: `_authentik_fix_pg_idle_timeout(plog)`

Lives next to `_authentik_fix_ldap_flow_recursion`. Hooked into both `_startup_migrations` and `_post_update_auto_deploy`, **before** the recursion fix (ordering matters — see below).

What it does:

1. Reads `~/authentik/docker-compose.yml`. If absent, skip (Authentik not installed).
2. Greps for `idle_in_transaction_session_timeout=Ns`. If absent, skip (compose not yet patched — first install path).
3. If `N == 300`: persist `last_outcome='idempotent-noop'`, return False.
4. If `N != 300` (i.e. `30` from v0.8.4 or `600` from a manual rescue sed): rewrite compose to `300s` via `_ensure_authentik_compose_patches`; force-recreate the Postgres container (`docker compose up -d --force-recreate postgresql`).
5. The force-recreate kills ALL Postgres sessions, which clears any stale advisory lock left by a previous crash loop (this is what unsticks already-crashed boxes).
6. Wait up to 60s for `pg_isready`.
7. Restart `authentik-server-1` and `authentik-worker-1` to clear any crash-loop state with the new timeout in effect. **LDAP outpost (`authentik-ldap-1`) is NOT touched** — cardinal rule preserved.
8. Persists outcome to `settings.authentik_pg_idle_timeout_fix` for operator audit.

**Why before the recursion fix:** the recursion fix restarts `authentik-server-1` to clear the in-memory flow plan cache. On a v0.8.7-vintage box with `30s` still in compose, that server restart would trigger Django startup migrations that hit the 30s timeout and crash-loop the box just from running our healing migration. By bumping the timeout *first*, the subsequent server restart from the recursion fix has 10x the headroom.

#### 6. Verifier extension: `_authentik_verify_runtime_config` adds a Postgres probe

The existing v0.8.7 verifier already checks `cache.timeout_*`, `log_level`, and `web.workers`. v0.8.8 adds:

```sql
SHOW idle_in_transaction_session_timeout;
```

Result is normalized to milliseconds (`300s` / `5min` / `300000ms` are all equivalent) and asserted against 300_000. Surfaces in the same `settings.authentik_runtime_config_check.last_results` audit and the same pass/fail summary log line:

```
authentik config verify: all checks passed (workers=4, cache=600s, log_level=warning, pg_idle_timeout=300s)
```

---

## What was explicitly NOT shipped

- ❌ **Console UI / button.** Same scope discipline as v0.8.7.
- ❌ **The rollback feature** (originally planned for v0.8.8). Parked to **v0.9.0 or later**. Authentik stabilization is the only priority until the fleet is provably stable across slow disks; rollback is a feature, not a fix, and ships when stability is no longer a moving target.
- ❌ **A flow-recursion check inside `_authentik_verify_runtime_config`.** Kept separation of concerns: runtime verifier checks `ak dump_config` + `docker top` + `psql SHOW` (all runtime config). The flow recursion fix audits its own DB-state outcome to a parallel settings key. The `idle_in_transaction_session_timeout` check WAS added to the verifier — that's runtime config, fits naturally.
- ❌ **Custom index on `policybindingmodel.target_id`** to mitigate the seq scan that amplifies the recursion. Tempting on slow disks, but Authentik manages its own schema. Removing recursion is the correct fix; the index would be treating a symptom.
- ❌ **Aggressive autovacuum tuning on the cache table.** Once recursion stops, cache churn drops to normal levels and default autovacuum is fine. Manual VACUUM during the live debugging session was a red herring.
- ❌ **Configurable `idle_in_transaction_session_timeout` via env var.** 300s is correct for every box we know about. Adding an env var is another knob operators can footgun. Revisit only if a box reports startup migration >300s — that's catastrophically slow disk and a different problem.

---

## Cursor rule applied during this investigation

`.cursor/rules/consult-upstream-docs.mdc` (shipped in v0.8.7) was followed: before deciding to flip `evaluate_on_plan` rather than `re_evaluate_policies`, the [Authentik flow docs](https://docs.goauthentik.io/docs/flow/) section on stage binding flags was read. The docs confirm `evaluate_on_plan` is the upfront-evaluation flag and is the safer one to disable when policy lookups become expensive.

---

## Field validation

### a) The slow-disk SSDNodes box (where the bug surfaced)

Manually fixed via SQL during the live debugging session before this code shipped:

```sql
UPDATE authentik_flows_flowstagebinding SET evaluate_on_plan = false
WHERE target_id IN (SELECT flow_uuid FROM authentik_flows_flow
                    WHERE slug = 'ldap-authentication-flow');
-- UPDATE 3
```

Then `docker restart authentik-server-1`. CPU sample:

| Metric | Before | After (60s soak) |
|---|---|---|
| `authentik-postgresql-1` CPU samples | 1297%, 1085%, 782%, 619%, 165%, 766% | 23%, 0.7%, 0.6%, 0.8%, 0.03%, 32%, 0.3%, 4.4% |
| Postgres CPU avg | ~900% | **~7.8%** (~115x reduction) |
| Long-running policy queries (>5s) | 5 backends × 86s | **0** |
| `authentik-server-1` CPU | 200-350% sustained | 100-250% (normal traffic) |
| `authentik-ldap-1` `StartedAt` | (preserved) | (preserved) — server-only restart honored cardinal rule |

Once v0.8.8 lands on this box, the recursion migration will be `idempotent-noop` (DB state already correct), validating the no-op path of the migration. The pg-idle-timeout migration will detect the rescue's manual `600s` value, normalize it to canonical `300s`, and force-recreate Postgres — validating the rewrite path on a non-default-but-not-broken value.

### b) Other fleet boxes (tak-10, responder, ssdnodes, Alex's R3930)

Currently all have v0.8.7-alpha. **Both bugs are present** — recursion is latent (fast disk masks it), idle timeout is dormant (only fires on full migration replay). After they pull v0.8.8 (either via Update Now or `git pull main`), migrations fire in order:

**Bug #2 fix runs first (ordering critical):**
1. Detects `idle_in_transaction_session_timeout=30s` in compose.
2. Rewrites compose to `300s`.
3. Force-recreates Postgres (~10-15s blip — kills sessions, clears any stale advisory locks).
4. Restarts server + worker (~10-30s on slow disks).
5. Audit: `last_outcome='fixed'`, `last_previous_value='30s'`, `last_new_value='300s'`.

**Bug #1 fix runs second:**
1. Count returns 3.
2. UPDATE flips them.
3. `docker restart authentik-server-1` (~5-10s blip, server only) — and now with 300s timeout, this restart's startup migrations cannot get killed mid-flight.
4. Audit: `last_outcome='fixed'`, `last_bad_count=3`.
5. Their next CPU samples should show a noticeable drop — less dramatic than ssdnodes (faster disks were hiding more) but measurable.

Total user-visible blip: ~30-60s of Authentik unavailability during the migrations. LDAP outpost stays up the whole time per cardinal rule.

### c) Stuck-box rescue procedure (for any box already crash-looping with `IdleInTransactionSessionTimeout`)

If an operator has a box that's already in the bug #2 crash loop and v0.8.8 hasn't been pulled yet, manual rescue gets it back online. v0.8.8's migration will then normalize the timeout to `300s` on first console restart.

```bash
cd ~/authentik

# 1. Stop the crash-looping containers
docker compose stop server worker

# 2. Bump the timeout to give slow-disk migrations enough headroom
sed -i 's/idle_in_transaction_session_timeout=30s/idle_in_transaction_session_timeout=600s/g' docker-compose.yml

# 3. Bring everything back up. Postgres recreate kills any stale advisory locks
#    left by the previous crash loop. New server boot has 600s of headroom and
#    completes the Django startup migration cleanly.
docker compose up -d

# 4. Wait for the migration to finish (1-3 min on slow disks) and confirm health
sleep 30
docker compose exec -T postgresql psql -U authentik -d authentik -c \
  "SHOW idle_in_transaction_session_timeout;"   # should show 600s
docker logs --tail 30 authentik-server-1         # should NOT show IdleInTransactionSessionTimeout
docker logs authentik-server-1 2>&1 | grep -c "Booting worker with pid"  # should be > 0
```

Once v0.8.8 ships and the operator pulls it, `_authentik_fix_pg_idle_timeout` will detect the `600s` value, see it's not the canonical `300s`, and rewrite it.

---

## Operator acceptance checklist

After Update Now or `git pull origin dev` + console restart:

**Bug #1 (LDAP flow recursion):**

- [ ] `cat ~/.takwerx/settings.json | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_ldap_flow_recursion_fix', {}), indent=2))"` shows `last_outcome` is `fixed` (first run on box that had the bug) or `idempotent-noop` (subsequent runs / fresh v0.8.8 deploy).
- [ ] No new long-running queries in `pg_stat_activity` against `policybindingmodel` (was 5 × 86s on the broken box; expect 0):

  ```bash
  docker exec -i authentik-postgresql-1 psql -U authentik -d authentik -c "
    SELECT pid, now()-query_start AS duration, LEFT(query,80)
    FROM pg_stat_activity
    WHERE state='active' AND now()-query_start > interval '5 seconds';
  "
  ```

- [ ] DB confirms the fix is in place:

  ```bash
  docker exec -i authentik-postgresql-1 psql -U authentik -d authentik -c "
    SELECT f.slug AS flow, fsb.\"order\" AS ord,
           fsb.evaluate_on_plan, fsb.re_evaluate_policies
    FROM authentik_flows_flowstagebinding fsb
    JOIN authentik_flows_flow f ON f.flow_uuid = fsb.target_id
    WHERE f.slug = 'ldap-authentication-flow' ORDER BY fsb.\"order\";
  "
  ```

  All three rows must show `evaluate_on_plan = f`.

**Bug #2 (pg idle timeout):**

- [ ] `cat ~/.takwerx/settings.json | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_pg_idle_timeout_fix', {}), indent=2))"` shows `last_outcome` is `fixed` (first run on v0.8.7-vintage box with `30s`) or `idempotent-noop` (subsequent runs / fresh v0.8.8 deploy).
- [ ] Postgres confirms the new value:

  ```bash
  docker exec -i authentik-postgresql-1 psql -U authentik -d authentik -c \
    "SHOW idle_in_transaction_session_timeout;"
  ```

  Should show `300s` (or `5min` — same thing).

- [ ] No `IdleInTransactionSessionTimeout` errors in server logs:

  ```bash
  docker logs authentik-server-1 2>&1 | grep -c IdleInTransactionSessionTimeout
  ```

  Should be `0`.

**Both bugs (combined verifier output):**

- [ ] `journalctl -u takwerx-console --since "5 min ago" | grep "authentik config verify"` shows:

  ```
  authentik config verify: all checks passed (workers=4, cache=600s, log_level=warning, pg_idle_timeout=300s)
  ```

- [ ] LDAP outpost `docker inspect authentik-ldap-1 --format '{{.State.Health.Status}}'` is `healthy`. (`StartedAt` may have changed on first v0.8.8 run because the pg-idle-timeout migration force-recreates Postgres, which severs the outpost's API connection briefly. Outpost reconnects on its next 3s retry.)

---

## Diagnostic commands

```bash
# What does each migration's audit say?
cat ~/.takwerx/settings.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('LDAP recursion:', json.dumps(d.get('authentik_ldap_flow_recursion_fix', {}), indent=2))
print('PG idle timeout:', json.dumps(d.get('authentik_pg_idle_timeout_fix', {}), indent=2))
print('Runtime config check:', json.dumps(d.get('authentik_runtime_config_check', {}), indent=2))
"

# Force the migrations to re-run (test path):
sudo systemctl restart takwerx-console
journalctl -u takwerx-console --since "1 min ago" | grep -E "ldap flow recursion|pg idle timeout|config verify"

# Manually re-run each fix function (skip the console):
cd /root/infra-TAK && python3 -c "
from app import _authentik_fix_pg_idle_timeout, _authentik_fix_ldap_flow_recursion
_authentik_fix_pg_idle_timeout(lambda m: print(m))
_authentik_fix_ldap_flow_recursion(lambda m: print(m))
"

# Look at active long-running queries on the Authentik PG (should be 0):
docker exec -i authentik-postgresql-1 psql -U authentik -d authentik -c "
  SELECT pid, now()-query_start AS duration, LEFT(query,80)
  FROM pg_stat_activity
  WHERE state='active' AND now()-query_start > interval '5 seconds';
"
```

---

## What's preserved from prior releases

- **`_authentik_spiral_monitor`** (v0.8.5) — unchanged.
- **Proactive FQDN routing migration** (v0.8.5) — unchanged.
- **Gunicorn worker timeout `--timeout=120`** (v0.8.5) — unchanged.
- **LDAP SA bind verifier** (v0.8.6) — unchanged.
- **`_authentik_apply_official_tunings`** (v0.8.7) — unchanged.
- **`_authentik_verify_runtime_config`** (v0.8.7, with race-tolerant retry) — unchanged.
- **`_recreate_authentik_server_worker`** (v0.8.7) — unchanged. (Note: the new migration uses `docker restart authentik-server-1` rather than the full `--force-recreate --no-deps server worker` because we don't need the worker recreate; only the server holds the in-memory flow plan cache that needs to be cleared. Restart is faster and equally safe.)

---

## Known limitations

- **One-time migration window.** First console startup after upgrade triggers both new migrations. Combined window: ~30-60s (Postgres force-recreate + server+worker restart from bug #2 fix, plus another ~5-10s server-only restart from bug #1 fix). LDAP outpost stays up the whole time (cardinal rule preserved); operators in flight may see one or two `503 Service Unavailable` errors during the Postgres recreate window before the outpost reconnects on its next 3s retry. Subsequent console restarts are full no-ops — both migrations self-gate.
- **Operator-managed flows are NOT modified.** The recursion migration only touches the slug `ldap-authentication-flow` (the one our blueprint creates). If an operator has built their own custom auth flow with `evaluate_on_plan=true` on every binding, this migration leaves it alone — that's their config.
- **Restart, not recreate (recursion fix).** Bug #1 uses `docker restart authentik-server-1` rather than `docker compose up -d --force-recreate --no-deps server worker`. Same effect for clearing the flow cache, ~10-20s faster on slow disks. If you ever need to apply env var changes (which require a full process restart with new environment), use `_recreate_authentik_server_worker` — that's the v0.8.7 path for env tunings.
- **Force-recreate IS used for the timeout fix.** Bug #2 uses `docker compose up -d --force-recreate postgresql` because the timeout is a Postgres command-line arg (`-c idle_in_transaction_session_timeout=...`), and command-line args are only read at container start. `pg_reload_conf()` won't apply them. The recreate also has a side benefit: it clears any stale advisory locks, which is what unsticks already-crashed boxes.
