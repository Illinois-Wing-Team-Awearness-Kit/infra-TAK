# Handoff — v0.6.7-alpha session (April 2026)

> **Purpose:** single-source brief for the infra-TAK agent (and next human session) on everything that landed on `dev` between the v0.6.6-alpha tag and the v0.6.7-alpha tag. Covers DataSync read-only missions, multi-flow shared missions, Node-RED deploy sync improvements, FedHub sudo fix, Postfix VPS fix, and TAK Server channels fix.

---

## 1. TL;DR — one-paragraph release summary

v0.6.7-alpha makes **DataSync read-only missions work end-to-end**: Node-RED's admin cert connection is automatically elevated to `MISSION_OWNER` five seconds after subscribing, so `MISSION_READONLY_SUBSCRIBER` missions receive data from Node-RED while field devices stay read-only. Multiple Node-RED flows (KML + ArcGIS) can now **share a single DataSync mission** without erasing each other by setting `strictMode: false` in the Configurator. The **FedHub cert generation and rotation** flows now detect missing passwordless sudo early and print the exact fix command. **Postfix** install no longer fails on VPS configs where `hostname -f` returns a bad value. **TAK Server channels** now work for manually-issued certs (Skydio drones, `makeCert.sh`) via `x509useGroupCacheRequiresExtKeyUsage="false"` in the generated CoreConfig `<auth>` block — apply on existing servers with **Resync LDAP to TAK Server**.

---

## 2. Key decisions and context

### DataSync: why `PUT /subscription` downgrades admin

TAK Server's `PUT /subscription?uid=admin` assigns the mission's `defaultRole` to the subscriber **regardless of who they are** — even admin. So if the mission is `MISSION_READONLY_SUBSCRIBER`, admin gets read-only after subscribing. This silently blocks all `PUT /contents` calls (returns 200 but UIDs don't appear).

**Josh Blomberg (TAK Server dev) confirmed:** "The admin cert should automatically grant you the mission owner role." The way to achieve this is to explicitly call `PUT /role?username=admin&role=MISSION_OWNER` after subscribe.

### Why inline https call instead of separate Node-RED nodes

The Configurator generates engine tabs using `ENGINE_TAB_TEMPLATE` — a base64-encoded blob embedded in `configurator.html`. When `build-flows.js` runs inside Docker during `deploy.sh`, it tries to regenerate this template but gets `EACCES` (can't write to `/tmp/configurator.html`). So new nodes added to `makeEngineTab()` never reach existing preserved tabs. The deploy.sh sync step only updates **function code** (`func`), not wiring or new nodes. Therefore the elevation was put **inside the existing `Build subscribe URL` function** using Node.js `https` module via the `libs` property.

### Why `libs` also needed syncing

Initially the `libs` property (which declares `https` and `fs` module imports for the function node) was not being synced alongside `func` by the deploy.sh sync step. Fixed by changing `template-functions.json` format from `{ key: funcString }` to `{ key: { func, libs } }` and updating the sync step to apply both.

### Multi-flow shared missions: strictMode mechanics

`strictMode: true` (default) → `cleanOrphans = true` → DELETE any mission UID not in this flow's current data (including UIDs from other flows).

`strictMode: false` → `cleanOrphans = false` → DELETE only UIDs starting with **this flow's own uidPrefix**.

KML flow defaults to `uidPrefix: kml-`, ArcGIS auto-generates prefix from layer name. With strict off, they never touch each other.

**Recommended pattern for FIRIS (fast KML) + USFS/CALFIRE (ArcGIS ETL):**
- KML engine: FIRIS only, `uidPrefix: kml-`, `strictMode: false`, target shared mission
- ArcGIS engine: USFS + CAL FIRE only (remove FIRIS from source filter), `strictMode: false`, same mission
- Result: FIRIS appears 15 min early from KML; USFS/CALFIRE land alongside later; neither erases the other

---

## 3. Files changed

| File | What changed |
|------|-------------|
| `nodered/build-flows.js` | `Build subscribe URL` function includes inline `https.request()` elevation to `MISSION_OWNER` 5s after subscribe. Uses `libs: [https, fs]`. `FN_SUB` (TFR) same change. `template-functions.json` now emits `{ func, libs }` objects. Added `fn_elevate` + `http_elevate` nodes to `makeEngineTab` and `makeTfrEngineTab` (for new tabs via Configurator). |
| `nodered/configurator.html` | `ENGINE_TAB_TEMPLATE` and `TFR_ENGINE_TAB_TEMPLATE` base64 blobs patched to include `fn_elevate` → `http_elevate` → `debug_sub` wiring (for new tabs created via Configurator Save). |
| `nodered/deploy.sh` | Sync step now applies `libs` alongside `func` when updating preserved function nodes. Handles both old string format and new `{ func, libs }` object format in `template-functions.json`. |
| `app.py` | VERSION bumped to `0.6.7-alpha`. FedHub cert generation + rotation: `sudo -n true` preflight check with actionable error. Postfix install: FQDN fallback chain + recovery path for `mydomain=0`. `x509useGroupCacheRequiresExtKeyUsage="false"` already in `<auth>` block (was added in a prior session but now documented). |
| `docs/GIS-TAK-DATASYNC-HANDOFF.md` | Corrected "must use MISSION_SUBSCRIBER" guidance — wrong. Documented real behavior: `PUT /subscription` always assigns defaultRole even to admin; elevation to MISSION_OWNER is the fix. Updated role table. Removed old test plan (solved). |
| `docs/HANDOFF-LDAP-AUTHENTIK.md` | Added TAK Server auth path diagram section. |
| `docs/RELEASE-v0.6.7-alpha.md` | New release notes. |
| `README.md` | Latest release line updated to v0.6.7-alpha. |

---

## 4. Operator upgrade steps

1. **Update Now** or `git pull && sudo systemctl restart takwerx-console`
2. **Node-RED:** `bash nodered/deploy.sh` — sync step auto-updates `Build subscribe URL` in all existing engine tabs. No tab deletion needed.
3. **Channels fix (existing servers with makeCert.sh certs):** TAK Server page → **Resync LDAP to TAK Server**
4. **Read-only missions:** create mission as `MISSION_READONLY_SUBSCRIBER` in TAK Portal. Node-RED handles the rest automatically on next cold start.
5. **Multi-flow shared missions:** Configurator → each flow → uncheck **"Strict mission ownership"** → Save

---

## 5. Known issues / follow-up

- **KML CA AIR INTEL `INTERNAL_SERVER_ERROR` on subscribe** — the KML flow tries to subscribe to the `CA AIR INTEL` mission but it's now `MISSION_READONLY_SUBSCRIBER` and KML flow isn't the owner. Separate from the ArcGIS fix. Needs the same elevation treatment in `makeKmlEngineTab` if KML flows use DataSync.
- **Cold start hash seeding** — on Node-RED restart, `Build subscribe URL` fires, elevation fires 5s later, but the reconcile's 30s PUT delay means data flows correctly. The only edge case: if someone manually deletes all UIDs from the mission AND the hash cache is stale, the reconcile won't re-PUT (sees "unchanged"). Fix: clear `_featureHashes` via `DELETE http://localhost:1880/context/flow/{tabId}/_featureHashes` then trigger a poll.
- **`ENGINE_TAB_TEMPLATE` EACCES in Docker** — `build-flows.js` cannot write back the updated template to `/tmp/configurator.html` inside the container. The inline elevation approach in `Build subscribe URL` works around this. Long-term: mount the file writable or generate template outside Docker.

---

## 6. Mattermost conversation context

Key TAK Server developer insights from Josh Blomberg during this session:
- `makeCert.sh` is NOT called during enrollment — only for manual cert creation
- Channels require Extended Key Usage extension added at enrollment time
- `x509useGroupCacheRequiresExtKeyUsage="false"` disables this requirement server-wide (correct for Skydio/drone certs)
- Admin cert auto-grants `MISSION_OWNER` — but only if explicitly set via the role API; `PUT /subscription` assigns `defaultRole` regardless
- `makeCert.sh` RC2-40-CBC issue is separate from channels (WinTAK multi-server edge case)
