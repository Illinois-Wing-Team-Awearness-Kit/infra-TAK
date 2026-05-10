# Release Notes — v0.9.9-alpha

## What ships

One bug fix. Fourth hotfix in the v0.9.5 Authentik Postgres cleanup chain — field test on `responder` showed v0.9.8's orphan kill ran at the right moment but a LATER post-update step created fresh orphans the first kill couldn't see.

---

### Bug Fix A — Orphan kill ran too early

**Problem:** v0.9.8's unconditional orphan-postgres kill runs at the end of `_auto_harden_containers()`. But further down the post-update pipeline, `_auto_authentik()` calls `run_authentik_deploy(reconfigure=True)` which recreates Authentik's containers AGAIN to apply tuning changes. That second recreate orphans yet another postgres process (the one created by the first recreate). By the time `_auto_authentik()` finishes, the orphan check has long since exited and nothing kills the new orphan.

Verified on `responder`:
- 22:23:23 — orphan kill ran, killed PIDs 7756, 7906 (orphans from the first recreate) ✓
- 22:23:26 — `auto-reconfiguring Authentik` starts (recreates containers again)
- 22:25:30 — auto-deploy complete — but PID 1668050 was an orphan at 1014% CPU, started ~22:23, surviving the second recreate

**Fix:** Add a second orphan postgres check right before `Post-update: auto-deploy complete`. Same cgroup-based detection — gets current `authentik-postgresql-1` container ID, reads `/proc/<pid>/cgroup` for every UID-70 postgres process, kills any whose cgroup does not contain the current container ID. Catches orphans left by `_auto_authentik()`'s reconfigure step.

---

### Operator notes

- **No manual steps required.** "Update Now" to v0.9.9 applies the fix automatically.
- The first orphan kill in `_auto_harden_containers()` is preserved — it still catches orphans from prior bad updates that linger even before any recreate runs.
- New log line to watch for: `Post-update: final pass — killed orphaned postgres PID <pid> (not in container <id>)`
