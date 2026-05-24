# Postgres Diagnostics — TAK Server (v0.9.4)

Run these commands in order and paste the output back. All queries run as the `postgres` user on the machine hosting the `cot` database.

---

## 1. What is Postgres doing right now?

```bash
sudo -u postgres psql -d cot -c "
SELECT pid, now() - pg_stat_activity.query_start AS duration,
       state, wait_event_type, wait_event, left(query, 120) AS query
FROM pg_stat_activity
WHERE state != 'idle'
ORDER BY duration DESC NULLS LAST;"
```

**Most important query.** Shows every active query — retention deletes, autovacuum, TAK Server reads, etc.

---

## 2. Table sizes and dead tuple bloat

```bash
sudo -u postgres psql -d cot -c "
SELECT relname,
       pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
       n_live_tup, n_dead_tup,
       last_autovacuum, last_autoanalyze
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC
LIMIT 10;"
```

High `n_dead_tup` (millions) means retention ran but vacuum hasn't caught up — this is the most common cause of Postgres CPU spikes in TAK deployments.

---

## 3. Database size

```bash
sudo -u postgres psql -t -A -c "
SELECT datname, pg_size_pretty(pg_database_size(datname))
FROM pg_database
ORDER BY pg_database_size(datname) DESC;"
```

---

## 4. Index bloat

```bash
sudo -u postgres psql -d cot -c "
SELECT indexrelname,
       pg_size_pretty(pg_relation_size(indexrelid)) AS index_size,
       idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
ORDER BY pg_relation_size(indexrelid) DESC
LIMIT 10;"
```

Indexes much larger than the table itself = index bloat, causing full scans instead of efficient lookups.

---

## 5. Which Postgres processes are eating CPU?

```bash
ps aux --sort=-%cpu | grep postgres | head -20
```

One PID at 100% = single heavy query or vacuum worker. Multiple PIDs = concurrent retention deletes.

---

## 6. Disk I/O (rule out disk wait showing as CPU)

```bash
iostat -x 2 3
```

---

## 7. Is retention running? When did it last fire?

```bash
systemctl status takserver | grep -i retent
journalctl -u takserver --since "1 hour ago" | grep -i "retent\|vacuum\|delete\|cleanup" | tail -30
```

Guard Dog auto-vacuum log:

```bash
tail -50 /var/log/takguard/restarts.log | grep -i vacuum
```

---

## 8. Postgres configuration

```bash
sudo -u postgres psql -d cot -c "
SELECT name, setting, unit FROM pg_settings
WHERE name IN (
  'autovacuum', 'autovacuum_vacuum_scale_factor',
  'autovacuum_vacuum_threshold', 'autovacuum_vacuum_cost_delay',
  'autovacuum_max_workers', 'work_mem', 'shared_buffers',
  'max_connections'
)
ORDER BY name;"
```

---

## What the results mean

| Finding | Likely cause |
|---|---|
| Long-running `DELETE` in query 1 | TAK Server retention actively running — let it finish |
| `autovacuum` process in query 1 | Autovacuum catching up after retention — normal, let it finish |
| `n_dead_tup` in the millions (query 2) | Retention ran, vacuum lagging — run `sudo -u postgres psql -d cot -c 'VACUUM ANALYZE;'` |
| `cot` DB > 20–25 GB (query 3) | Disk reclaim needed — `VACUUM FULL` in a maintenance window |
| Index size >> table size (query 4) | Index bloat — `REINDEX DATABASE cot` after vacuuming |
| Single Postgres PID at 100% (query 5) | One heavy query or vacuum worker |
| High `await` in iostat (query 6) | Disk bottleneck, not CPU — different problem |

---

## Quick fix if dead tuples are the culprit

```bash
# Safe to run live — no table locks, may take a few minutes on a large cot DB
sudo -u postgres psql -d cot -c 'VACUUM ANALYZE;'
```

If the DB is still large after that and you have a maintenance window:

```bash
# Locks tables — only run when TAK Server can be briefly stopped or traffic is low
sudo -u postgres psql -d cot -c 'VACUUM FULL;'
```
