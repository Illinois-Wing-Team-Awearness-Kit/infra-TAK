# AnchorTAK Authentik PostgreSQL Connection Accumulation — Technical Analysis

**Subject:** Root-cause analysis of idle PostgreSQL connection accumulation in Authentik 2026.2.3 under infra-TAK v0.9.22
**For:** infra-TAK development team
**Reporter:** AnchorTAK operations (anctakserver2)
**Date:** 2026-05-15
**Status:** Production stable on v0.9.22 via watchdog; upstream Authentik bug confirmed (goauthentik/authentik#20714)

---

## 1. TL;DR

90 minutes of 5-second-interval production data on `anctakserver2` (Lenovo M715q, Ryzen 5 PRO, Authentik 2026.2.3 + infra-TAK v0.9.22) shows that idle PostgreSQL connections in the `authentik` database accumulate from ~7 to ~180 over roughly 18-minute cycles and are then truncated to ~7 by the infra-TAK watchdog. Across 1,056 main samples and 176 detail snapshots:

- **Source is isolated:** `authentik-server-1` only. Average 88.2 idle connections (peak 179). `authentik-worker-1` is stable at 7 ± 2 throughout — it is **not** contributing.
- **Query class is identified:** The `enterprise/license` cache lookup dominates at **avg 61.3 idle / range 0–146** — i.e. ~64% of all idle connections at any moment are sitting on this single query. `django_channels_postgres` LISTEN sockets are flat at 2–3 and are statistically irrelevant.
- **CONN_MAX_AGE=10 is ineffective on this code path:** 79.6% of idle connections are older than 60 seconds; 37.8% are older than 5 minutes. With `CONN_MAX_AGE=10` set, well-behaved Django persistent connections should not exceed 10 seconds of idleness — so these connections are not being recycled by Django's request-end teardown hook.
- **This matches upstream issue [goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714)** ("2026.2.x Elevated Postgres Connections (1.5-3x from 2025.12.4)") — confirmed bug (label `bug/confirmed`), assigned to `rissson` (Authentik core), still open as of writing. The reporter's symptoms are identical to ours: had to raise `max_connections` from 200 to 500 to keep 2026.2.1 from falling over.

**v0.9.22's two design decisions are validated by this data:** (1) removing `idle_session_timeout=30s` was correct — only 2–3 LISTEN sockets exist at any time, so killing all idle sessions every 30s was destroying long-lived channels infrastructure for no proportional benefit; (2) the targeted connection watchdog is the right shape of mitigation — it caps the ceiling without disturbing healthy long-lived sockets.

---

## 2. What these connections actually are

To answer the central question — *what is being created, what is it for, why doesn't it go away?* — it helps to understand what changed in Authentik 2025.10. Authentik used to use Redis for cache, sessions, websocket channels, and the embedded outpost session store. In the 2025.10 release [Authentik removed Redis entirely](https://goauthentik.io/blog/2025-11-13-we-removed-redis/) and rerouted all of those subsystems through PostgreSQL. From their own release notes: *"As a result of this change, it is expected that authentik will use roughly 50% more database connections to Postgres."*

So as of 2025.10+ — and we are on 2026.2.3 — every Authentik server process is now talking to PostgreSQL through several **independent** connection-using subsystems, each with its own lifecycle policy:

| Subsystem | What it does | Lifecycle | Bound by `CONN_MAX_AGE`? |
|---|---|---|---|
| **Django ORM (sync)** | Standard model queries from HTTP request handlers | One connection per worker thread, recycled at request end | Yes |
| **`django_postgres_cache`** | Replaces Redis as the cache backend. Used by sessions, license cache, policy cache, flow plan cache, reputation cache, SCIM cache, etc. | Connection used per cache operation | **Unclear in practice — see below** |
| **`django_channels_postgres`** | Replaces Redis for websocket channel layer (LISTEN/NOTIFY) | **Must stay open** to receive NOTIFY events | No — these are designed to be persistent |
| **`django_dramatiq_postgres`** | Replaces Celery+Redis for background tasks (worker container) | Persistent LISTEN on task queue | No — designed persistent |
| **Outpost session store** | Replaces Redis for embedded proxy outpost sessions | Per-request | Yes |

`CONN_MAX_AGE=10` (the value v0.9.22 sets) only governs the **first** category — Django's standard `django.db.backends.postgresql` ORM connections that get attached at the start of a request and detached at the end. The other categories use their own connection pools or open their own raw psycopg connections, and Django's `close_old_connections()` signal at request end doesn't touch them.

---

## 3. What is creating the bulk of the connections

The detail snapshot data isolates this very cleanly. Across 176 snapshots:

| Query class | Avg idle | Range | What it is |
|---|---:|---|---|
| `enterprise/license` cache | **61.3** | 0–146 | The license summary cache lookup |
| `COMMIT` | 18.5 | 0–46 | Transaction commits sitting in idle state after the work that triggered them completed |
| `LISTEN channels_*` | 2.4 | 0–3 | django_channels_postgres websocket subscribers |
| Other cache | ~13 | — | Sessions, policy cache, flow plan cache, etc. |
| Empty/null query | 0 | — | (none observed) |

The dominant population is the `enterprise/license` cache check. We can trace exactly where this comes from in the Authentik source — a stack trace from issue [#17343](https://github.com/goauthentik/authentik/issues/17343) shows the exact call chain that runs on **every HTTP request** (including the `/-/health/live/` healthcheck):

```
authentik/events/middleware.py:154          __call__
  → authentik/enterprise/audit/middleware.py:29   connect
    → authentik/enterprise/audit/middleware.py:25  enabled
      → authentik/enterprise/apps.py:24            enabled
        → authentik/enterprise/apps.py:30          check_enabled
          → authentik/enterprise/license.py:234    cached_summary
```

`cached_summary()` does a `cache.get(...)` against the `default` cache. In Authentik's settings.py the `default` cache backend is `django_postgres_cache.backend.DatabaseCache`. So every HTTP request — every API call, every health probe, every websocket upgrade, every static asset request that hits the server proxy — does a PostgreSQL query to look up the license summary.

The 4 gunicorn worker threads in `authentik-server-1` (sized by Authentik's default formula `ceil(cores/4)+1` workers × default threads-per-worker on a 4-core Ryzen 5 PRO 2400GE) each maintain their own connection. Multiply by the cache backend's own pool, plus inflight COMMITs from those cache writes when freshness checks fire, plus the persistent LISTEN sockets, plus session-store traffic, and a steady-state of 60–180 idle connections is exactly the shape we'd predict.

---

## 4. Why they accumulate — the actual mechanism

For each connection class, here's what *should* happen versus what we observe:

**Standard ORM connections.** Django opens a connection at the start of a request, lets the view code use it, and at request end runs `close_old_connections()` which either closes the connection (if `CONN_MAX_AGE=0`) or keeps it alive for reuse up to `CONN_MAX_AGE` seconds (we've set 10). This is well-behaved. Connections in this class would show ages clustered under 10 seconds.

**`django_postgres_cache` connections.** This is where the picture changes in 2026.2.x. The cache backend has its own connection-acquisition logic — it doesn't necessarily use the same persistent connection the request started with. When the audit middleware calls `cache.get("enterprise_license_summary_*")` and the result misses, the cache backend opens or borrows a connection, does a `SELECT ... FROM django_postgres_cache_cacheentry WHERE ...`, and then returns the result. The connection goes back to **idle** state. It is not necessarily closed at request end because it was never registered as a request-scoped Django connection.

This is the connection class we observe accumulating. The evidence:

- **Age distribution is wrong for a 10-second TTL.** If `CONN_MAX_AGE=10` were governing these, the >60s bucket would be near zero. We see 79.6% over 60s and 37.8% over 5 minutes.
- **The dominant query text is the license cache lookup**, not any user-facing API or any ORM-shaped query.
- **The server container is the only contributor.** The worker is stable at 7 connections because it doesn't run the audit middleware on its dramatiq tasks — only HTTP request handlers do.

**LISTEN sockets.** These are *supposed* to stay open — that's the whole point of LISTEN/NOTIFY. If you kill them, the application stops receiving websocket events. There are only 2–3 of them at any time in our data, which is consistent with the very small number of websocket subscribers the system actually needs (admin UI, etc.). They are not the problem and v0.9.21's blanket `idle_session_timeout=30s` was killing them every 30 seconds, which is exactly the regression v0.9.22 corrected.

---

## 5. Why this is happening NOW (and not before 2026.2)

Three architectural changes stack up:

1. **2025.8** — background tasks moved from Celery+Redis to `django_dramatiq_postgres`. First wave of PostgreSQL connection growth.
2. **2025.10** — *everything* else (cache, sessions, websockets, outpost) moved off Redis to PostgreSQL. Authentik documented this as expected to use "roughly 50% more database connections."
3. **2026.2** — additional architectural changes (still being characterized upstream) push connection use 1.5–3× higher than 2025.12.4. This is the regression captured in [#20714](https://github.com/goauthentik/authentik/issues/20714).

The reporter on #20714 — a Kubernetes user with CloudNative-PG — describes the exact same operational symptom we have:

> *"While upgrading from 2025.12.4 to 2026.2.1, I'm seeing a 1.5-3x increase in postgres connections. ... I had to raise my allowed connection count up from 200 to 500 to get 2026.2.1 to work. With my setup on 2025.12.4 I rarely passed 100 connections in the last 30d. On 2026.2.1 its anywhere between 150-300."*

Our data is a near-perfect match: avg 95, peak 186, would saturate a 200-connection PostgreSQL without the watchdog or the 500-connection ceiling.

The issue carries the `bug/confirmed` label and is assigned to `rissson`, who is one of Authentik's core developers and the author of much of the PostgreSQL-cache migration. As of writing, no fix has been released. Authentik 2026.2.3 (current latest stable) does not address it.

---

## 6. Why v0.9.22's design choices are correct

The data validates both decisions taken in v0.9.22:

**Removing `idle_session_timeout=30s` was correct.** v0.9.21 added a server-side `idle_session_timeout=30s` aimed at culling idle connections. But that setting is blind — it kills *every* idle session, including the LISTEN sockets that `django_channels_postgres` needs to stay subscribed to. Our data shows only 2–3 LISTEN sockets at any time, so the cost of killing them every 30 seconds (reconnect storms, CPU spikes, "Postgres connection is not healthy" warnings — exactly what was seen on tak-10 and exactly what upstream [#18453](https://github.com/goauthentik/authentik/issues/18453) reports for 2025.10+) far outweighs the benefit. v0.9.22 was right to back this out.

**The targeted connection watchdog is the right shape.** Across the 90-minute capture, the watchdog fired 5 times, each at ~150-180 idle, dropping the count back to ~7. It cycles cleanly every 15–20 minutes and the system stays well clear of the 500-connection ceiling. CPU spikes during watchdog fires are 14–25% for a few seconds, much less disruptive than the every-30s reconnect storm v0.9.21 produced. Critically, the watchdog only acts when the *count* crosses a threshold — it does not blindly kill connections by age, so LISTEN sockets are preserved unless the system is actually about to saturate.

**What v0.9.22 retained is also right** — `max_connections=500`, `statement_timeout=120s`, `idle_in_transaction_session_timeout=300s`, `CONN_MAX_AGE=10`. None of these hurt; `CONN_MAX_AGE=10` does still work for the ORM-connection class that respects it, even if it doesn't reach the cache-backend class. And `idle_in_transaction_session_timeout=300s` is a useful safety net for an entirely different failure mode (abandoned transactions holding row locks).

---

## 7. What the data does NOT support

A few earlier hypotheses we should explicitly rule out:

- **It is NOT django_channels_postgres LISTEN sockets.** Only 2.4 average. Killing them is harmful (see v0.9.21 regression).
- **It is NOT request-driven bursts.** The accumulation rate is smooth and steady (~1 conn per 5-10s), not bursty. This means external traffic isn't the driver — it's an internal cleanup deficit.
- **It is NOT an infra-TAK regression.** Connection counts and accumulation pattern match the upstream issue exactly. infra-TAK only inherited the problem when it shipped Authentik 2026.2.3.
- **It is NOT the worker container.** The worker holds 7 idle connections continuously and never grows. Whatever is leaking is in the server container's HTTP handling path, not the dramatiq background-task path.

---

## 8. Recommendations for v0.9.23+

**Priority 1 — Track upstream.** Subscribe to [#20714](https://github.com/goauthentik/authentik/issues/20714). When Authentik ships a fix, plan a coordinated upgrade. Consider adding our capture data as a comment on that issue — our query-class breakdown and age-distribution analysis is more specific than what's currently posted there and would help the maintainer narrow the fix.

**Priority 2 — Keep the watchdog, consider one refinement.** The current threshold-based watchdog works. A useful refinement would be to switch from "fire at fixed count" to "fire when slope predicts saturation before next check interval" — this would smooth out the sawtooth and reduce the periodic CPU spike. Not urgent given current behavior is stable.

**Priority 3 — Surface `AUTHENTIK_WORKER__THREADS` / `WEB_CONCURRENCY` as tunables.** Per Authentik's gunicorn config, the server runs `ceil(cores/4)+1 = 2 workers` on this 4-core machine, with the default thread count per worker. Each thread holds an independent connection. Exposing these as environment variables in the infra-TAK compose template would let smaller deployments (single-tenant, low-traffic) reduce their connection ceiling without code changes. Recommended defaults for low-traffic single-server installs: 2 workers × 4 threads, plus the cache pool, lands the steady-state ceiling well below 100 connections.

**Priority 4 — Optional: pre-warm the license cache.** Since `cached_summary()` is the dominant query and the result is *static* for unlicensed/Community installs (always returns `status: "unlicensed"`), a periodic `cache.set()` from a dramatiq scheduled task with a long TTL would convert most cache lookups from cache-miss-falls-through-to-DB into cache-hit-no-DB-query. This is a possible workaround we could implement at the infra-TAK level without waiting for upstream — but it should be validated against `enterprise/license.py` source first to confirm the cache key format and TTL semantics, and it's a workaround for an upstream bug, not a fix.

**Priority 5 — Optional: surface watchdog activity in the Authentik web UI / system events.** Right now operators learn about watchdog fires from Postgres logs and the monitor script. A system event ("infra-TAK PostgreSQL connection watchdog triggered: dropped from 178 to 7") would help less-technical operators understand what's happening without spelunking.

---

## 9. Reference data summary (90-min capture, 2026-05-15 05:37 → 07:08 UTC)

**Connection counts:** min 7, max 186, avg 95.1 across 1056 samples.

**By container** (out of avg 95.1 total):
- `authentik-server-1` (172.18.0.4): avg 88.2, range 0–179 — **source**
- `authentik-worker-1`  (172.18.0.5): avg 7.0, range 5–9 — stable, not contributing

**By query class** (out of avg 95.1):
- `enterprise/license` cache: **avg 61.3, range 0–146 — dominant**
- COMMIT (post-transaction idle): avg 18.5, range 0–46
- LISTEN channels_*: avg 2.4, range 0–3
- empty/null: 0
- Remaining (~13) distributed across other cache lookups, sessions, etc.

**By connection age** (proves CONN_MAX_AGE=10 not effective on dominant class):
- <10s:  5.5%
- 10–30s: 7.6%
- 30–60s: 7.4%
- 1–5min: 41.8%
- >5min: 37.8%
- **Total over 60s: 79.6%**

**Watchdog activity:** 5 fires in 90 minutes (cycle ~15–20 min). CPU during watchdog fire: 14–25% for ~15 seconds. CPU during high-connection accumulation phase: 21–49% spikes likely correlated with healthcheck-driven license cache lookups against full pool.

**Comparison to upstream #20714 reporter:** Reports 150–300 connections sustained. Our peak 186 sits inside that range. Their workaround was raising `max_connections` 200→500. Ours is the same value (500) plus active watchdog mitigation. Behavior is consistent.

---

## 10. References

- [goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714) — Confirmed upstream bug, current
- [goauthentik/authentik#18453](https://github.com/goauthentik/authentik/issues/18453) — django_channels_postgres healthy-connection warnings on 2025.10+
- [goauthentik/authentik#20644](https://github.com/goauthentik/authentik/issues/20644) — Related: idle connection accumulation, user enabled idle_session_timeout as workaround
- [Authentik blog: We removed Redis](https://goauthentik.io/blog/2025-11-13-we-removed-redis/) — Context for the 2025.10 architectural change
- [Authentik 2025.10 release notes](https://docs.goauthentik.io/releases/2025.10/) — "Expected to use roughly 50% more database connections"
- [Authentik configuration docs — CONN_MAX_AGE](https://docs.goauthentik.io/install-config/configuration/#authentik_postgresql__conn_max_age) — Documentation of the setting we already have configured
- AnchorTAK capture files: `anchortak_main_20260515_053721.csv`, `anchortak_detail_20260515_053721.csv`
