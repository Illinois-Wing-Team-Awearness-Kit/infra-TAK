# v0.7.3-alpha Release Notes

> **Note:** v0.7.1-alpha and v0.7.2-alpha were dev milestones that never shipped to main. All of their changes are included here in this release.

---

## ⚠️ ACTION REQUIRED FOR EXISTING DEPLOYMENTS

After pulling this update, go to the **TAK Server page → Resync LDAP to TAK Server**.

This fixes password changes taking up to 24 hours to take effect on ATAK/iTAK devices. The fix is applied automatically every time Resync runs — one click and your deployment is current.

---

# Part 1 — Critical: Node-RED Configs Wiped on Update Now (v0.7.1)

## What Was Fixed

Older installs stored Node-RED global context **in memory only** (default `contextStorage`). Any container restart wiped every Configurator config — ArcGIS layers, KML feeds, TAK settings, IPAWS — with no recovery path.

During **Update Now**, `deploy.sh` tried to `docker cp` the context file out of the container before stopping it. The file didn't exist (memory storage never writes it), so the backup silently failed, the container stopped, and on restart all configs appeared erased.

**Fix (`app.py` — `_auto_nodered_settings()`):**
- Detects missing `contextStorage` in `settings.js`
- Before patching, exports the **live in-memory context** via the Node-RED REST API (`GET /context/global`) while the container is still running
- Writes it to `/data/context/global/global.json` inside the container
- Then adds `contextStorage: localfilesystem` to `settings.js` and restarts
- On restart Node-RED loads from the file — **no config loss**
- All subsequent deploys are safe because context is now on disk

**Fix (`nodered/deploy.sh`):**
- Context backup now uses **REST API first** (`docker exec nodered curl http://localhost:1880/context/global`) instead of relying on the file existing
- Falls back to `docker cp` only if the API is unavailable
- Restore step creates the `/data/context/global/` directory before copying, preventing failures on fresh volumes

**Migration:** On the first Update Now after this release, installs without `contextStorage` automatically export context to disk, patch `settings.js`, and restart — configs preserved with no operator action.

| File | Change |
|------|--------|
| `app.py` | `_auto_nodered_settings()`: detect missing contextStorage, export live context via API before patching |
| `nodered/deploy.sh` | REST API backup first; restore creates directory; file fallback retained |

---

# Part 2 — Tablet Command AVL Integration (v0.7.2)

## Tablet Command AVL → ATAK CoT Streaming

Agencies using [Tablet Command](https://tabletcommand.com) can now stream vehicle positions (fire engines, ambulances, command vehicles, helicopters) directly into ATAK as live CoT events.

- In the Configurator, click **Tablet Command AVL** → fill in your agency name and Feature Service URL
- Click **Deploy & Activate** — a dedicated Node-RED tab is created for that agency
- The tab polls the Tablet Command FeatureServer every 1–5 minutes (configurable)
- Each vehicle becomes a live CoT point on the TAK map, updating in place

**CoT type auto-detection** based on radio name prefix:

| Prefix | CoT Type |
|--------|---------|
| `E`, `ENG` | Engine (a-f-G-E-V-C) |
| `T`, `TRK`, `LAD` | Truck/Ladder (a-f-G-E-V-C) |
| `M`, `MED`, `AMB`, `ALS`, `BLS` | Medic (a-f-G-E-V-M) |
| `BC`, `BAT`, `CHIEF`, `AC`, `DC` | Chief/Command (a-f-G-E-V-C) |
| `H`, `HELO`, `AIR`, `HT` | Helicopter (a-f-A-C-H) |
| `WT`, `WAT`, `WATER` | Water Tender (a-f-G-E-V-C) |
| `RES`, `RESCUE`, `SQ`, `SQUAD` | Rescue/Squad (a-f-G-E-V-C) |

**Known Units / Remapping Table:**
Each agency config has a per-agency remapping table to override radio names with custom callsigns (e.g. `CA342` → `Corona Engine 42`) and override CoT types for specific units. Upload/download as CSV (`radioName,callsign,cotType`).

**Multi-agency:** Each agency gets its own named config card, its own Node-RED tab, and its own remapping table.

| File | Change |
|------|--------|
| `nodered/build-flows.js` | `makeTCEngineTab()`, TC config persistence nodes, TC template injection |
| `nodered/configurator.html` | TC source button, TC panel, TC JavaScript helpers |

---

# Part 3 — Immediate Password Propagation via LDAP Session Fix

## What's New

### Password Changes Now Take Effect Immediately

**Problem (pre-v0.7.3):** When a user or admin reset a password in TAK Portal (or directly in Authentik), the new password could take up to 24 hours to work on iTAK/ATAK. The old password continued to authenticate for the remainder of the cached session.

**Root cause:** Authentik's LDAP outpost runs with `bind_mode: cached`. A successful bind is cached for the lifetime of the user's authentication session. The `ldap-authentication-login` User Login stage had `session_duration: seconds=0`, which Authentik interprets as a full browser session — effectively ~24 hours for LDAP binds. Password changes made via TAK Portal (which directly call Authentik's `set_password` API) do not invalidate existing cached sessions.

**Fix:** Set `session_duration: seconds=120` on the `ldap-authentication-login` stage. Cached bind sessions now expire in 2 minutes. A user who changes their password will be able to authenticate with the new password within 2 minutes of the reset.

This is the correct and only reliable knob for this behavior. `token_validity` on the LDAP provider (a previous investigation path) is silently ignored by Authentik for LDAP providers — it only applies to OAuth/proxy providers.

### Why Not `bind_mode: direct`?

Direct mode re-authenticates against Authentik on every single LDAP bind — which TAK Server issues on a ~2-second polling cycle. At scale (hundreds of users, active sessions) this overwhelms the Authentik worker. Cached mode with a short session duration is the correct tradeoff: low resource usage, fast propagation.

### Self-Healing via Resync

The fix is enforced every time **Resync LDAP to TAK Server** runs — the console looks up `ldap-authentication-login` by name and patches `session_duration` regardless of its current value. Existing deployments that ran Resync after pulling this update are already fixed. Fresh deploys get the correct value baked into the blueprint.

## What Changed

| File | Change |
|------|--------|
| `app.py` | Blueprint YAML: `session_duration: seconds=0 → seconds=120` (two copies) |
| `app.py` | `_create_ldap_stage()` call: `seconds=0 → seconds=120` (fresh install path) |
| `app.py` | New unconditional PATCH in `_ensure_ldap_flow_authentication_none()`: looks up `ldap-authentication-login` and patches `session_duration=seconds=120` on every Resync run |
| `app.py` | Removed `token_validity: minutes=2` from all `providers/ldap` PATCH calls (wrong field, was silently ignored) |
| `app.py` | VERSION bumped to `0.7.3-alpha` |

## Operator Action Required

**For existing deployments:** After pulling this update, go to the TAK Server page → **Resync LDAP to TAK Server**. This patches the live Authentik stage immediately. Verify with:

```bash
TOKEN=$(grep AUTHENTIK_BOOTSTRAP_TOKEN ~/authentik/.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:9090/api/v3/stages/user_login/?search=ldap' | \
  python3 -c "import sys,json; r=json.loads(sys.stdin.read())['results']; [print(f'name={s[\"name\"]} session_duration={s.get(\"session_duration\")}') for s in r]"
```

Expected output: `name=ldap-authentication-login session_duration=seconds=120`

**Fresh deployments:** No action needed — the blueprint sets the correct value at deploy time.

## Testing

1. Create a user in TAK Portal, set a password, log in on ATAK/iTAK — should work immediately.
2. Reset the password to a new value in TAK Portal.
3. Try the new password on the device — should authenticate immediately.
4. Wait ~2 minutes, then try the old password — should be rejected.

## Files Changed

- `app.py` — fix + VERSION bump
- `docs/RELEASE-v0.7.3-alpha.md` — this file
- `README.md` — latest release updated
- `STATUS.md` — session state updated
- `docs/HANDOFF-LDAP-AUTHENTIK.md` — LDAP cache behavior documented

---

# Part 4 — Node-RED Configurator: ArcGIS Save Fix, Multi-Agency PulsePoint, UI Polish

## Critical Bug Fix: ArcGIS Save Hanging Indefinitely

**Problem:** Clicking **Save & Generate Config JSON** in the ArcGIS configurator caused the UI to display "Saving…" and hang forever. The form never collapsed, no success message appeared. This affected every ArcGIS, TFR, and KML config save attempt.

**Root cause (from Node-RED logs):**
```
[error] [function:Save to global context]
TypeError: configs.findIndex is not a function
```

The `fn_save` function node read `global.get('arcgis_configs')` and assumed it was a JavaScript array. After a deploy/restore cycle, the context value was silently stored as a stringified array (`"[]"`) or a localfilesystem REST API envelope (`{msg: "[]", format: "..."}`) rather than a real array. Calling `.findIndex()` on a string throws `TypeError`, the function node never returns `msg`, the http-response node never fires, and the HTTP request hangs until timeout.

`fn_load` had a 3-line unwrap guard (which is why `/arcgis-tak/config/load` returned correctly). The save and all other mutator nodes did not.

**Fix (`nodered/build-flows.js`):**
- New `ARRAY_GUARD` constant defines a `_coerceArr(v)` helper that handles all three rotten formats: `{msg, format}` envelope, double-serialized string, and anything-not-an-array → `[]`
- Injected into: `fn_save` (ArcGIS/TFR/KML), `fn_tc_save`, `fn_tc_delete`, `FN_PP_SAVE`, `FN_PP_LOAD`, `FN_PP_DELETE`
- `unwrapCtxVal` in `/config/deploy-restore` now also handles bare strings — this was the source of the corruption, writing stringified arrays into context on every deploy, planting the landmine that `fn_save` tripped on

**Commits:** `2d11f8a`

## ArcGIS Save UX: 15s Timeout, Console Diagnostics, Race-Free Collapse

Even before the root cause was identified, a series of frontend improvements were made to `generateConfig()`:

- **Hard 15s AbortController timeout** on the save fetch — the UI now shows `"Save timed out after 15s — check Node-RED is running"` instead of hanging silently forever
- **Console diagnostics**: `[save] POST /arcgis-tak/config/save <name>`, `[save] response {...}`, `[save] FAILED <error>` logged at every step for visibility
- **Reordered post-save flow**: reload saved-config list → collapse form → fire ensureEngineTab in background. Previously `ensureEngineTab` triggered a full Node-RED redeploy mid-sequence, causing `/arcgis-tak/config/load` to briefly 502 and blocking the UI collapse
- **`loadSavedConfigs` hardened**: new `_fetchT(url, opts, ms)` helper wraps every Node-RED API call with a 10s AbortController timeout. A single stuck endpoint can no longer hang the page on load or after save. IPAWS/PP/TAK-settings fetches now run in parallel via `Promise.allSettled`

**Commits:** `d23f96e`

## Multi-Agency PulsePoint (like Tablet Command)

PulsePoint was refactored from a single global configuration to a **multi-agency model** matching the Tablet Command design:

- Each agency gets its own named config card in Saved Configurations
- Each agency has its own independent Node-RED engine tab, its own CoT TCP streaming port, and its own PulsePoint API credentials
- Configs stored as `pp_configs[]` array in global context (was a single flat `pulsepoint_config` object)
- Migration: existing single `pulsepoint_config` is automatically migrated to the first entry in `pp_configs[]` on first load
- New API endpoints: `POST /pp/config/save`, `POST /pp/config/delete`, `GET /pp/config/load`
- Each agency's engine tab polls the PulsePoint CAD API, builds CoT for active incidents, and streams to a dedicated TCP port on the TAK server

**Commits:** `fce3a0b`, `68f28e5`

## ArcGIS DataSync Form: Field Visibility by Mode

When **Data Sync Mission** is checked in Step 5 (TAK Integration & Save):
- **CoT TCP stream port** field is hidden (not relevant for DataSync)
- **Saved-list note** field is hidden (streaming-only label)
- **Strict mission ownership** block remains visible

When **Data Sync Mission** is unchecked (broadcast/channel streaming mode):
- Stream port and saved-list note fields are shown
- Strict mission ownership block is hidden

`toggleDataSync()` enforces this on checkbox change, form reset, and config load.

**Commits:** `8e1f196`

## UI: PulsePoint and Tablet Command in Top Nav

PulsePoint and Tablet Command logos and labels are now displayed in the top navigation bar of the Configurator alongside the existing source buttons, providing quick one-click access to their configuration panels.

**Commits:** `4eb7e57`

## Color Input Warning Spam Fixed

Typing a hex color value (e.g. `#FFd500`) into the text companion input was generating a browser console warning on every keystroke:
```
The specified value "#FFd50" does not conform to the required format.
```
This happened because the `oninput` handler was pushing every partial value directly to `<input type="color">`, which rejects anything not matching `#rrggbb`.

**Fix:** New `syncColorPick(pickId, v)` helper only writes to the color picker when the value matches `/^#[0-9A-Fa-f]{6}$/`. Applied to ArcGIS, TFR, and KML stroke/fill text inputs.

**Commits:** `d23f96e`

---

# Part 5 — External / Managed Database Deployment Mode

## New: AWS RDS, Azure, and Cloud SQL Support

TAK Server can now be deployed against an **externally-hosted PostgreSQL** instance — AWS RDS, Azure Database for PostgreSQL, Google Cloud SQL, or any network-reachable PostgreSQL 15 server. This is a third deployment option alongside the existing Single Server and Split Two-Server modes.

**How it works:**
- infra-TAK installs the full `takserver_X.X_all.deb` package on the TAK Server VM
- TAK Server's built-in SchemaManager creates all tables in the remote `cot` database on first boot — no manual schema scripts
- infra-TAK patches `CoreConfig.xml` with the remote JDBC endpoint and credentials
- Guard Dog monitors the external endpoint via TCP and `pg_isready` — no SSH to a managed DB server; alerts direct you to the cloud console instead

**UI (TAK Server → Deploy TAK Server):**
- New **External / Managed DB** radio button (third option)
- Fields: DB Endpoint, Port, Database Name, Username, Password
- **Test Connection** button — runs TCP, `pg_isready`, and psql auth checks from the TAK Server VM before deploy
- Upload hint updates automatically for the correct `.deb` package (`takserver_X.X_all.deb`, same as One Server)

**Guard Dog:**
- Alert emails for managed DB failures include cloud console guidance instead of SSH restart commands
- TCP-only monitoring (no SSH key required for external DB mode)

See `docs/EXTERNAL-DB-SETUP.md` for full pre-flight SQL, network/firewall steps, and PostgreSQL parameter tuning for each cloud provider.

**Commits:** `ba2f269`

| File | Change |
|------|--------|
| `app.py` | `_tak_deployment_defaults` external_db block; `deploy_takserver` external_db flag; JDBC patch for external_db; `POST /api/takserver/external-db/test-connection` route; Guard Dog sync for external DB |
| `static/takserver.js` | Third radio button, external-db-config-panel, `saveExternalDbConfig`, `testExternalDbConnection`, all mode-aware helpers updated |
| `scripts/guarddog/tak-remotedb-watch.sh` | `EXTERNAL_DB_PLACEHOLDER`; contextual alert body for managed vs two-server |
| `docs/EXTERNAL-DB-SETUP.md` | New — full setup guide for RDS/Azure/Cloud SQL |

---

# Part 6 — Certificate Auto-Renewal Display Fixes

## Cert Expiry Card: No More False Alarms

The Caddy / TAK Server certificate expiry display was previously turning orange at 40 days remaining — well before any renewal was needed, creating unnecessary alerts.

**How renewal actually works:**
1. Caddy auto-renews the Let's Encrypt cert at ~30 days remaining
2. The `takserver-cert-renewal` systemd timer (monthly) runs `/opt/tak/renew-letsencrypt.sh` at ≤35 days — it reloads Caddy (triggering renewal), waits, then rebuilds the TAK JKS keystore from the fresh cert and restarts TAK Server
3. Both the Caddy cert and the JKS are the **same cert**, rebuilt in the same script run — same expiry date

**Old behavior:** UI turned yellow/orange at 40 days (before renewal), red at 14 days  
**New behavior:** Green at ≥30 days (renewal hasn't needed to fire), **red at <30 days** (renewal ran and failed — action required). No yellow/orange intermediate state.

**Guard Dog alert threshold** also corrected: was ≤40 days (fires before renewal runs), now ≤25 days (fires only if renewal already had its chance and failed).

**Commits:** `087df32`, `3b787fe`, `482b004`, `19f048e`

| File | Change |
|------|--------|
| `app.py` | `_caddy_cert_days_color`: green ≥30d, red <30d (removed yellow); renewal script `RENEW_WINDOW_DAYS` 40→35 |
| `scripts/guarddog/tak-cert-watch.sh` | Alert threshold 40→25 days |
| `docs/GUARDDOG.md` | Clarified cert renewal chain and alert thresholds |

---

## Files Changed (Node-RED / Configurator)

| File | Change |
|------|--------|
| `nodered/build-flows.js` | `ARRAY_GUARD` + `_coerceArr()` injected into all CRUD mutators; `unwrapCtxVal` hardened for bare strings; `fn_tc_delete` gains backup snippet |
| `nodered/build-flows.js` | Multi-agency PulsePoint: `pp_configs[]`, `FN_PP_SAVE`, `FN_PP_LOAD`, `FN_PP_DELETE`, `makePulsepointTab()` |
| `nodered/configurator.html` | ArcGIS save: 15s timeout, console diagnostics, race-free collapse, `_fetchT()` on all fetches |
| `nodered/configurator.html` | DataSync field visibility toggle |
| `nodered/configurator.html` | PulsePoint + TC nav bar logos |
| `nodered/configurator.html` | `syncColorPick()` — suppress color input console warnings |
| `nodered/template-functions.json` | PP engine function node templates added |
