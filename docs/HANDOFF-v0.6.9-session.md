# Handoff — v0.6.9-alpha session (April 2026)

> **Purpose:** single-source brief for the infra-TAK agent (and next human session) on everything that landed on `dev` between the v0.6.8-alpha tag and the v0.6.9-alpha tag. Covers IPAWS/NWS KML network link for ATAK, real zone polygon geometry via NWS zones API, Node-RED settings.js auto-migration, and release process.

---

## 1. TL;DR — one-paragraph release summary

v0.6.9-alpha ships a full **IPAWS/NWS active alert KML network link** for ATAK. Node-RED polls `api.weather.gov/alerts/active` and builds KML with NAPSG CDN icons and severity-colored polygons. Zone-based alerts (Red Flag Warning, High Wind, Winter Storm, Small Craft Advisory, Gale Warning, etc.) now fetch actual NWS forecast zone boundary polygons from the zones API (`api.weather.gov/zones/...`) and cache them in Node-RED global context with a 5-day TTL — all ~490 active NWS alerts render with real geometry, zero dropped. The Configurator has a full IPAWS UI (FEMA logo, pill-box state selector, severity filters, Deploy/Deactivate buttons, ACTIVE badge in saved configs). Node-RED `settings.js` is now auto-migrated on update to ensure `httpStatic`, `functionGlobalContext.nodeHttps`, and `editorTheme` are always present.

---

## 2. Key decisions and context

### Why only Flood Warnings had polygon geometry before this version

The NWS `alerts/active` GeoJSON API only embeds polygon coordinates directly in the `geometry` field for alerts where the issuing forecaster explicitly drew a polygon (Flood Warnings, Tornado Warnings, Severe Thunderstorm Warnings — ~18% of all active alerts). All other alert types (Red Flag Warning, Wind Advisory, Winter Storm Warning, Small Craft Advisory, Gale Warning, etc.) use pre-defined NWS forecast zone codes in `affectedZones` with `null` geometry. Prior code did `if (rings.length === 0) return` which silently dropped 80%+ of alerts.

### Why GoTAK had zone polygons

GoTAK is a private company that applied to FEMA for access to the live IPAWS all-hazards feed. For zone polygons, GoTAK either bundled NWS zone shapefiles (published at weather.gov/gis) or used the same NWS zones API approach implemented here. The FEMA OpenFEMA IPAWS Archived Alerts API (`api.weather.gov/open/v1/IpawsArchivedAlerts`) is 24-hour delayed and has the same null geometry problem for zone-based alerts — it was investigated and ruled out.

### NWS zones API caching strategy

Zone boundaries change extremely rarely (annual county/zone redraws). The zone cache uses a 5-day TTL stored in Node-RED global context (`ipaws_zone_geo`). On first poll after deploy, Node-RED fetches all unique zone URLs from current active alerts in parallel (Promise.all using `require('https')` via `functionGlobalContext`). ~1000-1100 zone geometries are fetched in ~5 seconds. All subsequent polls hit the cache and are instant. Zone cache key: `ipaws_zone_geo` → `{ url: { geom: GeoJSON, ts: epoch } }`.

### Why `fetch()` doesn't work in Node-RED function nodes

`fetch()` is a Web API added to Node.js in v18. Node-RED's function node sandbox does not expose it regardless of Node.js version. `require()` is also blocked by default in the function node sandbox. The solution: expose Node.js `https` module via `functionGlobalContext` in `settings.js` as `nodeHttps`, then use `global.get('nodeHttps')` in the function node. This works in all Node-RED versions.

### FEMA IPAWS live feed — real-time non-weather alerts

Real-time IPAWS data for non-weather alerts (AMBER, civil emergency, 911 outages, EAS activations from local EMAs) requires applying to FEMA at `ipaws@fema.dhs.gov`. The OpenFEMA public API is 24-hour delayed. The NWS API covers ~95% of field-relevant alerts (weather, flood, fire, wind, winter, marine) in real time. FEMA application is the path for AMBER/civil if needed in the future.

### IPAWS activation design

The IPAWS flow tab is always present in `flows.json` and visible in the Node-RED editor. When `cfg.activated === false` (default for new installs), `FN_IPAWS_BUILD_REQ` returns null (no NWS API calls) and `FN_IPAWS_BUILD_KML` returns an "inactive" KML. The Configurator's "▶ Deploy IPAWS" button sets `activated: true` in global context. This was a deliberate UX decision — "dormant but visible" vs "completely absent" — chosen for simplicity.

### settings.js auto-migration

`_auto_nodered_settings()` in `app.py` runs as part of `_run_post_update()` on every version change. It now checks for three keys (`editorTheme`, `httpStatic`, `functionGlobalContext`) and injects any that are missing, writing the file once and restarting Node-RED once. Fresh installs get the full settings.js from `run_nodered_deploy()` which already has all three keys.

---

## 3. Files changed

| File | What changed |
|------|-------------|
| `nodered/build-flows.js` | `FN_IPAWS_BUILD_KML` complete rewrite: zone geometry cache (`ipaws_zone_geo`), async `https.get()` via `global.get('nodeHttps')` for uncached zones, `buildKml(zc)` inner function, sync path (cache hit) vs async path (Promise.all fetch). `STATE_CENTROIDS` table (last-resort fallback). `STATE_CENTROIDS` marine zone prefixes (AN, AM, GM, PZ, PH, PK, BZ, LS, LM, LH, LE, LO). `FN_IPAWS_BUILD_REQ` unchanged. |
| `app.py` | VERSION bumped to `0.6.9-alpha`. Two `settings_js` templates updated with `functionGlobalContext: { nodeHttps: require('https') }` and `httpStatic`. `_auto_nodered_settings()` extended to auto-inject `httpStatic` and `functionGlobalContext` on update (in addition to existing `editorTheme`). |
| `nodered/configurator.html` | IPAWS UI: FEMA/IPAWS logo in header and source button. State/territory pill-box (58 NWS codes). Severity checkboxes. Deploy/Deactivate buttons. ACTIVE badge in saved configs. Clickable IPAWS config card. Panel closes on save. |
| `docs/RELEASE-v0.6.9-alpha.md` | New release notes. |
| `docs/COMMANDS.md` | Release block updated to `v0.6.9-alpha` (4 references). |
| `docs/HANDOFF-v0.6.9-session.md` | This file. |

---

## 4. Operator upgrade steps

1. **Update Now** button handles everything — `_auto_nodered_settings()` patches `settings.js` and restarts Node-RED automatically
2. On first ATAK KML request after update, zone cache populates (~5–15 seconds, visible in Node-RED debug as `IPAWS: fetching N uncached zone geometries...`)
3. Open **Configurator → IPAWS Alerts → ▶ Deploy IPAWS** to activate (optional — installs without IPAWS stay unaffected)

**Existing installs that updated manually before v0.6.9-alpha (e.g. test servers):**
```bash
cat > ~/node-red/settings.js << 'EOF'
module.exports = {
  flowFile: 'flows.json',
  flowFilePretty: true,
  userDir: '/data',
  httpAdminRoot: '/',
  httpNodeRoot: '/',
  httpStatic: '/data/public',
  contextStorage: { default: { module: 'localfilesystem' } },
  functionGlobalContext: { nodeHttps: require('https') },
  editorTheme: {
    header: { title: 'infra-TAK Node-RED  —  <a href="/configurator" target="_blank" style="color:#2ec4b6;text-decoration:underline">Open Configurator</a>' }
  }
};
EOF
docker restart nodered
```

---

## 5. Node-RED debug output — what to expect

**First request after deploy:**
```
IPAWS: fetching 1063 uncached zone geometries...
IPAWS: zone fetch done — 1063/1063 geometries retrieved
IPAWS KML: 498 alerts → 92 inline-poly, 406 zone-poly, 0 state-pt, 0 skipped | zone cache: 1063 entries
```

**Subsequent requests (cache warm):**
```
IPAWS KML: 498 alerts → 92 inline-poly, 406 zone-poly, 0 state-pt, 0 skipped | zone cache: 1063 entries
```

---

## 6. Known issues / follow-up

- **FEMA IPAWS live feed** — non-weather alerts (AMBER, civil emergency) require FEMA access approval via `ipaws@fema.dhs.gov`. Not implemented; NWS feed covers weather-only IPAWS alerts in real time.
- **Zone cache size** — 1063 zone geometries in global context is significant memory. If Node-RED memory pressure becomes an issue, TTL could be reduced or cache could be stored on disk. Not a current problem.
- **IPAWS tab visible in Node-RED editor even when inactive** — by design. If "completely absent unless activated" behavior is needed, deploy.sh would need to conditionally inject/remove the IPAWS tab based on `ipaws_config.activated`. Deferred.
- **NWS API rate limit** — `api.weather.gov` recommends no more than one request per 30 seconds. IPAWS polls on every KML request (ATAK pulls every 5 minutes by default). Zone API fetches are batched as a single Promise.all on first load only. Rate limit is not a current concern but worth monitoring if polling interval is reduced.

---

## 7. IPAWS architecture summary

```
ATAK Network Link → GET /ipaws/alerts.kml (Caddy bypasses Authentik)
  → Node-RED: FN_IPAWS_BUILD_REQ (NWS API request params)
  → HTTP Request node → api.weather.gov/alerts/active
  → FN_IPAWS_BUILD_KML:
      for each alert:
        if inline polygon → use it (Flood/Tornado/SVR Tstorm)
        else → look up affectedZones[*] in ipaws_zone_geo cache
               if cache miss → fetch api.weather.gov/zones/{type}/{id} (async, parallel)
               if fetch fails → STATE_CENTROIDS fallback
      → build KML with NAPSG CDN icons + NWS severity colors
  → serve KML with Content-Type: application/vnd.google-earth.kml+xml

Zone cache: global context key 'ipaws_zone_geo'
  { "https://api.weather.gov/zones/fire/IAZ001": { geom: GeoJSON, ts: epoch }, ... }
  TTL: 5 days (432000000 ms)
```
