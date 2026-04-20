# infra-TAK v0.6.5-alpha — KML Configurator + ArcGIS stable-ID pills, strict reconcile, Purge Orphans

Release date: April 2026

---

## Summary

v0.6.5-alpha ships two major Configurator upgrades:

**KML feeds** now have a full Configurator flow — matching ArcGIS in layout and interaction. Operators click **Fetch** to discover attribute keys directly from a live KML or NetworkLink URL, pick stable ID fields with the same pill UI, compose label templates and remarks, set a time field, dedup key, and time window — then save. No manual attribute typing required.

**ArcGIS stable ID** is now a **multi-select pill picker** — choose one stable native field, or combine several for a synthetic compound UID (`c` + hash) that survives OBJECTID rotation. Multi-layer feeds show presence badges (`in 2/3`) so operators avoid partial fields. This release also ships **strict mission ownership** reconcile mode and a one-shot **Purge Orphans** admin action for cleaning up UID drift.

---

## What's new — KML Configurator

A full three-step wizard that mirrors the ArcGIS Configurator in every detail.

### Step 1 — Source

- **KML / Network Link URL** field + **Fetch** button
- Fetch calls `POST /arcgis-tak/kml/fetch`, follows `<NetworkLink>` redirects, and parses up to 20 placemarks with geometry
- **Sample attributes table** appears after a successful Fetch (up to 15 rows) — same layout as ArcGIS sample features
- **Poll interval** (minutes)

### Step 2 — Field Mapping & Style

- **Stable ID pill picker** — same interaction as ArcGIS; auto-suggests `GlobalID` / `OBJECTID` from discovered keys
- **Time field** select (populated after Fetch) + **epoch-ms checkbox** — for feeds where timestamps are numeric epoch milliseconds
- **Deduplicate by** select (populated after Fetch) — keeps latest record per value each poll cycle
- **Label template pills** — click fields to compose callsigns; optional custom text prefix; live preview
- **Remarks pills** — click fields to add in order; auto-suggests `type`, `mission`, `source`, `description`
- Style controls — stroke color, fill color, fill opacity, stroke weight, labels on/off, uppercase, center label

### Step 3 — TAK Integration & Save

- **Time window (TTL)** — value + unit (Minutes / Hours / Days); `0` = no time filter, default CoT stale (1 h)
- Data Sync Mission toggle, Config name, TAK Mission name, UID prefix, Creator UID, Strict mission ownership

### Attribute parsing

KML attributes are extracted in priority order:
1. ArcGIS-style HTML attribute table in `<description>` (KML exported from ArcGIS Online / Portal)
2. `<ExtendedData>` / `<SimpleData>` (standard KML schema)
3. `<name>` and `OBJECTID` — auto-added for every placemark

### Fetch endpoint architecture

Replaced an unreliable single async `function` node with a robust Node-RED node chain:

```
http in → fn_kml_prep → http request (main) → fn_kml_check_nl
                                                  ├─ NetworkLink → http request (inner) → fn_kml_parse → http response
                                                  └─ No NetworkLink ──────────────────→ fn_kml_parse → http response
```

15-second request timeout on each hop; HTTP 4xx/5xx errors surface as JSON `{ error }` responses.

---

## What's new — ArcGIS: Stable ID multi-field picker

- Replaces the single **idField** `<select>` with **pills** (same interaction pattern as remarks): add/remove fields; **live preview** shows row-index fallback (red), single-field UID (green), or compound hash UID (cyan).
- **Compound UIDs** use a `c` prefix in logs (e.g. `arcgis-feed-c12345678`) so operators can spot synthetic-ID feeds quickly.
- **Backwards compatible:** `mapping.idFields` (array) with fallback to legacy `mapping.idField` (string).

## What's new — ArcGIS: Multi-layer field union + presence badges

- For **2+ layers** in Step 2, the picker unions **every field name** across those layers.
- **Full-opacity** pills = field present in **all** selected layers (safe default).
- **Dimmed** pills with red **`(in X/N)`** = partial presence; tooltip explains that rows from layers missing a partial field hash an empty string for that slot.
- Auto-pick of OID/GlobalID only runs if the field exists on **all** selected layers.

## What's new — ArcGIS: Strict mission ownership + Purge Orphans

- **Step 5 — "Strict mission ownership"** (default **on** for new configs): reconcile may delete mission UIDs **not** in the current ArcGIS result set, including orphans from older config iterations that prefix-guarded DELETE could not remove.
- **Multi-layer hotfix:** strict mode and one-shot purge are **disabled on per-layer passes** when `msg._layerPrefix` is set (sibling layers were deleting each other's UIDs). Engine logs a warning and falls back to prefix-guarded DELETE for that pass.
- **Purge** on each config card (when `missionName` is set): one click queues a **one-shot** strict reconcile for that config. Use after changing stable-ID fields or `uidPrefix` to clear old UIDs in one cycle.

Backing endpoint: `POST /arcgis-tak/tak/purge-orphans` with `{ configName }`.

---

## Operator guidance

### KML feeds

- Click **Fetch** first — this populates all field pickers from the live KML
- For ArcGIS-exported KML (has HTML table in `<description>`): all attributes will be discovered automatically
- Pick `GlobalID` or `OBJECTID` as stable ID when available; otherwise combine `name` + a geometry field
- Set **Deduplicate by** to the field that uniquely identifies a moving asset (e.g. tail number, unit ID)
- If the KML has timestamps, set **Time field** and **Time window** to filter stale placemarks

### ArcGIS rotating-OBJECTID feeds (NOAA-style)

- **NOAA FLOOD** (single layer, Flash Flood class): e.g. `INCIDENT_DATETIME + LATITUDE + LONGITUDE`
- **NOAA STORM** (multi-layer HAIL + TORNADO + WIND): e.g. `LATITUDE + LONGITUDE + COMMENTS` — all three often full-opacity across layers
- **POWER-OUTAGES / CA AIR INTEL:** keep existing stable natives (`IncidentId`, `GlobalID`)
- After changing stable-ID fields or `uidPrefix`, click **Purge** once on that config, then **Deploy** in Node-RED

---

## Upgrade notes

1. **Update Now** (or tag checkout + restart console).
2. Open `nodered.<fqdn>` → **Configurator**.
3. **KML feeds:** click **+ New Config** → KML → enter URL → **Fetch** → configure → **Save**.
4. **ArcGIS rotating-OBJECTID feeds:** Step 3 → adjust stable-ID pills → **Save** → **Deploy** in Node-RED.
5. **Purge** once per ArcGIS config if you changed identity fields.

Same post-update shape as v0.6.3 / v0.6.4: Guard Dog + Node-RED `deploy.sh` merge (no new compose requirements).

---

## Known limitations

- **Multi-layer orphan cleanup** under strict mode: per-layer passes disable strict so sibling layers are not cross-deleted. Orphans whose UID no longer matches the current `uidPrefix` may require temporarily switching to single-layer, running **Purge**, then restoring multi-layer — full cross-layer strict cleanup is a future enhancement.
- **KML time filtering** is enforced in the KML engine, not the source query (unlike ArcGIS `where` clauses). Features outside the time window are dropped after fetch, not before download.

---

## Technical changes (condensed)

| Area | Change |
|------|--------|
| KML Configurator | Three-step wizard (`kmlStep1/2/3`); `Fetch` button; pill pickers for ID / label / remarks; time field + epoch-ms; dedup field; TTL value + unit; sample table |
| KML fetch endpoint | `hi_kml_fetch` → `fn_kml_prep` → `hr_kml_main` → `fn_kml_check_nl` → `hr_kml_inner` → `fn_kml_parse` → `ho_kml_fetch`; 15 s timeout; NetworkLink follow |
| KML parse | `FN_KML_PARSE_FIELDS`: HTML attr table, ExtendedData/SimpleData, name/OBJECTID; up to 20 geo placemarks, 15 samples |
| ArcGIS Reconcile | `strictMode`, `oneShotPurge`, `cleanOrphans`; `isMultiLayerPass` via `msg._layerPrefix`; hotfix disables strict on multi-layer passes |
| ArcGIS Engine | `FN_PARSE_COT`: `idFields[]` + legacy `idField`; compound UID `c` + djb2; `FN_RECONCILE` strict branches |
| API | `POST /arcgis-tak/tak/purge-orphans`; `POST /arcgis-tak/kml/fetch` |
| ArcGIS Configurator | Stable-ID pill picker; Step 5 strict checkbox; sidebar **Purge** |
| Docs | `docs/TESTING-NODERED-DEPLOYS.md` (new); `.cursorrules` deploy safety rule |
| Artifacts | Regenerated `flows.json`, `template-functions.json` |

---

## Related

- [RELEASE-v0.6.4-alpha.md](RELEASE-v0.6.4-alpha.md) — VERSION/tag alignment for Update Now.
- [RELEASE-v0.6.3-alpha.md](RELEASE-v0.6.3-alpha.md) — Layer/class/epoch-ms Configurator baseline.
