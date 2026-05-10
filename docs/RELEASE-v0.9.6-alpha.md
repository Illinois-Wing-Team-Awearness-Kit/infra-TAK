# Release Notes — v0.9.6-alpha

## What ships

Two fixes. Emergency hotfix release addressing a regression in v0.9.5 where the Authentik Postgres `shm_size: 256m` patch was written to the compose file but never applied to the running container — and adding automated task log cleanup to "Update Now" so that operators with bloated tables get relief immediately without needing to follow the manual runbook.

---

### Bug Fix — Authentik `shm_size: 256m` never applied to running container (regression from v0.9.5)

**Problem:** `_auto_harden_containers()` correctly detected the absence of `shm_size:` in the Authentik compose file and wrote the patch — but the subsequent `docker compose up -d --force-recreate` call only listed `worker server ldap`. The `postgresql` service was never in the recreate list, so the container kept running with Docker's default 64 MB `/dev/shm`. All installs that went through "Update Now" on v0.9.5 had the compose file patched but the running container unchanged (`docker inspect authentik-postgresql-1 | grep ShmSize` showed `67108864` on every server).

**Impact:** On servers with large `authentik_tasks_tasklog` / `authentik_tasks_task` tables (500–900 MB, common after ~1 month of operation), `VACUUM ANALYZE` triggered by the new weekly Guard Dog timer would fail with:
```
ERROR: could not resize shared memory segment to 67145504 bytes: No space left on device
```
Additionally, autovacuum checkpoint pressure on these bloated tables was contributing to sustained high CPU on Authentik's Postgres process.

**Fix:** Two-part change:

1. Added `_shm_added` flag — set when `shm_size: 256m` is newly written to the compose file (fresh installs only).

2. Added a runtime container inspection check — even when the compose file already has `shm_size: 256m` (i.e., the server was on v0.9.5), the code now runs `docker inspect authentik-postgresql-1` and checks `HostConfig.ShmSize`. If it reads `67108864` (64 MB — the Docker default), it sets `_shm_needs_pg_recreate = True` and recreates `postgresql` first with a 5-second settle, then proceeds with `worker server ldap` if any other compose changes are also pending.

This means **"Update Now" to v0.9.6 is fully automatic for all operators** — no manual steps required. The postgresql container will be recreated with the correct `ShmSize: 268435456` (256 MB) on the first update, and the check becomes a no-op on every subsequent run (container already has 256 MB).

---

---

### Fix B — Authentik task log cleanup automated into "Update Now"

**Problem:** The weekly `takauthentiktasklogpurge.timer` (shipped in v0.9.5) handles ongoing maintenance going forward, but on servers that already had bloated task log tables before v0.9.5, the backlog remained until the timer's first Sunday 03:00 fire. Operators experiencing CPU spikes had to follow a manual runbook (`docs/AUTHENTIK-TASK-BLOAT-FIX.md`) to get immediate relief.

**Fix:** New `_authentik_tasklog_cleanup()` runs on every "Update Now." It checks whether `authentik_tasks_tasklog` exceeds 100 MB and if so runs the same DELETE + `VACUUM ANALYZE` that the weekly timer runs. This clears the backlog immediately on update. On subsequent runs (tables already small) the DELETE matches nothing and returns in milliseconds — effectively a no-op.

The weekly timer remains in place as the ongoing scheduled maintenance.

---

### Operator notes

- **No manual steps required.** "Update Now" to v0.9.6:
  1. Detects the 64 MB Authentik postgres container and recreates it with `shm_size: 256m`
  2. Purges Authentik task log rows older than 30 days and runs `VACUUM ANALYZE` if the table exceeds 100 MB
- Both steps are idempotent — safe to run repeatedly, no-op once already applied.
- If you need relief before the v0.9.6 rollout, follow `docs/AUTHENTIK-TASK-BLOAT-FIX.md` manually.
