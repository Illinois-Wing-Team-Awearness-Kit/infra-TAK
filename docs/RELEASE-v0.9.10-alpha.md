# Release Notes — v0.9.10-alpha

## What ships

One critical bug fix. v0.9.8 and v0.9.9 introduced an orphan-postgres-process killer that incorrectly classified **legitimate CloudTAK PostGIS processes as orphans** and SIGKILLed them on every update.

---

### Bug Fix A — Orphan kill was killing CloudTAK PostGIS

**Problem:** v0.9.8 and v0.9.9 use a cgroup-based check to identify orphaned UID-70 postgres processes left behind by `authentik-postgresql-1` container recreates. The check compared each process's cgroup against `authentik-postgresql-1`'s container ID — and killed anything that didn't match.

Two problems:
1. `cloudtak-postgis-1` (the `postgis/postgis:17-3.4-alpine` container that backs CloudTAK's spatial database) also runs its postgres as UID 70.
2. Its processes are in `cloudtak-postgis-1`'s cgroup — not `authentik-postgresql-1`'s — so the check thought they were orphans.

Result: **every "Update Now" SIGKILLed all CloudTAK PostGIS processes**. Docker's `restart: always` auto-recreated the container immediately, but CloudTAK had to replay any in-flight transactions and catch up on the WAL, causing the postgres CPU to spike to 800–1100% as it recovered. Verified on both `responder` and `tak-10`.

Update logs that previously looked like wins were actually friendly fire:
```
Post-update: killed orphaned postgres PID 1714362 (not in container 995e0c9593d5)  ← cloudtak postgis
Post-update: final pass — killed orphaned postgres PID 1722498 ...                  ← cloudtak postgis
```

**Fix:** Get the set of ALL currently running Docker container IDs via `docker ps -q --no-trunc`, then kill a UID-70 postgres process ONLY if its cgroup matches NO running container. Genuine orphans (cgroup points at a container that no longer exists) are still killed; live processes from any running postgres container (Authentik, CloudTAK, future additions) are left alone.

---

### Operator notes

- **No manual steps required.** "Update Now" to v0.9.10 applies the fix automatically.
- After the v0.9.10 update, postgres CPU should settle for good — Authentik's task tables are clean (v0.9.5+ purge) and CloudTAK is no longer being murdered on every update.
- The real fixes from v0.9.5–v0.9.9 are preserved: `shm_size: 256m` on `authentik-postgresql-1`, weekly `takauthentiktasklogpurge` timer, `docker stop -t 30` for clean checkpointing, one-time `_authentik_tasklog_cleanup()` on Update Now if either task table is over 100 MB.
- New log line wording for clarity: `Post-update: killed orphaned postgres PID <pid> (cgroup matches no running container)`.
