# v0.8.4-alpha Release Notes

## Bug Fixes

### Fix: reverse v0.8.0 LDAP outpost routing on boxes spiraling on the direct internal URL

**Problem (April 2026):** v0.8.0 changed the LDAP outpost from `AUTHENTIK_HOST=https://<fqdn>` (routed through Caddy) to `AUTHENTIK_HOST=http://authentik-server-1:9000` (direct Docker network). The change was correct for fresh installs that hit `tls: internal error` before Caddy completed its ACME challenge. But on established installs with working Caddy, the direct path bypasses Caddy's HTTP/2 multiplexing and connection pooling, allowing the LDAP outpost to fan out parallel unbounded HTTP/1.1 requests to Authentik. On Authentik 2026.2.2, where the `policybindingmodel` flow evaluation regressed from sub-second to 100+ seconds, that parallelism produces a Postgres query storm (200+ active queries pegged on a single SELECT), exhausts `max_connections`, drives the LDAP outpost into `EOF` / `nil pointer dereference` panics, wipes the bind cache, and locks the system into a self-perpetuating spiral.

**Symptoms:**
- Postgres `pg_stat_activity` shows 100+ `active` queries, almost all on `authentik_policies_policybindingmodel`
- Authentik server logs show `runtime: 100000+` ms on `/api/v3/flows/executor/ldap-authentication-flow/`
- LDAP outpost logs cycle through `Result Code 50` ("Insufficient Access Rights"), `nil pointer dereference`, `EOF`, `503 Service Unavailable`
- TAK Server piles up 150+ in-flight LDAP connections (`ss -tnp ':389'`)
- 4 vCPU pegged at 100%, system load average 100+

**Differential diagnosis:** Boxes that survive v0.8.0+ are those with low LDAP request volume — typically Node-RED running streaming-only flows on port 8089 (one cert auth per long-lived TLS session). Boxes that break are those with high LDAP request volume — Mission API / DataSync subscriptions that re-authenticate per HTTP request. The volume difference crosses the spiral threshold; the routing change exposes it. Both kinds of boxes were running fine before v0.8.0 because the FQDN-via-Caddy path contained the parallelism.

**Fix:** New post-update migration `_apply_authentik_ldap_routing_repair` reverses the v0.8.0 change for boxes whose outpost is spiraling. The migration is strictly health-gated and validated:

1. **Trigger conditions (all must hold):**
   - LDAP service in `~/authentik/docker-compose.yml` currently uses `http://authentik-server-1:9000`
   - LDAP outpost log shows ≥2 spiral markers in last 200 lines: `Result Code 50`, `nil pointer`, `failed to execute flow`, `EOF`, `502 Bad Gateway`, `503 Service Unavailable`, `exceeded stage recursion depth`
   - `https://<fqdn>/-/health/live/` is reachable from inside the LDAP container (probe via `docker exec wget`) — confirms Caddy is serving and DNS works
2. **What it does:**
   - Backs up `docker-compose.yml` to `docker-compose.yml.bak.v0.8.4.<timestamp>`
   - Rewrites the `ldap:` service: `AUTHENTIK_HOST: https://<fqdn>` and inserts `extra_hosts: - "<fqdn>:host-gateway"` after the image line
   - `docker compose up -d --no-deps --force-recreate ldap` — only the LDAP container is touched (preserves the cardinal rule against gratuitous server/worker restarts)
3. **Validation:**
   - Waits 30s after recreate
   - Checks `docker logs authentik-ldap-1 --since 30s` for `successfully connected websocket` and absence of `tls:` / `502` / `503` errors
   - On failure, restores the backup and recreates LDAP back on the internal URL — the box ends up exactly where it started

**No-ops on:**
- Healthy boxes (no spiral markers in outpost logs)
- Boxes without an FQDN configured (no `AUTHENTIK_HOST=https://...` in `~/authentik/.env`)
- Boxes where Caddy isn't serving the FQDN (probe fails)
- Boxes already routed via FQDN

The two LDAP `AUTHENTIK_HOST` migrations now coexist and are mutually exclusive by design:

| Migration | Fires when LDAP is on... | And outpost is broken with... | Action |
|---|---|---|---|
| v0.8.0 (existing, retained) | `https://<fqdn>` | `tls: internal error` (no websocket) | Force to `http://authentik-server-1:9000` |
| v0.8.4 (new) | `http://authentik-server-1:9000` | spiral signature (≥2 markers) | Reverse to `https://<fqdn>` if Caddy serves |

### Fix: post-update migration thread now survives gunicorn worker recycle

**Problem:** During `systemctl restart takwerx-console`, gunicorn briefly spawns and reaps a transient worker before the new master takes over. The pre-v0.8.4 `_post_update_auto_deploy` would (1) save `last_console_version = VERSION` to `settings.json` immediately, (2) spawn a daemon thread that sleeps 10s before doing anything, and (3) print "scheduling auto-deploy". Then the transient worker would be killed by the master roughly 2-7 seconds later — taking the daemon thread with it. The new worker would then import `app.py`, see `last_ver == VERSION` (already saved by the dead worker), and short-circuit. **Migrations silently never ran.** This was the failure mode discovered in the field on the broken `0.8.4-alpha` dev build: the routing repair, the PG tuning, and AUTHENTIK_WEB_WORKERS adjustments all looked like they had run because the version ticket was saved, but no migration code actually executed.

**Fix:**
1. **Version save moved to AFTER migrations complete.** The `last_console_version = VERSION` write now lives in a `finally` block that runs only after `_run_post_update()` returns (whether successfully or with an exception caught and logged). If the worker is killed mid-migration, the version stays at the old value and the next worker re-runs the migration.
2. **PID-checked lockfile (`/tmp/takwerx-post-update.lock`) prevents concurrent runs.** Multiple gunicorn workers boot in close succession during `systemctl restart`. The lockfile contains the holder PID. New workers acquire it only if (a) the file doesn't exist, (b) the holder PID is dead (`os.kill(pid, 0)` raises `ProcessLookupError`), or (c) the lock is older than 30 minutes. This means a killed worker holding a stale lock doesn't block recovery; a new worker takes over within seconds.
3. **All migration prints now use `flush=True`.** Gunicorn's stdout was being lost when the worker was killed mid-buffer. Forcing flush ensures each step is journalled.

**Operator impact:** none on healthy boxes. On boxes where the broken dev build of 0.8.4-alpha set the version ticket prematurely, the **first restart after pulling the fixed v0.8.4-alpha tag** will run all migrations to completion. (The dev build's bad ticket was only saved on tak-10 and was repaired manually before main was tagged; production boxes upgrading from v0.8.3-alpha will see this work correctly on the first try.)

### Fix: detect any non-30s `idle_in_transaction_session_timeout`, not just hardcoded values

The `needs_pg_update` check in `_ensure_authentik_compose_patches` previously triggered only on `300s`, `10s`, `120s` literal values. Operators who manually edited `docker-compose.yml` (e.g. `15s` during debugging) would not have their setting auto-corrected on Update Now. v0.8.4 generalizes the check: any value other than `30s` triggers a migration to `30s`.

**Why 30s is still the right value:** Authentik's startup migration lifecycle holds a Postgres transaction open during module loading with idle-in-transaction gaps of 10–20s. Settings below 30s kill that connection and crash-loop the server. 30s is the safe minimum that still aggressively reaps the enterprise-license-check leak introduced by Authentik 2026.2.2 (see v0.8.3 release notes for that incident).

## Who is affected

- **Operators on v0.8.0+ whose Authentik+LDAP routing was migrated to internal URL** — if your LDAP outpost is currently spiraling, **Update Now** will detect it, validate the FQDN path, and reverse the routing automatically.
- **Operators with manual `idle_in_transaction_session_timeout` values** other than `30s` — these will be reset to `30s`.
- **Healthy boxes** — no changes; both migrations skip cleanly.

## How to recover (broken box, console unreachable)

If your console is unreachable, use the backdoor at `https://<server-IP>:5001`. From there, **Update Now** runs the migration and your outpost will be back on FQDN routing within ~60 seconds. The migration logs to the post-update output stream — search for `v0.8.4 routing repair` to see what it did.

If the migration's auto-rollback fires (validation failed because Caddy wasn't ready or your FQDN is misconfigured), the box ends up exactly as before — no infrastructure damage. Fix Caddy / DNS and re-run.

## Files changed

| File | Change |
|---|---|
| `app.py` | New function `_apply_authentik_ldap_routing_repair(ak_dir, plog)` — the v0.8.4 migration. Health-gated, Caddy-probed, validated, auto-rollback on failure |
| `app.py` | `_post_update_auto_deploy` calls the new function after the existing v0.8.0 LDAP HOST check, before AUTHENTIK_WEB_WORKERS |
| `app.py` | `needs_pg_update` (2 call sites) generalized to detect any value other than `30s` |
| `app.py` | `_post_update_auto_deploy`: version save moved to `finally` after migration completes; PID-checked lockfile prevents stale-ticket short-circuit on worker recycle; migration prints use `flush=True` |
| `app.py` | VERSION bumped to `0.8.4-alpha` |
| `docs/HANDOFF-LDAP-AUTHENTIK.md` | New incident section "v0.8.0 → v0.8.4 LDAP outpost routing reversal" with measured data, gates, and rules |

## What v0.8.4 explicitly does NOT change

- **Authentik image tag** — still tracking latest 2026.2.x. Once Authentik ships a fix for the `policybindingmodel` regression, the FQDN routing requirement may relax; for now the workaround is shipping.
- **`AUTHENTIK_WEB_WORKERS=4`** logic from v0.8.2 — preserved
- **CoreConfig auth order** — no changes; admin auth still goes through LDAP first per existing config
- **Mission API client cert** — no changes (this was investigated and ruled out as a v0.8.4 fix; see HANDOFF-LDAP-AUTHENTIK.md)
- **Guard Dog** — no changes; existing Authentik health monitor and 3-restart-per-day cap unchanged
- **Rollback to arbitrary GitHub release** — planned for v0.8.5
