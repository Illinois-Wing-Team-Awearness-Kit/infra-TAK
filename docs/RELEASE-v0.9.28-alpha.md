# Release v0.9.28-alpha — Enterprise Authentik PG Scaling

> **Released:** 2026-05-17
> **Branch:** `main` (selective squash merge from `dev`)
> **Tag:** `v0.9.28-alpha`
> **Scope:** Enterprise-tier Authentik PostgreSQL capacity scaling for production-scale TAK deployments running 12-core / 48 GB minimum hardware.

---

## TL;DR

infra-TAK targets enterprise TAK customers. Each Authentik instance must absorb authentication bursts from **100s of simultaneous TAK clients**. v0.9.27's 300-conn pool ceiling — sized for the channels-leak hotfix on small-tier validation boxes — is undersized for that load class.

v0.9.28 raises every relevant ceiling in lockstep with the underlying PostgreSQL capacity, while preserving every v0.9.27 self-healing layer (autotune, cl_waiting demand signal, continuous in-place remediation, ghost-Channels reaper).

| Constant | v0.9.27 | v0.9.28 | Why |
|---|---|---|---|
| PG `max_connections` | 500 | **2000** | Absorb Authentik #20714 channels_postgres leak at production scale |
| PG `shared_buffers` | (unset, default ~128 MB) | **12 GB** | 25% of 48 GB RAM (PG canonical) |
| PG `effective_cache_size` | (unset, default ~4 GB) | **36 GB** | 75% of 48 GB RAM (PG canonical) |
| PG `work_mem` | (unset, default 4 MB) | **16 MB** | Per-operation sort/hash memory |
| PG `maintenance_work_mem` | (unset, default 64 MB) | **2 GB** | Vacuum + index builds |
| PG `wal_buffers` | (unset, default ~4 MB) | **64 MB** | Larger commit batches |
| PG `max_wal_size` | (unset, default 1 GB) | **4 GB** | Checkpoint spacing |
| Autotune `PG_MAX_CONN` | 500 | **2000** | Matches PG bump |
| Autotune `CAP_PCT` | 0.60 | **0.75** | Use 75% of PG cap |
| Autotune effective ceiling | 300 | **1500** | 5× the v0.9.27 cap |
| Cold-start `DEFAULT/RESERVE` | 250/50 | **750/150** | Fleet safe-constant matches new ceiling |
| Pool floor `DEFAULT/RESERVE` | 75/15 | **300/60** | Enterprise boxes never shrink below this |
| PgBouncer `MAX_CLIENT_CONN` | 1000 | **5000** | Enterprise has many containers × workers × outposts simultaneously |

Memory budget on a 48 GB enterprise box at peak: ~35-38 GB (PG `shared_buffers` + 1500 backends × ~10 MB + Authentik containers + OS), leaves ~10 GB headroom. The user has documented enterprise hardware as 12-core / 48 GB minimum.

---

## Root motivation

### Authentik upstream issue #20714 (confirmed bug)

[goauthentik/authentik#20714](https://github.com/goauthentik/authentik/issues/20714) — "Elevated Postgres Connections (1.5-3x from 2025.12.4)". Assigned to maintainer `rissson`. Targeted for **2026.8.0**, ~6 months out. Reporter:

> "While upgrading from 2025.12.4 to 2026.2.1, I'm seeing a 1.5-3x increase in postgres connections. ... I had to raise my allowed connection count up from 200 to 500 to get 2026.2.1 to work."

The maintainer himself identified the exact source:

> "In `packages/django-channels-postgres/django_channels_postgres/layer.py:174`, channel layers are cached per event loop (`self._layers[loop]`), and each layer can create an async DB pool with `min_size=1, max_size=4`."

That `max_size=4` is hardcoded inside the vendored library. Each Authentik gunicorn worker holds:
- 1 LISTEN connection (for receiving Channels messages)
- 4 send-pool connections

So 4 workers × 5 conns × 5 service containers (server, worker, ldap, embedded proxy, RAC) ≈ **100 Channels conns at baseline** before any user activity.

Until 2026.8.0 ships the fix, the maintainer-endorsed mitigation (per the #20714 thread) is to **raise capacity**. v0.9.28 implements that.

### Tom Endress's `anctakserver2` field forensic

Real-world capture, 2026-05-17 20:31 UTC, moderate-load production:

> "Look at the smoking gun in cap2's grouped query output: `78 connections | idle | SELECT DISTINCT django_channels_postgres_groupchannel.channel FROM ...`. 78 connections all running the same query, but it's NOT _fetch_pending_messages this time. It's `django_channels_postgres_groupchannel`. ... Authentik 2026.2.2 uses django-channels-postgres for its WebSocket channel layer instead of Redis. Every active WebSocket subscriber (admin UI tabs, outpost real-time connections, flow consumers, notification listeners) holds a long-lived PostgreSQL connection for LISTEN."

Pool saturation at the time of capture: **78 of 85** (= 92%), only 7 slots of burst headroom. v0.9.27's `300`-conn ceiling would absorb this baseline but a 100-TAK-client burst would not fit.

v0.9.28 sized for that load class with deliberate 5× headroom.

---

## What ships

### Item 1 — Postgres command-line tuning (the headline)

New constant `_AUTHENTIK_PG_COMMAND_ENTERPRISE` at the top of `app.py`:

```python
_AUTHENTIK_PG_COMMAND_ENTERPRISE = (
    'postgres'
    ' -c max_connections=2000'
    ' -c shared_buffers=12GB'
    ' -c effective_cache_size=36GB'
    ' -c work_mem=16MB'
    ' -c maintenance_work_mem=2GB'
    ' -c wal_buffers=64MB'
    ' -c max_wal_size=4GB'
    ' -c statement_timeout=120s'
    ' -c idle_session_timeout=300s'
    ' -c idle_in_transaction_session_timeout=300s'
    ' -c tcp_keepalives_idle=60'
    ' -c tcp_keepalives_interval=10'
    ' -c tcp_keepalives_count=6'
)
```

**Single source of truth.** This is referenced by both the install-time compose template (in fresh installs) AND both Update-Now migration paths:
- `_ensure_authentik_compose_patches_legacy` (text-based fallback patcher)
- `_ensure_authentik_compose_patches` (modern YAML parse-and-mutate)

No more drift between 3 inline copies as we had in v0.9.27.

Memory rationale per PG canonical guidance for 48 GB hardware:
- `shared_buffers = 12 GB` (25% of RAM)
- `effective_cache_size = 36 GB` (75% of RAM)
- `work_mem = 16 MB` per operation
- `maintenance_work_mem = 2 GB` for vacuum/index builds
- `wal_buffers = 64 MB`, `max_wal_size = 4 GB` for commit-batching

### Item 2 — Autotune ceiling raise

All autotune constants raised together:

```python
_AUTHENTIK_POOL_AUTOTUNE_PG_MAX_CONN = 2000   # was 500
_AUTHENTIK_POOL_AUTOTUNE_CAP_PCT = 0.75       # was 0.60
_AUTHENTIK_POOL_AUTOTUNE_COLD_START_DEFAULT = 750   # was 250
_AUTHENTIK_POOL_AUTOTUNE_COLD_START_RESERVE = 150   # was 50
_AUTHENTIK_PGBOUNCER_DEFAULT_POOL_SIZE = 300  # was 75 (also FLOOR_DEFAULT)
_AUTHENTIK_PGBOUNCER_RESERVE_POOL_SIZE = 60   # was 15 (also FLOOR_RESERVE)
_AUTHENTIK_PGBOUNCER_MAX_CLIENT_CONN = 5000   # was 1000
```

Effective autotune behavior:

```
   FLOOR (quiet)          COLD-START (escape)        CAP (sustained burst)
       │                          │                          │
      360 ───────────── 900 ────────────────────── 1500
       │                                                     │
       └── pool floor                            ────────────┘
                                                 75% × 2000 PG_MAX_CONN
```

Quiet enterprise box → autotune to FLOOR (still 4× v0.9.27 floor of 90).
Channels-heavy box (cl_waiting > 0 in samples) → stuck-at-floor escape forces cold-start 900.
Sustained burst → autotune grows toward CAP 1500.

### Item 3 — PgBouncer MAX_CLIENT_CONN reconciliation

`_ensure_authentik_pgbouncer_pool_size()` now includes `MAX_CLIENT_CONN` in its `matches_target` check + writes the fleet constant on compose mutation:

```python
matches_target = (
    (cur_default == target)
    and (cur_reserve == target_reserve)
    and (cur_max_client == target_max_client)  # v0.9.28-alpha
)
```

Without this, a v0.9.27 → v0.9.28 update on a box whose pool size didn't drift (which is most boxes — they're at autotune-floor) would never recreate pgbouncer, and `MAX_CLIENT_CONN` would stay stuck at the v0.9.27 value of 1000. This was discovered during field validation when test6 and test12 came up at `MAX_CLIENT_CONN=1000` after the initial v0.9.28 update.

### Item 4 — Channels-baseline telemetry

`_authentik_pool_autotune_compute()` now scans samples for Channels-class idle count (which `_authentik_pool_autotune_sample()` already persists as a flat `channels` key, alongside `dramatiq`, `cache`, `advisory_lock`, `other`). New fields in `settings.json → pool_autotune.last_decision`:

```jsonc
{
  "peak_channels_idle": 85,
  "peak_channels_idle_at": "2026-05-18T00:47:07Z",
  "avg_channels_idle": 7.1,
  "samples_with_classes": 30
}
```

Field-validated on test8 (Channels-heavy production-like load):
```
peak_channels_idle: 85 @ 2026-05-18T00:47:07Z
avg_channels_idle: 7.1 (over 30 samples)
```

Test6: `peak=79, avg=3.0`. Test12: `peak=1, avg=0.0` (genuinely quiet).

Telemetry applied on BOTH the normal compute path AND the stuck-at-floor escape path, so Tom-class boxes that hit the escape still get the forensic data.

### Item 5 — Fleet-uniform enforcement (removed operator-override carve-out)

v0.9.27's modern compose patcher had a special case:

```python
# v0.9.27 — REMOVED in v0.9.28
if _pg_mc_m and _pg_mc_m.group(1).isdigit() and int(_pg_mc_m.group(1)) > 500:
    plog(f"  pg command: operator-set max_connections={...} > 500 — preserving existing command")
```

This violated `.cursor/rules/fleet-uniform-config.mdc`. An operator who set `max_connections=600` in v0.9.27 would never get the v0.9.28 memory params (12 GB shared_buffers, etc.). v0.9.28 removes the carve-out — the canonical enterprise command is always written when it differs. Drift detection updated to consider lack of `shared_buffers` tuning as drift (any pre-v0.9.28 install).

---

## What does NOT ship (and why)

| Alternative | Why deferred |
|---|---|
| Patch vendored `layer.py max_size=4 → 2` | Fragile (~40-conn savings, breaks at every Authentik upstream Update Now since the library is inside the image). Re-evaluate in v0.9.29+ only if field shows we still hit walls. |
| Switch CHANNEL_LAYERS to Redis | Authentik 2026.2.x **hardcodes** `CHANNEL_LAYERS = django_channels_postgres.layer.PostgresChannelLayer` in `/authentik/root/settings.py:328-330`. `channels_redis` and the `redis` Python module are not installed in the image. Would require image fork — high ongoing maintenance for a fix landing upstream in 2026.8.0. |
| License-cache pre-warm | Tom's anctakserver2 forensic showed `enterprise/license` cache lookups dominate 61% of idle conns. Pre-warming could free ~900 conns at peak, but patches Authentik's internal cache-key format which is not stable across releases. Deferred to v0.9.29 only if v0.9.28 still shows under-sizing. |
| `AUTHENTIK_WEB__WORKERS=4 → 2` | Halves Authentik HTTP throughput. Wrong direction for enterprise "100s of TAK clients" use case. |
| Adaptive sizing based on host RAM | Operator decision — infra-TAK is documented as requiring enterprise hardware (12-core / 48 GB minimum). Sizing for smaller boxes would mean shipping a less-effective default to all customers. |

---

## Field validation evidence

**3 dev boxes:** test6 (responder), test8, test12. **3 commits to dev:**

```
32131ef v0.9.28-alpha: enterprise Authentik PG scaling (12-core / 48GB tier)
a01dc6e v0.9.28-alpha hotfix #1: reconcile MAX_CLIENT_CONN in pool migration
28d0f8c v0.9.28-alpha hotfix #2: read channels-baseline from flat sample key
```

Both hotfixes were discovered during the same validation sequence (not separate incidents):

- **Hotfix #1** caught when initial validation showed all boxes had `MAX_CLIENT_CONN=1000` despite the new constant being 5000. Root cause: pool-size-match short-circuited the pgbouncer recreate. Fix: include `MAX_CLIENT_CONN` in `matches_target`.
- **Hotfix #2** caught when initial validation showed all decisions had `peak_channels_idle=0, samples_with_classes=0`. Root cause: my code looked for `smp.get('classes')` (a tuple), but `_authentik_pool_autotune_sample()` persists `channels` as a flat key. Fix: read `smp['channels']` directly.

Final state across all 3 boxes:

```
=== test6 (responder — moderate prior load) ===
  version: 0.9.28-alpha
  pool target: 300 / 60 (ceiling: 360 — autotune-floor for quiet load)
  peak_channels_idle: 79 @ 2026-05-18T00:46:54Z
  avg_channels_idle: 3.0 (over 30 samples)
  PG: max_connections=2000, shared_buffers=12GB, effective_cache_size=36GB,
      work_mem=16MB, maintenance_work_mem=2GB, wal_buffers=64MB, max_wal_size=4GB
  PgBouncer: DEFAULT=300, RESERVE=60, MAX_CLIENT_CONN=5000
  All 18 Authentik containers (healthy)
  query_wait_timeout last 10m: 0

=== test8 (Channels-heavy — prior cl_waiting > 0) ===
  version: 0.9.28-alpha
  pool target: 750 / 150 (ceiling: 900 — stuck-at-floor escape fired correctly)
  peak_channels_idle: 85 @ 2026-05-18T00:47:07Z
  avg_channels_idle: 7.1 (over 30 samples)
  peak_cl_waiting: 1
  PG: enterprise tuning verified
  PgBouncer: DEFAULT=750, RESERVE=150, MAX_CLIENT_CONN=5000
  All 18 Authentik containers (healthy)
  query_wait_timeout last 10m: 0
  reason: "stuck-at-floor escape: peak_cl_waiting=1 > 0 (30/30 samples had
           cl_waiting telemetry); pool is under-sized regardless of idle-count
           peak (168); forcing fleet safe-constant 750/150 (ceiling=900)"

=== test12 (genuinely quiet) ===
  version: 0.9.28-alpha
  pool target: 300 / 60 (ceiling: 360)
  peak_channels_idle: 1 @ 2026-05-18T00:35:06Z
  avg_channels_idle: 0.0
  PG: enterprise tuning verified
  PgBouncer: DEFAULT=300, RESERVE=60, MAX_CLIENT_CONN=5000
  All 18 Authentik containers (healthy)
  query_wait_timeout last 10m: 0
```

**Key observations:**

1. **test8's `peak_channels_idle=85` directly mirrors Tom Endress's anctakserver2 observation (78/85 conn baseline at production scale).** v0.9.28's ceiling of 1500 leaves ~1415 slots of burst headroom on top of that baseline.
2. **Pool sizing is fleet-uniform per observed load class** — quiet boxes (test6, test12) converge to autotune floor 300/60; Channels-heavy boxes (test8) get cold-start fleet safe-constant 750/150 via stuck-at-floor escape. Two boxes with the same load produce the same config. No fracture.
3. **Zero `query_wait_timeout`** across all 3 boxes during the entire validation window.
4. **All v0.9.27 self-healing preserved** — cold-start, cl_waiting demand signal, continuous in-place remediation, ghost-Channels reaper all still functional.

---

## Migration path

`Update Now` on existing installs runs:

1. **VERSION bump** detection (0.9.27-alpha → 0.9.28-alpha).
2. **`_ensure_authentik_compose_patches()`** detects deviation from `_AUTHENTIK_PG_COMMAND_ENTERPRISE`, rewrites `postgresql.command:` line in `~/authentik/docker-compose.yml`, runs `docker compose up -d --force-recreate postgresql`. PG restarts (~30s downtime), comes back with all new tuning.
3. **`_ensure_authentik_pgbouncer_pool_size()`** runs autotune compute over carried-over v0.9.27 samples, computes target, detects `MAX_CLIENT_CONN` drift, rewrites compose env, recreates pgbouncer (~5s).
4. **Autotune decision recorded** in `settings.json → pool_autotune.last_decision` with v0.9.28 telemetry fields (peak_channels_idle, avg_channels_idle, samples_with_classes).
5. **Watchdog threshold reconciled** to `pool_ceiling + 50` (e.g., 410 for floor boxes, 950 for cold-start boxes).

**Per-box estimated downtime:** ~45-60s on `Update Now`. No data migration.

**Rollback path:** Read `settings.json → pool_autotune.last_decision.version` to find prior values. Revert compose `postgresql.command:` manually if needed.

---

## Lessons learned

### 1. "The simplest path is to add capacity"
A 5× capacity headroom multiplier (300 → 1500) was simpler and more robust than the alternatives we considered (patching vendored libraries, switching channel backends, pre-warming caches). For a fix landing upstream in 6 months, the right move is to wait it out comfortably rather than introduce fragility.

### 2. "Always read the upstream issue thread"
Per `.cursor/rules/consult-upstream-docs.mdc`, the first move was reading Authentik #20714 in full — including the maintainer's `layer.py:174` pin and his explicit endorsement of capacity bumps as the interim mitigation. Without that, we might have tried to patch the vendored library directly and shipped a v0.9.28 that breaks at every Authentik Update Now. The official thread saved us from that.

### 3. "Field validation finds bugs that local testing can't"
Hotfix #1 (MAX_CLIENT_CONN reconciliation) and hotfix #2 (channels sample key name) were both invisible until we actually ran the migration on dev boxes and inspected the result. The pool-size-match short-circuit logic had been working correctly for v0.9.27 — it just happened to skip an enterprise-tier addition that didn't exist in v0.9.27.

### 4. "Single source of truth eliminates drift"
The v0.9.27 codebase had the PG command line written as a literal string in 3 places (install template, legacy patcher, modern patcher) — kept in sync by hand. Defining `_AUTHENTIK_PG_COMMAND_ENTERPRISE` and referencing it from all 3 places means future memory-tuning bumps touch one constant instead of three literals.

---

_Released 2026-05-17. Cites Authentik upstream issue #20714 (assigned to `rissson`, target 2026.8.0) and Tom Endress's `anctakserver2` field forensic (78/85-conn baseline). Hardware tier confirmed by operator: 12-core / 48 GB minimum for enterprise TAK deployments serving 100s of simultaneous clients._
