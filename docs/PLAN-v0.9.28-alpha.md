# Plan — v0.9.28-alpha

> **Status:** IN DEVELOPMENT
> **Target:** v0.9.28-alpha
> **Scope:** Enterprise-tier Authentik PostgreSQL capacity scaling (12-core / 48 GB minimum hardware).
>
> **Origin:** Field requirement surfaced 2026-05-17 immediately after v0.9.27-alpha merged to `main`. Tom Endress's forensic on `anctakserver2` showed Channels-postgres conns running at 78/85 baseline (92% saturation) on a moderate-load production box — leaving 7 slots of headroom for any burst. v0.9.27's deterministic autotune correctly absorbed test/dev-tier load patterns but its hard ceiling of `60% × 500 max_connections = 300 conns` is undersized for enterprise deployments serving 100s of TAK clients per Authentik instance. The ArcGIS multipart polygon plan that previously held v0.9.28 has been moved to v0.9.29.

---

## Why v0.9.28 exists

infra-TAK is shipped to enterprise TAK customers running 12-core / 48 GB minimum hardware. Each Authentik instance must absorb authentication bursts from 100s of simultaneous TAK clients (each triggers an LDAP bind → outpost WebSocket → flow consumer → cache lookups chain, all hitting PostgreSQL).

Authentik upstream issue [#20714](https://github.com/goauthentik/authentik/issues/20714) (confirmed bug, assigned to `rissson`, targeted at 2026.8.0) is the documented root cause:

> "While upgrading from 2025.12.4 to 2026.2.1, I'm seeing a 1.5-3x increase in postgres connections. ... I had to raise my allowed connection count up from 200 to 500 to get 2026.2.1 to work."

The maintainer (`rissson`) himself identified the exact line of code:

> "In packages/django-channels-postgres/django_channels_postgres/layer.py:174, channel layers are cached per event loop (self._layers[loop]), and each layer can create an async DB pool with `min_size=1, max_size=4`."

That `max_size=4` is **hardcoded** in the vendored library. Each Authentik worker holds 1 LISTEN conn + 4 send-pool conns = 5 Channels conns. With 4 gunicorn workers per Authentik container × 5 service containers (server, worker, ldap, embedded proxy, RAC) = **~100 Channels conns at baseline** before any user activity. Tom's 78-conn observation matches.

Until Authentik 2026.8.0 ships the fix (~6 months out), we need to absorb this with capacity. v0.9.27's 300-conn ceiling is insufficient for the enterprise tier; v0.9.28 raises it to **1500 conns** with maintainer-endorsed mitigations.

---

## What ships

### Item 1 — Postgres command-line tuning bump (the headline)

Replace the v0.9.27 PG command (`max_connections=500`, untuned memory) with a canonical enterprise command:

```
postgres -c max_connections=2000 \
         -c shared_buffers=12GB \
         -c effective_cache_size=36GB \
         -c work_mem=16MB \
         -c maintenance_work_mem=2GB \
         -c wal_buffers=64MB \
         -c max_wal_size=4GB \
         -c statement_timeout=120s \
         -c idle_session_timeout=300s \
         -c idle_in_transaction_session_timeout=300s \
         -c tcp_keepalives_idle=60 -c tcp_keepalives_interval=10 -c tcp_keepalives_count=6
```

**Memory rationale (PG canonical sizing for 48 GB hardware):**
- `shared_buffers=12GB` — 25% of RAM (Postgres canonical guidance)
- `effective_cache_size=36GB` — 75% of RAM
- `work_mem=16MB` per operation
- `maintenance_work_mem=2GB` for vacuum/index builds
- `wal_buffers=64MB` for larger commit batches

**Peak memory budget on a 48 GB box:** ~35-38 GB (12 GB shared_buffers + 1500 backends × ~10 MB + Authentik containers + OS), leaves ~10 GB headroom.

**Single source of truth:** new constant `_AUTHENTIK_PG_COMMAND_ENTERPRISE` referenced by both install-time template AND Update-Now migration paths (legacy text patcher + modern YAML parse-and-mutate). No more drift between 3 inline copies.

### Item 2 — Autotune ceiling raise

| Constant | v0.9.27 | v0.9.28 |
|---|---|---|
| `_AUTHENTIK_POOL_AUTOTUNE_PG_MAX_CONN` | 500 | **2000** |
| `_AUTHENTIK_POOL_AUTOTUNE_CAP_PCT` | 0.60 | **0.75** |
| Effective autotune ceiling | 300 | **1500** |
| `_AUTHENTIK_POOL_AUTOTUNE_COLD_START_DEFAULT` | 250 | **750** |
| `_AUTHENTIK_POOL_AUTOTUNE_COLD_START_RESERVE` | 50 | **150** |
| `_AUTHENTIK_PGBOUNCER_DEFAULT_POOL_SIZE` (floor) | 75 | **300** |
| `_AUTHENTIK_PGBOUNCER_RESERVE_POOL_SIZE` (floor) | 15 | **60** |

Cold-start fleet safe constant 750/150 (= 900-conn ceiling) sits 2.5× above the new floor (360) and 60% of the new cap (1500). Quiet boxes converge downward to ~360; busy boxes hold at 900 or grow toward 1500.

### Item 3 — PgBouncer MAX_CLIENT_CONN bump

| | v0.9.27 | v0.9.28 |
|---|---|---|
| `_AUTHENTIK_PGBOUNCER_MAX_CLIENT_CONN` | 1000 | **5000** |

Enterprise deployments have 100s of Authentik containers + workers + outposts + admin tabs connecting to PgBouncer simultaneously. 1000 was sized for Pi-tier installs. Each PgBouncer client slot costs ~3-5 KB, so 5000 ≈ 25 MB — trivial.

### Item 4 — Fleet-uniform enforcement (remove operator-override carve-out)

The v0.9.27 modern compose patcher had a special case at `_ensure_authentik_compose_patches`:

```python
if _pg_mc_m and _pg_mc_m.group(1).isdigit() and int(_pg_mc_m.group(1)) > 500:
    plog(f"  pg command: operator-set max_connections={...} > 500 — preserving existing command")
```

This violated `.cursor/rules/fleet-uniform-config.mdc` (forbids `max(cur, target)` semantics that fracture the fleet). v0.9.28 removes it — the canonical enterprise command is always written when it differs.

### Item 5 — Channels-baseline telemetry

The `_authentik_pool_autotune_compute()` function now records peak + average Channels-class idle counts (the `query LIKE '%groupchannel%'` filter from `_classify_idle_load()`) into `settings.json → pool_autotune.last_decision`:

```jsonc
{
  "peak_channels_idle": 78,
  "peak_channels_idle_at": "2026-05-17T22:14:00Z",
  "avg_channels_idle": 38.4,
  "samples_with_classes": 30
}
```

Operators can read this at-a-glance to see channels-baseline drift across releases. Forensic groundwork for future tuning decisions (e.g., whether v0.9.29 needs to attack the leak further or whether capacity is holding).

---

## What does NOT ship (and why)

### Patch `layer.py max_size=4→2` in the vendored Authentik library
**Why not:** The maintainer himself flagged this as the leak amplifier — but patching a vendored library inside the Authentik image breaks at every `Update Now` of Authentik upstream. Saves ~40 conns per Authentik instance (real, but small relative to the 1200-conn headroom v0.9.28 delivers). Risk/reward is bad. Re-evaluate in v0.9.29+ only if field validation shows we still hit walls.

### Switch CHANNEL_LAYERS to Redis
**Why not:** Authentik 2026.2.x **hardcodes** `CHANNEL_LAYERS = django_channels_postgres.layer.PostgresChannelLayer` in `/authentik/root/settings.py:328-330`. `channels_redis` and the `redis` Python module are **not even installed** in the image. The `default.yml` `# channel.url: ""` is a DEAD commented line in 2026.2.x — no code reads it. Would require forking the Authentik image, which is high ongoing maintenance for a fix landing upstream in 2026.8.0.

### License-cache pre-warm
**Why not (yet):** Tom's anchortak forensic shows the `enterprise/license` cache lookup dominates 61% of idle conns. Pre-warming via a dramatiq scheduled task could free ~900 conns at peak (61% × 1500 ceiling). BUT this patches Authentik's internal cache-key format, which is not stable across releases. Validation requires source-reading `authentik/enterprise/license.py` per release. Deferred to v0.9.29 only if v0.9.28 field validation shows we still need more headroom.

### Reduce `AUTHENTIK_WEB__WORKERS=4→2`
**Why not:** Halves Authentik HTTP throughput. Wrong direction for enterprise "100s of TAK clients" use case. Worker count directly caps concurrent flow processing.

### Adaptive sizing based on host RAM tier
**Why not:** Operator decision — infra-TAK is documented as requiring enterprise hardware (12-core / 48 GB minimum). Sizing for smaller boxes would mean shipping a less-effective default to all customers to protect the operator-violations.

---

## Migration path

1. **`Update Now`** triggers `_ensure_authentik_compose_patches()` (modern YAML path) on every Authentik install.
2. The patcher detects current compose YAML deviates from `_AUTHENTIK_PG_COMMAND_ENTERPRISE`, rewrites the `postgresql.command:` line, runs `docker compose up -d --force-recreate postgresql`.
3. PostgreSQL restarts (~30s downtime), comes back with new tuning.
4. PgBouncer compose env is rewritten with `MAX_CLIENT_CONN=5000`, also force-recreated (~5s).
5. Autotune fleet-safe constant 750/150 is enforced on next watchdog tick + recorded in `settings.json`.
6. Watchdog dynamic threshold reconciles to `pool_ceiling + 50` = ~450 (was ~150).
7. New Channels-baseline telemetry starts populating in `pool_autotune.last_decision`.

**Per-box estimated downtime:** ~45s on `Update Now`. No data migration.

**Rollback path:** Read `settings.json → pool_autotune.last_decision.version` to find prior values. Revert compose `postgresql.command:` manually if needed.

---

## Acceptance criteria

- [ ] All 3 dev boxes (test6, test8, test12) update to v0.9.28-alpha via `Update Now` and report `VERSION = "0.9.28-alpha"`.
- [ ] PostgreSQL container restarts with `max_connections=2000` + `shared_buffers=12GB` confirmed via `SHOW max_connections;` and `SHOW shared_buffers;` on each box.
- [ ] PgBouncer `MAX_CLIENT_CONN=5000` confirmed via `docker inspect authentik-pgbouncer-1 | grep MAX_CLIENT_CONN`.
- [ ] PgBouncer pool sized to fleet-uniform 750/150 on each box (or autotune-adjusted from samples).
- [ ] Watchdog threshold reconciled to ~450 (pool_ceiling + 50).
- [ ] Soak window ≥ 30 min per box: zero `query_wait_timeout` errors, zero watchdog ALERT fires, all 18 Authentik containers `(healthy)`.
- [ ] `settings.json → pool_autotune.last_decision` shows new `peak_channels_idle` and `avg_channels_idle` fields after one ~6-min watchdog window.
- [ ] No regression in install-time path — fresh installs land at enterprise tuning directly from the compose template.

---

## Test plan

1. **Pre-flight on each dev box:**
   - Confirm baseline: `docker exec authentik-postgresql-1 psql -U authentik -d authentik -tAc "SHOW max_connections;"` → 500 (pre-update)
   - Capture baseline Channels-conn count: `... query LIKE '%groupchannel%'` count

2. **Push to dev branch + commit, run `Update Now` on test6 → test8 → test12 (staggered so I can watch logs)**.

3. **Verify on each box post-update:**
   - `max_connections=2000`, `shared_buffers=12GB`, `effective_cache_size=36GB`, `work_mem=16MB`, `maintenance_work_mem=2GB`, `wal_buffers=64MB`, `max_wal_size=4GB`
   - PgBouncer `MAX_CLIENT_CONN=5000`, `DEFAULT_POOL_SIZE=750`, `RESERVE_POOL_SIZE=150` (or autotune-adjusted)
   - All 18 Authentik containers `(healthy)`
   - `settings.json` shows `pool_autotune.last_decision.version = "0.9.28-alpha"`

4. **Soak 30 min, check for:**
   - Zero `query_wait_timeout` in PG logs
   - Zero `[ak-pg-watchdog] ALERT` lines
   - Zero `Postgres connection is not healthy` warnings
   - No ghost-Channels re-accumulation

5. **Sanity-check resource usage:**
   - `docker stats authentik-postgresql-1` — PG memory usage should rise to ~14-15 GB (shared_buffers + workmem allocations during normal load)
   - `free -h` on host — ample headroom remains

6. **If all 3 boxes pass for 30 min:** selective squash merge dev → main, tag v0.9.28-alpha, push.

---

_Plan authored 2026-05-17 in response to user requirement "this needs to work for large scale deployments" + Tom Endress's anctakserver2 78/85-conn forensic. Hardware tier confirmed by operator: 12-core / 48 GB minimum. Authentik #20714 upstream-tracked at <https://github.com/goauthentik/authentik/issues/20714>._
