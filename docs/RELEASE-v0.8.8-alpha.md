# v0.8.8-alpha Release Notes

## What this fixes

Two fleet-wide latent bugs in the Authentik integration. Both have been present for several releases. Fast-disk boxes hide them; slow-disk boxes (sub-2k IOPS) explode under them. v0.8.8 fixes both for new deploys AND auto-heals every existing box on next update.

### Bug #1 — LDAP flow stage-binding recursion

Every infra-TAK install since the LDAP feature shipped has had `evaluate_on_plan=true` AND `re_evaluate_policies=true` on all three `ldap-authentication-flow` stage bindings. That combo causes a cascading policy re-evaluation on every step of every authentication plan — every replay re-triggers plan generation, which re-evaluates policies, which re-triggers replays. Each loop hits `authentik_policies_policybindingmodel` (which has only the PK as an index, so it's a seq scan).

On fast disks the loop completes in microseconds — invisible. On slow disks it pegs Postgres at hundreds of percent CPU and produces multi-second policy queries on a box doing zero real work.

**Fix:** flip `evaluate_on_plan` to `false` on the three `ldap-authentication-flow` bindings (matching how `default-authentication-flow` is configured). `re_evaluate_policies: true` is preserved — that's part of how Authentik flows are supposed to work, and it's not what causes the recursion. The recursion is the *combination* of both flags.

### Bug #2 — Postgres `idle_in_transaction_session_timeout` set to 30s

In v0.8.4 we set `idle_in_transaction_session_timeout=30s` on Authentik's Postgres to bound zombie connections that occasionally got stuck idle-in-tx. 30 seconds is fine on NVMe (Authentik's Django startup migrations complete in a few ms each — never idle).

On slow disks, individual fsync waits push migration steps into idle-in-transaction state for tens of seconds. Postgres kills the connection at exactly 30s, leaving a stale advisory lock from `/lifecycle/migrate.py`. The next server boot can't acquire the migration lock, blocks forever on `waiting to acquire database lock`, and the server crash-loops. Caddy returns 503; the LDAP outpost sits unhealthy.

**Fix:** bump to `300s`. Tightly bounds zombie sessions to 5 minutes (instead of unbounded), gives slow-disk migrations 10x the headroom.

---

## Changes shipped

### `app.py`

- **Blueprint YAML** (initial deploy + healing reimport, 6 occurrences) — `evaluate_on_plan: true` → `false` on the three `ldap-authentication-flow` stage bindings.
- **`_ensure_ldap_flow_authentication_none()`** — same fix on the post-update healing path so a heal pass doesn't re-introduce the recursion.
- **Compose template + sentinel** (4 occurrences) — `idle_in_transaction_session_timeout=30s` → `300s` for new installs, plus the regex sentinel that detects "compose needs update" so existing boxes get rewritten on next deploy.
- **New idempotent migration `_authentik_fix_pg_idle_timeout(plog)`** — wired into both `_startup_migrations` and `_post_update_auto_deploy`, runs **before** the recursion fix (ordering is important; see below). Detects the old 30s value, rewrites compose to 300s, force-recreates Postgres (which also clears any stale advisory locks left by previous crash loops), restarts server + worker. Idempotent — no-op on already-300s boxes.
- **New idempotent migration `_authentik_fix_ldap_flow_recursion(plog)`** — wired into both startup and post-update, runs *after* the timeout fix. Counts bindings on `ldap-authentication-flow` with `evaluate_on_plan=true`. If `count > 0`, runs a single SQL UPDATE and restarts `authentik-server-1` only (cardinal rule: LDAP outpost untouched). Idempotent — no-op when `count == 0`.
- **Verifier extension** — `_authentik_verify_runtime_config` now also probes `SHOW idle_in_transaction_session_timeout` so operator audit catches any future regression. Verifier re-runs after the pg-idle-timeout migration so the audit reflects post-migration state.

Both migrations write outcome to `~/.takwerx/settings.json` (`authentik_pg_idle_timeout_fix` and `authentik_ldap_flow_recursion_fix`) for operator audit.

### Why ordering matters

The recursion fix restarts `authentik-server-1` to clear the in-memory flow plan cache. On a v0.8.7-vintage box with `30s` still in compose, that server restart would trigger Django startup migrations that hit the 30s timeout and crash-loop the box just from running our healing migration. Running the timeout fix *first* gives the subsequent server restart 10x the headroom and lets the recursion fix complete cleanly.

---

## What was explicitly NOT shipped

- **Console UI / button.** Same scope discipline as v0.8.7. v0.8.8 is a stability release.
- **The rollback feature** (originally planned for v0.8.8). Parked to **v0.9.0 or later** — the v0.8.x line is reserved for Authentik stabilization until the fleet is provably stable across slow disks.
- **Custom index on `policybindingmodel.target_id`** to help worst-case seq scans on slow disks. Removing the recursion is the correct fix; the index would be treating a symptom and we don't fork Authentik's schema.
- **Configurable `idle_in_transaction_session_timeout` via env var.** 300s is correct for every box we know about. Adding a knob is another footgun.

---

## Operator acceptance checklist

After Update Now or `git pull origin main` + console restart:

```bash
# Both migrations' audits
sudo cat /home/takwerx/infra-TAK/.config/settings.json | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('PG idle timeout:', json.dumps(d.get('authentik_pg_idle_timeout_fix', {}), indent=2))
print()
print('LDAP recursion:', json.dumps(d.get('authentik_ldap_flow_recursion_fix', {}), indent=2))
print()
print('Runtime config last_outcome:', d.get('authentik_runtime_config_check', {}).get('last_outcome'))
"
```

Expected on a v0.8.7-vintage box on its first v0.8.8 startup:

- `authentik_pg_idle_timeout_fix.last_outcome`: `"fixed"`, with `last_previous_value: "30s"` and `last_new_value: "300s"`
- `authentik_ldap_flow_recursion_fix.last_outcome`: `"fixed"`, with `last_bad_count: 3`
- `authentik_runtime_config_check.last_outcome`: `"pass"`

Subsequent restarts:

- Both migrations: `last_outcome: "idempotent-noop"`
- Verifier: `last_outcome: "pass"`

Direct DB confirmation (optional):

```bash
# Postgres timeout — should be 5min (300s)
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc \
  "SHOW idle_in_transaction_session_timeout;"

# LDAP bindings — all three should show evaluate_on_plan=f
docker exec -i authentik-postgresql-1 psql -U authentik -d authentik -c "
SELECT fsb.\"order\", fsb.evaluate_on_plan, fsb.re_evaluate_policies
FROM authentik_flows_flowstagebinding fsb
JOIN authentik_flows_flow f ON f.flow_uuid = fsb.target_id
WHERE f.slug = 'ldap-authentication-flow' ORDER BY fsb.\"order\";
"
```

---

## Migration window

First console restart after upgrade triggers both migrations sequentially:

| Step | Time |
|---|---|
| Postgres force-recreate (bug #2 fix) | ~10-15s |
| Wait for Postgres ready | ~5-30s |
| Server + worker restart with Django startup migrations | ~30-90s on slow disks |
| Recursion SQL UPDATE + server-only restart | ~5-15s |

**Total user-visible Authentik unavailability: ~50-150 seconds** (between Postgres recreate and final server-up). Web UI logins fail during that window; existing browser sessions and TAK LDAP binds for already-known users continue working (LDAP outpost has its own cache and reconnects on its 3s retry interval).

Subsequent restarts are full no-ops — both migrations self-gate.

---

## What's preserved from prior releases

- `_authentik_apply_official_tunings` (v0.8.7) — `AUTHENTIK_WEB__WORKERS=4`, cache timeouts, log level. Unchanged.
- `_authentik_verify_runtime_config` (v0.8.7) — extended in v0.8.8 with the Postgres timeout probe.
- `_authentik_spiral_monitor` (v0.8.5) — unchanged.
- LDAP SA bind verifier (v0.8.6) — unchanged.

---

## Cardinal rules upheld

- **Server-only restart for the recursion fix.** `docker restart authentik-server-1`. LDAP outpost (`authentik-ldap-1`) stays up the whole time. No thundering herd.
- **Postgres recreate severs DB connections briefly** during the timeout fix; LDAP outpost reconnects automatically on its 3-second retry. No outpost restart.
- **Idempotent.** Both migrations safe to run repeatedly.
- **Audit trail in `settings.json`.** Operators can read `last_outcome` to know what happened on every box.
