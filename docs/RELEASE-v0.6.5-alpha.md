# infra-TAK v0.6.5-alpha — Node-RED ArcGIS Configurator (stable-ID pills, strict reconcile, Purge Orphans)

Release date: April 2026

---

## Summary

v0.6.5-alpha makes ATAK DataSync **much quieter** on ArcGIS feeds where services **rotate OBJECTIDs** every poll (e.g. NOAA storm reports, NOAA FLOOD). The Configurator **Stable ID** control is now a **multi-select pill picker**: choose one stable native field (as before), or select **several fields** to build a **synthetic compound UID** (`c` + hash) that survives OBJECTID rotation. The picker is **multi-layer aware**: for feeds spanning 2+ layers it shows the **union of all fields** across selected layers, with each pill annotated **how many layers contain that field** (`in 2/3`, `in 1/3`) so operators do not pick a field that only exists on one sibling.

This release also ships **strict mission ownership** reconcile mode and a one-shot **Purge Orphans** admin action, addressing **yellow missions**, **items vanishing from the map without a DataSync prompt**, and **leftover UID drift** after past `idField` / `uidPrefix` / class-mapping edits.

---

## What’s new — Stable ID multi-field picker

- Replaces the single **idField** `<select>` with **pills** (same interaction pattern as remarks): add/remove fields; **live preview** shows row-index fallback (red), single-field UID (green), or compound hash UID (cyan).
- **Compound UIDs** use a `c` prefix in logs (e.g. `arcgis-feed-c12345678`) so operators can spot synthetic-ID feeds quickly.
- **Backwards compatible:** `mapping.idFields` (array) with fallback to legacy `mapping.idField` (string).

## What’s new — Multi-layer field union + presence badges

- For **2+ layers** in Step 2, the picker unions **every field name** across those layers.
- **Full-opacity** pills = field present in **all** selected layers (safe default).
- **Dimmed** pills with red **`(in X/N)`** = partial presence; tooltip explains that rows from layers missing a partial field hash an empty string for that slot.
- Auto-pick of OID/GlobalID only runs if the field exists on **all** selected layers.

## What’s new — Strict mission ownership + Purge Orphans

- **Step 5 — “Strict mission ownership”** (default **on** for new configs): reconcile may delete mission UIDs **not** in the current ArcGIS result set, including orphans from older config iterations that prefix-guarded DELETE could not remove.
- **Multi-layer hotfix:** strict mode and one-shot purge are **disabled on per-layer passes** when `msg._layerPrefix` is set (sibling layers were deleting each other’s UIDs). Engine logs a warning and falls back to prefix-guarded DELETE for that pass.
- **Purge** on each config card (when `missionName` is set): one click queues a **one-shot** strict reconcile for that config. Use after changing stable-ID fields or `uidPrefix` to clear old UIDs in one cycle.

Backing endpoint: `POST /arcgis-tak/tak/purge-orphans` with `{ configName }`.

---

## Operator guidance (NOAA-style)

- **NOAA FLOOD** (single layer, Flash Flood class): e.g. `INCIDENT_DATETIME + LATITUDE + LONGITUDE` (or add `ISSUANCE` for extra stability).
- **NOAA STORM** (multi-layer HAIL + TORNADO + WIND): e.g. `LATITUDE + LONGITUDE + COMMENTS` — all three often **full-opacity** across layers. Prefer fields present in **all** layers; avoid partial-only datetime fields like `UTC_DATETIME` if only **in 2/3** layers on that service.
- **POWER-OUTAGES / CA AIR INTEL:** keep existing stable natives (`IncidentId`, `GlobalID`).
- After changing stable-ID fields or `uidPrefix`, click **Purge** once on that config, then **Deploy** in Node-RED.

---

## Upgrade notes

1. **Update Now** (or tag checkout + restart console).
2. Open `nodered.<fqdn>` → **Configurator**.
3. For rotating-OBJECTID feeds: Step 3 → adjust stable-ID pills → **Save** → **Deploy** in Node-RED.
4. **Purge** once per affected config if you changed identity fields.

Same post-update shape as v0.6.3 / v0.6.4: Guard Dog + Node-RED `deploy.sh` merge (no new compose requirements).

---

## Known limitations

- **Multi-layer orphan cleanup** under strict mode: per-layer passes disable strict so sibling layers are not cross-deleted. Orphans whose UID no longer matches the feed’s current `uidPrefix` may still need **temporarily switching Step 2 to a single layer**, running **Purge**, then restoring multi-layer, or cleaning the mission in TAK Server admin — full cross-layer strict cleanup is a future enhancement.

---

## Technical changes (condensed)

| Commit / area | Change |
|---------------|--------|
| Reconcile | `strictMode`, `oneShotPurge`, `cleanOrphans`; `isMultiLayerPass` via `msg._layerPrefix`; hotfix disables strict/purge on multi-layer passes |
| Engine | `FN_PARSE_COT`: `idFields[]` + legacy `idField`; compound UID `c` + djb2; `FN_RECONCILE` strict branches |
| API | `POST /arcgis-tak/tak/purge-orphans` |
| Configurator | Pill picker, previews, Step 5 strict checkbox, sidebar **Purge** |
| Artifacts | Regenerated `flows.json`, `template-functions.json` |

---

## Related

- [RELEASE-v0.6.4-alpha.md](RELEASE-v0.6.4-alpha.md) — VERSION/tag alignment for Update Now.
- [RELEASE-v0.6.3-alpha.md](RELEASE-v0.6.3-alpha.md) — Layer/class/epoch-ms Configurator baseline.
