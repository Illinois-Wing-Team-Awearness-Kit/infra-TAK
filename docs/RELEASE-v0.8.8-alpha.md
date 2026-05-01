# v0.8.8-alpha Release Notes

## Headline: fix the LDAP flow stage-binding recursion bug (latent on every box since the LDAP feature shipped)

> **Another fleet-wide latent bug, found because slow-disk boxes can't hide it.** Every infra-TAK install since the LDAP feature shipped has had `evaluate_on_plan=true` AND `re_evaluate_policies=true` on all three `ldap-authentication-flow` stage bindings. That combo causes a cascading policy re-evaluation on every step of every authentication plan. Fast-disk boxes hide it because each cascading policy query completes in microseconds. On a buddy's slow-disk SSDNodes box (1795 random-write 4k IOPS, 31.7 MB/s sequential write — between spinning rust and slow SATA SSD), it surfaced spectacularly: **Postgres CPU pinned at 900-1500% sustained**, with five backends running 86-second `policybindingmodel` queries on a box doing 0.36 LDAP binds per second. Setting `evaluate_on_plan=false` (matching how `default-authentication-flow` is configured) dropped Postgres CPU **~115x in 60 seconds** with the LDAP outpost untouched. v0.8.8 ships the corrected blueprint YAML, fixes the healing path, and adds an idempotent self-healing migration so every box auto-corrects on next update.

The Apr 30 2026 investigation started from a buddy's box that "just won't stop screaming after v0.8.7." Initial diagnostics ruled out CPU steal time (0%) and worker count (correct, 4). `fio` exposed the slow disk (1795 IOPS), but the smoking gun came from `pg_stat_activity`: five backends sitting on the same `SELECT FROM authentik_policies_policybindingmodel` query for 86 seconds each. That pattern is not slow-disk noise — that's a recursive query loop. A `grep` of `app.py` found we ourselves had been shipping `evaluate_on_plan: true` AND `re_evaluate_policies: true` in the LDAP blueprint since day one. The fix was a one-row UPDATE per binding.

**Result on the slow-disk box, ~60 seconds after `evaluate_on_plan=false` + server restart:**

| Metric | Before fix | After fix |
|---|---|---|
| `authentik-postgresql-1` CPU avg | ~900% sustained, multiple 1000%+ spikes | ~7.8% (idle) |
| Long-running queries (>5s) | 5 backends × 86s on `policybindingmodel` | 0 |
| `authentik-server-1` CPU | 200-350% | 100-250% (normal traffic handling) |

LDAP outpost (`authentik-ldap-1`) `StartedAt` unchanged. Cardinal rule held.

---

## The bug, explained

Each Authentik flow has stage bindings. Each binding has two flags that control when policies are evaluated:

- **`evaluate_on_plan`** — when `true`, policies attached to this binding are evaluated *during plan generation* (before the user even sees a step). When `false`, they're only evaluated lazily as the user reaches each step.
- **`re_evaluate_policies`** — when `true`, policies are re-evaluated *each time* the flow plan is replayed/inspected.

When **both** are `true` on every binding in a flow, replays re-trigger plan generation, which re-evaluates policies, which re-triggers replays. With Authentik 2025.10+ using Postgres for cache + channels + tasks (no Redis), every step of that loop is a Postgres query on `authentik_policies_policybindingmodel`. The table has only the primary key as an index, so each lookup is a sequential scan. On fast disks the loop completes per-bind in well under a millisecond — invisible. On slow disks it explodes.

`default-authentication-flow` (which works fine on every box) has `evaluate_on_plan=false, re_evaluate_policies=true`. That's the combo we now ship for `ldap-authentication-flow`.

---

## Changes

### 1. Blueprint YAML — flip `evaluate_on_plan: true` → `false` on all 3 LDAP flow stage bindings

Two YAML copies in `app.py` (initial deploy + healing reimport), three bindings each = six edits. `re_evaluate_policies: true` is preserved (matches default flow, not part of the recursion combo).

```yaml
# was:
evaluate_on_plan: true
re_evaluate_policies: true
# is now:
evaluate_on_plan: false
re_evaluate_policies: true
```

### 2. `_ensure_ldap_flow_authentication_none()` healing path

```python
# was:
'evaluate_on_plan': True, 're_evaluate_policies': True,
# is now:
'evaluate_on_plan': False, 're_evaluate_policies': True,
```

This function is called both during initial deploy and as a post-update healing pass. Without this fix, the heal would re-introduce the bug on already-fixed boxes.

### 3. New idempotent self-healing migration: `_authentik_fix_ldap_flow_recursion(plog)`

Lives next to `_authentik_apply_official_tunings` and `_authentik_verify_runtime_config` in `app.py`. Hooked into both `_startup_migrations` (every console start) and `_post_update_auto_deploy` (after every update).

What it does:

1. Probes for `authentik-postgresql-1`. If not running, skip.
2. Counts bindings on `ldap-authentication-flow` with `evaluate_on_plan=true`.
3. If `count == 0`: persist `last_outcome='idempotent-noop'`, return False. (Every startup after the first on already-fixed boxes — costs a single COUNT query + one settings.json write.)
4. If `count > 0`: idempotent UPDATE setting them to false; **restart `authentik-server-1` only** (server alone — no `--no-deps server worker`, no docker compose recreate, no LDAP outpost touch); persist `last_outcome='fixed'` + `last_bad_count=N`.

Persists outcome to `settings.authentik_ldap_flow_recursion_fix` for operator audit. Like every other v0.8.7+ self-healing migration, this is fully idempotent and self-gating.

---

## What was explicitly NOT shipped

- ❌ **Console UI / button.** Same scope discipline as v0.8.7.
- ❌ **The rollback feature** (originally planned for v0.8.8). Pushed to v0.8.9. Stability fixes outrank features when we keep finding fleet-wide latent bugs.
- ❌ **A check inside `_authentik_verify_runtime_config`.** Considered. Kept separation of concerns: runtime verifier checks `ak dump_config` + `docker top` (runtime config). New migration audits its own DB-state outcome to a parallel settings key. Cleaner.
- ❌ **Custom index on `policybindingmodel.target_id`** to mitigate the seq scan that amplifies the recursion. Tempting on slow disks, but Authentik manages its own schema. Removing recursion is the correct fix; the index would be treating a symptom.
- ❌ **Aggressive autovacuum tuning on the cache table.** Once recursion stops, cache churn drops to normal levels and default autovacuum is fine. Manual VACUUM during the live debugging session was a red herring.

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

Once v0.8.8 lands on this box, the migration will be `idempotent-noop` (DB state already correct), validating the no-op path of the migration.

### b) Other fleet boxes (tak-10, responder, ssdnodes, Alex's R3930)

Currently all have v0.8.7-alpha and the latent bug. After they pull v0.8.8 (either via Update Now or `git pull main`), the migration will fire once:

1. Count returns 3.
2. UPDATE flips them.
3. `docker restart authentik-server-1` (~5-10s blip, server only).
4. Audit shows `last_outcome='fixed'`, `last_bad_count=3`.
5. Their next CPU samples should show a noticeable drop — less dramatic than ssdnodes (faster disks were hiding more) but measurable.

---

## Operator acceptance checklist

After Update Now or `git pull origin dev` + console restart:

- [ ] `cat ~/.takwerx/settings.json | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_ldap_flow_recursion_fix', {}), indent=2))"` shows `last_outcome` is `fixed` (first run on box that had the bug) or `idempotent-noop` (subsequent runs / fresh v0.8.8 deploy).
- [ ] LDAP outpost `docker inspect authentik-ldap-1 --format '{{.State.StartedAt}}'` is **unchanged** from before the upgrade (server-only restart honored the cardinal rule).
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

---

## Diagnostic commands

```bash
# What does the recursion-fix audit say?
cat ~/.takwerx/settings.json | python3 -c \
  "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_ldap_flow_recursion_fix', {}), indent=2))"

# Force the migration to re-run (test path):
sudo systemctl restart takwerx-console
journalctl -u takwerx-console --since "1 min ago" | grep -E "ldap flow recursion"

# Manually re-run the fix function (skip the console):
cd /root/infra-TAK && python3 -c "
from app import _authentik_fix_ldap_flow_recursion
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

- **One-time migration window.** First console startup after upgrade triggers `_authentik_fix_ldap_flow_recursion`, which restarts `authentik-server-1` for ~5-10s. The LDAP outpost is unaffected (preserves bind cache). Subsequent console restarts are no-ops because the migration self-gates.
- **Operator-managed flows are NOT modified.** The migration only touches the slug `ldap-authentication-flow` (the one our blueprint creates). If an operator has built their own custom auth flow with `evaluate_on_plan=true` on every binding, this migration leaves it alone — that's their config.
- **Restart, not recreate.** We use `docker restart authentik-server-1` rather than `docker compose up -d --force-recreate --no-deps server worker`. Same effect for clearing the flow cache, ~10-20s faster on slow disks. If you ever need to apply env var changes (which require a full process restart with new environment), use `_recreate_authentik_server_worker` — that's the v0.8.7 path for env tunings.
