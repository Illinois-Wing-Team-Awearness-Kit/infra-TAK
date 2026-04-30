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

### 2c. Speed test: restore read MB/s display

The manual disk speed test computes both read and write (`disk_speed_test_read_mbs` and `disk_speed_test_write_mbs`) but the current display only shows write. Read was accidentally dropped when the disk lines were rewritten in v0.8.6. Guard Dog is write-only by design (`dd oflag=dsync`), so the Guard Dog line stays write-only. The speed test line should show both:

```
Disk speed test (256 MiB):  210 MB/s write  /  1331 MB/s read
```

Low priority — data is still collected, just not rendered.

### 2d. NSG ARM template — integrate into `start.sh` advisory

`docs/azure-nsg-infra-tak.json` exists but is not linked from `start.sh` output. When `start.sh` detects an Azure environment (public IP ≠ private IP), it could print a one-line advisory:
```
Azure detected — ensure NSG allows: 443, 5001, 8089, 8443, 8446
See docs/azure-nsg-infra-tak.json for the ARM template.
```

### 2e. Authentik server+worker periodic auto-restart on heavy-load boxes

**Field evidence (tak-10, Apr 30 2026):** After ~5h of heavy LDAP load on a DataSync/Node-RED box, Authentik server + postgres CPU pinned at p50 99% / 93% even with bind volume 489/5min and 96% cache hit rate. **A simple `docker compose up -d --no-deps --force-recreate server worker` immediately dropped postgres p50 to ~30% and server to bursty pattern matching responder.** Confirms runtime state accumulation (memory growth, query plan cache fragmentation, or session tracker drift) in long-running Authentik server containers under sustained event-trigger load.

**Proposed:** Weekly (or every 72h) auto-recreate of `authentik-server-1` + `authentik-worker-1`, scheduled off-peak (e.g. 04:00 local). LDAP outpost stays up so cached SA bind survives — no thundering herd, no LDAP downtime. ~15-30s API outage during recreate is acceptable in the maintenance window. Add a "Restart Authentik server now" button in the console for manual on-demand triggering.

**Settings:**
```json
{
  "authentik_periodic_restart_enabled": true,
  "authentik_periodic_restart_interval_hours": 168,
  "authentik_periodic_restart_window_hour_local": 4,
  "authentik_periodic_restart_last_run": 0
}
```

Detector logic: skip if `_detect_authentik_ldap_spiral` is currently firing (don't restart during an active spiral; let the spiral monitor handle it).

### 2f. Node-RED: verify all flows fire on container restart / post-update

**Operator concern (tak-10):** Some flows (DataSync) restart cleanly after Node-RED container restart. Others (Tablet Command AVL) appeared to need manual restart. We need to audit which flow patterns auto-fire vs. need explicit kick.

**Audit checklist:**
- ArcGIS engine tabs (dynamic, Configurator-driven): confirmed auto-fire after `nodered/deploy.sh` (context restore restores credentials and engine state).
- TFR / TC / PulsePoint / KML feeds: same path — confirmed.
- TAK Mission API (mTLS): inject node "fire on deploy" must be set; otherwise depends on first incoming event.
- DataSync flows: depend on TAK Server WebSocket being reachable on container start; if Authentik proxy was slow during restart, the WebSocket connect can fail silently. Add a `catch + 30s retry` pattern as a flow template.

**Proposed:**
- Add a "Flow health check" Node-RED endpoint that reports per-tab connection status, scraped by the dashboard.
- Document the "fire on deploy" inject-node pattern in `nodered/README.md`.
- Add a CHANGELOG note in v0.8.7 explaining what auto-fires vs. what needs manual restart.

### 2g. TAK Server: webadmin admin-role assignment regression

**Field evidence (tak-10, Apr 30 2026):** After webadmin password rotation / Authentik webadmin user re-creation (via "Resync LDAP webadmin"), the TAK Server WebUI redirected webadmin to **WebTAK (operator UI)** instead of the **Admin Console** — meaning TAK Server's `UserAuthenticationFile.xml` did not list the new webadmin user with the `admin` role. **Mitigation that worked:** running "Resync LDAP webadmin" again from the console resolved it. So the resync flow has the admin-role assignment, but the *first* run didn't fully complete the role propagation (or the role got dropped during a subsequent reconcile).

**Suspect paths:**
- `/opt/tak/UserAuthenticationFile.xml` — does the resync flow ALWAYS add `<userRole role="ADMIN"/>` for webadmin? Verify in `_ensure_authentik_webadmin` / TAK Server reconcile.
- TAK Server's `setadmin.sh` or admin-cert sync — does it run on every webadmin resync or only on first deploy?
- Race between webadmin user creation in Authentik and admin-role apply in TAK Server (LDAP cache / sync delay).

**Proposed:**
- Add a final verifier to "Resync LDAP webadmin" that confirms webadmin appears in `UserAuthenticationFile.xml` with `role="ADMIN"` before reporting success.
- If missing, automatically run the admin-role apply step (idempotent).
- Add a console button "Verify webadmin admin role" that runs the check on demand.
- Log the verifier result to `settings.json → webadmin_admin_role_check`.

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

## 5. Apr 30 2026 — tak-10 field session findings

Three issues surfaced during a tak-10 deep-dive after v0.8.6 was already shipped to main. None are v0.8.6 regressions; all are pre-existing or operational items now scoped into v0.8.7:

1. **Authentik runtime state accumulation** (item 2e above) — `server+worker --force-recreate` cleared sustained 99%/93% CPU back to bursty pattern. Manual restart works; need automation.
2. **Tablet Command flow not flowing data on Node-RED** (parked separately for triage outside v0.8.7) — likely related to flow restart behavior (item 2f) OR a TAK Server mission-channel mismatch. Will diagnose live; if it points to a code-fixable pattern, fold the fix into v0.8.7.
3. **TAK Server webadmin redirected to WebTAK instead of Admin Console** (item 2g above) — fixed by running "Resync LDAP webadmin" a second time. Investigate why one resync wasn't sufficient and add a final-state verifier.
