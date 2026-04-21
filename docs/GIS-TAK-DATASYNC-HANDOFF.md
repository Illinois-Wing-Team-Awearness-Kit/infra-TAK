# Handoff: GIS → TAK DataSync (Node-RED) — infra-TAK module

## Background

The goal is a **reusable path from ArcGIS Feature Services into TAK Server**, using **Node-RED** and **DataSync** as the way to **publish and remove** map content (not just stream CoT over TCP). A prior attempt (Nov 2024) used PGE outage polygons: one flow to **GET** data and push CoT into a mission, and a second **cleanup** flow to list mission UIDs and **DELETE** to avoid duplicates. That approach never behaved reliably: **ongoing outages** tended to get **re-added each poll**, producing **stacked duplicate polygons** on the map.

As of **v0.5.8-alpha**, `nodered/flows.json` includes both an **ArcGIS Configurator** tab (`GET /configurator`) and an **ArcGIS → TAK Engine** tab (disabled by default) implementing the single reconciliation loop described below. The older PGE work was never committed. See *Maintainer log* for current status.

## Problem statement

1. **DataSync** is the practical API surface for **persisting** CoT in a mission and **removing** it later.
2. **Polling** without a clear **reconciliation model** (diff "what the feed says now" vs "what's already in the mission") causes **duplicates** or **racey** delete-then-re-add behavior.
3. **ArcGIS Feature Services** vary by layer: field names, geometry types, date fields, etc. Manually finding the **right index in the array** / the right attribute in each feature is what was hardest in Node-RED function nodes.

## Product direction (infra-TAK module — implemented)

A **"feed configurator"** (web UI served from Node-RED) that:

1. Accepts an **ArcGIS Feature Service** (or layer) URL.
2. Calls the **REST API** to expose **layer metadata** (fields, types) and **sample features** so the user isn't guessing array indices.
3. Lets the user define:
   - **Which field** is the stable **ID** (for mission UID / dedup).
   - **Which field** is **time** (for "only show data < 72 hours" and expiry).
   - **Geometry**: polygons vs points.
   - **CoT styling**: stroke/fill color (ARGB), fill opacity, line thickness, line style (solid/dashed/dotted/outlined), closed polygon toggle, label toggle + label field + center-in-shape option.
   - **Source filter**: checkbox selection from distinct ArcGIS values + free-form manual entry, merged into a combined `WHERE` clause.
   - **Remarks template**: clickable field picker to build the remarks string from feature attributes.
   - **TTL (hours)**: dual role — filters ArcGIS query by time window AND sets CoT `stale` time.
   - **Mission name**: target TAK Server DataSync mission for the engine.
4. Emits **runtime config** (JSON stored in Node-RED flow context with `localfilesystem` persistence) that the **engine flow** reads — configs survive Node-RED container restarts.
5. Supports **multiple named configurations** — save, load, and manage from a card-based UI.

## Recommended runtime behavior (avoid duplicate stacking)

**Single reconciliation loop** (conceptually), not "delete everything" vs "add everything" as two unrelated timers unless carefully sequenced:

1. **Fetch current features** from ArcGIS (with `where`/time filter as needed, e.g. last 72h).
2. **Fetch current mission contents** (or DataSync mission API) and build a **set of UIDs** already present.
3. **Diff**:
   - **In ArcGIS, not in mission** → **PUT/add** CoT for that UID.
   - **In mission, not in ArcGIS** (or older than TTL) → **DELETE** that UID.
   - **In both** → **no-op** (or optional **update** only if geometry/attributes changed — compare hash or `edit` timestamp if available).

Stable **UID** must be **deterministic** (e.g. `prefix + feature id` or hash of id + layer id), identical every poll.

**Current implementation** uses `arcgis-{featureID}` as the UID scheme, where `featureID` is the value of the user-selected ID field from the ArcGIS layer.

## References / packages already in mind

| Item | Role |
|------|------|
| [node-red-contrib-tak](https://github.com/snstac/node-red-contrib-tak) | CoT encode/decode, TAK protocol, TCP/mesh patterns. Engine outputs CoT JSON in the `_attributes` format this node expects. |
| [node-red-contrib-tfr2cot](https://flows.nodered.org/node/node-red-contrib-tfr2cot) | Reference for **DataSync** patterns: `dataSyncSubscription`, pairing **HTTP request** (method from msg) with Mission API. Greg Albrecht's blog post on RIIS covers the wiring in detail. |
| Paul's DataSync flow (user's prior work) | Pattern for DataSync PUT/DELETE with Mission API — used as the basis for the engine's reconciliation approach. Key insight: `PUT /Marti/api/missions/{name}/contents?creatorUid={uid}` with `{uids: [uid]}` payload to add, `DELETE /Marti/api/missions/{name}/contents?uid={uid}&creatorUid={uid}` to remove. |
| TAK Mission / DataSync API | Add/remove persisted mission content. Ports: **8443** for Mission API (HTTPS with client cert), **8089** for streaming TCP CoT. Tested on TAK Server **5.7**. Exact paths depend on TAK Server version and `CoreConfig.xml`. |

**Note:** `tfr2cot` docs explicitly call out that **saving to DataSync** does not auto-remove stale items — aligns with building **explicit DELETE** or reconciliation in infra-TAK.

## Operational / TLS notes

- **HTTP request** to Mission API over TLS needs a **TLS config** node in Node-RED with the **client cert + key + CA** matching what TAK Server trusts. The engine includes a placeholder `tls-config` node (`tls_tak`) that the user must configure in the Node-RED editor with their certs.
- TAK Portal's "Create Integration" flow auto-generates client certs tied to a group — use those for Node-RED's TLS config.
- Errors like **`ERR_SSL_SSLV3_ALERT_CERTIFICATE_UNKNOWN`** usually mean **wrong CA**, **self-signed** cert not trusted in Node-RED, or **hostname mismatch** — fix in Node-RED TLS config, not by disabling verification in production.
- **v0.5.8-alpha fix**: `cert-metadata.sh` ownership was broken after TAK Server upgrades (owned by `root:root` instead of `tak:tak`), preventing TAK Portal from generating certs for Node-RED integrations. Now auto-healed on console startup.

## Hard-won DataSync lessons (2026-04-10 live debugging)

These are findings from end-to-end testing against TAK Server 5.5-RELEASE-53 with the Node-RED engine. Each cost hours to diagnose.

### 1. Portal cert zip has hash mismatch with server files

TAK Portal's "Download Integration Certs" ZIP sometimes contains stale or mismatched `.pem`/`.key` files — `sha256sum` of the ZIP contents did not match the same-named files under `/opt/tak/certs/files/` on the TAK server. Reported to Justin (he couldn't reproduce on his end). **Workaround**: pull cert files directly from the server via SSH or the infra-TAK file browser (`/opt/tak/certs/files/<name>.pem`, `.key`). Those are the source of truth — Portal is a pass-through.

### 2. `creatorUid` must match the cert identity exactly

First attempt used `creatorUid=nodered-arcgis-engine` in the configurator but the TLS cert was for `nodered-global-airdata` → **403 Forbidden** on every PUT. The `creatorUid` in the URL **must match the CN (common name) of the client cert** the TLS config is using. Fix: set Creator UID in the configurator to the exact cert name.

### 3. Mission `defaultRole` and admin write access — RESOLVED 2026-04-20

**Original understanding (incorrect):** `MISSION_READONLY_SUBSCRIBER` as `defaultRole` was believed to block all API writes for everyone including admin, requiring `MISSION_SUBSCRIBER` to allow PUT/DELETE.

**Correct behavior:** TAK Server CAN support read-only missions where admin writes and field users only read. The mechanism:

1. Create the mission with `defaultRole: MISSION_READONLY_SUBSCRIBER` in TAK Portal — field devices that subscribe get read-only automatically.
2. Admin must be explicitly elevated to `MISSION_OWNER` after subscribing. TAK Server's `PUT /subscription` assigns the mission's `defaultRole` to the subscriber, **even for admin**. This silently downgrades admin to read-only.
3. The fix: immediately after `PUT /subscription?uid=admin`, call `PUT /Marti/api/missions/{name}/role?username=admin&clientUid=admin&role=MISSION_OWNER`. This overrides the defaultRole assignment.

**Implementation in infra-TAK (v0.6.7+):** The `Build subscribe URL` function node in all DataSync engine tabs (ArcGIS + TFR) performs the role elevation automatically, 5 seconds after subscribe, using a fire-and-forget `https.request()` with the admin cert at `/certs/admin.pem`. The warn log confirms it:
```
"Subscribing to READ ONLY TEST as admin"          ← subscribe fires
"Elevated admin to MISSION_OWNER on READ ONLY TEST (HTTP 200)"  ← 5s later
"CA AIR INTEL PUT -> 13 UIDs -> ..."              ← 30s later, writes succeed
```

**Verified 2026-04-20:** Mission created as `MISSION_READONLY_SUBSCRIBER`, admin cert (Node-RED) writes 13 UIDs successfully, ATAK field devices see the data read-only. No `defaultRole` change needed.

### 4. CoT must stream via TCP BEFORE the DataSync PUT

`PUT /Marti/api/missions/{name}/contents?creatorUid={user}` with body `{"uids":["<uid>"]}` returned **500 INTERNAL_SERVER_ERROR** because TAK Server didn't know about that UID yet. The server needs to **receive the CoT event first** (via TCP streaming on port 8089 with `<Marti><dest mission="..."/></Marti>` in the detail), then the PUT registers that already-known UID into the mission's persistent contents.

**Sequence**: Stream CoT → **5-second delay** (`eng_delay_put`) → PUT to `/contents`. The delay gives TAK time to process the inbound CoT before the DataSync registration.

### 5. Mission API returns empty body — use `ret: 'txt'`

TAK Server often returns **empty body** (Content-Length: 0) on successful PUT/DELETE to mission contents. The `eng_http_action` node was originally set to `ret: 'obj'` (parse as JSON), which caused **"JSON parse error"** on every successful call. **Fix**: set `ret: 'txt'` so empty responses don't blow up.

### 6. ArcGIS time filter: use DATE format, not epoch

The engine initially built time filters like `poly_DateCurrent > 1730000000000` (epoch ms). Many ArcGIS Feature Services don't accept raw epoch in WHERE clauses on date fields → **0 features returned**. **Fix**: use ArcGIS SQL date format: `poly_DateCurrent >= DATE '2026-03-11'` (UTC calendar date computed from TTL).

### 7. Node-RED flow context is per-tab

Configurator and engine were originally on **separate tabs** in Node-RED. `flow.get('arcgis_configs')` on the engine tab returned `[]` because the configurator saved to **its** tab's context. **Fix**: merged both onto a single tab (`ArcGIS → TAK`) so `flow` context is shared. Commit `be4287a`.

### 8. `docker cp flows.json` wipes TLS config

Every `docker cp nodered/flows.json nodered:/data/flows.json` replaces the flow file — including the `tls_tak` node which ships with **empty** cert/key fields (secrets aren't committed). After each deploy you must **re-upload certs** in the Node-RED editor or use **"local file paths"** mode pointing to a mounted volume that persists across deploys.

### 9. `node-red-contrib-tak` encode node does not reliably deliver CoT via TCP

The `eng_tak` (CoT encode) → `eng_tcp_out` path showed "connected" and no errors, but TAK Server never stored the CoT. After hours of debugging, replaced with a **custom JSON-to-XML function node** (`eng_cot_to_xml`) that builds the XML string manually and sends it as a `Buffer` directly to `eng_tcp_out`. This approach works reliably.

### 10. `StrictUidMissionMemebershipFilter` blocks CoT with `<Marti>` tags

CoT events containing `<Marti><dest mission="..."/></Marti>` trigger TAK Server's `StrictUidMissionMemebershipFilter`. If the TCP connection's sender isn't identified as a mission member, the event is silently dropped with: `Illegal attempt to send mission event outside of a mission context`. **Fix**: removed the `<Marti>` tag from streamed CoT. The DataSync PUT (via HTTPS 8443) handles mission association separately.

### 11. SA identification message needed for TCP connection identity

When ATAK connects via TLS to port 8089, it sends a self-identification CoT (`a-f-G-U-C`) as the first message. TAK Server uses this to associate the connection with a UID (visible as `Set client for subscription: tls:XX to CALLSIGN`). Node-RED's TCP connection was anonymous — TAK Server knew it as `tls:XX` from an IP but had no UID. Added `eng_sa_inject` → `eng_sa_build` that sends an SA CoT with `uid=creatorUid` on startup and every 10 minutes.

### 12. TAK Server does not overwrite stored CoT via TCP streaming alone

Sending a new CoT event with the same UID over the TCP stream did **not** update the stored version on TAK Server. The `/Marti/api/cot/xml/<uid>` endpoint continued returning the old event. The only reliable way to update stored CoT was to **DELETE the UID from the mission**, then re-stream + re-PUT. New features coming in from ArcGIS (not yet in mission) get fresh CoT from the start.

### 13. `POST /Marti/api/cot` does not exist on TAK Server 5.5

Attempted to POST CoT XML directly via HTTPS as a fallback for the TCP stream. TAK Server returned an HTML page (404-equivalent). There is no HTTP endpoint for submitting CoT on this version — TCP streaming on port 8089 is the only ingest path.

### 14. Docker networking for Node-RED → TAK Server (host)

TAK Server runs natively on the host, not in Docker. Node-RED runs in Docker. For the TCP stream to reach port 8089 on the host:
- `docker-compose.yml` needs `extra_hosts: ["host.docker.internal:host-gateway"]`
- `eng_tcp_out` host = `host.docker.internal`, port = `8089`
- Cert volume mount: `/opt/tak/certs/files:/certs:ro` (plus `chmod 644` on `.key` files for container read access)

### Summary: DataSync API patterns that work

```
# Subscribe integration user to mission (once)
PUT /Marti/api/missions/{name}/subscription?uid={creatorUid}

# Add UID to mission (after CoT is streamed)
PUT /Marti/api/missions/{name}/contents?creatorUid={user}
Body: {"uids":["<uid>"]}

# Remove UID from mission
DELETE /Marti/api/missions/{name}/contents?uid={uid}&creatorUid={user}

# Read mission contents (for reconciliation diff)
GET /Marti/api/missions/{name}
```

All require mTLS on port **8443** with the integration cert. The CoT itself streams over TCP on port **8089** (also mTLS via `tls_tak`).

## What's built inside infra-TAK (current state)

### Slice 1: Configurator UI ✅

**File:** `nodered/configurator.html` — served at `GET /configurator`

Full-featured 5-step wizard:
1. **Paste URL** → auto-detect Feature Service, list layers
2. **Pick layer** → fetch fields, sample data (scrollable table), geometry type detection
3. **Configure fields** — ID field, time field, source filter field with distinct value checkboxes + manual entry, `WHERE` clause auto-generation
4. **Shape styling** — stroke color, fill color, fill opacity, line thickness, line style, closed polygon, label toggle + field + centering, remarks field picker
5. **Export** — TTL hours, CoT type prefix, UID prefix, mission name, named save with card management

**Backend API endpoints** (all in `nodered/build-flows.js`):
- `POST /arcgis-tak/arcgis/service` — proxy to ArcGIS service metadata
- `POST /arcgis-tak/arcgis/layer` — proxy to ArcGIS layer metadata
- `POST /arcgis-tak/arcgis/sample` — proxy to ArcGIS sample features
- `POST /arcgis-tak/arcgis/distinct` — proxy to ArcGIS distinct values query
- `POST /arcgis-tak/config/save` — save single config to flow context
- `POST /arcgis-tak/config/save-all` — save all configs at once
- `GET /arcgis-tak/config/load` — load all saved configs
- `POST /arcgis-tak/tak-settings/save` — save TAK server connection settings
- `GET /arcgis-tak/tak-settings/load` — load TAK server connection settings

**Note:** Paths use `/arcgis-tak/...` (not `/api/...`) because Node-RED’s admin API registers `/api/*` first; `/api/config/load` was intercepted and the Configurator appeared empty.

### Slice 2: Engine flow ✅ (fully wired, reconciliation + streaming working)

**Flow tab:** "ArcGIS → TAK" (shared tab with configurator for flow context)

**Active path (working as of 2026-04-15 — Map Items, no broadcast, correct names):**
```
{feed}_sa_inject → {feed}_sa_build → {feed}_cot_to_xml → {feed}_tcp_out  (SA ident on startup)
{feed}_inject → {feed}_load → {feed}_build_q → {feed}_http_ag → {feed}_parse
  ├→ {feed}_build_sub → {feed}_http_sub (subscribe to mission)
  └→ {feed}_build_m → {feed}_http_m → {feed}_reconcile
       ├→ Port 0: ALL features streamed with <marti><dest mission="..."/> tag
       │          ({feed}_cot_to_xml → {feed}_rate_stream → {feed}_tcp_out)
       │          + {feed}_delay_put → {feed}_build_put → {feed}_http_action (PUT new UIDs only)
       └→ Port 1: Stale UIDs ({feed}_delay_del → {feed}_http_action DELETE)
```
Each feed (CA AIR INTEL, POWER-OUTAGES) gets its own engine tab with all nodes prefixed by `{feed_id}_` (e.g. `air_intel_`, `pwr_outages_`). TLS config (`tls_tak`) is shared globally.

**Node inventory:**
- **`eng_sa_inject`** — fires 10s after deploy, repeats every 10 min; sends SA identification CoT
- **`eng_sa_build`** — builds SA CoT event with `uid=creatorUid` to identify the TCP connection to TAK Server
- **`eng_inject`** — timer, every 5 minutes (+ once at 30s after deploy)
- **`eng_load`** — loads saved ArcGIS configs + TAK settings from flow context; skips configs without `missionName`
- **`eng_build_q`** — builds ArcGIS query URL with `WHERE` clause + dynamic time filter using `DATE 'YYYY-MM-DD'` format + `outSR=4326`
- **`eng_http_ag`** — HTTP GET to ArcGIS Feature Service
- **`eng_parse`** — transforms ArcGIS features → CoT JSON array (deterministic UID, ARGB color, polygon rings → `link` elements, PST-formatted date remarks, rounded decimal numbers). Currently only processes first ring (`g.rings[0]`).
- **`eng_build_sub`** — builds `PUT .../subscription?uid=<creatorUid>` URL
- **`eng_http_sub`** — fires mission subscription (TLS via `tls_tak`)
- **`eng_build_m`** — builds `GET .../missions/<name>` URL for mission contents
- **`eng_http_m`** — fetches existing mission contents (TLS via `tls_tak`)
- **`eng_reconcile`** — diffs ArcGIS UIDs vs mission UIDs. Streams CoT for ALL features every poll (keeps TAK Server cache fresh). Only sends DataSync PUT for new UIDs. Port 0 = stream + PUT new, Port 1 = DELETE stale. Includes ArcGIS fetch failure guard (skips deletes on non-200).
- **`eng_cot_to_xml`** — custom JSON-to-XML serializer (bypasses `node-red-contrib-tak` encode — see lesson #9 below). Outputs `Buffer` directly to `eng_tcp_out`.
- **`eng_delay_put`** / **`eng_delay_del`** — rate-limit delays before API calls
- **`eng_build_put`** — assembles PUT request from `msg._putUrl` + `msg._putUid` after delay
- **`eng_http_action`** — executes PUT/DELETE against Mission API (TLS via `tls_tak`, method from `msg.method`)
- **`eng_tcp_out`** — TLS TCP stream to TAK Server port 8089 via `host.docker.internal`
- **Debug/status/catch nodes** at key points

### Slice 3: Docs (this file + release notes)

- This handoff doc
- `docs/RELEASE-v0.5.8-alpha.md`

## Resolved decisions

| Decision | Resolution |
|----------|------------|
| **Config storage** | Node-RED **flow context** with `localfilesystem` persistence (survives container restarts). Configured via `settings.js` `contextStorage`. |
| **UID scheme** | `arcgis-{value_of_id_field}` — deterministic, stable across polls. |
| **Auth: ArcGIS** | Public services for now (no token). Token auth is a future enhancement. |
| **Auth: TAK certs** | `tls_tak` node uses `admin.pem`/`admin.key` from `/certs/` mount (local file paths in `cert`/`key` properties). Passphrase entered once in UI, stored encrypted in `flows_cred.json`. Admin cert bypasses group direction issues. |
| **Scale / batching** | Split node processes configs one at a time. No explicit batching of features yet — adequate for hundreds of features per poll. |
| **Color format** | Hex → ARGB integer conversion in `eng_parse`. Alpha channel derived from fill opacity percentage. |

## Open items / future work

- ~~**Mount TLS certs into Node-RED container**~~: ✅ Done. Volume mount `/opt/tak/certs/files:/certs:ro` in `docker-compose.yml`. TLS config uses `/certs/admin.pem` and `.key`.
- ~~**End-to-end testing**~~: ✅ Done. Full loop verified 2026-04-15 against live ArcGIS + TAK Server 5.7. Map Items, no broadcast, correct names.
- ~~**PST timestamp formatting**~~: ✅ Done. `fmtVal()` helper in `eng_parse` converts epoch > 1e12 to `MM/DD/YYYY H:MM PST` and rounds decimals.
- ~~**Persist tcp out host/port in build-flows.js**~~: ✅ Done. Hardcoded `host.docker.internal:8089` in `makeEngineTab()`.
- ~~**No-broadcast routing**~~: ✅ Done. `<marti><dest mission="..."/>` tag inside `<detail>` prevents map-wide broadcast. Confirmed on second ATAK device.
- ~~**Multi-feed support**~~: ✅ Done. Feeds are added via the **ArcGIS Configurator** (dynamic engine tabs). `FEEDS` in `build-flows.js` is kept **empty** so the shipped `flows.json` does not embed named production feeds on every box.
- **Test `MISSION_READONLY_SUBSCRIBER` as `defaultRole`**: Currently must be `MISSION_SUBSCRIBER` for writes. Test whether changing to READONLY after admin is subscribed as SUBSCRIBER still allows admin to write (subscription role may override default).
- **Multi-polygon support**: `eng_parse` currently only processes `g.rings[0]`. Iterate all rings, emit separate CoT per ring with UID `<prefix>-<id>-<ringIndex>`.
- **Update detection**: Currently re-streams all CoT every poll (keeps TAK cache fresh). Could compare a hash of geometry/attributes to skip unchanged features for efficiency.
- **ArcGIS token auth**: Add optional token field to configurator for secured services.
- **Multiple geometry support per config**: Currently one geometry type per config. Could detect mixed layers.
- **Error handling / retry**: Engine has basic `node.warn` logging but no retry logic for failed HTTP requests.
- **Automate deploy steps**: Deploy command with passphrase injection works (see Maintainer log 2026-04-15). Could wrap in a `deploy.sh` script.

## Global DataSync Feed Architecture

### Mission roles (TAK Server)

| Role | Permissions | Who gets it |
|------|-------------|-------------|
| **Owner** | Full control — can delete the mission itself | Admin only (via TAK Server GUI / TAK Portal) |
| **Subscriber** | Read + write mission content (PUT/DELETE UIDs) | Integration cert (`admin` for now). Mission `defaultRole` must be set to this. |
| **Read-only** | See and receive data, cannot modify | All agency/field users (goal — need to test changing `defaultRole` after admin is subscribed) |

### Global feed distribution pattern

Create a single TAK group (e.g. `DATASYNC-FEEDS`) that acts as the global channel for all automated DataSync feeds. Every feed (fire perimeters, power outages, weather, etc.) is tied to this group.

**Group assignments:**

| Cert / User | Group assignment | Why |
|-------------|-----------------|-----|
| Integration users (`nodered-global-airdata`, etc.) | `DATASYNC-FEEDS -ig` (write / IN) | Push data into the group. Don't need to see feeds. |
| All agency certs (ATAK field users) | `DATASYNC-FEEDS -og` (read / OUT) | See all feeds in Data Sync menu. Can subscribe and receive data. Cannot modify. |

**Per-feed setup:**

1. Admin creates the DataSync feed in TAK Server GUI or Portal (admin is owner)
2. Feed is tied to the `DATASYNC-FEEDS` group
3. Feed `defaultRole` = **read-only** — subscribers can't edit content
4. Integration user subscribed as **subscriber** (`PUT .../subscription?uid=<name>`) — can PUT/DELETE content
5. Any user with `DATASYNC-FEEDS -og` automatically sees the feed

**Benefits:**

- Adding a new feed automatically shows up for every agency user (they already have the group)
- No per-user or per-agency configuration needed per feed
- Field users can't accidentally pollute feed data
- Integration user can manage content but can't delete the mission itself
- Admin retains owner-level control via GUI

## One-line elevator pitch

**"ArcGIS Feature Service URL + guided field/style mapping → reproducible Node-RED + DataSync lifecycle (add / keep / delete), with TTL like 'only show last 72 hours' — shipped as an infra-TAK module."**

---

## Maintainer log (keep this doc current)

**Why this section exists:** Cursor (and similar) may **not** reload the full chat transcript into the assistant on every turn. **Treat this file as the source of truth** for Node-RED / GIS–DataSync decisions. When you agree on a plan in chat, **append a short bullet here** so the next session does not depend on chat memory.

### 2026-04-15 — EVERYTHING WORKING: Map Items + No Broadcast + Correct Names

This is the definitive "known-good" state. If something breaks, **restore to this**.

#### What is confirmed working

1. **ArcGIS data flows as Map Items** (not Files) in both `CA AIR INTEL` and `POWER-OUTAGES` missions
2. **No broadcast** — data goes ONLY to the DataSync mission. Verified on a second ATAK device: unsubscribed from mission → no data appears. Subscribed → data appears.
3. **Human-readable names** — callsigns show the actual feature label (e.g. `CA-BDU-BOURBON-N50X`, `PGE Outage - 1234`) instead of raw UIDs. These show up in the DataSync mission panel and on the map.
4. **Two-feed architecture** — separate engine tabs for `CA AIR INTEL` and `POWER-OUTAGES`, each with independent poll timers, ArcGIS queries, and reconciliation.
5. **Reconciliation working** — new features added, stale features deleted, existing features re-streamed to keep cache fresh.
6. **TLS connected** — admin cert (CN=admin) used for both TCP streaming (8089) and Mission API (8443).

#### Architecture that works (do NOT change without testing)

```
ArcGIS Feature Service
    ↓ HTTP GET (every 5 min)
Node-RED: Parse → CoT JSON (with callsign from labelField)
    ↓
CoT JSON → Custom XML serializer (FN_COT_TO_XML)
    ↓ includes <marti><dest mission="MISSION_NAME"/></marti> inside <detail>
TCP stream → TAK Server :8089 (TLS via admin.pem)
    ↓ 5-second delay for CotCacheHelper
PUT /Marti/api/missions/{name}/contents (HTTPS :8443, TLS via admin.pem)
    → registers UIDs as Map Items in the mission
```

**Key mechanism**: TCP streaming populates TAK Server's `CotCacheHelper` with the CoT data. The `PUT /contents` with `{"uids":[...]}` then links those cached UIDs to the mission. This is what makes them appear as **Map Items** (not Files). The `<marti><dest mission="..."/>` tag in the CoT XML prevents the data from broadcasting to all connected clients — it targets only the named mission.

**What makes Enterprise Sync different (and why we don't use it)**: `POST /Marti/sync/missionupload` puts data into the mission's `contents` array, which renders as **Files** in ATAK. Map Items come from the `uids` array, which is only populated via TCP streaming + PUT UIDs. This was the critical insight that took days to discover.

#### Mission setup requirements

| Setting | Value | Why |
|---------|-------|-----|
| `defaultRole` | `MISSION_READONLY_SUBSCRIBER` | Set this for field-user read-only access. Node-RED auto-elevates admin to `MISSION_OWNER` after subscribe so writes succeed. Old workaround of requiring `MISSION_SUBSCRIBER` is no longer needed. |
| Integration cert | `admin` (`CN=admin`) | Admin has `ROLE_ADMIN`, bypasses x509 group direction issues. Any cert can work IF it has IN direction on the mission's group AND is subscribed to the mission. |
| Cert paths in `build-flows.js` | `cert: '/certs/admin.pem', key: '/certs/admin.key'` | These go in the `tls_tak` node's `cert`/`key` properties (NOT `certname`/`keyname` — see TLS lesson below). |
| Passphrase | `atakatak` | Must be entered once in Node-RED TLS config UI after first deploy. Stored encrypted in `flows_cred.json`. |
| `creatorUid` | Must match cert CN (e.g. `admin`) | Set in the Configurator UI. Used in Mission API URLs. |

#### Does admin need to be subscribed to the mission?

**Unclear but probably yes for clean operation.** The TAK Server log shows:

```
ERROR StreamingEndpointRewriteFilter - unable to find mission subscription
  for client CA AIR INTEL, CN=admin
```

Despite this error, **data still flows as Map Items**. This is because:
- The `StreamingEndpointRewriteFilter` only affects real-time streaming delivery to mission subscribers via TCP
- The PUT UIDs API call works independently — it registers UIDs in the mission's `uids` array regardless of the streaming filter
- ATAK clients pick up the UIDs when they sync the mission

The Node-RED flow auto-subscribes via `PUT /missions/{name}/subscription?uid=admin` on first poll. The TAK Server may still log the error if the subscription hasn't propagated to the streaming layer. **This is cosmetic — not blocking data flow.**

#### What makes the names show correctly

The `callsign` field in the CoT determines what name appears in DataSync and on the map. In `FN_PARSE_COT` (`build-flows.js`):

```javascript
var callsign = uid;  // fallback to UID
if (cfg.style.labelField && a[cfg.style.labelField] != null)
  callsign = String(a[cfg.style.labelField]);
```

The user sets `labelField` in the Configurator UI (Step 4 — "Label field"). This maps to an ArcGIS attribute like `mission` (for fire perimeters) or `outage_name` (for power outages). The callsign then appears as `<contact callsign="CA-BDU-BOURBON-N50X"/>` in the CoT XML.

#### `<marti><dest>` tag — placement and casing matter

The tag that prevents broadcast and routes to the mission MUST be:
- **Lowercase** `<marti>` (not `<Marti>`)
- **Inside** `<detail>`, before `</detail></event>`
- Mission name must match exactly (case-sensitive, spaces included)

In `FN_COT_TO_XML`:
```javascript
if (msg._missionName) {
  xml += '<marti><dest mission="' + msg._missionName + '"/></marti>';
}
xml += '</detail></event>\n';
```

Without this tag, CoT broadcasts to ALL connected clients map-wide. With it, only mission subscribers see the data.

#### Node-RED TLS config: `cert`/`key` vs `certname`/`keyname`

**Hard-won lesson:** In Node-RED's `tls-config` node, the properties are **not** what you'd expect:

- **`cert`**, **`key`**, **`ca`** = **local file paths** (e.g. `/certs/admin.pem`). When these have values, the **"Use key and certificates from local files"** checkbox auto-checks.
- **`certname`**, **`keyname`**, **`caname`** = **uploaded file display names** (label shown next to the Upload button). These are NOT file paths.
- **`passphrase`** = credential field, stored encrypted in `flows_cred.json`. Must be re-entered after wiping credentials.

Putting paths in `certname`/`keyname` causes the checkbox to stay unchecked and shows paths as "uploaded filenames" with no actual cert data — TLS silently fails.

The correct `tls_tak` definition in `build-flows.js`:
```javascript
{
  id: 'tls_tak', type: 'tls-config',
  name: 'TAK Mission API TLS',
  cert: '/certs/admin.pem', key: '/certs/admin.key', ca: '',
  certname: '', keyname: '', caname: '',
  servername: '', verifyservercert: false
}
```

#### `flows_cred.json` gotcha

Deploying via the Node-RED admin API (`POST /flows`) or `docker cp flows.json` does **not** update `flows_cred.json`. If credentials are wiped (e.g. `echo '{}' > flows_cred.json`), the passphrase is lost and must be re-entered.

**Deploy command that preserves/injects passphrase:**
```bash
docker exec nodered node -e "
  var http = require('http');
  var fs = require('fs');
  var flows = JSON.parse(fs.readFileSync('/data/flows.json','utf8'));
  var tls = flows.find(n => n.id === 'tls_tak');
  if (tls) { tls.credentials = { passphrase: 'atakatak' }; }
  var body = JSON.stringify(flows);
  var req = http.request({
    hostname:'127.0.0.1', port:1880, path:'/flows',
    method:'POST', headers:{'Content-Type':'application/json','Node-RED-Deployment-Type':'full','Content-Length':Buffer.byteLength(body)}
  }, function(res){ var d=''; res.on('data',function(c){d+=c}); res.on('end',function(){console.log(res.statusCode,d)}); });
  req.write(body); req.end();
"
```

#### `paytoqs: 'body'` — recurring regression

The `http_action` node (Mission API PUT/DELETE) MUST have `paytoqs: 'body'`. This tells Node-RED to send `msg.payload` as the HTTP request body. Without it (default `'ignore'`), the `{"uids":[...]}` payload is silently dropped and the PUT registers nothing. This has regressed twice — always verify after regenerating `flows.json`.

#### Deployment checklist (from git to working)

On the server:
```bash
cd /root/infra-TAK && git pull
docker cp nodered/flows.json nodered:/data/flows.json
# Deploy with passphrase injection (see command above)
```

After first deploy on a fresh server, also enter passphrase `atakatak` in Node-RED UI:
1. Open Node-RED editor
2. Double-click any TCP Out node → click the pencil icon on TLS config
3. Enter passphrase → Update → Deploy

#### Dedup by field (added 2026-04-15 late night)

Solves the "same fire flown multiple times" problem. ArcGIS returns multiple features with the same mission name (e.g. `CA-BDU-BOURBON-N50X`) but different creation dates — one per flight. Without dedup, all perimeters appear on the map.

**Configurator**: Step 3 → "Deduplicate by" dropdown. Set to the grouping field (e.g. `mission` for fire perimeters).

**Engine logic** (in `FN_PARSE_COT`): when `dedupField` and `timeField` are both set, features are grouped by `dedupField` value. For each group, only the feature with the highest (newest) `timeField` value is kept. Older duplicates are discarded before CoT is built. Reconciliation then DELETEs any UIDs from old duplicates that were previously in the mission.

```javascript
if (dedupField && timeField) {
  var groups = {};
  for (var di = 0; di < features.length; di++) {
    var da = features[di].attributes || {};
    var key = String(da[dedupField] || '');
    if (!groups[key] || (Number(da[timeField] || 0) > Number((groups[key].attributes || {})[timeField] || 0))) {
      groups[key] = features[di];
    }
  }
  features = Object.keys(groups).map(function(k) { return groups[k]; });
}
```

Debug log shows: `CA AIR INTEL: dedup by mission: 15 → 10 features`

**Important**: requires both `dedupField` AND `timeField` to be set. If either is empty, all features pass through (no dedup).

#### PUT delay increased to 30 seconds

Changed from 5s to 30s. With large feeds (55+ polygon CoTs, many over 10KB), 5 seconds wasn't enough for all CoTs to stream via TCP and get cached in `CotCacheHelper` before the PUT fired — resulting in 500 errors. The system self-heals on next poll, but 30s prevents the 500 entirely. No downside — data is polled every 5 minutes, so an extra 25 seconds is negligible.

#### Deployment one-liner (git pull → deploy with TLS passphrase)

Proven working — run on the server:
```bash
cd /root/infra-TAK && git pull && docker cp nodered/flows.json nodered:/data/flows.json && docker exec nodered node -e "
var http = require('http');
var fs = require('fs');
var flows = JSON.parse(fs.readFileSync('/data/flows.json','utf8'));
var tls = flows.find(function(n){ return n.id === 'tls_tak'; });
if (tls) { tls.credentials = { passphrase: 'atakatak' }; }
var body = JSON.stringify(flows);
var req = http.request({
  hostname:'127.0.0.1', port:1880, path:'/flows',
  method:'POST', headers:{'Content-Type':'application/json','Node-RED-Deployment-Type':'full','Content-Length':Buffer.byteLength(body)}
}, function(res){ var d=''; res.on('data',function(c){d+=c}); res.on('end',function(){console.log(res.statusCode,d)}); });
req.write(body); req.end();
"
```
Expect `204` on success. After deploying new flow code, Configurator settings (saved in global context) persist — no need to reconfigure feeds. Only the TLS passphrase needs to be injected (handled by the deploy command above).

#### Read-only missions — SOLVED 2026-04-20

**Goal achieved**: field users get `MISSION_READONLY_SUBSCRIBER` (can see data, can't edit), admin retains full write access via automatic role elevation in Node-RED flow.

See "Known issue 3" above for full explanation. Summary: create mission as read-only in TAK Portal, Node-RED auto-elevates admin to `MISSION_OWNER` 5 seconds after every cold-start subscribe. No manual intervention needed.

#### Open questions

1. **`StreamingEndpointRewriteFilter` error** — cosmetic, not blocking data flow. Low priority.
2. **Ghost mission package files on ATAK** — server shows 0 contents but ATAK still displays old files from Enterprise Sync era. Unsubscribe/resubscribe didn't clear them. May need to clear ATAK's local file cache manually (`/sdcard/atak/tools/datapackage/` or similar).
3. **Non-admin integration user** — if we ever switch from admin to a dedicated user, that user needs: (a) IN direction on the mission's group, (b) explicit subscription to the mission, (c) cert CN matching the `creatorUid` in the Configurator, (d) explicit role elevation to `MISSION_OWNER` (same mechanism, different cert path).

### 2026-04-12 — New global integration user + DataSync PUT 403 / group direction investigation

#### New integration user: `nodered-global-datasyncfeed`

Replaced per-feed cert (`nodered-global-airdata`) with a single global integration user that will push data to ALL DataSync feeds. Cert created via TAK Portal, assigned to `DATASYNC-FEED` group.

- **Cert files**: `/opt/tak/certs/files/nodered-global-datasyncfeed.pem` / `.key`
- **chmod 644** required on `.key` for Node-RED container to read
- **TLS configs in Node-RED**: both TCP Out (8089 streaming) and HTTP Request (8443 API) nodes share one TLS config node — update cert/key paths and re-enter passphrase after any cert change

#### TCP streaming: working

CoT streaming over TCP 8089 works. Data appears on ATAK map. SA ident fires, TAK Server associates the connection with `nodered-global-datasyncfeed`. Formatted remarks confirmed (24h clock, whole-number acres, PST dates):
```
CreationDate: 03/25/2026 16:53 PST | source: FIRIS | mission: CA-FKU-PARAMOUNT | incident_name:  | area_acres: 1280
```

#### DataSync PUT: 403 Forbidden

Reconcile node fires 10 PUTs, all return empty response. Manual `curl -v` confirms **HTTP 403**. Root cause: `nodered-global-datasyncfeed` only has **OUT** (read) direction on the `DATASYNC-FEED` group — no write access.

#### Mission setup (CA AIR INTEL)

1. Created mission `CA AIR INTEL` in TAK Server GUI, group `DATASYNC-FEED`, default role `MISSION_SUBSCRIBER`
2. Subscribed `nodered-global-datasyncfeed` via CLI PUT — got `MISSION_SUBSCRIBER` (read+write)
3. Changed default role to `MISSION_READONLY_SUBSCRIBER` in GUI — new subscribers get read-only
4. **Finding**: changing default role may retroactively downgrade existing subscriptions. Re-subscribing while default is SUBSCRIBER restores write access.

#### Confirmed working pattern (2026-04-15) — DEFINITIVE

**Use admin cert for all DataSync integrations.** Admin has `ROLE_ADMIN` so it bypasses x509 group direction issues (OUT-only bug). Requirements:

1. Mission `defaultRole` **must be `MISSION_SUBSCRIBER`** — `MISSION_READONLY_SUBSCRIBER` silently blocks PUT/DELETE (returns 200 but UIDs don't stick, no error). **TODO: test changing to READONLY after admin is subscribed — subscription role may override default.**
2. Admin must be **subscribed** to the mission (Node-RED does this automatically via `PUT /missions/{name}/subscription`)
3. Stream CoT via TCP 8089 with **lowercase** `<marti><dest mission="..."/></marti>` tag **inside `<detail>`** to route to mission only (no broadcast)
4. Wait 5 seconds for CotCache, then `PUT /missions/{name}/contents` with `{"uids":[...]}` body and `paytoqs: 'body'`
5. TLS config: `cert`/`key` = local file paths, `certname`/`keyname` = empty. Passphrase stored in `flows_cred.json`.
6. Callsign set from `cfg.style.labelField` attribute → human-readable names in DataSync

**Result**: Map Items (not Files), no broadcast, correct callsigns, full reconciliation lifecycle.

For non-admin integration users, additionally need `certmod -g "DATASYNC-FEED" -ig` to get IN direction.

#### Group direction bug: x509 cert auth gets OUT only from base LDAP group

**Symptom**: `nodered-global-datasyncfeed` in LDAP group `tak_DATASYNC-FEED` (base = BOTH). TAK Server groups API returns `direction: OUT` only. DataSync PUT returns 403.

**Proof regular users are fine**: user `ajioscacor` is in `tak_CA-COR takaware` (base group, one entry in Authentik) and TAK Server shows `CA-COR takaware←, CA-COR takaware→` (BOTH directions). No `_READ` or `_WRITE` groups needed.

**Conclusion**: TAK Server resolves the base LDAP group as **BOTH** for username/password LDAP auth, but only **OUT** for x509 cert-only auth. This is either a TAK Server bug or a missing step when creating integration users.

#### TAK Portal bugs found

1. **"Edit Group" direction changes don't propagate to LDAP**: changing direction from BOTH→WRITE in Portal UI updates Portal's DB but does NOT move the user between Authentik LDAP groups (`tak_DATASYNC-FEED` → `tak_DATASYNC-FEED_WRITE`). Verified via `ldapsearch`.
2. **Manual fix in Authentik works**: directly adding user to `tak_DATASYNC-FEED_WRITE` in Authentik admin DID propagate to LDAP, but TAK Server returned `data: []` (no groups) because the user was moved OUT of the base group.
3. **LDAP group convention** (from MyTeckNet): `tak_GROUP` = BOTH, `tak_GROUP_READ` = OUT, `tak_GROUP_WRITE` = IN. For LDAP, to restrict to a single direction, user goes in the `_READ` or `_WRITE` variant only.

#### Proposed fix (to try next session)

**Important**: with `default="ldap"` and `x509groups="true"` in CoreConfig, TAK Server uses LDAP as the sole authority for group assignment. The cert is authentication only; `UserAuthentication.xml` / `certmod` is ignored. The fix must be in LDAP/Authentik.

**Try `certmod -g` anyway** as a quick test — if TAK Server truly ignores it, no harm done:
```bash
java -jar /opt/tak/utils/UserManager.jar certmod -g DATASYNC-FEED /opt/tak/certs/files/nodered-global-datasyncfeed.pem
```

**Core question for Justin**: Regular user `ajioscacor` in base group `tak_CA-COR takaware` gets BOTH arrows (IN+OUT) in TAK Server client monitoring. Integration user `nodered-global-datasyncfeed` in base group `tak_DATASYNC-FEED` gets OUT only. Both are in `ou=users,dc=takldap` with the same `memberOf` pattern. Why the difference?

**TAK Portal action items**:
- Fix "Edit Group" direction changes to actually update LDAP group membership (currently only updates Portal DB)
- Investigate why integration users resolve to OUT-only from a base LDAP group that should be BOTH
- Consider whether `certmod` should be part of the integration creation flow as a belt-and-suspenders measure

#### TAK Server version note

This is **TAK Server 5.7** (previously noted as 5.5 — corrected).

---

### 2026-04-11 — Engine fully operational, CoT streaming + DataSync confirmed

#### What was fixed

1. **Reconciliation wiring**: Removed `eng_add_mission_simple` (blind add-only path). Rewired `eng_parse` to feed both `eng_build_sub` (subscribe) and `eng_build_m` (reconcile) in parallel. Reconciliation is now the primary and only path.

2. **Zero-feature safety guard**: `eng_parse` passes `msg._features = []` on 0 features instead of returning null, allowing reconciliation to delete stale items.

3. **ArcGIS failure guard**: `eng_reconcile` checks `msg._arcgisStatus`. If ArcGIS returned non-200, skips all deletes to prevent mass removal when ArcGIS is unreachable.

4. **Remarks formatting**: `fmtVal()` helper in `eng_parse` converts epoch timestamps > 1e12 to `MM/DD/YYYY HH:MM PST` (24-hour clock, hardcoded UTC-8) and rounds decimal numbers to whole integers. Confirmed working on TAK Server.

5. **Custom XML serializer**: `eng_cot_to_xml` replaces `node-red-contrib-tak` encode node. Builds CoT XML string from JSON and sends as `Buffer` to `eng_tcp_out`. The tak encode node was not reliably delivering data.

6. **SA identification**: Added `eng_sa_inject` → `eng_sa_build` that sends a self-identification CoT on startup (10s delay) and every 10 minutes. Required for TAK Server to associate the TCP connection with the integration user.

7. **Removed Marti tag from streamed CoT**: `<Marti><dest mission="..."/>` in the CoT XML triggered `StrictUidMissionMemebershipFilter` rejection. Removed it — DataSync PUT handles mission association via HTTPS 8443.

8. **Re-stream all features every poll**: Changed `eng_reconcile` to stream CoT for ALL features (not just new ones). Keeps TAK Server's CoT cache fresh. Only sends DataSync PUT for UIDs not already in the mission.

9. **Docker networking**: Confirmed `host.docker.internal:8089` for TCP stream, cert volume mount `/opt/tak/certs/files:/certs:ro`, `chmod 644` on key files.

10. **Deploy cheat sheet**: Created `docs/NODERED-DEPLOY.md` with all cert paths, host/port, and step-by-step post-deploy procedure.

#### What was proven end-to-end

- ArcGIS FIRIS/CAL FIRE/USFS query → 10 features → CoT JSON with formatted remarks → custom XML serializer → TCP stream to TAK Server 8089 → DataSync PUT to mission → ATAK receives polygons with formatted dates (`03/25/2026 4:53 PM PST`) and whole-number acres (`1280`).
- Delete from mission (via TAK Server GUI) → next poll re-adds with fresh CoT → confirms full lifecycle.

#### Key commits

- `a85372a` — SA identification message
- `a06c03d` — re-stream CoT for all features every poll
- `4944ba7` — remove Marti tag from streamed CoT
- `28df73e` — bypass tak encode, custom XML serializer via TCP
- `4d63929` — (intermediate) HTTPS POST attempt (reverted approach, endpoint doesn't exist)

---

### 2026-04-10 — Engine wiring audit + DataSync integration plan

#### Critical finding: reconciliation path is orphaned

Code review of `build-flows.js` revealed the **reconciliation path exists but is not wired into the main flow**. The current active wiring is:

```
eng_inject → eng_load → eng_build_q → eng_http_ag → eng_parse
  → eng_build_sub → eng_http_sub → eng_add_mission_simple (add-only, NO dedupe)
```

`eng_add_mission_simple` PUTs **every UID every poll** regardless of whether it already exists in the mission, and **never deletes anything**. The smart reconciliation nodes (`eng_build_m → eng_http_m → eng_reconcile`) are fully written but have **no incoming wire** — they are orphaned.

#### TAK group terminology (corrected)

- **`-ig` (IN group)** = **write** — the cert can push data into that group
- **`-og` (OUT group)** = **read** — the cert receives data from that group
- **`-g` (BOTH)** = **read + write**

The Node-RED integration user likely needs `-g` (BOTH) so it can both push CoT/DataSync content AND `GET /Marti/api/missions/{name}` to read existing UIDs for reconciliation. Verify during prove-out whether an IN-only user can still read mission contents via the API.

#### TAK Portal status (as of 2026-04-10)

TAK Portal already provides:
1. **Create Integration** — generates a client cert, ties it to a group, assigns write-only (IN group). The cert files from Portal had a hash mismatch in Paul's testing (reported to Justin; Justin could not reproduce — revisit later). Workaround: pull cert files directly from `/opt/tak/certs/files/` on the server.
2. **Create DataSync Feed** — new capability, Portal can now create the mission itself.

**The missing piece**: Portal creates the integration user AND creates the DataSync feed, but does not connect them. The gap is a single API call to subscribe the integration user to the feed:
```
PUT /Marti/api/missions/{feedName}/subscription?uid={integrationUser}
```
Feature request for Justin: add a checkbox/dropdown on "Create Integration" — "Subscribe to DataSync feed: [list of existing feeds]" — that fires this PUT after cert+group creation.

#### CLI prove-out steps (bridge the Portal gap)

Until Portal adds the subscription step, do it manually:

1. **Create integration in TAK Portal** (or `makeCert.sh client <name>` + `certmod -g <group>` on CLI)
2. **Create DataSync feed in TAK Portal** (or `POST /Marti/api/missions/<name>?creatorUid=<name>&group=<group>`)
3. **Subscribe integration user to feed (CLI — the missing glue)**:
   ```bash
   curl -k --cert /opt/tak/certs/files/<NAME>.pem \
          --key /opt/tak/certs/files/<NAME>.key.pem \
          --cacert /opt/tak/certs/files/ca-trusted.pem \
     -X PUT "https://localhost:8443/Marti/api/missions/<FEED>/subscription?uid=<NAME>" \
     -H "Content-Type: application/json"
   ```
4. **Configure Node-RED**: upload certs to TLS config node, set Mission name + Creator UID in configurator, enable engine flow tab.

#### Planned `build-flows.js` changes (7 items)

**1. Remove `eng_add_mission_simple`** (lines 707–757): Delete the competing no-dedupe path entirely.

**2. Rewire flow so reconciliation is the primary path**:
- `eng_parse.wires`: change from `[['eng_build_sub']]` to `[['eng_build_sub', 'eng_build_m']]`
- `eng_http_sub.wires`: change from `[['eng_debug_sub', 'eng_add_mission_simple']]` to `[['eng_debug_sub']]`
- Subscription and reconciliation now run in parallel from `eng_parse`.

**3. Safety guard in `eng_parse` for zero features**: Currently returns `null` on 0 features, killing the entire chain including reconciliation. Fix: when features = 0, still pass `msg._features = []` to `eng_build_m` so reconciliation can delete stale items. Do NOT send to `eng_build_sub` (no point subscribing with nothing).

**4. Safety guard in `eng_reconcile` for failed ArcGIS fetch**: If `msg.statusCode` from the ArcGIS HTTP request is not 200, do NOT delete mission items (ArcGIS was unreachable, not "zero real features"). If statusCode is 200 and features = 0, proceed with deletion (outages genuinely cleared).

**5. Multi-polygon support in `eng_parse`**: Currently only processes `g.rings[0]`. Change to iterate all rings, emitting a separate CoT per ring with UID `<prefix>-<id>-<ringIndex>` (or no suffix for single-ring features).

**6. Web Mercator → WGS84 conversion**: Add `&outSR=4326` to `eng_build_q` so ArcGIS returns WGS84 natively. Add a `mercatorToWgs84()` fallback helper in `eng_parse` for services that ignore `outSR`.

**7. PST timestamp formatting in remarks**: Add `formatPST(epochMs)` helper in `eng_parse` — converts epoch timestamps to `MM/DD/YYYY HH:MM AM PST` (hardcoded UTC-8). Apply to any remarks field value > 1e12.

#### Configurator changes (minimal)

- Add help text on Creator UID field: "Must match the cert name from TAK Portal → Create Integration"
- No structural changes needed — mission name, creator UID, all config fields already present.

#### Execution order for tomorrow

1. Changes 1–2 (remove add-only path, rewire to reconciliation) — structural fix
2. Changes 3–4 (safety guards) — prevents data loss
3. Changes 5–7 (multi-polygon, coordinate conversion, PST) — feature completeness
4. Run `node build-flows.js` to regenerate `flows.json`
5. Deploy to test server, create integration user + feed on CLI, prove out end-to-end

#### Portal integration vision (for Justin)

Once proven on CLI, the full automated flow would be:
1. Portal "Create Integration" form gains: **"Subscribe to DataSync feed"** checkbox + feed dropdown
2. On submit, Portal does: `makeCert.sh` → `certmod` → `PUT .../subscription?uid=<name>` (one extra call)
3. Output: cert bundle + mission name + creator UID — user plugs these into Node-RED configurator
4. Long-term: Portal could push config directly into Node-RED flow context via its API, fully zero-touch

### 2026-04-10 — v0.5.9-alpha shipped (Boot sequence hardening)

- **Guard Dog Boot Sequencer** (`tak-boot-sequencer.sh`): On reboot, all Docker containers are stopped so TAK Server gets exclusive CPU during Java initialization. Nothing else starts until port 8089 is listening.
- **Authentik staggered start** (`tak-post-start.sh`): PostgreSQL starts first → waits for `pg_isready` → then `docker compose up -d` brings server/worker/LDAP. Eliminates "FATAL: sorry, too many clients already" PostgreSQL connection storms.
- **PostgreSQL tuning — idempotent compose patching** (`app.py`): New `_ensure_authentik_compose_patches()` helper injects `max_connections=300`, `idle_session_timeout=300s`, and TCP keepalives into `docker-compose.yml`. Called from `run_authentik_deploy(reconfigure)`, `authentik_control(update)`, and `_apply_authentik_pg_tuning`. Previously, reconfigure and update code paths skipped compose patching, and `_apply_authentik_pg_tuning` could inadvertently clear `ALTER SYSTEM` settings.
- **Priority service ordering with connection-safe stagger**: TAK Server → Authentik → TAK Portal start as fast as possible (critical trio). CloudTAK and Node-RED get 30-second cooldown delays before their `docker compose up -d` to prevent Docker iptables rule rebuilds from disrupting active TAK client connections on port 8089. MediaMTX (systemd-native, no Docker iptables impact) starts last with a shorter delay.
- **`docker-stagger.service` disabled**: This older systemd service conflicted with `tak-boot-sequencer.sh`, causing repeated PostgreSQL stops/restarts during boot. Must be disabled on existing VPS installs (`systemctl disable --now docker-stagger.service`).
- **Validated on three topologies**:
  - **Single server (Responder)**: Critical trio 2m 53s, full stack 4m 42s
  - **Single server (tak-10, beefy)**: Critical trio 1m 03s, full stack 2m 50s
  - **Two-server split**: Critical trio 2m 22s, full stack 4m 15s
- **Results**: Zero PostgreSQL errors, zero LDAP 502s (except expected cold-cache first binds ~80s), LDAP binds under 1ms once warm, TAK client connections stable through CloudTAK/Node-RED startup. ATAK clients may see one data reception timeout on first connect during cold boot (normal TAK Server behavior — clients reconnect immediately).

### 2026-04-08 — v0.5.8-alpha shipped (Configurator + Engine + cert fix)

- **Configurator UI complete**: Full 5-step wizard with ArcGIS proxy APIs, distinct-value checkboxes + manual source filter, shape styling (stroke/fill/opacity/thickness/style/labels/remarks picker), named config save/load with card management, TAK Server settings panel, mission name field. Served at `GET /configurator`.
- **Engine flow built** (first pass, `build-flows.js`): 11-node chain implementing the single reconciliation loop. Disabled by default — user enables in Node-RED editor after configuring TLS certs. Polls every 5 min, iterates all saved configs with a `missionName`, queries ArcGIS, transforms to CoT JSON, diffs against TAK mission contents, emits PUT/DELETE via DataSync API.
- **Config persistence**: `settings.js` configured for `localfilesystem` context storage; configs and TAK settings survive Node-RED container restarts.
- **cert-metadata.sh fix**: Ownership was broken after TAK Server upgrades (`root:root` instead of `tak:tak`), preventing TAK Portal cert generation. Fixed in `app.py` (all code paths that touch the file) + self-healing on console startup for existing servers.
- **Key reference material consumed**: Paul's DataSync flow patterns (PUT/DELETE to Mission API), Greg Albrecht's RIIS blog post on `node-red-contrib-tak` (CoT JSON `_attributes` format), `node-red-contrib-tfr2cot` subflow JSON (DataSync subscription wiring).
- **What's next**: End-to-end test with live ArcGIS service + TAK Server. Wire `node-red-contrib-tak` to Port 1 of reconcile node for TCP CoT streaming. Test polygon and point rendering in TAK clients.

### 2026-04 — earlier session notes

- **Repo state:** `nodered/` includes committed flows (Configurator UI + Engine).
- **Operational tie-in (high-volume CoT / ADSB):** TAK Server retention should run **hourly** when possible (same 24h window, smaller deletes per run). Overlapping multi-hour `DELETE` on `cot_router` can exhaust RAM/swap; infra-TAK **v0.5.2+** adds Guard Dog **`tak-retention-guard.sh`** as a safety net (kill stuck DELETEs, batched cleanup). Not a substitute for hourly retention — complementary.

### 2026-02-06 — recovered plan (paste from prior chat; canonical example layer)

**Product intent (Node-RED tab):** Help the user **identify the slice of data they want from an ArcGIS URL** without manually opening raw GET responses, walking nested JSON, or guessing array indices. The tab is **discovery + filter definition + export** so building downstream flows (CoT, DataSync, mission) starts from a **known-good query**, not trial-and-error. *First concrete feed to ship against:* **airborne intel** — i.e. the CA perimeters layer filtered to **FIRIS** + **CAL FIRE INTEL FLIGHT DATA**, **72h** on `poly_DateCurrent`, **`mission`** as UID (see table below).

**Goal:** Lower the bar from "navigate ArcGIS REST and attribute tables" to **paste Feature Service URL → discover fields/values in-tool → build filter → export config** for the engine.

**Example layer (screenshot / attribute table):** *CA Perimeters CAL FIRE NIFC FIRIS public view* (~467 records in sample). Key fields:

| Field | Role |
|--------|------|
| `source` | Filter — user wants only **FIRIS** and **CAL FIRE INTEL FLIGHT DATA** (ignore USFS, NIFC, etc. for this deployment). Full strings must come from API (UI may truncate, e.g. "CAL FIRE INTEL FLIGHT D…"). |
| `poly_DateCurrent` | Time — keep features **newer than 72 hours** (rolling window). |
| `mission` | Stable **UID / dedup** candidate (e.g. `CA-LPF-Gifford-N50X`, `GIFFORD`). |
| `incident_name` | Human-readable label (optional for CoT title). |

**Representative `where` clause** (date literal must be generated in code from "now − 72h" in ArcGIS SQL syntax):

```text
source IN ('FIRIS', 'CAL FIRE INTEL FLIGHT DATA')
AND poly_DateCurrent > DATE '2025-08-10'   -- example; use dynamic cutoff
```

(Use the REST API's date format your layer expects — confirm against the service.)

**Reference flows (user's Node-RED screenshots, not in repo):** *GET PGE OUTAGES* (ArcGIS → `dataSyncSubscription` / CoT / mission), *PGE CLEANUP V2* (mission list → split → `DELETE` per UID), *PGE NUKE MISSION*. Patterns align with **`node-red-contrib-tfr2cot`** / Mission API; fix **TLS** properly (`ERR_SSL_SSLV3_ALERT_CERTIFICATE_UNKNOWN` in screenshots — same CA/client cert story as in *Operational / TLS notes* above).

*Add dated bullets above as you ship slices or change direction.*
