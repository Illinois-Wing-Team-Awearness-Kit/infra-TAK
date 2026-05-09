# v0.9.5 — Feature Plan

> Not yet implemented. This is a planning document.

---

## TAK Server Snapshots — split / two-server support

Currently, `_tak_snapshot()` runs `sudo -u postgres pg_dump -Fc cot` locally on the console host. In a standard single-server deployment this is correct — postgres is co-located with the TAK Server application. In a **split (two-server) deployment**, the PostgreSQL database lives on Server One while the infra-TAK console runs on Server Two. The local pg_dump either fails (no local postgres) or captures nothing useful.

**Plan:**

- `_tak_snapshot()` checks `settings.tak_two_server` — if true, uses the existing `server_one` SSH key/host config to stream the pg_dump from Server One:
  ```bash
  ssh server_one "sudo -u postgres pg_dump -Fc cot" > /opt/tak/snapshots/<label>/cot.pgdump
  ```
- `_tak_rollback()` checks the same flag — if true, streams the `.pgdump` back to Server One via SSH and runs `pg_restore` there instead of locally.
- Config files (CoreConfig.xml, UserAuthenticationFile.xml, /etc/default/takserver, certs/) already live on Server Two — those snapshot/restore steps are unchanged.
- The two-server SSH infrastructure (`server_one` config, `_ssh_probe()`, key management) is already in place and will be reused.

**Current behavior on split deployments (v0.9.2–v0.9.4):** snapshot captures config files and certs correctly but skips the database dump, making rollback fail with `has no cot.pgdump`. Operators on split deployments should use manual `pg_dump` on Server One until v0.9.5 ships.

---

## TAK Server Snapshots — Upload & Restore

The **Download** button (added v0.9.2) lets operators save a snapshot `.tar.gz` off-box. There is currently no way to push that archive back and restore from it — making off-box backups useful only as archival copies, not as a real disaster-recovery path.

**Use cases:**
- VPS is destroyed / unrecoverable — spin up a new host, upload last good snapshot, restore
- Migrating TAK Server to a different VPS
- Restoring a snapshot that has already been pruned from the server by the retention policy

**Plan:**

- Add an **Upload Snapshot** button in the Snapshots & Recovery section (file input, accepts `.tar.gz`)
- Backend endpoint `POST /api/takserver/snapshot/upload`:
  - Validates the archive contains the expected structure (`cot.pgdump`, `CoreConfig.xml`, `certs/` etc.)
  - Extracts to `/opt/tak/snapshots/<label>/` (label derived from archive name, de-duped if needed)
  - Returns the new snapshot label on success
- Once extracted, the snapshot appears in the table like any locally-created one and the existing **Rollback** button works without any changes
- No streaming write concern — uploads are operator-initiated, infrequent, and bounded by snapshot size

---

## Authentik Postgres — `shm_size` and task log retention

Two related hardening items surfaced from a field operator hitting Postgres pegged-CPU on v0.9.4.

### 1. Add `shm_size` to the Authentik compose template

Docker's default `/dev/shm` for containers is 64 MB. PostgreSQL 16 (`postgres:16-alpine`) needs slightly more than 64 MB for `VACUUM ANALYZE` with parallel workers, causing:

```
ERROR: could not resize shared memory segment to 67145504 bytes: No space left on device
```

**Fix:** add `shm_size: 256m` to the `postgresql` service in the generated Authentik `docker-compose.yml` (both the template in `app.py` and the `_auto_harden_containers()` patcher that runs on "Update Now" for existing installs).

### 2. Monthly Authentik task log cleanup — Guard Dog timer

Authentik writes a record to `authentik_tasks_task` and `authentik_tasks_tasklog` on every background task run. These tables are never automatically purged. After ~1 month of normal operation they can grow to 500–900 MB (88%+ of the Authentik DB), causing autovacuum lag and background writer CPU spikes.

**Fix:** add a Guard Dog systemd timer (monthly, similar to `tak-auto-vacuum.sh`) that runs inside the Authentik postgres container:

```sql
DELETE FROM authentik_tasks_tasklog
WHERE task_id IN (
  SELECT pk FROM authentik_tasks_task
  WHERE finish_timestamp < NOW() - INTERVAL '30 days'
);
DELETE FROM authentik_tasks_task
WHERE finish_timestamp < NOW() - INTERVAL '30 days';
VACUUM ANALYZE;
```

Timer should run once a month at a low-traffic time (e.g. 03:00 on the 1st). Console UI shows last-run timestamp on the Guard Dog page alongside the existing CoT DB auto-vacuum entry.

---

## Console Rollback — move banner to Guard Dog page

The yellow console rollback banner currently lives on the Console (home) page. It clutters the most-visited page and is better suited to Guard Dog, which is already the home for health, recovery, and maintenance actions.

**Plan:**

- Remove the rollback banner from the Console page entirely
- Add a **Console Rollback** section to the Guard Dog page: shows the previous version (if any), the "Roll back to vX.X.X" button, and the same logic that currently drives the banner
- The rollback action itself (`POST /api/console/rollback`) is unchanged — only the UI surface moves
- If no previous version is recorded (fresh install or settings cleared), the section shows a greyed-out "No previous version available" state rather than hiding completely
