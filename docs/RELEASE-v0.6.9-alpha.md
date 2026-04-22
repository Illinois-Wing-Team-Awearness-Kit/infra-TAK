# Release v0.6.9-alpha

## What's new

### IPAWS / NWS Active Alerts — KML Network Link for ATAK

infra-TAK now includes a built-in FEMA IPAWS feed that serves active NWS alerts as a KML network link consumable by ATAK, Google Earth, and any KML-compatible GIS client.

**How it works:**
- Node-RED polls `api.weather.gov/alerts/active` on every ATAK request
- Returns KML with severity-colored polygons + NAPSG Public Alert icon markers
- Tap any alert in ATAK for full NWS text: areas, description, instructions, severity, expires

**Icons:** Uses [NAPSG Foundation Public Alert symbols](https://www.napsgfoundation.org/all-resources/symbology-library/) served from the NAPSG CDN (CC BY 4.0) — no local hosting required.

**Severity colors:** 🔴 Extreme · 🟠 Severe · 🟡 Moderate · 🔵 Minor

**Activation:**
- The IPAWS flow is deployed on all new installations but starts **inactive** — no NWS traffic, empty KML
- Go to **Configurator → IPAWS Alerts → ▶ Deploy IPAWS** to activate
- Configure severity filters and optional state filter (e.g. `CA,OR,WA`) before deploying
- ATAK setup: Overlay Manager → **+** → **Add URL** → paste the KML URL → 5 min refresh

**Caddy / Authentik:** `/ipaws/alerts.kml` and NAPSG CDN icon URLs are bypassed from Authentik SSO so ATAK can fetch them unauthenticated. The `/ipaws/config` write endpoint remains protected.

---

### Security — IPAWS KML endpoint public, config endpoint protected

Previous builds protected all Node-RED paths behind Authentik forward_auth. This blocked ATAK and Google Earth from fetching the KML feed without a browser session.

**Fix:** Caddy now bypasses Authentik for `/ipaws/alerts.kml` only. All other Node-RED paths (including `/ipaws/config`) remain behind SSO.

---

## Upgrade steps

```bash
cd ~/infra-TAK && git pull origin dev
```

Regenerate Caddyfile (adds the IPAWS public bypass):

```bash
python3 -c "
import sys; sys.path.insert(0,'.')
from app import generate_caddyfile, load_settings
generate_caddyfile(load_settings())
print('Caddyfile written')
" && systemctl reload caddy
```

Redeploy Node-RED flows (adds IPAWS tab):

```bash
bash nodered/deploy.sh --no-pull
```

Then open **Configurator → IPAWS Alerts** and click **▶ Deploy IPAWS** to activate.

---

## What upgraders get

| Scenario | What happens |
|---|---|
| Existing install, no IPAWS config | IPAWS tab deploys, starts **inactive** — nothing changes in ATAK |
| Existing install, IPAWS was previously set up | Stays **active** (no `activated: false` in existing config) |
| New install | IPAWS tab present, starts **inactive** until Configurator activation |
