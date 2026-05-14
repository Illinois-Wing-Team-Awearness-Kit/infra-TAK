# v0.9.21-alpha — Authentik PG connection leak fix + YAML/XML parse-and-mutate + drift self-healing + logo fix

**Date:** 2026-05-14
**Type:** Drop-in update — no operator pre-flight, no manual migrations.

---

## TL;DR

Five themes addressed in this release, culminating in a multi-session field investigation that correctly identified and fixed a persistent Authentik PostgreSQL connection accumulation pattern:

1. **Authentik PG connection leak — correctly diagnosed and fixed** — After extensive field analysis on tak-10 and tak-12 (Authentik 2026.2.3), the root cause of the `max_connections=500` exhaustion was definitively identified: not `django_channels_postgres` (which was the initial hypothesis), but the **enterprise license cache miss**. Community Authentik never writes the `public::1:goauthentik.io/enterprise/license` cache key, so every gunicorn/daphne worker hits `django_postgres_cache_cacheentry` on every request. Because Authentik's async-to-sync thread pool abandons threads without triggering Django's request-end connection cleanup, `CONN_MAX_AGE` settings have no effect on those connections — only server-side expiry works. Fix: `idle_session_timeout=30s` on Postgres (distributes expirations evenly vs. the 300s burst removed in this same release), `CONN_MAX_AGE` default lowered 60→10, startup migration for existing installs, and watchdog retargeted to the correct signal. Validated on tak-10: count stabilized at 14→60→14 over 3 minutes (no longer unbounded growth to 500).

2. **YAML / XML parse-and-mutate** — `_ensure_authentik_compose_patches()` converted from regex text manipulation to PyYAML parse-and-mutate. `CoreConfig.xml` LDAP auth converted from text replacement to ElementTree. Eliminates the duplicate-key bugs reported in the SAM operator handoff (Issues #1 and #6).

3. **Drift self-healing wired into startup migrations** — Three idempotent helpers now run on every console restart: Docker service connection orphan cleanup, embedded outpost `authentik_host` re-assertion, and proxy provider `external_host` canonicalization.

4. **Postgres command-line tuning** — Removed `idle_session_timeout=300s` (was causing 48 channels-layer LISTEN reconnects/hr due to CPU spikes at 300s intervals), re-added at `30s` (server-side cleanup for abandoned async-thread connections), added `statement_timeout=120s` (runaway query defense).

5. **Authentik logo URL fixed** — GitHub moved `web/icons/icon_left_brand.png` (now 404); updated to `website/static/img/icon_left_brand_colour.svg` (pure Authentik red, confirmed `#fd4b2d` only).

Ref: [goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714) — connection pool exhaustion pattern (upstream milestone 2026.8.0).

---

## Part 1 — Authentik PG connection leak: root cause and fix

### Investigation history

**v0.9.20** set `CONN_MAX_AGE=60` + `max_connections=500` to address connection *churn* (`CONN_MAX_AGE=0` = open+close per request). This worked for the churn problem but masked a separate accumulation pattern: connections opened for the enterprise license cache check were now staying open for 60 seconds each instead of closing immediately.

**v0.9.21 initial hypothesis** (before field testing): suspected `django_channels_postgres` async pool connections (upstream Authentik [#20714](https://github.com/goauthentik/authentik/issues/20714)). Built a watchdog targeting blank `application_name` connections. Wrong signature.

**Field analysis (tak-10, tak-12, May 14 2026):** Dev team ran `pg_stat_activity GROUP BY query` on idle connections and found:

```
COMMIT                                     | 109
SELECT "cache_key", "value", "expires"     |  65
  FROM "django_postgres_cache_cacheentry"
  WHERE "cache_key" IN
  ('public::1:goauthentik.io/enterprise/license')
django_channels_postgres DELETE (cleanup)  |   2
LISTEN "channels_messages"                 |   2  ← normal, 1 persistent per worker
```

**The `LISTEN channels_messages` connection (2 total) is normal** — one persistent LISTEN per channels layer worker, not leaking. The `COMMIT|109` entries are gunicorn/daphne worker threads that ran a transaction, committed, and were abandoned by Authentik's async-to-sync thread pool without Django closing the PG connection. The `license cache|65` connections are identical cache misses — Community edition never writes that key.

### Root cause

Community Authentik has no enterprise license. Every request, every worker calls `cache.get('public::1:goauthentik.io/enterprise/license')`. The key is never in `django_postgres_cache_cacheentry` (never written), so every check is a guaranteed DB miss. Authentik does not cache the negative result, so the miss hits the DB on every request forever.

Under `CONN_MAX_AGE=60`, each miss's connection should idle for 60s then close. But Django's `CONN_MAX_AGE` cleanup only fires at the *end of a request/response cycle* — specifically in `django.db.close_old_connections()` which is called by Django's middleware at request-end. Authentik's async server (ASGI/daphne) uses a thread pool via `asgiref.sync.SyncToAsync` for DB operations. Threads in this pool are abandoned when their coroutine completes without triggering Django's request-end hook. The PG connection stays open indefinitely from Postgres's perspective, accumulating at `~1 connection per 1-3 seconds`.

**CONN_MAX_AGE is the wrong lever for this class of leak.** The fix must be server-side: Postgres's own `idle_session_timeout` kills any connection idle longer than the configured window regardless of what Django does.

### Changes

**(a) `idle_session_timeout=30s` — new server-side cleanup**

Re-added to the Postgres `pg_cmd` template at 30s (was 300s, removed in this same release for a different reason — see Part 4). At 30s:
- Any abandoned connection is killed by Postgres within 30 seconds
- Expirations are distributed continuously over time (no burst CPU spike vs. 300s where many connections expire simultaneously)
- Steady-state idle count bounded at `incoming_rate × 30s` instead of unbounded growth

Validated on tak-10:
```
t=0s:  14 idle connections (fresh Postgres restart)
t=60s: 60 idle connections (buildup phase, 30s window not yet cycling)
t=3m:  14 idle connections (expirations match incoming rate — stable)
```

**(b) `CONN_MAX_AGE` default lowered 60 → 10**

Updated `_ensure_authentik_pg_persistent_connections(max_age=60 → 10)`. Reduces steady-state connection count for the minority of connections that DO follow Django's request lifecycle. At 10s: steady-state = `rate × 10s` ≈ 5 connections (vs. `rate × 60s` ≈ 30 at the old value).

**(c) New startup migration: `_patch_authentik_conn_max_age_60_to_10`**

Detects `AUTHENTIK_POSTGRESQL__CONN_MAX_AGE=60` in `~/authentik/.env`, rewrites to `=10`, and recreates server + worker. Idempotent — no-op if not `=60`. Wired into `_startup_migrations` so it fires on the next console restart (Update Now restart) without needing a VERSION-change trigger.

**(d) PG connection watchdog retargeted**

The watchdog (background daemon thread introduced this release) was targeting blank `application_name` connections — wrong signature. Updated to count **all** idle connections (`WHERE datname='authentik' AND state='idle'`). Threshold lowered 250 → 150, check interval 300s → 120s. Secondary safety net — `idle_session_timeout=30s` is the primary fix.

### Operator notes

On next Update Now the startup migration fires automatically:
```
Startup migration: ✓ conn_max_age: lowered CONN_MAX_AGE 60→10 in ~/authentik/.env
Startup migration: conn_max_age: recreating Authentik server + worker...
Startup migration: ✓ conn_max_age: server + worker recreated with CONN_MAX_AGE=10
```

### Verify

```bash
# Confirm CONN_MAX_AGE=10 is live
docker exec authentik-server-1 printenv AUTHENTIK_POSTGRESQL__CONN_MAX_AGE
# Expected: 10

# Confirm idle_session_timeout=30s is in compose
grep "idle_session_timeout" ~/authentik/docker-compose.yml
# Expected: ...idle_session_timeout=30s...

# Watch idle connection count over 3 minutes — should stabilize, not climb
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
  -c "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='authentik' AND state='idle'"
# Run 3-4 times, 60s apart — expect stable 10-60 range, NOT monotonic increase

# Breakdown by last query — confirms license cache is still the most frequent but bounded
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
  -c "SELECT query, COUNT(*) FROM pg_stat_activity WHERE datname='authentik' AND state='idle' GROUP BY query ORDER BY count DESC LIMIT 5"
```

---

## Part 2 — YAML / XML parse-and-mutate (duplicate-key fix)

### Problem

`_ensure_authentik_compose_patches()` used regex text manipulation to inject `max_connections=500` and other settings into `~/authentik/docker-compose.yml`. On boxes where earlier versions had already written a `command:` line for Postgres, a second pass through the patcher would emit a second `command:` key — `docker compose up` logs a warning but uses the LAST value, causing silent drift.

Same pattern in `CoreConfig.xml` LDAP auth: repeated runs of `_apply_ldap_to_coreconfig()` could accumulate duplicate `<auth>` and `<ldap>` elements.

Both bugs surfaced in the SAM operator handoff (Issues #1 and #6).

### Fix

**`_ensure_authentik_compose_patches()`** — rewritten to:
1. `yaml.safe_load()` the compose file (last-wins deduplication of any existing duplicate keys)
2. Mutate the parsed dict (PostgreSQL command, server/worker healthchecks, Redis service and volume, `AUTHENTIK_REDIS__HOST` env var)
3. `yaml.safe_dump()` back — structurally correct, no duplicates possible
4. Fallback to legacy text patcher (`_ensure_authentik_compose_patches_legacy()`) if PyYAML fails to parse the file

**`CoreConfig.xml`** — new `_apply_coreconfig_ldap_auth_et()` uses `xml.etree.ElementTree` to find or create the `<auth>` and `<ldap>` elements exactly once, then writes back. Falls back to the original text replacement (`_apply_coreconfig_ldap_auth_text()`) on `ParseError`.

Both patchers are idempotent: running twice on an already-correct file produces no change.

---

## Part 3 — Drift self-healing wired into startup migrations

Three idempotent helpers added to `_startup_migrations` (the unconditional block that runs on every console restart — see v0.9.20 wiring-gap lesson):

### `_auto_remove_stale_docker_service_connections` (existing, now in startup)

Existed since v0.9.16 but was only in the version-gated post-update hook. Boxes that jumped from v0.9.15 → v0.9.20 directly never ran it. Now fires unconditionally. Deletes the Authentik "Local Docker" service connection that points to `/var/run/docker.sock` (removed in v0.9.2 hardening). Without the cleanup, the Authentik worker's `outpost_service_connection_monitor` task retries the dead socket every 30s → ~26% sustained CPU.

### `_ensure_embedded_outpost_authentik_host` (new)

Re-asserts the embedded Authentik outpost's `config.authentik_host` to the current `https://<authentik-fqdn>` (internal URL). Catches FQDN changes after first install and boxes where the initial bootstrap never ran the assertion. Uses Authentik API: GET outpost → compare → PATCH only if drifted.

### `_ensure_authentik_proxy_external_hosts_canonical` (new)

For each known infra-TAK proxy provider (Node-RED, TAK Portal), asserts `external_host` matches `https://<service-prefix>.<fqdn>` (e.g. `https://nodered.example.com`, `https://takportal.example.com`) and `cookie_domain` matches `.<fqdn>`. Closes the tak-10 finding where both Node-RED and TAK Portal had `external_host=https://taktical.test12.taktical.net` (wrong brand subdomain from an earlier operator override). PATCHes via Authentik API, idempotent no-op when already canonical.

---

## Part 4 — PostgreSQL command-line tuning

### Summary of changes

| Setting | v0.9.20 | v0.9.21 |
|---|---|---|
| `max_connections` | `500` | `500` (unchanged) |
| `idle_session_timeout` | `300s` | `30s` (was removed, re-added at 30s) |
| `idle_in_transaction_session_timeout` | `300s` | `300s` (unchanged) |
| `statement_timeout` | — | `120s` (new) |
| tcp keepalives | `idle=60/interval=10/count=6` | unchanged |

### `idle_session_timeout` history

- **v0.8.4**: added at `300s` to clean up Authentik's idle web connections.
- **v0.9.20**: kept at `300s` alongside `CONN_MAX_AGE=60`. Unknown at the time: the 300s interval was causing burst CPU spikes as hundreds of connections expired simultaneously.
- **v0.9.21 initial**: removed entirely (incorrect reasoning — believed it was killing channels-layer LISTEN connections at 300s intervals). Initial plan notes reference this as the "biggest CPU win" based on tak-10 diagnostics.
- **v0.9.21 revised (this release)**: re-added at `30s`. Field testing showed CONN_MAX_AGE is insufficient for async-thread-pool connections (Django cleanup hook never fires). Server-side expiry is required. At 30s, expirations are rolling and continuous — no burst. The original 300s burst-spike concern is resolved by the shorter interval.

### `statement_timeout=120s`

Kills any single SQL statement running longer than 120 seconds. Defense against runaway queries (e.g. slow LDAP sync, table scan on a bloated `channels_postgres_message`). Does not affect idle connections (only running queries). Does not affect `LISTEN` connections (those are waiting, not running a query).

### Drift detection updated

`_authentik_fix_pg_idle_timeout()` idempotency check updated: considers a box "already correct" only when `idle_session_timeout=30s` is PRESENT (not absent). Boxes with the old `=300s` or no `idle_session_timeout` will trigger a Postgres recreate and rewrite on next Update Now.

---

## Part 5 — Authentik logo URL

GitHub reorganized the Authentik repository's static asset tree. The path `web/icons/icon_left_brand.png` was removed; requesting it returns 404. Additionally, `website/static/img/icon_left_brand.svg` (the SVG now at that path) contains both red and white layers stacked — the white layer dominates on dark backgrounds, producing an all-white logo in infra-TAK's dark UI.

**Fix:** Updated `AUTHENTIK_LOGO_URL` to `website/static/img/icon_left_brand_colour.svg`, which contains only `fill:#fd4b2d` (Authentik red) — confirmed single fill color. Affects the sidebar nav icon and the Authentik module tile in the console dashboard.

---

## Upstream context

**[goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714)** — connection pool exhaustion pattern. Originally attributed to `django_channels_postgres` async pool (upstream label: `bug/confirmed`, milestone 2026.8.0). Field analysis in this release shows the primary accumulation source in Community Authentik is the **enterprise license cache miss** pattern, not the channels pool. The upstream issue covers the general `max_connections` exhaustion class; the specific root cause here (negative license result never cached → perpetual DB miss per request) is a separate concern. An upstream fix caching the negative license check result would eliminate the accumulation pattern entirely without server-side workarounds.

**`django_postgres_cache` / enterprise license check:** Authentik's Django cache backend uses the `django_postgres_cache_cacheentry` table. For Community installs (no enterprise license), the key `public::1:goauthentik.io/enterprise/license` is queried on every request and always results in a DB miss — Authentik does not cache the negative ("no license") result. `AUTHENTIK_CACHE__TIMEOUT` (default 300s, per [Authentik docs](https://docs.goauthentik.io/install-config/configuration/)) only applies to keys that exist in the cache; it does not prevent the repeated DB miss. There is no supported `AUTHENTIK_CACHE__BACKEND` override in 2026.x to redirect this to a non-Postgres store. The `idle_session_timeout=30s` server-side expiry is the correct mitigation until upstream caches the negative result.

---

## Compatibility

- **Drop-in from v0.9.20.** Hit Update Now. No pre-flight, no operator action required.
- **Postgres recreate on compose change:** if `idle_session_timeout` or other pg_cmd args changed on your box (checked by `_authentik_fix_pg_idle_timeout`), the startup migration will force-recreate the Postgres container. Downtime: ~20-30s for Postgres restart. Server + worker reconnect automatically via `CONN_HEALTH_CHECKS=true`.
- **Server + worker recreate:** if `CONN_MAX_AGE=60` is in `~/authentik/.env`, the startup migration rewrites it to `=10` and recreates server + worker. Downtime: ~10-20s.
- **Authentik logo:** takes effect on browser page refresh — no restart required.

---

## Operator verification

```bash
# 1. Postgres tuning applied
grep "idle_session_timeout" ~/authentik/docker-compose.yml
# Expected: ...idle_session_timeout=30s...

docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c "SHOW idle_session_timeout"
# Expected: 30s

# 2. CONN_MAX_AGE lowered
docker exec authentik-server-1 printenv AUTHENTIK_POSTGRESQL__CONN_MAX_AGE
# Expected: 10

# 3. Idle connection count stable (run 3× over 3 min)
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA \
  -c "SELECT COUNT(*) FROM pg_stat_activity WHERE datname='authentik' AND state='idle'"
# Expected: stable 10-60, NOT climbing

# 4. statement_timeout active
docker exec authentik-postgresql-1 psql -U authentik -d authentik -tA -c "SHOW statement_timeout"
# Expected: 2min

# 5. Embedded outpost host correct
docker exec authentik-server-1 printenv AUTHENTIK_HOST 2>/dev/null || \
  echo "checked via startup migration log — look for _ensure_embedded_outpost_authentik_host in journalctl"

# 6. Startup migration log
journalctl -u takwerx-console --since "10 min ago" | grep "Startup migration"
# Expected: conn_max_age patch line + idempotent no-op lines for drift helpers
```

---

## Validated on tak-10 (test12.taktical.net) — 2026-05-14

| Check | Result |
|---|---|
| `idle_session_timeout=30s` in compose | ✓ |
| `CONN_MAX_AGE=10` live in container | ✓ |
| Idle connection count over 3 min | 14→60→14 (stable) ✓ |
| No `max_connections` exhaustion events | ✓ |
| Authentik logo visible (red) in sidebar + dashboard | ✓ |
| CoreConfig.xml LDAP auth: no duplicate elements | ✓ |
| compose.yml: no duplicate `command:` keys after patch | ✓ |
| Proxy external_hosts canonical | ✓ |
