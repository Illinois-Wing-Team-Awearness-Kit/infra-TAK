# Authentik Task Table Bloat — Fix

## What happened

Authentik's background task system writes a record to `authentik_tasks_task` and `authentik_tasks_tasklog` on every task run. These tables are never automatically purged. After a month of normal operation they can grow to 500–900 MB (88%+ of the Authentik DB), causing checkpoint and background writer CPU spikes that peg the machine.

Additionally, Docker's default `/dev/shm` for containers is 64 MB. PostgreSQL 16 needs slightly more than that to run `VACUUM ANALYZE`, so you must increase the container's shm size first.

---

## Fix — run these steps in order

### Step 1 — Increase the container shm size

Find the Authentik compose file:

```
find /root -name "docker-compose.yml" 2>/dev/null
```

Open it and add `shm_size: 256m` to the `postgresql` service:

```
services:
  postgresql:
    image: docker.io/library/postgres:16-alpine
    shm_size: 256m
    ...
```

Recreate the container to apply it:

```
cd ~/authentik
docker compose up -d --force-recreate postgresql
```

Wait for it to come back healthy:

```
docker ps | grep postgres
```

---

### Step 2 — Delete task records older than 30 days

Use a heredoc to avoid quote issues:

```
docker exec -i authentik-postgresql-1 psql -U authentik << 'EOF'
DELETE FROM authentik_tasks_tasklog
WHERE task_id IN (
  SELECT message_id FROM authentik_tasks_task
  WHERE mtime < NOW() - INTERVAL '30 days'
);
DELETE FROM authentik_tasks_task
WHERE mtime < NOW() - INTERVAL '30 days';
EOF
```

> **Schema note (Authentik 2026.x):** The task table PK is `message_id` (not `pk`) and the timestamp column is `mtime` (not `finish_timestamp`). The tasklog FK `task_id` references `authentik_tasks_task(message_id)`.

This deletes the bulk of the bloated rows. Safe to run live — no Authentik functionality depends on historical task records.

---

### Step 3 — VACUUM ANALYZE

```
docker exec -i authentik-postgresql-1 psql -U authentik << 'EOF'
VACUUM ANALYZE authentik_tasks_task, authentik_tasks_tasklog;
EOF
```

Takes a few minutes. Watch CPU settle in a second terminal:

```
watch -n2 "ps aux | sort -rk3 | grep postgres | head -5"
```

---

### Step 4 — Verify

```
docker exec -i authentik-postgresql-1 psql -U authentik << 'EOF'
SELECT relname,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       n_live_tup, n_dead_tup,
       last_autovacuum
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 5;
EOF
```

`authentik_tasks_tasklog` and `authentik_tasks_task` should now show sizes in the low MB range. The total size depends on how far back records go — if all records are within 30 days the DELETE is a no-op and the table size won't change (the weekly timer will maintain it going forward).

---

## Why this happened

Authentik runs background tasks continuously (certificate checks, outpost health, policy evaluation, session cleanup, etc.). Each task run writes records to both tables. With no automatic cleanup, these accumulate over weeks until the constant write volume causes heavy checkpoint pressure and background writer CPU spikes.

The `authentik_tasks_tasklog` table had never been autovacuumed because Postgres autovacuum only triggers when dead tuples exceed 20% of live row count — at 1.8M rows that threshold is 360K dead tuples, which it hadn't hit yet. So autovacuum never fired on it despite the table dominating the database.

## Will this come back?

A weekly cleanup timer is planned for v0.9.5 that will run this automatically. Until that ships, you can re-run Steps 2 and 3 manually any time the task tables grow large again.
