# v0.7.1-alpha Release Notes

---

## ⚠️ ACTION REQUIRED FOR EXISTING DEPLOYMENTS

After pulling this update, go to the **TAK Server page → Resync LDAP to TAK Server**.

This fixes password changes taking up to 24 hours to take effect on ATAK/iTAK devices. The fix is applied automatically every time Resync runs — one click and your deployment is current.

---

## What's in This Release

1. [Critical: Node-RED configs wiped on Update Now — fixed](#1-critical-node-red-configs-wiped-on-update-now--fixed)
2. [Tablet Command AVL → ATAK CoT Streaming](#2-tablet-command-avl--atak-cot-streaming)
3. [LDAP: Password changes now take effect immediately](#3-ldap-password-changes-now-take-effect-immediately)
4. [Node-RED Configurator: ArcGIS save fix, multi-agency PulsePoint, UI polish](#4-node-red-configurator-arcgis-save-fix-multi-agency-pulsepoint-ui-polish)
5. [External / Managed Database (AWS RDS, Azure, Cloud SQL)](#5-external--managed-database-aws-rds-azure-cloud-sql)
6. [Certificate auto-renewal display fixes](#6-certificate-auto-renewal-display-fixes)

---

## 1. Critical: Node-RED configs wiped on Update Now — fixed

Older installs stored Node-RED global context **in memory only** (Node-RED's default `contextStorage`). Any container restart wiped every Configurator config — ArcGIS layers, KML feeds, TAK settings, IPAWS — with no recovery.

During **Update Now**, `deploy.sh` tried to back up the context file before stopping the container. The file didn't exist (memory storage never writes it), the backup silently failed, the container stopped, and on restart all configs appeared erased.

**Fix (`app.py` — `_auto_nodered_settings()`):**
- Detects missing `contextStorage` in `settings.js`
- Before patching, exports the **live in-memory context** via the Node-RED REST API (`GET /context/global`) while the container is still running
- Writes it to `/data/context/global/global.json` inside the container
- Adds `contextStorage: localfilesystem` to `settings.js` and restarts
- Node-RED loads from the file on restart — **no config loss**
- All subsequent updates are safe because context is now on disk

**Fix (`nodered/deploy.sh`):**
- Context backup uses **REST API first** (`docker exec nodered curl http://localhost:1880/context/global`) before stopping the container
- Falls back to `docker cp` if the API is unavailable
- Restore step creates the `/data/context/global/` directory before copying

**Migration:** On the first Update Now after this release, affected installs automatically export context to disk and patch `settings.js` — no operator action required.

| File | Change |
|------|--------|
| `app.py` | `_auto_nodered_settings()`: detect missing contextStorage, export live context via API before patching |
| `nodered/deploy.sh` | REST API backup first; restore creates directory; file fallback retained |

---

## 2. Tablet Command AVL → ATAK CoT Streaming

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

**Known Units / Remapping Table:** Each agency config has a per-agency table to override radio names with custom callsigns (e.g. `CA342` → `Corona Engine 42`) and override CoT types per unit. Upload/download as CSV (`radioName,callsign,cotType`).

**Multi-agency:** Each agency gets its own named config card, its own Node-RED tab, and its own remapping table.

| File | Change |
|------|--------|
| `nodered/build-flows.js` | `makeTCEngineTab()`, TC config persistence nodes, TC template injection |
| `nodered/configurator.html` | TC source button, TC panel, TC JavaScript helpers |

---

## 3. LDAP: Password changes now take effect immediately

**Problem (pre-v0.7.1):** When a user or admin reset a password in TAK Portal (or directly in Authentik), the new password could take up to 24 hours to work on iTAK/ATAK. The old password continued to authenticate for the remainder of the cached session.

**Root cause:** Authentik's LDAP outpost runs with `bind_mode: cached`. The `ldap-authentication-login` User Login stage had `session_duration: seconds=0`, which Authentik interprets as a full browser session — effectively ~24 hours for LDAP binds. Password changes do not invalidate existing cached sessions.

**Fix:** Set `session_duration: seconds=120` on the `ldap-authentication-login` stage. Cached bind sessions now expire in 2 minutes.

**Self-healing:** The fix is enforced every time **Resync LDAP to TAK Server** runs — it patches the live stage regardless of its current value.

**Operator action (existing deployments):** TAK Server page → **Resync LDAP to TAK Server**. Verify with:

```bash
TOKEN=$(grep AUTHENTIK_BOOTSTRAP_TOKEN ~/authentik/.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" \
  'http://127.0.0.1:9090/api/v3/stages/user_login/?search=ldap' | \
  python3 -c "import sys,json; r=json.loads(sys.stdin.read())['results']; [print(f'name={s[\"name\"]} session_duration={s.get(\"session_duration\")}') for s in r]"
```

Expected: `name=ldap-authentication-login session_duration=seconds=120`

| File | Change |
|------|--------|
| `app.py` | Blueprint YAML `session_duration: seconds=0 → seconds=120`; `_create_ldap_stage()` updated; unconditional PATCH on every Resync |

---

## 4. Node-RED Configurator: ArcGIS save fix, multi-agency PulsePoint, UI polish

### Critical Bug Fix: ArcGIS Save Hanging Indefinitely

Clicking **Save & Generate Config JSON** caused the UI to show "Saving…" and hang forever.

**Root cause (Node-RED logs):**
```
TypeError: configs.findIndex is not a function
```

After a deploy/restore cycle, `global.get('arcgis_configs')` returned a stringified array (`"[]"`) or a REST API envelope (`{msg: "[]", format: "..."}`) instead of a real array. Calling `.findIndex()` on a string threw `TypeError`, the function node never returned, and the HTTP request hung until timeout.

**Fix (`nodered/build-flows.js`):**
- New `_coerceArr(v)` helper handles all three corrupt formats: REST API envelope, stringified JSON, and anything-not-an-array → `[]`
- Injected into all CRUD mutators: `fn_save`, `fn_tc_save`, `fn_tc_delete`, `FN_PP_SAVE`, `FN_PP_LOAD`, `FN_PP_DELETE`
- `unwrapCtxVal` in `/config/deploy-restore` now handles bare strings — prevents corrupt writes from happening in the first place

### ArcGIS Save UX Hardening

- **15s AbortController timeout** on save — shows a clear timeout error instead of hanging
- **Console diagnostics** at every step: POST, response, FAILED
- **Race-free collapse**: reload configs → collapse form → redeploy Node-RED in background (previously the redeploy mid-sequence caused a 502 that blocked the UI)
- **All Node-RED API calls** now have 10s timeouts via `_fetchT()` helper; IPAWS/PP/TAK-settings fetches run in parallel

### Multi-Agency PulsePoint

PulsePoint refactored from a single global config to a **multi-agency model** matching Tablet Command:
- Each agency has its own config card, Node-RED engine tab, CoT TCP port, and PulsePoint API credentials
- Configs stored as `pp_configs[]` array (was a flat `pulsepoint_config` object)
- Existing single config automatically migrated to first entry in `pp_configs[]` on first load

### ArcGIS DataSync Form Field Visibility

When **Data Sync Mission** is checked: stream port and saved-list note fields are hidden (not relevant for DataSync). When unchecked: shown. `toggleDataSync()` enforces this on checkbox change, form reset, and config load.

### UI: PulsePoint and Tablet Command in Top Nav

PulsePoint and Tablet Command logos and labels added to the Configurator top navigation bar for one-click access.

### Color Input Warning Spam Fixed

Typing a hex color value was generating browser console warnings on every keystroke (`"#FF0" does not conform to required format`). New `syncColorPick()` helper only writes to the color picker when the value is a complete valid `#rrggbb`. Applied to ArcGIS, TFR, and KML color inputs.

| File | Change |
|------|--------|
| `nodered/build-flows.js` | `_coerceArr()` injected into all CRUD mutators; `unwrapCtxVal` hardened; multi-agency PulsePoint |
| `nodered/configurator.html` | 15s save timeout, diagnostics, race-free collapse, `_fetchT()`, DataSync visibility toggle, PulsePoint + TC nav logos, `syncColorPick()` |
| `nodered/template-functions.json` | PP engine function node templates |

---

## 5. External / Managed Database (AWS RDS, Azure, Cloud SQL)

TAK Server can now be deployed against an externally-hosted PostgreSQL instance — AWS RDS, Azure Database for PostgreSQL, Google Cloud SQL, or any network-reachable PostgreSQL 15 server. This is a third deployment option alongside Single Server and Split Two-Server.

**How it works:**
- infra-TAK installs the full `takserver_X.X_all.deb` on the TAK Server VM
- TAK Server's SchemaManager creates all tables in the remote `cot` database on first boot
- infra-TAK patches `CoreConfig.xml` with your JDBC endpoint and credentials
- Guard Dog monitors the endpoint via TCP and `pg_isready` — no SSH to a managed DB; alert emails include cloud console guidance instead of SSH restart commands

**UI (TAK Server → Deploy TAK Server):**
- New **External / Managed DB** radio button
- Fields: DB Endpoint, Port, Database Name, Username, Password
- **Test Connection** button — runs TCP, `pg_isready`, and psql auth checks before deploy

See `docs/EXTERNAL-DB-SETUP.md` for pre-flight SQL, network/firewall steps (RDS, Azure, Cloud SQL), and PostgreSQL parameter tuning.

| File | Change |
|------|--------|
| `app.py` | External DB deployment mode, JDBC patch, `POST /api/takserver/external-db/test-connection`, Guard Dog sync |
| `static/takserver.js` | Third radio button, external-db-config-panel, `saveExternalDbConfig`, `testExternalDbConnection` |
| `scripts/guarddog/tak-remotedb-watch.sh` | `EXTERNAL_DB_PLACEHOLDER`; cloud-aware alert body |
| `docs/EXTERNAL-DB-SETUP.md` | New — full setup guide |

---

## 6. Certificate auto-renewal display fixes

The Caddy / TAK Server cert card was turning orange at 40 days remaining — before any renewal had run — creating a false alarm.

**How renewal works:**
1. Caddy auto-renews the Let's Encrypt cert at ~30 days remaining
2. The `takserver-cert-renewal` systemd timer (monthly) runs `/opt/tak/renew-letsencrypt.sh` at ≤35 days — reloads Caddy, waits 15s, then rebuilds the TAK JKS from the fresh cert and restarts TAK Server
3. The Caddy cert and the JKS are **the same cert** — same expiry, rebuilt together

**Old behavior:** yellow/orange at ≤40 days, red at ≤14 days (fired before renewal ran)  
**New behavior:** green at ≥30 days, **red at <30 days** (renewal ran and failed — action required). No yellow/orange.

Guard Dog alert threshold also corrected: 40 → 25 days (fires only after renewal has had its chance).

| File | Change |
|------|--------|
| `app.py` | `_caddy_cert_days_color`: green ≥30d, red <30d; renewal `RENEW_WINDOW_DAYS` 40→35 |
| `scripts/guarddog/tak-cert-watch.sh` | Alert threshold 40→25 days |
| `docs/GUARDDOG.md` | Cert renewal chain and thresholds clarified |

---

## Files Changed

| File | Change |
|------|--------|
| `app.py` | VERSION → 0.7.1-alpha; all fixes above |
| `static/takserver.js` | External DB UI |
| `nodered/build-flows.js` | TC engine tab; `_coerceArr()`; multi-agency PulsePoint |
| `nodered/configurator.html` | TC panel; save hardening; PulsePoint multi-agency; nav logos; color fix; DataSync visibility |
| `nodered/deploy.sh` | REST API context backup; directory creation on restore |
| `nodered/template-functions.json` | PP engine templates |
| `scripts/guarddog/tak-cert-watch.sh` | Alert threshold fix |
| `scripts/guarddog/tak-remotedb-watch.sh` | External DB support |
| `docs/EXTERNAL-DB-SETUP.md` | New |
| `docs/GUARDDOG.md` | Cert renewal clarification |
| `README.md` | Release highlights updated |
| `docs/COMMANDS.md` | Selective merge updated for v0.7.1-alpha |
| `docs/RELEASE-v0.7.1-alpha.md` | This file |
