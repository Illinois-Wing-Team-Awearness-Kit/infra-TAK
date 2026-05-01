# v0.8.8-alpha — Work Plan

**Headline (and ONLY) feature: fix the LDAP flow stage-binding recursion bug.**

> **Scope discipline note:** v0.8.8 was originally going to be the rollback feature. That work is pushed again. Operator stability comes first, and we just discovered another fleet-wide bug that's been latent since the LDAP feature shipped — caught only because it surfaced spectacularly on a slow-disk ssdnodes box (Postgres CPU pinned at 900-1500% sustained, with multiple PG backends running 86-second policy queries). Fix lands first, then we can finally tackle rollback in v0.8.9.
>
> **No UI changes in v0.8.8.** Same discipline as v0.8.7. All audit state lives in `~/.takwerx/settings.json` and is operator-readable; defaults are correct for every box.

---

## 1. The Apr 30 2026 ssdnodes investigation (root cause)

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

## 2. What v0.8.8 ships

### Changes to `app.py`

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

### Documentation

- **`docs/PLAN-v0.8.8.md`** — this file.
- **`docs/RELEASE-v0.8.8-alpha.md`** — operator-facing release notes with field evidence.
- **`docs/HANDOFF-LDAP-AUTHENTIK.md`** — adds a "v0.8.8 — flow recursion fix" section.
- **`README.md`** — bumps "Latest release" headline + adds changelog entry.

---

## 3. What v0.8.8 explicitly does NOT ship

- ❌ **UI changes.** Same as v0.8.7.
- ❌ **The rollback feature.** Pushed to v0.8.9.
- ❌ **A check inside `_authentik_verify_runtime_config`.** Considered adding the recursion check there for one-stop status, but kept separation of concerns: the runtime verifier is for *runtime config* (what `ak dump_config` and `docker top` see). The flow recursion fix is *DB state* (what `psql` sees). They live in parallel keys (`authentik_runtime_config_check` vs `authentik_ldap_flow_recursion_fix`) and operators can audit each independently.
- ❌ **Index on `policybindingmodel.target_id`.** Tempting on slow-disk boxes (would help worst-case sequential scans), but Authentik manages its own schema. We don't fork it. Removing the recursion is the correct fix; the index would be treating a symptom.
- ❌ **Aggressive autovacuum on cache table.** Same reason — once recursion stops, cache churn drops to normal levels and default autovacuum is fine.

---

## 4. Cardinal rules upheld

- **Server-only restart.** `docker restart authentik-server-1`. Never `--no-deps server worker` (we don't need the worker recreate; only server holds the in-memory flow plan cache). LDAP outpost (`authentik-ldap-1`) stays up the whole time. `authentik-postgresql-1` and `authentik-worker-1` stay up.
- **Idempotent.** Running the migration twice is safe and cheap.
- **Self-gating.** No-op on already-fixed boxes — the count query short-circuits the UPDATE and restart.
- **Audit trail in `settings.json`.** Operators read `last_outcome` + `last_bad_count` to know what happened.
- **Documented in upstream-style.** `consult-upstream-docs` Cursor rule applied during this investigation: we read [Authentik flows docs](https://docs.goauthentik.io/docs/flow/) on `re_evaluate_policies` and `evaluate_on_plan` semantics before deciding which flag to flip.

---

## 5. Validation plan

### a) ssdnodes (the slow-disk box that surfaced the bug)

Already manually fixed via SQL during the live debugging session. Postgres CPU dropped 115x. Box is currently green. After v0.8.8 lands, the migration will be idempotent-noop on first run (the SQL we ran by hand already set `evaluate_on_plan=false`). This validates the no-op path.

### b) tak-10 / responder / ssdnodes-validated / Alex's R3930

These boxes have v0.8.7-alpha and currently still have `evaluate_on_plan=true` on their LDAP flow bindings (latent bug, hidden by faster disks). After they pull v0.8.8 (either via Update Now or `git pull main`), the migration should:

1. Detect 3 bindings with `evaluate_on_plan=true`.
2. UPDATE them.
3. Restart `authentik-server-1` (~5-10s blip, server only).
4. Persist `last_outcome='fixed'`, `last_bad_count=3`.
5. Their next CPU samples should show a noticeable drop (less dramatic than ssdnodes since their disks aren't as starved, but measurable).

### c) New deploys

After v0.8.8 ships, fresh installs run the corrected blueprint YAML on first import. The migration is then a no-op forever after.

### d) Operator acceptance gate

```bash
sudo -u takwerx cat /root/infra-TAK/.config/settings.json | python3 -c \
  "import json,sys; print(json.dumps(json.load(sys.stdin).get('authentik_ldap_flow_recursion_fix', {}), indent=2))"
```

Expected after first console restart on an upgraded box:

```json
{
  "last_check_utc": "2026-04-30T...",
  "last_outcome": "fixed",
  "last_bad_count": 3
}
```

Subsequent restarts:

```json
{
  "last_check_utc": "2026-04-30T...",
  "last_outcome": "idempotent-noop",
  "last_bad_count": 0
}
```

---

## 6. Release flow

1. Commit to `dev` (this PR).
2. Pull `dev` onto tak-10 + responder for validation soak (operator request: validate before merging to main).
3. Once green, selective merge to `main` (just like v0.8.7 — see `docs/COMMANDS.md`).
4. Tag `v0.8.8-alpha`, push tag.
5. ssdnodes-validated, Alex's box, and any other operator boxes pull main / hit Update Now to get the fix.
