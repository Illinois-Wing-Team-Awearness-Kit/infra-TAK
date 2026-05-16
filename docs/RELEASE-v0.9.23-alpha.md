# v0.9.23-alpha — PgBouncer architectural fix + TAK Server connection-state diagnostic

**Date:** 2026-05-15
**Type:** Architectural release — drop-in update via Update Now. Closes the Authentik PG connection-leak class structurally; retires the zombie-subscription misdiagnosis.

---

## TL;DR

`v0.9.23-alpha` closes the Authentik PostgreSQL connection-leak class by inserting **PgBouncer** (transaction-pool mode) between Authentik and its Postgres. PgBouncer caps real PG connections at a fixed ceiling regardless of how leaky Authentik's client-side pool becomes — moving the constraint from "Authentik must not leak" (which we can't enforce — it's upstream code) to "PgBouncer must cap connections" (which is exactly what PgBouncer is designed to do).

Field-validated on tak-10 over the 2026-05-15 day-long forensic session:

- **Pre v0.9.23:** 72-167 idle PG connections, `ak-pg-watchdog` firing on cumulative pool exhaustion, Authentik server CPU bursts.
- **Post v0.9.23 (after v2.2 fix):** PgBouncer multiplexing 28 client conns down to 7 real PG conns. `maxwait=0`. Hard ceiling of 30 real PG conns. Watchdog goes silent for the right reason.

Plus a **TAK Server connection-state diagnostic** (`GET /api/takserver/zombies` aka `/api/takserver/connection-state`) that replaces an earlier "zombie subscription" framing that turned out to be a misdiagnosis once we read the actual cot DB schema.

Three iterations were needed to land the architectural fix correctly; the **v2.2 compose-precedence fix** is what actually makes PgBouncer take effect on existing installs. All three iterations are documented honestly below — including v1 mistakes — because the failure pattern is a useful reference for future docker-compose env work.

---

## Why this release exists — the upstream context

`v0.9.20`/`v0.9.21`/`v0.9.22` chased the same symptom (Authentik PG connection exhaustion → `ak-pg-watchdog` firing → LDAP outpost restarts → TAK Server "User lookup failed" errors during the outpost outage window) through three different lever positions:

- `v0.9.20` — bumped `max_connections` 300 → 500, added `CONN_MAX_AGE=60` + `CONN_HEALTH_CHECKS=true` to Authentik client side. Helped, didn't fix.
- `v0.9.21` — added `idle_session_timeout=30s` on Postgres command line. Caused `django_channels_postgres` LISTEN socket reconnect storms (regression).
- `v0.9.22` — removed `idle_session_timeout=30s` again; runtime is stable but the underlying leak is still present, watchdog still fires periodically.

Root cause confirmed via Authentik upstream issue ([goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714)) and our own investigation:

- **`django_postgres_cache` cache-miss path** bypasses Django's `CONN_MAX_AGE` request-end cleanup — every `enterprise/license` cache miss (which is **every request** on Community Authentik because the enterprise license row is never written) opens a fresh PG connection that never gets cleaned up by Django.
- Authentik 2026.x's async-to-sync thread pool abandons threads without triggering Django's request-end hook, so `CONN_MAX_AGE` has no effect on those connections.

**No amount of `CONN_MAX_AGE` tuning or worker recycling fixes this** — it's structurally upstream code that we can't patch. The architectural answer is a connection pooler.

---

## Phase 6 — PgBouncer install (the architectural fix)

### What gets installed

`_ensure_authentik_pgbouncer(plog)` in `app.py` adds an `edoburu/pgbouncer:v1.25.1-p0` service to `~/authentik/docker-compose.yml`:

- **Image:** `edoburu/pgbouncer:v1.25.1-p0` — pinned. ~15 MB Alpine, auto-generates `userlist.txt` from `DB_USER`/`DB_PASSWORD` env vars, supports `scram-sha-256`.
- **Pool mode:** `transaction` — required for our workload; statement-mode would break prepared-statement-heavy Django queries.
- **Pool sizing:** `DEFAULT_POOL_SIZE=25 + RESERVE_POOL_SIZE=5 = 30 real PG conns ceiling`. `MAX_CLIENT_CONN=1000` (Authentik can park as many client conns as it wants — PgBouncer multiplexes).
- **`SERVER_RESET_QUERY=DISCARD ALL`** — clean slate between transactions per upstream Authentik PgBouncer guidance.
- **Healthcheck:** `pg_isready` every 30s.
- **Compose `depends_on`** wired so server + worker wait for pgbouncer.

### Authentik `.env` and compose changes

Five required Authentik settings are written to `~/authentik/.env`:

- `AUTHENTIK_POSTGRESQL__HOST=pgbouncer`
- `AUTHENTIK_POSTGRESQL__PORT=5432`
- `AUTHENTIK_POSTGRESQL__DISABLE_SERVER_SIDE_CURSORS=true` — **REQUIRED** for transaction-pool mode (per [Authentik docs](https://docs.goauthentik.io/install-config/configuration/#using-a-postgresql-connection-pooler))
- `AUTHENTIK_POSTGRESQL__CONN_HEALTH_CHECKS=true`
- `AUTHENTIK_POSTGRESQL__CONN_MAX_AGE=0`

### v2.2 — the compose precedence fix (the part that actually makes it work)

The upstream Authentik compose template hardcodes `AUTHENTIK_POSTGRESQL__HOST: postgresql` directly in `services.{server,worker}.environment`:

```yaml
services:
  server:
    environment:
      AUTHENTIK_POSTGRESQL__HOST: postgresql      # ← hardcoded
      AUTHENTIK_POSTGRESQL__USER: ${PG_USER:-authentik}
      AUTHENTIK_POSTGRESQL__PASSWORD: ${PG_PASS}
    env_file:
      - .env
```

**Per Docker Compose semantics, `environment:` takes PRECEDENCE over `env_file:`.** So rewriting `.env` alone — which v1 of this install did — is silently overridden every time those containers are (re)created. The v1 post-install probe missed the bypass because of an IP-mapping assumption error: it thought `172.19.0.3`/`172.19.0.4` were PgBouncer-multiplexed client pools, when in fact they're server/worker connecting **directly** to postgresql (PgBouncer was actually at `172.19.0.7`).

v2.2 fixes this by **also rewriting `services.{server,worker}.environment.AUTHENTIK_POSTGRESQL__HOST` from `postgresql` → `pgbouncer`**. The fix has three parts:

1. **Idempotency gate now reads per-service compose env**, not just `.env`. Only declares "already installed" when compose + `.env` + per-service `environment.AUTHENTIK_POSTGRESQL__HOST` are all wired to `pgbouncer`. Detects the v1 partial-install state on existing dev boxes and reports `PARTIAL INSTALL DETECTED` in the migration log.
2. **Install patches the compose environment** for both `server` and `worker` services (handles both dict and list forms of `environment:`).
3. **Post-install probe records `last_outcome='bypassed'`** (not `'ok'`) if `via_pgbouncer==0 AND direct>0`, logs `✗` instead of `⚠`, and prints the manual remediation command.

### Tak-10 v2.2 evidence

After the v2.2 migration ran on tak-10:

```
=== AUTHENTIK PG CONNECTIONS ===
 client_addr | count
-------------+-------
 172.19.0.7  |     7    ← PgBouncer (THE ONLY non-psql client)
             |     1    ← psql session

=== PGBOUNCER SHOW POOLS ===
 database  | cl_active | sv_active | sv_idle | sv_used | maxwait | pool_mode
-----------+-----------+-----------+---------+---------+---------+-------------
 authentik |    28     |     0     |    3    |    4    |    0    | transaction

=== PGBOUNCER SHOW STATS (over ~60s) ===
 database  | total_query_count | avg_wait_time
-----------+-------------------+---------------
 authentik |  99,823 → 101,094 | 2µs            ← 1,271 queries / min, 2 microsecond avg wait
```

**Multiplexing ratio: 28 client conns → 7 server conns ≈ 4:1.** Under load this would go higher (~10:1). `maxwait=0` means no client is queued waiting for a PG slot — pool sizing is correct. 100k+ queries through PgBouncer in steady state, averaging 2µs wait time per transaction.

### Operator surface

- New API endpoint `GET /api/authentik/pgbouncer` — install status, container state, `SHOW POOLS` / `SHOW STATS` output, `pg_stat_activity` split by `via_pgbouncer` vs `direct`. Powers a dashboard tile.
- `_authentik_channels_pool_watchdog_loop` docstring + alert message updated — PgBouncer is now THE fix; watchdog is defense-in-depth for catastrophic regressions.
- `settings.authentik_pgbouncer.last_outcome` exposes `'ok'` / `'bypassed'` / `'probe-too-early'` / `'probe-degraded'` for dashboard visibility.

---

## Phase 6b — TAK Server connection-state diagnostic (v2, with v1 misdiagnosis kept as a cautionary tale)

### v1 — the misdiagnosis

Phase 6b initially shipped as a "TAK Server zombie subscription" diagnostic + sweep on the working hypothesis that pre-PgBouncer Authentik LDAP-outpost outages left orphaned subscriptions in TAK Server's `DistributedSubscriptionManager` that survived JVM restarts. The hypothesis was based on Tom Andersen's anctakserver2 forensic showing "199 subscriptions, 165 epoch-zero `lastEventTime`, ZERO actively reporting" as the trailing effect of `ak-pg-watchdog` restarts.

**That mental model was wrong.** The endpoint and sweep were built, but never delivered the value they implied. Both are retired in this release.

### v2 — the corrected model

Field forensic on tak-10 the same evening — after PgBouncer landed and the watchdog went quiet — revealed the actual TAK Server data model:

1. **`client_endpoint` is an immortal audit log, not a runtime subscription pool.** Schema has `ON DELETE RESTRICT` FK from `client_endpoint_event` — TAK Server is explicitly preserving the audit trail. Rows persist across JVM restarts. Proven by `sudo systemctl restart takserver` on tak-10: 27 rows pre-restart, 27 rows post-restart, same UIDs.

2. **`client_endpoint_event` records ONLY state transitions** (Connect=type 1, Disconnect=type 2). Two event types. The `created_ts` column tells you when an identity transitioned state, not when it last sent a CoT message.

3. **Marti `/api/clientEndPoints` `lastEventTime: null` means "currently disconnected"**, NOT "zombie". Proven empirically: device `D8985041-...` reported as `lastEventTime: null` at 13:31 PT, **connected one minute later** at 13:32, with 7+ daily connect/disconnect cycles already in the audit log.

4. **`sudo systemctl restart takserver` does NOT clear any of this** — the data is persistent in Postgres. The v1 "sweep" was a no-op.

### What v2 ships

- **`_takserver_connection_state(timeout_s, sample_size)`** helper. Queries the local cot DB directly via `sudo -u postgres psql cot`. No mTLS, no admin cert passphrase, no Marti API. Returns actual state derived from each identity's most recent event row:
  - `currently_connected` / `currently_disconnected`
  - `total_identities` (audit-log scale)
  - `total_events`, `events_last_5min`, `events_last_1h`, `events_last_24h`
  - `earliest_event_utc` / `latest_event_utc`
  - `sample_connected` — top 10 currently-connected clients with callsign/uid/username/since-when
  - **Advisory:** `HEALTHY` / `IDLE` / `QUIET` / `DORMANT` / `INACTIVE`. Never `CRITICAL` or `ATTENTION` from stale-row count alone (the v1/v2.0 bugs).

- **`GET /api/takserver/zombies`** kept as a back-compat alias. **`GET /api/takserver/connection-state`** is the canonical name now.

- **`POST /api/takserver/zombies/sweep`** — returns **410 Gone** with explanation. There is nothing to sweep under the corrected model.

- **`ops/diagnostics/anchortak/zombies.sh` + `zombies.py`** rewritten to query the cot DB via `sudo -u postgres psql` instead of curling Marti. Same v2 output. No cert passphrase required.

### v2.1 advisory correction

The first v2 run on tak-10 fired `ADVISORY: ATTENTION 5 client(s) currently connected BUT no events in last 5 min` on a healthy box. False alarm caused by another mental-model error: **`client_endpoint_event` records state transitions, not CoT traffic** — a stably-connected client generates zero audit rows during its entire session. v2.1 drops the `events_last_5min == 0 → ATTENTION` branch; if `currently_connected > 0`, advisory is `HEALTHY`.

---

## What stayed the same

- `max_connections=500`, `statement_timeout=120s`, `idle_in_transaction_session_timeout=300s` on Authentik Postgres
- `ak-pg-watchdog` retained as defense-in-depth (will fire less often now that PgBouncer caps the ceiling)
- `_patch_authentik_web_max_requests_to_1000` (worker recycling) retained — now serves a memory-bounding role, not a connection-leak workaround
- All v0.9.22 stability fixes (proxy host canonicalization, `idle_session_timeout=0` enforcement)

---

## Verify on a box after Update Now

```bash
# 1) PgBouncer is installed + Authentik routed through it
docker exec authentik-server-1 env | grep AUTHENTIK_POSTGRESQL__HOST
# expected: AUTHENTIK_POSTGRESQL__HOST=pgbouncer
docker exec authentik-worker-1 env | grep AUTHENTIK_POSTGRESQL__HOST
# expected: AUTHENTIK_POSTGRESQL__HOST=pgbouncer

# 2) Actual connections to Authentik PG come only from PgBouncer
PG_PW=$(sudo grep -E '^PG_PASS=' /home/takwerx/authentik/.env | head -1 | cut -d= -f2-)
docker exec -e PGPASSWORD="$PG_PW" authentik-postgresql-1 \
  psql -U authentik -d authentik -c \
  "SELECT client_addr, count(*) FROM pg_stat_activity WHERE datname='authentik' GROUP BY 1;"
# expected: ONE row with client_addr = pgbouncer container IP (172.19.0.X)
#           and count <= 30 (DEFAULT_POOL_SIZE=25 + RESERVE_POOL_SIZE=5)

# 3) PgBouncer is actively multiplexing
docker exec -e PGPASSWORD="$PG_PW" authentik-pgbouncer-1 \
  psql -h 127.0.0.1 -p 5432 -U authentik pgbouncer -c 'SHOW POOLS;'
# expected: row with database=authentik, pool_mode=transaction,
#           cl_active > 0 and sv_active + sv_idle + sv_used > 0,
#           maxwait = 0 (no queued clients)

# 4) Stats are growing — traffic is flowing
docker exec -e PGPASSWORD="$PG_PW" authentik-pgbouncer-1 \
  psql -h 127.0.0.1 -p 5432 -U authentik pgbouncer -c 'SHOW STATS;'
# expected: total_query_count growing over time on the authentik row

# 5) TAK Server connection-state diagnostic (cot DB)
curl -sk -o /tmp/zombies.sh \
  https://raw.githubusercontent.com/takwerx/infra-TAK/main/ops/diagnostics/anchortak/zombies.sh
curl -sk -o /tmp/zombies.py \
  https://raw.githubusercontent.com/takwerx/infra-TAK/main/ops/diagnostics/anchortak/zombies.py
sudo bash /tmp/zombies.sh
# expected: ADVISORY: HEALTHY  (or IDLE / QUIET if no clients connected)
#           — never CRITICAL or ATTENTION from this code path
```

---

## Lessons captured (for the next dev who touches PgBouncer or compose env vars)

1. **`environment:` in docker-compose.yml takes PRECEDENCE over `env_file:`.** Always check `docker exec <container> env | grep <VAR>` against the .env file to confirm what the container actually has. .env rewriting alone is not enough when a compose template hardcodes the same key in `environment:`.

2. **PgBouncer post-install probes must validate by `client_addr`, not `application_name`.** PgBouncer doesn't propagate `application_name` for Authentik 2026.2.x. Resolve PgBouncer's container IP via `docker inspect` first, then group `pg_stat_activity` by `client_addr` and check for that specific IP. Anything else is guessing.

3. **PgBouncer pool stats lie when nothing's using the pool.** `SHOW POOLS` only shows pools that have ever received a client connection. An empty pool with a configured `authentik` database row in `SHOW DATABASES` but no row in `SHOW POOLS` means **no client has ever connected** — i.e. you're being bypassed.

4. **TAK Server's `client_endpoint` and `client_endpoint_event` tables are an audit log, not a runtime pool.** Don't try to derive "live subscription count" or "leaked subscriptions" from them. Marti's `lastEventTime: null` means "currently disconnected", not "abandoned". CoT-routing impairment shows up in `takserver-messaging.log`, not in the audit tables.

5. **A diagnostic that fires `CRITICAL` on a healthy box is worse than no diagnostic.** Validate threshold logic against the real data model before shipping the alarm copy. v2.1 of this release dropped a false-positive `ATTENTION` rule because audit-log silence is the normal steady state for stably-connected clients.

Full forensic trace and code references: [docs/PLAN-v0.9.23-alpha.md](PLAN-v0.9.23-alpha.md).
