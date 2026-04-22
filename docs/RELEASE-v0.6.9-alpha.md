# Release v0.6.9-alpha

## What's new

### IPAWS / NWS Active Alerts — KML Network Link for ATAK

infra-TAK now includes a built-in FEMA IPAWS feed that serves active NWS alerts as a KML network link consumable by ATAK, Google Earth, and any KML-compatible GIS client.

**How it works:**
- Node-RED polls `api.weather.gov/alerts/active` on every ATAK request
- Returns KML with severity-colored polygons + NAPSG Public Alert icon markers
- Tap any alert in ATAK for full NWS text: areas, description, instructions, severity, expires

**Zone polygon geometry:**
- Alerts with inline polygon geometry (Flood Warnings, Tornado Warnings) render as filled polygons
- Zone-based alerts (Red Flag Warning, High Wind, Winter Storm, Small Craft Advisory, Gale Warning, etc.) fetch actual NWS forecast zone boundary polygons from the zones API (`api.weather.gov/zones/...`) and cache them in Node-RED global context with a 5-day TTL
- First request after deploy populates the cache (~5–15 seconds); all subsequent requests are instant
- Result: all ~490+ active NWS alerts rendered with real zone polygons — zero dropped, zero state-centroid dots

**Icons:** Uses [NAPSG Foundation Public Alert symbols](https://www.napsgfoundation.org/all-resources/symbology-library/) served from the NAPSG CDN (CC BY 4.0) — no local hosting required.

**Severity colors:** 🔴 Extreme · 🟠 Severe · 🟡 Moderate · 🔵 Minor

**Activation:**
- The IPAWS flow is deployed on all new installations but starts **inactive** — no NWS traffic, empty KML
- Go to **Configurator → IPAWS Alerts → ▶ Deploy IPAWS** to activate
- Configure severity filters and optional state/territory pill-box filter before deploying
- ATAK setup: Overlay Manager → **+** → **Add URL** → paste the KML URL → 5 min refresh

**Caddy / Authentik:** `/ipaws/alerts.kml` is bypassed from Authentik SSO so ATAK can fetch unauthenticated. The `/ipaws/config` write endpoint remains protected.

---

### Node-RED settings.js — automatic migration on update

Node-RED `settings.js` is now automatically kept current by the post-update hook (`_auto_nodered_settings`). On every version update the hook checks for and injects any missing keys:

- `httpStatic: '/data/public'` — serves static icon assets
- `functionGlobalContext.nodeHttps` — exposes the Node.js `https` module inside function nodes (required for IPAWS zone polygon fetching)
- `editorTheme` — Configurator link in the Node-RED editor header (existing)

No manual settings.js edits needed on existing installs — the update handles it and restarts Node-RED once if anything changed.

---

### Configurator — IPAWS UI improvements

- IPAWS Alerts source button added to Configurator with FEMA/IPAWS logo
- IPAWS logo in page header alongside other source logos
- State/territory filter replaced with pill-box multi-select (58 NWS codes including marine areas)
- Severity filter checkboxes (Extreme / Severe / Moderate / Minor)
- **▶ Deploy IPAWS** / **Deactivate** buttons replace generic Save
- **ACTIVE** badge in Saved Configurations list when IPAWS is activated
- Clicking the IPAWS saved config card opens the panel for editing
- Panel closes automatically on save

---

## Upgrade steps

Update Now button handles everything automatically. After the update:

1. Node-RED `settings.js` is patched and Node-RED restarted automatically
2. On first ATAK KML request, zone geometries are fetched and cached (one-time, ~5–15 s)
3. Open **Configurator → IPAWS Alerts** and click **▶ Deploy IPAWS** to activate (optional — existing installs without IPAWS stay unaffected)

---

## What upgraders get

| Scenario | What happens |
|---|---|
| Existing install, no IPAWS config | IPAWS tab deploys, starts **inactive** — nothing changes in ATAK |
| Existing install, IPAWS was previously activated | Stays active, zone polygon fetching now works correctly |
| New install | IPAWS tab present, starts **inactive** until Configurator activation |
| Any install | `settings.js` automatically updated — no manual steps |
