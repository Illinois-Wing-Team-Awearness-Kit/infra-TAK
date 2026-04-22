# Release Notes — v0.7.1-alpha

## Critical Bug Fix: Configurator configs wiped on Update Now

### Root Cause

Older installs (anything deployed before the `contextStorage` setting was added to the
`settings.js` template) stored Node-RED global context **in memory only**.

Node-RED's default `contextStorage` is `memory`. With memory storage:

- All global context (every Configurator config — ArcGIS layers, KML feeds, TAK settings, IPAWS)
  lives only in the running container's RAM.
- Any container stop/start or restart **wipes the entire context with no recovery path**.
- The file `/data/context/global/global.json` does not exist, because Node-RED never writes it.

During `Update Now` → `_auto_nodered_flows()` → `deploy.sh`:

1. `deploy.sh` tries `docker cp nodered:/data/context/global/global.json /tmp/nr_ctx_global.json`
2. The file doesn't exist → `docker cp` fails silently (`2>/dev/null || true`)
3. `/tmp/nr_ctx_global.json` is never created
4. `deploy.sh` stops the container (context gone from memory)
5. Restore step skips (file doesn't exist on host)
6. Node-RED starts with empty context
7. Init nodes run with default empty values → all configs appear wiped

### What Was Fixed

**`app.py` — `_auto_nodered_settings()`**

- Added detection for missing `contextStorage` key in `settings.js`.
- Before adding `contextStorage: { default: { module: 'localfilesystem' } }`, the function
  now calls the Node-RED REST API (`GET http://localhost:1880/context/global`) on the
  running container to export the live in-memory context.
- Writes that JSON to `/data/context/global/global.json` inside the container.
- Then updates `settings.js` and restarts Node-RED with the new storage setting.
- On restart, Node-RED immediately loads from the file — **no config loss**.
- All subsequent deploys then use filesystem storage and are fully safe.

**`nodered/deploy.sh`**

- Changed the context backup strategy: **REST API first, file fallback**.
- `docker exec nodered curl -sf http://localhost:1880/context/global` is called on the
  still-running container before it is stopped. This returns the full live in-memory
  context regardless of whether `localfilesystem` or `memory` storage is configured.
- If the API returns valid JSON (non-empty, non-`{}`), it is written to the host temp file.
- If the API is unavailable (e.g., container just started), falls back to `docker cp` of
  the on-disk `global.json` file.
- Restore step now explicitly creates the `/data/context/global` directory inside the
  container before copying, preventing failures on fresh volumes.

### Migration Behavior

On the **first Update Now after this release**, installs without `contextStorage` will:

1. `_auto_nodered_settings()` detects missing key.
2. Exports in-memory context via API → writes to disk **while Node-RED is still running**.
3. Adds `contextStorage: localfilesystem` to `settings.js`.
4. Restarts Node-RED → loads context from file.
5. `deploy.sh` runs, backs up via API, deploys flows, restores → **configs preserved**.

All subsequent Update Now runs are safe because context is now on disk.

### Files Changed

| File | Change |
|------|--------|
| `app.py` | `VERSION` → `0.7.1-alpha`; `_auto_nodered_settings()` adds `contextStorage` with pre-export |
| `nodered/deploy.sh` | Context backup uses REST API first; restore ensures directory exists |
| `README.md` | Latest release updated |
| `docs/RELEASE-v0.7.1-alpha.md` | This file |
