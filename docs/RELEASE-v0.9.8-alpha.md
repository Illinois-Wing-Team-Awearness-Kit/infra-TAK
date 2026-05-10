# Release Notes — v0.9.8-alpha

## What ships

One bug fix. Third emergency hotfix release — v0.9.7 still left servers with postgres pegged at 1100%+ because `docker compose up -d --force-recreate` does not reliably terminate the old container's postgres process.

---

### Bug Fix A — Orphaned postgres processes survive container recreate

**Problem:** `docker compose up -d --force-recreate postgresql` sends SIGTERM to the old container and waits only 10 seconds (Docker default) before it gives up. Under high load, postgres needs more than 10 seconds to complete its checkpoint and exit cleanly. When it doesn't exit in time, Docker declares the stop "complete" but the postgres process continues running on the host as an orphan — UID 70, no longer attached to any container, consuming 1100%+ CPU doing nothing useful.

This happened on every server that ran "Update Now" on v0.9.6 or v0.9.7 while Authentik's postgres was under heavy load from the task log bloat.

**Fix — part 1 (v0.9.8, for future recreates):** The `_shm_needs_pg_recreate` path now uses `docker stop -t 30 authentik-postgresql-1` before `docker compose up -d postgresql`, giving postgres 30 seconds for a clean checkpoint exit instead of 10. It also records UID-70 postgres PIDs before stopping and kills any that survive the recreate.

**Fix — part 2 (v0.9.8, unconditional):** A cgroup-based orphan check runs at the end of every update, regardless of whether a container recreate was triggered. For every UID-70 postgres process on the host, it reads `/proc/<pid>/cgroup` and checks whether the process belongs to the current `authentik-postgresql-1` container. Any process whose cgroup does not contain the current container ID is an orphan and is killed with SIGKILL. This catches orphans left by prior bad updates (e.g. a server that ran v0.9.7 and still has a zombie postgres from that update).

---

### Operator notes

- **No manual steps required.** "Update Now" to v0.9.8 applies the fix automatically.
- On servers running v0.9.7 that still have a pegged postgres orphan: the v0.9.8 update will detect it via cgroup membership check and kill it unconditionally — no recreate needs to trigger.
- The unconditional orphan check runs on every future update as a safety net.
