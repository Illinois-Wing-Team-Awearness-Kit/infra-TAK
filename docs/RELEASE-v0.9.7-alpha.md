# Release Notes — v0.9.7-alpha

## What ships

Three bug fixes. Second emergency hotfix release — field testing of v0.9.6 revealed two additional bugs that left all servers still broken despite the update running successfully.

---

### Bug Fix A — `shm_size` detection false-positives on other services

**Problem:** v0.9.5's shm_size patch checked `'shm_size:' not in whole_compose_file`. On installs where the Authentik `server` and/or `worker` services already had their own `shm_size:` values (e.g. `shm_size: 512mb` from a prior manual config or a different install path), the whole-file check returned False and the `postgresql` service never got `shm_size: 256m` added. The `postgresql` service block had no `shm_size:` entry at all.

v0.9.6's runtime check compounded this: it only ran `docker inspect` if `'shm_size: 256m' in _ak` — which also returned False for the same reason (the file had `512mb` on other services, and `256m` nowhere). So the container was never recreated.

**Fix:** Anchor the detection on the postgres image line (`image: docker.io/library/postgres:16-alpine`), scan only the `postgresql` service block for `shm_size:`, and add it immediately after the image line if absent. Separately, the docker inspect ShmSize check now runs unconditionally (no compose file content gate) and compares the running container's `HostConfig.ShmSize` against `268435456` (256 MB).

---

### Bug Fix B — DELETE SQL uses wrong column names for Authentik 2026.x

**Problem:** The SQL in `_authentik_tasklog_cleanup()`, the weekly `takauthentiktasklogpurge` timer script written to disk, and `docs/AUTHENTIK-TASK-BLOAT-FIX.md` all referenced `pk` and `finish_timestamp` — column names from a prior Authentik schema that do not exist in Authentik 2026.x. The DELETE failed immediately with `ERROR: column "pk" does not exist`, leaving all task log tables at full size.

Actual Authentik 2026.x schema for `authentik_tasks_task`:
- Primary key: `message_id` (uuid)
- Timestamp: `mtime` (timestamp with time zone — last modified time)
- FK from `authentik_tasks_tasklog`: `task_id` → `authentik_tasks_task(message_id)`

**Fix:** All three SQL locations updated to use `message_id` and `mtime`.

---

### Bug Fix C — Cleanup size threshold now counts both tables

The `_authentik_tasklog_cleanup()` threshold check was measuring only `authentik_tasks_tasklog`. Changed to sum both `authentik_tasks_tasklog` and `authentik_tasks_task` — the task table itself can be 300–400 MB and would otherwise not trigger cleanup if tasklog happened to be just under 100 MB.

---

### Operator notes

- **No manual steps required.** "Update Now" to v0.9.7 applies all three fixes automatically.
- On servers that ran "Update Now" on v0.9.6 and still have a pegged Authentik Postgres: the v0.9.7 update will correctly detect the 64 MB container, add `shm_size: 256m` to the postgresql service block, recreate the container, then run the corrected DELETE + VACUUM.
- If you need immediate relief before the v0.9.7 rollout, follow `docs/AUTHENTIK-TASK-BLOAT-FIX.md` (updated with correct column names).
