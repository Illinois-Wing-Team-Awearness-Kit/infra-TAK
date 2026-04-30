# v0.8.7-alpha — Work Plan

**Headline feature: Rollback**

The ability to revert to the previous working version of infra-TAK from the console, without SSH. This is the single most-requested operator safety net and the main focus of v0.8.7.

---

## 1. Rollback (main feature)

### Problem

Today's Update Now is one-directional. It runs `git fetch && git checkout --force origin/main` and restarts the console. If the new version has a regression (bad Authentik deploy, broken UI, startup crash), the operator has no recovery path from the console — they must SSH in, find the previous commit hash, and manually `git checkout <hash>` + restart. Non-technical operators on production boxes cannot do this, and experienced operators shouldn't have to.

The same issue applies after configuration changes (e.g. someone clicked Save on a Caddy block and broke TLS, or a TAK Server reconfigure pushed a bad CoreConfig.xml). Those aren't code-rollback scenarios but they point to the same gap: **no in-console undo for consequential actions**.

### Design goals

1. **One-click rollback from the console** — clearly labeled "Rollback to v0.8.x-alpha" button that undoes the last update.
2. **Before any update, snapshot the current state** — at minimum the current git commit hash, so rollback always knows where to go back to.
3. **Safe and idempotent** — rollback runs the same start-up path as a normal install (no special teardown). Services end up in the same state as a clean install of the previous version.
4. **Minimal footprint** — store the rollback snapshot in `settings.json` (already used for migration forensics). No new daemon, no new files except optionally a lightweight pre-update config dump.

### Proposed implementation

#### Phase 1 — Code rollback (MVP)

**Pre-update snapshot (in `_run_update_now()`):**
Before `git fetch + force-checkout`, record the current state in `settings.json`:
```json
{
  "rollback_snapshot": {
    "ts": 1777500000,
    "version": "0.8.6-alpha",
    "git_commit": "abc1234",
    "git_branch": "main"
  }
}
```

**Rollback function (`_run_rollback()`):**
1. Read `settings.json → rollback_snapshot`.
2. If no snapshot or snapshot is the current commit → surface "No rollback available" message.
3. Run `git checkout <git_commit> -- .` (checkout specific commit, not a branch).
4. Restart the console service (same as Update Now finish).
5. Clear the snapshot after rollback (avoid rollback-of-rollback confusion).

**Console UI:**
- Under "Update Now" button: small secondary button "Rollback to v0.8.6-alpha" (only visible if a snapshot exists and differs from current version).
- Confirmation modal: "This will revert infra-TAK to v0.8.6-alpha (commit abc1234). Continue?"
- Progress feedback identical to Update Now.

#### Phase 2 — Config backup/restore (stretch goal for v0.8.7 or defer to v0.8.8)

Before a TAK Server deploy or Authentik reconfigure, snapshot key config files:
- `~/authentik/.env`
- `~/authentik/docker-compose.yml`
- `/opt/tak/CoreConfig.xml`
- `/opt/tak/UserAuthenticationFile.xml`

Store as timestamped tarballs in `/root/infra-TAK/.backups/`. Expose "Restore last config" button if a backup exists newer than the current config file mtime. This covers the "bad Save" scenario without needing a full code rollback.

### Scope / boundaries for v0.8.7

- Phase 1 (code rollback) is the v0.8.7 deliverable.
- Phase 2 (config backup/restore) is a stretch goal — design it so Phase 1 doesn't block it.
- Rollback does NOT re-run migrations or undeploy Authentik — it only reverts the `app.py` code. If the bad version touched `~/authentik/docker-compose.yml`, the operator may still need a manual fix. Document this limitation clearly in the UI.
- No rollback chain (rollback of rollback). One level deep. Simple and safe.

### Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| git detached-HEAD confusion | After rollback, branch is detached at the commit. "Update Now" re-attaches to `origin/main`. Document this. |
| Snapshot gets stale (multiple updates without rollback use) | Overwrite snapshot on every update. Only one level of rollback supported. |
| Console restart mid-rollback | Same risk as Update Now. systemd restarts the service automatically. Rollback is idempotent. |
| Version banner shows wrong version in detached state | Read `VERSION` from `app.py` after checkout, not from git. Already how it works. |

---

## 2. Minor / maintenance items (v0.8.7 scope TBD)

These are lower-priority and can ship alongside rollback or slip to v0.8.8 depending on complexity.

### 2a. Dashboard: CPU % per-core breakdown (stretch)

Currently shows aggregate CPU %. On DataSync/Node-RED boxes the aggregate can look alarming (104%) while individual cores are fine. A per-core bar or sparkline would let operators distinguish "one core busy" from "all cores pegged". Low priority — burst-and-idle is already documented as expected.

### 2b. Authentik deploy: wait for all containers healthy before API poll

Confirmed fix is in v0.8.6 (the `elif needs_pg_update:` scope bug was the root cause, not poll timing). But if there are still edge cases where the API poll starts before postgres is healthy on very slow disk (< 100 MB/s), a health-gate loop before the poll adds 0-5s on fast boxes and prevents edge-case races. Low risk, low effort.

### 2c. NSG ARM template — integrate into `start.sh` advisory

`docs/azure-nsg-infra-tak.json` exists but is not linked from `start.sh` output. When `start.sh` detects an Azure environment (public IP ≠ private IP), it could print a one-line advisory:
```
Azure detected — ensure NSG allows: 443, 5001, 8089, 8443, 8446
See docs/azure-nsg-infra-tak.json for the ARM template.
```

---

## 3. v0.8.7 acceptance criteria

- [ ] "Rollback to v0.8.x-alpha" button appears in console after an update is applied.
- [ ] Clicking rollback returns `app.py` to the previous version (verified by `grep '^VERSION'`).
- [ ] Confirmation modal shows previous version string and commit hash.
- [ ] After rollback, Update Now works normally and returns to current main.
- [ ] If no snapshot exists, the rollback button is hidden (not just greyed out).
- [ ] Snapshot survives a console restart (stored in `settings.json`, not in-memory).
- [ ] Tested on Azure (tak-test-3) — rollback from a dummy v0.8.7 bump back to v0.8.6.

---

## 4. Notes from v0.8.6 post-release

- All four v0.8.6 fixes confirmed working on Azure tak-test-3 (D8as_v5, P10 64 GiB, ~145 MB/s).
- v0.8.5 production fleet (tak-10, ssdnodes, responder) is stable at all-zeros health metrics.
- No LDAP incidents since v0.8.5. Spiral monitor heartbeating silently every 10 min on all three boxes.
- v0.8.6 dev→main selective merge uses the pattern in `docs/COMMANDS.md`.
- Rollback is the most immediately useful operator-safety feature. No need for complex Phase 2 to ship a useful v0.8.7.
