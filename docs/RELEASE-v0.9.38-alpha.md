# v0.9.38-alpha — CloudTAK plugin manager + LDAP 49 auto-heal + Cesium 3D Tiles + WebODM

**Date:** 2026-05-23
**Type:** New features + self-healing reliability — drop-in update via Update Now.
**Status:** RELEASED to `main` 2026-05-23. Validated on test8 + test12, both modules (Cesium 3D Tiles + WebODM) deployed and exercised end-to-end.

---

## TL;DR

Four independent improvements ship together in v0.9.38:

1. **CloudTAK plugin manager** — install, update, and remove community CloudTAK plugins directly from the infra-TAK CloudTAK page without touching the command line. Ships with the `clptak/cloudtak-plugin-cellphone` (Cell Ping / RTT) plugin in the catalog. Guard Dog now monitors installed plugins for upstream commits.

2. **LDAP 49 auto-heal** — when a user changes their password but TAK Server's internal bind-cache is still serving the stale failure, the ldap-sa watchdog detects the pattern and self-heals within 5 minutes. No operator action required.

3. **Cesium 3D Tiles** — new marketplace module. Streams 3D terrain, building models, and point clouds to ATAK and WinTAK clients over HTTPS. No new Docker container — Caddy's built-in `file_server` handles everything. Includes browser zip upload, dataset management, Remove button, and detailed operator instructions.

4. **WebODM** — new marketplace module. Deploys WebODM + NodeODM + the TAK Incident Overlay plugin on the VPS. Upload GPS-tagged drone photos, process them into georeferenced MBTiles/GeoTIFF overlays, and import directly into ATAK. Full lifecycle management: deploy with warmup health-check, version tracking with one-click update, account recovery, password-gated uninstall, and self-healing installed-state detection.

---

## CloudTAK Plugin Manager

### Background

CloudTAK plugins are client-side Vue 3 SFCs that Vite auto-discovers at build time by scanning `~/CloudTAK/api/web/plugins/`. Installing or removing a plugin requires cloning/deleting the repo there, then rebuilding and restarting the `api` container (`docker compose build --no-cache api && docker compose up -d --force-recreate api`), which takes 5–15 minutes. The CloudTAK UI is live during the build and only briefly restarts at the end.

After a plugin install or core CloudTAK update, the new service worker must be activated via **CloudTAK → Settings → Refresh App** (standard `Cmd+Shift+R` is intercepted by the service worker and does not work).

### What's new

- **Plugins section** on the CloudTAK page — lists all catalog plugins with author, license, version requirements, and GitHub link.
- **Install / Update / Remove buttons** with spinner feedback and button-disable during the operation. Buttons re-enable on error; page reloads automatically on success.
- **Live log streaming** — a scrolling log box appears below the plugin card during any action, following the same 1-second polling pattern used everywhere else in infra-TAK.
- **Update available detection** — on page load, `git ls-remote origin HEAD` is compared to the local HEAD for each installed plugin. If the remote is ahead, the Update button gets a cyan border + `●` dot and the SHA line shows `update available` — matching the visual language used for CloudTAK core and other services.
- **SHA display** — shows the short (7-char) git SHA of the installed commit.
- **Guard Dog notifications** — `tak-updates-watch.sh` now checks every installed plugin's remote HEAD. When an update is available it generates a notification in the same format used for all other infra-TAK update alerts.
- **Auto-deploy** — Guard Dog's plugin-checking logic is automatically deployed to target systems during normal infra-TAK console updates (via `_auto_update_guarddog()`). No manual script copying required.
- **Post-install SW hint** — the log box includes a clear reminder: use `Settings → Refresh App` inside CloudTAK after a plugin install; `Cmd+Shift+R` does not bypass the service worker.

### Plugin catalog (v0.9.38)

| Plugin | Author | Requires | Description |
|---|---|---|---|
| Cell Ping / RTT | clptak | CloudTAK 13.2+ | Plot cellphone tower coverage as CoT features. Cell Ping produces a u-d-c-c uncertainty circle; RTT produces a u-rb-a arc (±70° wedge). Posts directly to an active DataSync mission. |

### CloudTAK version detection fix

Upstream dfpc-coe applied the `v13.3.0` git tag before committing the matching `package.json` version bump, causing infra-TAK to read `13.2.0` from `package.json` and perpetually report an update available when already on the latest code. `_get_cloudtak_version_info()` now resolves version in this order:

1. `git describe --tags --exact-match HEAD` — authoritative when HEAD is at a release tag.
2. `package.json` — used when HEAD is between tags (development or pre-release).
3. `git describe --tags --always` — final fallback.

---

## LDAP 49 Auto-Heal

### Root cause

TAK Server's `DistributedPersistentGroupManager` caches each failed user bind result. After a password change, subsequent logins with the correct new password still hit that stale negative cache entry and return LDAP error code 49 (Invalid Credentials) — without the request ever reaching Authentik. The bind attempts don't appear in the Authentik LDAP outpost logs at all; they never get that far.

The only way to clear this cache is `docker compose up -d --force-recreate ldap`, which forces TAK Server to drop and re-establish its LDAP connection, flushing the cached failures. Prior to this release, an operator had to do this manually ("Sync webadmin" has this as a side effect, but it's not obvious and requires admin access).

### Fix

The existing ldap-sa watchdog (`_authentik_ldap_sa_bind_watchdog_loop`, runs every 5 minutes) now calls `_check_takserver_ldap49_and_heal` at the start of each tick. This function checks `/opt/tak/logs/takserver-api.log` for:

```
WARN c.bbn.marti.groups.LdapAuthenticator - exception during group assignment
javax.naming.AuthenticationException: [LDAP: error code 49 - Invalid Credentials]
```

**Threshold:** ≥ 2 of these lines within the last 6 minutes. One failure = normal wrong-password typo, no action. Two or more = pattern consistent with a stuck cache.

When triggered, the console runs `--force-recreate ldap` automatically, logs the event, and records the flush count + timestamp in `settings.json` (`ldap49_cache_flush`) for operator visibility. A 4-minute cooldown prevents repeated flushes within a single watchdog cycle. Supports both local and remote Authentik deployments.

### Operator experience

**Before:** user changes password → still can't log in → admin must manually trigger "Sync webadmin" or know to restart the LDAP outpost.

**After:** user changes password → retries once or twice → console self-heals within the next watchdog tick (up to 5 minutes) → user retries and succeeds. Zero operator action required in the normal case.

---

## Cesium 3D Tiles

New marketplace module. Serves any [Cesium 3D Tiles](https://github.com/CesiumGS/3d-tiles) dataset (`tileset.json` + `.b3dm`/`.pnts`/etc.) to TAK clients over HTTPS. No new Docker container — Caddy's built-in `file_server` handles all serving.

### What's new

- **Marketplace entry** — "Cesium 3D Tiles" card at `/marketplace`; click Deploy to go to `/cesium-tiles`.
- **Enable / disable** — creates `~/cesium-tiles/` and adds a `file_server` vhost at `3dtiles.<fqdn>` to the Caddyfile (with `Access-Control-Allow-Origin: *` required by ATAK's WebView).
- **Browser zip upload** — drag-and-drop or browse; XHR with progress bar; server streams zip to temp file, validates `tileset.json` present, strips single wrapping directory if present, extracts to `~/cesium-tiles/<name>/`.
- **Dataset table** — name, size, tile count, one-click copy-paste `tileset.json` URL, delete button.
- **SVG logo** — bundled `3DTiles_light_color.svg` used on console card, sidebar, and page header (no separate text label needed — logo contains the wordmark).
- **Collapsible upload instructions** — step-by-step guide for naming conventions, browser upload, SFTP upload, and copying the ATAK URL. Naming step explicitly points to the Dataset Name field in the UI to avoid confusion with folder naming.
- **Remove button** — modal confirmation + `shutil.rmtree(~/cesium-tiles)` + settings flag cleared + Caddy regenerated.
- **ATAK / WinTAK connection walkthrough** — collapsible step-by-step instructions sourced from TAK Developers Confluence.
- **Sidebar link** — appears when enabled, uses the SVG logo.
- **Startup migration** — recreates `~/cesium-tiles/` on boot if it was deleted while the module is enabled.

### ATAK connection steps

1. ATAK → Map Manager → MOBILE tab → Down arrow → (+) Add
2. Paste: `https://3dtiles.<fqdn>/<dataset>/tileset.json`
3. ATAK discovers the tileset — tick checkbox to import
4. Dataset available under Overlay Manager as a 3D layer

### API surface

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/api/cesium-tiles/enable` | Enable module, create dir, regen Caddyfile |
| `POST` | `/api/cesium-tiles/disable` | Disable module, regen Caddyfile |
| `GET` | `/api/cesium-tiles/datasets` | List datasets with size, tile count, URL |
| `POST` | `/api/cesium-tiles/upload` | Streamed zip upload + extract |
| `DELETE` | `/api/cesium-tiles/datasets/<name>` | Delete a dataset directory |
| `POST` | `/api/cesium-tiles/uninstall` | Password-gated full removal |

---

## WebODM

New marketplace module. Deploys WebODM + NodeODM (nodeodx image) + the TAK Incident Overlay plugin by [Humble-Helper-96](https://github.com/Humble-Helper-96/webodm-tak-overlay) on the VPS with a single click.

### Operator workflow

1. **Marketplace** → Deploy WebODM → deploy log streams image pull + plugin clone + container start + Authentik provisioning + NodeODM registration.
2. **Warmup phase** — after deploy completes, the page enters a health-check polling phase instead of immediately reloading. Every 5 seconds `/api/webodm/ready` is called, which checks Docker's container healthcheck (`healthy` state) and falls back to `curl http://localhost:8000/api/` from inside the container. The "Open WebODM" button only appears and lights up when the check actually passes — no guessing when it's safe to click.
3. **First visit** — WebODM prompts for a new admin username and password (fresh database on every deploy — see below).
4. **Management page** — shows running status, version, update availability, and direct Open link. Buttons: Open WebODM, Update, Repair Authentik, Account Recovery, Uninstall.

### Key technical decisions

**Database isolation:** The Postgres data directory is bind-mounted to `~/webodm/db/` (not a named Docker volume). Uninstall wipes this directory so a fresh redeploy always starts with an empty database and prompts for new admin credentials. `~/webodm/media/` (processed jobs / outputs) is preserved across uninstall/reinstall.

**Environment variable correctness:** WebODM's `settings.py` reads `WO_DATABASE_HOST` (double-underscore-free) to locate Postgres. The compose template uses the service name `wo_db` as the value. A startup migration patches any existing compose files that used the incorrect `WO_DB_HOST` name (which was silently ignored).

**Plugin path:** WebODM discovers plugins from `MEDIA_ROOT/plugins/` (maps to `/webodm/app/media/plugins/` in the container). The TAK overlay plugin is bind-mounted there, not to the legacy `app/plugins/` path.

**NodeODM registration:** Done via `docker exec webapp python manage.py addnode wo_nodeodm 3000 --label NodeODX` (Django management command, direct DB write, no auth credentials required, idempotent on duplicate hostname). Replaces the fragile HTTP API approach using default `admin/admin` credentials.

**Port hardening:** `wo_nodeodm` exposes no host ports (internal only). `wo_webapp` binds to `127.0.0.1:{port}` only. Per PORT-EXPOSURE-POLICY.md.

**Authentik integration:** On deploy, the console auto-provisions an Authentik proxy provider + application for WebODM using the same pattern as other modules. A "Repair Authentik" button re-runs this provisioning if the integration drifts.

**Self-healing installed state:** `detect_modules()` checks whether the `webapp` Docker container is running even when `webodm_enabled = False`. If containers are up but the flag is cleared (interrupted uninstall, interrupted deploy), the flag is automatically set back to `True` and the management page is shown — no manual intervention required.

### Features

- **Version tracking** — reads version from `package.json` inside the running container; checks GitHub releases API for latest. Shows current version and "vX.Y.Z available" badge on both the console card and the management page.
- **One-click update** — "Update to vX.Y.Z" button runs `docker compose pull` + `docker compose up -d` with a live log. Page reloads when complete.
- **Account Recovery** — modal shows all Django superuser accounts (fetched via `manage.py shell`); operator selects a username, enters a new password (min 8 chars, confirmation field). Executed via `docker exec manage.py shell -c "u.set_password(...)"`.
- **Password-gated uninstall** — uninstall modal requires the infra-TAK admin password (same gate as Node-RED, MediaMTX, CloudTAK). Wrong password returns `403` and the modal stays open. On success: `docker compose down --volumes`, wipe `~/webodm/db/`, clear `webodm_enabled`, regenerate Caddyfile, reload Caddy.

### Startup migration

On every console restart, if `webodm_enabled = True`:
- Patches any existing `docker-compose.yml` missing `WO_DATABASE_HOST=wo_db`.
- Strips any host port binding from `wo_nodeodm` (port-hardening migration).
- Removes any empty `ports:` key left by the stripping (prevents YAML parse error).
- Runs `docker compose up -d` to apply changes without restarting unaffected containers.

### API surface

| Method | Route | Description |
|--------|-------|-------------|
| `POST` | `/api/webodm/deploy` | Start background deploy |
| `GET` | `/api/webodm/deploy-status` | Poll deploy log + state |
| `GET` | `/api/webodm/ready` | Container healthcheck for warmup phase |
| `GET` | `/api/webodm/admin-accounts` | List Django superusers |
| `POST` | `/api/webodm/reset-password` | Set password for a superuser |
| `POST` | `/api/webodm/update` | Start background image pull + restart |
| `GET` | `/api/webodm/update-status` | Poll update log + state |
| `POST` | `/api/webodm/repair-authentik` | Re-provision Authentik proxy provider/app |
| `POST` | `/api/webodm/uninstall` | Password-gated teardown |

---

## Changes

### `app.py`

- `VERSION` bumped `0.9.37-alpha` → `0.9.38-alpha`.
- **LDAP 49 auto-heal:** `_check_takserver_ldap49_and_heal()`, integrated into `_authentik_ldap_sa_bind_watchdog_loop()`.
- **CloudTAK plugin manager:** `CLOUDTAK_PLUGINS` catalog, `_detect_cloudtak_plugins()`, `_run_cloudtak_plugin_action()`, plugin API routes, plugin section in `CLOUDTAK_TEMPLATE`, Guard Dog script update.
- **`_get_cloudtak_version_info()`** — `git describe --tags --exact-match` priority over `package.json`.
- **Cesium 3D Tiles:** `CESIUM_TILES_LOGO_URL`, `detect_modules()` entry, sidebar link, Caddyfile stanza, startup migration, page route, 6 API endpoints, `CESIUM_TILES_TEMPLATE`.
- **WebODM:** `WEBODM_DOCKER_COMPOSE` template, `_run_webodm_deploy()`, `_get_webodm_version_info()`, `_run_webodm_update()`, `_run_webodm_update_status`, `webodm_ready()`, `webodm_admin_accounts()`, `webodm_reset_password()`, `webodm_update()`, `webodm_uninstall()`, `WEBODM_TEMPLATE`, startup migration block.
- **`detect_modules()` WebODM self-heal** — checks `webapp` container state; auto-sets `webodm_enabled = True` if containers are running but flag is cleared.

### `scripts/guarddog/tak-updates-watch.sh`

- Plugin update detection block: iterates installed CloudTAK plugins, compares local vs remote HEAD, generates notification when behind.

### `static/3DTiles_light_color.svg`

- Bundled Cesium 3D Tiles SVG logo (includes wordmark).

### `docs/COMMANDS.md`

- Added row: "CloudTAK / ATAK / TAK app login fails after password change" — explains the self-heal and the Sync-webadmin fast path.

---

## Backward compatibility

- No config changes required on update.
- `ldap49_cache_flush` is written to `settings.json` only after the first flush event.
- Boxes without TAK Server installed skip the LDAP 49 check entirely.
- CloudTAK plugin section only renders when CloudTAK is installed locally.
- Cesium 3D Tiles and WebODM are opt-in (disabled by default); existing installations unaffected until operator enables from Marketplace.
- WebODM startup migration is a no-op on boxes where WebODM was never deployed.
- `~/webodm/media/` (processed jobs) is always preserved — only `~/webodm/db/` is wiped on uninstall.
