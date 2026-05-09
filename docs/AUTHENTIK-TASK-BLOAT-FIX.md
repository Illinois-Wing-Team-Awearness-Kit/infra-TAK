# Authentik Task Table Bloat — Fix

## What happened

Authentik's background task system (LDAP sync, policy evaluation, etc.) writes a record to `authentik_tasks_task` and `authentik_tasks_tasklog` on every task run. At the default 5-minute LDAP sync interval, one month of operation generates hundreds of thousands of rows. These tables had grown to:

| Table | Size | Rows |
|---|---|---|
| `authentik_tasks_tasklog` | 492 MB | 1,806,861 |
| `authentik_tasks_task` | 391 MB | 538,780 |

Combined: **883 MB — 88% of the entire Authentik database.** The `authentik_tasks_tasklog` table had never been vacuumed. The constant heavy writes caused checkpoint and background writer pressure that pegged the machine.

The Authentik container restart temporarily relieved the pressure. These steps clear the backlog so it doesn't come back.

---

## Fix — run these four commands in order

### Step 1 — VACUUM to clear dirty pages from the restart

```bash
docker exec -it authentik-postgresql-1 psql -U authentik -c 'VACUUM ANALYZE;'
```

Takes a few minutes. You can watch CPU settle in a second terminal:

```bash
watch -n2 "ps aux | sort -rk3 | grep postgres | head -5"
```

---

### Step 2 — Delete task records older than 30 days

```bash
docker exec -it authentik-postgresql-1 psql -U authentik -c "
DELETE FROM authentik_tasks_tasklog
WHERE task_id IN (
  SELECT pk FROM authentik_tasks_task
  WHERE finish_timestamp < NOW() - INTERVAL '30 days'
);
DELETE FROM authentik_tasks_task
WHERE finish_timestamp < NOW() - INTERVAL '30 days';"
```

This will delete the bulk of the 1.8M tasklog rows and 538K task rows. Safe to run live — no Authentik functionality depends on historical task records.

---

### Step 3 — VACUUM again after the deletes

```bash
docker exec -it authentik-postgresql-1 psql -U authentik -c 'VACUUM ANALYZE;'
```

This reclaims the disk space freed by Step 2. The Authentik DB should drop from ~1 GB to well under 100 MB.

---

### Step 4 — Verify

```bash
docker exec -it authentik-postgresql-1 psql -U authentik -c "
SELECT relname,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       n_live_tup, n_dead_tup,
       last_autovacuum
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 5;"
```

`authentik_tasks_tasklog` and `authentik_tasks_task` should now show sizes in the low MB range.

---

## Why this happened

Authentik creates task records on every LDAP sync cycle. At the default 5-minute sync interval, a single month of operation generates:

- 30 days × 24h × 12 syncs/hour = ~8,600 sync cycles
- Multiple task + tasklog records per cycle (user sync, group sync, membership resolution)
- No automatic cleanup of old completed task records

The `authentik_tasks_tasklog` table triggers autovacuum when dead tuples exceed 20% of its row count — at 1.8M rows that threshold is 360K dead tuples, which it hadn't reached yet. So autovacuum never ran on it, and the write pressure from continuous inserts caused the background writer to spike.

## Will this come back?

It will grow again slowly — that is normal. Authentik tasks are supposed to accumulate and Postgres handles it fine at a moderate volume. As long as VACUUM ANALYZE runs periodically (autovacuum handles this automatically once the table is at a normal size), it won't spike like this again.

If you want to be proactive, set a cron or systemd timer to run the Step 2 DELETE monthly. The Guard Dog auto-vacuum timer (`tak-auto-vacuum.sh`) handles the TAK Server cot database — a similar approach for the Authentik DB is worth adding if this recurs.
