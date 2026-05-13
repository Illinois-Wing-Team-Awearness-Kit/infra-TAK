# Postgres Diagnostics — Round 2

## What we found from round 1

There are **two separate Postgres clusters** running on this machine.

### UID 70 — Authentik's containerized Postgres (THE PROBLEM)

| PID | CPU | RAM | Process |
|-----|-----|-----|---------|
| 9112 | **588%** | 2.4 GB | `postgres: background writer` |
| 13279 | 1.1% | idle | `authentik authentik` connection |
| 33592 | 1.0% | idle | `authentik authentik` connection |

The `background writer` moves dirty pages from `shared_buffers` to disk. It should be nearly silent. **588% CPU on a background writer is not normal** — something is generating massive write pressure into Authentik's database.

### UID `postgres` — TAK Server's host Postgres (FINE)

All `martiuser cot` connections are idle. TAK Server's Postgres is healthy and not the cause.

### iostat summary

- Machine is **100% CPU** across all three samples
- Disk (`sda`) at **67–71% utilization** but `iowait ≈ 0%` — this is **pure CPU load, not a disk bottleneck**
- The `%nice` column at 60–70% is the containerized Authentik Postgres processes running at nice priority

### Secondary flag (not the fire right now)

TAK Server Postgres has `max_connections = 2100`. Default is 100–200. At `work_mem = 4 MB` per connection, worst case is 8.4 GB RAM consumed. Address this after the immediate problem is resolved.

---

## What to run next

First, find the exact container name:

```bash
docker ps | grep postgres
```

Then substitute the real name in the commands below (likely `authentik-postgresql-1` or `authentik_postgresql_1`).

---

### 1. What is Authentik's Postgres doing right now?

```bash
docker exec -it authentik-postgresql-1 psql -U authentik -c "
SELECT pid, now() - query_start AS duration,
       state, wait_event_type, wait_event, left(query, 120) AS query
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY duration DESC NULLS LAST;"
```

---

### 2. Table sizes and dead tuple bloat in the Authentik DB

```bash
docker exec -it authentik-postgresql-1 psql -U authentik -c "
SELECT relname,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       n_live_tup, n_dead_tup,
       last_autovacuum
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 10;"
```

High `n_dead_tup` (millions) on any table = autovacuum is churning trying to clean up — that's likely what's driving the background writer CPU.

---

### 3. Authentik DB total size

```bash
docker exec -it authentik-postgresql-1 psql -U authentik -c "
SELECT datname, pg_size_pretty(pg_database_size(datname))
FROM pg_database
ORDER BY pg_database_size(datname) DESC;"
```

---

### 4. Authentik Postgres memory and autovacuum config

```bash
docker exec -it authentik-postgresql-1 psql -U authentik -c "
SELECT name, setting, unit FROM pg_settings
WHERE name IN (
  'shared_buffers', 'work_mem', 'max_connections',
  'autovacuum', 'autovacuum_vacuum_scale_factor',
  'checkpoint_completion_target', 'wal_buffers'
)
ORDER BY name;"
```

---

### 5. Docker memory and CPU stats

```bash
docker stats --no-stream | grep -i postgres
```

---

## What the results will tell us

| Finding | Cause |
|---------|-------|
| Long-running queries in result 1 | Active query storm — need to see what query |
| `autovacuum worker` process in result 1 | Autovacuum churning on dead tuples — likely culprit |
| `n_dead_tup` millions in result 2 | Dead tuple bloat — Authentik event/session tables grew unchecked |
| Authentik DB > 1–2 GB in result 3 | Authentik event log never pruned |
| `shared_buffers` very large in result 4 | Possible checkpoint pressure |

The most likely scenario: Authentik's **event log or session tables** have accumulated millions of dead tuples. Autovacuum is trying to clean them up, driving the background writer to 588% CPU. Once we confirm with results 1 and 2, the fix is either running `VACUUM ANALYZE` manually inside the container or pruning Authentik's event log from the UI.
