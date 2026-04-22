# Release v0.6.8-alpha

## What changed

### Node-RED deploy — `Elevate to MISSION_OWNER` now syncs on every deploy

`deploy.sh` was not updating the `Elevate to MISSION_OWNER` function node when you ran a deploy — only `Build subscribe URL` and a few others were in the sync map. This meant elevation logic changes were silently ignored on existing engine tabs until you deleted and recreated them from the Configurator.

**Fix:** Added `Elevate to MISSION_OWNER` to `deploy.sh`'s `nameToKey` map with separate keys for ArcGIS tabs (`arcgis.fn_elevate`) and KML tabs (`kml.fn_elevate`). Added `_templateKey` to both node definitions in `build-flows.js`. `template-functions.json` now exports 16 keys (was 14).

**Effect:** After any future `bash deploy.sh`, the elevation function code is always up to date in running engine tabs without recreating flows.

---

### DataSync — subscribe HTTP 500 correctly handled, no retry loop

TAK Server returns HTTP 500 when you `PUT /subscription` for a user who is already subscribed to a read-only mission. Previous code cleared `_subscribed` cache on any 5xx response, causing Node-RED to retry the subscribe on every poll — which kept getting 500 — which kept clearing the cache — infinite loop.

**Fix:** Removed the cache-clear on 5xx. The 500 is now logged as `(already subscribed or read-only — normal)` and the elevation fires regardless. `_subscribed` stays set so subsequent polls skip the subscribe entirely.

**Effect:** Clean logs, one subscribe attempt per Node-RED restart, no polling spam.

---

### DataSync — clearer cold-start log when ArcGIS returns 0 features

When an ArcGIS feed returns 0 features, the reconcile was logging `cold start: seeded 0 hashes without re-streaming` — which looked like a bug. It's not: it just means there's no active data in the source right now.

**Fix:** Split into two messages:
- `cold start: seeded N hashes without re-streaming` — when there are features to seed
- `cold start: no ArcGIS features this poll, nothing to reconcile` — when the source is empty

---

## Upgrade steps

Standard update — click **Update Now** in the console or on your VPS:

```bash
cd ~/infra-TAK && git pull origin main && ./start.sh
```

Then redeploy Node-RED flows to pick up the deploy.sh fix:

```bash
cd ~/infra-TAK/nodered && bash deploy.sh && cd ..
```

No Configurator changes needed. Existing engine tabs will be updated automatically by the deploy.

---

## Background

These fixes came out of live testing after the v0.6.7-alpha release — running CA AIR INTEL (ArcGIS + KML sharing one read-only DataSync mission) and POWER-OUTAGES (ArcGIS, ~200+ active features, incremental PUT/DELETE every poll cycle). All three flows verified stable over multiple poll cycles with clean logs before tagging.
