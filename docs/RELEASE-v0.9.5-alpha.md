# Release Notes — v0.9.5-alpha

## What ships

Five changes: one bug fix already applied to the `dev` branch plus four new features. All changes apply automatically on next "Update Now."

---

### Bug Fix — CloudTAK initial deploy hangs at "Waiting for CloudTAK API"

**Problem:** Fresh CloudTAK installs got stuck at Step 6/7 for up to 15 minutes then failed with `RemoteDisconnected`. The `docker-compose.override.yml` written for hardening includes `cap_drop: ALL` on the `api` service. CloudTAK's container generates `user nginx;` at startup — nginx workers immediately fail to open `/dev/stdout`/`/dev/stderr` after dropping privileges and die (exit 2). The nginx master stays alive but serves nothing, so the health check never gets 200/401/403/404.

**Fix:** `_cloudtak_fix_nginx_user()` was already patching `user nginx;` → `user root;` on update and restart paths, but was missing from the initial `run_cloudtak_deploy()` path. Added the call with a 5-second settle delay immediately after `docker compose up -d --force-recreate` succeeds.

**To unblock a currently-hanging install** (run on the server while Step 6 is waiting):
```bash
docker exec cloudtak-api-1 sed -i "s/^user nginx;/user root;/" /etc/nginx/nginx.conf \
  && docker exec cloudtak-api-1 nginx -s reload
```

---

### Feature A — TAK Server Snapshots: two-server (split) support

**Problem:** On split deployments, `_tak_snapshot()` ran `pg_dump` locally — the database lives on Server One, so the dump either failed or captured nothing. Rollback then failed with `has no cot.pgdump`.

**Fix:**
- `_tak_snapshot()` checks `tak_deployment.mode == 'two_server'`. If true, streams `pg_dump -Fc cot` from Server One via SSH using the existing `server_one` key/host config.
- `_tak_rollback()` checks the same flag. If true, streams the `.pgdump` back to Server One via SSH and runs `pg_restore` there.
- Config files (CoreConfig.xml, UserAuthenticationFile.xml, certs/) live on Server Two — those snapshot/restore steps are unchanged.

---

### Feature B — TAK Server Snapshots: Upload & Restore

**Problem:** The Download button saved a snapshot `.tar.gz` off-box but there was no way to push it back — making off-box backups archival-only.

**Fix:**
- New **⬆ Upload Snapshot** button on the Snapshots & Recovery section (accepts `.tar.gz`, sits next to Take Snapshot Now and Refresh).
- `POST /api/takserver/snapshot/upload` validates the archive (must contain `CoreConfig.xml` or `cot.pgdump`), extracts to `SNAPSHOT_DIR`, registers the snapshot in settings.
- Uploaded snapshots appear in the table like locally-created ones; the existing **↩ Restore** button works without any changes.

---

### Feature C — Authentik Postgres `shm_size`

**Problem:** Docker's default `/dev/shm` (64 MB) is too small for `VACUUM ANALYZE` with parallel workers in `postgres:16-alpine`, causing:
```
ERROR: could not resize shared memory segment to 67145504 bytes: No space left on device
```

**Fix:**
- Added `shm_size: 256m` to the `postgresql` service in the Authentik `docker-compose.yml` template (fresh installs).
- Added the same patch to `_auto_harden_containers()` so existing installs receive it on next "Update Now" (detected by absence of `shm_size:` in the file).

---

### Feature D — Authentik task log cleanup: weekly Guard Dog timer

**Problem:** Authentik writes to `authentik_tasks_task` and `authentik_tasks_tasklog` on every background task. These tables are never automatically purged — after ~1 month they can grow to 500–900 MB (88%+ of the Authentik DB), causing autovacuum lag and CPU spikes.

**Fix:** New Guard Dog timer `takauthentiktasklogpurge.timer` (weekly — Sunday 03:00):

```sql
DELETE FROM authentik_tasks_tasklog
WHERE task_id IN (
  SELECT pk FROM authentik_tasks_task
  WHERE finish_timestamp < NOW() - INTERVAL '30 days'
);
DELETE FROM authentik_tasks_task
WHERE finish_timestamp < NOW() - INTERVAL '30 days';
VACUUM ANALYZE authentik_tasks_task, authentik_tasks_tasklog;
```

- Script runs via `docker exec authentik-postgresql-1 psql …`
- Guard Dog page shows a new **Database maintenance (Authentik)** card with schedule and last-run timestamp.
- Timer is installed automatically when Guard Dog is deployed (if Authentik is present) and on "Update Guard Dog".
- Log: `/var/log/takguard/authentik-tasklog-purge.log`

---

### Feature E — Console Rollback: moved from Console page to Guard Dog page

**Before:** A yellow "Rollback available" banner lived on the Console (home) page, cluttering the most-visited page.

**After:**
- Banner removed from the Console page entirely.
- New **Console Rollback** card on the Guard Dog page shows the previous version, "Roll Back to vX.X.X" button, and the same rollback action.
- If no previous version is recorded (fresh install or settings cleared), the card shows a greyed-out "No previous version available" state.

---

### Operator notes

- No manual steps required — all changes apply on "Update Now."
- Two-server operators: snapshot pg_dump from Server One now works correctly. No data migration needed for existing snapshots (config/cert snapshots are unchanged; only new snapshots will include the database dump).
