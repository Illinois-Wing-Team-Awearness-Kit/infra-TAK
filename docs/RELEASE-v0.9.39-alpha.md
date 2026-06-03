# v0.9.39-alpha — Atomic Authentik upgrade + Guard Dog rollback fix + WebODM remote deploy

**Date:** 2026-05-24 / 2026-05-25
**Type:** Reliability fix + new feature + UX fixes — drop-in update via Update Now.
**Status:** RELEASED to `main` 2026-05-25. Validated on test6 (13 h) + test8 (11 h, fresh 2026.2.3→2026.5.0 upgrade via fixed sequence) + test12 (18 h). All three boxes: zero `query_wait_timeout`, zero watchdog ALERTs, zero `column does not exist` errors, zero `exceeded stage recursion depth` errors, all containers `(healthy)`. Authentik version gate unchanged — `AUTHENTIK_VETTED_RELEASE="2026.2.3"` (main channel ceiling).

---

## TL;DR

Three things in this release:

1. **Atomic Authentik upgrade sequence** — `pull → down → up` instead of `pull → up -d`. Eliminates the mixed-container schema-mismatch window where `server-1` ran migrations on the new image while `worker-1` was still on the old one. Discovered by Tom Endress on 2026-05-24 when upgrading 2026.2.3 → 2026.5.0 on a dev-channel box.

2. **Guard Dog rollback button** — `doConsoleRollback` was defined only in the main console page JS, not in `guarddog.js`. The button threw `ReferenceError` on every click. Fixed + added password confirmation modal (matching the uninstall/remove pattern used across all other destructive actions).

3. **WebODM remote deployment** — WebODM can now be deployed to a remote host via SSH, using the same deployment-target infrastructure as Node-RED, MediaMTX, Authentik, and CloudTAK. Deployment Target card on the WebODM page with SSH host/user/port/auth fields, generate/install key, test connection, and Save. Includes uninstall spinner (no more silent button), and fixes for the remote host toggle error flash.

---

## Background: the mixed-container schema-mismatch bug

**Symptom (Tom Endress, 2026-05-24):**
Upgrading from Authentik 2026.2.3 → 2026.5.0 via the Update button on a dev-channel box caused a continuous worker error:
```
column authentik_providers_saml_samlprovider.issuer does not exist
```
`server-1` came up on 2026.5.0 first, ran a Django migration that dropped the `issuer` column, and `worker-1` (still the old 2026.2.3 container) crashed against the migrated schema.

**Root cause:** The old upgrade sequence was `docker compose pull && docker compose up -d`. `up -d` recreates containers in dependency order — `server` first. There was a window where server was on the new image and worker was still the old container.

**Why test6 never hit this:** test6 was manually set to 2026.5.0 before the Update button path existed for this version bump. The path was never exercised until Tom's test.

**Fix:**
```
docker compose pull → docker compose down --timeout 30 → docker compose up -d
```
All old containers stop before any new-version container starts. Applied to both local and remote Authentik update paths. Timeout raised 300 → 360 s to cover the down cycle.

---

## Changes

### Core: atomic Authentik upgrade (`ded1a61`)
- `VERSION` bumped `0.9.38-alpha` → `0.9.39-alpha`
- `authentik_control()` local update path: `pull && down --timeout 30 && up -d && image prune` (was `pull && up -d && image prune`); subprocess timeout 300 → 360 s
- `authentik_control()` remote update path: same sequence via `_ssh_probe`; timeout 300 → 360 s

### Guard Dog rollback fix (`8ecfb98`)
- **Bug:** `doConsoleRollback` defined in main console page inline `<script>` — not in `guarddog.js`. Guard Dog page loads only `guarddog.js` + `log-tools.js`. Click → `ReferenceError: doConsoleRollback is not defined`.
- **`guarddog.js`:** added `doConsoleRollback()` (opens modal) + `doConsoleRollbackSubmit()` (posts password, shows spinner, reloads after 10 s on success, re-enables on error)
- **Guard Dog template:** added `gd-rollback-modal` with password input, Cancel/Roll Back buttons, Enter-key submit — matches `gd-uninstall-modal` pattern
- **`/api/console/rollback`:** added `check_password_hash` verification (HTTP 403 on wrong password) before any git operations

### WebODM remote deployment (`02c4d0e`, `5875a48`, `9aade85`, `df22cd8`)
- **`_run_webodm_deploy_remote(settings, deploy_cfg, plog)`:** full remote deploy via SSH — resolves remote `$HOME` (Docker doesn't expand `~` in volume paths), checks/installs Docker, `mkdir -p`, git-clones TAK overlay plugin, writes + SCPs `docker-compose.yml`, pulls images, UFW hardens ports, `up -d`, `curl` readiness probe, NodeODM `addnode` registration. Caddy reload and Authentik setup run on the local console host (same as all other remote modules).
- **`_run_webodm_deploy()`:** branches on `target_mode == 'remote'` at entry
- **`generate_caddyfile()`:** WebODM upstream uses `{remote_host}:{wo_port}` when remote mode, `127.0.0.1:{wo_port}` otherwise
- **`webodm_uninstall()`:** `docker compose down --volumes` + `rm -rf db/` on remote via SSH; local path unchanged
- **`webodm_page()`:** passes `wo_deploy_cfg` to template
- **`_register_module_remote_routes('webodm', 'webodm_deployment')`:** five generic REST endpoints registered (`GET/POST /api/webodm/deployment-config`, `POST /api/webodm/remote/ensure-ssh-key`, `POST /api/webodm/remote/install-ssh-key`, `POST /api/webodm/remote/test`)
- **`WEBODM_TEMPLATE`:** Deployment Target card with "This server" / "Remote host" toggle + SSH fields; "Deploy WebODM" card title shows `→ {host}` when remote; toggle is purely visual (no auto-save to prevent error flash on empty host); Save button is the only write path
- **Uninstall spinner:** modal now has spinner + progress row, Cancel/Confirm disabled during operation, Enter-key submits password, error re-enables buttons without reload
- **Toggle error flash fix:** `woSetTarget` no longer fires `woSaveConfig` in either direction; `woMsg('')` clears leftover messages on toggle

---

## What this does NOT do

- Does **not** promote `AUTHENTIK_DEV_RELEASE` → `AUTHENTIK_VETTED_RELEASE`. `AUTHENTIK_VETTED_RELEASE="2026.2.3"` is unchanged. Main-channel customers stay on 2026.2.3. The Authentik version gate promotion is a separate future decision.
- Does **not** change any Authentik container configuration, compose template, PgBouncer tuning, or watchdog thresholds.

---

## T&E results

| Box | Authentik | Soak | idle_in_tx | query_wait_timeouts | worker errors | LDAP recursion |
|---|---|---|---|---|---|---|
| test6 | 2026.5.0 | 13 h | 0 | 0 | 0 | not seen |
| test8 | 2026.5.0 | 11 h (fresh upgrade via fixed sequence) | 0 | 0 | 0 | not seen |
| test12 | 2026.5.0 | 18 h | 0 | 0 | 0 | not seen |

test8 validated the core fix: 2026.2.3 → 2026.5.0 upgrade via the Update button using the new `pull → down → up` sequence. All containers came up simultaneously on 2026.5.0 with clean migrations and zero schema-mismatch errors. The LDAP `exceeded stage recursion depth` issue Tom observed was not reproduced on any of the three T&E boxes.
