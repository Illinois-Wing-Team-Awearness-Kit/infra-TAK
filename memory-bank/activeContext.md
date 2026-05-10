# Active Context — infra-TAK

> Current work focus, recent changes, next steps, and active decisions. Updated frequently.

## Current state (snapshot)

- **Active branch:** `dev`. Local clone is in sync with `origin/dev`.
- **Latest tag on `main`:** `v0.9.4-alpha` (in `app.py`: `VERSION = "0.9.4-alpha"`). Released 2026-05-08.
- **Latest commits on `dev` (top of log, ahead of `main`):**

  | Commit | Subject |
  |---|---|
  | `129dfc0` | fix: CloudTAK fresh deploy leaves Caddy missing map.* (configure loop) |
  | `328b5d7` | fix: JS syntax error breaking all TAK server page card toggles |
  | `d2d55c4` | fix: TAK version shown as ? in snapshot list for uploads and two-server |
  | `a5b7f3c` | feat: snapshot upload progress bar (XHR with bytes/pct indicator) |
  | `f0d6d76` | feat: v0.9.5-alpha — snapshot split-server, upload/restore, authentik shm_size + tasklog purge, rollback to Guard Dog |
  | `dc45f72` | Update Authentik task bloat fix doc: add shm_size step, use heredocs |
  | `11614e9` | Plan v0.9.5: change Authentik task cleanup timer to weekly |
  | `8c400fd` | Plan v0.9.5: Authentik shm_size fix and task log retention timer |
  | `39560f8` | Add Authentik task table bloat fix runbook |
  | `63dc39d` | Add round-2 Postgres diagnostics targeting Authentik DB |

- **Test VPS:** `tak-10` (`172.93.50.47`). Currently SSH'd-in (terminal 1). Last command on the VPS: `git fetch origin dev && git checkout -B dev origin/dev && sudo systemctl restart takwerx-console` — succeeded, branch `dev` set up to track `origin/dev` (`328b5d7..129dfc0`).

## Active focus: v0.9.5-alpha

Per `docs/PLAN-v0.9.5.md`, the v0.9.5 release is mid-flight on `dev`. Themes:

1. **CloudTAK initial-deploy hang fix.** Root cause: `_cloudtak_fix_nginx_user()` (which patches `user nginx;` → `user root;` to keep nginx workers alive under `cap_drop: ALL`) was missing from the fresh-deploy path. Fix already applied to `run_cloudtak_deploy()` with a 5s settle delay.

2. **TAK Server snapshots — split / two-server support.** Currently `_tak_snapshot()` runs `pg_dump` locally, which fails on two-server topology where Postgres lives on Server One. Plan: detect `settings.tak_two_server` and stream the dump via SSH from Server One. Same for `_tak_rollback()`. Reuse the existing `server_one` SSH config and `_ssh_probe()` helper.

3. **TAK Server snapshots — upload & restore.** Currently snapshots can be downloaded but not pushed back. Plan: `POST /api/takserver/snapshot/upload` accepting `.tar.gz`, validate structure, extract to `/opt/tak/snapshots/<label>/`, reuse existing rollback button. Already partially shipped on `dev` (`a5b7f3c` adds the XHR progress bar).

4. **Authentik Postgres hardening.**
   - Add `shm_size: 256m` to the Authentik compose template (postgres:16-alpine needs >64 MB for `VACUUM ANALYZE` with parallel workers — fails with `could not resize shared memory segment` on default).
   - Add a Guard Dog systemd timer (weekly) that deletes `authentik_tasks_tasklog` rows older than 30 days + `VACUUM ANALYZE`. Tables grow to 500–900 MB after ~1 month and dominate the Authentik DB. Runbook lives at `docs/AUTHENTIK-TASK-BLOAT-FIX.md`.

5. **Console rollback banner — move to Guard Dog page.** Currently lives on the Console (home) page. Plan: remove from home, add a "Console Rollback" section to Guard Dog. Same `POST /api/console/rollback` endpoint. Show greyed-out "No previous version available" if no rollback recorded.

## Recent changes worth remembering

- `c958cb3` (tag `v0.9.4-alpha`): seven JS-syntax / Authentik-domain-audit fixes + Federation Hub local deployment + MediaMTX RTSP fail2ban watching panel + kernel patch banner. See `docs/RELEASE-v0.9.4-alpha.md`.
- v0.9.4 introduced **Domain Migration Audit** card (Authentik page) — scans 7 known locations for stale FQDN references and offers "Fix All". Bug fix in `a13d55c`: empty `cookie_domain` is no longer flagged (it's the default; means "use request domain").
- v0.9.4 fixed **multiple Python `\'` escape bugs** in JS template strings — Python's triple-quoted strings resolve `\'` → `'`, breaking adjacent JS string literals. The fix is to use `\"` (escaped double quotes) or template literals.
- v0.9.2 (2026-05-06) was a major release: Authentik Reputation Policy, SSH Fail2ban, TAK Snapshots/Rollback, Console Rollback, TAK Plugins, **CVE-2026-31431 ("Copy Fail")** container hardening — `cap_drop: ALL`, `no-new-privileges`, removed `/var/run/docker.sock` from Authentik worker.
- v0.8.7–v0.8.9 was the great Authentik silent-default cleanup: `AUTHENTIK_WEB__WORKERS` (double underscore) bug, LDAP flow recursion (evaluate_on_plan=true), trusted-proxy-CIDRs (X-Forwarded-For was being discarded). Three releases of fleet-wide latent bugs, all caught by reading upstream docs and adding runtime verifiers. Motivated `.cursor/rules/consult-upstream-docs.mdc`.

## Open items / watch list

- **`flows.json` conflict on `git pull`** is a recurring footgun on tester VPSes. Recovery: `git checkout -- nodered/flows.json && git pull && bash nodered/deploy.sh --no-pull`.
- **TLS node `tls=undefined` in deploy log** for dynamic engine tabs created before the v0.6+ TLS fix — harmless cosmetic, resolves once those tabs are rebuilt via Configurator.
- **`Skipped configurator.html template injection (EACCES)`** — appears in deploy log when `build-flows.js` runs inside the container. Harmless (template injection already happened on the host).
- **Phase 0 spike (flat-file `nodered` user) — NOT YET RUN.** `docs/SPIKE-flatfile-nodered.md` has six curl tests (T0–T6) to determine whether a least-privilege flat-file user can replace `admin.pem` for Node-RED's TAK Mission API calls. Phase 1A (`scripts/bootstrap-nodered-flatfile.sh`) is wired defensively — code activates only if `/certs/nodered.pem` exists.
- **Non-root console migration** scaffolded but deferred from v0.9.2. `_sudo_wrap`/`_write_priv`/`_read_priv` helpers exist; user provisioning + `User=takwerx` in the systemd unit is the remaining work.

## Active decisions

- **No static feeds in `flows.json`.** `FEEDS=[]` in `build-flows.js` — non-negotiable. Real feeds are dynamic engine tabs, created via Configurator. Committing customer feed names (e.g., "CA AIR INTEL", "POWER-OUTAGES") would propagate them to every install.
- **Always use `nodered/deploy.sh`.** Never raw-`docker cp flows.json` into the container. The deploy script's safety gate validates the global-context backup before stopping the container; weakening that gate is forbidden.
- **Read upstream docs first.** When debugging Authentik / TAK Server / Caddy / Node-RED behavior, find the project's official docs and verification command (`ak dump_config`, `caddy adapt`, etc.) **before** building a workaround. The five-release silent-default chain is institutional memory; `.cursor/rules/consult-upstream-docs.mdc` codifies the rule.
- **Reuse what works first.** When adding a variant (remote deploy, new target, new env), try the existing local pattern with minimal tweaks (URLs, paths, where commands run). Don't invent a parallel flow.
- **Cardinal LDAP rule.** Authentik server + worker are recreated when env changes; the LDAP outpost (`authentik-ldap-1`) is **never** recreated as a side effect — only when its own config genuinely changed. v0.8.1 incident is the canonical reminder.

## Next steps

- Continue v0.9.5 work on `dev`: complete the split-server snapshot path, finish the upload/restore flow, ship the Authentik `shm_size` + tasklog purge timer, move the rollback banner to Guard Dog.
- Validate v0.9.5 on `tak-10` (already on `dev`, just restarted the console). Watch for the v0.9.5 migrations firing on next "Update Now" or restart.
- Once validated: bump VERSION on `dev`, write `docs/RELEASE-v0.9.5-alpha.md`, selective merge `dev` → `main`, tag, push.

## How to resume after a memory reset

1. Read `STATUS.md` (top of repo) for the current session's working state.
2. Read this file (`memory-bank/activeContext.md`) for context.
3. Read `memory-bank/progress.md` for what's known to work and what's known to be broken.
4. Re-read `.cursorrules` and `.cursor/rules/consult-upstream-docs.mdc` before touching Node-RED, Authentik, or any third-party config.
5. If the operator says **"update memory bank"** — review every file in `memory-bank/`, even if no changes are needed. Pay special attention to `activeContext.md` and `progress.md`.
