# Handoff — v0.6.0 / v0.6.1-alpha session (April 16, 2026)

## Current release state

- **v0.6.1-alpha** is tagged and live on `main`. All boxes get "Update Available."
- v0.6.1 is a version-bump patch over v0.6.0 to ensure boxes that fetched the original v0.6.0 tag get the fix. No functional changes beyond the VERSION string.
- `dev` branch is up to date and matches what's on `main` for all shipped files.

## What shipped in v0.6.0-alpha

1. **Guard Dog Disk I/O Performance Monitor** — `tak-diskio-watch.sh`, `takdiskioguard.timer/.service`, API endpoints (`/api/guarddog/diskio-history`, `/api/guarddog/diskio-report`), dashboard card with sparkline chart, time range dropdown (24h–30d), CSV download, color-coded stats, local timezone labels, refresh button with "Updated" feedback.
2. **VPS swappiness tuning** — `vm.swappiness=10` set during Guard Dog deploy (persistent + immediate).
3. **Postfix debconf preseed** — `debconf-set-selections` before `apt-get install postfix` to fix `meter mydomain: bad parameter value: 0`.
4. **Node-RED ArcGIS DataSync & FAA TFR Configurator** — new feature, web-based configurator at `nodered.<fqdn>`, non-destructive deploys, cold-start guards, template sync, stable hashing, TFR ID fix.
5. **guarddog.js cache-control** — no-cache headers on `/guarddog.js` route (was missing, unlike firewall.js).
6. **Disk I/O API error handling** — visible error messages instead of silent `.catch(function(){})`.

## Bugs found and fixed during testing

| Bug | Root cause | Fix |
|-----|-----------|-----|
| Disk I/O card shows nothing, no error | `guarddog.js` served without cache-control headers; browser cached old JS | Added `Cache-Control: no-cache, no-store, must-revalidate` to `/guarddog.js` route |
| Disk I/O card shows "API error (500)" | `from datetime import datetime` missing `timedelta` | Changed to `from datetime import datetime, timedelta` on line 64 |
| `takdiskioguard.timer` not enabled after deploy | Timer was in the unit definitions but missing from the hardcoded timer enable list | Added to timer list (line ~4906) |
| Refresh button "does nothing" | No visual feedback on fetch completion | Added "Refreshing…" → "Updated" (green) state transitions |
| Errors silently swallowed | `.catch(function(){})` in fetch | Added response status/content-type checks, visible error messages on card, console.error logging |

## VPS test boxes — current state

| Box | IP | Version | Disk I/O | Swappiness | Notes |
|-----|-----|---------|----------|------------|-------|
| **tak-10** | 172.93.50.47 | v0.6.1-alpha | 193–276 MB/s (healthy) | 10 | Previously had bad I/O, SSD Nodes migrated it. Timer running, card working. |
| **responder** | 190.102.110.224 | v0.6.1-alpha | 4.8–103 MB/s (degraded) | 10 | Noisy neighbor. SSD Nodes migration requested and confirmed. Timer running, card working. |
| **ssdnodes-66871ef1e08d7** | 63.250.55.132 | v0.6.1-alpha | 30–125 MB/s (poor) | 10 | Included in SSD Nodes migration request. Timer running. |

**SSD Nodes migration:** Email sent requesting migration for both 190.102.110.224 and 63.250.55.132. They confirmed they're moving the machines. Performance metrics pending after migration completes.

## Open issue — Amos (TN) Authentik outage

- **Box:** Azure VPS, `infratak.tntak.net`
- **Timeline:** Health endpoint (`/health`) went down ~10 hours before he noticed. He updated to v0.6.0-alpha via the console (likely backdoor since Authentik was down). Update didn't fix it. Full reboot fixed everything.
- **What was down:** Guard Dog health agent (port 8080) and Authentik web page. TAK Server and TAK Portal were fine.
- **What was NOT the cause:** The update didn't cause it — outage predated update by ~10 hours.
- **Root cause unknown.** Possibilities: Authentik PG connection leak, OOM kill, Azure silent host maintenance. The `/health` route in Caddy bypasses Authentik forward auth (goes directly to 8080), so the 502 means the health agent process itself died, not an Authentik auth issue.
- **Action:** DM Amos to ask:
  - Did Azure show planned maintenance around that time?
  - Was the console (backdoor IP:5001) reachable when health was down?
  - Run `journalctl --since "10 hours ago" | grep -i "oom\|killed\|out of memory" | head -20`
  - Run `cat /proc/sys/vm/swappiness && free -h && dd if=/dev/zero of=/tmp/.iotest bs=1M count=256 oflag=dsync 2>&1 | tail -1 && rm -f /tmp/.iotest` for baseline
- **Future consideration:** Deeper Authentik monitoring (actual login page probe, PG connection count, OOM-kill detection). Not in this release.

## Files changed (key ones)

- `app.py` — VERSION bump, `timedelta` import, disk I/O API endpoints + error handling, `guarddog.js` cache headers, Postfix debconf, swappiness tuning, Guard Dog timer list, disk I/O card template
- `static/guarddog.js` — `gdRefreshDiskIO()`, `gdDrawDiskIOChart()`, `gdDownloadDiskIOReport()`, error visibility, refresh feedback, local timezone
- `scripts/guarddog/tak-diskio-watch.sh` — new disk I/O benchmark + alert script
- `nodered/` — all Node-RED ArcGIS DataSync files (build-flows.js, configurator.html, deploy.sh, template-functions.json, flows.json, changelog)
- `README.md` — v0.6.1 latest release, v0.6.0 changelog entry with Node-RED intro
- `docs/RELEASE-v0.6.0-alpha.md` — full release notes
- `docs/RELEASE-v0.6.1-alpha.md` — patch note pointing to v0.6.0
- `docs/COMMANDS.md` — updated selective merge block for v0.6.0 (includes `nodered/`, updated tag/version refs)

## Reconciliation & notification churn — how it works (v0.6.2-dev)

### The problem
Every poll cycle, Node-RED was deleting and re-adding hundreds of items to TAK missions, flooding ATAK with "X changes" notifications. Three root causes:

1. **OBJECTID rotation** — ArcGIS feature services (especially power outage feeds) republish data periodically, resetting all OBJECTIDs. Since UIDs were based on OBJECTID, every item looked "new" and the old ones got deleted.
2. **Duplicate engine tabs** — `deploy.sh` preserved dynamic engine tabs across deploys, but if a tab was duplicated (same label, different ID), both survived. Two tabs polling the same feed = double notifications.
3. **Deploy burst-polling** — The poll throttle (`_lastPoll`) used flow context, which gets wiped on every deploy. All inject timers would fire 2-3 times simultaneously after each deploy.
4. **Unstable geometry hashing** — Full `JSON.stringify(geometry)` included `spatialReference` metadata and raw floating-point coordinates. Minor precision drift between ArcGIS API calls changed every hash, causing mass re-streams.

### The fixes

| Fix | File | What it does |
|-----|------|-------------|
| Stable ID fields | Configurator config | Use `IncidentId`, `GlobalID`, or other stable field instead of `OBJECTID`. OBJECTID rotates on republish; stable IDs don't. |
| Tab deduplication | `deploy.sh` | On deploy, if two preserved tabs share the same label, only the first is kept. Extras and their child nodes are dropped. Logged as `Dedup: removed extra tab "..."`. |
| Label-aware tab creation | `configurator.html` | `ensureEngineTab()` now checks all existing tab labels (not just the expected ID) before creating a new engine tab. |
| Global poll throttle | `build-flows.js` | `_lastPoll` moved from flow context to global context, keyed by feed name (`_lastPoll_POWER-OUTAGES`). Survives deploys, prevents burst-polling. |
| Geometry hash normalization | `build-flows.js` | Points: rounded to 6 decimal places, stripped of spatialReference. Polygons/polylines: vertex count + rounded centroid instead of full coordinate dump. Only genuinely different shapes change the hash. |
| `_subscribed` cache clear on deploy | `build-flows.js` | The `Build subscribe URL` node's `initialize` block clears the global `_subscribed` map on every deploy/restart, ensuring fresh mission subscriptions without needing to wipe `global.json`. |

### How reconciliation works

Each poll cycle per feed:

1. **Poll ArcGIS** — fetch features from the configured service/layers.
2. **Build CoTs** — generate CoT JSON for each feature, compute a stable hash from normalized geometry + ID field + label/remarks fields.
3. **GET mission** — fetch current mission contents from TAK server (list of UIDs).
4. **Compare**:
   - **Hash unchanged + UID in mission** → skip (no notification)
   - **Hash changed + UID in mission** → re-stream via TCP (updates CoT on TAK server, ATAK gets notification)
   - **UID not in mission** → stream via TCP + PUT UID to mission (ATAK gets "added" notification)
   - **UID in mission but not in ArcGIS** → DELETE from mission (ATAK gets "removed" notification)
5. **Cold start** (first poll after restart, empty hash cache): seed hashes for items already in the mission without re-streaming. Only truly new items get streamed/PUT.

### Expected ATAK notification behavior

- **Steady state**: 0 notifications per cycle if data hasn't changed. Small numbers (single digits) for legitimate adds/removes.
- **After deploy**: one transition cycle where all hashes are re-computed. Items already in the mission are seeded (0 notifications). Only genuinely new/removed items generate notifications.
- **Dynamic data** (NOAA storm reports): legitimate changes as reports age in/out of the 24-hour window. Expect ~5-20 notifications per cycle depending on storm activity.

### Operator notes

- **Choosing an ID field**: Always prefer `GlobalID` or a domain-specific stable ID (like `IncidentId` for power outages) over `OBJECTID`. If only OBJECTID is available (e.g., NOAA), it's fine as long as the service doesn't republish/reset OBJECTIDs.
- **After deploy**: expect one "warm-up" cycle. Clear ATAK badges after that cycle; subsequent cycles should be quiet.
- **Never wipe `global.json`** to fix issues. The `_subscribed` cache auto-clears on deploy. Wiping `global.json` destroys all Configurator configs.

## Git state

- On `dev` branch, up to date with `origin/dev`
- Tags: `v0.6.0-alpha` and `v0.6.1-alpha` both on `main`
- No uncommitted changes
